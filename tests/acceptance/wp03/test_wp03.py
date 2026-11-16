"""WP3 acceptance tests: Ingestor core and batch (no Docker needed)."""

import shutil
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from contracts.payloads import Arrival, NeverFulfilled, Watermark, make_envelope
from contracts.raw import RawArrival, RawSnapshot
from emtra_common.corridor import load_corridor
from services.ingestor.batch import run_batch
from services.ingestor.core import ArrivalDeriver
from sim.run import run_sim

T0 = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)
CORR = load_corridor()
STOPS = CORR.stop_ids()


def snap(stop: str, t: datetime, *buses: tuple[str, str, int]) -> RawSnapshot:
    """One poll; buses are (line_id, bus_id, time_left_s)."""
    return RawSnapshot(
        stop_id=stop, polled_at=t, collector_id="test", stopset_version=1,
        arrivals=[RawArrival(line_id=ln, direction_id=0, bus_id=b, destination="x",
                             time_left_s=tl, distance_m=tl * 5) for ln, b, tl in buses])


def feed(d: ArrivalDeriver, snaps: list[RawSnapshot]) -> list:
    out: list = []
    for s in snaps:
        out += d.process(s)
    return out


def of(out: list, kind: type) -> list:
    return [o for o in out if isinstance(o, kind)]


def s(sec: int) -> datetime:
    return T0 + timedelta(seconds=sec)


def test_midpoint_rule():
    # (a) close to the stop, then gone: arrival at the midpoint
    out = feed(ArrivalDeriver(), [snap("S04", s(0), ("19", "B1", 60)),
                                  snap("S04", s(30), ("19", "B1", 30)),
                                  snap("S04", s(60))])
    (a,) = of(out, Arrival)
    assert a.arrival_time == s(45) and a.uncertainty_s == 15
    assert (a.stop_id, a.line_id, a.bus_id, a.stop_sequence) == ("S04", "19", "B1", 4)
    assert a.last_seen_at == s(30) and a.first_missing_at == s(60)
    assert not of(out, NeverFulfilled)
    # (b) disappears far away and never comes back: never fulfilled, after the grace period
    out = feed(ArrivalDeriver(), [snap("S04", s(0), ("19", "B1", 600)),
                                  snap("S04", s(30), ("19", "B1", 570)),
                                  *[snap("S04", s(60 + 30 * i)) for i in range(8)]])
    (nf,) = of(out, NeverFulfilled)
    assert (nf.bus_id, nf.last_seen_at, nf.last_time_left_s) == ("B1", s(30), 570)
    assert not of(out, Arrival)
    # (c) disappears far away and reappears within the grace period: nothing, then one arrival
    out = feed(ArrivalDeriver(), [snap("S04", s(0), ("19", "B1", 600)),
                                  snap("S04", s(30)),
                                  snap("S04", s(60), ("19", "B1", 60)),
                                  snap("S04", s(90), ("19", "B1", 30)),
                                  *[snap("S04", s(120 + 30 * i)) for i in range(8)]])
    assert not of(out, NeverFulfilled)
    assert len(of(out, Arrival)) == 1 and of(out, Arrival)[0].last_seen_at == s(90)


def test_poll_gap_widens_uncertainty():
    out = feed(ArrivalDeriver(), [snap("S04", s(0), ("19", "B1", 60)),
                                  snap("S04", s(30), ("19", "B1", 30)),
                                  # poll at 60 failed: no record
                                  snap("S04", s(90))])
    (a,) = of(out, Arrival)
    assert a.uncertainty_s == 30 and a.arrival_time == s(60)


def _round(t: int, skip: tuple[str, ...] = ()) -> list[RawSnapshot]:
    return [snap(st, s(t)) for st in STOPS if st not in skip]


def _marks(out: list) -> list[datetime]:
    return [w.watermark for w in of(out, Watermark)]


