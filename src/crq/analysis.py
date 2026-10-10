"""Decision analysis on top of the simulator: control gaps, packages, sensitivity."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .model import FirmProfile, Policy, SimulationResult, simulate
from .parameters import ParameterSet
from .questionnaire import Assessment, assess, best_answers, load_questions


@dataclass
class Case:
    """A firm profile plus its questionnaire answers (one case study)."""

    id: str
    profile: FirmProfile
    answers: dict[str, str]
    notes: str = ""
    extra: dict = field(default_factory=dict)


def load_case(path: str | Path) -> Case:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    f = raw["firm"]
    policy = Policy(**raw.get("policy", {}))
    profile = FirmProfile(
        name=f["name"], industry=f["industry"], employees=int(f["employees"]), annual_revenue=float(f["annual_revenue"]),
        records_held=int(f["records_held"]), sensitive_records=bool(f.get("sensitive_records", True)),
        regulated=bool(f.get("regulated", False)), endpoints=f.get("endpoints"),
        operational_dependency=float(f.get("operational_dependency", 0.8)), policy=policy, description=f.get("description", ""))
    return Case(id=raw.get("id", Path(path).stem), profile=profile, answers=raw["answers"], notes=raw.get("notes", ""))


def indicated_premium(res: SimulationResult, params: ParameterSet) -> float:
    """Loss-cost view: premium = expected insured loss / market loss ratio (Aon: 42%)."""
    return float(res.insured_loss.mean() / params.central("pricing.loss_ratio"))


@dataclass
class GapRow:
    question: str
    section: str
    current: str
    recommended: str
    critical: bool
    modelled: bool
    delta_ale: float
    delta_tvar99: float
    delta_p_over_100k: float
    delta_insured: float
    premium_change_pct: float
    annual_cost: float
    evidence_strength: str
    note: str
    evidence: tuple[str, ...]


def gap_analysis(case: Case, n: int = 100_000, seed: int = 20261010, params: ParameterSet | None = None) -> tuple[Assessment, SimulationResult, list[GapRow]]:
    """Value of fixing each gap one at a time (common random numbers), ranked by ALE reduction."""
    params = params or ParameterSet()
    qs = load_questions()
    a = assess(case.answers, qs)
    base = simulate(case.profile, a.posture, n=n, seed=seed, params=params)
    base_ins = float(base.insured_loss.mean())
    rows = []
    for qid, q in qs.items():
        cur = case.answers[qid]
        best = q.best_option
        if q.options[cur]["score"] >= q.options[best]["score"]:
            continue
        improved = dict(case.answers)
        improved[qid] = best
        if q.modelled:
            r = simulate(case.profile, assess(improved, qs).posture, n=n, seed=seed, params=params)
            d_ale = base.ale - r.ale
            d_tvar = base.tvar(0.99) - r.tvar(0.99)
            d_p100 = float(base.exceedance([1e5])[0] - r.exceedance([1e5])[0])
            d_ins = base_ins - float(r.insured_loss.mean())
        else:
            d_ale = d_tvar = d_p100 = d_ins = float("nan")
        rows.append(GapRow(
            question=qid, section=q.section, current=q.options[cur]["label"], recommended=q.options[best]["label"],
            critical=q.critical and a.question_scores[qid] < q.critical_min, modelled=q.modelled,
            delta_ale=d_ale, delta_tvar99=d_tvar, delta_p_over_100k=d_p100, delta_insured=d_ins,
            premium_change_pct=(-100.0 * d_ins / base_ins) if (q.modelled and base_ins > 0) else float("nan"),
            annual_cost=q.annual_cost(case.profile.employees, case.profile.n_endpoints),
            evidence_strength=q.note.get("strength", ""), note=q.note.get("text", ""), evidence=q.evidence))
    rows.sort(key=lambda r: (not r.critical, -(r.delta_ale if np.isfinite(r.delta_ale) else -1)))
    return a, base, rows


def gaps_frame(rows: list[GapRow]) -> pd.DataFrame:
    df = pd.DataFrame([r.__dict__ for r in rows])
    if not df.empty:
        df["ale_reduction_per_dollar"] = df["delta_ale"] / df["annual_cost"].where(df["annual_cost"] > 0)
    return df


def packages(case: Case, n: int = 100_000, seed: int = 20261010, params: ParameterSet | None = None) -> dict[str, tuple[Assessment, SimulationResult]]:
    """Current posture vs 'fix knock-outs only' vs 'fix everything' (all best answers)."""
    params = params or ParameterSet()
    qs = load_questions()
    cur = assess(case.answers, qs)
    knock = dict(case.answers)
    for qid in cur.critical_failures:
        knock[qid] = qs[qid].best_option
    out = {}
    for name, ans in (("current", case.answers), ("fix_knockouts", knock), ("all_controls", best_answers(qs))):
        a = assess(ans, qs)
        out[name] = (a, simulate(case.profile, a.posture, n=n, seed=seed, params=params))
    return out


def tornado(case: Case, n: int = 40_000, seed: int = 20261010, top: int = 12, q_low: float = 0.10, q_high: float = 0.90,
            params: ParameterSet | None = None) -> pd.DataFrame:
    """One-at-a-time sensitivity of ALE: pin each uncertain parameter at its P10 and P90."""
    params = params or ParameterSet()
    reg = params.registry
    posture = assess(case.answers).posture
    base = simulate(case.profile, posture, n=n, seed=seed, params=params).ale
    rows = []
    for name, prm in reg.items():
        lo, hi = prm.dist.ppf(q_low), prm.dist.ppf(q_high)
        if np.isclose(lo, hi):
            continue
        ale_lo = simulate(case.profile, posture, n=n, seed=seed, params=ParameterSet(reg, {name: lo})).ale
        ale_hi = simulate(case.profile, posture, n=n, seed=seed, params=ParameterSet(reg, {name: hi})).ale
        rows.append({"parameter": name, "description": prm.description, "basis": prm.basis, "p10_value": lo, "p90_value": hi,
                     "ale_at_p10": ale_lo, "ale_at_p90": ale_hi, "swing": abs(ale_hi - ale_lo)})
    df = pd.DataFrame(rows).sort_values("swing", ascending=False).head(top).reset_index(drop=True)
    df.attrs["base_ale"] = base
    return df


def ale_uncertainty(case: Case, n: int = 400_000, block: int = 1_000, seed: int = 20261010, params: ParameterSet | None = None) -> dict:
    """Two-dimensional Monte Carlo: 400 parameter draws x 1,000 years each -> interval on ALE."""
    posture = assess(case.answers).posture
    res = simulate(case.profile, posture, n=n, seed=seed, params=params, block=block)
    lo, hi = res.ale_interval((0.05, 0.95))
    return {"ale": res.ale, "ale_p05": lo, "ale_p95": hi, "n_parameter_draws": n // block, "years_per_draw": block}
