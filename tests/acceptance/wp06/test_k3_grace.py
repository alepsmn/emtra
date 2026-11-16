"""K3 definition (KPIS.md) and the calibration of the never_fulfilled grace."""

import pandas as pd

from experiments.kpis import false_never_fulfilled
from services.ingestor.config import load_ingestor_config
from services.ingestor.tune_grace import CAP, required_polls
from sim.params import load_sim_config

T = pd.Timestamp("2026-11-16 06:00:00", tz="UTC")


def nf(*rows: tuple[str, int, int]) -> pd.DataFrame:
    """rows: (bus_id, last_seen offset s, last_time_left_s); stop S05, line 19."""
    return pd.DataFrame([{"stop_id": "S05", "line_id": "19", "bus_id": b,
                          "last_seen_at": T + pd.Timedelta(seconds=o), "last_time_left_s": tl}
                         for b, o, tl in rows])


def truth(*rows: tuple[str, int]) -> pd.DataFrame:
    """rows: (bus_id, arrival offset s)."""
    return pd.DataFrame([{"stop_id": "S05", "line_id": "19", "bus_id": b,
                          "arrival_time": T + pd.Timedelta(seconds=o)} for b, o in rows])


def test_false_only_if_the_bus_arrives_within_estimate_plus_tolerance():
    # estimated arrival: last_seen (0) + 600 s. Tolerance 300 s
    n = nf(("early", 0, 600), ("edge", 0, 600), ("late", 0, 600), ("never", 0, 600))
    t = truth(("early", 650), ("edge", 900), ("late", 901))
    assert false_never_fulfilled(n, t, 300) == 2          # 'early' and 'edge' (<= est + 300)
    assert false_never_fulfilled(n, t, 0) == 0
    assert false_never_fulfilled(n, t, 301) == 3


def test_every_emitted_row_counts_separately():
    # the same bus can be never_fulfilled twice at a stop; only the one whose estimate was
    # followed by the real arrival in time is false
    n = nf(("b", 0, 600), ("b", 1000, 300))
    t = truth(("b", 1350))
    assert false_never_fulfilled(n, t, 300) == 1


def test_required_polls_is_the_smallest_value_that_removes_reappearances():
    assert required_polls([]) == 1
    assert required_polls([0, 1, 4]) == 5                 # a 4-poll absence needs N = 5
    assert required_polls([2, 2]) == 3
    assert required_polls([30, 2]) == CAP                 # capped (12 per the decision)
    assert CAP == 12


def test_grace_config_is_coherent():
    cfg = load_ingestor_config()
    poll = load_sim_config().emitter.poll_interval_s
    assert 1 <= cfg.never_fulfilled_grace_polls <= CAP
    # the time condition must not be stricter than the poll count: Nth consecutive missing
    # poll happens (N - 1) polls after the first one
    assert cfg.grace_s == (cfg.never_fulfilled_grace_polls - 1) * poll
