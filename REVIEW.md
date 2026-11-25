# Review of the Codex research, designs and implementation plan

Date: 2026-11-25. Scope: `IMPLEMENTATION_PLAN.md`, the 74 report artboards and ~50 source-register artboards on the **Reports** page, and the 3 artboards on the **Designs** page of the Social Manager file.

Seven independent reviewers each took one area: statistics and modelling, data access and terms, X ranking and growth evidence, competitors, architecture, designs, and the source register. Each checked claims against primary sources (docs.x.com, the `xai-org/x-algorithm` repo, PyPI and npm, vendor pages) or recomputed the numbers. I re-checked the highest-impact claims myself; they are marked ✔.

---

## Verdict

The research is careful about provenance, and most of the arithmetic reproduces. But the work answers a narrower question than the one asked, and some of its blocking assumptions are wrong.

1. **It blocks the things you asked for on incorrect premises.**
   - Peer and competitor tracking is deferred behind "rights qualification". X's own docs list "Competitive analysis: compare accounts, benchmark performance" as a use case for the API ✔.
   - Automated owner capture is replaced by about 212 minutes of manual capture over 4 weeks. Owner reads cost $0.001 per post and are deduplicated per UTC day ✔.
2. **It never studied the current X ranking code.** X open-sourced its For You ranker (`xai-org/x-algorithm`, Apache-2.0) in January 2026 and last synced it on 2026-11-25 ✔. The reports cite the repo but contain no weights, no cold-start mechanism and no filters. These are the most useful facts available for growing a new account.
3. **The model doesn't meet the brief.** There is no follower scaling, no cold-start prior, no factor model (topic, format, timing), and no operational "working / near threshold / not working" rule or "thresholds are wrong" detector. The post baseline will drift low as you gain followers, so almost everything will read "above expectation". That is the exact failure you asked the system to catch.
4. **The plan is over-engineered where it doesn't matter and under-specified where it does.** It specifies multi-tenant row-level security, lease fences, grant generations, revocation replay after backup restore, and an uncertain-debit ledger guarding cents. It specifies no schema, no tool signatures, no frontend stack, no labelling pipeline, no scheduler, no dashboard queries, and no milestone durations. It gives you nothing on your real account for weeks.
5. **The designs show a data-capture pilot, not a growth dashboard.** About 70% of the required surfaces are missing.

---

## 1. Inaccuracies

Severity: **H** high / **M** medium / **L** low.

### Data access and X terms

