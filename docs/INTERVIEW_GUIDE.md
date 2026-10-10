# Interview guide: cyber-risk-quant

This is written for you, Ali, so you can explain and defend every part of this project. Read it top to bottom once, then use the Q&A section as flash cards. All numbers quoted here come from `docs/results/*.json` (seed 20261010).

---

## Part 0. The 60-second pitch

> "When I was an IT intern at a small IT services firm, I helped SMB clients pull together control evidence for their cyber-insurance renewals: MFA screenshots, backup reports, EDR coverage. Insurers ask those questions because the controls drive claims, but nobody could tell the client what a missing control was actually *worth*. So I built a FAIR-style Monte Carlo model that turns the application questions into dollars. It anchors frequency on NAIC claim counts and Coalition's claim mix, and each control acts only on the attack routes it blocks, using Sophos and Verizon vector shares. It then simulates 100,000 years per control posture and outputs the annualized loss, the loss exceedance curve, the readiness tier, and a ranked gap list with each fix's effect on expected insured loss. I validated it against public benchmarks. It matches Coalition's average claim to within 1%. It failed a mid-size benchmark by 5x in version 1, and I fixed that with a size elasticity taken from Cyentia data. I also document where it still disagrees, like patching and HHS breach counts."

If they ask for a single headline result:

> "For a hypothetical 18-person dental clinic, fixing the three knock-out controls cuts expected annual loss 45%, from $2,720 to $1,504, and the worst-1%-of-years average from $258K to $148K. But the fixes cost more per year than the expected-loss reduction. The case for them is insurability and tail risk, not ALE, and that's a nuance I think underwriters and brokers live with every day."

---

## Part 1. Cyber insurance underwriting in plain English

**What a cyber policy covers (SMB package).**
- *First party:* your own costs. Incident response (forensics, breach counsel), data restoration, business interruption (lost income while systems are down), cyber extortion (ransom and negotiation), and funds-transfer / social-engineering fraud (usually sublimited, e.g. $100K-$250K).
- *Third party:* claims against you. Privacy liability and lawsuits, regulatory defence and penalties (HIPAA, state AGs), and payment-card (PCI) assessments.

**Policy structure words you must know.**
- *Aggregate limit:* the most the policy pays in a year, all claims combined ($1M is typical for small firms).
- *Retention:* the deductible, paid by the insured per claim ($10K-$50K here).
- *Sublimit:* a smaller cap inside the limit for a specific cover. Social engineering is the classic one. Carriers may also sublimit ransomware for weak controls.
- *Coinsurance:* the insured shares a percentage of a loss. Sometimes imposed on ransomware for weak controls.
- *Subjectivities:* conditions to satisfy before or soon after binding ("implement MFA on remote access within 30 days").
- *Loss ratio:* claims paid (plus defence costs) / premium earned. Aon's analysis of NAIC filings put the U.S. cyber loss ratio at 42% in 2023 (44% standalone, 36% package). The rest pays expenses and profit.

**Why carriers ask about specific controls.** After the 2019-2021 ransomware wave, cyber loss ratios spiked and carriers tightened underwriting. The application and the "ransomware supplemental" became control checklists. MFA (email, remote access, privileged accounts), offline/immutable tested backups, EDR, patching cadence, email filtering, training, payment-verification procedures and an IR plan are the common core. Some are **knock-outs**: missing them means a decline, a ransomware sublimit, or a fix-before-bind subjectivity. At-Bay says any policy covering ransomware is highly likely to require MFA. Marsh says firms lacking key controls face non-renewal or ransomware limitations.

**Where your internship fits.** Gathering MFA, backup and EDR evidence for renewals is the *evidence* side of underwriting. The underwriter's question is "Is the answer on the application true, and what does it mean for loss?" This project's Evidence checklist sheet lists exactly what proves each answer (identity-provider MFA report, backup job report with immutability setting and last restore test, EDR console export reconciled to the asset inventory). The model answers "what does it mean for loss." **Never** name clients or give specifics. Say "small business clients" and talk about the *types* of gaps you saw (e.g. a vendor accessing a server over remote desktop without MFA). The dental case study is a composite of common gaps, not a client.

