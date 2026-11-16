"""E2 design: K5 population (high slowdown / blocked_segment, both segments, 10 seeds each)
and the informative faults; paired controls cover every fault seed."""

from collections import Counter

import yaml

from emtra_common.config import ROOT
from experiments.runner import expand, load_scenario

SC = ROOT / "experiments" / "scenarios"


def e2(smoke: bool = False):  # noqa: ANN201
    return expand(load_scenario(SC / "e2_detection.yaml"), smoke)


def faults(specs):  # noqa: ANN001, ANN201
    return [s for s in specs if s.faults]


def test_k5_population_is_ten_seeds_per_type_and_segment():
    cfg = yaml.safe_load((ROOT / "config" / "kpis.yaml").read_text())["params"]
    pop = [s for s in faults(e2()) if s.faults[0]["type"] in cfg["detection_types"]
           and s.faults[0]["severity"] in cfg["detection_severities"]]
    assert cfg["detection_severities"] == ["high"]
    assert len(pop) == 40
    by_cell = Counter((s.faults[0]["type"], s.faults[0]["segment_id"]) for s in pop)
    assert by_cell == {(t, g): 10 for t in ("slowdown", "blocked_segment") for g in (1, 2)}
    for t in ("slowdown", "blocked_segment"):
        for g in (1, 2):
            seeds = {s.seed for s in pop if (s.faults[0]["type"], s.faults[0]["segment_id"])
                     == (t, g)}
            assert len(seeds) == 10 and min(seeds) >= 3000          # validation seeds
    assert all("07:00" <= s.faults[0]["t0"] <= "10:00" for s in pop)    # t0 in am_peak


def test_informative_faults_and_unique_runs():
    specs = e2()
    ids = [s.run_id for s in specs]
    assert len(ids) == len(set(ids))
    assert len([s for s in specs if not s.faults]) == 20                # clean days unchanged
    cfg = yaml.safe_load((ROOT / "config" / "kpis.yaml").read_text())["params"]
    other = Counter((s.faults[0]["type"], s.faults[0]["severity"]) for s in faults(specs)
                    if not (s.faults[0]["type"] in cfg["detection_types"]
                            and s.faults[0]["severity"] in cfg["detection_severities"]))
    expected = {(t, v): 4 for t in ("slowdown", "blocked_segment") for v in ("low", "medium")}
    expected |= {(t, v): 4 for t in ("breakdown", "dispatch_delay", "demand_surge")
                 for v in ("low", "medium", "high")}
    assert other == expected


def test_smoke_has_two_clean_days_and_six_faults():
    specs = e2(smoke=True)
    assert len([s for s in specs if not s.faults]) == 2 and len(faults(specs)) == 6


def test_controls_cover_every_fault_seed():
    ctl = {s.seed for s in expand(load_scenario(SC / "e2_controls.yaml"))}
    assert {s.seed for s in faults(e2())} <= ctl
    ctl_smoke = {s.seed for s in expand(load_scenario(SC / "e2_controls.yaml"), smoke=True)}
    assert {s.seed for s in faults(e2(smoke=True))} <= ctl_smoke
