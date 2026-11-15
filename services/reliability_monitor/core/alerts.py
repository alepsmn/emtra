"""Alert life cycle of one (line, direction, segment) tern."""

import uuid
from datetime import datetime

from contracts.envelope import NAMESPACE
from contracts.payloads import Alert

DETECTOR_TRIGGERS = ("ewma", "cusum")


def make_alert_id(line_id: str, direction_id: int, segment_id: int,
                  opened_at: datetime) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"alert|{line_id}|{direction_id}|{segment_id}|"
                                 f"{opened_at.isoformat()}")


class TernAlerts:
    """At most one open alert. Opens on a trigger, updates every band while open, closes after
    `close_after_bands` consecutive inside bands (the caller then resets the detectors).
    """

    def __init__(self, line_id: str, direction_id: int, segment_id: int,
                 close_after_bands: int) -> None:
        self.line_id, self.direction_id, self.segment_id = line_id, direction_id, segment_id
        self.close_after = close_after_bands
        self._open: Alert | None = None
        self._inside = 0

    @property
    def is_open(self) -> bool:
        return self._open is not None

    def step(self, band_end: datetime, status: str, triggers: list[str], distance: float,
             ewma_w: float, cusum_s: float) -> Alert | None:
        """One closed band. Returns the alert message to emit (open, update or closed), if any."""
        level = ("action_required" if "blocked_segment" in triggers
                 or (status == "outside" and any(t in DETECTOR_TRIGGERS for t in triggers))
                 else "warning")
        cur = self._open
        if cur is None:
            if not triggers:
                return None
            cur = Alert(
                alert_id=make_alert_id(self.line_id, self.direction_id, self.segment_id, band_end),
                line_id=self.line_id, direction_id=self.direction_id, segment_id=self.segment_id,
                state="open", level=level, triggers=[], distance_to_range=distance,
                ewma_w=ewma_w, cusum_s=cusum_s, opened_at=band_end, updated_at=band_end,
                closed_at=None)
            self._inside = 0
        merged = list(dict.fromkeys([*cur.triggers, *triggers]))   # what fired during its life
        self._inside = self._inside + 1 if status == "inside" else 0
        closing = self._inside >= self.close_after
        alert = cur.model_copy(update={
            "state": "closed" if closing else "open",
            "level": "action_required" if "action_required" in (cur.level, level) else "warning",
            "triggers": merged, "distance_to_range": distance, "ewma_w": ewma_w,
            "cusum_s": cusum_s, "updated_at": band_end,
            "closed_at": band_end if closing else None})
        self._open = None if closing else alert
        return alert
