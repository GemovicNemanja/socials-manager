# Experiments for a single social-media account: literature check and framework spec

Date: 2026-11-25. Scope: one X account (one unit), ~3 posts/day. AI agents propose experiments; the platform assigns, tracks and reads them out.
Working files: `scripts/` (simulations, Python 3 + numpy/scipy venv), `downloads/` (Crossref/arXiv metadata and PDFs, untrusted), `sim_*.md` (raw simulation output).

## 0. How the sources were checked
- 39 DOIs resolved through `https://api.crossref.org/works/<doi>` (36 directly, plus Onghena & Edgington, Scott 2015, and Deng–Lu–Chen DSAA found by Crossref bibliographic search). Authors, year, title, venue, volume(issue):pages below come from Crossref, not from memory.
- 13 arXiv IDs resolved through the arXiv API (title, authors, first-posted date, journal_ref where present).
- Key claims read in the paper text: Bojinov, Simchi-Levi & Zhao Theorem 2 and Section 4.3 (arXiv 2009.00148v4); Hadad et al. Eq. 15 (arXiv 1911.02768v4); Javanmard & Montanari Theorem 3.1 and Section 3.2 (arXiv 1603.09000v3); the Ariu et al. abstract (arXiv 2109.08229).
- Not checked through an API: Fisher (1935), *The Design of Experiments* (book), and the Stucchio (2015) VWO Bayesian A/B whitepaper (not peer reviewed). They are cited only as background.

## 1. Verified bibliography (what each source gives us, relevance)

Relevance: H = changes the spec, M = used for a component or caveat, L = background.

### 1a. Single-unit designs: n-of-1, single-case, micro-randomized, time-series experiments
| # | Source (Crossref/arXiv verified) | What it gives us | Rel. |
|---|---|---|---|
| 1 | Klasnja, Hekler, Shiffman, Boruvka, Almirall, Tewari, Murphy (2015). Microrandomized trials: an experimental design for developing just-in-time adaptive interventions. *Health Psychology* 34(Suppl):1220–1228. doi:10.1037/hea0000305 | The template: re-randomize at every decision point (here, each post) and estimate proximal effects. Assignment probabilities are known and logged. | H |
| 2 | Liao, Klasnja, Tewari, Murphy (2016). Sample size calculations for micro-randomized trials in mHealth. *Statistics in Medicine* 35(12):1944–1971. doi:10.1002/sim.6847 | The sample-size formula is solved for the **number of participants**, with many decision points per person. It does not apply directly to N = 1. For one account, use the per-post formula in §4 and treat inference as about this account's own effect over the study window (finite population over time). | M |
| 3 | Qian, Walton, Collins, Klasnja, Lanza, Nahum-Shani, Rabbi, Russell, Walton, Yoo, Murphy (2022). The microrandomized trial for developing digital interventions: experimental design and data analysis considerations. *Psychological Methods* 27(5):874–894. doi:10.1037/met0000283 | A practical MRT checklist: choosing decision points and randomization probabilities, proximal vs distal outcomes, lagged and moderated effects, availability. | H |
| 4 | Boruvka, Almirall, Witkiewitz, Murphy (2018). Assessing time-varying causal effect moderation in mobile health. *JASA* 113(523):1112–1121. doi:10.1080/01621459.2017.1305274 | Centred and weighted least squares for proximal and **lagged** effects and their moderators. It stays valid when randomization probabilities differ from 0.5 or vary over time, so it covers bandit-assigned posts with logged propensities. | H |
| 5 | Bojinov, Shephard (2019). Time series experiments and causal estimands: exact randomization tests and trading. *JASA* 114(528):1665–1682. doi:10.1080/01621459.2018.1527225 | The closest formal match to our setting: **one unit randomized repeatedly over time**. Estimands allow carryover, and exact randomization tests use only the known assignment mechanism. | H |
| 6 | Bojinov, Rambachan, Shephard (2021). Panel experiments and dynamic causal effects: a finite population perspective. *Quantitative Economics* 12(4):1171–1196. doi:10.3982/QE1744 | The extension to many units. Use it when the platform pools experiments across customer accounts. | M |
| 7 | Duan, Kravitz, Schmid (2013). Single-patient (n-of-1) trials: a pragmatic clinical decision methodology for patient-centered comparative effectiveness research. *J Clin Epidemiol* 66(8 Suppl):S21–S28. doi:10.1016/j.jclinepi.2013.04.006 | The n-of-1 framing: multiple crossovers, washout, and Bayesian aggregation of many n-of-1 trials into a population estimate (a future cross-account model). | M |
| 8 | Kratochwill, Hitchcock, Horner, Levin, Odom, Rindskopf, Shadish (2013). Single-case intervention research design standards. *Remedial and Special Education* 34(1):26–38. doi:10.1177/0741932512452794 | Single-case design (SCED) standards: at least 3 demonstrations of effect, with a minimum number of points per phase. A plain AB phase design (one switch, e.g. "turn on Premium") is weak evidence. | M |
| 9 | Shadish, Kyse, Rindskopf (2013). Analyzing data from single-case designs using multilevel models. *Psychological Methods* 18(3):385–405. doi:10.1037/a0032964 | Multilevel models for single-case data. Supports partial pooling across experiments and accounts. | L |
| 10 | Onghena, Edgington (2005). Customization of pain treatments: single-case design and analysis. *Clin J Pain* 21(1):56–68. doi:10.1097/00002508-200501000-00007 | Randomization tests for single-case designs, including a **randomized intervention start point** in AB designs. We use it for account-level levers: randomize the Premium/strategy start date inside a pre-declared window to get an exact test. | M |

