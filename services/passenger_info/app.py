"""FastAPI adapter: passenger page, stop board, bus map and the WP11 /arrival contract."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from emtra_common.corridor import Corridor
from services.passenger_info import core
from services.passenger_info.config import PassengerInfoConfig
from services.passenger_info.sources import PassengerSource
from services.reliability_monitor.config import load_monitor_config

STATIC = Path(__file__).parent / "static"


def normalize_stop(corridor: Corridor, stop: str) -> str:
    """Accept "S04", "s04" or the stop number "4"."""
    s = stop.strip().upper()
    if s.isdigit():
        s = f"S{int(s):02d}"
    if s not in corridor.stop_ids():
        raise HTTPException(404, f"unknown stop {stop!r}")
    return s


def create_app(source: PassengerSource, corridor: Corridor, cfg: PassengerInfoConfig) -> FastAPI:
    app = FastAPI(title="EMTRA PassengerInfo")
    coords = {s.stop_id: [s.lat, s.lon] for s in corridor.stops}
    otp = load_monitor_config()

    @app.get("/", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/meta")
    def meta() -> dict:
        return {
            "timezone": corridor.timezone,
            "synthetic": corridor.synthetic,
            "source": cfg.source,
            "refresh_s": cfg.refresh_s,
            "stops": [{"stop_id": s.stop_id, "name": s.name, "lat": s.lat, "lon": s.lon,
                       "segment_id": corridor.segment_of(s.stop_id),
                       "lines": core.lines_at(corridor, s.stop_id)} for s in corridor.stops],
            "lines": [{"line_id": ln.line_id,
                       "path": [coords[s] for s in corridor.served_stops(ln.line_id)]}
                      for ln in corridor.lines],
        }

    @app.get("/api/board")
    def board(stop_id: str) -> dict:
        sid = normalize_stop(corridor, stop_id)
        t = source.now()
        lines = core.stop_board(sid, source.snapshot(t), source.schedule(), source.passed(t),
                                source.status(t), t, corridor, cfg.next_scheduled,
                                otp.otp_early_tolerance_s, otp.otp_on_time_s)
        return {"as_of": t, "stop_id": sid, "segment_id": corridor.segment_of(sid),
                "lines": lines}

    @app.get("/api/buses")
    def buses() -> dict:
        t = source.now()
        return {"as_of": t, "buses": core.bus_positions(source.snapshot(t), corridor)}

    @app.get("/arrival")
    def arrival(stop_id: str, line_id: str) -> dict:
        sid = normalize_stop(corridor, stop_id)
        t = source.now()
        info = core.arrival_info(sid, line_id, source.snapshot(t), source.status(t), corridor)
        if info is None:
            raise HTTPException(404, "no estimate for this stop and line")
        return info

    return app
