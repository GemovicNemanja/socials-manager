# Forecasting, calibration and monitoring: verified sources, spec and simulation

**Sources.** 64 sources were checked against Crossref, the arXiv API, OpenAlex or the publisher page on 2026-11-25.

**Simulation files.** In the scratchpad under `math/forecasting/`: scripts `sim_cusum.py`, `sim_scale_aci.py` and `sim_bocpd.py`, with results in the matching `sim_results*.txt`.

**Headline.** Tracking one metric at 3 posts/day, a 30% shift is detected after a typical 15–30 posts, not about 10.

## Key sources (H = drives the spec)

**Scoring and calibration**
- Gneiting & Raftery 2007, JASA, 10.1198/016214506000001437 (H)
- Gneiting, Balabdaoui & Raftery 2007, JRSS-B, 10.1111/j.1467-9868.2007.00587.x (H)
- Czado, Gneiting & Held 2009, Biometrics, 10.1111/j.1541-0420.2009.01191.x (H). Their contribution is the non-randomized PIT histogram; the randomized PIT is older.
- Kolassa 2016, IJF, 10.1016/j.ijforecast.2015.12.004 (H)
- Bracher et al. 2021, PLoS CB, 10.1371/journal.pcbi.1008618 (H)
- Bosse et al. 2023, PLoS CB, 10.1371/journal.pcbi.1011393 (H). Score on log1p.
- Diebold, Gunther & Tay 1998, IER, 10.2307/2527342 (H)
- Berkowitz 2001, JBES, 10.1198/07350010152596718 (H)
- Wei & Held 2014, TEST, 10.1007/s11749-014-0380-8 (H)

**Hierarchical models and workflow**
- Gelman & Hill (H); BDA3 (H); Gelman 2006 on variance priors, 10.1214/06-BA117A (H)
- Bayesian Workflow (arXiv 2011.01808, a preprint)
- LFO-CV: Bürkner, Gabry & Vehtari 2020, 10.1080/00949655.2020.1783262 (H). Use it instead of plain LOO for time-ordered data.

**Dynamic models**
- West, Harrison & Migon 1985, 10.1080/01621459.1985.10477131 (H)
- West & Harrison 1997 (H)
- West & Harrison 1986, "Monitoring and adaptation", 10.1080/01621459.1986.10478331 (H)
- Berry & West 2020, DCMM, 10.1080/07350015.2019.1604372 (H)
- Harvey & Fernandes 1989 (M–H)
- Brodersen et al. 2015 (CausalImpact) (M)

**Transfer priors**
- Neuenschwander et al. 2010, MAP prior, 10.1177/1740774509356002 (H)
- Schmidli et al. 2014, robust MAP, 10.1111/biom.12242 (H)
- Power prior: Chen & Ibrahim 2000, 10.1214/ss/1009212673

**Conformal prediction**
- Gibbs & Candès 2021, ACI, arXiv 2106.00170 (H)
- Zaffran et al. 2022, AgACI

**Monitoring and change points**
- Page 1954 (CUSUM) (H)
- Roberts 1959 (EWMA) (H)
- Steiner et al. 2000, risk-adjusted CUSUM, 10.1093/biostatistics/1.4.441 (H)
- Jensen et al. 2006 (H)
- Brook & Evans 1972 (ARL via Markov chain)
- Adams & MacKay 2007 (BOCPD); Killick et al. 2012 (PELT); ruptures

**Decisions and goals**
- FPP3 §1.2 on separating forecasts from goals (H)
- Kruschke 2018, ROPE (H)
- Chow 1970, reject option

## Corrections to earlier proposals

