"""Detector thresholds are calibrated separately for c_vh cells and OTP cells."""

import numpy as np

from services.reliability_monitor.config import load_monitor_config
from services.reliability_monitor.core.detectors import Cusum, Ewma, Shewhart
from services.reliability_monitor.tune_detectors import load_residuals, shewhart_z_max


def test_thresholds_follow_the_indicator_of_the_band():
    z = [1.5] * 12
    e = Ewma(0.2, {"cvh": 0.5, "otp": 50.0})
    assert any(e.update(v, "cvh") for v in z)
    e = Ewma(0.2, {"cvh": 0.5, "otp": 50.0})
    assert not any(e.update(v, "otp") for v in z)
    c = Cusum(0.5, {"cvh": 3.0, "otp": 300.0})
    assert any(c.update(v, "cvh") for v in z)
    c = Cusum(0.5, {"cvh": 3.0, "otp": 300.0})
    assert not any(c.update(v, "otp") for v in z)
    s = Shewhart({"cvh": 1.0, "otp": 5.0})
    assert s.update(2.0, "cvh") and not s.update(2.0, "otp")
    # a plain number still works (one threshold for every indicator)
    assert Shewhart(1.0).update(2.0) and Ewma(0.2, 0.5).update(10.0)


def test_config_and_residuals_have_both_indicators():
    cfg = load_monitor_config()
    for per in (cfg.ewma.L, cfg.cusum.h, cfg.shewhart.z_max):
        assert per.cvh > 0 and per.otp > 0
    pools = load_residuals()
    assert set(pools) == {"cvh", "otp"} and all(len(p) > 500 for p in pools.values())


def test_shewhart_z_max_gives_the_target_arl0_on_each_pool():
    for ind, pool in load_residuals().items():
        z = getattr(load_monitor_config().shewhart.z_max, ind)
        target = load_monitor_config().arl0_target
        assert 0 <= z - shewhart_z_max(pool, target) < 0.0101   # rounded up to 2 decimals
        assert (pool > z).mean() <= 1 / target
    assert np.isfinite(z)
