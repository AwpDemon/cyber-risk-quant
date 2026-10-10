"""Static report figures (PNG) for the README and case studies.

Palette: first three categorical slots of a CVD-validated palette (blue, orange,
aqua) - validated all-pairs; aqua is below 3:1 contrast on the light surface, so
every multi-series chart carries direct labels. Grid and axes are recessive.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import textwrap  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter, PercentFormatter  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
NEUTRAL = "#b9b8b3"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10, "axes.titlesize": 12,
    "axes.titleweight": "bold", "axes.titlelocation": "left", "legend.frameon": False, "text.parse_math": False,
})


def money(x, _=None) -> str:
    x = float(x)
    if abs(x) >= 1e6:
        return f"${x / 1e6:.1f}M"
    if abs(x) >= 1e4:
        return f"${x / 1e3:.0f}K"
    return f"${x:,.0f}"


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


def loss_exceedance(results: dict[str, object], title: str, path: Path) -> Path:
    """LEC: P(annual loss > x) on a log money axis, one line per control posture."""
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    xs = np.logspace(3, 7.3, 300)
    for color, (label, res) in zip(SERIES, results.items()):
        ys = res.exceedance(xs)
        ax.plot(xs, ys, color=color, lw=2, label=label)
        # direct label at the $100K point
        y100 = float(res.exceedance([1e5])[0])
        ax.annotate(f"{label}: {y100:.2%} > $100K", (1e5, y100), xytext=(6, 4), textcoords="offset points",
                    fontsize=8.5, color=INK_2)
        ax.plot([1e5], [y100], "o", ms=5, color=color, mec=SURFACE, mew=1.5)
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(FuncFormatter(money))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=1))
    ax.set_xlabel("Annual loss (gross, before insurance)")
    ax.set_ylabel("Probability annual loss exceeds x")
    ax.set_ylim(0, None)
    ax.set_title(title)
    ax.legend(loc="upper right", fontsize=9)
    return _save(fig, path)


def tornado(df: pd.DataFrame, title: str, path: Path) -> Path:
    base = df.attrs["base_ale"]
    d = df.iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 0.42 * len(d) + 1.2))
    y = np.arange(len(d))
    lo = d["ale_at_p10"].to_numpy()
    hi = d["ale_at_p90"].to_numpy()
    ax.barh(y, lo - base, left=base, color=SERIES[0], height=0.62, label="Parameter at its P10")
    ax.barh(y, hi - base, left=base, color=SERIES[1], height=0.62, label="Parameter at its P90")
    ax.axvline(base, color=INK_2, lw=1)
    ax.set_yticks(y, [f"{p}  ({b})" for p, b in zip(d["parameter"], d["basis"])], fontsize=8.5)
    ax.xaxis.set_major_formatter(FuncFormatter(money))
    ax.set_xlabel(f"Annualized loss expectancy (baseline {money(base)})")
    ax.set_title(title)
    ax.legend(loc="lower right", fontsize=8.5)
    ax.grid(axis="y", visible=False)
    return _save(fig, path)


def control_value(gaps: dict[str, pd.DataFrame], path: Path) -> Path:
    """ALE reduction from fixing each gap alone, per case study (small multiples, shared x)."""
    cases = list(gaps)
    fig, axes = plt.subplots(1, len(cases), figsize=(4.2 * len(cases), 4.6), sharex=True)
    for ax, color, case in zip(np.atleast_1d(axes), SERIES, cases):
        g = gaps[case]
        g = g[g["modelled"]].sort_values("delta_ale")
        labels = [f"{q}{' *' if c else ''}" for q, c in zip(g["question"], g["critical"])]
        ax.barh(labels, g["delta_ale"], color=color, height=0.62)
        for yi, v in enumerate(g["delta_ale"]):
            ax.text(v, yi, f" {money(v)}", va="center", fontsize=8, color=INK_2)
        ax.set_title(case, fontsize=11)
        ax.xaxis.set_major_formatter(FuncFormatter(money))
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="y", labelsize=8.5)
    fig.suptitle("ALE reduction from closing each gap on its own  (* = knock-out control)", x=0.01, ha="left",
                 fontweight="bold", fontsize=12)
    fig.supxlabel("Reduction in annualized loss expectancy", fontsize=10, color=INK_2)
    fig.tight_layout()
    return _save(fig, path)


def scenario_mix(rows: pd.DataFrame, path: Path) -> Path:
    """Stacked bars: ALE by scenario for each case (current posture)."""
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    cases = rows.index.tolist()
    left = np.zeros(len(cases))
    for color, col in zip(SERIES, rows.columns):
        vals = rows[col].to_numpy()
        ax.barh(cases, vals, left=left, color=color, height=0.55, label=col, edgecolor=SURFACE, linewidth=2)
        for yi, (lft, v) in enumerate(zip(left, vals)):
            if v > 0.12 * rows.sum(axis=1).max():
                ax.text(lft + v / 2, yi, money(v), ha="center", va="center", fontsize=8.5, color="white")
        left += vals
    for yi, tot in enumerate(left):
        ax.text(tot, yi, f"  {money(tot)}", va="center", fontsize=9, color=INK)
    ax.xaxis.set_major_formatter(FuncFormatter(money))
    ax.set_xlabel("Annualized loss expectancy, current controls")
    ax.set_title("Where the expected loss comes from")
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.22), ncol=3, fontsize=8.5)
    ax.grid(axis="y", visible=False)
    ax.invert_yaxis()
    return _save(fig, path)


def kev_patch(lags: np.ndarray, sla_table: pd.DataFrame, path: Path) -> Path:
    """Left: CDF of days from CVE publication to CISA KEV listing. Right: exposure removed by a patch SLA."""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.8))
    t = np.sort(np.clip(lags, 0, None))
    a1.step(t, np.arange(1, t.size + 1) / t.size, where="post", color=SERIES[0], lw=2)
    for d in (7, 30, 90):
        share = float((lags <= d).mean())
        a1.plot([d], [share], "o", color=SERIES[0], ms=5, mec=SURFACE, mew=1.5)
        a1.annotate(f"{share:.0%} by day {d}", (d, share), xytext=(6, -12), textcoords="offset points", fontsize=8.5, color=INK_2)
    a1.set_xscale("symlog", linthresh=1)
    a1.set_xlim(0, 1500)
    a1.xaxis.set_major_locator(FixedLocator([0, 1, 7, 30, 90, 365, 1000]))
    a1.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    a1.xaxis.set_minor_formatter(NullFormatter())
    a1.yaxis.set_major_formatter(PercentFormatter(1.0))
    a1.set_xlabel("Days from NVD publication to KEV listing")
    a1.set_ylabel("Share of exploited CVEs")
    a1.set_title(f"Days to KEV listing (n={lags.size} CVEs, 2022+)", fontsize=10.5)
    a2.plot(sla_table["sla_days"], sla_table["exposure_removed"], color=SERIES[0], lw=2, marker="o", ms=5, mec=SURFACE)
    for _, r in sla_table.iterrows():
        a2.annotate(f"{r.exposure_removed:.0%}", (r.sla_days, r.exposure_removed), xytext=(4, 4), textcoords="offset points", fontsize=8, color=INK_2)
    a2.yaxis.set_major_formatter(PercentFormatter(1.0))
    a2.set_xlabel("Patch SLA (days after publication)")
    a2.set_ylabel("Exploitable exposure removed")
    a2.set_title("Patch SLA -> share of exposure-days avoided", fontsize=10.5)
    fig.tight_layout()
    return _save(fig, path)


def validation(df: pd.DataFrame, path: Path) -> Path:
    """Model / published-midpoint ratio for each benchmark, log scale, 0.8-1.2 band shaded."""
    d = df[df["published_low"] > 0].iloc[::-1].reset_index(drop=True)  # one-sided bounds are reported in the table only
    fig, ax = plt.subplots(figsize=(8.5, 0.55 * len(d) + 1.4))
    ax.axvspan(0.8, 1.25, color=GRID, alpha=0.7, lw=0)
    ax.axvline(1.0, color=INK_2, lw=1)
    markers = {"out-of-sample": ("o", SERIES[0]), "post-hoc": ("s", SERIES[1]), "partly in-sample": ("D", SERIES[2])}
    for kind, (mk, col) in markers.items():
        sub = d[d["sample"] == kind]
        ax.scatter(sub["model_vs_mid"], sub.index, marker=mk, s=60, color=col, edgecolor=SURFACE, linewidth=1.5, label=kind, zorder=3)
    for i, r in d.iterrows():
        ax.annotate(f"{r.model_vs_mid:.2f}x", (r.model_vs_mid, i), xytext=(8, -3), textcoords="offset points", fontsize=8.5, color=INK_2)
    ax.set_yticks(d.index, [f"{n}\nvs {textwrap.shorten(b, 62, placeholder='...')}" for n, b in zip(d["name"], d["benchmark"])],
                  fontsize=7.5)
    ax.set_xscale("log")
    ax.set_xlim(0.4, 2.5)
    ax.xaxis.set_major_locator(FixedLocator([0.5, 0.8, 1.0, 1.25, 2.0]))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}x"))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_xlabel("Model / published value (1.0 = agreement; shaded = within -20%/+25%)")
    ax.set_title("Model vs public benchmarks")
    ax.legend(loc="lower right", fontsize=8.5)
    ax.grid(axis="y", visible=False)
    return _save(fig, path)


def hhs_records(model_q: dict, hhs_records: np.ndarray, records_held: int, path: Path) -> Path:
    """ECDF of HHS dental breach sizes vs the model's breach-size distribution for the case clinic."""
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    x = np.sort(hhs_records)
    ax.step(x, np.arange(1, x.size + 1) / x.size, where="post", color=SERIES[0], lw=2, label=f"HHS dental breaches, all sizes (n={x.size})")
    xs = np.sort(model_q)
    ax.step(xs, np.arange(1, xs.size + 1) / xs.size, where="post", color=SERIES[1], lw=2, label=f"Model, case clinic ({records_held:,} patients), events >= 500")
    ax.axvline(records_held, color=NEUTRAL, lw=1, ls="--")
    ax.annotate("case clinic's whole\npatient base", (records_held, 0.08), xytext=(6, 0), textcoords="offset points", fontsize=8, color=INK_2)
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_xlabel("Individuals affected per breach")
    ax.set_ylabel("Cumulative share")
    ax.set_title("Breach size: HHS dental reports vs model")
    ax.legend(loc="center right", fontsize=8.5)
    return _save(fig, path)
