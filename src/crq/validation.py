"""Check model outputs against public benchmarks the model was (mostly) not fitted to.

Every check is labelled:

* ``out-of-sample`` - the benchmark number was NOT used to set any parameter.
* ``partly in-sample`` - a related number from the same source informed a parameter,
  so agreement is weaker evidence.
* ``post-hoc`` - the check failed in v1 and prompted a structural change (size
  elasticity of severity, set from IRIS, not from this benchmark). Agreement
  after the change is still weaker evidence than a true out-of-sample test.

Benchmarks describe populations (all Coalition policyholders, NetDiligence SMEs,
Sophos survey respondents), so each check simulates a "benchmark firm" sized like
that population at the reference control posture. The firm sizes are our
assumptions and are printed with the results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .calibration import PROCESSED
from .controls import CONTROLS, Posture, patch_exposure_reduction
from .model import FirmProfile, SimulationResult, reference_posture, simulate
from .parameters import ParameterSet


@dataclass
class Check:
    name: str
    benchmark: str
    published: str
    published_low: float
    published_high: float
    model: float
    unit: str
    sample: str
    firm: str
    note: str = ""

    @property
    def within(self) -> bool:
        return self.published_low <= self.model <= self.published_high

    @property
    def ratio_to_mid(self) -> float:
        if self.published_low <= 0:  # one-sided bound: compare with the bound itself
            return self.model / self.published_high
        return self.model / np.sqrt(self.published_low * self.published_high)


def per_event_mean(res: SimulationResult, scenario: str, exclude: tuple[str, ...] = ()) -> float:
    n_events = res.event_counts[scenario].sum()
    total = sum(v for k, v in res.components[scenario].items() if k not in exclude)
    return float(total / n_events) if n_events else float("nan")


def _firm(name, employees, revenue, records, industry="general", regulated=False) -> FirmProfile:
    return FirmProfile(name=name, industry=industry, employees=employees, annual_revenue=revenue, records_held=records,
                       sensitive_records=True, regulated=regulated, operational_dependency=0.8)


def severity_checks(n: int = 400_000, seed: int = 7, params: ParameterSet | None = None) -> list[Check]:
    params = params or ParameterSet()
    ref = reference_posture(params)
    checks = []

    smb = _firm("Typical insured SMB (25 staff, $5M revenue)", 25, 5e6, 5_000)
    r = simulate(smb, ref, n=n, seed=seed, params=params)
    checks.append(Check("Ransomware cost per event", "Coalition average ransomware claim (2024 / 2025 data)",
                        "$292K / $269K", 269e3, 292e3, per_event_mean(r, "ransomware"), "USD", "out-of-sample", smb.name,
                        "Coalition reports insured loss (after retention/limits); the model reports gross loss."))
    bec_mix = (0.31 * 27e3 + 0.27 * 141e3) / 0.58
    checks.append(Check("BEC/FTF cost per event", "Coalition 2026: BEC avg $27K (31% of claims) and FTF avg $141K (27%), blended",
                        f"${bec_mix / 1e3:.0f}K blended", 0.8 * bec_mix, 1.2 * bec_mix, per_event_mean(r, "bec"), "USD",
                        "partly in-sample", smb.name, "BEC response cost was anchored to Coalition's BEC average; the FTF share comes from the same report."))
    all_events = sum(res.sum() for res in r.event_counts.values())
    checks.append(Check("Average cost per event (all scenarios)", "Coalition average claim (2024 / 2025 data)", "$115K / $116K",
                        115e3, 116e3, float(r.annual_loss.sum() / all_events), "USD", "out-of-sample", smb.name))

    sme = _firm("NetDiligence-average SME (350 staff, $108M revenue)", 350, 108e6, 20_000)
    r2 = simulate(sme, ref, n=n, seed=seed, params=params)
    checks.append(Check("Ransomware cost per event", "NetDiligence 2025: SME ransomware average incident cost (2020-2024)",
                        "~$630K-$660K", 630e3, 660e3, per_event_mean(r2, "ransomware"), "USD", "post-hoc", sme.name,
                        "SME = revenue < $2B; average SME revenue in the study is $108M."))
    checks.append(Check("BEC/FTF cost per event", "NetDiligence 2025: SME BEC average incident cost (2024)", "$75K", 60e3, 90e3,
                        per_event_mean(r2, "bec"), "USD", "out-of-sample", sme.name, "Band is +/-20% around the published figure."))

    mid = _firm("Sophos 100-250 employee respondent (175 staff, $35M revenue)", 175, 35e6, 10_000)
    r3 = simulate(mid, ref, n=n, seed=seed, params=params)
    checks.append(Check("Ransomware recovery cost per event (excl. ransom)", "Sophos 2025: mean recovery cost, 100-250 employees",
                        "$638,536", 0.8 * 638_536, 1.2 * 638_536, per_event_mean(r3, "ransomware", exclude=("ransom",)), "USD",
                        "post-hoc", mid.name, "Survey of organisations hit by ransomware (not insured claims). Band +/-20%."))
    pay = params.central("rw.p_pay_bad_backups") * (1 - ref.level("backups")) + params.central("rw.p_pay_good_backups") * ref.level("backups")
    checks.append(Check("Share of ransomware victims that pay", "Coveware Q2 2024-Q3 2025 payment rates; DBIR 2025 (64% did not pay)",
                        "23%-36%", 0.23, 0.36, pay, "share", "out-of-sample", "Reference posture",
                        "Coalition reports 44% (2024 data) and 14% (2025 data), so published rates span 14%-44%."))
    return checks


def frequency_checks(params: ParameterSet | None = None, n: int = 400_000, seed: int = 7) -> list[Check]:
    params = params or ParameterSet()
    ref = reference_posture(params)
    firm = _firm("IRIS $10M-$100M tier firm (100 staff, $32M revenue)", 100, 31.6e6, 5_000)
    r = simulate(firm, ref, n=n, seed=seed, params=params)
    return [Check("P(at least one loss event in a year)", "Cyentia IRIS 2022, $10M-$100M revenue: upper-bound model",
                  "< 11.53% (upper bound)", 0.0, 0.1153, r.prob_any_event(), "probability", "out-of-sample", firm.name,
                  "IRIS counts publicly reported events of all kinds; it only bounds the model from above.")]


def breach_records_sample(profile: FirmProfile, params: ParameterSet, m: int = 200_000, seed: int = 7):
    """Records exposed per breach event, using the same mixture as ``model._breach_cost``.

    Returns (records for standalone breaches, P(>=500 | standalone breach), P(>=500 | ransomware exfiltration)).
    """
    rng = np.random.default_rng(seed)
    p_broad = params.registry["db.p_broad_exposure"].dist.sample(rng, m)
    broad = rng.random(m) < p_broad
    frac = np.where(broad, rng.beta(4.0, 2.0, m), rng.beta(1.0, 30.0, m))
    records = np.floor(frac * profile.records_held)
    broad_rw = rng.random(m) < params.registry["rw.exfil_broad_share"].dist.sample(rng, m)
    frac_rw = np.where(broad_rw, rng.beta(4.0, 2.0, m), rng.beta(1.0, 30.0, m))
    p500_rw = float((np.floor(frac_rw * profile.records_held) >= 500).mean())
    return records, float((records >= 500).mean()), p500_rw


def hhs_dental_check(case_profile: FirmProfile, posture: Posture, n: int = 1_000_000, seed: int = 7,
                     params: ParameterSet | None = None) -> dict:
    """Compare the dental case to HHS OCR breach reports (>= 500 individuals) for dental practices.

    Frequency: HHS dental breaches per year / dental establishments (Census CBP 2022)
    gives an *observed reportable rate*, a lower bound on true incidence. The model's
    rate of events that would be reportable to HHS (ransomware or breach exposing
    >= 500 patients) for the case firm is compared against it.
    Size: model records exposed per reportable event vs HHS individuals affected.
    """
    params = params or ParameterSet()
    summ = json.loads((PROCESSED / "calibration_summary.json").read_text(encoding="utf-8"))
    dental = pd.read_csv(PROCESSED / "hhs_dental_breaches.csv")
    estab = summ["cbp2022"]["dentists"]["establishments"]
    recent = dental[dental["year"].between(2020, 2025)]
    per_year = recent.groupby("year").size()
    observed_rate = float(per_year.mean() / estab)

    records, p500_breach, p500_rw = breach_records_sample(case_profile, params, seed=seed)

    def reportable_rate(profile: FirmProfile, post: Posture) -> float:
        res = simulate(profile, post, n=n, seed=seed, params=params)
        # Under HIPAA, ransomware on systems holding PHI is presumed a reportable breach.
        return float(res.lam["data_breach"].mean()) * p500_breach + float(res.lam["ransomware"].mean()) * p500_rw

    model_rate = reportable_rate(case_profile, posture)
    emp = summ["cbp2022"]["dentists"]["employees"] / estab
    avg_office = FirmProfile(name="Average dental establishment", industry="healthcare", employees=max(1, round(emp)),
                             annual_revenue=1.5e6, records_held=case_profile.records_held, regulated=True)
    avg_rate = reportable_rate(avg_office, reference_posture(params))
    sim_records = records[records >= 500]
    return {
        "observed_reportable_rate": observed_rate,
        "observed_breaches_per_year_2020_2025": per_year.to_dict(),
        "dental_establishments_cbp2022": estab,
        "model_reportable_rate_case": model_rate,
        "ratio_case_to_observed": model_rate / observed_rate,
        "avg_dental_employees_cbp2022": emp,
        "model_reportable_rate_avg_office_ref_posture": avg_rate,
        "ratio_avg_office_to_observed": avg_rate / observed_rate,
        "model_records_quantiles_ge500": {str(q): float(np.quantile(sim_records, q)) for q in (0.25, 0.5, 0.75, 0.9)},
        "hhs_records_quantiles": {str(q): float(dental["individuals"].quantile(q)) for q in (0.25, 0.5, 0.75, 0.9)},
        "hhs_dental_n": int(len(dental)),
        "hhs_records_quantiles_le_case_size": {str(q): float(dental.loc[dental.individuals <= case_profile.records_held, "individuals"].quantile(q)) for q in (0.25, 0.5, 0.75, 0.9)},
    }


def control_effect_checks(params: ParameterSet | None = None, n: int = 400_000, seed: int = 7) -> list[Check]:
    """Model-implied incident-frequency ratios vs Marsh's observational claims analysis."""
    params = params or ParameterSet()
    ref = reference_posture(params)
    firm = _firm("Reference SMB (50 staff, $10M revenue)", 50, 10e6, 5_000)

    def total_freq(posture: Posture) -> float:
        r = simulate(firm, posture, n=n, seed=seed, params=params)
        return float(sum(v.mean() for v in r.lam.values()))

    no_mfa = Posture({**ref.as_dict(), "mfa_email": 0.0, "mfa_remote": 0.0, "mfa_privileged": 0.0})
    full_mfa = Posture({**ref.as_dict(), "mfa_email": 1.0, "mfa_remote": 1.0, "mfa_privileged": 1.0})
    slow = Posture({**ref.as_dict(), "patching": patch_exposure_reduction(90)})
    fast = Posture({**ref.as_dict(), "patching": patch_exposure_reduction(7)})
    return [
        Check("Incident frequency: no MFA vs broad MFA", "Marsh 2023: broad MFA -> 1.4x less likely to have a successful attack",
              "1.4x", 1.4 * 0.8, 1.4 * 1.2, total_freq(no_mfa) / total_freq(full_mfa), "ratio", "out-of-sample", firm.name,
              "Marsh compares firms with and without broad MFA among its clients (observational)."),
        Check("Incident frequency: 90-day vs 7-day patching", "Marsh 2023: patching high-severity vulns within 7 days -> ~2x lower incident probability",
              "~2x", 2.0 * 0.8, 2.0 * 1.2, total_freq(slow) / total_freq(fast), "ratio", "out-of-sample", firm.name,
              "Marsh's comparison is observational: fast patchers also tend to do everything else well (confounding)."),
    ]


def checks_frame(checks: list[Check]) -> pd.DataFrame:
    return pd.DataFrame([{**c.__dict__, "within_band": c.within, "model_vs_mid": c.ratio_to_mid} for c in checks])


__all__ = ["CONTROLS", "Check", "checks_frame", "control_effect_checks", "frequency_checks", "hhs_dental_check", "severity_checks"]