| Sev | Where | Claim | Correction |
|---|---|---|---|
| H | Plan row 4, `erp`, `iva`, milestone 4 | Peer data stays off until "source, lineage and use rights" are verified; the reports cite "restrictions on benchmarking". | ✔ docs.x.com/what-to-build lists **Competitive analysis** as a use to build. The policy's "X performance benchmarking" and "Measuring the X service" clauses ban measuring X itself (uptime, DAU, posts per day), not comparing accounts. The real limits are: redistribution (IDs only to third parties), deletion compliance within 24h, no off-X identity matching without a basis, no training or fine-tuning foundation models, and no off-X ad targeting. Peer tracking through the official API for private analysis can ship in v1. |
| H | Plan, milestone 4 (hosted beta) | Not addressed. | Developer Agreement §III.L/M: pay-per-use covers "applications with a limited number of end-users". Beyond that, an Enterprise plan is required. **This** is the real gate on a public multi-user product. |
| M | Plan, Collection step 5 and the cost model | Prices reads at "$0.015 for one user + one post". | The sum is right ($0.010 + $0.005), but the plan omits ✔ **Owned Reads at $0.001 per resource** (your own timeline, mentions, followers; in effect since 2026-04-20). It also omits that **deduplication per UTC day** makes hourly polling cost the same as one daily read. Estimated cost of fully automated own-account capture: **≈ $1–3 per month**. 50 peers at 3 posts/day with 1h/6h/24h/72h/7d checkpoints: **≈ $105–130 per month** at official prices. |
| M | Plan step 5 | "Request `offline.access` only for authorized scheduled refresh." | Without `offline.access`, X OAuth 2.0 tokens expire after **2 hours**. Scheduled 24h and 72h capture is the core feature, so the scope is required from day one. |
| M | Plan, Experiment step 2; pilot gate | Locking a 72h impressions measure is uncertain; otherwise run a "process-only rehearsal". | `impression_count` is in **public_metrics**, which has no age limit. Only non_public and organic metrics (clicks, profile clicks, video quartiles) are capped at 30 days. The gate is overstated. |
| M | `bah`, plan | Post Analytics is footnoted as Enterprise-only. | `GET /2/tweets/analytics` returns **hourly/daily buckets including follows and unfollows per post**, which is per-post follower attribution. Add a probe for whether your app can call it. If it can, it is the best owner data source available. |
| M | `ike`, `iva` | The vendor survey covers only enterprise suites. | It misses the cheap unofficial APIs (TwitterAPI.io ≈ $0.15/1k tweets, SocialData, Apify). It also misses the route the earlier ledger actually used: free `api.fxtwitter.com` JSON plus Wayback CDX. List these as an explicitly ToS-risky fallback. X's ToS since 2026-10-09 forbids scraping without permission. |
| L | `vim` | The native CSV export gives 30 days and 3,000 posts. | Secondary sources agree, but help.x.com could not be fetched. Whether a **free** (non-Premium) account gets analytics or export is contested. Don't assume it. |

### X ranking and growth evidence