def test_watermark_is_min_over_stops():
    d = ArrivalDeriver()
    out = feed(d, _round(0))
    assert _marks(out)[-1] == s(0)
    # S05 goes mute: the mark is held at its last poll...
    held: list[datetime] = []
    for t in range(30, 301, 30):
        held += _marks(feed(d, _round(t, skip=("S05",))))
    assert max(held, default=s(0)) <= s(0)
    # ...until it has been silent longer than stale_stop_s (300 s)
    out = feed(d, _round(330, skip=("S05",)))
    assert _marks(out)[-1] == s(330)
    # a stop that is only late (polled one round behind) holds the mark back
    d = ArrivalDeriver()
    feed(d, _round(0))
    out = feed(d, [snap(st, s(30)) for st in STOPS if st != "S09"])
    assert not _marks(out) or max(_marks(out)) <= s(0)
    assert _marks(feed(d, [snap("S09", s(30))]))[-1] == s(30)


def test_watermark_advances_without_arrivals():
    d = ArrivalDeriver()
    marks: list[datetime] = []
    for t in range(0, 301, 30):
        marks += _marks(feed(d, _round(t)))
    assert marks[-1] == s(300)
    assert marks == sorted(set(marks))  # strictly increasing: emitted only on advance


@pytest.fixture(scope="module")
def run3h(tmp_path_factory):
    d = run_sim(seed=3000, hours=3, out=tmp_path_factory.mktemp("wp03"))
    run_batch(d)
    return d


def _truth_and_derived(d):
    tr = pd.read_parquet(d / "truth_arrivals.parquet")
    ar = pd.read_parquet(d / "arrivals.parquet")
    wm = pd.read_parquet(d / "watermarks.parquet")
    # a bus that arrives within the last minute of the run cannot be derived by a causal rule
    tr = tr[tr.arrival_time <= wm.watermark.max() - pd.Timedelta(seconds=60)]
    return tr, ar


def test_arrival_error_vs_truth(run3h):  # K2
    tr, ar = _truth_and_derived(run3h)
    m = tr.merge(ar, on=["stop_id", "line_id", "bus_id"], suffixes=("_true", "_der"))
    err = (m.arrival_time_der - m.arrival_time_true).abs().dt.total_seconds()
    assert len(m) > 500
    assert err.quantile(0.95) <= 15


def test_never_fulfilled_false_rate(run3h):  # K3
    tr, _ = _truth_and_derived(run3h)
    nf = pd.read_parquet(run3h / "never_fulfilled.parquet")
    key = ["stop_id", "line_id", "bus_id"]
    false = nf.merge(tr[key].drop_duplicates(), on=key, how="inner")
    assert len(false) <= 0.01 * max(len(nf), 1)


def test_all_true_arrivals_found(run3h):
    tr, ar = _truth_and_derived(run3h)
    key = ["stop_id", "line_id", "bus_id"]
    found = tr.merge(ar[key].drop_duplicates(), on=key, how="inner")
    assert len(found) >= 0.98 * len(tr)


def _msg_ids(d) -> list:
    ar = pd.read_parquet(d / "arrivals.parquet")
    return [make_envelope("emtra.arrival", Arrival.model_validate(r), producer="t",
                          produced_at=r["arrival_time"]).msg_id for r in ar.to_dict("records")]


def test_msg_ids_deterministic(run3h, tmp_path_factory):
    again = tmp_path_factory.mktemp("again")
    shutil.copytree(run3h / "raw", again / "raw")
    run_batch(again)
    ids = _msg_ids(run3h)
    assert ids and ids == _msg_ids(again)
    assert len(set(ids)) == len(ids)