### 1b. Switchback, carryover and interference
| # | Source | What it gives us | Rel. |
|---|---|---|---|
| 11 | Bojinov, Simchi-Levi, Zhao (2023). Design and analysis of switchback experiments. *Management Science* 69(7):3759–3777. doi:10.1287/mnsc.2022.4583 (arXiv 2009.00148) | Theorem 2: with carryover of order m and T = nm, the minimax-optimal design randomizes at {1, 2m+1, 3m+1, …, (n−2)m+1}. That is a fair coin every m periods, with the first and last epochs 2m long. The Horvitz–Thompson estimator uses periods whose last m+1 assignments are all equal. §4.3: **overestimating m keeps inference valid, while underestimating m biases it**, so err long. | H |
| 12 | Hu, Wager (2022). Switchback experiments under geometric mixing. arXiv 2209.00197 | For carryover that decays geometrically rather than ending at a fixed lag: discard a burn-in after each switch, and block length trades bias against variance. | M |
| 13 | Eckles, Karrer, Ugander (2017). Design and analysis of experiments in networks: reducing bias from interference. *J Causal Inference* 5(1). doi:10.1515/jci-2015-0021 | Interference between units through a network. Our interference is between an account's own posts over time, so this is background only. | L |

### 1c. Variance reduction and randomization inference
| # | Source | What it gives us | Rel. |
|---|---|---|---|
| 14 | Deng, Xu, Kohavi, Walker (2013). Improving the sensitivity of online controlled experiments by utilizing pre-experiment data (CUPED). *WSDM '13*, 123–132. doi:10.1145/2433396.2433413 | Use a pre-treatment covariate X with an **estimated** coefficient θ = Cov(Y,X)/Var(X). Variance falls by a factor (1−ρ²). The covariate must not be affected by treatment. | H |
| 15 | Lin (2013). Agnostic notes on regression adjustments to experimental data: reexamining Freedman's critique. *Ann Appl Stat* 7(1):295–318. doi:10.1214/12-AOAS583 | OLS with treatment × centred-covariate interactions and HC standard errors cannot hurt asymptotic precision and is consistent under randomization alone. | H |
| 16 | Rosenbaum (2002). Covariance adjustment in randomized experiments and observational studies. *Statistical Science* 17(3):286–327. doi:10.1214/ss/1042727942 | Randomization inference with covariates: residualize the outcome on covariates, then permute. That is our randomization-test check (§3.2, simulation B). | H |
| 17 | Kohavi, Tang, Xu (2020). *Trustworthy Online Controlled Experiments*. Cambridge UP. doi:10.1017/9781108653985 | Operational practice: sample-ratio-mismatch checks, guardrail metrics, capping heavy tails, novelty and primacy effects, A/A tests. | M |
| 18 | Kohavi, Deng, Longbotham, Xu (2014). Seven rules of thumb for web site experimenters. *KDD '14*, 1857–1866. doi:10.1145/2623330.2623341 | Heavy-tailed metrics need capping or transformation; most ideas fail; be wary of surprising large wins. | M |

### 1d. Sequential and always-valid inference, e-values
| # | Source | What it gives us | Rel. |
|---|---|---|---|
| 19 | Johari, Koomen, Pekelis, Walsh (2017). Peeking at A/B tests: why it matters, and what to do about it. *KDD '17*, 1517–1525. doi:10.1145/3097983.3097992 | Repeated significance testing inflates false positives. The mixture SPRT (mSPRT) gives always-valid p-values. | H |
| 20 | Johari, Koomen, Pekelis, Walsh (2022). Always valid inference: continuous monitoring of A/B tests. *Operations Research* 70(3):1806–1821. doi:10.1287/opre.2021.2135 | The journal version: always-valid p-values and CIs, plus guidance on choosing the mixing distribution. | H |
| 21 | Howard, Ramdas, McAuliffe, Sekhon (2021). Time-uniform, nonparametric, nonasymptotic confidence sequences. *Ann Stat* 49(2):1055–1080. doi:10.1214/20-AOS1991 | Confidence sequences valid at all stopping times. We use them for guardrails that are monitored continuously. | M |
| 22 | Waudby-Smith, Arbour, Sinha, Kennedy, Ramdas (2021). Time-uniform central limit theory and asymptotic confidence sequences. arXiv 2103.06476 | Asymptotic confidence sequences that fit regression-adjusted (CUPED/Lin) estimators. | M |
| 23 | Ramdas, Grünwald, Vovk, Shafer (2023). Game-theoretic statistics and safe anytime-valid inference. *Statistical Science* 38(4):576–601. doi:10.1214/23-STS894 | The SAVI framework: e-processes, test martingales, optional stopping and optional continuation. | M |
| 24 | Grünwald, de Heide, Koolen (2024). Safe testing. *JRSS-B* 86(5):1091–1128. doi:10.1093/jrsssb/qkae011 | E-values allow optional continuation: an experiment can be extended, or a replication multiplied in, without losing validity. | M |
| 25 | Vovk, Wang (2021). E-values: calibration, combination and applications. *Ann Stat* 49(3):1736–1754. doi:10.1214/20-AOS2020 | Converting between p and e (calibrators). Averaging e-values is valid under arbitrary dependence. | M |

### 1e. Multiplicity across the stream of agent-proposed experiments
| # | Source | What it gives us | Rel. |
|---|---|---|---|
| 26 | Javanmard, Montanari (2018). Online rules for control of false discovery rate and false discovery exceedance. *Ann Stat* 46(2):526–554. doi:10.1214/17-AOS1559 | LORD/LOND generalized alpha-investing. **Theorem 3.1 proves FDR control assuming independent p-values.** Under general dependence §3.2 gives only an upper bound. Our experiments share an account and overlapping posts, so independence is doubtful. | M |
| 27 | Ramdas, Yang, Wainwright, Jordan (2017). Online control of the false discovery rate with decaying memory. arXiv 1710.00499 (NeurIPS 2017) | Decaying memory avoids "alpha-death" and stale wealth in a long-running test stream. | L |
| 28 | Zrnic, Ramdas, Jordan (2018). Asynchronous online testing of multiple hypotheses. arXiv 1812.05068 | Online FDR when tests overlap in time and are locally dependent: our case of concurrent experiments. | M |
| 29 | Xu, Ramdas (2023). Online multiple testing with e-values. arXiv 2311.06412 | e-LOND: online FDR control with e-values under **arbitrary dependence**. This is the recommended default. (Venue not checked through an API.) | H |
| 30 | Wang, Ramdas (2022). False discovery rate control with e-values. *JRSS-B* 84(3):822–852. doi:10.1111/rssb.12489 | e-BH for batch readouts (e.g. a quarterly review of all concluded experiments) under arbitrary dependence. | M |

