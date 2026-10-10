import numpy as np
import pytest
from scipy import stats

from crq.distributions import PERT, Fixed, LogNormal90, LogNormalMedianMean, Uniform, from_spec
from crq.parameters import Fixed as FixedP
from crq.parameters import ParameterSet, load_registry, load_sources, validate_registry


def test_pert_bounds_and_mean():
    d = PERT(1.0, 2.0, 6.0)
    x = d.sample(np.random.default_rng(0), 200_000)
    assert x.min() >= 1.0 and x.max() <= 6.0
    assert abs(x.mean() - (1 + 4 * 2 + 6) / 6) < 0.01
    assert d.ppf(0.1) < d.central() < d.ppf(0.9)


def test_lognormal90_hits_its_percentiles():
    d = LogNormal90(10_000, 250_000)
    x = d.sample(np.random.default_rng(1), 400_000)
    assert abs(np.quantile(x, 0.05) / 10_000 - 1) < 0.02
    assert abs(np.quantile(x, 0.95) / 250_000 - 1) < 0.02
    assert d.ppf(0.05) == pytest.approx(10_000, rel=1e-9)


def test_lognormal_median_mean_matches_both_moments():
    d = LogNormalMedianMean(50_000, 129_193)
    assert d.sigma == pytest.approx(np.sqrt(2 * np.log(129_193 / 50_000)))
    assert stats.lognorm(s=d.sigma, scale=50_000).mean() == pytest.approx(129_193, rel=1e-9)


@pytest.mark.parametrize("spec", [{"dist": "pert", "low": 3, "mode": 1, "high": 2}, {"dist": "lognormal", "p05": 5, "p95": 1},
                                  {"dist": "nope"}, {"dist": "uniform", "low": 2, "high": 1}])
def test_bad_specs_raise(spec):
    with pytest.raises((ValueError, KeyError)):
        from_spec(spec)


def test_fixed_and_uniform():
    assert Fixed(3).sample(np.random.default_rng(0), 4).tolist() == [3, 3, 3, 3]
    assert Uniform(0, 2).central() == 1


def test_registry_provenance_is_clean():
    """Every parameter is sourced/derived with a citation, or an assumption expressed as a range."""
    reg = load_registry()
    assert validate_registry(reg, load_sources()) == []
    assert all(not isinstance(p.dist, FixedP) for p in reg.values() if p.basis == "assumption")
    assert len(reg) > 60


def test_overrides_pin_one_parameter_without_shifting_others():
    a = ParameterSet().sample(np.random.default_rng(5), 1000)
    b = ParameterSet(overrides={"eff.mfa": 0.5}).sample(np.random.default_rng(5), 1000)
    assert np.all(b["eff.mfa"] == 0.5)
    assert np.array_equal(a["eff.training"], b["eff.training"])  # stream unchanged for the others


def test_unknown_override_rejected():
    with pytest.raises(KeyError):
        ParameterSet(overrides={"not.a.param": 1})
