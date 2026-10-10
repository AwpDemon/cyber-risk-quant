# Methodology

This page is the technical specification of the model. `docs/INTERVIEW_GUIDE.md` explains the same ideas from scratch.

## 1. Question the model answers

For one small or mid-size business and one set of security controls: **how much cyber loss should we expect in a year, how bad can a bad year get, and how much does each missing control cost in expected loss and in insurance terms?**

The model follows the **FAIR** (Factor Analysis of Information Risk) decomposition:

```
Risk  =  Loss Event Frequency (LEF)  x  Loss Magnitude (LM)
LEF   =  Threat Event Frequency (TEF) x  Vulnerability
LM    =  Primary loss (response, restoration, business interruption, ransom, stolen funds)
       + Secondary loss (notification, credit monitoring, regulatory, litigation)
```

Both factors are distributions, not point estimates, and are combined by Monte Carlo simulation.

## 2. Scenarios

Three loss scenarios cover most small-business cyber claims (Coalition: ransomware 21%, BEC/FTF 58-60%; NetDiligence: ransomware + BEC = 50% of SME claims):

| Scenario | What happens | Main loss components |
|---|---|---|
| Ransomware / extortion | Intrusion, encryption and/or data theft | Business interruption, response, restoration, ransom (if paid), breach costs if data stolen |
| BEC / funds-transfer fraud | Mailbox takeover or impersonation leads to a fraudulent payment | Stolen funds net of bank recall, response |
| Data breach (non-ransomware) | Stolen credentials, exploited app, phishing, or error/loss exposes personal data | Response, notification, credit monitoring, regulatory, litigation |

## 3. Frequency

### 3.1 Anchor: insurance claim frequency

TEF and Vulnerability cannot be estimated separately from public data (only their product is ever observed), so the model anchors **LEF for a reference insured SMB** and scales it:

```
lambda_s = LEF_ref,s  x  (employees / ref_employees)^beta  x  industry_s  x  V_s(firm) / V_s(reference)
N_s ~ Poisson(lambda_s)       (per simulated year, with lambda_s itself uncertain)
```

* `LEF_ref,s` = NAIC claims per policy (33,561 / 4,369,741 = 0.77% in 2023; ~50,000 / 4,368,614 = 1.14% in 2024) times Coalition's share of claims by scenario (ransomware 21%, BEC+FTF 60%, other 19%).
* `beta` (size elasticity of frequency): IRIS 2022 revenue-tier probabilities give a slope of 0.116 on a log-log fit; the range extends to 0.5 because IRIS 2025 reports the largest firms' per-firm rate is 620x that of small firms.
* Industry multipliers are assumptions with ranges (healthcare data breach 1.5x, legal BEC 1.5x, manufacturing ransomware 1.3x, ...).

### 3.2 Relative vulnerability: how controls act

Each scenario has an initial-access mix. A control only reduces the vectors it blocks; post-intrusion controls act on whatever gets in:

```
V = [ sum_v  w_v * prod_{c blocks v} (1 - eff_c * level_c) ]  x  prod_{post-intrusion c} (1 - eff_c * level_c)
```

| Scenario | Vector weights (source) | Entry controls | Post-intrusion controls |
|---|---|---|---|
| Ransomware | credentials 29%, exploit 32%, email 36%, other 3% (Sophos 2024/2025) | MFA (remote 70% / email 30% blend), patching, email security x training | EDR (prevent), privileged MFA/PAM |
| Data breach | non-malicious 20-65% (VCDB small victims vs HHS dental); malicious split credentials 37% / exploit 30% / email 33% (VCDB) | MFA (email 80% / remote 20%), patching, email security x training | EDR (breach) on the malicious share only |
| BEC / FTF | own-mailbox takeover 45%, impersonation 30%, vendor/bank-side 25% (Coalition, assumption) | MFA on email, email security x training | Out-of-band payment verification |