---

## Part 2. FAIR from scratch

**FAIR = Factor Analysis of Information Risk.** It is an open standard (The Open Group) for expressing cyber risk in money instead of red/amber/green.

```
Risk = Loss Event Frequency (LEF) x Loss Magnitude (LM)
LEF  = Threat Event Frequency (TEF) x Vulnerability
LM   = Primary loss + Secondary loss
```

- **TEF:** how often a threat actor *acts* against you (phishing emails that land, login attempts, exploit scans).
- **Vulnerability:** the probability a threat event becomes a loss event. Controls live here.
- **LEF:** loss events per year, e.g. 0.0057 ransomware events/year for the dental clinic.
- **Primary loss:** your direct costs (response, restoration, downtime, ransom, stolen funds).
- **Secondary loss:** costs from others' reactions (notification, credit monitoring, regulators, lawsuits).

**Why FAIR beats a heat map.** "High likelihood, high impact" can't be added up, compared with a control's cost, or turned into a premium. FAIR outputs a distribution of annual loss in dollars, so you can answer "how much should we spend?" and "how much insurance should we buy?"

**A key design point you can show off:** public data never tells you TEF and Vulnerability separately; you only observe their product (claims). So I anchor LEF for a *reference* insured firm with *average* controls, and scale by the firm's **relative** vulnerability: V(firm) / V(reference). This is called an identifiability problem: two unknowns, one observable.

---

## Part 3. The math, step by step

### 3.1 Monte Carlo

Instead of solving for the loss distribution analytically (impossible with this many moving parts), simulate 100,000 independent "years". In each year, draw how many events of each type happen and how big each one is, then add them up. The 100,000 annual totals *are* the loss distribution. Everything (mean, percentiles, exceedance curve) is computed from those numbers.

**Monte Carlo error.** The ALE estimate has a standard error of SD / sqrt(n). For the dental clinic it's $115 on an ALE of $2,720 (4%). Rare events make this noisier than you'd expect: only ~1.7% of years have any loss.

### 3.2 Frequency: the Poisson distribution

If events happen independently at average rate lambda per year, the count N in a year is Poisson: P(N = k) = e^(-lambda) lambda^k / k!. With lambda = 0.0057, P(N=0) = 99.4%, P(N=1) ~ 0.57%, and P(N=2) is tiny.

**Where lambda comes from (the anchor):**
- NAIC: 33,561 claims on 4,369,741 policies (2023) = 0.77%; ~50,000 on 4,368,614 (2024) = 1.14% per policy-year.
- Coalition: ransomware = 21% of claims, BEC + funds-transfer fraud = 60%, everything else 19%.
- So for the reference firm, ransomware LEF ~ 1.14% x 21% = 0.24%/yr; BEC ~ 0.69%; data breach ~ 0.22%.

Then:
```
lambda = LEF_ref x (employees / 25)^beta x industry multiplier x V(firm)/V(ref)
```
beta (size elasticity, mode 0.12) comes from fitting IRIS 2022's probability-by-revenue table on a log-log scale.

### 3.3 How controls change vulnerability (the vector model)

Each scenario has an attack-route mix. For ransomware, Sophos 2025 gives roughly 29% credentials, 32% exploited vulnerabilities, 36% email, 3% other. A control only reduces the routes it blocks:

```
V = [ w_cred (1 - e_MFA L_MFA) + w_vuln (1 - e_patch L_patch) + w_email (1 - e_filter L_filter)(1 - e_train L_train) + w_other ]
    x (1 - e_EDR L_EDR) x (1 - e_PAM L_PAM)
```

- **L** = how fully the firm has the control (0 to 1, from the questionnaire).
- **e** = how much a *fully* implemented control cuts that route. It is uncertain, so it is sampled from a range each year. MFA is PERT(0.70, 0.90, 0.99). Microsoft measured 99.2%, and I discount for MFA-bypass attacks.
- EDR and privileged-account MFA multiply the *whole* thing, because they act after the attacker is in.