1. **Randomized PIT.** Never show it in the UI, because it changes on recompute. Display the mid-PIT; keep the randomized PIT (seeded by post id) for monitors.
2. **Status rule.** "PIT > 0.8 OR P(goal) > 0.7" mixes "vs forecast" with "vs goal". Give them separate readouts.
3. **"More than 70% of posts not working means miscalibration."** This almost never fires. Under calibration the probability is 4.5×10⁻⁹; after a real 30% drop only 38% of posts fall below PIT 0.2. Replace it with the detectors below.
4. **Peer prior.** It must be the predictive distribution for a new account (a MAP prior plus a robust mixture), not the posterior of the population mean.
5. **Promoting models.** Use LFO-CV, not PSIS-LOO.
6. **Scoring.** Score WIS/CRPS on log1p as well as on raw counts.

## Spec

### (a) Post model

Fit per age (primary 48h), jointly on the owner and peers:

- y_i ~ NB2(μ_i, φ_j)
- log μ_i = α_j + β_j·log(1+F) + x_i'(θ+δ_j) + s(hour, weekday) + u_{j,week}

Terms and priors:
- s: Fourier term with K=3 on a 24h period, plus a weekend term.
- u_{j,week}: weekly random walk, σ_u ~ half-N(0, 0.15).
- (α_j, β_j) ~ MVN with LKJ(2); τ_α ~ half-N(1), τ_β ~ half-N(0.3).
- μ_β ~ N(0.75, 0.2²). The ledger implies an elasticity of about 0.76.
- θ ~ N(0, 0.5²); owner deviations δ ~ N(0, τ_θ²), τ_θ ~ half-N(0.3).
- log φ ~ N(log 2, 0.7²).

Engagement metrics: NB2 with an offset of log impressions.

**Peer data → prior**
- Joint hierarchical fit, refreshed nightly in NumPyro. This is equivalent to the MAP prior.
- Cheap path: robust MAP 0.8·MAP + 0.2·vague.

**Staged promotion**
- Post counts of 0/20/60/150 make components eligible.
- A component is promoted when its LFO-CV ΔELPD exceeds 2 SE and the Berkowitz test passes.

### (b) Follower model

Daily funnel:
- Gains: G_t ~ NB2(exp(η_t) + π_follow·ΣPC_t, φ_G), where PC_t is profile clicks and η_t is a DGLM local level (discount 0.97).
- Losses: L_t ~ NB2(ρ_t·F_{t−1}).
- Use the Berry–West DCMM form if many days have zero gains.

Forecast with 4,000 Monte Carlo paths that feed impressions back from followers. Outputs: P(F_T ≥ goal) and what-if scenarios.

### (c) Status rule

1. **Per-post surprise.** u_mid = F(y−1) + ½·p(y).
   - u_mid ≥ 0.8: "above expected". u_mid ≤ 0.2: "below expected".
   - This is not a "working" verdict.
2. **Goal status for a scope** (last 21 posts, a lever, or a follower goal). With ε = log 1.1:
   - p_hi = P(θ_S > log G + ε) and p_lo = P(θ_S < log G − ε).
   - **Working** if p_hi ≥ 0.80. **Not working** if p_lo ≥ 0.80. Otherwise **near threshold**.
   - Reasons for near threshold: `close`, `uncertain` (80% interval width > 4ε) or `insufficient_owner_data` (< 5 posts).
   - Hysteresis: switch only when the opposite probability reaches 0.85, or the current one falls below 0.70.
3. **Calibration check.** Monthly Brier score and CORP reliability diagram for logged P(goal met).

### (d) Detectors

z = Φ⁻¹(randomized PIT) of 48h impressions on originals.

**D1: forecast drift**
- Alarm charts, each tuned to ARL0 = 100 posts:
  - Location CUSUM: k = 0.25, h = 5.61.
  - Or EWMA: λ = 0.1, L = 2.148.
  - Optional risk-adjusted NB likelihood-ratio CUSUM.
- Scale CUSUMs on z²−1:
  - k = 0.25, h = 11.06 (upper) and 6.94 (lower), each ARL0 = 200.
- Diagnostics every 21 posts, over the last 63:
  - Berkowitz LR test (3 df).
  - 50% and 80% coverage against binomial bands. For n = 60 the 80% band is [0.70, 0.90].
  - Wei–Held test for small counts.
