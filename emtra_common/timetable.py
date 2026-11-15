"""Synthetic timetable (INTERFACES section 2), replaces GTFS."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from emtra_common.bands import BandsConfig, band_group
from emtra_common.config import CONFIG_DIR, SimParams, load_model, load_sim_params
from emtra_common.corridor import BAND_GROUPS, Corridor, load_corridor

COLUMNS = ["line_id", "direction_id", "trip_id", "stop_id", "stop_sequence", "sched_arrival"]


def _group_windows(corridor: Corridor) -> list[tuple[str, datetime, datetime]]:
    """(group, start, end) in UTC for the service date; groups come from config/bands.yaml."""
    cfg = load_model(CONFIG_DIR / "bands.yaml", BandsConfig)
    tz = ZoneInfo(corridor.timezone)
    day = datetime(corridor.service_date.year, corridor.service_date.month,
                   corridor.service_date.day, tzinfo=tz)

    def at(hour: int) -> datetime:
        return (day + timedelta(hours=hour)).astimezone(UTC)

    return [(g, at(lo), at(hi)) for g, (lo, hi) in cfg.groups.items()]


def build_timetable(corridor: Corridor, sim_params: SimParams | None = None) -> pd.DataFrame:
    """One row per (trip, served stop).

    Departures from the line's first stop every headway of each band group, restarting at the
    group start. Arrival at the k-th served stop = departure + k * (link mean + dwell mean).
    """
    sp = sim_params or load_sim_params()
    step = timedelta(seconds=sp.link_mean_s + sp.dwell_mean_s)
    rows = []
    for ln in corridor.lines:
        stops = corridor.served_stops(ln.line_id)
        for group, start, end in _group_windows(corridor):
            dep = start
            while dep < end:
                trip_id = f"{ln.line_id}-{ln.direction_id}-{dep:%Y%m%dT%H%M%S}"
                for k, stop_id in enumerate(stops):
                    rows.append((ln.line_id, ln.direction_id, trip_id, stop_id,
                                 corridor.stop_sequence(stop_id), dep + k * step))
                dep += timedelta(seconds=ln.headway_s[group])
    df = pd.DataFrame(rows, columns=COLUMNS)
    return df.sort_values(["line_id", "trip_id", "stop_sequence"]).reset_index(drop=True)


def scheduled_headway(line_id: str, stop_id: str, band_start: datetime,
                      corridor: Corridor | None = None) -> int:
    """Scheduled headway (s) of a line at a stop in the band starting at band_start."""
    c = corridor or load_corridor()
    if stop_id not in c.served_stops(line_id):
        raise ValueError(f"line {line_id} does not serve {stop_id}")
    return c.line(line_id).headway_s[band_group(band_start)]


def line_type(line_id: str, group: str, corridor: Corridor | None = None) -> str:
    """HF (c_vh) if the scheduled headway is at most hf_max_headway_s, else LF (OTP)."""
    c = corridor or load_corridor()
    if group not in BAND_GROUPS:
        raise ValueError(group)
    return "HF" if c.line(line_id).headway_s[group] <= c.hf_max_headway_s else "LF"