### 1f. Bayesian decision rules, bandits, inference after adaptivity, off-policy evaluation
| # | Source | What it gives us | Rel. |
|---|---|---|---|
| 31 | Deng (2015). Objective Bayesian two sample hypothesis testing for online controlled experiments. *WWW '15 Companion*, 923–928. doi:10.1145/2740908.2742563 | Bayesian A/B testing with priors learned from past experiments: an empirical-Bayes prior for our lever families. | M |
| 32 | Deng, Lu, Chen (2016). Continuous monitoring of A/B tests without pain: optional stopping in Bayesian testing. *IEEE DSAA 2016*. doi:10.1109/DSAA.2016.33 (arXiv 1602.05549) | Bayesian posteriors stay calibrated under optional stopping **if the prior is right**. Our simulation D shows that with a flat prior, weekly P(lift>1) > 0.95 rejects a true null 16% of the time. So use a skeptical prior. | M |
| 33 | Scott (2015). Multi-armed bandit experiments in the online service economy. *Applied Stochastic Models in Business and Industry* 31(1):37–45. doi:10.1002/asmb.2104 | Thompson sampling in production, with a "potential value remaining" stopping rule. This is the peer-reviewed reference for an expected-loss style stop. | M |
| 34 | Thompson (1933). On the likelihood that one unknown probability exceeds another in view of the evidence of two samples. *Biometrika* 25(3–4):285–294. doi:10.1093/biomet/25.3-4.285 | The origin of Thompson sampling. | L |
| 35 | Russo, Van Roy, Kazerouni, Osband, Wen (2018). A tutorial on Thompson sampling. *Foundations and Trends in ML* 11(1):1–96. doi:10.1561/2200000070 | Practical TS: priors, batching, non-stationarity, and when TS is the wrong tool (e.g. pure information gathering). | H |
| 36 | Hadad, Hirshberg, Zhan, Wager, Athey (2021). Confidence intervals for policy evaluation in adaptive experiments. *PNAS* 118(15):e2014602118. doi:10.1073/pnas.2014602118 | Adaptively weighted AIPW. The "constant allocation" weights h_t = √(e_t/T) (Eq. 15) restore asymptotic normality after bandit allocation, provided propensities do not decay too fast. That supports a propensity floor. | H |
| 37 | Zhang, Janson, Murphy (2020). Inference for batched bandits. arXiv 2002.03217 (NeurIPS 2020) | Under bandit allocation, OLS can be **non-normal** when arms are nearly tied. Batched OLS (estimate within each batch, then combine) restores valid inference. With daily batches, compute per-day contrasts. | H |
| 38 | Kasy, Sautmann (2021). Adaptive treatment assignment in experiments for policy choice. *Econometrica* 89(1):113–132. doi:10.3982/ECTA17527 | Exploration sampling: assign with q_k ∝ p_k(1−p_k), where p_k is the posterior probability that arm k is best. This suits "find the best format to adopt" better than maximizing in-experiment reward. **Caveat:** Ariu, Kato, Komiyama, McAlinn, Qin (arXiv 2109.08229) show Theorem 1(2) is incorrect and Theorem 1(3) is false. Treat it as a heuristic without the claimed optimality. | M |
| 39 | Bibaut, Chambaz, Dimakopoulou, Kallus, van der Laan (2021). Post-contextual-bandit inference. arXiv 2106.00418 | Inference after contextual bandits (stabilized DR estimators). For when allocation uses context such as slot or topic. | L |
| 40 | Dudík, Langford, Li (2011). Doubly robust policy evaluation and learning. arXiv 1103.4601 (ICML 2011) | DR off-policy evaluation from logged propensities. Lets the platform score an agent's proposed *new* allocation policy on past randomized posts before running it. | H |
| 41 | Li, Chu, Langford, Wang (2011). Unbiased offline evaluation of contextual-bandit-based news article recommendation algorithms. *WSDM '11*, 297–306. doi:10.1145/1935826.1935878 | Replay evaluation on uniformly randomized logs. The simplest OPE, using the uniform-arm data from fixed-allocation experiments. | M |

### 1g. Heavy tails and non-randomizable account-level levers
| # | Source | What it gives us | Rel. |
|---|---|---|---|
| 42 | Chen, Roth (2024). Logs with zeros? Some problems and solutions. *QJE* 139(2):891–936. doi:10.1093/qje/qjad054 | With zeros, an ATE on log(1+y) is not a percentage and depends on units. Impressions are rarely zero, so logs are fine. For replies, follows, profile clicks and negative feedback (many zeros), use a ratio of means (Poisson QMLE) or report the extensive margin separately. | H |
| 43 | Firpo (2007). Efficient semiparametric estimation of quantile treatment effects. *Econometrica* 75(1):259–276. doi:10.1111/j.1468-0262.2007.00738.x | Quantile treatment effects for the "does it raise the breakout tail?" question (e.g. τ at q = 0.9). | M |
| 44 | Abadie, Diamond, Hainmueller (2010). Synthetic control methods for comparative case studies. *JASA* 105(490):493–505. doi:10.1198/jasa.2009.ap08746 | A synthetic control built from a donor pool of peer accounts, with placebo (permutation) inference across donors. | M |
| 45 | Abadie (2021). Using synthetic controls: feasibility, data requirements, and methodological aspects. *JEL* 59(2):391–425. doi:10.1257/jel.20191450 | Feasibility conditions: a long pre-period with good fit, no spillover onto donors, and donors free of their own shocks. A fast-growing new account often fails these. | M |
| 46 | Brodersen, Gallusser, Koehler, Remy, Scott (2015). Inferring causal impact using Bayesian structural time-series models. *Ann Appl Stat* 9(1):247–274. doi:10.1214/14-AOAS788 | CausalImpact (BSTS): local trend, seasonality and spike-and-slab regression on control series give a posterior of the post-period counterfactual. The default for Premium, follow strategy and reply strategy. | H |
| 47 | Lopez Bernal, Cummins, Gasparrini (2017; online 2016). Interrupted time series regression for the evaluation of public health interventions: a tutorial. *Int J Epidemiol* 46(1):348–355. doi:10.1093/ije/dyw098 | Segmented regression (level and slope change), seasonality, autocorrelation, and a pre-specified impact model. Crossref has the online 2016 record; the issue details come from the journal. | M |

