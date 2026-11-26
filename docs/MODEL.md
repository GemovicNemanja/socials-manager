# Model v2: growth-modelling specification (X first, other platforms via parameters)

**Sources.** Notes are in `docs/research/`:
- [HM] `x-algorithm-home-mixer.md`
- [PX] `x-algorithm-phoenix.md`
- [GR] `x-algorithm-graph.md`
- [SF] `x-algorithm-safety.md`
- [HI] `x-algorithm-history.md`
- [POP] `popularity-prediction-literature.md`
- [FC] `forecasting-calibration-literature.md`
- [EXP] `experiment-design-literature.md`
- [TAPI] `twitterapi-io.md`
- [REV] `REVIEW.md`

**Markers.**
- **[J]** marks a judgement call.
- **[S-n]** marks a place where this spec replaces a REVIEW §3.2/3.3 proposal.

All X mechanics refer to `xai-org/x-algorithm` @52d4598 (param.rs synced 2026-11-24). The live production config is not public and may differ.

**Operating mode (v1): batch runs.** The platform does not run continuously. The owner (or their agent) starts a run every few days; each run collects everything new, then labels, forecasts, scores, refits and monitors in one pass. This spec is written for that mode. Counts are read at whatever age a post has reached at a run and mapped to canonical ages with a fitted maturity curve (§1.3). Forecasts are frozen when a post is planned, or rebuilt afterwards strictly from pre-post information (§1.3). Parts that need dense early snapshots (the two-phase nowcast, the 30m–2h breakout alarm, early like-rate features) are deferred until the platform is hosted, unless owner analytics buckets supply the early hours (§1.3).

---

## 0. How conflicts between the notes were resolved

| # | Topic | What the notes say | Decision | Why |
|---|---|---|---|---|
| C1 | **Do replies get For You reach?** | [HM]: replies from accounts the viewer doesn't follow are always dropped; replies from followed accounts get ×0.75. [GR]: Thunder keeps a followed account's reply only if the parent is an original still in Thunder, or it replies to a direct reply and the viewer follows the person replied to. `EnablePhoenixOonReplies` is false. | Replies get **zero out-of-network reach** (a structural zero). In-network reach is 0.75·ρ_th × the in-network term, with ρ_th ~ Beta(2,2) learned [J]. Reply exposure in conversation view and notifications sits in a separate reply-outcome block. Reply experiments are scored on profile clicks and follows. | The two notes agree; [GR] adds the Thunder conditions. With zero followers, a reply has about zero For You reach. |
| C2 | **Scope of the 0.75 out-of-network factor** | [HM]: applies to out-of-network posts **and** to in-network replies and reposts. [GR] and [HI] mention 0.75 without a scope. | Use [HM]'s scope. In-network originals and quotes get ×1 [J: treating quotes as originals here is inferred from the cold-start code]. | [HM] cites `scoring.rs`; the other notes don't contradict it. |
| C3 | **Cold-start eligibility** | [HM]: originals **including quotes**; ≤50k followers; ≤2h old; <200 Home views; must already have a Home view entry; not in the bottom 3% of the pool. [GR]: the post must already have been retrieved. [REV]: "original (not reply/repost)", silent on quotes. | Eligible types are {original, quote}. Eligibility is a chain: **retrieval → first Home view → Thompson draw**. Self-reply thread continuations are ineligible and never indexed [PX]. **[S-20]** | All three notes hold together once read as a chain. |
| C4 | **Do likes from outside Home count?** | [HM]: the sampler uses x = min(total likes, n), with likes from any surface. [HI]: "navigating directly… has no ranking impact" (`param.rs:460-464`). | Both hold. Likes from outside Home enter the **cold-start numerator** (capped at Home views) but not ranking or training engagement. The like-rate feature uses total likes. Coordinating likes is forbidden (§7). | The two quotes describe different components. |
| C5 | **Click and share weights** | [HM] 0.3 / 2; [HI] and [REV] 0.274 / 2.118. | Use 0.274 / 2.118. | [HI] tracks the param.rs changelog; [HM]'s values look like the unsynced vm-ranker defaults. Only the ratios enter Ŝ_p. |
| C6 | **β prior: 0.7 or 0.75** | [POP] N(0.7, 0.2²), a pure judgement call. [FC] μ_β ~ N(0.75, 0.2²), from a ledger elasticity of ≈0.76. | **μ_β ~ N(0.75, 0.2²)**. | Backed by data, and only 0.25 SD from the alternative. Garimella & West imply β < 1 [POP]. |
| C7 | **Likelihood for engagement** | BetaBinomial [POP, PX] vs NB2 with a log-impressions offset [FC, EXP]. | **NB2 with an offset** [J]. The exact Beta posterior is kept for the like-rate feature, which mirrors the cold-start sampler. | Rates are 0.1–5%, so the binomial cap doesn't matter. Experiments use the same code path. |
| C8 | **Splitting reach into in-network and out-of-network** | [POP] fits I_in and I_oon each as NB2, but only their sum is observable [GR]. | One NB2 on the total, with mean μ_in + μ_oon [J]. | The model stays identifiable: peer replies (μ_oon = 0) and the spread of follower counts F across accounts separate the two terms. |
| C9 | **Prior on links** | [POP] −1 (SD 0.7). [HM]: no hand-coded penalty. [HI]: grade-B statements that links aren't deboosted. Galeazzi: up to 8× fewer views (observational). | **N(−0.5, 0.7²)** on the out-of-network log-mean [J]. | The observational evidence is confounded; the code is grade A. The wide SD lets about 30 link posts override the prior. Experiment E02 tests it directly. |
| C10 | **Checkpoint schedules** | [HM] 5m…72h; [POP] 15m…48h; [TAPI] owner 82 snapshots, peers 2h/6h/24h/48h/7d; [REV] 15–60 min. | Superseded for v1 by batch capture with a maturity curve (§1.3). The dense schedules return when the platform is hosted. | The owner runs the platform every few days, so fixed-age snapshots cannot be taken. |
| C11 | **Status rule, the 70% rule, CUPED vs offset, switchback for Premium, Thompson-sampling floor, FDR method, power figure, peer prior, model promotion, nowcast baseline, Hawkes, form of the follower model** | REVIEW proposals vs the newer notes. | Replaced: [S-1]…[S-12] in §§2–6. | — |

---

## 1. Measurement layer

### 1.1 Entities

- `account`, with an `account_snapshot` at every run: followers F, following, statuses, mutual count M, Premium/verified flag.
- `post`:
  - type ∈ {original, quote, reply, self_reply, repost}
  - `conversation_id` and parent author (with their follower count)
  - `created_at` to the second
  - `regime_id`
  - `forecast_id` (the frozen forecast)
- `post_snapshot`:
  - `age_min`
  - impressions: `viewCount` or `impression_count`, which is cumulative total impressions, **not Home views**
  - likes, reposts, replies, quotes, bookmarks
  - owner-only: `user_profile_clicks`, `url_link_clicks`, video quartiles, follows, unfollows
  - `provider`, `fetched_at`, `late` [TAPI]
- `engager`:
  - user id, followers, following, `follows_us`, `created_at`
  - action, timestamp, available as follows:
    - repliers, quoters, reposters: always
    - likers: where an endpoint allows
- `follower_event`: gain or loss, date, source.
- `label_set` / `post_label`:
  - fields: topic, content kind, hook type, slop proxy
  - source: rule, LLM, owner or agent
  - taxonomy version
- `activity`: owner actions taken in a real client and logged (replies with target, follows), for the follower model and guardrails.
- `regime_event`: from the change tracker (§2e).
- `assignment`: experiment, arm, π for every arm, seed, posterior snapshot, treatment-blind forecast hash.
- `forecast`: frozen at issue, with model version, stage and hash.
- `monitor_state`.

### 1.2 Metrics

| Role | Metrics |
|---|---|
| Primary | **I48**: impressions at 48h (the For You age cutoff [HM]; half of all impressions arrive in a median of about 80 min [POP Pfeffer]). In batch mode I48 is observed through the first capture at age ≥ 48h, with a maturity-curve offset (§1.3), or exactly from owner analytics buckets. |
| Secondary | I7d (first capture at age ≥ 7d, same offset); each engagement count at 48h. **Only when owner analytics buckets exist:** like rate within the first ~200 impressions and time to first like (TTFL) [PX]. Neither is observable for peers in batch mode. |
| Owner only | profile clicks, link clicks and follows per post (`/2/tweets/analytics` if the probe succeeds [TAPI]); follower gains and losses per run interval (daily only with analytics buckets) |
| Not available | Negative feedback (not interested, mute, block, report). Proxies: unfollows, reply-sentiment flags, impressions below forecast |