- With K charts that page, set ARL0 per chart to 100·K. Compute h exactly with a Brook–Evans Markov chain.
- On alarm:
  - West–Harrison intervention (σ_u × 3 for a week).
  - Switch to ACI intervals.
  - Reset with a head start of h/2.
- BOCPD/PELT explain when the regime changed. They don't raise alarms.

**D2: goal is wrong** (only while D1 is quiet)
- π̄ = mean over the last 30 posts of P(y ≥ G).
- If π̄ < 0.10, suggest the 60th–70th predictive percentile as the new goal. If π̄ > 0.90, suggest the 70th–80th.
- Check against peer percentiles for the same follower band, and run a Poisson-binomial hit-count test.

**D3: adaptive conformal intervals (ACI)**
- γ = 0.05, or AgACI over {0.01, 0.02, 0.05, 0.1}.
- α_t clipped to [0.01, 0.6].
- Alarm if α_t < 0.10 for 20 posts.

### (e) Release gates

1. Rolling-origin backtest with frozen forecasts. Test cold start by leaving out one peer account.
2. Log-scale CRPS skill vs the trailing-median baseline > 0, with the lower bound of the week-block bootstrap 90% interval > 0.
3. Coverage inside the binomial bands.
4. Berkowitz and PIT χ² p-values > 0.01.
5. No CUSUM alarm in the backtest.
6. 80% interval width ≤ 1.1× the baseline's.
7. Sampler health: R̂ < 1.01, ESS > 400, SBC passes.
8. Brier score no worse than the baseline.

## Simulation: zero-state ARL1 for a 30% drop with CUSUM (k = 0.25, h = 5.61)

| Noise φ (log-sd) | ARL1 (posts) | P(alarm ≤10 posts) | P(alarm ≤20 posts) |
|---|---|---|---|
| 1.5 (0.97) | 29.8 | 0.10 | 0.40 |
| 3 (0.63) | 17.6 | 0.24 | 0.70 |
| 8 (0.37) | 9.1 | 0.72 | 0.99 |
| 20 (0.22) | 5.4 | 0.99 | 1.00 |

**Other charts and metrics**
- EWMA and the LR-CUSUM detect drops about 10–15% faster.
- Likes: ARL1 20–25.
- Follows per post: ARL1 55–90, too slow to use. Monitor daily follower gains instead.

**Limit.** ARL1 ≤ 10 at ARL0 = 100 needs a shift of at least 0.85 SD, meaning predictive log-sd ≤ 0.42.

**Robustness of ARL0 (nominal 100)**
- Autocorrelation in z, ρ = 0.1 / 0.2 / 0.3: ARL0 71 / 53 / 42.
- Plug-in parameters from n = 20 / 60 / 150 posts: ARL0 66 / 82 / 92. So use the posterior predictive, not plug-in estimates.
- Overconfident dispersion: ARL0 23. In this case the alarm is correct.

**Scale chart and intervals**
- Scale CUSUM: ARL1 15.4.
- ACI 80% coverage: 0.63 with no adaptation, 0.77 with γ = 0.02, 0.80 with γ = 0.05.

**BOCPD vs CUSUM**
- After fixing a bug, BOCPD is slower than CUSUM at steady state. At φ = 3: median delay 19 vs 12 posts; P(≤10) 0.29 vs 0.42.

## Libraries (PyPI, 2026-11-25)

- **Core fitting:** NumPyro 0.22.0 + JAX 0.12.1. Alternatives: PyMC 6.4.0 + nutpie 0.16.11, or CmdStanPy 1.3.2.
- **Diagnostics:** ArviZ 1.3.0. LFO-CV has to be written by hand.
- **Baselines and tests:** statsmodels 0.15.0.
- **Scoring:** scoringrules 0.12.0.
- **Change points:** ruptures 1.1.10 (Python < 3.14).
- **Optional:** bambi 0.21.0, MAPIE 1.6.0.
- **Avoid:** properscoring.
- Python 3.13 satisfies every constraint.
