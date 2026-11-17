"""Monitor core: arrivals, estimates and watermarks in; band statuses, alerts and
recommendation requests out."""

from dataclasses import dataclass

import pandas as pd

from contracts.payloads import Alert, Arrival, BandStatus, RecommendationRequest, Watermark
from contracts.raw import RawSnapshot
from emtra_common.bands import band_group
from emtra_common.corridor import Corridor, load_corridor
from services.reliability_monitor.baseline import Baseline, indicator_x
from services.reliability_monitor.config import MonitorConfig, load_monitor_config
from services.reliability_monitor.core.alerts import TernAlerts
from services.reliability_monitor.core.bands import BAND, BandBuffer
from services.reliability_monitor.core.detectors import Cusum, Ewma, Shewhart
from services.reliability_monitor.core.indicators import Indicators, Tern
from services.reliability_monitor.core.recommend import Requester, RequestEvent
from services.reliability_monitor.core.state import CorridorTracker


@dataclass
class TernState:
    ewma: Ewma
    cusum: Cusum
    alerts: TernAlerts
    shewhart: Shewhart | None = None


class Monitor:
    """With `baseline=None` only the indicators are computed (no detectors, so no detector
    alerts); `blocked_segment` alerts still work.

    `shewhart=True` replaces EWMA + CUSUM by a Shewhart chart (K8 comparison only): its alarm is
    reported as trigger "ewma" and the alert's `ewma_w`/`cusum_s` stay 0. It asks for no
    recommendation.

    An `action_required` alert comes with a recommendation request carrying the corridor state
    at the watermark that closed the band (WP8). `speed` is the world's (simulated seconds per
    wall-clock second): the budget of the request is wall-clock time."""

    def __init__(self, timetable: pd.DataFrame, baseline: Baseline | None = None,
                 cfg: MonitorConfig | None = None, corridor: Corridor | None = None,
                 shewhart: bool = False, speed: float = 1.0) -> None:
        self.cfg = cfg or load_monitor_config()
        self.c = corridor or load_corridor()
        self.baseline = baseline
        self.budget_s = self.cfg.recommendation_budget_s / speed
        self.tracker = CorridorTracker(self.c, self.cfg.state_stale_s)
        self._requester = None if shewhart else Requester(self.cfg.rerequest_distance_delta)
        self._buffer = BandBuffer(self.cfg.allowed_lateness_s)
        self._ind = Indicators(timetable, self.c, self.cfg)
        self._state: dict[Tern, TernState] = {
            t: TernState(Ewma(self.cfg.ewma.lam, self.cfg.ewma.L.model_dump()),
                         Cusum(self.cfg.cusum.k, self.cfg.cusum.h.model_dump()),
                         TernAlerts(*t, close_after_bands=self.cfg.close_after_bands),
                         Shewhart(self.cfg.shewhart.z_max.model_dump()) if shewhart else None)
            for t in self.c.terns()}

    @property
    def late_arrivals(self) -> int:
        return self._buffer.late_arrivals

    def state(self, tern: Tern) -> TernState:
        return self._state[tern]

    def on_arrival(self, a: Arrival) -> None:
        self._buffer.add(a)
        self.tracker.on_arrival(a)

    def on_snapshot(self, snap: RawSnapshot) -> None:
        """Estimates only feed the corridor state; indicators and alerts do not read them."""
        self.tracker.on_snapshot(snap)

    def on_watermark(self, w: Watermark) -> list[BandStatus | Alert | RequestEvent]:
        out: list[BandStatus | Alert | RequestEvent] = []
        for band_start, arrivals in self._buffer.close(w.watermark):
            group = band_group(band_start)
            for tb in self._ind.close_band(band_start, arrivals):
                st = self._state[tb.tern]
                line_id, direction_id, segment_id = tb.tern
                out.append(BandStatus(
                    line_id=line_id, direction_id=direction_id, segment_id=segment_id,
                    band_start=band_start, indicator=tb.indicator,  # type: ignore[arg-type]
                    value=tb.value, n_obs=tb.n_obs,
                    status=tb.status, los=tb.los))  # type: ignore[arg-type]
                triggers: list[str] = []
                cell = self.baseline.cell(line_id, segment_id, group) if self.baseline else None
                if tb.value is not None and cell is not None:
                    z = ((indicator_x(tb.indicator, tb.value) - cell.mu0)
                         / max(cell.sigma0, self.cfg.sigma_floor))
                    if st.shewhart is not None:
                        hits = (("ewma", st.shewhart.update(z, tb.indicator)),)
                    else:
                        hits = (("ewma", st.ewma.update(z, tb.indicator)),
                                ("cusum", st.cusum.update(z, tb.indicator)))
                    triggers += [n for n, hit in hits if hit]
                if tb.blocked:
                    triggers.append("blocked_segment")
                alert = st.alerts.step(band_start + BAND, tb.status, triggers,
                                       tb.distance_to_range, st.ewma.w, st.cusum.s)
                if alert is not None:
                    out.append(alert)
                    if self._requester is not None and self._requester.due(alert):
                        out.append(RequestEvent(alert.updated_at, RecommendationRequest(
                            alert_id=alert.alert_id, line_id=line_id, direction_id=direction_id,
                            segment_id=segment_id, budget_s=self.budget_s,
                            state=self.tracker.build(w.watermark))))
                    if alert.state == "closed":
                        st.ewma.reset()
                        st.cusum.reset()
                        if st.shewhart is not None:
                            st.shewhart.reset()
        return out