| Sev | Where | Claim | Correction (✔ checked against code at commit 52d4598) |
|---|---|---|---|
| H | `oho` | Ranking code "suggests tests, not lift estimates". No weights are given. | ✔ `home-mixer/params/param.rs` sets reply 5.0, repost 1.0, open link 0.2, click 0.274, share 2.118 (copy-link share 20, DM share 5), and video-quality-view 0. The reviewer also read follow-author 4, like ≈0.71, a +15 reply boost for mutual follows, and negative weights: not interested −53, mute −59, block −31, report −234. The weights multiply **predicted probabilities**, not counts. |
| H | `oho` | "Cold start is conditional" (no detail). | ✔ `author_cold_start.rs`: for authors with ≤50k followers, an **original** post (not a reply or repost) under **2 hours** old with under 200 Home impressions can be lifted to about rank 15. One post per feed request gets this. Selection is by Thompson sampling on likes per view with a Beta(0.75, 49.25) prior. **This is the main mechanism a 0-follower account can use, and the reports omit it.** |
| H | `man`, `iii` | Reply strategies are designed as reach experiments. | The reviewer found that replies and reposts from accounts the viewer doesn't follow are filtered out of For You (`OONRetweetReplyFilter`). Reply-guy tactics earn reach only through the conversation view and profile clicks, not For You. Experiments must measure profile visits and follows, not impressions. |
| M | `bar`, `our` | A 1.32× video association. | There is no explicit video bonus (vqv weight 0, popular-video carousel off). Video can only help through predicted dwell time. Frame the 1.32× as a dwell effect, not a boost. |
| M | `bar` | Link penalty, citing vendor associations of −23% and −41%. | The scoring code has no link penalty (open-link weight +0.2). X's head of product and Musk both said in October 2025 that links aren't de-boosted. Present this as "conflicting evidence, test it", not as a penalty. |
| M | All reports | Premium is never discussed. | The current scorer has no Premium multiplier. The `user-cred-v2` reputation PageRank seeds from Premium and verified accounts, and X Help lists reply prioritization by tier. Buffer's observational study of 18.8M posts found about 10× median reach for Premium (confounded). This is a first-order decision for a new account and should be tested. |
| M | `res` | Breakout is defined over 7 days. | `AgeFilter` drops posts older than **48h** from For You. Split breakout metrics at 48h. |
| L | Practitioner claims | The 2023 heavy-ranker weights (reply 13.5, author-engaged reply 75) are not flagged. | They are superseded. The current code has no author-engaged-reply term. Mark them outdated. |
| L | `man` | Table row "7,311 randomized participants, +3.3 pp". | Matches no cited source. The cited Hutto et al. (CHI 2013) is observational (507 users). Its useful findings went unused: informational content had a positive coefficient on follower gain (the paper's "30×" compares standardized coefficients and is not a growth multiplier), and heavy hashtag use meant fewer new followers. |
| L | `nep` | Pfeffer half-life. | About 80 minutes is the median half-life of **all** impressions, not of 24h views. |

### Use of the earlier ledger

| Sev | Where | Claim | Correction |
|---|---|---|---|
| H | `our`, chat, `bar` | "None of the 2,682 recent posts is text-only, so text vs media can't be isolated." | ✔ It's a **labelling bug** in `methods/xcontent.py`, which sets `format = "link" if embed_card else "text"`. 463 of the 529 recent "link" posts (88%) have no URL in their text. All 8 re-queried posts were plain text. There is therefore a text sample of about 460 posts. The ledger's own finding f08 ("link posts 7.0× top-post lift") is also invalid. Codex flagged the zero as "unverified" instead of reading the 20-line script. |
| M | Reports overall | — | These ledger findings were left unused despite being directly relevant: **f03** (weekly views per follower rise with posting cadence, then plateau, 0.27 → 0.89 → 1.06 → 1.05), **f09** (demos reach 18–20× account median vs 10–11× for launches), **f05** (accounts under 5k followers get 0.47 views per follower vs 0.23 above 100k), and quote posts (0.90–0.95× baseline, yet 52% of Wonder's posts). |

### Statistics

| Sev | Where | Claim | Correction (recomputed) |
|---|---|---|---|
| H | Plan, forecast table row 1 | The post baseline is the median of "compatible mature counts". | There is no follower scaling. For a growing account, the baseline lags and nearly every post reads "above expectation". The follower term exists only in the challenger on `any`. |
| M | Plan row 2, `are` | 64/3,000 rejections under a true 25% lift; "at 5%". | It reproduces (1.9–2.3%), but the **false-positive rate is 1.5–1.8%**. With 6 pairs the smallest two-sided p is 2/64 = .031 and the next is .0625. So the test rejects only if all 12 slots are captured and all 6 pairs agree. Power is capped at 54% even for an infinite effect, and it is 6.2% at a 2× lift. State plainly: "this design cannot detect efficacy". |
| M | Plan, follower forecast | Persistence plus median past net change. | Both are biased low on a convex launch-stage path. Codex's own `fcc` example shows persistence MAE 72.3 and drift still 29–31 low. |
| L | Plan row 3, `mag` | Exact binomial "at a nominal 5% level". | The level actually reached is 1.94% (2.6%, 4.3% and 3.4% for 30, 60 and 100). The power values reproduce. Report the size actually achieved. |
| L | `mow`, `usa` | "Two successive low misses in 51–53% of stable 26-week runs". | 26 independent forecasts at 10% give 20.6%. 51% corresponds to about 78 forecasts (3 per week). State the unit. |
| L | Plan, Forecasts section | The remainder "came close to the richer count challengers". | On `mia` it **won**: WIS 2.3741 vs 2.3742 hurdle and 2.3794 NB, with narrower bands. |
| L | `zee`, last row | — | The trend and persistence results sit under the "Shared-shock / Independence" column headers, so the columns are mislabeled. |

These reproduce exactly: the `mag` Wilson intervals and power values, `vum` sample sizes (256/205/144/103) and weeks (180/152/106/76), `irc` 12.6% error with four looks, `man` p = .125, `gee` effective sample sizes 6 and 36, the binomial 0.0194 / 11.8%, and the weighted interval score and NB2 formulas.

### Architecture and MCP

| Sev | Where | Claim | Correction |
|---|---|---|---|
| M | Plan, Architecture | "The architecture report exercised concurrency… PostgreSQL is the implementation choice." | PostgreSQL never ran, so the concurrency design has only been tested on SQLite. `was` and `had` still specify **SQLite and one worker**, so the reports and the plan disagree. PostgreSQL 19.0 shipped on 2026-11-19; pinning 18.7 is fine, but say why. |
| L | Plan, MCP contract | "Start with stateless legacy mode." | This isn't an SDK term. The SDK has `stateless_http=True`, which affects only 2025-11-25 clients (2026-07-28 connections are already sessionless). It also removes server-to-client messages (elicitation, notifications) for legacy clients, which the plan doesn't mention. |
| L | Plan, MCP contract | MCP Tasks not mentioned. | Tasks moved to an extension in 2026-07-28. The plan's job-ID plus `get_job` polling matches SEP-2567's recommendation. State that as a decision. |
| L | Plan, Review section | "Five independent reviews found the design viable." | These were AI reviews of a database design that was never run. Present them as such. |

All URLs the plan cites return 200, apart from legal.x.com, which returned 403 behind Cloudflare. Correct: MCP spec revisions 2026-07-28 and 2025-11-25, `mcp` SDK v2 (2.0.0 released 2026-07-28, now 2.3.0), running the session manager in the parent lifespan, RFC 9728 / 8707 / PKCE-S256 / audience checks, PostgreSQL RLS bypass rules, `SET LOCAL`, and `SKIP LOCKED`.

### Competitors

The vendor facts mostly check out (Orbin, NextCounts, Hootsuite, Typefully, Fedica, Sprinklr, Doomers, Brandwatch, Meltwater). Minor slips:
- X-Predict's 72h curve is on its /about page, not the /how-it-works page cited, and its "S-grade accuracy" claim is in-sample.
- Typefully's last refresh is on day 3, so it cannot supply 7-day outcomes.
- "Twitter Growth Optimizer" is hobby-grade.
- Pinlyx never claims licensed data.
- Sprout ViralPost doesn't confirm X support. Its useful idea is falling back to a global prior when data is sparse.

Material omissions:
- **X's official MCP server** (June 2026), plus MCPs from Metricool, Buffer, Typefully, Hootsuite and SuperX, and experiment MCPs from GrowthBook, Statsig and PostHog. An MCP for data access is no longer a differentiator.
- The X-tool market is contracting: Black Magic shut down in July 2026, Hypefury dropped X posting, Shield is winding down, Spotter closed.
- Native platform A/B tests elsewhere (Instagram Trial Reels, YouTube Test & Compare). X has none.

### Source register

See section 5.

---

## 2. Gaps against what you asked for

Ranked by impact on your stated goals:

1. **No cold-start path.** Pre-publication forecasts abstain until there is owner history, and peer data is blocked. For a 0-follower account the product does nothing for months.
2. **No factor analysis.** You asked to explain performance by topic, format, tags/mentions and timing. The plan defers it as "predictive associations", and there is no pipeline for labelling topic and format.
3. **"Working / near threshold / not working" never becomes a rule, and there is no "thresholds are wrong" detector.**
4. **No reach model linking followers to impressions, profile visits and follows.** So the plan cannot forecast account growth or answer "what if I post 3 vs 5 times a day?".
5. **The experiment design discards most of the sample.** It allows 3 demos a week, slot pairs, a 5% test and a 25% effect. At 3 posts a day randomized per post for 4 weeks, with the pre-publication forecast as a covariate, power is ≈ 63% for a 1.5× lift and 98% for a 2× lift. The levers worth finding for growth are large ones.
6. **Virality is deferred to milestone 5.** There is no tail model and no early-breakout alert, although tail coverage was only 73.5% on `los`.
7. **No public journal**, even though building in public is the plan for your account. It is excluded from the first release and never scheduled.
8. **No agent visibility.** The designs and the plan have no MCP activity log, conclusions library or approvals queue. Observational insights have nowhere to live, because conclusions must be tied to an experiment.

---

## 3. Recommended improvements

### 3.1 Data collection

- **Owner:** register one developer app owned by your X account. Poll `GET /2/users/:id/tweets` with `public_metrics,non_public_metrics,organic_metrics` every 15–60 minutes for posts under 8 days old, then daily to day 30, at Owned Read rates. Read `users/me` daily for followers. Probe `/2/tweets/analytics` on day 1. Request `offline.access`. Mark missed windows as late and never backfill them.
- **Peers:** ship in v1. Declare "competitive analysis" as the app's use case. Discover new posts with `since_id` timeline polling or an X List, set a monthly spend cap, keep the data private, honour deletions within 24h, and do no off-X joins.
- **Backfill:** the native CSV export (if your account has access), and a corrected re-labelling of the earlier ledger.
- **Fallback:** unofficial APIs, explicitly labelled as a ToS risk.

### 3.2 Modelling

Combines the statistics reviewer's design with the ranking-code findings.

- **Post model (hierarchical Bayesian NB, per metric, at fixed age):**
  - `y ~ NB2(μ, φ)`, `log μ = α + β·log(1+F_t) + x'θ + u_week`
  - `x`: format, topic, mentions/hashtags, link, local hour and day, posts in the last 24h, whether it's an original.
  - `θ` is partially pooled toward the peer or platform prior.
  - `u_week` is a random walk for shared shocks; Codex's `zee` showed these are needed.
  - Engagements are modelled as rates given impressions.
- **Cold start:** fit the same model on a peer panel of 50–200 niche accounts in your follower band, and use the posterior as your prior. Without peers, use weak priors. Then update in stages:

  | Stage | Matured posts | What gets estimated |
  |---|---|---|
  | 0 | 0 | prior only |
  | 1 | ≥20 | owner level |
  | 2 | ≥60 | format effects |
  | 3 | ≥150 | interactions and hour splines |

- **Nowcast:** keep Codex's empirical remainder as the baseline; it won its replays. Challengers:
  - Szabo–Huberman log-linear updating at 1h/2h/6h/24h. The **2h** checkpoint matters because of the cold-start window and the 48h For You cutoff.
  - A self-exciting (Hawkes) model once repeated captures exist.
- **Account model:** a dynamic NB (West–Harrison):
  - `follows_t ~ NB(exp(η_t + κ·log(1+Imp_t) + ψ·ProfileVisits_t + ω'Activity_t))`, plus unfollows.
  - Forecasting followers means simulating joint paths using the post model's impression draws. This also enables "what if" scenarios.
- **Status rule:** for each post, compute the randomized PIT of the outcome under its frozen forecast (its percentile within the forecast distribution).
  - **Working:** above 0.8, or P(goal met) > 0.7.
  - **Not working:** below 0.2 and P(goal met) < 0.3.
  - **Near threshold:** otherwise.
  - For levers, use P(θ > practical δ), with "near threshold" when the 80% credible interval straddles δ.
- **"Thresholds are wrong" detector:**
  - CUSUM/EWMA on Φ⁻¹(PIT), tuned to about 100 posts between false alarms.
  - A PIT histogram test over a rolling window.
  - If more than about 70% of recent posts are "not working" across all formats, flag the model or goal as miscalibrated rather than the content.
- **Experiments:**
  - Randomize per post, stratified by slot, with the frozen forecast as an offset (CUPED-style; `vum` showed a 30–50% variance cut).
  - Use switchback blocks of 2–3 days for schedule, Premium and activity levers.
  - Use Thompson sampling with a propensity floor of 0.2 for format and time allocation, logging propensities for later evaluation.
  - Control multiplicity with hierarchical shrinkage plus online FDR.
  - Model breakouts with peaks-over-threshold on log(y/μ̂).
- **Track Phoenix's action types separately:** replies, quotes, shares (copy-link and DM), profile clicks, follows, and negative feedback as a guardrail. Don't collapse them into one "engagement" number.

### 3.3 First hypotheses for a new account

Taken from the ranking code, the ledger and the literature. Treat these as direction-finding, not proof.

1. Early like rate in the first 2h drives cold-start reach. Compare peak-hour and off-peak originals.
2. Original-heavy weeks vs reply-heavy weeks for follower growth, at equal time spent.
3. Replies to 5k–50k-follower peers vs mega-accounts. Measure profile visits and follows per reply, not impressions.
4. Effect of mutual follows with peers on in-network reach (the +15 mutual-follow reply boost).
5. Narrated vs silent demos, measured by dwell, profile clicks and follows. Keep the pilot, but auto-capture it.
6. Link in the post vs link in a reply. X says there is no penalty, so test it.
7. Premium on vs off, in 4-week blocks. This is confounded with growth, so label it as such.
8. 1–2 posts a day vs 3 or more. Motivated by the same-author decay of 0.5 with a floor of 0.25, and the f03 plateau.
9. "How I did X" craft posts vs product posts, measured by shares and follows.
10. 0 vs 1 hashtag.

### 3.4 Plan restructure

Replace the plan with a buildable spec. The architecture reviewer's skeleton:

- **Stack** (versions verified today; pin them at the spike):
  - Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2.1 + Alembic, psycopg 3.
  - PostgreSQL 18.7 in docker-compose.
  - **Procrastinate** (a Postgres job queue with periodic tasks) instead of hand-rolled leases.
  - `mcp` 2.3.0, polars, statsmodels and PyMC/NumPyro.
  - Vite + React 19 + TanStack Router/Query + ECharts or Observable Plot, with types generated by openapi-typescript.
- **Core tables:**
  - `accounts`, `posts` (kind, media type, link, mentions, hashtags, raw JSON)
  - `post_snapshots` (age in minutes, all metrics, source, late flag), `account_snapshots`
  - `label_sets`, `post_labels` (source: rule, LLM, owner or agent)
  - `activities`, `collection_jobs`, `api_usage`, `expected_ranges`
  - `experiments`, `experiment_units`, `readouts`
  - `insights` (observation, experiment or forecast-evaluation; draft, approved, superseded or withdrawn)
  - `journal_entries`, `agent_clients`, `audit_log`
- **MCP:**
  - Read tools: `get_account_overview`, `list_posts`, `get_post`, `breakdown(metric, age_h, group_by, min_n)`, `get_expected_range(post | draft_features)`, `list_insights` / `get_insight`, `list_experiments` / `get_experiment`, `get_job`.
  - Write tools: `request_collection(targets, fields, checkpoints, max_cost_usd)` returning a job or an approval request, `set_post_labels`, `draft_experiment`, `record_experiment_event`, `run_readout`, `write_insight`, `draft_journal_entry`.
  - Resources: `sm://playbook` (digest of approved insights), plus metric definitions.
  - Prompts: `weekly_review`, `design_experiment`, `conclude_experiment`.
  - Approvals, experiment activation and paid scope stay in the web UI.
- **Labelling:**
  - Deterministic at ingest: media, link, mentions, original/reply/quote/thread, local hour and day.
  - Topic: an LLM classifier against a versioned taxonomy you can edit.
  - Owner overrides in the UI; agents relabel through MCP.
- **Defer:**
  - RLS and hosted OAuth, grant generations, lease fences, revocation replay, the debit ledger (use X's spending cap plus `api_usage`), forecast release policies, efficacy trials, and LinkedIn/Instagram.
  - Replace the seven-way permission matrix with two flags, `share_with_agents` and `publishable`.
  - Keep the good discipline: freeze forecasts at issue, as-of cutoffs, code missing separately from zero, pre-register experiments, record deviations.
- **Milestones, cut by what you get to use:**

  | When | Deliverable | What you see |
  |---|---|---|
  | Days 1–3 | X OAuth, backfill own posts | Your real data on screen |
  | Weeks 1–2 | Scheduler, snapshots, overview and post-detail charts, read-only MCP | Agents can analyze your account |
  | Weeks 3–4 | Topic labels, breakdowns with n and intervals, expected range and status per post, journal export | First build-in-public post |
  | Weeks 5–6 | Experiments end to end, write tools, insights library | Narration pilot runs as experiment #1, auto-captured |
  | Weeks 7–8 | Peer collection with cost preview and cap, peer-based priors | Comparisons and cold-start priors |
  | Later | Bayesian model, bandit allocation, breakout alerts, hosted beta (behind the Enterprise-terms check) | — |

---

## 4. Designs

### Current artboards

- **Growth Dashboard Analysis:** the whole screen is a 2019 repost benchmark replay, not your account, with an unrelated 2026 follower series next to it. The forecast is one box at 72h rather than a band along the trajectory. The out-of-band result is grey text. "Coverage 92.8% vs nominal 80%" means the intervals are too wide, but the screen never says so.
- **Owner Dashboard Capture:** the "Post capture plan" chart is five identical bars of 12, which is filler. Almost every panel is empty or pending, and no populated state exists.
- **Experiment Suggestions And Tracking:** the strongest screen. But the Planned row shows "—/—/12" where it should be 6/6/12. It shows an "Estimated lift" KPI for a test with ~2% power. The funnel is all zeros. It has no conclusions library and no labels saying whether an agent or a human wrote each item.

### Missing everywhere

- Post explorer with factor columns
- Topic × format matrix and day × hour heatmap, with n badges
- Followers vs forecast band
- Status pills
- Collection job queue for own and peer accounts
- Agent/MCP activity log and approvals
- Conclusions library
- Public journal

### Tokens

- There are three token families (`report/*` warm light paper, `ledger/*` green-tinted, `dashboard/*` cold grey), and the dashboard borrows `ledger/sans` and `ledger/mono`.
- There is no green token, so status has no colour.
- The forecast band (`#213653` on the plot) has 1.53:1 contrast and nearly disappears.
- `dashboard/faint` reaches only 4.50:1 on raised surfaces, which fails for 11px labels.
- Series colour `#a0a8b5` is nearly identical to muted text `#a4aab4`.

### Reports

- They are clean and cited, but they are text tables of hedged prose with no charts.
- There are three numbering schemes and no index across about 124 artboards.
- Restructure as a 2-slide executive summary plus 6 themed decks: data access, what drives performance, forecasting, experiments, market, build plan. Each slide gets an assertion headline, one chart, a three-line "so what", and a confidence tag. Put sources in an appendix with IDs that the slides cite.

### Recommended information architecture

1. **Overview:** KPI strip, followers with forecast band, posts with status, agent feed.
2. **Posts:** explorer table plus a side drawer with the post's trajectory and band.
3. **Patterns:** format × topic, day × hour, tags and mentions, cadence; all with n badges.
4. **Account:** followers vs forecast, schedule calendar, activity.
5. **Experiments:** suggestions (author labelled), tracker, readout with interval, conclusions library.
6. **Accounts & Collection:** own and peer accounts, collect-now, job queue with cost.
7. **Agents:** connected clients, tool-call log, approvals.
8. **Journal:** public-ready weekly entries generated from approved insights.

---

## 5. Source register

The register links are mostly sound. The weak points are how the URLs are counted and how hard they are to use.

**Counts**
- The "427 distinct public URLs" figure is 427 distinct URL strings. They correspond to about **403 distinct documents**: the X Developer Policy appears 7×, the Nature article 4×, and at least 8 papers appear once as arXiv and once as DOI.
- The closeout pages (`sun`, `tur`) add 24 URLs that the 427 leaves out, and `mag` cites two of them.
- `rib` says 243 in its header, but its category counts add up to 209.
- `nam` says "24 artboards", but there are 51 report artboards.
- Source keys are reused across different URLs: R1-153 appears 4×, R2-a865163c 5×.

**Status of 451 URLs:** 372 OK, 72 bot-blocked with a 403 (help.x.com, paywalled DOIs; probably fine but not machine-verifiable), 3 redirect to different content, 1 bad anchor, 2 dead. All 32 arXiv IDs exist, and 61 Crossref DOIs match their title and authors.

**Errors to fix**

| Artboard | Problem |
|---|---|
| `yon` | The Lambert 1992 DOI `10.1080/00401706.1992.10485228` does not exist. The real one is `10.2307/1269547`. |
| `gps` | `chenhaot.com/papers/wording-for-propagation.html` returns 404. Use `/papers/wording-for-propagation/`. |
| `tig` | The Nature link uses anchor `#Sec21`, but the article's sections run only to Sec17. |
| `wet` (2 rows) | arXiv 1702.06673 is titled "Cascades: A view from Audience", not "The Importance of the Retweet Cascade". |
| `arm` | DOI 10.1080/01621459.1994.10476818 is titled "Estimation of Regression Coefficients When Some Regressors are not Always Observed". |
| `amp`, `com` | Each shows a paraphrase instead of the real title. |
| `sun`, `too`, `its` | Each link redirects elsewhere: a different Brandwatch article, the current Facebook docs instead of the pinned version, or a different LinkedIn docs version than the label says. |
| `can` | The Sprinklr PDF URL is malformed. |

**Do the sources support the claims?** Of 10 cited claims spot-checked, 7 are fully supported, 2 partly and 1 cannot be verified.
- **Partly:** "fitting models on X data is gated" (`kim`). The agreement bans training foundation or frontier models, not small statistical fits.
- **Partly:** SEISMIC cited for a negative-binomial model (`any`). SEISMIC is a self-exciting point-process model.
- **Unverifiable:** the "36h to stabilize" export claim (`vim`); the page is bot-blocked.

**Usability against "all sources readable and seen on the canvas"**
- No URL is a link. All are plain text, so nothing can be clicked.
- Report citations are **10px** (on `kim`, `oho`, `any`, `are`, `bah`, `vim` and others), too small to read at normal zoom.
- Reports have no inline citation markers. Each lists 2–4 sources at the bottom without saying which claim each supports.
- Register entries don't say which report uses them.
- The register sits more than 14,000px away from the reports on the page.

**Fix:** give every URL its own key, add numbered inline citations, use real hyperlinks at 12–13px or larger, add a "used in" field to each entry, and mark bot-blocked pages as "manually verified" rather than "verified".

Full per-URL table: `/private/tmp/claude-501/-Users-nemanjagemovic-wonder-workspaces-social-manager-lemming/75c937fa-0b41-446b-b96e-cd0b406e2022/scratchpad/sources/url_status.tsv`

---

## 6. Keep from Codex's work

- Separating "goal met" from "outside the forecast band", with low and high misses tracked separately.
- Forecasts frozen at issue time with the exact inputs they used. Late labels marked missing. Missing coded separately from observed zero.
- Evaluation design: weighted interval score, coverage and width, paired comparison against a baseline, chronological holdouts, caution about overlapping windows.
- The empirical remainder as the nowcast baseline.
- The finding that independence undercovers aggregates (53.7% vs 80%), and the shared-shock joint paths.
- Pre-registration, deviation logs, and the warnings about peeking (12.6% error with four looks) and outcome-linked missingness.
- Honest labelling of the historical archives as method benchmarks only.
- The import quality audit (zero impressions with positive engagement) and the timestamp discipline in the evidence records.