Total: 47 sources. Sources marked H are the core set. All DOIs and arXiv IDs listed resolved to the stated title and authors.

## 2. Corrections to earlier work (REVIEW.md / plan)
1. **"Frozen forecast as an offset (CUPED-style)" mixes up two different things.** An offset fixes the forecast coefficient at 1. CUPED (Deng et al. 2013) and Lin (2013) **estimate** it. Var(y − x) < Var(y) only when the slope of y on x exceeds 0.5. A miscalibrated or over-dispersed forecast used as an offset can therefore *add* variance. Fix: include log-forecast as a covariate with an estimated slope (Bayesian prior β ~ N(1, 0.25²), so "offset" is the prior mean), plus a treatment × centred-covariate interaction (Lin).
2. **The covariate must be blind to treatment.** If the pre-publication forecast takes the randomized lever as an input (format, hook, link, hashtag), it is a post-treatment variable and absorbs the effect. Freeze a *treatment-blind* forecast (slot, day, follower count, recent baseline, topic-plan prior) **before** randomization, and log a hash of it.
3. **Reviewer's "≈63% at 1.5×, 98% at 2×" (3/day, 4 weeks, per-post) reproduces only at covariate R² ≈ 0.47.** That is the top of the 30–50% variance cut the reviewer cites. Analytic power at R² = 0.47 is 64%/98%. At R² = 0.3 it is 52%/93% (simulated 50%/92%); with no covariate, 39%/82% (simulated 38%/80%). Report it as a range (≈50–65% at 1.5×, 92–98% at 2×), not a point.
4. **Switchback blocks of 2–3 days for Premium are not appropriate.** Premium is billed by subscription, is hard to toggle every few days, and works partly through followers gained, which carry over indefinitely. That breaks the bounded-carryover assumption in Bojinov, Simchi-Levi & Zhao. Premium and follow/reply strategy belong under account-level ITS/BSTS with a randomized start date. Switchbacks suit timing windows and posting frequency, with outcomes measured per post or per day (impressions), **not** follower stock.
5. **Liao et al. (2016) sample sizes are for numbers of participants.** They cannot size an N = 1 study. Use the per-post formula in §4.
6. **Javanmard & Montanari (2018) LORD/LOND carry the FDR guarantee only under independent p-values.** Concurrent experiments on one account are dependent (shared days, shared followers, factorial posts). Use e-LOND (Xu & Ramdas) or e-BH (Wang & Ramdas 2022), which hold under arbitrary dependence.
7. **Kasy & Sautmann (2021): do not cite the asymptotic optimality guarantee.** Ariu et al. (arXiv 2109.08229) show parts of Theorem 1 are incorrect or false. Exploration sampling remains a reasonable heuristic.
8. **Thompson sampling with a floor of 0.2 is supported, but it is not free.** Simulation C (3/day, 4 weeks, 1.5×): 50/50 allocation gives 51% power. Batched TS puts 71% of posts on the better arm but gives 36–44% power, depending on the estimator. Valid inference needs logged propensities and AIPW / adaptively weighted / batched estimators (Hadad et al.; Zhang et al.).
9. **Discreteness (confirms REVIEW.md):** 6 pairs gives a minimum two-sided p of 2/64 = 0.031. With per-post randomization of 84 posts there are about 10²² assignments, so a Monte Carlo randomization test with 999 draws is effectively continuous. Simulation B: size 0.051, and the decision agrees with the ANCOVA t-test 97–99% of the time.
10. Minor: Lopez Bernal et al. is the 2017 print issue of IJE 46(1), first online in 2016. Cite it as 2017.

## 3. Experiment framework spec

### 3.1 Design catalogue by lever type
| Lever class | Examples | Design | Unit / outcome | Defaults |
|---|---|---|---|---|
| **Per-post randomizable** | format (demo video / image / text / thread), hook style, 0 vs 1 hashtag, link in post vs first reply, topic within the approved plan, CTA | **Micro-randomized**, blocked by day: in each day's k posts, ⌊k/2⌋ or ⌈k/2⌉ treated (coin flip). At 1 post/day, block by 2-day pairs. Several independent factors can be randomized **factorially** on the same post (≤3 concurrent factors), so each factor gets the full sample. | post; primary = log Home impressions at 48 h (the For You cutoff), secondary per Phoenix action type | π = 0.5 fixed for confirmatory tests. Bandit mode only after a confirmatory read or for low-stakes optimization: π ∈ [0.2, 0.8], batched daily, propensities logged. |
| **Time-level** | posting frequency (2 vs 4/day), timing windows (morning vs evening), spacing | **Switchback** on daily periods. Carryover order m = 2 days is a *lower* bound: the 48 h For You cutoff ends a post's own distribution, and repeat-author decay (×0.5, floor 0.25) couples posts inside that window. Use blocks of **4 days with the first 2 days as washout** (excluded from the estimate). That is equivalent to the Bojinov, Simchi-Levi & Zhao lag-2 estimator, with m deliberately overestimated, which their §4.3 shows is safe. | day; daily log mean impressions/post, profile visits, new follows (flow, not stock) | ≥12 weeks for 1.5× (simulation E). Randomization test over block assignments. |
| **Account-level, not reversible** | Premium, reply strategy, follow strategy, bio/pinned changes | **ITS/BSTS (CausalImpact)** on daily series, with peer-panel controls (public per-post metrics of matched accounts) plus own unaffected series. **Randomize the start date** within a declared window (Onghena & Edgington) for an exact test. ABAB reversal only when the lever is cheap to toggle and carryover is short (Kratochwill: ≥3 demonstrations). | day; impressions/post, follows/day, profile visits | Pre-period ≥8 weeks; post-period ≥4 weeks; frozen pre-registration of model and controls. Treat as *evidence*, not confirmation. |

