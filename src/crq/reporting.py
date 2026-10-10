"""`crq report`: regenerate every number, figure, workbook and case study in the repo.

Outputs (all deterministic for a given seed):
  docs/results/results.json        headline numbers quoted in README / case studies
  docs/results/validation.json     benchmark checks + HHS comparison
  docs/figures/*.png               figures embedded in README
  docs/case_studies/<case>.md      case-study write-ups (tables generated, commentary from the profile YAML)
  examples/<case>_underwriting.xlsx underwriter workbooks
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import plots
from .analysis import ale_uncertainty, gap_analysis, gaps_frame, indicated_premium, load_case, packages, tornado
from .calibration import PROCESSED
from .controls import CONTROL_LABELS, kev_lags, patch_exposure_reduction
from .data_sources import REPO_ROOT
from .excel import write_workbook
from .model import SCENARIO_LABELS
from .parameters import ParameterSet
from .questionnaire import assess, load_questions
from .validation import breach_records_sample, checks_frame, control_effect_checks, frequency_checks, hhs_dental_check, severity_checks

CASES = ("dental_clinic", "law_firm", "manufacturer")
SHORT = {"dental_clinic": "Dental clinic", "law_firm": "Law firm", "manufacturer": "Manufacturer"}
PKG_LABEL = {"current": "Current controls", "fix_knockouts": "Fix knock-outs only", "all_controls": "All controls best-practice"}


def _metrics(res, params) -> dict:
    return {
        "ale": res.ale,
        "ale_mc_se": res.ale_se,
        "p_any_event": res.prob_any_event(),
        "p_loss_gt_100k": float(res.exceedance([1e5])[0]),
        "p_loss_gt_1m": float(res.exceedance([1e6])[0]),
        "p99": res.quantile(0.99),
        "tvar99": res.tvar(0.99),
        "expected_insured_loss": float(res.insured_loss.mean()),
        "indicated_premium": indicated_premium(res, params),
        "ale_by_scenario": res.ale_by_scenario(),
        "events_per_year": {s: float(v.mean()) for s, v in res.lam.items()},
    }


def run_case(case_id: str, n: int, seed: int, params: ParameterSet, fig_dir: Path, tornado_n: int) -> tuple[dict, pd.DataFrame]:
    case = load_case(REPO_ROOT / "profiles" / f"{case_id}.yaml")
    a, base, rows = gap_analysis(case, n=n, seed=seed, params=params)
    pk = packages(case, n=n, seed=seed, params=params)
    gdf = gaps_frame(rows)
    tor = tornado(case, n=tornado_n, seed=seed, params=params)
    unc = ale_uncertainty(case, seed=seed, params=params)
    write_workbook(case, a, base, rows, pk, REPO_ROOT / "examples" / f"{case_id}_underwriting.xlsx", params)
    plots.loss_exceedance({PKG_LABEL[k]: v[1] for k, v in pk.items()}, f"{case.profile.name}: loss exceedance curve",
                          fig_dir / f"lec_{case_id}.png")
    plots.tornado(tor, f"{case.profile.name}: what drives the ALE estimate (top {len(tor)} inputs)", fig_dir / f"tornado_{case_id}.png")
    out = {
        "name": case.profile.name, "industry": case.profile.industry, "employees": case.profile.employees,
        "annual_revenue": case.profile.annual_revenue, "records_held": case.profile.records_held,
        "policy": case.profile.policy.__dict__, "readiness_score": a.score, "tier": a.tier,
        "critical_failures": a.critical_failures,
        "packages": {k: {"score": v[0].score, "tier": v[0].tier, **_metrics(v[1], params)} for k, v in pk.items()},
        "ale_interval_parameter_uncertainty": unc,
        "gaps": json.loads(gdf.drop(columns=["evidence"]).to_json(orient="records")),
        "tornado": json.loads(tor.to_json(orient="records")), "tornado_base_ale": tor.attrs["base_ale"],
        "components_current": pk["current"][1].component_means(),
    }
    return out, gdf


def _fmt_money(x) -> str:
    return plots.money(x) if x is not None and np.isfinite(x) else "n/a"


def write_case_study(case_id: str, r: dict, path: Path) -> None:
    case = load_case(REPO_ROOT / "profiles" / f"{case_id}.yaml")
    qs = load_questions()
    cur, kn, al = (r["packages"][k] for k in ("current", "fix_knockouts", "all_controls"))
    lines = [
        f"# Case study: {r['name']}",
        "",
        "> **Hypothetical firm.** Not a real business and not built from any client's data. Every number below is model output "
        "from `crq report` (seed 20261010) using public data and the stated assumptions in `config/parameters.yaml`.",
        "",
        f"{case.profile.description}",
        "",
        f"| Profile | |\n|---|---|\n| Employees / endpoints | {r['employees']} / {case.profile.n_endpoints} |\n"
        f"| Annual revenue | {_fmt_money(r['annual_revenue'])} |\n| Records held | {r['records_held']:,} |\n"
        f"| Policy | {_fmt_money(r['policy']['aggregate_limit'])} aggregate, {_fmt_money(r['policy']['retention'])} retention, "
        f"{_fmt_money(r['policy']['social_engineering_sublimit'])} social-engineering sublimit |",
        "",
        "## Underwriting readiness",
        "",
        f"**Score {r['readiness_score']:.0f}/100, tier: {r['tier']}.**"
        + (f" Knock-out gaps: {', '.join('`' + c + '`' for c in r['critical_failures'])}." if r["critical_failures"] else " No knock-out gaps."),
        "",
        "| Control | Current answer |\n|---|---|",
    ]
    for qid, q in qs.items():
        lines.append(f"| {qid} | {q.options[case.answers[qid]]['label']} |")
    lines += [
        "",
        "## Quantified risk (100,000 simulated years per posture)",
        "",
        "| Metric | Current | Fix knock-outs only | All controls best-practice |",
        "|---|---|---|---|",
        f"| Readiness score / tier | {cur['score']:.0f} / {cur['tier']} | {kn['score']:.0f} / {kn['tier']} | {al['score']:.0f} / {al['tier']} |",
        f"| Annualized loss expectancy (ALE) | {_fmt_money(cur['ale'])} | {_fmt_money(kn['ale'])} | {_fmt_money(al['ale'])} |",
        f"| Monte Carlo standard error of ALE | {_fmt_money(cur['ale_mc_se'])} | {_fmt_money(kn['ale_mc_se'])} | {_fmt_money(al['ale_mc_se'])} |",
        f"| P(any loss event in a year) | {cur['p_any_event']:.2%} | {kn['p_any_event']:.2%} | {al['p_any_event']:.2%} |",
        f"| P(annual loss > $100K) | {cur['p_loss_gt_100k']:.2%} | {kn['p_loss_gt_100k']:.2%} | {al['p_loss_gt_100k']:.2%} |",
        f"| P(annual loss > $1M) | {cur['p_loss_gt_1m']:.3%} | {kn['p_loss_gt_1m']:.3%} | {al['p_loss_gt_1m']:.3%} |",
        f"| TVaR 99% (mean of worst 1% of years) | {_fmt_money(cur['tvar99'])} | {_fmt_money(kn['tvar99'])} | {_fmt_money(al['tvar99'])} |",
        f"| Expected insured loss | {_fmt_money(cur['expected_insured_loss'])} | {_fmt_money(kn['expected_insured_loss'])} | {_fmt_money(al['expected_insured_loss'])} |",
        f"| Indicated premium (loss-cost view) | {_fmt_money(cur['indicated_premium'])} | {_fmt_money(kn['indicated_premium'])} | {_fmt_money(al['indicated_premium'])} |",
        "",
        f"ALE 90% interval from parameter uncertainty alone (two-dimensional Monte Carlo, "
        f"{r['ale_interval_parameter_uncertainty']['n_parameter_draws']} parameter draws x "
        f"{r['ale_interval_parameter_uncertainty']['years_per_draw']:,} years): "
        f"**{_fmt_money(r['ale_interval_parameter_uncertainty']['ale_p05'])} to {_fmt_money(r['ale_interval_parameter_uncertainty']['ale_p95'])}**.",
        "",
        "Why no 95th percentile? Fewer than 5% of simulated years contain any loss, so the 95th-percentile annual loss is $0. "
        "Tail risk is reported as exceedance probabilities and TVaR instead.",
        "",
        f"![Loss exceedance curve](../figures/lec_{case_id}.png)",
        "",
        "### Expected loss by scenario (current controls)",
        "",
        "| Scenario | Events / year | ALE | Share |",
        "|---|---|---|---|",
    ]
    for s, v in cur["ale_by_scenario"].items():
        lines.append(f"| {SCENARIO_LABELS[s]} | {cur['events_per_year'][s]:.4f} | {_fmt_money(v)} | {v / cur['ale']:.0%} |")
    lines += [
        "",
        "## Gap list: what each fix is worth on its own",
        "",
        "One control changed at a time, same random numbers (common random numbers), so the differences are not sampling noise "
        "(the shared noise cancels; every simulated year is at least as cheap after a fix, which the test-suite checks). "
        "The rows do not add up to the package results above, because controls overlap (MFA and EDR both stop some of the same intrusions).",
        "",
        "| Control | Knock-out | Recommended | ALE reduction | TVaR99 reduction | Indicated premium change | Illustrative annual cost | Evidence strength |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for g in r["gaps"]:
        prem = f"{g['premium_change_pct']:.1f}%" if g["premium_change_pct"] is not None and np.isfinite(g["premium_change_pct"]) else "qualitative only"
        lines.append(f"| {g['question']} | {'**yes**' if g['critical'] else ''} | {g['recommended']} | {_fmt_money(g['delta_ale'])} | "
                     f"{_fmt_money(g['delta_tvar99'])} | {prem} | {_fmt_money(g['annual_cost'])} | {g['evidence_strength']} |")
    lines += [
        "",
        "Indicated premium change = change in expected insured loss (loss-cost view). In the market, knock-out gaps matter far more "
        "than this percentage suggests: they decide whether the risk is quoted at all, and whether ransomware is sublimited. "
        "Small accounts also often sit at a carrier's minimum premium, so a lower technical price may not show up on the invoice.",
        "",
        "## What drives the estimate",
        "",
        f"![Tornado](../figures/tornado_{case_id}.png)",
        "",
        "| Input | Basis | ALE at P10 | ALE at P90 |",
        "|---|---|---|---|",
    ]
    for t in r["tornado"][:8]:
        lines.append(f"| `{t['parameter']}` - {t['description']} | {t['basis']} | {_fmt_money(t['ale_at_p10'])} | {_fmt_money(t['ale_at_p90'])} |")
    lines += ["", "## Analyst commentary", "", case.notes.strip() if case.notes else "_(see profile YAML `notes`)_", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run_report(n: int = 100_000, seed: int = 20261010, tornado_n: int = 100_000) -> dict:
    t0 = time.time()
    params = ParameterSet()
    fig_dir = REPO_ROOT / "docs" / "figures"
    res_dir = REPO_ROOT / "docs" / "results"
    res_dir.mkdir(parents=True, exist_ok=True)
    results, gap_frames = {}, {}
    for cid in CASES:
        print(f"[case] {cid}")
        results[cid], gap_frames[SHORT[cid]] = run_case(cid, n, seed, params, fig_dir, tornado_n)
        write_case_study(cid, results[cid], REPO_ROOT / "docs" / "case_studies" / f"{cid}.md")

    plots.control_value(gap_frames, fig_dir / "control_value.png")
    mix = pd.DataFrame({SCENARIO_LABELS[s]: [results[c]["packages"]["current"]["ale_by_scenario"][s] for c in CASES]
                        for s in SCENARIO_LABELS}, index=[SHORT[c] for c in CASES])
    plots.scenario_mix(mix, fig_dir / "scenario_mix.png")

    # KEV / patch cadence
    lags = kev_lags()
    sla = pd.DataFrame({"sla_days": [7, 14, 30, 60, 90, 180]})
    sla["exposure_removed"] = [patch_exposure_reduction(d) for d in sla["sla_days"]]
    plots.kev_patch(lags, sla, fig_dir / "kev_patch_cadence.png")

    # Validation
    print("[validation]")
    checks = checks_frame(severity_checks(params=params) + frequency_checks(params=params) + control_effect_checks(params=params))
    plots.validation(checks, fig_dir / "validation.png")
    dental = load_case(REPO_ROOT / "profiles" / "dental_clinic.yaml")
    hhs = hhs_dental_check(dental.profile, assess(dental.answers).posture, params=params)
    recs, _, _ = breach_records_sample(dental.profile, params)
    hhs_df = pd.read_csv(PROCESSED / "hhs_dental_breaches.csv")
    plots.hhs_records(recs[recs >= 500], hhs_df["individuals"].to_numpy(), dental.profile.records_held, fig_dir / "hhs_dental_breach_size.png")
    validation = {"checks": json.loads(checks.to_json(orient="records")), "hhs_dental": hhs}
    (res_dir / "validation.json").write_text(json.dumps(validation, indent=2, default=float), encoding="utf-8")

    calib = json.loads((PROCESSED / "calibration_summary.json").read_text(encoding="utf-8"))
    summary = {"seed": seed, "iterations_per_run": n, "cases": results,
               "patch_sla_exposure_removed": dict(zip(sla["sla_days"].astype(str), sla["exposure_removed"])),
               "kev": calib["kev"], "control_labels": CONTROL_LABELS,
               "registry_counts": pd.Series([p.basis for p in params.registry.values()]).value_counts().to_dict(),
               "runtime_seconds": round(time.time() - t0, 1)}
    (res_dir / "results.json").write_text(json.dumps(summary, indent=2, default=float), encoding="utf-8")
    print(f"done in {summary['runtime_seconds']} s")
    return summary
