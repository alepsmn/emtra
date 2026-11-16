"""Effect of a fault on the truth: worst change over the affected segment and its neighbours."""

from datetime import UTC, datetime, timedelta

import pandas as pd

from experiments.kpis import effect_from_status
from experiments.matching import FaultEvent

T0 = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)          # 07:00 Madrid, band aligned


def status(rows: list[tuple]) -> pd.DataFrame:
    """rows: (line_id, segment_id, band offset in bands, indicator, value)."""
    return pd.DataFrame([{"line_id": ln, "direction_id": 0, "segment_id": seg,
                          "band_start": pd.Timestamp(T0 + timedelta(minutes=15 * k)),
                          "indicator": ind, "value": v} for ln, seg, k, ind, v in rows])


def fault(segment: int = 1, line: str | None = None) -> FaultEvent:
    return FaultEvent(fault_id="f", type="slowdown", line_id=line,
                      direction_id=None if line is None else 0, segment_id=segment, t0=T0)


def test_effect_is_the_worst_over_segment_and_adjacent_segments():
    ctl = status([("19", s, k, "cvh", 0.2) for s in (1, 2, 3) for k in range(4)]
                 + [("170", s, k, "otp", 0.9) for s in (1, 2, 3) for k in range(4)])
    flt = status([("19", 1, k, "cvh", 0.2) for k in range(4)]            # no effect at the fault
                 + [("19", 2, k, "cvh", 0.4) for k in range(4)]          # +0.2 next door
                 + [("19", 3, k, "cvh", 0.7) for k in range(4)]          # not adjacent to 1
                 + [("170", 1, k, "otp", 0.8) for k in range(4)]         # -0.1
                 + [("170", 2, k, "otp", 0.6) for k in range(4)]         # -0.3
                 + [("170", 3, k, "otp", 0.0) for k in range(4)])
    e = effect_from_status(flt, ctl, fault(segment=1), tolerance=1)
    assert abs(e["d_cvh"] - 0.2) < 1e-9 and e["cvh_segment"] == 2
    assert abs(e["d_otp"] - (-0.3)) < 1e-9 and e["otp_segment"] == 2
    # from segment 2, segment 1 and 3 are both neighbours: the worst c_vh is segment 3's
    e = effect_from_status(flt, ctl, fault(segment=2), tolerance=1)
    assert abs(e["d_cvh"] - 0.5) < 1e-9 and e["cvh_segment"] == 3


def test_effect_counts_complete_bands_in_the_hour_after_t0_and_the_faults_line():
    ctl = status([("19", 1, k, "cvh", 0.2) for k in range(8)]
                 + [("C1", 1, k, "cvh", 0.2) for k in range(8)])
    flt = status([("19", 1, k, "cvh", 0.2 + (0.1 if k < 4 else 5.0)) for k in range(8)]
                 + [("C1", 1, k, "cvh", 9.0) for k in range(8)])
    e = effect_from_status(flt, ctl, fault(segment=1, line="19"), tolerance=1)
    assert abs(e["d_cvh"] - 0.1) < 1e-9 and e["n_cvh"] == 4   # bands 4..7 are after t0 + 1 h
    assert e["d_otp"] is None and e["n_otp"] == 0
