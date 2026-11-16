"""Sink core: NDJSON part files of the local lake, rotation and the offsets safe to commit.

Layout: <root>/<topic>/dt=YYYY-MM-DD/part-<n>.ndjson.gz, with dt = UTC date of the envelope's
event_time (INTERFACES says nothing else). A part is written as `part-<n>.ndjson.gz.open` and
renamed when closed, so readers (dbt) only ever see complete files. All of a topic's parts are
closed together, and only then is its offset safe to commit: a crash repeats messages (the lake
keeps the raw duplicates; staging deduplicates by msg_id), never loses them.
Time comes from the injected Clock (wall clock, rotation only), never from the messages.
"""

import gzip
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from emtra_common.clock import Clock

OPEN_SUFFIX = ".open"
UNKNOWN_DT = "unknown"     # event_time missing or unreadable: kept, not dropped


def partition_date(value: bytes) -> str:
    """UTC date (YYYY-MM-DD) of the envelope's event_time."""
    try:
        t = datetime.fromisoformat(json.loads(value)["event_time"])
        return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC).date().isoformat()
    except (ValueError, KeyError, TypeError):
        return UNKNOWN_DT


class _TopicState:
    def __init__(self, opened_at: datetime) -> None:
        self.opened_at = opened_at
        self.msgs = 0
        self.next_offset = 0
        self.files: dict[str, tuple[IO[bytes], Path]] = {}   # dt -> (gzip stream, .open path)


class Sink:
    def __init__(self, root: Path, clock: Clock, max_msgs: int, max_s: float) -> None:
        self.root, self.clock, self.max_msgs, self.max_s = root, clock, max_msgs, max_s
        self.total = 0
        self._state: dict[tuple[str, int], _TopicState] = {}

    def discard_stale(self) -> int:
        """Remove part files left open by a crash: their messages were never committed."""
        stale = list(self.root.rglob(f"*{OPEN_SUFFIX}"))
        for p in stale:
            p.unlink()
        return len(stale)

    def write(self, topic: str, partition: int, offset: int, value: bytes) -> None:
        st = self._state.get((topic, partition))
        if st is None:
            st = self._state[(topic, partition)] = _TopicState(self.clock.now())
        dt = partition_date(value)
        if dt not in st.files:
            st.files[dt] = self._open(topic, dt)
        st.files[dt][0].write(value.replace(b"\n", b" ") + b"\n")   # one message per line
        st.msgs += 1
        st.next_offset = offset + 1
        self.total += 1

    def _open(self, topic: str, dt: str) -> tuple[IO[bytes], Path]:
        d = self.root / topic / f"dt={dt}"
        d.mkdir(parents=True, exist_ok=True)
        used = [int(m.group(1)) for p in d.iterdir() if (m := re.match(r"part-(\d+)\.", p.name))]
        path = d / f"part-{max(used, default=0) + 1:06d}.ndjson.gz{OPEN_SUFFIX}"
        return gzip.GzipFile(path, "wb", mtime=0), path   # type: ignore[return-value]

    def rotate_due(self) -> dict[tuple[str, int], int]:
        """Close the topics that reached the message or time limit.

        Returns {(topic, partition): offset to commit} for what was closed.
        """
        now = self.clock.now()
        due = [k for k, st in self._state.items()
               if st.msgs >= self.max_msgs or (now - st.opened_at).total_seconds() >= self.max_s]
        return {k: self._close(k) for k in due}

    def close_all(self) -> dict[tuple[str, int], int]:
        return {k: self._close(k) for k in list(self._state)}

    def _close(self, key: tuple[str, int]) -> int:
        st = self._state.pop(key)
        for stream, path in st.files.values():
            stream.close()
            path.rename(path.with_name(path.name.removesuffix(OPEN_SUFFIX)))
        return st.next_offset
