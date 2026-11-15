"""Corridor model and loader (INTERFACES section 1)."""

from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, model_validator

from emtra_common.config import CONFIG_DIR, load_model

BAND_GROUPS = ("early", "am_peak", "midday", "pm_peak", "evening")
MIN_STOPS_PER_TERN = 3


class Stop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stop_id: str
    name: str
    lat: float
    lon: float
    bays: int


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: int
    stop_ids: list[str]


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: str
    direction_id: int
    enter_stop: str
    exit_stop: str
    capacity: int
    headway_s: dict[str, int]


class Corridor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corridor_id: str
    timezone: str
    synthetic: bool
    service_date: date
    service_window: tuple[str, str]
    hf_max_headway_s: int
    stops: list[Stop]
    segments: list[Segment]
    lines: list[Line]
    control_points: list[str]

    @model_validator(mode="after")
    def _consistent(self) -> "Corridor":
        ids = [s.stop_id for s in self.stops]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate stop_id")
        flat = [sid for seg in self.segments for sid in seg.stop_ids]
        if flat != ids:
            raise ValueError("segments must cover all stops in order, without overlap")
        seg_ids = [s.segment_id for s in self.segments]
        if seg_ids != sorted(seg_ids) or len(set(seg_ids)) != len(seg_ids):
            raise ValueError("segment_id must be unique and ordered")
        for ln in self.lines:
            if ln.enter_stop not in ids or ln.exit_stop not in ids:
                raise ValueError(f"line {ln.line_id}: unknown enter/exit stop")
            if ids.index(ln.enter_stop) >= ids.index(ln.exit_stop):
                raise ValueError(f"line {ln.line_id}: enter_stop must precede exit_stop")
            if set(ln.headway_s) != set(BAND_GROUPS):
                raise ValueError(f"line {ln.line_id}: headway_s needs {BAND_GROUPS}")
        if not set(self.control_points) <= set(ids):
            raise ValueError("unknown control point")
        return self

    # -- queries --------------------------------------------------------------------------
    def stop_ids(self) -> list[str]:
        return [s.stop_id for s in self.stops]

    def stop_sequence(self, stop_id: str) -> int:
        return self.stop_ids().index(stop_id) + 1

    def segment_of(self, stop_id: str) -> int:
        for seg in self.segments:
            if stop_id in seg.stop_ids:
                return seg.segment_id
        raise KeyError(stop_id)

    def line(self, line_id: str) -> Line:
        for ln in self.lines:
            if ln.line_id == line_id:
                return ln
        raise KeyError(line_id)

    def served_stops(self, line_id: str) -> list[str]:
        ln = self.line(line_id)
        ids = self.stop_ids()
        return ids[ids.index(ln.enter_stop) : ids.index(ln.exit_stop) + 1]

    def _served_in_segment(self, ln: Line, segment_id: int) -> int:
        seg = next(s for s in self.segments if s.segment_id == segment_id)
        served = set(self.served_stops(ln.line_id))
        return sum(1 for sid in seg.stop_ids if sid in served)

    def lines_in_segment(self, segment_id: int) -> list[Line]:
        """Lines serving at least 3 stops of the segment."""
        return [ln for ln in self.lines
                if self._served_in_segment(ln, segment_id) >= MIN_STOPS_PER_TERN]

    def terns(self) -> list[tuple[str, int, int]]:
        """(line_id, direction_id, segment_id) triples that exist."""
        return [(ln.line_id, ln.direction_id, seg.segment_id)
                for seg in self.segments for ln in self.lines_in_segment(seg.segment_id)]


@lru_cache
def load_corridor(path: Path | str | None = None) -> Corridor:
    return load_model(path or CONFIG_DIR / "corridors" / "c19.yaml", Corridor)


def service_datetime(corridor: Corridor, hhmm: str) -> datetime:
    """UTC instant of "HH:MM" local time on the corridor's service date."""
    h, m = map(int, hhmm.split(":"))
    d = corridor.service_date
    local = datetime(d.year, d.month, d.day, h, m, tzinfo=ZoneInfo(corridor.timezone))
    return local.astimezone(UTC)