That one structure explains a result you can talk about. **EDR is the single most valuable fix for the dental clinic**, more than any knock-out MFA control, because MFA blocks one route while EDR works after entry on all of them.

**Worked example (the logic, not exact model numbers).** Say credentials are 29% of ransomware entries and MFA is 90% effective. Going from no MFA to full MFA removes 0.29 x 0.9 = 26% of ransomware vulnerability. Email and exploit routes are untouched. That's why no single control gets you to zero.

### 3.4 PERT and lognormal

- **PERT(min, mode, max):** a smooth, bounded distribution built from a Beta, with mean = (min + 4 mode + max)/6. It is the FAIR community's standard for expert ranges, because people think in "min / most likely / max".
- **Lognormal:** if log(X) is normal, X is lognormal. It fits money and time because they're positive, right-skewed, and errors are multiplicative ("2x off", not "$10K off"). Two facts to know:
  - median = e^mu
  - mean = e^(mu + sigma^2/2) > median, so the mean is pulled up by the tail.

**Derived parameter you can explain fully (BEC amount).** DBIR 2025 says the median BEC theft is about $50K. IC3 2024 says $2,770,151,146 over 21,442 complaints, a mean of $129,193. For a lognormal, mean/median = e^(sigma^2/2), so sigma = sqrt(2 ln(129,193/50,000)) = 1.38. Two numbers from two reports pin down one distribution.

**Another (recovery time).** Sophos 2025: 16% of victims recovered within a day, 53% within a week. Two quantiles of a lognormal give two equations:
```
ln(1) = mu + sigma z(0.16)
ln(7) = mu + sigma z(0.53)
```
Here z is the standard-normal quantile. Solving gives sigma = 1.82 and a median of 6.1 days. Check against a third, unused figure: the fit says 93% recover within 90 days, and Sophos reports 97%. Close, but not perfect. Sophos 2024 also split by backup status (46% vs 25% recovered within a week), which gives medians of 8.4 vs 23.9 days, a **2.84x downtime multiplier for compromised backups**.

### 3.5 Patching from real data (CISA KEV + NVD)

For each of 832 exploited CVEs published since 2022, t = days from publication to CISA listing it as known-exploited. 27% were listed by day 0 and 50% by day 7. If you patch s days after publication, you're exposed to known exploitation for max(0, s - t) days. Exposure removed = 1 - E[max(0, s - t)] / E[365 - t]. 7 days removes 99%, 30 removes 95%, 90 removes 80%. Then a realization factor (0.6-0.95) accounts for KEV lagging real exploitation and SLAs slipping.

### 3.6 Loss magnitude for a ransomware event

```
BI = recovery days x outage share x daily revenue x dependency x (1 - deferred revenue recovered) x size scale
+ incident response + restoration (per endpoint) + ransom (if paid) + breach costs (if data stolen)
```
- The probability of paying depends on backups: 67% when backups are compromised vs 36% when not (Sophos 2024).
- **Size scale** = (revenue / $5M)^(gamma - 1) with gamma = 0.25. Loss grows like revenue^0.25, not 1:1. This came from the validation failure (Part 5).

### 3.7 Outputs and why each exists

- **ALE (annualized loss expectancy)** = the mean annual loss. It is the number you compare with a control's annual cost, and the pure premium.
- **LEC (loss exceedance curve)** = P(annual loss > x) for every x. It is the single best risk picture: read across at "$100K" to get "0.70% chance per year".
- **VaR95 = $0 here, and why that matters.** Fewer than 5% of years have *any* loss, so the 95th percentile of annual loss is zero. That's a trap. A naive "95th-percentile loss reduction" metric would say controls do nothing. So I report exceedance probabilities and **TVaR99** = the average loss in the worst 1% of years.
- **Insured loss:** for each event, min(max(loss - retention, 0), cap), where the cap is the social-engineering sublimit for BEC. Then cap the year at the aggregate limit.
- **Indicated premium** = expected insured loss / market loss ratio (42%). This is the "loss-cost" view. It's honest about what it is: a technical indication, not a market price.

### 3.8 Common random numbers (CRN), a strong talking point