### 1.3 Batch capture [resolves C10]

**Runs.** A run happens whenever the owner or their agent starts one, typically every 2–4 days (G = the gap). Nothing is collected between runs. Every count is stored with its exact `fetched_at` and the post's age at that moment.

**Owner (official API).**
- Each run reads every owner post younger than 30 days, with public, non-public and organic metrics. Non-public metrics exist only for posts up to 30 days old, so a gap above about 25 days loses them for the oldest posts. Owned Reads are deduplicated per UTC day, so a run costs about one read per post [TAPI].
- `/2/tweets/analytics`, if the day-1 probe succeeds: pull hourly buckets once a post is at least 7 days old, and daily buckets once at about 30 days. Hourly buckets reconstruct the post's exact trajectory, so I48, I7d, the early like rate and TTFL become **exact** for owner posts. **[S-21]**
- Follower count at each run. New followers since the last run (newest-first list) and unfollowers (follower-ID diff). These give **interval** gains and losses, not daily ones (§2c).

**Peers (twitterapi.io, or the official API if the owner chooses it).**
- Discovery: grouped `from:a OR from:b … -filter:replies since_time:<last run>` search, 25 handles per query, once per run.
- Re-checks: each peer post is re-read by ID at every later run until a capture at age ≥ 7d exists. At G = 3 that is about 2.3 re-reads per post.
- Peer follower counts once per run.
- Batch size is 50 IDs, because [TAPI] reports both 50 and 100.

**Maturity curve.** Let G_k(a) = E[y_k(a)] / E[y_k(48h)] for metric k and age a.
- Shape: monotone and increasing; log G_k(a) is a monotone spline in log a, with G_k(48h) = 1. Separate curves by post type and follower band, partially pooled [J].
- **Identified from repeated captures.** Every post captured at two ages a₁ < a₂ gives the ratio y(a₂)/y(a₁) ≈ G(a₂)/G(a₁). Because posting times are spread across the run gap, captures cover ages from 0 to beyond 7 days. Owner analytics buckets, if available, give exact owner curves.
- Prior: half of impressions within about 80 min [POP Pfeffer] and about 94% of the 72h total by 24h [repost archive, `nep`]. After 48h the post leaves For You, so log G(a) for a > 48h has a small positive slope with a half-N(0.1) prior on log G(7d) [J].
- The curve is versioned with the model, and every forecast uses the curve version frozen with it.

**How outcomes enter the model.** I48 is latent. Each post contributes its level once, plus the shape of its trajectory:
```
level:  y(a_obs) ~ NB2(μ48 · G(a_obs), φ_obs(a_obs))      a_obs = first capture with age ≥ 48h
        log φ_obs(a) = log φ − ψ·|log(a/48h)|,   ψ ~ half-N(0.5)   [J]
shape:  y(a₁) | y(a₂) ~ BetaBinomial(y(a₂), G(a₁)/G(a₂), κ)      for each earlier capture a₁ < a₂ of the same post
        κ ~ half-N(0, 200²) on the concentration   [J]
```
- Cumulative captures of one post are nested, so they are **never** entered as independent NB2 observations. The conditional BetaBinomial uses each earlier capture once, against the next later capture. It identifies the curve's shape separately from the post's level.
- **Identification.** The shape between ages a and a + G is identified by the spread of posting times within the run gap, plus the spline prior. Irregular gaps help. With perfectly constant gaps, ages that are never captured rely on the prior; the curve gate (§5) checks this.
- A post first captured after 14 days is still scored, flagged `late_maturity`. Lateness depends on run timing, not on outcomes, so leaving these posts out is ignorable: they are excluded from D1, D3 and the release gates, but kept in diagnostics.
- Owner posts with analytics buckets use the exact I48 instead.
- The same treatment applies to I7d and to each engagement count, each with its own curve.

**What a frozen forecast stores.** A quantile summary of I48 isn't enough, because the observed age a_obs isn't known when the forecast is frozen. Each forecast stores a hashed reference to 1,000 joint posterior draws of (μ48, φ, ψ, curve parameters), in an append-only draw store. The mid-PIT and CRPS are computed from those draws at the actual a_obs, so curve uncertainty carries through.

**Forecast freezing in batch mode.**
- **Planned posts (live forecasts).** When a post is registered ahead of time (`register_planned_post`, by the owner or the agent), its forecast is frozen and hashed at registration. Experiment slots always go this way: the treatment-blind forecast is frozen, then the arm is drawn.
- **Unplanned posts (as-of forecasts).** A post the platform first sees at a run gets an **as-of** forecast: the forecast that would have been made **at the end of the last run before the post's `created_at`**. It uses:
  - that run's database snapshot: its champion model and curve version, the follower count and mutuals measured at that run (carried forward, never interpolated), the regimes known then, and the outcomes matured by then;
  - plus the post's own content labels and schedule features (time, gap since the previous own post), which are known before posting.
  
  Nothing captured after that run is used. That includes the next run's follower count, which already contains follows the post itself caused. The input manifest names the snapshot (run ID) and is hashed, so anyone can recompute the forecast. As-of forecasts enter calibration and monitors; experiments use only live forecasts.

**Quality checks.**
- Each week, cross-check about 50 posts between twitterapi.io and official `public_metrics` [TAPI].
- Monitor the maturity curve's fit with LFO-CV on held-out capture pairs.
- **Interpolation rule (kept for the hosted mode):** log–log monotone interpolation only with snapshots on both sides within ±25% of the target age. Never backfill [REV][J].

**Hosted mode (later).** Once the platform runs continuously, restore the dense schedule:
- owner: 5, 10, 15 min, every 15 min to 2h, hourly to 48h, then 72h, 7d and daily to 30d;
- peers: 1h, 2h, 6h, 24h, 48h and 7d.

The maturity curve then becomes a check on those direct measurements.

### 1.4 Derived features (computed at publish time unless marked *early*)

*Early* features need the first hours of a post. In batch mode they exist only for owner posts with analytics buckets; models must run without them.

| Feature | Definition / source |
|---|---|
| `post_type`, `cs_eligible` | type; cs_eligible = original or quote, F ≤ 50k [HM] |
| `like_rate_t` (*early*) | (0.75 + x)/(50 + n), with x = min(likes, n) and n = impressions at t. The posterior is Beta(0.75+x, 49.25+n−x) [HM][POP]. Total impressions overstate Home views, so the kink at 200 Home views shows up at **total** I ≥ 200. |
| `has_url`, `link_domain_tier` | link in the body (weighted length 23); domain tier is allowlisted, unknown or shortener [SF] |
| media | `has_photo`, `n_media`, `has_video`, `video_dur`, `video_gt10` (native video over 10 s enters the video index at creation [PX]), gif |
| text | weighted length, newline count, `n_mentions`, `mention_nonfollower`, `n_hashtags` [HM] |
| cadence | `own_posts_48h` (author-diversity exposure), `gap_prev_h` (any own post), `gap_prev_orig_h` |
| graph | F_post, M_post, log(1+F), number of peer-panel accounts following us |
| timing | local hour and weekday: Fourier series with K=3 on a 24h period, plus a weekend term [FC] |
| embedding | Render the post as v8.2 does: bio, verified and follower tier, text, link-card title and domain, ASR transcript (≤2,000 chars), quoted post [PX]. Embed with Qwen3-VL-Embedding-8B (1024-d) or a smaller model. Features: `cos_topq` (cosine to the centroid of the owner's top-quartile posts by residual; the peers' centroid at cold start), `dist_topic_centroid`, `cluster_L1` (k=16) and `cluster_L2` (k=64) as stand-ins for SID levels [J on k], `near_dup_max` (max cosine to own last 200 posts and replies) |
| topic | LLM label against a versioned taxonomy mapped to `software_development`/`ai`/`science_technology`; `topic_share_last15` with flags at ≥0.5/0.75/0.9 (topic-index rule [PX]) |
| `regime_id` | tracker regime at post time [HI] |
| content labels | `content_kind` (demo, build-log, reference, opinion, launch), `hook_type`, slop proxy (LLM judge) |
| *early* dynamics | ΔI per interval; TTFL; time to 3 and 10 likes [POP Shulman]; share of engagers who don't follow us; `cap_hit` (I ≥ 200 before 2h) |

### 1.5 What we cannot observe, and how the model handles it

| Unobservable | Handling |
|---|---|
| Home views (only total impressions) | proxy |
| Whether and how a post was retrieved; the in/out-of-network split [GR] | latent split via the μ_in/μ_oon structure |
| Per-viewer probabilities; K competing cold-start posts | prior on K |
| Live config | change tracker |
| Shares (copy-link, DM), dwell, negative actions | bookmarks and reposts as share proxies |
| Safety labels and reputation; Under the Hood (needs an account ≥1 year old) [SF] | suppression HMM plus visibility probes |

