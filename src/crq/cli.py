"""Command-line interface: ``crq <command>``.

  crq data download [--only kev,nvd,hhs,vcdb,cbp]   fetch public datasets into data/raw/
  crq calibrate                                    derive KEV/VCDB/HHS calibration tables
  crq run profiles/dental_clinic.yaml [--xlsx out.xlsx] [--n 100000] [--seed 20261010]
  crq assess profiles/law_firm.yaml                 readiness score + gaps only (no simulation)
  crq validate                                      model vs public benchmarks
  crq params                                        list every parameter with its basis and sources
  crq report                                        regenerate all results, figures, case studies, workbooks
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from .analysis import gap_analysis, gaps_frame, indicated_premium, load_case, packages
from .parameters import ParameterSet
from .plots import money
from .questionnaire import assess, load_questions


def _cmd_data(args) -> int:
    from .data_sources import download_all

    download_all(args.only.split(",") if args.only else None)
    return 0


def _cmd_calibrate(args) -> int:
    from .calibration import run_all

    s = run_all()
    print(json.dumps({k: s[k] for k in ("kev", "vcdb_small_victim_vectors", "cbp2022")}, indent=2, default=str))
    return 0


def _cmd_assess(args) -> int:
    case = load_case(args.profile)
    a = assess(case.answers)
    qs = load_questions()
    print(f"{case.profile.name}: readiness {a.score:.1f}/100 -> {a.tier}")
    print(f"  {a.tier_meaning}")
    for qid, q in qs.items():
        s = a.question_scores[qid]
        if s < q.options[q.best_option]["score"]:
            flag = "KNOCK-OUT" if qid in a.critical_failures else "gap"
            print(f"  [{flag:9s}] {qid:22s} {q.options[case.answers[qid]]['label']}")
    return 0


def _cmd_run(args) -> int:
    params = ParameterSet()
    case = load_case(args.profile)
    a, base, rows = gap_analysis(case, n=args.n, seed=args.seed, params=params)
    pk = packages(case, n=args.n, seed=args.seed, params=params)
    print(f"{case.profile.name}  |  readiness {a.score:.1f}/100 -> {a.tier}  |  {args.n:,} simulated years, seed {args.seed}")
    if a.critical_failures:
        print(f"  knock-out gaps: {', '.join(a.critical_failures)}")
    tbl = pd.DataFrame({
        label: {
            "ALE": money(r.ale), "P(any event)": f"{r.prob_any_event():.2%}", "P(loss > $100K)": f"{r.exceedance([1e5])[0]:.2%}",
            "TVaR99": money(r.tvar(0.99)), "Expected insured loss": money(r.insured_loss.mean()),
            "Indicated premium": money(indicated_premium(r, params)),
        } for label, (_, r) in {"current": pk["current"], "fix knock-outs": pk["fix_knockouts"], "all controls": pk["all_controls"]}.items()
    })
    print(tbl.to_string())
    g = gaps_frame(rows)
    if not g.empty:
        show = g[["question", "critical", "delta_ale", "premium_change_pct", "annual_cost"]].copy()
        show["delta_ale"] = show["delta_ale"].map(lambda v: money(v) if pd.notna(v) else "n/a (scored only)")
        show["premium_change_pct"] = show["premium_change_pct"].map(lambda v: f"{v:.1f}%" if pd.notna(v) else "")
        show["annual_cost"] = show["annual_cost"].map(money)
        print("\nGaps (one at a time, common random numbers):")
        print(show.rename(columns={"delta_ale": "ALE reduction", "premium_change_pct": "premium change", "annual_cost": "est. cost/yr"}).to_string(index=False))
    if args.xlsx:
        from .excel import write_workbook

        path = write_workbook(case, a, base, rows, pk, args.xlsx, params)
        print(f"\nwrote {path}")
    if args.json:
        print(json.dumps(base.summary(), indent=2))
    return 0


def _cmd_validate(args) -> int:
    from .validation import checks_frame, control_effect_checks, frequency_checks, severity_checks

    df = checks_frame(severity_checks() + frequency_checks() + control_effect_checks())
    pd.set_option("display.width", 200)
    print(df[["name", "benchmark", "published", "model", "model_vs_mid", "within_band", "sample"]].to_string(index=False))
    return 0


def _cmd_params(args) -> int:
    reg = ParameterSet().registry
    rows = [{"parameter": p.name, "basis": p.basis, "distribution": p.describe_range(), "sources": ",".join(p.sources)} for p in reg.values()]
    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 500)
    print(pd.DataFrame(rows).to_string(index=False))
    return 0


def _cmd_report(args) -> int:
    from .reporting import run_report

    run_report(n=args.n, seed=args.seed)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="crq", description="FAIR-style cyber risk quantification + underwriting readiness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("data", help="download public datasets")
    d.add_argument("action", choices=["download"])
    d.add_argument("--only", help="comma list of kev,nvd,hhs,vcdb,cbp")
    d.set_defaults(func=_cmd_data)
    sub.add_parser("calibrate", help="derive calibration tables from data/raw").set_defaults(func=_cmd_calibrate)
    a = sub.add_parser("assess", help="readiness score and gaps for a profile")
    a.add_argument("profile")
    a.set_defaults(func=_cmd_assess)
    r = sub.add_parser("run", help="simulate a profile: ALE, tail metrics, gap values")
    r.add_argument("profile")
    r.add_argument("--n", type=int, default=100_000)
    r.add_argument("--seed", type=int, default=20261010)
    r.add_argument("--xlsx", help="write the underwriter workbook here")
    r.add_argument("--json", action="store_true", help="also print the summary as JSON")
    r.set_defaults(func=_cmd_run)
    sub.add_parser("validate", help="compare model to public benchmarks").set_defaults(func=_cmd_validate)
    sub.add_parser("params", help="list all parameters with provenance").set_defaults(func=_cmd_params)
    rp = sub.add_parser("report", help="regenerate all results, figures, case studies and workbooks")
    rp.add_argument("--n", type=int, default=100_000)
    rp.add_argument("--seed", type=int, default=20261010)
    rp.set_defaults(func=_cmd_report)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
