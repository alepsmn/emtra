"""Tables (Markdown + CSV) and the detection-delay figure. PASS/FAIL comes only from
config/kpis.yaml: `evaluate` compares each result with its target, with no per-KPI logic."""

import operator
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from emtra_common.config import CONFIG_DIR, ROOT, load_model
from experiments.kpis import Params

KPI_CONFIG = CONFIG_DIR / "kpis.yaml"
OPS = {">=": operator.ge, "<=": operator.le}
SEV_COLORS = {"low": "#9cc4f2", "medium": "#4f8fdd", "high": "#1f5fb0"}   # one hue, light -> dark


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    experiments: list[str]   # runs the KPI is measured on, pooled; shown under the first
    op: Literal[">=", "<="]
    target: float
    unit: str


class KpiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    params: Params
    kpis: dict[str, Target]


@dataclass
class Row:
    id: str
    target: Target
    value: float | None
    ci: tuple[float, float] | None
    n: int
    status: Literal["PASS", "FAIL", "N/A"]
    detail: dict | None = None


def load_kpi_config(path: Path | str = KPI_CONFIG) -> KpiConfig:
    return load_model(path, KpiConfig)


def evaluate(results: dict[str, dict], targets: dict[str, Target]) -> dict[str, Row]:
    rows = {}
    for kid, r in results.items():
        t = targets[kid]
        v = r["value"]
        status = "N/A" if v is None or r["n"] == 0 else (
            "PASS" if OPS[t.op](v, t.target) else "FAIL")
        rows[kid] = Row(kid, t, v, r["ci"], r["n"], status, r.get("detail"))  # type: ignore[arg-type]
    return rows


def _fmt(v: float | None) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:.3g}" if abs(v) < 100 else f"{v:.0f}"


def _fmt_eff(v: float | None, n: int) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:+.3f} ({n})"


def _kpi_table(rows: dict[str, Row], experiment: str) -> str:
    out = ["| KPI | Measure | Value | 95 % CI | n | Target | |", "|---|---|---|---|---|---|---|"]
    for r in rows.values():
        if r.target.experiments[0] != experiment:
            continue
        ci = "—" if r.ci is None else f"[{_fmt(r.ci[0])}, {_fmt(r.ci[1])}]"
        name = r.target.name.replace("|", "\\|")      # a pipe would split the Markdown cell
        out.append(f"| {r.id} | {name} | {_fmt(r.value)} {r.target.unit} | {ci} | {r.n} "
                   f"| {r.target.op} {_fmt(r.target.target)} {r.target.unit} | **{r.status}** |")
    return "\n".join(out)