When comparing "current" against "with EDR", if each run used fresh random numbers, the difference would be partly noise. With an ALE standard error of ~$115, a $150 control effect would be unreadable. So both runs use the same random numbers:
- Same parameter draws.
- Frequency drawn by **inverse-CDF**: one uniform U per year, and N = smallest k with F(k; lambda) >= U. Lowering lambda can only lower N, never reshuffle which years have events.
- The j-th event of each year always takes its random numbers from the same stream.

Result: every simulated year is at least as cheap after any control improvement. **The test-suite checks this pathwise**, which is a much stronger check than "the average went down".

### 3.9 Two kinds of uncertainty

- *Aleatory:* year-to-year luck (will we get hit this year?).
- *Epistemic:* we don't know the parameters (is MFA 80% or 95% effective?).

Default FAIR practice mixes both into one curve. I also run a **two-dimensional Monte Carlo**: 400 parameter draws x 1,000 years each. The spread of the 400 ALEs gives a 90% interval from parameter uncertainty alone: **$1,086-$4,824 for the dental clinic**. Say it plainly: the point ALE of $2,720 is uncertain by roughly 2x either way, and the model is honest about that.

### 3.10 Tornado chart

Pin one uncertain parameter at its 10th percentile, then its 90th, keeping everything else random (and the same seed). Record the ALE each time, then sort by swing. For the dental clinic the top inputs are the ransomware frequency anchor, the healthcare ransomware multiplier, and the business-interruption outage share. That tells you where better data would most improve the estimate. One-at-a-time swings are smaller than the 2-D interval because the 2-D interval moves all parameters together.

---

## Part 4. The underwriting-readiness module

- 15 questions in application language. 11 drive the model; 4 (asset inventory, encryption, vendor access, logging) are **scored but not simulated**, because I couldn't defend a numeric effect size from public data. Saying "I chose not to model this" is a strength.
- **Score** = weighted average of answer scores (weights mirror underwriting emphasis: MFA and backups 12 each, EDR 10...).
- **Tier:**
  - any failing knock-out (MFA email, MFA remote, offline/immutable backups) = *Refer / restricted*, regardless of score;
  - otherwise >= 85 Preferred, >= 70 Standard, else Conditional.

  That mirrors how underwriting really works: knock-outs aren't averaged away. The law firm scores 66 overall but is referred for one reason, its backups.
- **Gap list:** fix each gap alone with CRN and report ALE reduction, TVaR99 reduction, indicated-premium change, an illustrative annual cost, and an **evidence-strength label** (strong / moderate / weak) for the premium narrative. Payment verification is "weak" because no public study quantifies it, even though it's the law firm's best fix.
- **Why the gap values don't add up:** controls overlap. Once MFA blocks a credential attack, EDR has fewer intrusions left to stop.

---

## Part 5. Validation: the part to lead with when asked "how do you know it's right?"

1. **What matched (out-of-sample).** Average cost per event vs Coalition's $115K average claim: **0.99x**. Ransomware vs Coalition: 1.16x. SME BEC vs NetDiligence: 0.92x.
2. **What failed and what I did.** Version 1 scaled business interruption linearly with revenue. For a NetDiligence-sized SME ($108M revenue) it predicted $3.48M per ransomware event vs ~$645K published: **5.4x too high**. Sophos's mid-size figure was 2x too high. Decomposing showed business interruption was 75% of the number. I added a size elasticity, *taken from Cyentia IRIS loss-by-revenue tiers, not tuned to NetDiligence*: loss grows with revenue^0.25. NetDiligence is now 0.99x. I label it **post-hoc**, because the benchmark prompted the change, which makes it weaker evidence than a clean out-of-sample test.
3. **What still disagrees.**
   - *Patching:* the model says 90-day vs 7-day patching changes incident frequency 1.03x; Marsh observed ~2x. Marsh's comparison is confounded (fast patchers do everything well). In the model, SMB frequency is dominated by BEC, which patching can't touch. The model probably undervalues patching somewhat.
   - *HHS:* for an average dental office the model implies ~26x more reportable breaches than the HHS portal shows (18 dental breaches/yr across 136,140 offices). I checked whether my name-matching undercounted: it catches 85% of entries whose description mentions dentistry, so no. The explanations are under-reporting, claims not equalling breaches, or dental being less targeted. Public data can't separate them. Consequence: read healthcare ALEs as upper-range. Control *rankings* don't depend on the anchor.