def test_breakdown_emits_never_fulfilled(tmp_path_factory):  # K3 with a real withdrawal
    d = run_sim(seed=3000, hours=4, out=tmp_path_factory.mktemp("bd"),
                faults=[{"type": "breakdown", "severity": "high", "t0": "08:00", "line_id": "19"}])
    run_batch(d)
    tr = pd.read_parquet(d / "truth_arrivals.parquet")
    ar = pd.read_parquet(d / "arrivals.parquet")
    nf = pd.read_parquet(d / "never_fulfilled.parquet")
    es = pd.read_parquet(d / "estimates.parquet")
    end = pd.read_parquet(d / "watermarks.parquet").watermark.max()
    # the broken bus: a line-19 bus whose last true arrival is short of the line's end, long ago
    last = tr[tr.line_id == "19"].groupby("bus_id").agg(seq=("stop_sequence", "max"),
                                                       t=("arrival_time", "max"))
    broken = last[(last.seq < 12) & (last.t < end - pd.Timedelta(minutes=30))]
    assert len(broken) == 1
    bus = broken.index[0]
    fail_seq = int(broken.seq.iloc[0]) + 1                      # the stop it never reached
    fail_stop = STOPS[fail_seq - 1]
    # downstream stops with a pending estimate when the bus vanished
    t_last = es[es.bus_id == bus].polled_at.max()
    pending = set(es[(es.bus_id == bus) & (es.polled_at == t_last)].stop_id) - {fail_stop}
    assert len(pending) >= 3
    mine = nf[nf.bus_id == bus]
    assert set(mine.stop_id) == pending
    assert (mine.last_seen_at == t_last).all()
    # no derived arrival for that bus downstream of the failure
    assert not ((ar.bus_id == bus) & (ar.stop_id.map(STOPS.index) >= fail_seq)).any()
    # K3 here: never fulfilled that had a true arrival; and false derived arrivals overall
    key = ["stop_id", "line_id", "bus_id"]
    assert len(nf.merge(tr[key].drop_duplicates(), on=key)) <= 0.01 * len(nf)
    m = ar.merge(tr[[*key, "arrival_time"]], on=key, how="left", suffixes=("", "_true"))
    err = (m.arrival_time - m.arrival_time_true).abs().dt.total_seconds()
    false = m[err.isna() | (err > 30)]
    # one is unavoidable: at the failure stop the last sighting (26 s) looks like a normal arrival
    assert set(zip(false.bus_id, false.stop_id, strict=True)) <= {(bus, fail_stop)}


# ---- never_fulfilled_grace_polls: consecutive missing polls of the stop before confirming ----
def _grace_deriver(polls: int) -> ArrivalDeriver:
    """grace_s = 0 isolates the poll count."""
    from services.ingestor.config import IngestorConfig
    return ArrivalDeriver(cfg=IngestorConfig(pass_threshold_s=90, grace_s=0, stale_stop_s=300,
                                             never_fulfilled_grace_polls=polls))


def test_grace_default_is_three_polls():
    from services.ingestor.config import load_ingestor_config
    assert load_ingestor_config().never_fulfilled_grace_polls == 3


def test_grace_polls_reappearing_continues_tracking():
    d = _grace_deriver(3)
    out = feed(d, [snap("S04", s(0), ("19", "B1", 600)),
                   snap("S04", s(30)), snap("S04", s(60)),            # missing 2 polls < 3
                   snap("S04", s(90), ("19", "B1", 480)),             # back: same track goes on
                   snap("S04", s(120), ("19", "B1", 60)),
                   snap("S04", s(150))])                              # close and gone: arrival
    assert not of(out, NeverFulfilled)
    (a,) = of(out, Arrival)
    assert a.arrival_time == s(135) and a.last_seen_at == s(120)      # midpoint rule untouched


def test_grace_polls_confirms_at_the_nth_missing_poll():
    d = _grace_deriver(3)
    out = feed(d, [snap("S04", s(0), ("19", "B1", 600)), snap("S04", s(30)), snap("S04", s(60))])
    assert not of(out, NeverFulfilled)
    out = d.process(snap("S04", s(90)))                               # third consecutive miss
    (nf,) = of(out, NeverFulfilled)
    assert (nf.bus_id, nf.last_seen_at, nf.last_time_left_s) == ("B1", s(0), 600)
    assert not of(feed(d, [snap("S04", s(120))]), NeverFulfilled)     # emitted once


def test_grace_polls_failed_poll_does_not_count():
    d = _grace_deriver(3)
    out = feed(d, [snap("S04", s(0), ("19", "B1", 600)), snap("S04", s(30)), snap("S04", s(60)),
                   snap("S05", s(90)), snap("S05", s(120))])          # S04 poll failed at 90
    assert not of(out, NeverFulfilled)
    (nf,) = of(d.process(snap("S04", s(120))), NeverFulfilled)        # third miss of S04
    assert nf.bus_id == "B1"
