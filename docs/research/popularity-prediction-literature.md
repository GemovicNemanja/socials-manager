# Popularity and follower-growth literature: verified sources and recommended components

54 sources were verified on 2026-11-25: 47 DOIs via Crossref and 39 arXiv IDs via the arXiv API (some sources have both). Full text was read for Hutto 2013, Bakshy 2011, Su 2016 and Cheng 2014; the other sources were checked from their abstracts.

## Core sources

| Source | Finding | Relevance |
|---|---|---|
| Szabo & Huberman 2010, CACM, 10.1145/1787234.1787254 | A log-linear model predicts late popularity from early counts: 2h of Digg votes predicts the 30-day total. It works best where attention decays fast. | High |
| Pinto et al. 2013, WSDM, 10.1145/2433396.2433443 | Using every early increment, not just the latest count, gives about 20% lower error (figure from a secondary report). | High |
| Lerman & Hogg 2010, WWW, 10.1145/1772690.1772754 | Modelling how the site exposes content beats plain extrapolation. This is the precedent for building ranker mechanics into the model. | High |
| Cheng et al. 2014, WWW, 10.1145/2566486.2567997 | Predicts whether a cascade doubles: accuracy 0.795, AUC 0.877. Speed and early breadth carry most of the signal. | High |
| Zhao et al. 2015, SEISMIC, KDD, 10.1145/2783258.2783401 | A self-exciting point process for reshares, about 15% error 1h in. It needs reshare timestamps. | Med |
| Rizoiu et al. 2017, HIP, WWW, 10.1145/3038912.3052650 | Hawkes intensity model on binned counts with external promotion, studied on YouTube. | Med–High |
| Martin et al. 2016, WWW, 10.1145/2872427.2883001 | Even rich models explain less than half the variance in cascade size. | High |
| Shulman et al. 2016, ICWSM, 10.1609/icwsm.v10i1.14748 | Most early predictive power comes from how fast an item reaches its first few adopters. | High |
| Bakshy et al. 2011, WSDM, 10.1145/1935826.1935845 | Average cascade size is predictable (R² 0.98); individual cascades are not (R² 0.34). | Med |
| Pfeffer et al. 2023, ICWSM, 10.1609/icwsm.v17i1.22228 | Impressions peak at 72 s, the median half-life is about 80 min, and about 95% of tweets get no meaningful impressions after 24h. A preliminary study from before the Grok ranker. | High |
| Rotabi et al. 2017, WWW, 10.1145/3038912.3052647 ("Cascades: A view from Audience") | The impressions paradox: rare large cascades carry a large share of all impressions. | High |
| Galeazzi et al. 2024, arXiv 2410.17390 | Across 40M tweets, posts with external links get up to 8× fewer views. | High |
| Corsi 2024, EPJ Data Science, 10.1140/epjds/s13688-024-00456-3 | Compares impressions within engagement × follower strata. | Med–High |
| Ye, Luceri & Ferrara 2025, FAccT, 10.1145/3715275.3732159 | About 50% of For You comes from accounts the viewer doesn't follow, concentrated on a few popular accounts. | High |
| Gauthier et al. 2026, Nature, 10.1038/s41586-026-10098-2 | Randomized trial (N=4,965, about 7 weeks): the For You feed increased engagement, and the follows it caused persisted. | High |
| Huszár et al. 2022, PNAS, 10.1073/pnas.2025334119; Milli et al. 2025, PNAS Nexus, 10.1093/pnasnexus/pgaf062 | Algorithmic amplification is measurable, and engagement ranking amplifies divisive content. Supports a guardrail on negative feedback. | Med |
| Weng et al. 2013, KDD, 10.1145/2487575.2487607 | People follow accounts after seeing their posts relayed by others. | High |
| Myers & Leskovec 2014, WWW, 10.1145/2566486.2568043 | Follower growth is a steady rate plus bursts triggered by cascades. | High |
| Antoniades & Dovrolis 2015, Computational Social Networks, 10.1186/s40649-015-0023-6 | Follows that come through retweets outnumber follows that don't. | High |
| Su, Sharma & Goel 2016, WWW, 10.1145/2872427.2883040 | The launch of Who-to-Follow raised edge creation 22%: +50% for accounts with 100k+ followers, about +15% for accounts with 1–10k. | Med–High |
| Hutto et al. 2013, CHI, 10.1145/2470654.2470771 | NB autoregression on 507 users. Informational content had a positive coefficient, negative sentiment and hashtag overuse negative ones. **Its "30×" figure compares standardized coefficients and is not a growth multiplier.** | Med–High |
| Garimella & West 2021, ICWSM, 10.1609/icwsm.v15i1.18131 | Engagement per follower peaks and then declines, which implies follower elasticity β < 1. | Med–High |
| Tan, Lee & Pang 2014, ACL, 10.3115/v1/P14-1017 | Design precedent: pairs of posts by the same author with the same URL but different wording. | High |
| Goel et al. 2016, Management Science, 10.1287/mnsc.2015.2158 | Popularity is driven mainly by the single largest broadcast. | High |
| Miotto & Altmann 2014, PLoS ONE; Bessi 2017, Physica A; Davison & Smith 1990 | Peaks-over-threshold methods for extremes. Extremes are more predictable, and viral posts arrive as a Poisson process. | High |
| Russo et al. 2018 (Thompson sampling); Zhao et al. 2026, arXiv 2602.00943; Nie et al., arXiv 1708.01977; Denrell & March 2001 | Under Thompson sampling the prior dominates early exposure, and adaptively collected rates are biased downward (the hot-stove effect). | High |
| Czado, Gneiting & Held 2009, Biometrics, 10.1111/j.1541-0420.2009.01191.x | The randomized probability integral transform (PIT) for count data. | High |