The interview line: **"I'd rather show you where my model is wrong and why than hide it."**

---

## Part 6. Likely questions with strong answers

**Q: Why FAIR instead of a qualitative risk matrix?**
A matrix can't be added, compared with cost, or priced. FAIR gives a loss distribution in dollars, which supports spend decisions, insurance limits and premium indications. A matrix is fine for triage; FAIR is for decisions involving money.

**Q: Where do your frequencies come from? Isn't that the weakest part?**
Yes, and the tornado confirms it: the ransomware frequency anchor is the top driver. I used NAIC claims per policy and Coalition's claim mix because claims are what an insurer prices on. I cross-checked against IRIS (model below their upper bound) and HHS (model well above observed reports). A carrier would replace the anchor with its own class frequency. The model is built so that's a one-line change.

**Q: How do you know MFA is 90% effective?**
I don't know it's exactly 90%. Microsoft measured a 99.2% reduction in account compromise across Azure AD accounts. I use a range of 70-99% with mode 90%, because org-level intrusions involve MFA bypass, partial coverage and legacy protocols. Then I check the implied effect: full vs no MFA changes incident frequency 1.76x in the model, and Marsh observed 1.4x for broad vs not-broad MFA. Same direction, similar size.

**Q: Your expected losses are only a few thousand dollars. Is cyber risk overhyped for SMBs?**
The *average* is small because events are rare (1-2% per year). The *distribution* is the point. The dental clinic has a 0.7% annual chance of losing more than $100K, and the average of its worst 1% of years is $258K, about 8% of revenue. That's why insurance exists: a small, certain premium against a rare, large loss. ALE alone would mislead.

**Q: The controls cost more than the ALE reduction. So why do them?**
1. Insurability: knock-outs decide whether you get coverage at all, and at what terms.
2. Tail risk: TVaR99 almost halves for the dental clinic.
3. Uninsured costs: retention, sublimits, and reputational loss and lost patients that aren't fully modelled.
4. Contract requirements from clients.

The model makes that trade-off explicit instead of pretending every control pays for itself.

**Q: How would an underwriter use this?**
- As a structured second opinion on the application: which answers matter most for loss.
- To set subjectivities (fix MFA on remote access before binding).
- To explain pricing or terms to the broker and insured ("closing these gaps lowers expected insured loss ~46%").
- A broker would use the gap list and evidence checklist to prepare a client for renewal. That's the workflow I supported as an intern.

**Q: How does insured loss differ from gross loss?**
Retention (the client pays the first $10K-$50K of each claim), sublimits (social engineering capped at $100K-$250K), and the aggregate limit. For the law firm, a large closing wire can exceed the $250K social-engineering sublimit, so part of the BEC tail stays uninsured no matter what the controls are.

**Q: What's the premium number? Is it a real price?**
No. It's expected insured loss divided by the market's 42% loss ratio, a loss-cost indication. Real premiums use filed base rates, revenue bands, minimum premiums and the market cycle. Small accounts often sit at the minimum premium, so a lower technical price may never reach the invoice. I couldn't find verified public SMB premium data to compare against, so I don't claim one.

**Q: Why 100,000 iterations? Why that seed?**
At ~1.7% event years, 100K gives about 1,700 loss years, and the ALE's standard error is ~4%, reported next to it. The seed is fixed so results are reproducible. Two full independent runs gave identical outputs. CRN makes comparisons much more precise than the 4% suggests.

**Q: What's the difference between VaR and TVaR?**
- VaR_q is the q-th percentile: "a loss this bad or worse happens (1-q) of the time".
- TVaR_q is the *average* of losses beyond that point.

TVaR captures how bad the tail is, not just where it starts, and it's coherent: diversification never increases it. For rare-event risks VaR can be $0 while TVaR is large, which is exactly the situation here.

