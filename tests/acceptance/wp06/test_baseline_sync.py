"""The detector residuals must correspond to the current baseline (`make baseline`)."""

import shutil
import subprocess

import pytest

from emtra_common.config import CONFIG_DIR, ROOT
from services.reliability_monitor import tune_detectors as td
from services.reliability_monitor.baseline import BASELINE_PATH


def test_residuals_match_baseline():
    """Fails if c19_residuals.npy was not generated from the committed c19.yaml."""
    ok, why = td.residuals_are_current()
    assert ok, f"{why}. Regenerate with `make baseline` and commit the three files."


def test_stale_residuals_are_detected(tmp_path):
    """The check bites: a baseline that changes by one byte invalidates the residuals."""
    base = tmp_path / "c19.yaml"
    shutil.copy(BASELINE_PATH, base)
    assert td.residuals_are_current(base)[0]
    base.write_text(base.read_text() + "# edited\n")
    ok, why = td.residuals_are_current(base)
    assert not ok and "baseline" in why
    # and so does a residuals file that does not match its recorded hash
    res = tmp_path / "c19_residuals.npy"
    shutil.copy(td.RESIDUALS_FILE, res)
    shutil.copy(td.META_FILE, tmp_path / "c19_residuals.meta.yaml")
    shutil.copy(BASELINE_PATH, base)
    assert td.residuals_are_current(base, res, tmp_path / "c19_residuals.meta.yaml")[0]
    res.write_bytes(res.read_bytes()[:-8] + b"\x00" * 8)
    assert not td.residuals_are_current(base, res, tmp_path / "c19_residuals.meta.yaml")[0]


def test_load_residuals_refuses_stale(monkeypatch, tmp_path):
    base = tmp_path / "c19.yaml"
    shutil.copy(BASELINE_PATH, base)
    base.write_text(base.read_text() + "# edited\n")
    monkeypatch.setattr(td, "BASELINE_PATH", base)
    with pytest.raises(td.StaleResidualsError):
        td.load_residuals()


def test_one_make_target_regenerates_baseline_residuals_and_tuner():
    out = subprocess.run(["make", "-n", "baseline"], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout
    assert "services.reliability_monitor.baseline" in out
    assert "services.reliability_monitor.tune_detectors" in out
    assert (CONFIG_DIR / "baselines" / "c19_residuals.meta.yaml").exists()