**Why 48 h washout:** a post stays eligible for For You for 48 h. Its cold-start boost applies only while it is ≤2 h old, and author-diversity decay penalizes the account's other candidates in the same request. So a treatment applied on day d moves outcomes on days d and d+1 (through competition with, and attention from, the account's own posts), and moves them only weakly after day d+2. Follower gains carry over without limit, so follower **stock** is never a switchback outcome. Use per-day follow **flows** and accept bias, or use ITS.

### 3.2 Analysis
**Primary model (per-post levers), fitted per experiment family:**

    log y_i = α_slot(i) + β·log f_i + Σ_k τ_k·A_ik + γ_k·A_ik·(log f_i − mean) + λ·A_{i−1,k} + u_day(i) + ε_i
    ε_i ~ Student-t(ν, 0, σ),  ν ~ Gamma(2, 0.1),  σ ~ Half-Normal(1.5)
    β ~ N(1, 0.25²)              (frozen, treatment-blind forecast; "offset" is the prior mean, not a constraint)
    u_day ~ N(0, σ_day²)         (day shocks; within-day blocking makes τ orthogonal to them)
    τ_k ~ N(μ_family, s_family²) (partial pooling across experiments in the same lever family = multiplicity shrinkage)
    μ_family ~ N(0, 0.2²)        (skeptical: 95% prior mass on 0.68×–1.48×)
    λ: lag-1 (carryover/interference) effect of the previous post's assignment (Boruvka et al. 2018)

- Count outcomes with many zeros (replies, follows, profile clicks, negative feedback): negative binomial with log link and log(impressions) offset. Report the ratio of means, not log(1+y) (Chen & Roth 2024).
- Breakout question: report the quantile treatment effect at q = 0.9 (Firpo 2007) as secondary.
- **Randomization-test check** (always run, reported next to the posterior): residualize log y on log f, take the arm-difference statistic, and re-draw 9,999 assignments from the *same* blocked design (Rosenbaum 2002; Bojinov & Shephard 2019). If the posterior and the randomization p disagree in direction or at the decision boundary, flag for human review.
- Bandit-assigned data: use AIPW with logged propensities and adaptive weights h_t = √e_t (Hadad et al.), or batched per-day contrasts (Zhang, Janson & Murphy). Never use a plain difference in means without saying so.
- Health checks before any readout: sample-ratio mismatch (χ² on assignment counts vs logged π), capture completeness (posts missing a 48 h measurement), forecast frozen before assignment (timestamp/hash), and no treatment-dependent posting delays.

### 3.3 Decision rule (defaults)
Let θ = log lift (treatment vs control) on the primary metric. δ = practical threshold, default **log 1.10**.
- **Adopt** if P(θ > δ) ≥ **0.80** AND expected loss E[max(0, −θ)] < **0.01** (≈1% of impressions).
- **Reject / stop** if P(θ < 0) ≥ 0.90, or at the horizon when P(θ > δ) < 0.20.
- **Near threshold** if the 80% credible interval straddles δ at the horizon. Offer "extend once by 50%" (allowed because the decision is Bayesian and the guardrail e-process stays valid under continuation) or "stop".
- **Horizon:** 4 weeks at 3/day (84 posts) by default. 8 weeks if the hypothesized effect is <1.5×.
- Prior s0 = 0.2 on log lift. Simulation F, weekly looks, 4-week horizon: false adoption at a true null 2.3%; adopting a harmful 0.8× 0.1%; P(adopt) 18% at 1.25×, 48% at 1.5×, 91% at 2×. At 8 weeks: 4.9% / 0.2% / 41% / 84% / 99.8%. A looser s0 = 0.35 raises power (73% at 1.5×, 4 weeks) but false adoption rises to 10%.

### 3.4 Sequential monitoring
- **Efficacy:** look weekly at the posterior under the rule above. Do **not** use a frequentist "p < 0.05 at any look". Simulation D: naive weekly peeking over 8 weeks gives a false-positive rate of 17.4%; flat-prior Bayes P > 0.95 gives 15.8%.
- **If a frequentist guarantee is required** (e.g. a public claim): use an mSPRT / normal-mixture e-process with mixing sd = log 1.5 and α = 0.05, which is valid at every look. Simulation D: realized type I 0.7% with weekly looks, but power drops from 81% (fixed 8 weeks) to 48% at 1.5×. Use it for claims, not for routine product decisions.
- **Guardrails, monitored daily, always-valid:** one-sided confidence sequence (Howard et al. 2021) or mSPRT for harm on (a) negative feedback rate per 1k impressions (show-less / mute / block / report proxies where exposed), (b) unfollows per day / net follower churn, (c) reply-sentiment flags, (d) impressions per post falling below 0.5× forecast for 3 consecutive treated posts. Crossing a guardrail **pauses** the arm automatically and notifies the owner.

### 3.5 Bandit allocation
- Use it only for per-post levers whose confirmatory read is done or whose stakes are low. Batch daily. Thompson draw on the family posterior. Clip π to [0.2, 0.8] (2 arms), or to ≥ 0.1 per arm (≤5 arms).
- Log per post: experiment id, arm, π (all arms), the RNG seed or draw, posterior snapshot id, forecast hash, posting time.
- To pick a winner to adopt (policy choice) rather than maximize in-experiment reward, use exploration sampling q ∝ p(1−p) (Kasy & Sautmann), as a heuristic.
- Before deploying an agent-proposed allocation policy, score it off-policy with DR (Dudík et al. 2011) on logged randomized data. Deploy only if its DR lower 80% bound beats current policy.

### 3.6 Interference handling
- Space posts by ≥ **3 h** (beyond the 2 h cold-start window) and keep posting times fixed per slot across arms, so the arm is not confounded with competition from the account's own posts.
- Record for each post: the number of the account's own posts published in the previous 48 h, and the previous post's arm. Include A_{i−1} (λ) in the model. A material λ (|λ| > 0.5|τ|) signals carryover: switch that lever to day-level randomization.
- Do not run a frequency experiment at the same time as a per-post experiment whose outcome is impressions, unless the per-post design is blocked within frequency condition.
- Out-of-network replies are filtered from For You, so reply-strategy outcomes must be measured on profile visits and follows, not reply impressions.

