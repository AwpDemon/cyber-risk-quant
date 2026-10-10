"""Streamlit front end: answer the underwriting questionnaire, see the quantified risk.

Run:  streamlit run app/streamlit_app.py
Read-only: nothing the user enters is stored or logged.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from crq.analysis import Case, gap_analysis, gaps_frame, indicated_premium, load_case, packages
from crq.excel import write_workbook
from crq.model import SCENARIO_LABELS, FirmProfile, Policy
from crq.parameters import ParameterSet
from crq.plots import money
from crq.questionnaire import assess, load_questions

ROOT = Path(__file__).resolve().parents[1]
st.set_page_config(page_title="Cyber Risk Quant", page_icon=None, layout="wide")

st.title("Cyber risk quantification + underwriting readiness")
st.caption("FAIR-style Monte Carlo on public data. Illustrative, not a rate or a quote. Presets are hypothetical firms.")

PRESETS = {p.stem: p for p in sorted((ROOT / "profiles").glob("*.yaml"))}
qs = load_questions()

with st.sidebar:
    st.header("Firm")
    preset = st.selectbox("Start from a hypothetical preset", list(PRESETS), format_func=lambda s: s.replace("_", " ").title())
    base_case = load_case(PRESETS[preset])
    bp = base_case.profile
    industry = st.selectbox("Industry", ["healthcare", "legal", "manufacturing", "general"],
                            index=["healthcare", "legal", "manufacturing", "general"].index(bp.industry))
    employees = st.number_input("Employees", 1, 5000, bp.employees)
    endpoints = st.number_input("Endpoints (PCs + servers)", 1, 10000, bp.n_endpoints)
    revenue = st.number_input("Annual revenue ($)", 100_000, 2_000_000_000, int(bp.annual_revenue), step=100_000)
    records = st.number_input("Personal records held", 0, 10_000_000, bp.records_held, step=500)
    regulated = st.checkbox("Regulated data (HIPAA / GLBA / state)", bp.regulated)
    dependency = st.slider("Share of revenue that stops when IT is down", 0.0, 1.0, bp.operational_dependency, 0.05)
    st.header("Policy")
    limit = st.number_input("Aggregate limit ($)", 100_000, 50_000_000, int(bp.policy.aggregate_limit), step=250_000)
    retention = st.number_input("Retention ($)", 0, 1_000_000, int(bp.policy.retention), step=5_000)
    se_sub = st.number_input("Social-engineering sublimit ($)", 0, 5_000_000, int(bp.policy.social_engineering_sublimit), step=25_000)
    st.header("Simulation")
    n = st.select_slider("Simulated years", [20_000, 50_000, 100_000, 200_000], 50_000)
    seed = st.number_input("Seed", 0, 2**31 - 1, 20261010)

st.subheader("Underwriting questionnaire")
answers = {}
cols = st.columns(2)
for i, (qid, q) in enumerate(qs.items()):
    with cols[i % 2]:
        opts = list(q.options)
        label = f"{'(knock-out) ' if q.critical else ''}{q.text}"
        answers[qid] = st.selectbox(label, opts, index=opts.index(base_case.answers[qid]),
                                    format_func=lambda k, q=q: q.options[k]["label"], key=f"{preset}-{qid}")

profile = FirmProfile(name=f"{preset.replace('_', ' ').title()} (hypothetical)", industry=industry, employees=int(employees),
                      annual_revenue=float(revenue), records_held=int(records), regulated=regulated, endpoints=int(endpoints),
                      operational_dependency=float(dependency),
                      policy=Policy(aggregate_limit=float(limit), retention=float(retention), social_engineering_sublimit=float(se_sub)))
case = Case(id=preset, profile=profile, answers=answers)
a = assess(answers)


@st.cache_data(show_spinner="Simulating...")
def run(profile_key: str, answers_key: tuple, n: int, seed: int):
    # profile_key (repr of the frozen profile) is only the cache key; the profile itself is the module global.
    c = Case(id="ui", profile=profile, answers=dict(answers_key))
    params = ParameterSet()
    ass, base, rows = gap_analysis(c, n=n, seed=seed, params=params)
    pk = packages(c, n=n, seed=seed, params=params)
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as td:
        p = write_workbook(c, ass, base, rows, pk, Path(td) / "wb.xlsx", params)
        buf.write(p.read_bytes())
    lec_x = np.logspace(3, 7.3, 120)
    lec = pd.concat([pd.DataFrame({"loss": lec_x, "p_exceed": r.exceedance(lec_x), "posture": name})
                     for name, (_, r) in pk.items()])
    metrics = {k: {"ale": r.ale, "p_any": r.prob_any_event(), "p100k": float(r.exceedance([1e5])[0]), "tvar99": r.tvar(0.99),
                   "ins": float(r.insured_loss.mean()), "prem": indicated_premium(r, params),
                   "by_scn": r.ale_by_scenario(), "score": v.score, "tier": v.tier} for k, (v, r) in pk.items()}
    return metrics, gaps_frame(rows), lec, buf.getvalue()


metrics, gaps, lec, xlsx = run(repr(profile), tuple(sorted(answers.items())), n, int(seed))
cur = metrics["current"]

st.divider()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Readiness score", f"{a.score:.0f}/100", a.tier, delta_color="off")
c2.metric("Annualized loss expectancy", money(cur["ale"]))
c3.metric("P(any loss event / yr)", f"{cur['p_any']:.2%}")
c4.metric("P(annual loss > $100K)", f"{cur['p100k']:.2%}")
c5.metric("Indicated premium (loss-cost)", money(cur["prem"]))
if a.critical_failures:
    st.error(f"Knock-out gaps: {', '.join(a.critical_failures)}. {a.tier_meaning}")
else:
    st.success(a.tier_meaning)

left, right = st.columns([3, 2])
with left:
    st.markdown("**Loss exceedance curve** - probability the year's total loss exceeds each amount")
    names = {"current": "Current", "fix_knockouts": "Fix knock-outs", "all_controls": "All controls"}
    lec["posture"] = lec["posture"].map(names)
    chart = alt.Chart(lec).mark_line(strokeWidth=2).encode(
        x=alt.X("loss:Q", scale=alt.Scale(type="log"), title="Annual loss ($)", axis=alt.Axis(format="$,.0s")),
        y=alt.Y("p_exceed:Q", title="P(loss > x)", axis=alt.Axis(format=".1%")),
        color=alt.Color("posture:N", scale=alt.Scale(domain=list(names.values()), range=["#2a78d6", "#eb6834", "#1baf7a"]), title=None),
        tooltip=[alt.Tooltip("posture:N"), alt.Tooltip("loss:Q", format="$,.0f"), alt.Tooltip("p_exceed:Q", format=".3%")],
    ).properties(height=320)
    st.altair_chart(chart, use_container_width=True)
with right:
    st.markdown("**Posture comparison**")
    comp = pd.DataFrame({names[k]: {"Score": f"{m['score']:.0f} ({m['tier']})", "ALE": money(m["ale"]), "P(>$100K)": f"{m['p100k']:.2%}",
                                    "TVaR99": money(m["tvar99"]), "Expected insured loss": money(m["ins"]), "Indicated premium": money(m["prem"])}
                         for k, m in metrics.items()})
    st.dataframe(comp, use_container_width=True)
    st.markdown("**ALE by scenario (current)**")
    st.dataframe(pd.DataFrame({"ALE": {SCENARIO_LABELS[s]: money(v) for s, v in cur["by_scn"].items()}}), use_container_width=True)

st.subheader("Gap list")
st.caption("Each gap fixed on its own with common random numbers. Values overlap, so they do not sum to the package result.")
if gaps.empty:
    st.write("No gaps: every answer is at best practice.")
else:
    show = gaps[["question", "critical", "recommended", "delta_ale", "delta_tvar99", "premium_change_pct", "annual_cost", "evidence_strength", "note"]].copy()
    for col in ("delta_ale", "delta_tvar99", "annual_cost"):
        show[col] = show[col].map(lambda v: money(v) if pd.notna(v) else "scored only")
    show["premium_change_pct"] = show["premium_change_pct"].map(lambda v: f"{v:.1f}%" if pd.notna(v) else "qualitative")
    st.dataframe(show.rename(columns={"delta_ale": "ALE reduction", "delta_tvar99": "TVaR99 reduction", "premium_change_pct": "premium change",
                                      "annual_cost": "est. cost / yr", "evidence_strength": "evidence"}), use_container_width=True, hide_index=True)

st.download_button("Download underwriter workbook (.xlsx)", xlsx, file_name=f"{preset}_underwriting.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
with st.expander("Method and limits"):
    st.markdown((ROOT / "docs" / "METHODOLOGY.md").read_text(encoding="utf-8") if (ROOT / "docs" / "METHODOLOGY.md").exists() else "See README.")