### 1.6 Platform profile: what is X-specific and what is generic

**Generic (code):**
- hierarchical NB reach and engagement;
- nowcast regression;
- follower funnel;
- peaks-over-threshold (POT) tail;
- status rule and detectors;
- experiment engine;
- prior transfer;
- guardrail engine.

**Platform profile (YAML):**
- `primary_horizon_h` (X: 48)
- `cold_start` {window_h: 2, view_cap: 200, prior: [0.75, 49.25], follower_cap: 50000, eligible_types: [original, quote]}
- `type_reach_matrix` {type → (m_in, m_oon)}
- `oon_factor` 0.75
- `author_diversity` (0.5, floor 0.25)
- `mutual_boost`
- `observable_public_metrics`, `owner_only_metrics` and their age limits
- `capture_mode` (batch in v1, with `maturity_curve` priors; `checkpoints` for the hosted mode)
- `topic_consistency_rule` (15 posts)
- `change_tracker_source` (X repo; none for LinkedIn or Instagram)
- `safety_rules`
- `ranker_weight_vector` for Ŝ_p

LinkedIn and Instagram profiles start with `cold_start: null`, `change_tracker: null`, a horizon the platform team picks, and weak priors. Nothing X-specific is hard-coded [J].

---

## 2. Models

### 2a. Model before publishing (primary age 48h; secondary 7d)

**Reach**
```
I_p ~ NB2(μ_in,p + μ_oon,p, φ_j)
μ_in,p  = m_in(type) · F_p^{β_j} · exp(a_in,j  + x_p'θ_in + g_w + u_jw + λ_in·q_p)
μ_oon,p = m_oon(type) ·          exp(a_oon,j + x_p'(θ_oon+δ_j) + s(hour,wd) + g_w + u_jw + λ_oon·q_p)
m_in:  original/quote 1; reply/self-reply 0.75·ρ_th        m_oon: original/quote 1; reply/self-reply 0
q_p ~ N(0,1)   (latent post quality)
```
F = 0 makes μ_in = 0, so a brand-new account's reach is entirely out-of-network. Peer replies pin down the in-network term.

**Engagement**, for k ∈ {like, reply, repost, quote, bookmark} and, for the owner, {profile_click, link_click, follow}:
```
y_pk ~ NB2(I_p · exp(g_kj + z_p'δ_k + κ_k·q_p), φ_k)
```
The shared q_p makes reach and engagement rates correlate, as with Phoenix's correlated heads [PX][POP].

**Priors**

| Parameter | Prior |
|---|---|
| (a_in, a_oon, β)_j | multivariate normal, LKJ(2); τ_a ~ half-N(1), τ_β ~ half-N(0.3) |
| μ_β | N(0.75, 0.2²) [C6] |
| θ | N(0, 0.5²), except `has_url` in θ_oon ~ N(−0.5, 0.7²) [C9] |
| δ_j | N(0, τ_θ²), τ_θ ~ half-N(0.3) |
| log φ | N(log 2, 0.7²) |
| λ | half-N(1) |
| κ_k | N(0.5, 0.5²) [J] |
| g_like | N(log 0.015, 1²), matching the mean of Beta(0.75, 49.25) |
| ρ_th | Beta(2,2) |
| g_w | global weekly random walk, σ_g ~ half-N(0.1), plus jumps at tracker change-points with σ_J ~ half-N(0.3) |
| u_jw | σ_u ~ half-N(0.15) [FC] |
| owner-only rates | placeholders: profile clicks per 1k impressions log-mean ~ N(log 5, 1²); follows per profile click ~ N(logit 0.1, 1²) on the logit scale (assumption A7) |

**Observation.** Each outcome enters through the batch-capture likelihood in §1.3, y(a_obs) ~ NB2(μ·G(a_obs), φ_obs), or exactly from owner analytics buckets. The maturity curve G is fitted jointly with the reach model.

**Estimation**
- NumPyro NUTS with non-centred parameterisation, refit once per run on owner and peers jointly. The level term uses matured posts (a capture at age ≥ 48h); the shape term uses every capture pair.
- Draft forecasts use 1,000 cached posterior draws and integrate over q_p.

**Minimum data**
- At least 30 matured posts per peer and at least 2,000 peer posts in total.
- At least 20 owner posts before owner-level terms are estimated.

**Outputs**
- I48 and engagement predictive quantiles (q05–q95), and P(I48 ≥ G).
- The model-implied in/out-of-network split, labelled latent.
- Driver multipliers.
- A **treatment-blind** variant that leaves the randomized levers out of x. It is frozen and hashed before randomization [EXP].

**Fallbacks and benchmarks**
1. A single-component NB [FC]: log μ = α_j + β_j·log(1+F) + x'(θ+δ_j) + s + u.
2. Trailing median × (F/F_ref)^0.75, which fixes the lagging baseline flagged in [REV] [J].
3. The peer median for the same follower band.

The primary model must beat all three (§5).

### 2b. Nowcast

**Batch mode (v1).** A post younger than 48h at a run gets a provisional estimate from its single capture. Use the baseline below with t as a continuous age: coefficients are splines in log t, trained on peer posts captured at two ages either side of 48h. The like-rate and ΔI terms are dropped unless owner analytics buckets supply them. The two-phase challenger is deferred to the hosted mode, because it needs 15-minute increments.

**Baseline, one regression per canonical t** [POP; Szabo–Huberman, Pinto]:
```
log(1+I48) = a_t + b_t·log(1+I_t) + c_t·logit(π̂_like,t) + d_t·log(1+F)
             + Σ_j w_tj·log(1+ΔI_tj) + e_t·log μ̂_pre + ε_t,   ε_t ~ Student-t(ν, 0, σ_t)
```
- Including log μ̂_pre generalises precision weighting [J]. With fewer than 50 trajectories, fall back to plain precision weighting of the Gaussian log-scale pre-publication and nowcast estimates.
- The model is fitted on peers plus owner, with an owner intercept shift, and refitted at every run. Each engagement metric gets its own regression.
- Expect c_t to be large up to 2h and to shrink after.
- Where the early like rate is observable (owner analytics buckets, or the hosted mode), compare posts on the rate within roughly the first 200 impressions, not the 48h rate [POP].

**Challenger: two-phase ranker model** [POP]
- **Phase A**, for t ≤ T_A = min(2h, time at which I reaches 200). 15-min increments follow NB2 with mean (λ_in·F^β·h(t) + λ_cs·P̂win_t)·Δt.
  - P̂win_t = P(Beta draw > θ*), where θ* is the threshold for winning the cold-start slot against K competitors.
  - K ~ Uniform{20…50} [J], calibrated to the [HM] simulation table.
- **Phase B**:
  - I(t) = I(T_A) + (I∞ − I(T_A))·H(t − T_A), with H a Weibull CDF.
  - log I∞ = a + b·log Ŝ_p + d·log(1+F).
  - Ŝ_p = Σ_k W_k·r̂_k over the observable actions: like 0.708, reply 5, repost 1, quote 5, and follow 4 (owner) [HM][HI]. The r̂_k are shrunken early per-impression rates.
- An equivalent variant updates q_p's posterior from early counts using the loadings in 2a.
- **Minimum data:** at least 100 owner trajectories before promotion.

**Benchmark.** Codex's empirical remainder is kept as a benchmark the baseline must beat; if the baseline doesn't, ship the remainder. **[S-10]** Hawkes and SEISMIC models are deferred because they need reshare timestamps. **[S-11]**

**Outputs, per capture:** I48 quantiles, P(I48 ≥ G), the posterior of the early like rate where it is observable, and the inputs to the breakout model.

### 2c. Follower model (latent daily, observed per run) **[S-12]**

REVIEW's κ·log(1+Imp) form is replaced by an additive funnel, which separates organic follows from conversion and behaves at zero impressions [FC][POP]. The level used depends on which data is available:

