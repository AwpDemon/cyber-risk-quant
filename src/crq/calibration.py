"""Turn public data and published statistics into model parameters.

Two kinds of calibration live here:

1. **Dataset-derived** (needs ``data/raw`` from ``crq data download``):
   * CISA KEV + NVD -> distribution of days from CVE publication to KEV listing
     (drives the patch-cadence effect).
   * VCDB -> initial-access / action mix for small victims (drives the
     data-breach vector weights).
   * HHS OCR + Census CBP -> dental-practice breach statistics (validation).
2. **Published-statistic fits** (pure functions, no data needed, unit-tested):
   * Sophos recovery-time percentages -> lognormal recovery time.
   * DBIR median + IC3 mean -> lognormal BEC transfer amount.
   * IRIS revenue-tier probabilities -> size elasticity of frequency.
   * NAIC claim counts x Coalition claim mix -> reference loss event frequencies.

Every number returned by the fits is copied (rounded) into ``parameters.yaml``
and ``tests/test_calibration.py`` checks the YAML still matches the fit.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from .data_sources import RAW, REPO_ROOT

PROCESSED = REPO_ROOT / "data" / "processed"
PKG_DATA = Path(__file__).resolve().parent / "data"


# --------------------------------------------------------------------------
# Published-statistic fits (sourced inputs -> derived parameters)
# --------------------------------------------------------------------------

def fit_lognormal_two_quantiles(x1: float, p1: float, x2: float, p2: float) -> tuple[float, float]:
    """Lognormal (median, sigma) such that P(X <= x1) = p1 and P(X <= x2) = p2."""
    z1, z2 = stats.norm.ppf(p1), stats.norm.ppf(p2)
    sigma = (np.log(x2) - np.log(x1)) / (z2 - z1)
    mu = np.log(x1) - sigma * z1
    return float(np.exp(mu)), float(sigma)


def sophos_recovery_time() -> dict[str, float]:
    """Recovery-time lognormal from Sophos.

    * Shape: Sophos 2025 says 16% of victims fully recovered within a day and
      53% within a week -> sigma.
    * Backups: Sophos 2024 says 46% of victims whose backups were NOT
      compromised recovered within a week, vs 25% when backups WERE compromised.
      Holding sigma fixed, those two shares give two medians; their ratio is
      the downtime multiplier for compromised / missing backups.
    """
    med_all, sigma = fit_lognormal_two_quantiles(1.0, 0.16, 7.0, 0.53)
    med_good = 7.0 / np.exp(sigma * stats.norm.ppf(0.46))
    med_bad = 7.0 / np.exp(sigma * stats.norm.ppf(0.25))
    p90d = stats.norm.cdf((np.log(90) - np.log(med_all)) / sigma)
    return {"median_all": med_all, "sigma": sigma, "median_good_backups": float(med_good),
            "median_bad_backups": float(med_bad), "bad_backup_multiplier": float(med_bad / med_good),
            "check_p_within_90d": float(p90d)}  # Sophos 2025 reports 97% within three months


def bec_transfer_lognormal() -> dict[str, float]:
    """BEC amount: DBIR 2025 median (~$50,000) + IC3 2024 mean ($2,770,151,146 / 21,442 complaints)."""
    median = 50_000.0
    mean = 2_770_151_146 / 21_442
    sigma = float(np.sqrt(2 * np.log(mean / median)))
    return {"median": median, "mean": mean, "sigma": sigma}


IRIS_2022_UPPER = {  # revenue tier (low, high) -> P(>=1 loss event in a year), upper-bound model
    (1e7, 1e8): 0.1153, (1e8, 1e9): 0.1295, (1e9, 1e10): 0.1704, (1e10, 1e11): 0.2193, (1e11, 1e12): 0.2933}


def iris_size_elasticity() -> float:
    """Slope of log(annual event rate) on log(revenue) across IRIS 2022 tiers.

    Converts each probability to a Poisson rate (-ln(1-p)) and uses the
    geometric midpoint of each revenue tier.
    """
    x = np.array([np.log(np.sqrt(lo * hi)) for lo, hi in IRIS_2022_UPPER])
    y = np.array([np.log(-np.log(1 - p)) for p in IRIS_2022_UPPER.values()])
    return float(np.polyfit(x, y, 1)[0])


IRIS_2025_MEDIAN_LOSS = {  # revenue tier (low, high) -> median loss per event (IRIS 2025, Table 1)
    (1e6, 1e8): 357.0e3, (1e8, 1e9): 466.7e3, (1e9, 1e10): 1.8e6, (1e10, 1e11): 2.2e6}
IRIS_2025_P95_LOSS = {(1e6, 1e8): 9.1e6, (1e8, 1e9): 12.3e6, (1e9, 1e10): 61.8e6, (1e10, 1e11): 266.2e6}


def iris_loss_size_elasticity() -> dict[str, float]:
    """Slope of log(loss per event) on log(revenue) across IRIS 2025 tiers.

    Used for ``sev.size_elasticity``: losses grow far more slowly than revenue.
    The ">$10B" tier is given a $10B-$100B midpoint; "<$100M" uses $1M-$100M.
    """
    x = np.array([np.log(np.sqrt(lo * hi)) for lo, hi in IRIS_2025_MEDIAN_LOSS])
    med = float(np.polyfit(x, np.log(list(IRIS_2025_MEDIAN_LOSS.values())), 1)[0])
    p95 = float(np.polyfit(x, np.log(list(IRIS_2025_P95_LOSS.values())), 1)[0])
    return {"median_slope": med, "p95_slope": p95}


def reference_frequencies() -> dict[str, dict[str, float]]:
    """Claims per policy-year (NAIC) split by Coalition's claim mix.

    NAIC: 33,561 claims / 4,369,741 policies (2023); ~50,000 / 4,368,614 (2024).
    Coalition 2025 report (2024 data): ransomware 21% of claims, BEC + FTF 60%.
    The remaining 19% is treated as the non-ransomware breach / other bucket.
    """
    f2023 = 33_561 / 4_369_741
    f2024 = 50_000 / 4_368_614
    mix = {"ransomware": 0.21, "bec": 0.60, "data_breach": 0.19}
    return {s: {"low": f2023 * w, "mode": f2024 * w, "high": 1.5 * f2024 * w} for s, w in mix.items()} | {
        "_total": {"2023": f2023, "2024": f2024}}


def ransom_reference() -> dict[str, float]:
    """Sophos 2025: median demand $109,670 for $10M-$50M revenue orgs; payments average 85% of demand."""
    return {"median_paid": 109_670 * 0.85, "ref_revenue": float(np.sqrt(10e6 * 50e6))}


# --------------------------------------------------------------------------
# Dataset-derived calibration
# --------------------------------------------------------------------------

def kev_lags(raw: Path = RAW, since: str = "2022-01-01") -> pd.DataFrame:
    """Days from NVD publication to CISA KEV listing for CVEs published since ``since``.

    KEV launched in Nov 2021 with a large backfill of old CVEs, so only CVEs
    published after launch give a meaningful "how fast does exploitation
    follow disclosure" distribution.
    """
    nvd = json.loads((raw / "nvd_kev.json").read_text(encoding="utf-8"))
    pub = pd.DataFrame([{"cve": v["cve"]["id"], "published": v["cve"]["published"]} for v in nvd["vulnerabilities"]])
    kev = pd.DataFrame(json.loads((raw / "kev.json").read_text(encoding="utf-8"))["vulnerabilities"])
    df = kev.merge(pub, left_on="cveID", right_on="cve", how="inner")
    df["published"] = pd.to_datetime(df["published"]).dt.normalize()
    df["date_added"] = pd.to_datetime(df["dateAdded"])
    df["lag_days"] = (df["date_added"] - df["published"]).dt.days
    df = df[df["published"] >= since]
    return df.rename(columns={"vendorProject": "vendor", "knownRansomwareCampaignUse": "ransomware_use"})[
        ["cve", "vendor", "published", "date_added", "lag_days", "ransomware_use"]].sort_values("cve").reset_index(drop=True)


def kev_summary(lags: pd.DataFrame) -> dict:
    lag = lags["lag_days"]
    return {
        "n_cves": int(len(lags)),
        "published_from": str(lags["published"].min().date()),
        "published_to": str(lags["published"].max().date()),
        "share_listed_by_day": {str(d): round(float((lag <= d).mean()), 4) for d in (0, 7, 14, 30, 60, 90, 180, 365)},
        "median_lag_days": float(lag.median()),
        "share_known_ransomware_use": round(float((lags["ransomware_use"] == "Known").mean()), 4),
        "top_vendors": lags["vendor"].value_counts().head(10).to_dict(),
    }


def load_hhs(raw: Path = RAW) -> pd.DataFrame:
    frames = []
    for name in ("hhs_breaches_archive.csv", "hhs_breaches_open.csv"):
        df = pd.read_csv(raw / name)
        df.columns = ["entity", "state", "entity_type", "individuals", "submitted", "breach_type", "location", "ba_present", "description"]
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["submitted"] = pd.to_datetime(df["submitted"], format="%m/%d/%Y")
    df["year"] = df["submitted"].dt.year
    return df.drop_duplicates(["entity", "submitted", "individuals"]).reset_index(drop=True)


DENTAL_PATTERN = r"dental|dentist|orthodon|endodon|periodon|oral surg|oral and maxillofacial|\bdds\b|\bd\.d\.s\b|\bdmd\b|\bd\.m\.d\b"


def dental_breaches(hhs: pd.DataFrame) -> pd.DataFrame:
    """Healthcare-provider breaches whose covered-entity name identifies a dental practice.

    Name matching is a heuristic: it misses practices with non-descriptive names
    and dental-service organisations filed under a parent name, so counts are a
    lower bound. Entity names are dropped from the committed output.
    """
    m = hhs["entity"].str.contains(DENTAL_PATTERN, case=False, regex=True) & (hhs["entity_type"] == "Healthcare Provider")
    out = hhs.loc[m, ["year", "state", "individuals", "breach_type", "location"]].copy()
    out["hacking"] = out["breach_type"].str.contains("Hacking", na=False)
    return out.sort_values(["year", "individuals"]).reset_index(drop=True)


def cbp_establishments(raw: Path = RAW) -> dict[str, dict[str, int]]:
    cbp = pd.read_csv(raw / "cbp22us.txt", dtype=str)
    cbp = cbp[cbp["lfo"] == "-"]
    out = {}
    for code, label in (("621210", "dentists"), ("541110", "lawyers"), ("31----", "manufacturing")):
        row = cbp[cbp["naics"] == code].iloc[0]
        out[label] = {"naics": code, "establishments": int(row["est"]), "employees": int(row["emp"])}
    return out


VCDB_COLS = {
    "small": ["victim.employee_count.1 to 10", "victim.employee_count.11 to 100", "victim.employee_count.Small"],
    "year": "timeline.incident.year",
    "ransomware": "action.malware.variety.Ransomware",
    "creds": ["action.hacking.variety.Use of stolen creds", "action.hacking.variety.Brute force"],
    "exploit": ["action.hacking.variety.Exploit vuln", "action.hacking.variety.SQLi"],
    "social": ["action.Social"],
    "hacking": ["action.Hacking"],
    "malware": ["action.Malware"],
    "error_misuse_physical": ["action.Error", "action.Misuse", "action.Physical"],
}


def vcdb_small_victim_vectors(raw: Path = RAW, years=(2015, 2025)) -> pd.DataFrame:
    """Non-ransomware incidents at 1-100-employee victims, bucketed by the control that could stop them.

    Each incident is assigned to the first matching bucket in this priority:
    credentials -> exploit -> email/social -> other hacking/malware (unknown
    entry) -> error/misuse/physical. Incidents with no known action are dropped.
    """
    flat = [c for v in VCDB_COLS.values() for c in (v if isinstance(v, list) else [v])]
    df = pd.read_csv(raw / "vcdb.csv", usecols=lambda c: c in set(flat), low_memory=False)
    small = df[VCDB_COLS["small"]].fillna(False).astype(bool).any(axis=1)
    yr = df[VCDB_COLS["year"]].between(*years)
    nonrw = ~df[VCDB_COLS["ransomware"]].fillna(False).astype(bool)
    d = df[small & yr & nonrw]

    def anyof(cols):
        present = [c for c in cols if c in d.columns]
        return d[present].fillna(False).astype(bool).any(axis=1)

    bucket = np.select(
        [anyof(VCDB_COLS["creds"]), anyof(VCDB_COLS["exploit"]), anyof(VCDB_COLS["social"]),
         anyof(VCDB_COLS["hacking"]) | anyof(VCDB_COLS["malware"]), anyof(VCDB_COLS["error_misuse_physical"])],
        ["credentials", "exploit", "email_social", "other_malicious", "error_misuse_physical"], default="unknown")
    counts = pd.Series(bucket).value_counts()
    known = counts.drop("unknown", errors="ignore")
    return pd.DataFrame({"incidents": known, "share": (known / known.sum()).round(4)}).rename_axis("bucket").reset_index()


def run_all(raw: Path = RAW) -> dict:
    """Recompute every dataset-derived artefact and write committed outputs."""
    PROCESSED.mkdir(parents=True, exist_ok=True)
    PKG_DATA.mkdir(parents=True, exist_ok=True)
    lags = kev_lags(raw)
    lags[["cve", "vendor", "published", "date_added", "lag_days", "ransomware_use"]].to_csv(PKG_DATA / "kev_lags.csv", index=False)
    hhs = load_hhs(raw)
    dental = dental_breaches(hhs)
    dental.to_csv(PROCESSED / "hhs_dental_breaches.csv", index=False)
    hp = hhs[hhs["entity_type"] == "Healthcare Provider"]
    hp_by_year = hp.groupby("year").agg(breaches=("individuals", "size"), individuals=("individuals", "sum")).reset_index()
    hp_by_year.to_csv(PROCESSED / "hhs_provider_breaches_by_year.csv", index=False)
    vectors = vcdb_small_victim_vectors(raw)
    vectors.to_csv(PROCESSED / "vcdb_small_victim_vectors.csv", index=False)
    estab = cbp_establishments(raw)
    summary = {
        "kev": kev_summary(lags),
        "hhs": {"records": int(len(hhs)), "years": [int(hhs.year.min()), int(hhs.year.max())],
                "dental_breaches": int(len(dental)),
                "dental_by_year": dental.groupby("year").size().to_dict(),
                "dental_hacking_share_2019_on": round(float(dental[dental.year >= 2019]["hacking"].mean()), 4),
                "dental_individuals_quantiles": {str(q): float(dental["individuals"].quantile(q)) for q in (0.25, 0.5, 0.75, 0.9)}},
        "vcdb_small_victim_vectors": vectors.set_index("bucket")["share"].to_dict(),
        "vcdb_small_victim_incidents": int(vectors["incidents"].sum()),
        "cbp2022": estab,
        "fits": {"sophos_recovery_time": sophos_recovery_time(), "bec_transfer": bec_transfer_lognormal(),
                 "iris_size_elasticity": iris_size_elasticity(),
                 "iris_loss_size_elasticity": iris_loss_size_elasticity(), "reference_frequencies": reference_frequencies(),
                 "ransom_reference": ransom_reference()},
    }
    (PROCESSED / "calibration_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary
