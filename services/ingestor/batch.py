"""Batch mode (INTERFACES section 9): raw/ -> estimates, arrivals, never_fulfilled, watermarks."""

from pathlib import Path

import pandas as pd

from contracts.payloads import Arrival, NeverFulfilled, Watermark
from services.ingestor.config import IngestorConfig
from services.ingestor.core import ArrivalDeriver
from services.ingestor.sources import FileSource

ESTIMATE_COLS = ["polled_at", "stop_id", "line_id", "direction_id", "bus_id", "time_left_s",
                 "distance_m"]


def run_batch(run_dir: Path, cfg: IngestorConfig | None = None) -> None:
    run_dir = Path(run_dir)
    deriver = ArrivalDeriver(cfg=cfg)
    est: list[tuple] = []
    out: dict[type, list[dict]] = {Arrival: [], NeverFulfilled: [], Watermark: []}
    for snap in FileSource(run_dir / "raw" / "snapshots.jsonl.gz").snapshots():
        est += [(snap.polled_at, snap.stop_id, a.line_id, a.direction_id, a.bus_id,
                 a.time_left_s, a.distance_m) for a in snap.arrivals]
        for o in deriver.process(snap):
            out[type(o)].append(o.model_dump())
    pd.DataFrame(est, columns=ESTIMATE_COLS).to_parquet(run_dir / "estimates.parquet",
                                                         index=False)
    for typ, name in ((Arrival, "arrivals"), (NeverFulfilled, "never_fulfilled"),
                      (Watermark, "watermarks")):
        cols = list(typ.model_fields)
        pd.DataFrame(out[typ], columns=cols).to_parquet(run_dir / f"{name}.parquet", index=False)