- **L1, per-post follows available:** follows_p is an engagement in 2a. Unattributed gains U_t ~ NB2(exp(η_t + ω'A_t), φ_U).
- **L2, profile clicks only:** G_t ~ NB2(exp(η_t + ω'A_t) + π_pc·PC_t, φ_G) [FC].
- **L3, public data only (also used for peers):** G_t ~ NB2(exp(η_t + ω'A_t) + π_I·I_t/1000, φ_G), with π_I = π_in·s_in + π_oon·(1−s_in) and s_in taken from 2a [POP].
- In batch mode, PC_t and I_t are the per-interval increments of the owner's captures (the sum over all posts of the change in profile clicks or impressions between runs), and the equations hold per interval with the summed latent mean.

**Terms**
- η_t: local level in a dynamic GLM (DGLM) with discount 0.97.
- A_t: activity covariates. Replies per day by target tier, change in mutual count, and reposts of our posts by others; follows via retweets dominate [POP Antoniades; Weng].
- Breakouts: an impulse ζ·1[breakout within the last 3 days] when follows can't be attributed to posts.
- Losses: L_t ~ NB2(ρ_t·F_{t−1}, φ_L), with log ρ_t a random walk.
- **Observations come per run interval in batch mode.** The latent process stays daily, and each observation is a sum over the days d in interval k = (t_{k−1}, t_k]:
  - gains: G_k ~ NB2(Σ_d λ_d, φ_G), from the newest-first follower list since the last run [J: the sum of NB counts is approximated by an NB with the summed mean];
  - losses: L_k from the follower-ID diff at each run;
  - net ΔF_k from the follower count at each run, as a consistency check (G_k − L_k should match ΔF_k up to protected or suspended accounts).
- Per-post follows from `/2/tweets/analytics`, if available, give level L1 with hourly attribution and remove most of the aggregation loss.
- Many zero-gain intervals: use the Berry–West DCMM [FC].

**Prior from peers [J].** Regress each peer's ΔF per run interval on their ΣΔviewCount over the same interval, within each follower band. The robust MAP of the result gives a prior on π_I, so follower forecasts are informed from day 0.

**Forecast.** Simulate 4,000 Monte Carlo paths: planned schedule → draw I_p from 2a at the current F → draw gains and losses → update F.

**Outputs**
- Quantiles of F at 7, 30 and 90 days, and P(F_T ≥ goal).
- What-if scenarios: 2, 3 or 4 posts a day; mix of originals and replies.
- Activity coefficients ω, labelled observational.

**Minimum data:** 4–8 weeks of counts [POP], which is about 10–20 run intervals at G = 3. Before that, the forecast is peer-prior only and labelled as such.

**Baselines:** persistence + drift (biased low on convex paths [REV]) and a local linear trend.

### 2d. Breakout model

**Residual.** r_p = log((1+I48)/(1+median_pre)).

**Thresholds** [J; [POP] gives a 90–95th percentile range]:

| Label | Condition |
|---|---|
| outperformer | r > peer 90th percentile |
| **breakout** | r > u, where u = peer 95th percentile |
| exceptional | r > the GPD-implied 99th percentile |

**Tail model**
- (r − u) | r > u ~ GPD(ξ, σ), with ξ pooled across at least 2,000 peer posts and σ estimated per follower band.
- Engagement counts use a discrete GPD (Hitz et al. 2024).
- Breakouts per month ~ Poisson–Gamma, with the Gamma prior taken from the peer band.

**Batch mode (v1).** Breakouts are labelled at maturity from the residual and the tail model. A post younger than 48h at a run gets a provisional P(breakout) from the batch nowcast (§2b). The early alarm below is deferred to the hosted mode, because it needs captures at 30m–2h.

**Early alarm at 30m, 1h and 2h (hosted mode)**
- A hierarchical logistic model of B = 1[r > u], with these features:
  - log TTFL;
  - time to 3 and 10 likes;
  - π̂_like,t − 0.015;
  - velocity residual vs the nowcast;
  - share of engagers who don't follow us;
  - `cap_hit`: at an 8% like rate the cap is reached in a median of 68 min [HM];
  - post type.
- Its output is averaged with the nowcast's P(I48 > median_pre·e^u) [J].
- **Alarm states:**
  - `watch` when P ≥ 0.3 at ≤2h;
  - `likely` when P ≥ 0.6 at 6h;
  - `confirmed` at 48h.
  - Tune the thresholds for precision ≥ 0.5 in backtests [J].
- Reference point: Cheng et al. reach AUC 0.877 for predicting whether a cascade doubles [POP].
- The output also includes the expected follower impulse from 2c.

### 2e. Ranker regimes and change-points

**X ranker change tracker** [HI]
- Fetch the repo at every run and process every commit since the previous run. The git history keeps the sync headers, so nothing is lost between runs.
- Parse `home-mixer/params/param.rs` and the README as high-confidence changes. Also parse the vm-ranker defaults, as a low-confidence class: since 09-24 they are the only source for the OON factor, author diversity and DPP settings (x-algorithm-verification.md §2).
- Classify each change as weight, cold-start, source, model or safety.
- Timestamp it with the **sync header**, not the commit time.
- Down-weight changes that revert within 7 days.
- Seed the registry with these dates: 08-13, 08-25, 09-18, 09-29, **09-30 (cold start)**, 10-07, 10-08/13, 10-14, 10-22, 11-05, 11-13, 11-18.

**In the model**
- g_w jumps at flagged dates, with σ_J ~ half-N(0.3); cold-start and source changes get half-N(0.5) [J].
- Feature × regime interactions (has_url, video) are fitted only for regimes with at least 300 peer posts [J].
- Peer backfill from before 09-30 enters only with regime fixed effects, and is left out of the coefficients tied to cold start.

**Changes the tracker doesn't announce (D0, peer-panel monitor)**
- Compute the mean of the peer z-scores per posting day (about 300 posts/day), and run a CUSUM with k = 0.25 on √n·z̄. In batch mode, days are added once their posts mature, so the chart lags by 48h plus the run gap.
- This catches platform-wide shifts within days of the shift becoming observable.
- When the owner's D1 detector alarms:
  - if D0 also fired within ±3 days, label the cause **platform**;
  - otherwise label it **owner-specific**.
- PELT and BOCPD (ruptures) are used to date a change, not to raise alarms [FC].

**Experiments.** If a regime change falls inside an experiment window, add a regime fixed effect and flag the readout. A cold-start-class change splits the readout [J].

### 2f. Suppression regime detection [SF]

**States** (hidden semi-Markov model over owner days):

| State | Effect |
|---|---|
| S0 | normal |
| S1 | out-of-network suppressed: μ_oon × ε₁, with ε₁ ~ Beta(1,19) |
| S2 | also hidden from followers: μ_in × ε₂ as well |

**Emissions**
- I48 of originals under 2a's component multipliers.
- Share of engagers who don't follow us, Beta-binomial by state.
- Whether authors we replied to engage with the reply, as a proxy for RISKY_HIGH_VIZ_REPLY [J].

**Durations.** A mixture over {7, 14, 30} days with weights 0.4/0.3/0.3 [J].

**Entry hazard.** 0.002/day × exp(γ'risk), where the risk actions are logged by the platform:
- a pinned link to an unknown domain;
- a link combined with @mentions of non-followers;
- `near_dup_max` > 0.95;
- bursty posting.

**Alert.** Raise "possible suppression" when P(S1 ∪ S2) ≥ 0.8 across at least 3 consecutive originals while in-network engagement stays normal. The response is to:
- pause risky levers;
- run a visibility probe from a consenting account that doesn't follow us;
- wait 7–30 days before drawing conclusions.

**Limit.** The model needs an in-network baseline (F ≳ 100 and at least 20 originals). Before that, "suppressed" and "not retrieved" look the same, so the output is `insufficient` and only the probes are used. When D1 alarms and the suppression posterior is high, suppression is offered as the leading explanation.

---

## 3. Turning peer data into priors

**Primary.** A **joint hierarchical fit** of owner and peers, refreshed at every run. The owner's parameters are drawn from the population predictive, which is equivalent to a meta-analytic-predictive (MAP) prior. **[S-8]** REVIEW's "use the peer posterior as the prior" understates the uncertainty.

**Cheap path, or fallback when the joint fit fails its gates.** A robust MAP:
- prior = 0.8·MAP + 0.2·vague, where vague is N(0, 2²) on log intercepts;
- the MAP part is a mixture of normals fitted to the predictive draws [FC; Neuenschwander 2010; Schmidli 2014].

**Peer panel**
- 50–200 dev-tools accounts, stratified by follower band (≤1k, 1–5k, 5–50k).
- No accounts above 50k: they are outside the cold-start cap and live in a different regime [J].
- Store each account's verified flag and its embedding similarity to the owner's content plan. Similarity can weight a power prior later [J].

**Prior–data conflict.** If the mean z of the owner's first 10 posts is below −1 or above +1, report "peer prior conflict". The robust mixture's weight then shifts automatically.

**Staged promotion [S-9]**

| Owner matured posts | Components that become eligible |
|---|---|
| 0 | peer-predictive prior; baseline nowcast trained on peers; ξ and u from peers; follower model from the peer prior |
| ≥20 | owner a_in, a_oon and φ; D1; goal status with owner data |
| ≥60 | owner δ for type, format, link and video; D2; follower DGLM (once there are 4 weeks of data) |
| ≥150 | interactions, hour s(), cluster effects, full suppression HMM, bandits; the two-phase challenger only in the hosted mode |

The post count only makes a component eligible. Promotion also requires:
- LFO-CV (Bürkner et al. 2020): 1-step-ahead, refitting when Pareto k > 0.7, with ΔELPD > 2 SE over the current champion;
- a Berkowitz test p > 0.01;
- the release gates in §5.

---

## 4. Status and alerting

### 4.1 Per-post surprise **[S-1]**

REVIEW's rule ("PIT > 0.8 OR P(goal) > 0.7") mixed comparison with the forecast and comparison with the goal. They are now separate readouts.

- u_mid = F(y−1) + ½·p(y), computed under the frozen pre-publication predictive. In batch mode, y is the maturity capture and the predictive is NB2(μ48·G(a_obs), φ_obs), using the maturity curve frozen with the forecast (§1.3).
- Labels: u_mid ≥ 0.8 is "above expected", u_mid ≤ 0.2 "below expected", anything else "as expected". These are **not** verdicts on whether something is working.
- Before 48h (a post younger than 48h at a run), show the batch nowcast's P(I48 > median_pre) as "provisional".
- The UI shows the mid-PIT. The randomized PIT, seeded by post id, is used only inside monitors [FC].

### 4.2 Goal status for a scope

A scope is one of: the last 21 originals; the posts for a lever; a follower goal.

**θ_S** is the log of the posterior median metric for the scope's average post at current conditions. It comes from 2a with a scope effect b_S ~ N(0, 0.3²), updated by the scope's outcomes. For follower goals, θ = log F_T from 2c.

With ε = log 1.1:
- p_hi = P(θ_S > log G + ε)
- p_lo = P(θ_S < log G − ε)

| Status | Condition |
|---|---|
| **Working** | p_hi ≥ 0.80 |
| **Not working** | p_lo ≥ 0.80 |
| **Near threshold** | otherwise, with a reason: `close`; `uncertain` (80% interval width > 4ε); `insufficient_owner_data` (fewer than 5 posts in scope) |

**Hysteresis.** Leave Working only when p_hi < 0.70 (to Near) or p_lo ≥ 0.85 (to Not working). Not working is symmetric. From Near, the 0.80 entry thresholds apply.

**Default goals when the owner hasn't set one [J].**
- Posts: the peer median I48 for the owner's follower band.
- Followers: the peer band's 75th percentile of 30-day growth.
- Goals are frozen per period, and every change is logged.

**Calibration.** Monthly Brier score and CORP reliability diagram for the logged P(goal) [FC].

### 4.3 Detectors **[S-2]**

REVIEW's "more than 70% of posts not working" rule is dropped: it fires with probability 4.5×10⁻⁹ when the model is calibrated, and only 38% of posts fall below PIT 0.2 after a real 30% drop [FC].

**D1: forecast drift.** Runs from stage ≥20 on z = Φ⁻¹(randomized PIT) of I48 for originals and quotes, always using the posterior predictive, never plug-in estimates.

| Chart | Parameters |
|---|---|
| Location CUSUM, two-sided | k = 0.25. Single-chart reference: h = 5.61 (ARL0 = 100). Deployed with K = 3: h = 7.62 (ARL0 = 300) |
| Alternative: EWMA | λ = 0.1, L = 2.148 |
| Optional | risk-adjusted NB likelihood-ratio CUSUM |
| Scale CUSUM on z² − 1 | k = 0.25; h = 11.06 upper, 6.94 lower (ARL0 = 200 each) |

- **Paging budget, in calendar time.** The target is about one false page per 100 days per chart, with K = 3 charts:
  - location on I48 (two-sided), scale on I48, and a CUSUM on the z of follower gains per run interval (§2c);
  - the post charts get ARL0 = 300 posts (≈ 100 days at 3 posts a day);
  - the follower chart gets ARL0 = 100 days ÷ G run intervals (≈ 33 intervals at G = 3). It is recomputed when the owner's typical gap changes.
- **Location threshold.** Each side of the two-sided chart needs a one-sided ARL0 of 600, so **h = 7.62** (exact Brook–Evans chain, 800 states, verified 2026-11-25). The single-chart h = 5.61 corresponds to each side at 200.
- **Other thresholds** are computed at build time with the same Markov-chain method on the correct in-control distribution: χ²₁ − 1 for the scale chart, and the follower chart's z at its ARL0. They are stored with the model version.
- Charts update at each run, once posts mature.
- **Diagnostics every 21 posts, over the last 63:**
  - Berkowitz LR test (3 df);
  - 50% and 80% coverage against binomial bands (for n = 60 the 80% band is [0.70, 0.90]);
  - Wei–Held test for small counts.
- **On alarm:**
  - West–Harrison intervention (σ_u × 3 for a week);
  - switch to ACI intervals;
  - reset the CUSUM with a head start of h/2;
  - attach the cause: platform (D0), suppression (2f) or owner.
- Likes run as a chart that doesn't page.

**D2: goal is wrong.** Runs only while D1 is quiet.
- π̄ = mean of P(y ≥ G) over the last 30 posts.
- If π̄ < 0.10, propose the 60th–70th predictive percentile as the new goal. If π̄ > 0.90, propose the 70th–80th.
- Check the proposal against peer percentiles for the same band, and require a Poisson-binomial hit-count test p < 0.05.

**D3: ACI.**
- γ = 0.05, or AgACI over {0.01, 0.02, 0.05, 0.1}.
- α_t clipped to [0.01, 0.6].
- Alarm if α_t < 0.10 for 20 posts.

### 4.4 Realistic detection delays [FC]

| Signal | Delay |
|---|---|
| 30% drop in I48, φ = 1.5 / 3 / 8 | ARL1 29.8 / 17.6 / 9.1 posts, about 10 / 6 / 3 days at 3 posts a day, for a **single** chart at h = 5.61 (simulated under NB). P(alarm within 10 posts) = 0.10 / 0.24 / 0.72. The deployed h = 7.62 is slower; re-simulate ARL1 under NB at build time. **In batch mode add 48h of maturity plus up to one run gap** before an alarm can be seen. |
| New account, predictive log-sd about 0.9 [J] | expect 25–30 posts (about 9–10 days) |
| Likes | ARL1 20–25 posts |
| Follows per post | 55–90 posts, too slow to use, so follower gains per run interval are monitored instead |
| Detection within 10 posts | requires predictive log-sd ≤ 0.42 |

**How fragile ARL0 is (nominal 100):**
- autocorrelation ρ = 0.1 / 0.2 / 0.3 → 71 / 53 / 42;
- plug-in parameters from n = 20 / 60 / 150 posts → 66 / 82 / 92.

**Other delays:**
- BOCPD is slower than CUSUM: median 19 vs 12 posts at φ = 3.
- Breakout: confirmed at maturity in batch mode; watch at 1–2h and confirm at 6h only in the hosted mode.
- Suppression: at least 3 originals, once F ≳ 100.

---

## 5. Evaluation and release gates

**Protocol**
- Rolling-origin backtests with frozen forecasts.
- Cold start is tested by leaving out one peer account at a time.
- Scores: CRPS and WIS on log1p and on raw counts; PIT; coverage; Brier score.

**Gates (all must pass)** [FC]:
1. Log-scale CRPS skill vs the trailing-median baseline is above 0, with the lower bound of the week-block bootstrap 90% interval above 0.
2. 50% and 80% coverage fall inside the binomial bands.
3. Berkowitz and PIT χ² p-values are above 0.01.
4. No CUSUM alarm in the backtest.
5. 80% interval width is at most 1.1× the baseline's, after the baseline's ranges are recalibrated to cover 80% on the same backtest. *Amended 2026-11-26 (owner): on real accounts the trailing median covered only 40–52% inside its 80% range, so the raw comparison held back a calibrated model for being wider than a wrong baseline.*
6. R̂ < 1.01, ESS > 400, and simulation-based calibration (SBC) passes.
7. Brier score is no worse than the baseline.

**Gates for specific components [J]:**
- **Nowcast:** CRPS skill above 0 in every capture-age band (0–6h, 6–24h, 24–48h), against both the pre-publication forecast alone and the Codex remainder.
- **Maturity curve:** on held-out capture pairs, the predicted ratio y(a₂)/y(a₁) has 80% coverage inside the binomial band, with no trend in the log-ratio residuals by age.
- **Follower model:** 7-day and 30-day CRPS skill against persistence + drift and against a local linear trend.
- **Breakout:**
  - Brier skill vs the base rate above 0;
  - calibration slope between 0.8 and 1.2;
  - coverage of the 95th and 99th quantiles inside binomial bands.
- **Experiment engine:**
  - simulated A/A size between 0.04 and 0.06;
  - sample-ratio-mismatch (SRM) check;
  - randomization test agrees with the posterior at least 97% of the time [EXP sim B].

---

## 6. Experiment framework [EXP]

### 6.1 Design by lever type

| Lever | Design | Unit and outcome |
|---|---|---|
| **Per post** (format, hook, link placement, content kind, quote vs original) | Micro-randomized trial blocked within each day: ⌊k/2⌋ or ⌈k/2⌉ of the day's posts treated. At 1 post a day, block by 2-day pairs. Up to 3 factors can run factorially on the same posts. π = 0.5. **In batch mode, slots are registered for whole local days, up to 7 days ahead, at a run or by the agent in between, and arms are drawn then.** Blind forecasts come from the protocol's frozen model version, not the current champion, so the covariate keeps one scale. Unplanned posts are not randomized and stay out of the experiment. | post; log I48 |
| **Time-level** (posting window, frequency) | **Switchback in 4-day blocks: 2 washout days, then 2 measured days.** This overstates carryover on purpose, which Bojinov–Simchi-Levi–Zhao §4.3 shows is safe. Analysed with a randomization test. | day; mean log impressions per post; follows as a flow, **never as a stock** |
| **Account-level** (Premium, bio, reply or follow strategy) | **Interrupted time series / BSTS (CausalImpact)** with peer-panel controls, and a **start date randomized** within a declared window (Onghena–Edgington). At least 8 weeks before and 4 weeks after. Reported as evidence, not proof. **[S-4]**: REVIEW's 2–3-day switchback for Premium breaks bounded carryover. | day |

### 6.2 Analysis model [S-3]

```
log y_i = α_slot + β·log f_i + Σ_k τ_k·A_ik + γ_k·A_ik·(log f_i − mean) + λ·A_{i−1,k} + u_day + ε_i
ε_i ~ t(ν, 0, σ), ν ~ Gamma(2, 0.1), σ ~ half-N(1.5)
β ~ N(1, 0.25²)          (f = frozen TREATMENT-BLIND forecast; "offset" is only the prior mean)
τ_k ~ N(μ_fam, s_fam²), μ_fam ~ N(0, 0.2²)
```
- REVIEW's offset fixes the slope at 1, which adds variance whenever the true slope is ≤ 0.5. This model estimates the slope, with a Lin interaction.
- Zero-heavy outcomes (replies, follows, clicks): NB with a log-impressions offset, reported as a ratio of means (Chen & Roth).
- Breakout question: quantile treatment effect at q = 0.9.
- Always also run a randomization test: 9,999 re-draws of the same blocked design. If it disagrees with the posterior, send the readout to a human.
- Data assigned by a bandit: AIPW with √e_t weights (Hadad) or batched per-day contrasts (Zhang–Janson–Murphy).
- Health checks before any readout:
  - SRM;
  - maturity capture (age ≥ 48h) complete for every assigned post, with the age offsets recorded;
  - missed-slot and deleted-post rates compared by arm. A post deleted before any run never appears; if the owner deletes flops, attrition depends on arm and outcome. Rule for the owner: don't delete experiment posts;
  - forecast hash timestamped before assignment;
  - no posting delay that depends on treatment;
  - a regime fixed effect if a change-point falls inside the window (§2e).

### 6.3 Decision rule

With δ = log 1.10 and s0 = 0.2:

| Outcome | Condition |
|---|---|
| **Adopt** | P(θ > δ) ≥ 0.80 **and** E[max(0, −θ)] < 0.01 |
| **Reject** | P(θ < 0) ≥ 0.90, or P(θ > δ) < 0.20 at the horizon |
| **Near threshold** | the 80% interval straddles δ at the horizon; offer one extension of up to 50% |

- The horizon is 4 weeks (84 posts), or 8 weeks if the hypothesized effect is under 1.5×.
- Simulation F (4 weeks, weekly looks): false adoption at a true null 2.3%; adoption of a harmful 0.8× lever 0.1%; P(adopt) = 18% at 1.25×, 48% at 1.5×, 91% at 2×.
- **Looks in batch mode.** The decision rule is evaluated only at the first run after each experiment-week boundary, and the final read at the first run after the horizon plus 48h. Looking at every run (about 9 looks in 4 weeks) would raise false adoption above the simulated 2.3%.

### 6.4 Sequential monitoring

- Look weekly at the posterior: in batch mode, at the first run after each experiment-week boundary (§6.3).
- Never run p < 0.05 at every look: naive weekly peeking over 8 weeks gives 17.4% false positives, and a flat-prior Bayes P > 0.95 gives 15.8%.
- **Public claims only:** an mSPRT with a normal mixture (sd log 1.5, α = 0.05). Realized type I error is 0.7%, but power at 1.5× falls from 81% to 48%.
- **Guardrails, always valid:** one-sided confidence sequences (Howard et al.). They stay valid at any stopping time, so checking them only at runs is fine. A breach on post outcomes is seen up to 48h + G after the post, so **keep G ≤ 3 days while an experiment runs** [J].
  - **Arm harm (post outcomes):** a confidence sequence on the treated-minus-control mean of log(y/f), where f is the blind forecast. The arm pauses when the sequence's upper bound falls below log 0.8. This replaces the "3 consecutive treated posts below 0.5×" rule: under a calibrated null with predictive log-sd 0.9–1.1, that rule trips in 29–44% of 4-week experiments (42 treated posts; verified by simulation 2026-11-25).
  - **Unfollows:** experiment-level, as a rate per day over each run interval against the pre-experiment baseline. Unfollows can't be attributed to an arm under within-day randomization, so a breach pauses the whole experiment. With per-post unfollows from analytics buckets, it becomes an arm-level sequence.
  - **Reply-sentiment flags and negative-feedback proxies:** arm-level where they attach to posts, experiment-level otherwise.

  **Pausing** stops new arm draws. Slots already drawn for the paused arm revert to control and are logged as deviations. The readout stays intention-to-treat on the original assignment, with a per-protocol sensitivity analysis.

### 6.5 Bandits **[S-5]**

- Only for levers that already have a confirmatory read, or for low-stakes ones.
- Batched Thompson sampling, updated at each run and applied when slots are registered, with π clipped to [0.2, 0.8] for 2 arms, or ≥ 0.1 per arm for up to 5 arms.
- Log propensities.
- Thompson sampling costs confirmatory power: 51% falls to 36–44% at 1.5×.
- To choose a policy, use exploration sampling q ∝ p(1−p) as a heuristic (Kasy–Sautmann's optimality claims are partly wrong).
- Before deploying a new allocation policy, score it off-policy with a doubly robust estimator. Deploy only if the lower 80% bound beats the current policy.

### 6.6 Interference

- Space originals at least 3h apart.
- Keep posting times per slot fixed across arms.
- Log `own_posts_48h` and the previous post's arm. If |λ| > 0.5|τ|, move that lever to day-level randomization.
- Never run a frequency experiment alongside an impressions experiment unless the per-post design is blocked within each frequency condition.
- Reply experiments are measured on profile clicks and follows (C1).

### 6.7 Multiplicity **[S-6]**

- Each concluded confirmatory experiment emits an e-value: the mSPRT likelihood ratio, or e = 1/(2√p).
- **e-LOND** at FDR 0.10 runs over the whole stream. It is valid under arbitrary dependence; LORD/LOND need independent p-values.
- Only e-LOND rejections may be labelled "confirmed".
- Agents and the owner see partially pooled τ.
- e-BH runs quarterly as an audit.
- Observational insights never enter the stream; they are labelled "hypothesis".

### 6.8 Power: key numbers **[S-7]**

REVIEW's "63% at 1.5× / 98% at 2×" holds only at R² ≈ 0.47. Report a range: about 50–65% and 92–98%.

**Per-post randomization, σ = 1.1, two-sided α = 0.05.** Cells show power at 1.25× / 1.5× / 2×, then the MDE at 80% power.

| Design | R² = 0 | R² = 0.3 | R² = 0.5 |
|---|---|---|---|
| 3/day × 2 weeks (n = 42) | .09/.21/.50, MDE 2.59× | .11/.28/.65, MDE 2.22× | .15/.36/.80, MDE 1.96× |
| 3/day × 4 weeks (n = 84) | .14/.38/.80, MDE 1.96× | .21/.50/.92, MDE 1.76× | .25/.65/.98, MDE 1.61× |
| 3/day × 8 weeks (n = 168) | .26/.66/.98, MDE 1.61× | .35/.81/1.0, MDE 1.49× | .46/.92/1.0, MDE 1.40× |
| 1/day × 4 weeks (n = 28) | .08/.15/.37 | .09/.20/.46 | .12/.26/.61 |
| 1/day × 8 weeks (n = 56) | .10/.27/.64 | .15/.37/.79 | .17/.48/.90 |

**Time-level and account-level designs:**
- Switchback with 4-day blocks, at 8 / 12 / 16 weeks: power at 1.5× is .20/.29/.37; at 2× it is .46/.65/.78.
- ITS with an 8-week pre-period, MDE at 80% power:
  - 4 weeks after: 2.20× / 1.74× / 1.42× at peer R² = 0 / 0.5 / 0.8;
  - 8 weeks after: 1.90× / 1.57× / 1.33×.
- A day fixed effect with ICC 0.3 raises 1.5× power at 3/day × 4 weeks from .51 to .61.
- Implementation: use the calculator formula SE = 2σ√(1−R²)/√n, which matches simulation within ±0.03.

**For the product:** a per-post experiment can find 2× lifts in 2–4 weeks and 1.5× lifts in 4–8 weeks. It can't realistically find 1.25×.

### 6.9 Agent permissions

**Agents may propose and auto-start:**
- per-post levers inside the approved content plan: format, hook, link placement, CTA, content kind, topic ordering;
- factorial designs of up to 3 factors, π = 0.5, horizon up to 4 weeks;
- moving a confirmed lever into bandit mode;
- one extension of up to 50%;
- off-policy evaluation;
- switchbacks that change cadence by at most ±1 post a day.

**The owner must approve:**
- anything that costs money (Premium);
- account-level changes (bio, pinned post, reply or follow strategy);
- anything touching other accounts;
- horizons over 8 weeks;
- more than 3 concurrent experiments;
- changes to a frozen metric, model or δ;
- overriding a guardrail;
- excluding posts from analysis;
- any public "confirmed" claim.

### 6.10 First 12 experiments for a new account (in priority order)

"Detectable" figures come from the power table above.

| # | Hypothesis | Mechanism | Design | Primary metric | Horizon | Detectability |
|---|---|---|---|---|---|---|
| E01 | Narrated native video over 10 s beats a static screenshot or GIF for the same demo | Enters the video index at creation, which bypasses the gate requiring a first like [PX]. The ASR transcript is embedded [PX]. VQV weight is 0, so any lift comes via dwell, follows or shares [HI]. | Per-post factor A on demo posts (about 1/day), 2-day-pair blocks | log I48; follows per 1k impressions | 4–8 weeks | n = 28: 2× at .37–.61; at 8 weeks .64–.90 |
| E02 | Link in the first self-reply beats a link in the body | `has_url` effect is learned, not hand-coded [HM]. The card title and domain are embedded [PX]. Galeazzi: up to 8× fewer views [POP]. Self-replies are never indexed [PX]. | Factor B on link posts | log I48; total link clicks (owner) as a net-value check | 4–8 weeks | Only an effect ≥2× is plausibly detectable |
| E03 | An outcome-first hook raises early like rate | Thompson sampling on likes per view drives the cold-start lift [HM]. Speed to the first adopters predicts reach [POP]. | Factor C on all originals | log I48; likes per 1k impressions at maturity. The like rate in the first 200 impressions is used only if owner analytics buckets exist. | 4 weeks | 1.5× at .50–.65; 2× at .92–.98 |
| E04 | Quoting an in-niche account beats a standalone original with the same commentary | Quotes count as originals for cold start [HM]. The quoted post and its author's tier are embedded [PX]. The ledger has quotes at 0.90–0.95× [REV]. | Per post, weeks 5–8 | log I48; follows | 4 weeks | as E03 |
| E05 | A single long post beats a thread | Conversation dedup [HM]. Self-replies are not indexed [PX]. Dwell weights [HI]. | Per post (factorial with E04) | root I48; profile clicks; follows | 4–8 weeks | as E03 |
| E06 | Image (annotated screenshot or chart) beats text only on non-demo posts | Media features; the first image is embedded in the SID [PX]. The ledger's text sample is available after the relabel fix [REV]. | Per post | log I48 | 4 weeks | 1.5× at about .50 |
| E07 | Reference content (cheat sheets, benchmarks) beats build-log updates | Copy-link share weight 20, DM share 5 [HM][HI]. Shares can't be observed, so bookmarks and reposts stand in. | Per-post content kind from a backlog of both | log I48; bookmarks per 1k; follows | 4–8 weeks | 1.5× at .50–.65 over 4 weeks |
| E08 | Posting in the engager-peak window beats off-peak | Time to first like gates the first-like index [PX]. The chance of winning the slot is about 1/K [HM]. Thunder shelf life [GR]. | Switchback, 4-day blocks | mean log I48 of the posts on each measured day; TTFL as a leading indicator only if owner analytics buckets exist | 12–16 weeks | 1.5× at .29–.37; 2× at .65–.78 |
| E09 | 2 vs 4 originals a day | Author diversity 0.75·0.5^k + 0.25 [HM]. Ledger f03 plateau [REV]. The reach-budget claim is grade B− [HI]. | Switchback, 4-day blocks, with per-post designs blocked within | sum of I48 over each posting day (daily totals aren't observable in batch mode); follows flow | 12–16 weeks | as E08 |
| E10 | Replies to 5k–50k-follower peers convert better than replies to accounts over 250k | Replies get no For You reach outside the replier's followers [GR]. Reply ranking uses whether the root author liked the reply, plus follower counts [PX]. Grox splits reply handling at 250k [SF]. | Per-reply randomization of target tier from a daily candidate list; replies written by hand in a real client. **Owner approval required.** | profile clicks and follows per reply (NB ratio of means) | 8 weeks | Low; zero-heavy; reported as evidence |
| E11 | Mutual-follow building with active niche peers increases their engagement and reposts of our posts | Reply weight rises from 5 to 20 on a mutual's originals [HM][GR]. Reposts by followed accounts are the only in-network route from a peer's audience [GR]. | Randomized encouragement: about 40 candidate peers, half randomly given a slow follow plus substantive engagement. **Approval required.** | follow-back; that peer's engagement and reposts on our posts over 4 weeks | 6 weeks | Large effects only (binary outcome, n = 40) |
| E12 | Premium | No Premium multiplier in For You [HI]. Reputation floor that shields against enforcement only [GR][SF]. Reply prioritization by tier, grade B− [HI]. | ITS/BSTS with a randomized start date, week 9 or later (needs the 8-week pre-period). **Approval required.** | profile clicks and follows per reply; impressions per post | 4–8 weeks after | MDE 1.42–2.20× |

**Schedule.** Two clocks are used. *Experiment weeks* count from when the experiment engine ships (implementation plan M3, about 6 weeks after the owner starts posting). *Account weeks* count from the owner's first post.
- Experiment weeks 1–4: E01 × E02 × E03 in factorial.
- From experiment week 3: E10 and E11, subject to approval.
- Experiment weeks 5–8: E04 × E05 plus E06 and E07.
- Experiment weeks 5–20: E08 (12–16 weeks). E09 follows it, about experiment weeks 21–36.
- E12 at account week 9 or later, which leaves the 8-week pre-period.

**Deprioritized, with reasons:**
- **0 vs 1 hashtag** (REVIEW §3.3 #10): hashtags carry no signal in the code [HI], and Hutto finds overuse harmful. Enforced as a guardrail of at most 1.
- **Niche bio:** set it at launch as policy rather than test it, since a test would need an 8-week pre-period.
- **Topic consistency:** enforced at ≥80% in-niche (§7) and tested later with ITS.

**Other REVIEW §3.3 items:**
- #1, peak vs off-peak, becomes E08 as a switchback rather than a comparison. **[S-16]**
- #7, Premium in 4-week blocks, becomes E12 with a randomized start. **[S-13]**

---

## 7. Safe-growth guardrails [SF][HI][TAPI]

**Blocked by the platform:**
- twitterapi.io write or login endpoints, and sharing cookies;
- automated likes, replies or follows (the API publishes the owner's own scheduled posts only);
- near-duplicate posts or replies (`near_dup_max` > 0.95, or a MinHash match) and templated text;
- originals less than 3h apart without an override;
- scheduled times without ±10–20 min of jitter;
- follow/unfollow cycles;
- chaining follow → like → reply on the same person;
- a pinned link to a domain that isn't allowlisted, or behind an unknown shortener;
- a link combined with @mentions of non-followers;
- coordinated engagement and DM pods (likes outside Home feed the sampler, but this is still manipulation; C4).

**Warnings:**
- more than 2–3 consecutive replies to one author;
- links in replies to strangers;
- replying to posts that look labelled or spammy;
- more than 1 hashtag;
- in-niche topic share below 80% over the last 15 posts [PX];
- slop-judge score high;
- NSFW risk (3 of the last 5 posts sets an account label);
- the 72h notification limits for a new account, plus a setup check (avatar, email, phone);
- Premium as a reputation floor;
- an action-timing regularity score (BDSM signals);
- negative-feedback proxies crossing their confidence sequences.

**Operating rules:**
- Change one risk factor at a time.
- Stop on a change-point, and wait 7–30 days before concluding.
- Keep peer data local and single-user, and keep a one-switch fallback provider [TAPI].
- Deletions found at a run are removed in that run. Between runs nothing is refreshed, so a copy of a post deleted on X can persist for up to one run gap. Whether that meets the Developer Agreement's deletion terms must be checked before M2 (a legal open item).

---

## 8. What agents get through the MCP tools

**Shared fields on every response:** `model_version`, `stage`, `regime_id`, `data_sufficiency` {owner_n, peer_n, reason}, `as_of`, and `caveats[]`. Example caveats:
- "reply: no For You reach";
- "regime change 3d ago";
- "relative to your past posts, not X's score" [PX].

**Tools**

`get_expected_range(post_id | draft_features)` returns:
- `metric`, `horizon_h`, quantiles q05/q10/q25/q50/q75/q90/q95, `mean`;
- `p_ge_goal`;
- `components` {in_network_share, oon_share, label: "latent"};
- `drivers[]` {feature, multiplier, ci80, source: prior|peer|owner, n_owner};
- `cs_eligible`;
- `treatment_blind` (bool), `forecast_hash`, `frozen_at`;
- `guardrail_flags[]`.

`get_post(id)` returns:
- `surprise` {u_mid, label};
- `nowcast` {capture_age_h, provisional, quantiles, p_ge_goal; like_rate_posterior [a, b, mean] and ttfl_min only when owner analytics buckets exist};
- `maturity` {capture_age_h, offset_log_g, curve_version, late_maturity};
- `forecast_kind` (live, or as_of with its input-manifest hash);
- `breakout` {p, state: none|watch|likely|confirmed, residual_r, tail_quantile};
- `suppression` {p, state};
- `engagement_rates` per action with intervals.

`get_account_overview()` returns:
- `goal_status[]` {scope, goal, status, p_hi, p_lo, reason, since, hysteresis_state};
- `follower_forecast` {horizon_d, quantiles, p_goal, scenarios[]};
- `monitors` {D1: {stat, h, state, last_alarm, attribution}, D2: {pibar, suggested_goal}, D3: {alpha_t}, D0};
- `regime` {current, recent_changes[]};
- `suppression`.

`breakdown(metric, age_h, group_by, min_n)` returns, per group:
- `n`, `pooled_multiplier`, `ci80`, `raw_median`;
- `evidence`: hypothesis|experiment|confirmed;
- `confounding_notes`.

Groups below `min_n` return `insufficient`.

**Experiment tools:**
- `power_preview(design, lift, R2)`;
- `get_experiment` {design, arms, π, n_by_arm, srm_p, readout: {P(θ>δ), expected_loss, ci80, randomization_p, decision, e_value, elond_status}, guardrails}.

**Insight object:**
- `claim`, `scope`, `metric`;
- `effect` {multiplier, ci80};
- `evidence_level` (observational / experiment / confirmed by e-LOND);
- `mechanism_ref` (note filename plus code path);
- `n`, `period`, `regime_ids`;
- `status` (draft / approved / superseded / withdrawn);
- `recheck_on` (the next regime change, or 90 days).

**Rules for insights:**
- Agents never present raw, unpooled estimates as conclusions.
- An insight is automatically marked "recheck" when a regime change touches its feature.

---

## 9. Libraries, compute and build order

**Stack** [FC]:
- Python 3.13;
- NumPyro 0.22.0 + JAX 0.12.1 (alternative: PyMC 6.4 + nutpie);
- ArviZ 1.3.0, with LFO-CV written by hand;
- statsmodels 0.15.0 (baselines, `UnobservedComponents` for BSTS/ITS [J]);
- scoringrules 0.12.0 (not properscoring);
- ruptures 1.1.10;
- polars;
- optionally MAPIE 1.6 and bambi.

**Embeddings and labels:**
- Qwen3-VL-Embedding-8B (about 16 GB in bf16, runs on a 48 GB Mac), or a small text-embedding fallback.
- Haiku 5.5 batch labelling at about $0.00004 per post [TAPI].

**Compute**
- The joint NUTS fit, once per run, covers about 20–40k posts and 200 groups, on CPU, in minutes to tens of minutes. A run should budget up to about 30 minutes end to end [J].
- Draft and nowcast scoring uses cached draws or closed-form regressions, in milliseconds.
- Monte Carlo follower forecasts (4,000 paths) take seconds.

**Phased build of the modelling code:**

| Stage | Ships |
|---|---|
| **0 posts** | Measurement layer, batch collector, and the maturity curve (from its prior until peers arrive, then fitted on peers). Feature pipeline including embeddings and labels. Peer backfill. Change tracker and regime registry. Joint peer fit giving prior-only forecasts. Nowcast baseline trained on peers. Breakout ξ and u from peers. Follower model from the peer prior. Per-post surprise. Goal status (`insufficient_owner_data`). Guardrail engine. Experiment engine with randomization, frozen treatment-blind forecasts, power calculator and readouts; E01–E03 start once the engine ships (plan M3). D0. |
| **20 posts** | Owner intercepts and dispersion. D1 and D3 live. Goal status with owner data. LFO-CV harness. First promotion review. Breakout labels calibrated on peers. |
| **60 posts** | Owner deviations for type, format, link and video. D2. Follower DGLM (from 4 weeks). Factor breakdowns with pooled effects. Switchback experiments. Suppression HMM in shadow mode. |
| **150 posts** | Interactions, hour splines, cluster effects. Full suppression HMM. (Two-phase nowcast challenger only once hosted.) Bandit mode with AIPW. Off-policy evaluation. Quarterly e-BH audit. |

---

## 10. Open questions and assumptions

| ID | Item | How to resolve |
|---|---|---|
| A1 | Does a brand-new post get a Home view entry, and does HOME_COLD retrieve posts with zero likes? [HM][PX] | Owner TTFL and impressions-at-2h distributions, which need owner analytics buckets in batch mode |
| A2 | Live config: the nine missing weights, DPP top_k, cold-start overrides | tracker; regime jumps absorb |
| A3 | Whether `/2/tweets/analytics` is available and what it costs | probe on day 1; follower model drops to L2 or L3 without it |
| A4 | K, the number of competitors for the cold-start slot, assumed 20–50 [J] | fit within the challenger |
| A5 | Thresholds for suppression entry hazard and durations [J]; label thresholds are redacted [SF] | probes; revisit after the first incident |
| A6 | Whether quotes get the in-network factor ×1 (C2) [J] | E04 residuals |
| A7 | Priors on owner-only rates are placeholders with no evidence behind them | replace at 20 posts |
| A8 | β on X under the 2026 ranker has no verified estimate [POP] | the peer panel identifies it |
| A9 | Link prior −0.5 [J] | E02 |
| A10 | Breakout u at the 95th percentile and alarm probabilities 0.3/0.6 [J] | tune in backtests |
| A11 | twitterapi.io legality and continuity; `viewCount` assumed equal to cumulative `impression_count` [TAPI] | weekly cross-check; provider switch |
| A12 | Whether TAIL or ZERO_FAV retrieval is served in Home (small-author window) [PX] | watch tracker diffs |
| A13 | Whether Premium is an implicit Phoenix feature [HI] | E12 |
| A14 | Whether to start Premium at launch (reputation shield against enforcement) or after week 9 (gives a causal test) | **owner decision** |
| A15 | The peer-panel prior may not transfer to an account growing from 0 followers | prior–data conflict check; robust mixture |
| A16 | LinkedIn and Instagram horizons, cold-start mechanics and observable metrics are unknown; profiles start null or weak | separate research needed |
| A17 | Peer accounts' Premium status confounds the peer prior | stored as a covariate |
| A18 | Reply ordering in conversation view, and Grox reply scores (prompts withheld) | E10 measures the downstream effects only |
| A19 | The maturity curve's shape, especially growth after 48h, and whether it differs by type, band or regime [J] | fitted from repeated captures; gate in §5; owner analytics buckets give an exact check |
| A20 | Run cadence. G ≤ 3 days keeps guardrail delay and maturity capture tight; gaps above about 25 days lose owner non-public metrics for the oldest posts | run reminders; the platform shows the gap and what it costs |
| A21 | As-of forecasts use the follower count from the last run before the post, which is stale by up to one run gap | small at G ≤ 3; never interpolate, because the next run's count contains follows the post caused |
| A22 | Before peers arrive (plan M1), owner forecasts come only from the §2a priors | labelled `prior_only_uncalibrated` and excluded from calibration and D1 until the peer prior exists |