### 3.7 Multiplicity across the agent stream
- Each concluded confirmatory experiment emits an e-value: the mSPRT mixture likelihood ratio at the stopping time, or the calibrator e = κ·p^(κ−1) with κ = 0.5, i.e. e = 1/(2√p) (Vovk & Wang 2021). The platform runs **e-LOND** at FDR 0.10 over the stream (Xu & Ramdas 2023). Only e-LOND rejections may be labelled "confirmed" in the conclusions library.
- Estimates shown to agents and owners are the **partially pooled** τ_k (hierarchical shrinkage), not raw per-experiment estimates.
- Quarterly: e-BH over all concluded experiments (Wang & Ramdas 2022) as an audit.
- Exploratory or observational "insights" never enter the FDR stream and are labelled "hypothesis".

### 3.8 Agent permissions
| Agents may auto-propose **and auto-start** (within guardrails) | Needs owner approval |
|---|---|
| Per-post levers inside the approved content plan and brand voice: format, hook style, hashtag 0/1, link in post vs reply, CTA wording, topic ordering within the plan | Anything that costs money (Premium, ads, tools) |
| Factorial combos of ≤3 such factors, π fixed 0.5, horizon ≤4 weeks, with standard guardrails | Account-level changes: reply strategy, follow/unfollow strategy, bio or pinned post, cadence change > ±1 post/day |
| Switching an already-confirmed lever into bandit mode (floor 0.2) | Any experiment touching other accounts (outreach replies, DMs, follows), which also needs a check against X automation rules |
| Extending a near-threshold experiment once by ≤50% | Horizon > 8 weeks; > 3 concurrent experiments; changing a frozen primary metric, model or δ after start |
| Off-policy evaluation of a proposed policy | Overriding a guardrail pause; deleting or excluding posts from analysis; any public claim of a "confirmed" result |

## 4. Power and sample size

