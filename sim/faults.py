"""Fault catalogue: specs (from YAML/dicts) resolved against a corridor and the sim parameters."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict

from emtra_common.corridor import Corridor
from sim.params import SimConfig

Severity = Literal["low", "medium", "high"]
SEVERITIES = ("low", "medium", "high")
FaultType = Literal["breakdown", "slowdown", "dispatch_delay", "demand_surge", "blocked_segment"]


class FaultSpec(BaseModel):
    """t0 is "HH:MM" Madrid time on the service date, or an aware ISO datetime."""

    model_config = ConfigDict(extra="forbid")
    type: FaultType
    severity: Severity
    t0: str
    line_id: str | None = None      # breakdown, dispatch_delay
    segment_id: int | None = None   # slowdown, blocked_segment
    stop_id: str | None = None      # demand_surge; breakdown (default: first control point)


def load_faults(path: Path | str) -> list[FaultSpec]:
    with open(path, encoding="utf-8") as f:
        return [FaultSpec.model_validate(x) for x in yaml.safe_load(f)["faults"]]


@dataclass
class Fault:
    """A fault resolved to simulation seconds. t0_s/t_end_s are updated when it actually fires."""

    fault_id: str
    spec: FaultSpec
    line_id: str | None
    direction_id: int | None
    segment_id: int
    t0_s: float
    t_end_s: float
    stop_id: str | None = None
    factor: float = 1.0
    duration_s: float | None = None  # None = until the end


def _parse_t0(spec: FaultSpec, corridor: Corridor) -> datetime:
    if "T" in spec.t0:
        return datetime.fromisoformat(spec.t0).astimezone(UTC)
    h, m = map(int, spec.t0.split(":"))
    d = corridor.service_date
    local = datetime(d.year, d.month, d.day, h, m, tzinfo=ZoneInfo(corridor.timezone))
    return local.astimezone(UTC)


def resolve_faults(specs: list[FaultSpec], corridor: Corridor, cfg: SimConfig, start: datetime,
                   end_s: float) -> list[Fault]:
    out = []
    for i, sp in enumerate(specs):
        sev = SEVERITIES.index(sp.severity)
        t0 = (_parse_t0(sp, corridor) - start).total_seconds()
        cat = getattr(cfg.faults, sp.type)
        dur = cat["duration_s"][sev] if "duration_s" in cat else None
        fid = f"F{i + 1:02d}"
        if sp.type in ("breakdown", "dispatch_delay"):
            if sp.line_id is None:
                raise ValueError(f"{sp.type} needs line_id")
            ln = corridor.line(sp.line_id)
            served = corridor.served_stops(ln.line_id)
            stop = sp.stop_id
            if sp.type == "breakdown" and stop is None:
                cps = [c for c in corridor.control_points if c in served[:-1]]
                stop = cps[0] if cps else served[1]
            f = Fault(fid, sp, ln.line_id, ln.direction_id,
                      corridor.segment_of(ln.enter_stop), t0, t0, stop_id=stop,
                      factor=cat["headway_fraction"][sev] if sp.type == "dispatch_delay" else 1.0,
                      duration_s=dur)
            if sp.type == "breakdown":
                f.t_end_s = end_s if dur is None else t0 + dur
        elif sp.type in ("slowdown", "blocked_segment"):
            if sp.segment_id is None:
                raise ValueError(f"{sp.type} needs segment_id")
            f = Fault(fid, sp, None, None, sp.segment_id, t0, t0 + float(dur or 0),
                      factor=cat["factor"][sev] if sp.type == "slowdown" else 1.0, duration_s=dur)
        else:  # demand_surge
            if sp.stop_id is None:
                raise ValueError("demand_surge needs stop_id")
            f = Fault(fid, sp, None, None, corridor.segment_of(sp.stop_id), t0,
                      t0 + float(dur or 0),
                      stop_id=sp.stop_id, factor=cat["factor"][sev], duration_s=dur)
        out.append(f)
    return out


def faults_frame(faults: list[Fault], start: datetime):
    import pandas as pd

    cols = ["fault_id", "type", "severity", "line_id", "direction_id", "segment_id", "t0",
            "t_end"]
    rows = [(f.fault_id, f.spec.type, f.spec.severity, f.line_id, f.direction_id, f.segment_id,
             start + timedelta(seconds=f.t0_s), start + timedelta(seconds=f.t_end_s))
            for f in faults]
    df = pd.DataFrame(rows, columns=cols)
    df["direction_id"] = df["direction_id"].astype("Int64")
    df["t0"] = pd.to_datetime(df["t0"], utc=True)
    df["t_end"] = pd.to_datetime(df["t_end"], utc=True)
    return df
