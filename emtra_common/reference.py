"""Initial FogStore load: stops and schedule. `python -m emtra_common.reference load`."""

import sys

from emtra_common.corridor import load_corridor
from emtra_common.db import apply_migrations, connect
from emtra_common.timetable import build_timetable


def load(dsn: str | None = None) -> int:
    """Replace stops and schedule from the corridor config. Returns the schedule row count."""
    corridor = load_corridor()
    tt = build_timetable(corridor)
    stops = [(s.stop_id, s.name, s.lat, s.lon, corridor.stop_sequence(s.stop_id),
              corridor.segment_of(s.stop_id)) for s in corridor.stops]
    rows = list(tt.itertuples(index=False, name=None))
    with connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM schedule")
        cur.execute("DELETE FROM stops")
        cur.executemany("INSERT INTO stops VALUES (%s, %s, %s, %s, %s, %s)", stops)
        cur.executemany("INSERT INTO schedule VALUES (%s, %s, %s, %s, %s, %s)", rows)
    return len(rows)


if __name__ == "__main__":
    if sys.argv[1:] != ["load"]:
        sys.exit("usage: python -m emtra_common.reference load")
    apply_migrations()
    print(f"loaded 12 stops and {load()} schedule rows")