### 4.1 Calculator formulas (implement in the platform)
Per-post randomization, 2 arms, π = 0.5, n posts, log-scale residual sd σ (default 1.1), covariate R² (from the frozen forecast's back-test on the account's or peers' past posts):

    SE(τ̂) ≈ σ·√(1−R²)·√(1/(nπ(1−π)))           = 2σ√(1−R²)/√n at π = 0.5
    power(lift L) ≈ Φ( |log L| / SE − z_{1−α/2} )
    n required    = (z_{1−α/2} + z_{power})² · σ²(1−R²) / (π(1−π)·(log L)²)
    MDE (×)       = exp( (z_{1−α/2} + z_{power}) · SE )
    Factorial with K independent ±factors: the same n serves each factor (no interaction assumed).
    Bandit with π_t: replace 1/(π(1−π)) by mean_t[1/(π_t(1−π_t))]. At floor 0.2 this can rise by up to 1.56×.
    Day shocks with ICC ρ and within-day blocking + day FE: σ² → σ²(1−ρ). At 1 post/day, use 2-day-pair blocking.
    Switchback (block length b days, washout w days, AR(1) φ, daily sd σ_d, T days):
      usable days ≈ T(b−w)/b,  SE ≈ 2σ_d√((1+φ)/(1−φ))/√(usable days), inflated further by the between-block variance of the coin flips. Use the simulator in scripts/sim_other.py.
    ITS level shift (n_pre, n_post days, AR(1) φ, residual sd σ_d·√(1−R²_peers)):
      SE ≈ σ_d√(1−R²)·√(1/n_pre + 1/n_post)·√((1+φ)/(1−φ))

The analytic formula matches the simulation within ±0.03 power (A2 vs A1).

### 4.2 Simulation A1: per-post randomization power (two-sided α = 0.05; ANCOVA with Lin interaction, HC2 SE; 4,000 reps/cell; Normal log residuals σ = 1.1; within-day blocking)
| posts/day | weeks | n | R2 | lift 1.25 | lift 1.5 | lift 2.0 | MDE@80% | one-sided 5% (Bayes P(lift>1)>0.95) at 1.25/1.5/2.0 |
|---|---|---|---|---|---|---|---|---|
| 1 | 2 | 14 | 0.0 | 0.07 | 0.09 | 0.20 | 5.19x | 0.11/0.16/0.30 |
| 1 | 2 | 14 | 0.3 | 0.07 | 0.11 | 0.24 | 3.97x | 0.12/0.17/0.37 |
| 1 | 2 | 14 | 0.5 | 0.07 | 0.14 | 0.31 | 3.21x | 0.12/0.22/0.44 |
| 1 | 4 | 28 | 0.0 | 0.08 | 0.15 | 0.37 | 3.21x | 0.13/0.25/0.49 |
| 1 | 4 | 28 | 0.3 | 0.09 | 0.20 | 0.46 | 2.65x | 0.15/0.30/0.58 |
| 1 | 4 | 28 | 0.5 | 0.12 | 0.26 | 0.61 | 2.28x | 0.19/0.38/0.72 |
| 1 | 8 | 56 | 0.0 | 0.10 | 0.27 | 0.64 | 2.28x | 0.18/0.39/0.75 |
| 1 | 8 | 56 | 0.3 | 0.15 | 0.37 | 0.79 | 1.99x | 0.23/0.49/0.87 |
| 1 | 8 | 56 | 0.5 | 0.17 | 0.48 | 0.90 | 1.79x | 0.27/0.60/0.94 |
| 2 | 2 | 28 | 0.0 | 0.07 | 0.16 | 0.37 | 3.21x | 0.13/0.25/0.50 |
| 2 | 2 | 28 | 0.3 | 0.10 | 0.19 | 0.46 | 2.65x | 0.16/0.29/0.61 |
| 2 | 2 | 28 | 0.5 | 0.10 | 0.26 | 0.60 | 2.28x | 0.17/0.38/0.71 |
| 2 | 4 | 56 | 0.0 | 0.12 | 0.27 | 0.63 | 2.28x | 0.18/0.38/0.75 |
| 2 | 4 | 56 | 0.3 | 0.15 | 0.37 | 0.76 | 1.99x | 0.23/0.49/0.85 |
| 2 | 4 | 56 | 0.5 | 0.19 | 0.49 | 0.90 | 1.79x | 0.28/0.62/0.95 |
| 2 | 8 | 112 | 0.0 | 0.19 | 0.48 | 0.91 | 1.79x | 0.29/0.60/0.95 |
| 2 | 8 | 112 | 0.3 | 0.25 | 0.63 | 0.98 | 1.63x | 0.35/0.74/0.99 |
| 2 | 8 | 112 | 0.5 | 0.30 | 0.77 | 1.00 | 1.51x | 0.44/0.86/1.00 |
| 3 | 2 | 42 | 0.0 | 0.09 | 0.21 | 0.50 | 2.59x | 0.14/0.32/0.63 |
| 3 | 2 | 42 | 0.3 | 0.11 | 0.28 | 0.65 | 2.22x | 0.17/0.40/0.75 |
| 3 | 2 | 42 | 0.5 | 0.15 | 0.36 | 0.80 | 1.96x | 0.24/0.49/0.88 |
| 3 | 4 | 84 | 0.0 | 0.14 | 0.38 | 0.80 | 1.96x | 0.23/0.51/0.88 |
| 3 | 4 | 84 | 0.3 | 0.21 | 0.50 | 0.92 | 1.76x | 0.30/0.63/0.96 |
| 3 | 4 | 84 | 0.5 | 0.25 | 0.65 | 0.98 | 1.61x | 0.37/0.77/0.99 |
| 3 | 8 | 168 | 0.0 | 0.26 | 0.66 | 0.98 | 1.61x | 0.37/0.77/0.99 |
| 3 | 8 | 168 | 0.3 | 0.35 | 0.81 | 1.00 | 1.49x | 0.47/0.88/1.00 |
| 3 | 8 | 168 | 0.5 | 0.46 | 0.92 | 1.00 | 1.40x | 0.59/0.96/1.00 |

Reading: the last column is the power of a one-sided 5% test, which equals the Bayes rule "P(lift > 1) > 0.95, flat prior, single look".

### 4.3 Simulation A3: sensitivities (3/day × 4 weeks, R² = 0.3, two-sided 5%)
- Normal (baseline): size 0.051; power 0.20 / 0.51 / 0.92 at 1.25× / 1.5× / 2×.
- t₃ log residuals (heavier tails, same variance): size 0.045; power 0.22 / 0.58 / 0.93. OLS on logs stays valid. A robust t-likelihood would gain more.
- Day shocks, ICC 0.3, no day FE: size 0.028 (conservative); power 0.17 / 0.51 / 0.95.
- Day shocks, ICC 0.3, **day FE** (within-day blocking pays off): size ≈0.05 (re-checked with 2×8,000 reps: 0.049–0.055); power 0.22 / 0.61 / 0.97.
- 1 post/day, ICC 0.3, 2-day-pair FE: size 0.051; power 0.11 / 0.24 / 0.55 (no FE: 0.06 / 0.16 / 0.46).

### 4.4 Simulation B: randomization test vs parametric (3/day × 4 weeks, R² = 0.3, 1,500 reps × 999 re-randomizations)
- lift 1.0: randomization-test rejection=0.051, ANCOVA t-test=0.055, agreement=0.988
- lift 1.25: randomization-test rejection=0.195, ANCOVA t-test=0.198, agreement=0.984
- lift 1.5: randomization-test rejection=0.493, ANCOVA t-test=0.504, agreement=0.971
- lift 2.0: randomization-test rejection=0.914, ANCOVA t-test=0.916, agreement=0.985

### 4.5 Simulation C: batched Thompson sampling with propensity floor vs fixed allocation (3/day × 4 weeks, R² = 0.3, 4,000 reps)
| lift | design | P(post on better arm) | estimator | bias (log) | 95% CI coverage | power (2-sided 5%) |
|---|---|---|---|---|---|---|
| 1.0 | fixed | n/a | naive diff-in-means | +0.000 | 0.940 | 0.060 |
| 1.0 | fixed | n/a | AIPW (logged propensities) | -0.000 | 0.940 | 0.060 |
| 1.0 | fixed | n/a | adaptively-weighted AIPW (Hadad et al. sqrt-propensity) | -0.000 | 0.939 | 0.061 |
| 1.0 | TS | n/a | naive diff-in-means | +0.007 | 0.943 | 0.057 |
| 1.0 | TS | n/a | AIPW (logged propensities) | +0.004 | 0.942 | 0.058 |
| 1.0 | TS | n/a | adaptively-weighted AIPW (Hadad et al. sqrt-propensity) | +0.005 | 0.941 | 0.059 |
| 1.5 | fixed | 0.50 | naive diff-in-means | +0.004 | 0.945 | 0.523 |
| 1.5 | fixed | 0.50 | AIPW (logged propensities) | +0.004 | 0.945 | 0.508 |
| 1.5 | fixed | 0.50 | adaptively-weighted AIPW (Hadad et al. sqrt-propensity) | +0.004 | 0.942 | 0.511 |
| 1.5 | TS | 0.71 | naive diff-in-means | +0.019 | 0.939 | 0.439 |
| 1.5 | TS | 0.71 | AIPW (logged propensities) | +0.001 | 0.942 | 0.363 |
| 1.5 | TS | 0.71 | adaptively-weighted AIPW (Hadad et al. sqrt-propensity) | +0.007 | 0.941 | 0.393 |
| 2.0 | fixed | 0.50 | naive diff-in-means | -0.000 | 0.949 | 0.926 |
| 2.0 | fixed | 0.50 | AIPW (logged propensities) | +0.001 | 0.950 | 0.920 |
| 2.0 | fixed | 0.50 | adaptively-weighted AIPW (Hadad et al. sqrt-propensity) | +0.001 | 0.949 | 0.922 |
| 2.0 | TS | 0.76 | naive diff-in-means | +0.015 | 0.941 | 0.849 |
| 2.0 | TS | 0.76 | AIPW (logged propensities) | +0.006 | 0.938 | 0.780 |
| 2.0 | TS | 0.76 | adaptively-weighted AIPW (Hadad et al. sqrt-propensity) | +0.008 | 0.936 | 0.803 |
Reading: TS sends 71–76% of posts to the better arm, but confirmatory power falls (1.5×: 51% → 36–44%). All estimators keep ≈94% coverage at this horizon because the floor keeps propensities bounded. The naive difference in means shows the small positive adaptive-sampling bias (+0.015 to +0.019 log).

### 4.6 Simulation D: sequential monitoring (weekly looks up to 8 weeks, 3/day, R² = 0.3, 20,000 reps)
| lift | fixed 8-wk test | naive weekly peeking p<.05 | weekly Bayes P(lift>1)>0.95 (flat prior) | mSPRT (normal mixture, tau=log1.5, alpha=.05) | mSPRT mean stop week (if stopped) |
|---|---|---|---|---|---|
| 1.0 | 0.050 | 0.174 | 0.158 | 0.007 | 4.9 |
| 1.25 | 0.348 | 0.489 | 0.615 | 0.104 | 5.5 |
| 1.5 | 0.812 | 0.871 | 0.929 | 0.484 | 5.4 |
| 2.0 | 0.998 | 0.999 | 1.000 | 0.973 | 3.9 |

### 4.7 Simulation F: proposed Bayesian decision rule with weekly looks (3/day, R² = 0.3, 20,000 reps)
| s0 | horizon wk | P(adopt) lift 1.0 | 1.1 | 1.25 | 1.5 | 2.0 | P(adopt) at true 0.8x (harmful) |
|---|---|---|---|---|---|---|---|
| 0.2 | 4 | 0.023 | 0.063 | 0.177 | 0.484 | 0.913 | 0.001 |
| 0.2 | 8 | 0.049 | 0.148 | 0.410 | 0.838 | 0.998 | 0.002 |
| 0.35 | 4 | 0.103 | 0.196 | 0.399 | 0.729 | 0.976 | 0.014 |
| 0.35 | 8 | 0.138 | 0.301 | 0.593 | 0.924 | 1.000 | 0.016 |

### 4.8 Simulation E: switchback designs for time-level levers (daily periods; AR(1) φ = 0.5, sd 0.7 on daily log outcome [assumed]; true carryover 0.6/0.3/0.1 over days d, d+1, d+2; estimator = mean of days whose last 3 assignments agree [lag-2], minus same for control; exact randomization test; 2,000 reps × 499 re-draws)
| design | weeks | lift 1.0 (size) | 1.25 | 1.5 | 2.0 | naive all-days estimator bias at 1.5x (log; true 0.405) |
|---|---|---|---|---|---|---|
| BSZ m=2 | 8 | 0.041 | 0.07 | 0.14 | 0.36 | -0.093 |
| BSZ m=2 | 12 | 0.051 | 0.09 | 0.23 | 0.54 | -0.102 |
| BSZ m=2 | 16 | 0.042 | 0.11 | 0.29 | 0.68 | -0.100 |
| 4-day blocks | 8 | 0.050 | 0.08 | 0.20 | 0.46 | -0.042 |
| 4-day blocks | 12 | 0.044 | 0.12 | 0.29 | 0.65 | -0.057 |
| 4-day blocks | 16 | 0.048 | 0.14 | 0.37 | 0.78 | -0.049 |
| 7-day blocks | 8 | 0.043 | 0.07 | 0.13 | 0.32 | -0.039 |
| 7-day blocks | 12 | 0.044 | 0.10 | 0.24 | 0.55 | -0.031 |
| 7-day blocks | 16 | 0.048 | 0.13 | 0.34 | 0.77 | -0.030 |
Reading: a time-level lever needs about 12–16 weeks to detect 1.5×, and 8 weeks for 2× only at ~45%. Under positively autocorrelated daily noise, 4-day blocks (2 washout + 2 measured) beat the minimax design with m = 2 (randomize every 2 days). Minimax protects against worst-case outcomes, not AR(1) noise. Ignoring washout (all-days estimator) biases the effect toward zero by 7–25%.

### 4.9 Account-level ITS/BSTS: minimum detectable level shift (80% power, α = 0.05; daily sd 0.7, AR(1) φ = 0.5, 8-week pre-period)
| post-period | peer-panel R² = 0 | 0.5 | 0.8 |
|---|---|---|---|
| 4 weeks | 2.20× | 1.74× | 1.42× |
| 8 weeks | 1.90× | 1.57× | 1.33× |
These are optimistic. They assume a stationary pre-period. A growing account needs a trend term, which widens the bands, and secular platform changes cannot be separated from a single intervention unless the start date is randomized or the peer panel absorbs them.

## 5. What the numbers mean for the product
- At 3 posts/day, a per-post experiment detects **2× lifts in 2–4 weeks** and **1.5× lifts in 4–8 weeks** (needs a forecast with R² ≥ 0.3). It cannot realistically detect 1.25× (power ≤ 0.46 even at 8 weeks). Agents should only propose per-post experiments with a hypothesized effect ≥ 1.5× unless they accept 8+ weeks.
- The frozen forecast is worth about 1 week in 4 (R² 0.3) to 2 weeks in 4 (R² 0.5). Building a good treatment-blind forecast is the cheapest power gain.
- Run ≤3 factors factorially on the same posts to test more levers without longer horizons.
- Time-level levers (frequency, windows) take a quarter. Account-level levers give evidence, not proof. The UI should say so.
- Bandits are for exploiting confirmed winners, not for confirming them.

## 6. Reproduce
`venv/bin/python -I scripts/sim_mrt.py` (A1–A3), `scripts/sim_other.py rt|bandit|seq|switch` (B–E), `scripts/sim_rule.py` (F). Seeds fixed in each script. Raw outputs: `sim_mrt_out.md`, `sim_mrt_sens_fixed.md` (corrected FE sensitivities; the FE rows in `sim_mrt_out.md` used a wrong SE and are superseded), `sim_rt.md`, `sim_bandit.md`, `sim_seq.md`, `sim_switch.md`, `sim_rule.md`.