`level_c` comes from the questionnaire (0 = absent, 1 = fully implemented). `eff_c` is sampled every iteration from a range (e.g. MFA 0.70 / 0.90 / 0.99, anchored to Microsoft's measured 99.2% and discounted for MFA bypass).

### 3.3 Patch cadence from CISA KEV + NVD

For each of the 832 KEV CVEs published since 2022, `t` = days from NVD publication to KEV listing (clipped at 0). A firm patching `s` days after publication is exposed to known exploitation for `max(0, s - t)` days; a firm that never patches is exposed for `H - t` (H = 365). The patching control level is

```
level(s) = 1 - E[max(0, min(s,H) - t)] / E[H - t]
```

multiplied by `eff.patching_realization` (0.6-0.95) because KEV listing lags real exploitation and SLAs slip. End-of-life systems multiply the level by 0.9 (isolated) or 0.7.

## 4. Magnitude

All money components are lognormal (positive, right-skewed, multiplicative error) unless noted.

### Ransomware event
* **Recovery time** `T` = lognormal fitted to Sophos: 16% recovered within a day and 53% within a week (2025) fixes sigma = 1.82. Sophos 2024's split (46% vs 25% recovered within a week, backups intact vs compromised) gives median 8.4 days with good backups and a 2.84x multiplier with compromised or missing ones. The multiplier is applied as `2.84^(1 - backup_level)`. EDR containment and a tested IR plan shorten `T`; capped at 180 days.
* **Business interruption** = `T x outage_equivalence x daily revenue x operational dependency x (1 - deferred revenue recovered) x size_scale`.
* **Restoration** = endpoints x per-endpoint cost x containment x backup penalty x size_scale.
* **Size scaling (v2):** `size_scale = (size / reference size)^(gamma - 1)`, gamma = 0.25 (IRIS 2025 slope of log loss on log revenue; range 0.15-0.45). Linear scaling overstated mid-size losses 5x in validation (see `VALIDATION.md`).
* **Ransom**: paid with probability `0.67 x (1 - b) + p_good x b` (Sophos 2024: 67% pay when backups are compromised, 36% when not; `p_good` 8-36%). Amount: median $93K at $22M revenue (Sophos 2025 median demand for $10-50M firms x 85% paid/demand), scaled by revenue^0.4.
* **Exfiltration**: probability 28-70% (Sophos 2025, Coalition 2026); if it happens, breach costs below apply to a broad share of records.

### BEC / FTF event
* Money moves in 35-60% of events (Coalition 2026: FTF 27% of claims out of BEC+FTF 58%).
* Amount: lognormal with median $50K (DBIR 2025) and mean $129,193 (IC3 2024: $2,770,151,146 / 21,442), so sigma = sqrt(2 ln(mean/median)) = 1.38; capped at 5-20% of revenue.
* Recovery: if reported fast (20-50%), 40-85% is frozen (IC3 Recovery Asset Team: 66% in 2024, 71% in 2023).

### Data breach event
* Records exposed = records held x fraction, where the fraction is a mixture: with probability `p_broad` a Beta(4,2) (most of the database) else a Beta(1,30) (one mailbox or file share).
* Cost = response (lognormal) + records x notification cost + records x monitoring uptake x monitoring cost (sensitive data only) + regulatory penalty (regulated firms, probability 2-15%) + litigation (probability rising with sqrt(records/10,000), capped at 2-20%).

Fixed shape constants (structural choices, not tuned): Beta(4,2) / Beta(1,30) breach-fraction shapes; lognormal sigma 0.7 for per-endpoint restoration, 0.8 for BEC response, 1.0 for regulatory and litigation amounts; the 180-day downtime cap; 1.3 endpoints per employee when not given.

## 5. Simulation design

* **Iterations:** 100,000 simulated years per posture (0.4 s). The ALE's Monte Carlo standard error is reported next to it.
* **Seed:** 20261010 for every published number; `np.random.SeedSequence` spawns independent streams for parameters, frequency and each event slot.
* **Common random numbers:** the Poisson count uses the inverse CDF of one uniform per year (so a lower rate can only remove events), and the j-th event of each year always draws from the same stream. Comparing postures with the same seed therefore isolates the control effect; the test-suite verifies every simulated year is at least as cheap after any single control improvement.
* **Parameter uncertainty:** by default, every simulated year draws its own parameters (one-dimensional mixing, the usual FAIR practice). `ale_uncertainty()` runs a two-dimensional Monte Carlo (400 parameter draws x 1,000 years each) and reports a 90% interval for the ALE that reflects parameter uncertainty alone.

## 6. Outputs

* **ALE** = mean annual loss. **P(any event)**, **P(annual loss > $100K)**, **P(annual loss > $1M)**.
* **Loss exceedance curve (LEC):** `P(annual loss > x)` for every x.
* **TVaR99:** mean loss across the worst 1% of years. VaR95 is usually $0 for an SMB, because fewer than 5% of years have any loss event, so it is not reported.
* **Insured loss:** per event `min(max(loss - retention, 0), per-event cap)`, where the cap is the social-engineering sublimit for BEC and otherwise the aggregate; the annual total is capped at the aggregate limit.
* **Indicated premium (loss-cost view)** = expected insured loss / market loss ratio (Aon: 42% for 2023). It is a technical indication. Real premiums also reflect filed base rates, revenue bands, minimum premiums and the market cycle, and knock-out gaps decide whether a quote happens at all.
* **Readiness score:** weighted questionnaire score (0-100); any failing knock-out (MFA on email, MFA on remote access, offline/immutable backups) forces the "Refer / restricted" tier.
* **Gap analysis:** each non-best answer is moved to best practice alone (common random numbers), giving its ALE, TVaR99 and indicated-premium change, an illustrative annual cost, and the evidence an underwriter will ask for.
* **Tornado:** each uncertain parameter is pinned at its P10 and P90 while everything else varies; the 12 largest ALE swings are shown.

## 7. Known structural limits

* Independence: scenarios are simulated independently (no "one intrusion causes ransomware and BEC" coupling) and firms are independent (no systemic or cloud-outage aggregation). This model prices a single risk, not a portfolio.
* Controls act multiplicatively and independently within a vector; real controls interact (EDR telemetry helps IR; MFA reduces the value of training on credential phishing).
* The reference posture is an assumption; a firm's relative vulnerability is only as good as that denominator.
* Contingent business interruption (a vendor or MSP outage), cyber extortion without encryption at scale, and regulatory regimes outside the U.S. are not modelled.
