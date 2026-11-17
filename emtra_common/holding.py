"""Holding rule (D5; a simplification of Daganzo's headway-based control) and where it applies."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from emtra_common.config import CONFIG_DIR, load_model
from emtra_common.corridor import Corridor

# Fixed texts per action (H3, Table 7): (pros, cons). The evaluator appends its figures.
ACTION_TEXT: dict[str, tuple[str, str]] = {
    "hold": ("no extra resources; quick to apply", "delays the passengers on board"),
    "no_action": ("no intervention; nobody on board is delayed",
                  "the disturbance is left to dissipate on its own"),
}


class HoldingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alpha: float
    hold_max_s: float
    max_candidates: int


def load_holding_config(path: Path | str | None = None) -> HoldingConfig:
    return load_model(path or CONFIG_DIR / "holding.yaml", HoldingConfig)


def hold_seconds(h_sched_s: float, h_fwd_s: float | None, alpha: float,
                 hold_max_s: float) -> float:
    """hold = clip(alpha * (H - h_fwd), 0, hold_max_s).

    `h_sched_s` (H) is the scheduled headway and `h_fwd_s` the headway to the previous bus of
    the same line when leaving the control point; None (no bus ahead known) gives no hold.
    """
    if h_fwd_s is None:
        return 0.0
    return min(max(alpha * (h_sched_s - h_fwd_s), 0.0), hold_max_s)


def hold_control_points(corridor: Corridor, line_id: str, segment_id: int,
                        max_n: int) -> list[str]:
    """Control points where a hold can act on an alert of (line, segment): those the line
    serves, in the alerted segment or after it, the line's last stop excluded; at most `max_n`,
    in travel order. The first one is the fallback's."""
    served = corridor.served_stops(line_id)[:-1]
    points = [s for s in served
              if s in corridor.control_points and corridor.segment_of(s) >= segment_id]
    return points[:max_n]


def hold_params(line_id: str, stop_id: str, cfg: HoldingConfig) -> dict:
    """`params` of a hold candidate (INTERFACES section 4)."""
    return {"stop_id": stop_id, "line_id": line_id, "alpha": cfg.alpha,
            "hold_max_s": cfg.hold_max_s}