**Q: What's a loss exceedance curve and how do you read it?**
The x-axis is a loss amount (log scale) and the y-axis is the probability the year's loss exceeds it. Read up from $100K: 0.70% for the clinic today, 0.46% after the knock-out fixes. The area under the curve is the ALE. Comparing curves shows whether a control mainly trims frequent small losses or the rare big ones.

**Q: What would you change with more time or data?**
- Carrier claims data with control attestations (the real calibration).
- State AG breach portals to test the HHS gap beyond healthcare.
- An EPSS-weighted patch curve and separate edge-device modelling.
- Correlated scenarios and a portfolio view for accumulation risk (many SMBs sharing an MSP or cloud provider).
- Bayesian updating of control effects.

**Q: What's one thing you'd defend strongly and one you're least sure of?**
- *Defend:* the structure. Vector-based relative vulnerability, CRN, separating insured from gross loss, and reporting tails as TVaR and exceedance instead of VaR95.
- *Least sure:* absolute frequency for healthcare (the HHS gap), and business-interruption assumptions (outage share, deferred revenue), which are judgement calls high in the tornado.

**Q: Where does the BEC median and mean come from, and why combine two reports?**
DBIR gives the median (~$50K) and IC3 lets me compute the mean ($129,193). A lognormal is fully defined by those two, so sigma = sqrt(2 ln(mean/median)) = 1.38. It's a nice example of turning two headline numbers into a full distribution.

**Q: How did you avoid making up numbers?**
- Every parameter in `config/parameters.yaml` has a basis (sourced / derived / assumption) and citations.
- The test-suite fails if a sourced or derived parameter lacks a source, or if an assumption is a single fixed value instead of a range.
- Derived numbers are recomputed in tests from the published inputs.
- 57 of 80 parameters are assumptions, and I say so in the README's first paragraph.

---

## Part 7. Design decisions, one line each

| Decision | Why |
|---|---|
| Anchor LEF on claims, scale by relative vulnerability | TEF and vulnerability aren't separately observable; insurers price on claims |
| Three scenarios (ransomware, BEC/FTF, breach) | Ransomware + BEC/FTF alone are ~80% of Coalition claims; the breach scenario absorbs the remaining ~19% |
| Vector-based control effects | A control only stops the routes it blocks; avoids double counting |
| PERT for expert ranges, lognormal for money/time | Standard FAIR practice; positive, skewed quantities |
| Inverse-CDF Poisson + per-slot streams | Makes control comparisons exact (CRN) |
| Report TVaR99 and exceedance, not VaR95 | VaR95 = $0 when < 5% of years have losses |
| Loss-cost premium indication | Transparent; doesn't pretend to be a filed rate |
| Knock-outs override the score | Mirrors real underwriting; a great average can't hide a missing control |
| Scored-not-modelled questions | Better no number than an invented one |
| Severity size elasticity from IRIS | Fixed a 5x validation failure without fitting to the failing benchmark |
| Python package + CLI + Streamlit + Excel | Engine is testable; underwriters live in Excel; UI for demos |

---

## Part 8. Glossary

- **ALE:** mean annual loss.
- **LEF / TEF:** loss / threat event frequency.
- **LEC:** loss exceedance curve.
- **VaR / TVaR:** percentile / tail-average loss.
- **CRN:** common random numbers.
- **PERT:** Beta-based min/mode/max distribution.
- **KEV:** CISA's list of exploited vulnerabilities.
- **EPSS:** probability a CVE gets exploited soon.
- **BEC / FTF:** business email compromise / funds transfer fraud.
- **EDR / MDR:** endpoint detection and response / managed (24x7 monitored) EDR.
- **PAM:** privileged access management.
- **DMARC:** email anti-spoofing policy.
- **Retention:** deductible.
- **Sublimit:** cap inside the limit.
- **Subjectivity:** condition of binding.
- **Loss ratio:** losses / premium.
- **Pure premium:** expected loss.
- **NAIC:** U.S. insurance regulators' association.
- **IC3:** FBI internet crime center.
- **DBIR:** Verizon's annual breach report.