Lower relevance: deep cascade models (DeepCas, DeepHawkes, CasCN) need full cascade graphs. LLM-era work (AutoCas, BuzzProphet) suggests using LLMs for content features, not for predicting numeric magnitudes.

## Recommended components

### Ranker implications

- **Use 48h as the target.** With a half-life of about 80 min and the For You cutoff at 48h, impressions at 48h are close to final reach. Keep 7d as a secondary target.
- **Reach has two parts.** In-network reach scales as F^β with β < 1; out-of-network reach carries most of the variance for a new account.
- **Two phases:**
  - **0–2h:** the cold-start slot uses Thompson sampling on likes per Home view. Its prior, Beta(0.75, 49.25), has a mean of 1.5% and is worth 50 views of evidence.
  - **After 2h:** reach saturates.

### (a) Forecast before publishing

- **Reach:** I_p = I_in + I_oon, each modelled as NB2:
  - log μ_in = a_in,acct + β·log(1+F) + x'θ_in + u_week
  - log μ_oon = a_oon,acct + x'θ_oon + λ·q_p + u_week
  - q_p ~ N(0,1) is the post's latent quality.
- **Engagement per action k:** y_pk | I_p ~ BetaBinomial(I_p, π_pk), with logit π_pk = g_k,acct + z'δ_k + κ_k·q_p. The shared q_p makes reach and engagement rate correlate.
- **Priors:**
  - Account intercepts: partially pooled toward the peer panel.
  - Like rate: Beta(0.75, 49.25).
  - External link: about −1 on the log scale (SD 0.7).
  - Replies to accounts the viewer doesn't follow: μ_oon ≈ 0.
  - β ~ N(0.7, 0.2²). This is a judgement call; no verified paper estimates β for X.
- **Minimum data:** at least 30 matured posts per peer and at least 20 of the account's own posts.

### (b) Nowcast at 15m, 30m, 1h, 2h, 6h, 24h and 48h

- **Baseline:**
  - log(1+I48) = a_t + b_t·log(1+I_t) + c_t·logit(π̂_like,t) + d_t·log(1+F) + Σ_j w_tj·log(1+ΔI_tj) + ε_t
  - π̂_like,t ~ Beta(0.75+likes, 49.25+impressions−likes).
  - Combine with the forecast made before publishing by precision weighting.
  - Expected pattern: c_t is large up to 2h and shrinks afterwards.
- **Challenger: a two-phase ranker model**, fit with an NB likelihood on binned increments:
  - **Phase A:** dI/dt = λ_in·F^β·h(t) + λ_cs·Pr[θ̃ > θ*]
  - **Phase B:** I(t) = I(T_A) + (I∞ − I(T_A))·H(t−T_A), with log I∞ = a + b·log Ŝ_p + d·log(1+F).
  - Ŝ_p is a score built from the ranker's weights.
- **Compare posts on the rate within roughly the first 200 impressions**, not the 48h rate, because the ranker throttles exposure adaptively.
- **Minimum data:** 50–100 complete trajectories, and at least 100 for the challenger.

### (c) Follower growth

- gains_t ~ NB(μ_t), with μ_t = Σ_p (ΔI_in·π_f,in + ΔI_oon·π_f,oon) + ν·F_{t−1}^α + exp(η_t), where η_t is a local-level random walk.
- losses_t ~ Binomial(F_{t−1}, ρ_t).
- Breakouts enter as impulses.
- Forecast by simulating paths from the post model.
- **Minimum data:** 4–8 weeks of daily follower counts.

### (d) Breakout detection

- **Residual:** r = log((1+I48)/(1+μ̂)).
- **Tail model:** for residuals above a threshold u set at the peer panel's 90th–95th percentile, fit r−u ~ GPD(ξ, σ), with ξ pooled from at least 2,000 peer posts. Use a discrete GPD for engagement counts (Hitz et al. 2024, 10.6339/24-JDS1120).
- **Frequency:** breakouts per month ~ Poisson-Gamma.
- **Early alarm at 1–2h** when P(I48 > μ̂·e^u) is high. Signals: speed to the first k engagements, a like-rate posterior above the prior, and engagers from outside the follower community. Confirm at 6h.

## Corrections to the earlier research's citations

1. arXiv 1702.06673 is "Cascades: A view from Audience".
2. arXiv 2003.09557 is "Variation across Scales: Measurement Fidelity under Twitter Data Sampling".
3. The full title of Garimella & West is "Evolution of Retweet Rates in Twitter User Careers: Analysis and Model".
4. SEISMIC is a point-process model, not a negative-binomial one.
5. Hutto's "30×" compares standardized coefficients, and the comparison coefficient is not significant. It is not a growth multiplier.
6. Szabo–Huberman's 2h horizon applies to Digg specifically.
7. Pfeffer et al. is a short, preliminary paper from before the Grok ranker.
8. Gauthier et al.: use the DOI rather than the broken #Sec21 anchor.
9. Add DOIs for HIP and Martin et al., and note that HIP studies YouTube.
10. Tan, Lee & Pang: the chenhaot.com link returns 404; use the ACL DOI.
11. Cola et al. 2023 is about whether new accounts survive, not how fast they gain followers.

## Gaps

- No verified estimate of follower elasticity β on X under the 2026 ranker. The peer panel has to supply it.
- No peer-reviewed study of the Grok-based ranker yet.
