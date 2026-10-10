"""The derived numbers in parameters.yaml must stay consistent with the fits that produced them."""

import numpy as np
import pytest

from crq import calibration as cal
from crq.controls import kev_lags, patch_exposure_reduction
from crq.parameters import load_registry

REG = load_registry()


def spec(name):
    return REG[name].spec


def test_sophos_recovery_fit_matches_registry_and_source():
    f = cal.sophos_recovery_time()
    assert f["sigma"] == pytest.approx(spec("rw.recovery_days_sigma")["mode"], abs=0.01)
    assert f["median_good_backups"] == pytest.approx(spec("rw.recovery_days_median_good")["mode"], abs=0.05)
    assert f["bad_backup_multiplier"] == pytest.approx(spec("rw.downtime_mult_bad_backups")["mode"], abs=0.01)
    # the fitted curve reproduces the two published points
    from scipy import stats
    for days, share in ((1, 0.16), (7, 0.53)):
        assert stats.norm.cdf(np.log(days / f["median_all"]) / f["sigma"]) == pytest.approx(share, abs=1e-9)
    # and roughly the third, unused one: Sophos 2025 says 97% within three months
    assert 0.90 < f["check_p_within_90d"] < 0.99


def test_bec_lognormal_from_dbir_median_and_ic3_mean():
    f = cal.bec_transfer_lognormal()
    assert f["mean"] == pytest.approx(2_770_151_146 / 21_442)
    assert f["sigma"] == pytest.approx(spec("bec.transfer_sigma")["mode"], abs=0.01)


def test_iris_elasticities_match_registry():
    assert cal.iris_size_elasticity() == pytest.approx(spec("freq.size_elasticity")["mode"], abs=0.01)
    e = cal.iris_loss_size_elasticity()
    assert e["median_slope"] == pytest.approx(spec("sev.size_elasticity")["mode"], abs=0.01)
    assert e["p95_slope"] <= spec("sev.size_elasticity")["high"] + 1e-9


def test_reference_frequencies_match_registry():
    f = cal.reference_frequencies()
    for s in ("ransomware", "bec", "data_breach"):
        assert f[s]["mode"] == pytest.approx(spec(f"lef_ref.{s}")["mode"], abs=1e-4)
    assert f["_total"]["2024"] == pytest.approx(50_000 / 4_368_614)


def test_ransom_reference():
    r = cal.ransom_reference()
    assert r["median_paid"] == pytest.approx(spec("rw.ransom_median_ref")["mode"], rel=0.01)
    assert r["ref_revenue"] == pytest.approx(spec("rw.ransom_ref_revenue")["value"], rel=1e-6)


def test_shipped_kev_lags_and_patch_curve():
    lags = kev_lags()
    assert lags.size > 500
    slas = [7, 14, 30, 90, 180, 365]
    red = [patch_exposure_reduction(s) for s in slas]
    assert all(a >= b for a, b in zip(red, red[1:]))  # faster patching never removes less exposure
    assert red[-1] == pytest.approx(0.0)
    assert 0.9 < red[0] <= 1.0


def test_patch_curve_toy_example():
    # one CVE exploited from day 10: a 30-day SLA leaves 20 of 355 exposure-days
    assert patch_exposure_reduction(30, lags=np.array([10.0])) == pytest.approx(1 - 20 / 355)
    # zero-day (negative lag clipped to 0) with a 7-day SLA
    assert patch_exposure_reduction(7, lags=np.array([-5.0])) == pytest.approx(1 - 7 / 365)