def _git_sha() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True,
                             capture_output=True, check=True).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", ".", ":!experiments/reports"],
            cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
        return sha + (" (+ uncommitted changes)" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def delay_figure(delays: pd.DataFrame, path: Path, window_min: float) -> None:
    """Detection delay (t_open - t0) of every matched fault, by type and severity."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    types = sorted(delays.type.unique()) if len(delays) else []
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=150)
    for j, sev in enumerate(SEV_COLORS):
        for i, typ in enumerate(types):
            y = delays[(delays.type == typ) & (delays.severity == sev)].delay_min
            if len(y):
                ax.scatter([i + (j - 1) * 0.22] * len(y), y, s=36, color=SEV_COLORS[sev],
                           edgecolor="white", linewidth=1.5, label=sev)
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles, strict=True))
    if uniq:
        ax.legend(uniq.values(), uniq.keys(), title="severity", frameon=False, loc="upper left")
    ax.axhline(window_min, color="#52514e", linewidth=1, linestyle=":")
    ax.set_xticks(range(len(types)), types)
    ax.set_ylabel("detection delay (min)")
    ax.set_title("E2: delay from fault start to alert opening (matched faults only)", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e5e4e0", linewidth=0.6)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def write_report(rows: dict[str, Row], matrix: pd.DataFrame, delays: pd.DataFrame,
                 by_segment: pd.DataFrame, breakdown: pd.DataFrame, meta: dict, out_dir: Path,
                 name: str, window_min: float) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"kpi": r.id, "value": r.value, "ci_lo": r.ci[0] if r.ci else None,
                   "ci_hi": r.ci[1] if r.ci else None, "n": r.n, "op": r.target.op,
                   "target": r.target.target, "unit": r.target.unit, "status": r.status}
                  for r in rows.values()]).to_csv(out_dir / "kpis.csv", index=False)
    matrix.to_csv(out_dir / "matrix.csv", index=False)
    by_segment.to_csv(out_dir / "recall_by_segment.csv", index=False)
    breakdown.to_csv(out_dir / "breakdown.csv", index=False)
    delay_figure(delays, out_dir / "detection_delay.png", window_min)
    k8 = rows["K8"].detail if "K8" in rows else None
    lines = [f"# H4 validation — {'SMOKE (reduced)' if meta['smoke'] else 'full'}", "",
             "Measured in batch against the simulator's truth. Targets: `config/kpis.yaml`. "
             "A KPI in FAIL is reported as it is.", "",
             "## E1 — no faults: F1 and F3", "", _kpi_table(rows, "E1"), "",
             "## E2 — detection: F9", "", _kpi_table(rows, "E2"), ""]
    if k8:
        lines += [f"K8 detail: recall EWMA+CUSUM {_fmt(k8['recall_ewma_cusum'])} vs Shewhart "
                  f"{_fmt(k8['recall_shewhart'])}; false alerts per line·direction·week "
                  f"{_fmt(k8['false_alerts_ewma_cusum'])} vs {_fmt(k8['false_alerts_shewhart'])} "
                  "(the three detectors are tuned to the same ARL0 on the baseline residuals).", ""]
    lines += ["### Detection matrix (TP / FN / FP per fault type and severity)", "",
              "FP = alerts without a matched fault in those runs; `clean` = fault-free days. "
              "`breakdown`, `dispatch_delay` and `demand_surge` have no detection target.", "",
              "| type | severity | faults | TP | FN | FP |", "|---|---|---|---|---|---|"]
    lines += [f"| {r.type} | {r.severity} | {r.faults} | {r.tp} | {r.fn} | {r.fp} |"
              for r in matrix.itertuples()]
    lines += ["", "### Recall of the K5 population by segment (medium/high faults)", "",
              "| type | segment | faults | TP |", "|---|---|---|---|"]
    lines += [f"| {r.type} | {r.segment} | {r.faults} | {r.tp} |" for r in by_segment.itertuples()]
    lines += ["", "### E2 by fault type, severity, segment and hour (no relocation)", "",
              "Detected = a matched alert of any level. Effect on the truth, independent of the "
              "detector: mean difference between the fault run and the same seed without fault, "
              "of the true c_vh (HF terns, Δc_vh > 0 is worse) and the true OTP (LF terns, "
              "ΔOTP < 0 is worse) of the affected segment (the fault's line if it has one, else "
              "every line of the segment), over the complete 15-min bands inside the hour "
              "after t0. `pairs` = (tern, band) pairs averaged.", "",
              "| type | severity | segment | hour | detected/n | median delay (min) | Δc_vh "
              "(pairs) | ΔOTP (pairs) |", "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r.type} | {r.severity} | {r.segment} | {r.hour:02d} | {r.detected}/{r.n} "
              f"| {_fmt(r.median_delay_min)} | {_fmt_eff(r.d_cvh, r.n_cvh)} "
              f"| {_fmt_eff(r.d_otp, r.n_otp)} |" for r in breakdown.itertuples()]
    lines += ["", "![Detection delay](detection_delay.png)", "",
              "---", f"Seeds: {meta['seeds']}. Runs: {meta['runs']}. Git: {meta['git']}."]
    path = out_dir / name
    path.write_text("\n".join(lines) + "\n")
    return path
