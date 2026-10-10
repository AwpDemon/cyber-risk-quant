"""FAIR-style Monte Carlo engine: Loss Event Frequency x Loss Magnitude.

One iteration = one simulated policy year for one firm. For each scenario:

1. Draw the uncertain parameters for that year (``ParameterSet.sample``).
2. Frequency: lambda = LEF_ref * size * industry * V(firm)/V(ref);
   number of loss events N ~ Poisson(lambda).
3. Magnitude: for each event, add up primary loss (response, restoration,
   business interruption, ransom, stolen funds) and secondary loss
   (notification, credit monitoring, regulatory, litigation).
4. Annual loss = sum over events and scenarios. Insured loss applies the
   policy retention, sublimits and aggregate limit to each event.

Common random numbers (CRN): two runs with the same seed but different
control postures see the same parameter draws, the same frequency uniforms
(Poisson by inverse CDF, so a lower rate can only *remove* events) and the same
severity draws for the j-th event of each year. Differences between runs are
therefore caused by the control change, not by sampling noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .controls import Posture, patch_exposure_reduction, vulnerability
from .parameters import ParameterSet

SCENARIOS: tuple[str, ...] = ("ransomware", "bec", "data_breach")
SCENARIO_LABELS = {
    "ransomware": "Ransomware / extortion",
    "bec": "BEC / funds transfer fraud",
    "data_breach": "Data breach (non-ransomware)",
}

MAX_EVENTS_PER_YEAR = 25  # cap on events per scenario-year (never binds at SMB rates)


@dataclass(frozen=True)
class Policy:
    """A simplified SMB cyber policy (first + third party, single aggregate)."""

    aggregate_limit: float = 1_000_000
    retention: float = 10_000
    social_engineering_sublimit: float = 100_000  # BEC/FTF is almost always sublimited
    ransomware_sublimit: float | None = None  # some carriers sublimit ransomware for weak controls

    def per_event_cap(self, scenario: str) -> float:
        if scenario == "bec":
            return self.social_engineering_sublimit
        if scenario == "ransomware" and self.ransomware_sublimit is not None:
            return self.ransomware_sublimit
        return self.aggregate_limit


@dataclass(frozen=True)
class FirmProfile:
    name: str
    industry: str  # healthcare | legal | manufacturing | general
    employees: int
    annual_revenue: float
    records_held: int  # people whose personal data the firm holds (patients, clients, employees)
    sensitive_records: bool = True  # SSNs / PHI / financial data -> credit monitoring offered
    regulated: bool = False  # HIPAA / GLBA / state regulator exposure
    endpoints: int | None = None
    operational_dependency: float = 0.8  # share of revenue that stops while systems are down
    policy: Policy = field(default_factory=Policy)
    description: str = ""

    @property
    def n_endpoints(self) -> int:
        return int(self.endpoints if self.endpoints is not None else round(self.employees * 1.3))

    @property
    def daily_revenue(self) -> float:
        return self.annual_revenue / 365.0


@dataclass
class SimulationResult:
    profile: FirmProfile
    posture: Posture
    n: int
    seed: int
    annual_loss: np.ndarray
    insured_loss: np.ndarray
    by_scenario: dict[str, np.ndarray]
    event_counts: dict[str, np.ndarray]
    components: dict[str, dict[str, float]]  # scenario -> component -> sum over all simulated years
    lam: dict[str, np.ndarray]  # annual event rate per simulated year
    block: int = 1

    @property
    def ale(self) -> float:
        """Annualized loss expectancy = mean simulated annual loss."""
        return float(self.annual_loss.mean())

    @property
    def ale_se(self) -> float:
        """Monte Carlo standard error of the ALE estimate (sampling noise only, not model uncertainty)."""
        return float(self.annual_loss.std(ddof=1) / np.sqrt(self.n))

    def quantile(self, q: float, insured: bool = False) -> float:
        return float(np.quantile(self.insured_loss if insured else self.annual_loss, q))

    def tvar(self, q: float, insured: bool = False) -> float:
        """Tail value-at-risk: mean loss in the worst (1-q) share of years."""
        x = np.sort(self.insured_loss if insured else self.annual_loss)
        k = int(np.floor(q * x.size))
        return float(x[k:].mean())

    def prob_any_event(self) -> float:
        return float((sum(self.event_counts.values()) > 0).mean())

    def exceedance(self, thresholds, insured: bool = False) -> np.ndarray:
        """P(annual loss > x) for each threshold x: the loss exceedance curve."""
        x = np.sort(self.insured_loss if insured else self.annual_loss)
        return 1.0 - np.searchsorted(x, np.asarray(thresholds), side="right") / x.size

    def ale_by_scenario(self) -> dict[str, float]:
        return {s: float(v.mean()) for s, v in self.by_scenario.items()}

    def component_means(self) -> dict[str, dict[str, float]]:
        return {s: {k: v / self.n for k, v in comp.items()} for s, comp in self.components.items()}

    def ale_interval(self, q=(0.05, 0.95)) -> tuple[float, float] | None:
        """Interval for ALE over parameter uncertainty (needs block > 1)."""
        if self.block <= 1:
            return None
        m = (self.n // self.block) * self.block
        per_block = self.annual_loss[:m].reshape(-1, self.block).mean(axis=1)
        lo, hi = np.quantile(per_block, q)
        return float(lo), float(hi)

    def summary(self) -> dict[str, float]:
        out = {
            "ale": self.ale,
            "ale_mc_se": self.ale_se,
            "p_any_loss_event": self.prob_any_event(),
            "median": self.quantile(0.5),
            "p90": self.quantile(0.90),
            "p95": self.quantile(0.95),
            "p99": self.quantile(0.99),
            "tvar95": self.tvar(0.95),
            "expected_insured_loss": float(self.insured_loss.mean()),
            "insured_sd": float(self.insured_loss.std()),
        }
        for s, v in self.ale_by_scenario().items():
            out[f"ale_{s}"] = v
            out[f"freq_{s}"] = float(self.lam[s].mean())
        return out


def reference_posture(p: ParameterSet) -> Posture:
    """The average insured-SMB control posture that the frequency anchors describe."""
    levels = {c: p.central(f"ref.{c}") for c in (
        "mfa_email", "mfa_remote", "mfa_privileged", "email_security", "training",
        "edr", "backups", "ir_plan", "payment_verification")}
    levels["patching"] = patch_exposure_reduction(p.central("ref.patch_sla_days"))
    return Posture(levels)


def poisson_inverse_cdf(u: np.ndarray, lam: np.ndarray, kmax: int = MAX_EVENTS_PER_YEAR) -> np.ndarray:
    """Smallest k with F(k; lam) >= u, vectorised, by walking the Poisson CDF upwards.

    Same result as ``scipy.stats.poisson.ppf`` but ~50x faster for the small
    rates seen here (SMB event rates are well below one per year).
    """
    lam = np.broadcast_to(np.asarray(lam, dtype=float), u.shape)
    pmf = np.exp(-lam)
    cdf = pmf.copy()
    k = np.zeros(u.shape, dtype=int)
    for j in range(1, kmax + 1):
        below = u > cdf
        if not below.any():
            break
        k += below
        pmf = pmf * lam / j
        cdf = cdf + pmf
    return k


class _Draws:
    """Random draws for one event slot (the j-th event of every simulated year).

    Every call draws a full length-``n`` vector (one value per year) in a fixed
    order, then keeps only the years that actually have a j-th event. So the
    j-th event of year i receives the same random numbers whatever control
    posture is simulated (common random numbers).
    """

    def __init__(self, rng: np.random.Generator, n: int, idx: np.ndarray):
        self.rng, self.n, self.idx = rng, n, idx

    def normal(self) -> np.ndarray:
        return self.rng.standard_normal(self.n)[self.idx]

    def uniform(self) -> np.ndarray:
        return self.rng.random(self.n)[self.idx]

    def beta(self, a: float, b: float) -> np.ndarray:
        return self.rng.beta(a, b, self.n)[self.idx]

    def lognormal(self, median, sigma) -> np.ndarray:
        return np.exp(np.log(median) + sigma * self.normal())


def _breach_cost(d: _Draws, prof: FirmProfile, p, broad_prob, posture: Posture) -> dict[str, np.ndarray]:
    """Cost of a confidentiality breach (standalone, or after ransomware exfiltration)."""
    idx = d.idx
    broad = d.uniform() < broad_prob
    frac_broad, frac_narrow = d.beta(4.0, 2.0), d.beta(1.0, 30.0)
    records = np.floor(np.where(broad, frac_broad, frac_narrow) * prof.records_held)
    ir = 1 - p["eff.ir_cost"][idx] * posture.level("ir_plan")
    response = d.lognormal(p["db.response_median"][idx], p["db.response_sigma"][idx]) * ir
    notify = records * p["db.cost_per_record"][idx]
    monitoring = records * p["db.monitoring_uptake"][idx] * p["db.monitoring_cost"][idx]
    if not prof.sensitive_records:
        monitoring = np.zeros_like(monitoring)
    reg_hit = d.uniform() < p["db.p_regulatory"][idx]
    reg_amt = d.lognormal(p["db.regulatory_median"][idx], 1.0)
    reg = np.where(reg_hit & prof.regulated, reg_amt, 0.0)
    # Litigation becomes more likely as the notified population grows.
    p_lit = p["db.p_litigation_10k"][idx] * np.clip(records / 10_000.0, 0, 1) ** 0.5
    lit_hit = d.uniform() < p_lit
    lit_amt = d.lognormal(p["db.litigation_median"][idx], 1.0)
    lit = np.where(lit_hit, lit_amt, 0.0)
    return {"breach_response": response, "notification": notify, "credit_monitoring": monitoring,
            "regulatory": reg, "litigation": lit, "_records": records}


def _event_losses(scn: str, d: _Draws, profile: FirmProfile, posture: Posture, p) -> dict[str, np.ndarray]:
    idx = d.idx
    c: dict[str, np.ndarray] = {}
    if scn == "ransomware":
        contain = 1 - p["eff.edr_contain"][idx] * posture.level("edr")
        b = posture.level("backups")
        # Recovery time: lognormal fitted to Sophos recovery-time data for orgs whose
        # backups worked; compromised/missing backups multiply it (Sophos 2024 split).
        mult = np.exp(np.log(p["rw.downtime_mult_bad_backups"][idx]) * (1 - b))
        ir_t = 1 - p["eff.ir_downtime"][idx] * posture.level("ir_plan")
        days = d.lognormal(p["rw.recovery_days_median_good"][idx], p["rw.recovery_days_sigma"][idx]) * mult * contain * ir_t
        days = np.minimum(days, 180.0)
        outage_equiv = days * p["rw.outage_equivalence"][idx]
        # Loss does not grow 1:1 with firm size (redundancy, partial operations, in-house
        # IT). Relative to the reference insured SMB, size-driven components scale with
        # elasticity gamma (IRIS 2025 loss-by-revenue tiers): linear amount * (size/ref)^(gamma-1).
        gamma = p["sev.size_elasticity"][idx]
        bi_scale = (profile.annual_revenue / p["sev.ref_revenue"][idx]) ** (gamma - 1)
        ep_scale = (profile.n_endpoints / (1.3 * p["freq.ref_employees"][idx])) ** (gamma - 1)
        c["business_interruption"] = (outage_equiv * profile.daily_revenue * profile.operational_dependency
                                      * (1 - p["rw.deferred_revenue_recovered"][idx]) * bi_scale)
        ir = 1 - p["eff.ir_cost"][idx] * posture.level("ir_plan")
        c["incident_response"] = d.lognormal(p["rw.response_median"][idx], p["rw.response_sigma"][idx]) * ir
        c["restoration"] = (profile.n_endpoints * d.lognormal(p["rw.restore_per_endpoint_median"][idx], 0.7)
                            * contain * (1 + (mult - 1) * 0.5) * ep_scale)
        p_pay = p["rw.p_pay_bad_backups"][idx] * (1 - b) + p["rw.p_pay_good_backups"][idx] * b
        pays = d.uniform() < p_pay
        ransom_median = (p["rw.ransom_median_ref"][idx]
                         * (profile.annual_revenue / p["rw.ransom_ref_revenue"][idx]) ** p["rw.ransom_revenue_elasticity"][idx])
        ransom = d.lognormal(ransom_median, p["rw.ransom_sigma"][idx])
        c["ransom"] = np.where(pays, ransom, 0.0)
        exfil = d.uniform() < p["rw.p_exfiltration"][idx]
        bc = _breach_cost(d, profile, p, p["rw.exfil_broad_share"][idx], posture)
        for key in ("notification", "credit_monitoring", "regulatory", "litigation"):
            c[key] = np.where(exfil, bc[key], 0.0)
    elif scn == "bec":
        amt = d.lognormal(p["bec.transfer_median"][idx], p["bec.transfer_sigma"][idx])
        amt = np.minimum(amt, p["bec.max_share_of_revenue"][idx] * profile.annual_revenue)
        transfer = d.uniform() < p["bec.p_transfer"][idx]  # many BEC events end before money moves
        reported_fast = d.uniform() < p["bec.p_reported_fast"][idx]
        recovered = np.where(reported_fast, p["bec.recovery_rate_if_reported"][idx], 0.0)
        c["funds_lost"] = np.where(transfer, amt * (1 - recovered), 0.0)
        c["incident_response"] = d.lognormal(p["bec.response_median"][idx], 0.8)
    else:  # data_breach
        bc = _breach_cost(d, profile, p, p["db.p_broad_exposure"][idx], posture)
        c = {k: v for k, v in bc.items() if not k.startswith("_")}
    return c


def simulate(
    profile: FirmProfile,
    posture: Posture,
    n: int = 100_000,
    seed: int = 20261010,
    params: ParameterSet | None = None,
    block: int = 1,
) -> SimulationResult:
    """Simulate ``n`` policy years for one firm and one control posture.

    ``block`` > 1 shares one parameter draw across ``block`` consecutive years
    (two-dimensional Monte Carlo) so ALE can be reported with an interval that
    reflects parameter (epistemic) uncertainty separately from year-to-year
    (aleatory) randomness.
    """
    params = params or ParameterSet()
    param_ss, freq_ss, sev_ss = np.random.SeedSequence(seed).spawn(3)
    n_draws = -(-n // block)
    p = {k: np.repeat(v, block)[:n] for k, v in params.sample(np.random.default_rng(param_ss), n_draws).items()}
    ref = reference_posture(params)

    size = (profile.employees / p["freq.ref_employees"]) ** p["freq.size_elasticity"]
    ind_key = profile.industry if f"ind.{profile.industry}.ransomware" in p else None

    by_scn, counts, lam_out, comps = {}, {}, {}, {}
    ev_it, ev_scn, ev_loss = [], [], []
    freq_seqs = freq_ss.spawn(len(SCENARIOS))
    sev_seqs = sev_ss.spawn(len(SCENARIOS))

    for si, scn in enumerate(SCENARIOS):
        rel_v = vulnerability(scn, posture, p) / vulnerability(scn, ref, p)
        ind = p[f"ind.{ind_key}.{scn}"] if ind_key else 1.0
        lam = p[f"lef_ref.{scn}"] * size * ind * rel_v
        # Inverse-CDF Poisson: the same uniform gives a count that is monotone in lambda.
        u = np.random.default_rng(freq_seqs[si]).random(n)
        k = poisson_inverse_cdf(u, lam)
        # Spawn a fixed number of slot streams so slot j always gets the same stream.
        slot_seqs = sev_seqs[si].spawn(MAX_EVENTS_PER_YEAR)
        totals: dict[str, float] = {}
        scn_loss = np.zeros(n)
        for j in range(int(k.max()) if k.size else 0):
            idx = np.flatnonzero(k > j)
            d = _Draws(np.random.default_rng(slot_seqs[j]), n, idx)
            c = _event_losses(scn, d, profile, posture, p)
            loss = np.sum(list(c.values()), axis=0)
            np.add.at(scn_loss, idx, loss)
            for name, v in c.items():
                totals[name] = totals.get(name, 0.0) + float(v.sum())
            ev_it.append(idx)
            ev_scn.append(np.full(idx.size, scn))
            ev_loss.append(loss)
        by_scn[scn], counts[scn], lam_out[scn], comps[scn] = scn_loss, k, lam, totals

    annual = sum(by_scn.values())
    if ev_it:
        insured = _insured(profile.policy, np.concatenate(ev_it), np.concatenate(ev_scn), np.concatenate(ev_loss), n)
    else:
        insured = np.zeros(n)
    return SimulationResult(profile, posture, n, seed, annual, insured, by_scn, counts, comps, lam_out, block)


def _insured(policy: Policy, it: np.ndarray, scn: np.ndarray, loss: np.ndarray, n: int) -> np.ndarray:
    """Apply per-event retention and (sub)limit, then the annual aggregate limit."""
    cap = np.select([scn == s for s in SCENARIOS], [policy.per_event_cap(s) for s in SCENARIOS])
    per_event = np.minimum(np.maximum(loss - policy.retention, 0.0), cap)
    annual = np.bincount(it, weights=per_event, minlength=n)
    return np.minimum(annual, policy.aggregate_limit)


def technical_premium(expected_loss: float, sd: float, risk_load: float, expense_ratio: float) -> float:
    """Standard-deviation-principle premium: (E[L] + alpha * SD[L]) / (1 - expense ratio).

    Illustrative only: real cyber pricing uses filed base rates, revenue and
    industry rating factors, minimum premiums and market-cycle judgement.
    """
    return (expected_loss + risk_load * sd) / (1.0 - expense_ratio)
