"""Control posture and how controls change loss-event frequency.

FAIR splits Loss Event Frequency (LEF) into Threat Event Frequency (TEF, how
often a threat actor acts against you) times Vulnerability (the probability a
threat event becomes a loss event). Security controls act on Vulnerability.

Public data cannot separate TEF from Vulnerability for a "typical" SMB (we only
ever observe their product, LEF), so the engine anchors LEF for a *reference*
firm with an *average* control posture, then scales it by the firm's
**relative vulnerability**::

    LEF_firm = LEF_ref * size_factor * industry_factor * V(firm posture) / V(reference posture)

V is built from attack vectors. Each scenario has an initial-access mix
(e.g. Sophos 2025: exploited vulnerability 32%, credentials 29%, email 37% for
ransomware). A control only reduces the vectors it actually blocks::

    V = [ sum_v  w_v * prod_{c blocks v} (1 - eff_c * level_c) ]  *  prod_{post-intrusion c} (1 - eff_c * level_c)

``level_c`` in [0, 1] is how fully the firm has implemented control c (from the
underwriting questionnaire); ``eff_c`` is how much a *fully* implemented control
cuts that vector (sampled from a range in ``parameters.yaml`` every iteration).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd

CONTROLS: tuple[str, ...] = (
    "mfa_email",
    "mfa_remote",
    "mfa_privileged",
    "email_security",
    "training",
    "patching",
    "edr",
    "backups",
    "ir_plan",
    "payment_verification",
)

CONTROL_LABELS = {
    "mfa_email": "MFA on email / cloud apps",
    "mfa_remote": "MFA on remote access (VPN/RDP)",
    "mfa_privileged": "MFA / PAM on privileged accounts",
    "email_security": "Advanced email filtering + DMARC",
    "training": "Phishing training + simulations",
    "patching": "Patch cadence (critical / internet-facing)",
    "edr": "EDR coverage (monitored)",
    "backups": "Backups: offline/immutable + tested",
    "ir_plan": "Incident response plan (tested)",
    "payment_verification": "Out-of-band payment verification",
}


@dataclass(frozen=True)
class Posture:
    """Implementation level (0 = absent, 1 = fully implemented) of each control."""

    levels: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for k, v in self.levels.items():
            if k not in CONTROLS:
                raise KeyError(f"unknown control {k!r}")
            if not 0.0 <= float(v) <= 1.0:
                raise ValueError(f"control level for {k} must be in [0,1], got {v}")

    def level(self, control: str) -> float:
        return float(self.levels.get(control, 0.0))

    def with_level(self, control: str, value: float) -> Posture:
        new = dict(self.levels)
        new[control] = float(value)
        return Posture(new)

    def as_dict(self) -> dict[str, float]:
        return {c: self.level(c) for c in CONTROLS}


# --- patch cadence -> effectiveness, derived from CISA KEV + NVD -------------


def _kev_lags_path() -> Path:
    return Path(str(resources.files("crq") / "data" / "kev_lags.csv"))


@lru_cache(maxsize=1)
def kev_lags() -> np.ndarray:
    """Days from CVE publication (NVD) to CISA KEV listing, CVEs published >= 2022.

    Produced by ``crq calibrate`` from public data; shipped with the package so
    the model runs offline.
    """
    df = pd.read_csv(_kev_lags_path())
    return df["lag_days"].to_numpy(dtype=float)


def patch_exposure_reduction(sla_days: float, horizon_days: float = 365.0, lags: np.ndarray | None = None) -> float:
    """Share of 'exploitable and unpatched' exposure removed by a patch SLA.

    For each actually-exploited CVE, exploitation is (conservatively) taken to
    start at its KEV listing date t (clipped at 0 for zero-days). A firm that
    patches ``sla_days`` after publication is exposed during [t, sla] if t < sla.
    A firm that never patches is exposed for the whole horizon [t, H]. So::

        reduction(sla) = 1 - E[max(0, min(sla, H) - t)] / E[H - t]

    KEV listing *lags* real exploitation, so this overstates what fast patching
    buys; the ``eff.patching_realization`` parameter discounts it.
    """
    t = np.clip(kev_lags() if lags is None else np.asarray(lags, dtype=float), 0, horizon_days)
    never = np.mean(horizon_days - t)
    sla = min(float(sla_days), horizon_days)
    exposed = np.mean(np.maximum(0.0, sla - t))
    return float(1.0 - exposed / never)


# --- vector structure ---------------------------------------------------------
# Which controls block which initial-access vector, per scenario. Weights of the
# vectors live in parameters.yaml (sourced from Sophos/VCDB); the *structure*
# (which control acts where) is a modelling decision documented in METHODOLOGY.md.

# credential vector: MFA level is a blend of the MFA controls relevant to the
# accounts that scenario's attackers actually log into.
CRED_MFA_BLEND = {
    "ransomware": {"mfa_remote": 0.7, "mfa_email": 0.3},  # Coveware: remote access compromise dominant
    "data_breach": {"mfa_email": 0.8, "mfa_remote": 0.2},  # mostly mailbox / cloud-app takeover
    "bec": {"mfa_email": 1.0},  # own-mailbox takeover
}


def _cred_level(posture: Posture, scenario: str) -> float:
    return sum(w * posture.level(c) for c, w in CRED_MFA_BLEND[scenario].items())


def vulnerability(scenario: str, posture: Posture, p: dict[str, np.ndarray]) -> np.ndarray:
    """Relative vulnerability V for one scenario (vectorised over iterations)."""
    L = posture.level
    if scenario == "ransomware":
        tot = p["rw.vec.credentials"] + p["rw.vec.exploit"] + p["rw.vec.email"] + p["rw.vec.other"]
        w_cred, w_vuln, w_email, w_other = (p[f"rw.vec.{k}"] / tot for k in ("credentials", "exploit", "email", "other"))
        cred = w_cred * (1 - p["eff.mfa"] * _cred_level(posture, scenario))
        vuln = w_vuln * (1 - p["eff.patching_realization"] * L("patching"))
        email = w_email * (1 - p["eff.email_security"] * L("email_security")) * (1 - p["eff.training"] * L("training"))
        entry = cred + vuln + email + w_other
        post = (1 - p["eff.edr_prevent"] * L("edr")) * (1 - p["eff.mfa_privileged"] * L("mfa_privileged"))
        return entry * post
    if scenario == "data_breach":
        # error / misuse / physical share is not touched by any modelled control;
        # the malicious remainder is split in VCDB proportions.
        w_other = p["db.vec.non_malicious"]
        w_cred = (1 - w_other) * p["db.split.credentials"]
        w_vuln = (1 - w_other) * p["db.split.exploit"]
        w_email = (1 - w_other) * (1 - p["db.split.credentials"] - p["db.split.exploit"])
        cred = w_cred * (1 - p["eff.mfa"] * _cred_level(posture, scenario))
        vuln = w_vuln * (1 - p["eff.patching_realization"] * L("patching"))
        email = w_email * (1 - p["eff.email_security"] * L("email_security")) * (1 - p["eff.training"] * L("training"))
        malicious = (cred + vuln + email) * (1 - p["eff.edr_breach"] * L("edr"))
        return malicious + w_other
    if scenario == "bec":
        w_ato, w_spoof = p["bec.vec.account_takeover"], p["bec.vec.impersonation"]
        w_vendor = np.clip(1 - w_ato - w_spoof, 0, None)  # vendor's mailbox compromised: only verification helps
        ato = w_ato * (1 - p["eff.mfa"] * _cred_level(posture, scenario))
        spoof = w_spoof * (1 - p["eff.email_security"] * L("email_security")) * (1 - p["eff.training"] * L("training"))
        entry = ato + spoof + w_vendor
        return entry * (1 - p["eff.payment_verification"] * L("payment_verification"))
    raise KeyError(scenario)
