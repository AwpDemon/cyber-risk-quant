import numpy as np
import pytest
from scipy import stats

from crq.controls import CONTROLS, Posture, vulnerability
from crq.model import FirmProfile, Policy, _insured, poisson_inverse_cdf, reference_posture, simulate
from crq.parameters import ParameterSet

FIRM = FirmProfile("test", "healthcare", 20, 3e6, 8000, regulated=True)
WEAK = Posture({c: 0.0 for c in CONTROLS})
STRONG = Posture({c: 1.0 for c in CONTROLS})
N = 40_000


def test_reproducible_with_seed():
    a = simulate(FIRM, WEAK, n=N, seed=1)
    b = simulate(FIRM, WEAK, n=N, seed=1)
    assert np.array_equal(a.annual_loss, b.annual_loss)
    assert not np.array_equal(a.annual_loss, simulate(FIRM, WEAK, n=N, seed=2).annual_loss)


def test_common_random_numbers_make_better_controls_pathwise_cheaper():
    """Same seed: every simulated year is at least as cheap with stronger controls (monotone coupling)."""
    weak = simulate(FIRM, WEAK, n=N, seed=3)
    strong = simulate(FIRM, STRONG, n=N, seed=3)
    assert np.all(strong.annual_loss <= weak.annual_loss + 1e-6)
    for s in weak.event_counts:
        assert np.all(strong.event_counts[s] <= weak.event_counts[s])
    assert strong.ale < weak.ale


def test_single_control_improvement_never_hurts():
    base = Posture({c: 0.3 for c in CONTROLS})
    r0 = simulate(FIRM, base, n=N, seed=4)
    for c in CONTROLS:
        r1 = simulate(FIRM, base.with_level(c, 1.0), n=N, seed=4)
        assert np.all(r1.annual_loss <= r0.annual_loss + 1e-6), c


def test_insured_loss_respects_policy_terms():
    pol = Policy(aggregate_limit=250_000, retention=25_000, social_engineering_sublimit=50_000)
    firm = FirmProfile("t", "legal", 40, 8e6, 20000, policy=pol)
    r = simulate(firm, WEAK, n=N, seed=5)
    assert np.all(r.insured_loss <= r.annual_loss + 1e-6)
    assert r.insured_loss.max() <= 250_000 + 1e-6
    # unit check of the layer arithmetic
    it = np.array([0, 0, 1, 2])
    scn = np.array(["bec", "ransomware", "data_breach", "bec"])
    loss = np.array([500_000, 100_000, 5_000, 30_000])
    ins = _insured(pol, it, scn, loss, 3)
    assert ins.tolist() == [min(50_000 + 75_000, 250_000), 0.0, 5_000.0]


def test_event_frequency_matches_poisson_rate():
    params = ParameterSet(overrides={"lef_ref.bec": 0.2, "freq.size_elasticity": 0.0})
    r = simulate(FIRM, reference_posture(params), n=200_000, seed=6, params=params)
    lam = r.lam["bec"].mean()
    counts = r.event_counts["bec"]
    assert counts.mean() == pytest.approx(lam, rel=0.02)
    assert counts.var() >= counts.mean() * 0.98  # Poisson mixture: variance >= mean


def test_bec_severity_matches_closed_form():
    """Pin BEC parameters so expected loss per event has a closed form."""
    ov = {"bec.p_transfer": 1.0, "bec.p_reported_fast": 0.0, "bec.max_share_of_revenue": 1e9, "bec.transfer_median": 50_000,
          "bec.transfer_sigma": 1.0, "bec.response_median": 10_000, "lef_ref.bec": 0.5}
    params = ParameterSet(overrides=ov)
    r = simulate(FIRM, WEAK, n=200_000, seed=7, params=params)
    per_event = r.by_scenario["bec"].sum() / r.event_counts["bec"].sum()
    expected = 50_000 * np.exp(0.5) + 10_000 * np.exp(0.8**2 / 2)
    assert per_event == pytest.approx(expected, rel=0.03)


def test_poisson_inverse_cdf_matches_scipy():
    rng = np.random.default_rng(8)
    u, lam = rng.random(50_000), rng.uniform(0, 4, 50_000)
    assert np.array_equal(poisson_inverse_cdf(u, lam), stats.poisson.ppf(u, lam).astype(int))


def test_vulnerability_monotone_and_bounded():
    p = ParameterSet().sample(np.random.default_rng(9), 1000)
    for s in ("ransomware", "bec", "data_breach"):
        vw, vs = vulnerability(s, WEAK, p), vulnerability(s, STRONG, p)
        assert np.all(vs <= vw) and np.all(vs > 0) and np.all(vw <= 1.0 + 1e-9)


def test_exceedance_curve_is_monotone_and_tvar_ordering():
    r = simulate(FIRM, WEAK, n=N, seed=10)
    xs = np.logspace(3, 7, 50)
    ex = r.exceedance(xs)
    assert np.all(np.diff(ex) <= 0)
    assert r.exceedance([0])[0] == pytest.approx(r.prob_any_event(), abs=1e-3)
    assert r.tvar(0.99) >= r.quantile(0.99) >= r.quantile(0.5)


def test_two_dimensional_mc_interval_brackets_ale():
    r = simulate(FIRM, WEAK, n=100_000, seed=11, block=500)
    lo, hi = r.ale_interval()
    assert lo < r.ale < hi


def test_posture_validation():
    with pytest.raises(KeyError):
        Posture({"firewall": 1.0})
    with pytest.raises(ValueError):
        Posture({"edr": 1.5})
