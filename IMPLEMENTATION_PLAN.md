# Social Manager: implementation plan

This plan is for building the platform. The maths lives in [`docs/MODEL.md`](docs/MODEL.md) (Model v2), the evidence lives in `docs/research/`, and the review of the earlier plan lives in [`REVIEW.md`](REVIEW.md). The screens are on the canvas, Designs page; each section below names the screen it serves.

Last revised 2026-11-25, revision 4.

**Revision 4: batch runs.** The owner decided that v1 runs every few days rather than continuously. That changes:
- collection, now a run that captures everything since the last run;
- outcome measurement, now through a maturity curve;
- forecast freezing, now live for planned posts and as-of for unplanned ones;
- the follower model, now fed per run interval;
- costs, recomputed;
- the milestone clock, now explicit;
- defaults and deadlines for every open decision.

It also states the location CUSUM as two-sided with an exact threshold, and pins PostgreSQL with a reason.

**Revision 3** applied an independent correctness review:
- forecast-freeze timing and planned slots;
- synchronous labelling;
- detector thresholds;
- release gates;
- milestone order;
- approval assurance;
- legal framing.

Where this plan and MODEL.md differ, MODEL.md governs the statistics.

---

## 1. What we are building

Social Manager is a local-first analytics and experimentation workbench for one X account. Other platforms come later.

**Operating mode (v1): batch runs on the owner's machine.** The platform is not a service that stays up. Every few days the owner, or their agent, starts a run (`socialmgr run`, or the `start_run` MCP tool). A run:
1. collects everything new since the last run;
2. labels new posts;
3. freezes the remaining forecasts;
4. scores posts that have matured;
5. refits and evaluates the models;
6. updates the monitors;
7. writes a briefing.

Between runs, the agent can still start the platform on demand over stdio, to read data or register planned posts. Hosting it continuously is a later phase (M7).

**Agents are first-class users, not an add-on.** Everything the platform knows or can do is available to the owner's external agents (Claude Code, Codex, any MCP client) through the MCP server, with the same depth as the web app. The owner never has to open the dashboard. They can work entirely in their agent's chat: "how did this week go?", "what should I test next?", "is my forecast still trustworthy?". The agent answers from the platform's data, analysis and experiment records instead of guessing.

Used this way, the platform is a **grounding and tracing layer** for the owner's agents:
- It gives them measured facts, calibrated forecasts and randomized evidence to reason from.
- It keeps a record of what each agent read, proposed and concluded, so any claim can be traced back to the numbers behind it.

This matters most for owners who are not numbers people: they get the benefit of the maths without reading a PIT histogram. The web app remains for owners who want to look, and for the public journal.

**What the platform does:**
1. **Collects.** At each run, the owner's posts, metrics and follower changes, plus the public posts of a panel of peer accounts.
2. **Freezes forecasts.** A forecast is frozen for every planned post when it is registered. For experiment slots, a treatment-blind forecast is frozen before the arm is drawn. Unplanned posts get an as-of forecast rebuilt from pre-post information only, which anyone can recompute.
3. **Scores and tracks goals.** Each post is scored against its forecast, and the platform shows whether the account is on track for its goals, kept as a separate question.
4. **Experiments.** It runs small randomized experiments on the levers the owner controls, and reads them out honestly, including "we can't tell yet".
5. **Agent access.** It exposes all of this to external agents through an MCP server with full parity:
   - read everything;
   - run every analysis;
   - propose, schedule and read out experiments;
   - manage peers, goals and budgets;
   - relay the owner's approvals.

   The server still enforces the hard limits: budget caps, owner confirmation for consequential actions, frozen forecasts, and no posting or engagement on X (§9).
6. **Journal (optional).** It publishes a public build-in-public journal, written as the owner's personal blog.

**Out of scope for v1:**
- posting to X on the owner's behalf (the owner posts manually, or schedules natively);
- automated engagement of any kind;
- LinkedIn and Instagram;
- multi-tenant hosting.

### Screens (canvas: Designs page)

The screens are one view onto the platform, not the only one. Every panel below has an MCP equivalent (§9.3). Nothing can only be done by clicking, except creating the X developer app and granting OAuth consent, which X requires in a browser.

| Screen | Serves | Built in |
|---|---|---|
| Overview | Status at a glance: KPI tiles against expected ranges, follower forecast, post status, recent posts, agent activity | M1, extended through M5 |
| Posts | Every post with its frozen forecast, outcome, percentile and labels, plus a detail drawer with its trajectory | M1 |
| Patterns | Pooled effects by format, topic, hour and cadence, with observational vs experimental evidence | M2 |
| Collection | Tracked accounts, providers, budget, jobs, spend by job type | M2 |
| Forecasts | Model card and release gates (M2); calibration, drift monitors, X ranker change log (M5) | M2, completed M5 |
| Experiments | Running and concluded experiments, readout, agent suggestions, lever calendar | M3 |
| Account | Next posts with their experiment arms (M3); follower funnel, goal probability, posts that brought followers (M5) | M3, completed M5 |
| Insights | Insight library with evidence level, review state, citations, evidence gaps by lever | M4 |
| Agents | MCP tool-call log, approvals, connected clients, guardrails | M4 |
| Public journal | The owner's personal blog, in the first person: home, experiments index, one page per experiment (hypothesis, design, stats, result), method page, note pages | M6 |

All screens are built from the shared components on the Platform Components board. The React components should mirror that set one to one: `NavItem`, `ExpectationTile`, `PanelHeader`, `ColumnHeader`, the `*Row` components, `Badge`, `StatusPill`, `RangeStrip`, `Meter`, `KeyValue`, `SuggestionCard`, `ApprovalCard` and so on.

---

## 2. Decisions this plan rests on

| Decision | Why | Source |
|---|---|---|
| **Batch runs, not a continuous service (v1).** | The owner will run the platform every few days on their own machine. Hosting comes later. | Owner decision, 2026-11-25 |
| **The official X API for the owner. Peers, search and backfill through one `XDataProvider` interface, with the provider chosen before M2 (§14).** Every need has an official-API path (§5.1). | Owner-only metrics (profile clicks, link clicks, video quartiles) exist only in the official API. Follows per post exist only in `/2/tweets/analytics`, whose availability and price are unverified. Public reads are 25–35× cheaper on twitterapi.io, at a real legal risk (§13). | `docs/research/twitterapi-io.md` |
| **Each workspace brings its own X developer app.** | Owned Reads at $0.001, deduplicated per UTC day, apply only to the developer-app owner. Without BYO keys, owner reads cost 5× more: about $5.50 instead of $1.90 a month at a 3-day run gap. | twitterapi-io.md, verification §3; `cost_model_batch.py` |
| **The primary outcome is impressions at 48h.** The 7-day figure is secondary. In batch mode both are observed at the first run after that age, through a maturity-curve offset; owner analytics buckets, if available, make them exact. | X drops posts older than 48h from For You. Cold start lasts 2h. Runs every few days can't take fixed-age snapshots. | x-algorithm-home-mixer.md, MODEL §1.3 |
| **Forecasts are frozen before they can be influenced, and are never edited.** Planned posts and experiment slots get a live forecast at registration; slots get a treatment-blind one, hashed before randomization. Unplanned posts get an **as-of** forecast: the forecast that would have been made at the end of the last run before the post, from that run's snapshot plus the post's own labels and schedule features, with the manifest hashed. | Status, calibration and experiments all compare against the forecast. A covariate frozen after assignment, or built from features the treatment changed, biases the effect estimate. As-of forecasts can be recomputed and checked, so calibration stays honest without a live service. Experiments use only live forecasts. | MODEL §1.3, §2a, §6.2 |
| **Model v2: a hierarchical NB2 with a peer-panel prior.** It ships prior-only from day 0 and is promoted by LFO-CV and the release gates. | A new account has no history. Peers supply it. | MODEL §2–3, §5 |
| **Status separates "vs forecast" from "vs goal".** Each post gets a mid-PIT; goal status uses ε = log 1.1, p ≥ 0.80 and hysteresis. | The old "PIT > 0.8 or P(goal) > 0.7" rule mixed the two. The "70% of posts" miscalibration rule almost never fires. | forecasting-calibration-literature.md |
| **Experiments use micro-randomization per post, with the forecast as a covariate.** Time levers use 4-day switchbacks; account levers use ITS/BSTS with a randomized start. e-LOND controls multiplicity. | Classical power at 3 posts/day over 4 weeks is 0.21 / 0.50 / 0.92 for 1.25× / 1.5× / 2× at R² = 0.3. The product's "adopt" and "confirmed" labels are stricter than that test (§8). | MODEL §6 |
| **The X ranker change tracker and regime registry ship with the peer prior (M2).** | Peer backfill from before 09-30 may enter the prior only with regime fixed effects. Parameters churn: copy-link went 20 → 0 → 20 within 5 days in November. | MODEL §2e, x-algorithm-history.md |
| **Full agent access: MCP parity with the web app.** | Owners who aren't numbers people should still get the maths. The platform is the agent's grounding and tracing layer. | Owner decision, 2026-11-25 |
| **The server enforces the limits, not the prompt.** Budgets, owner confirmation, frozen forecasts and the no-X-write rule are checked in application services. Approvals use a channel the agent cannot read (§9.2). | Agent mistakes come back in a confident tone, and agents read untrusted third-party text. | MODEL §6.9, §7 |

---

## 3. Stack

| Layer | Choice | Notes |
|---|---|---|
| Language (backend, models) | Python 3.13, managed with `uv` | Every modelling library pinned in MODEL §9 supports 3.13. |
| API | FastAPI + Pydantic v2 | One process serves the REST API, the MCP endpoint and the SPA's static files. |
| MCP | Official Python MCP SDK | stdio for local clients plus Streamable HTTP at `/mcp`. Long jobs return a job ID and are polled. Elicitation is used for approvals where the client renders it (§9.2). |
| Database | PostgreSQL 18.x (pinned to the current 18 minor); SQLAlchemy 2 + psycopg 3; Alembic | Docker, started and stopped by the `socialmgr` CLI with reference counting: a run, `serve` and an MCP session can overlap, and Postgres stops only when the last one exits. Runs and token refreshes take Postgres advisory locks. Pinned to 18 because 19.0 only shipped on 2026-11-19; move to 19 after its first minor release. Append-only tables are enforced in the database (no UPDATE/DELETE grants to the app role; triggers), not only in code. Postgres stays (not SQLite) because M7 hosting needs it and the review settled it. |
| Run orchestrator | One process runs the steps of a run in order, each step idempotent and checkpointed in a `runs` table | A run fixes its collection cutoff time when it starts. An interrupted run resumes from its last finished step within 24h; after that it is closed and a new run starts. A resume after a UTC day boundary re-bills Owned Reads, and the run report shows it. API calls go through a small job table with idempotency keys. No background scheduler in v1. Lease fencing waits for M7. |
| Modelling | NumPyro 0.22 + JAX 0.12.1, ArviZ 1.3, statsmodels 0.15, scoringrules 0.12, ruptures 1.1.10, polars | One fit per run on CPU; a run budgets about 30 min end to end. Scoring uses cached posterior draws. SBC runs per model version. |
| Labelling | Claude Haiku 5.5 (`claude-haiku-5-5`), called synchronously when a slot or post is registered; Batch API only for backfill and relabelling; deterministic rules first | Thinking/effort set explicitly, since it bills as output. ≈ $0.00004 per post assumes about 400 input and 80 output tokens; that figure is an assumption, not a measurement. Owner overrides always win. |
| Near-duplicate check | MinHash at stage 0; embeddings optional later | The near-duplicate guardrail blocks from day one (MODEL §7). |
| Frontend | React + TypeScript + Vite, TanStack Query, uPlot, plain SVG for small charts | Dark, dense, tokens from the canvas `dashboard/*` set. |
| Secrets | OS keychain (or an encrypted file with a key in the keychain) for X tokens, the twitterapi.io key and the Anthropic key; MCP client tokens stored hashed | Secrets are entered in a local form the server opens. They never pass through an agent's transcript. |
| Packaging and host | `socialmgr` CLI plus `docker compose` (Postgres, app). Runs on the owner's laptop. | `socialmgr run` brings everything up, runs, writes the briefing and shuts down. `socialmgr serve` keeps the web app up while the owner looks. The MCP stdio server starts the app on demand. `socialmgr reminders install` adds a tiny launchd/cron entry that reads a `last_run.json` file (written at the end of each run, so Postgres needn't be running) and shows an OS notification once the gap passes the target (default 3 days). It never collects data. |
| Observability | Structured logs, a run report (steps, timings, provider errors, spend, gaps), provider error rates | Shown on Collection and in `get_briefing`. |
| Backups | `pg_dump` at the end of every run **and** at the end of every session that registers slots or planned posts, to a second disk or offsite, from M1; restore tested monthly | Frozen forecasts and assignment receipts cannot be recreated, and receipts made between runs must not wait for the next run. |

### Repository layout

```
socialmgr/
  core/            # domain types, interfaces (XDataProvider, Labeler, ModelService), config (Pydantic)
  providers/       # x_official/, twitterapi_io/, socialdata/, fake/  — adapters only
  collection/      # run orchestrator, batch collector, capture ages, cost ledger, budget guard, deletion sync
  labelling/       # rules, synchronous LLM labeller, batch relabeller, label-set versions, MinHash
  modelling/       # features, maturity_curve/, nb2_hierarchical/, nowcast/, followers_dglm/, breakout/, detectors/, evaluation/
  experiments/     # slots, protocol, randomization, receipts, readout, power, e-LOND
  insights/        # insight store, citations, recheck triggers
  ranker_tracker/  # x-algorithm repo diff, classifier, regime registry
  notify/          # OS notifications, optional email, approval codes
  mcp/             # tool definitions, auth, scopes, audit log
  api/             # FastAPI routers for the web app
  journal/         # public page renderer (static export) and receipt timestamps
web/               # React app; components mirror the canvas component set
migrations/
tests/             # unit, integration (real Postgres), simulation, contract and parity (MCP)
```

Core modules never import provider SDKs directly. Providers implement the `core` interfaces and are chosen in config.

**Time.** Every timestamp is stored in UTC. Each account has one IANA zone. "Day" in blocking and switchbacks means the account's local day. Follower data comes per run interval.

---

## 4. Data model

Every table carries `workspace_id`. Platform IDs are stored as strings, and times as UTC `timestamptz`.

| Table | Purpose and key columns |
|---|---|
| `accounts` | Owner and peer accounts: `platform`, `external_id`, `handle`, `role` (owner/peer), `niche`, `follower_band` (≤1k, 1–5k, 5–50k), `premium` (a covariate), `tracked_since` |
| `planned_slots` | A planned post before it exists: `slot_id`, `planned_at`, `lever_context`, `experiment_id`, `arm` (drawn after the blind forecast is frozen), `match_rule`, `matched_post_id`, `state` (planned/assigned/posted/missed/unmatched) |
| `posts` | `external_id`, `account_id`, `created_at`, `type` (original/quote/reply/repost/self_reply), `conversation_id`, `text_hash`, `minhash`, `has_url`, `media` (jsonb: kind, count, video_s), `text_len_weighted`, `newlines`, `gap_since_prev_min`, `own_posts_48h`, `followers_at_post`, `mutuals_at_post`, `slot_id` |
| `runs` | One row per run: `run_id`, `started_at`, `finished_at`, `gap_since_prev_h`, `steps` (jsonb: status, timing, counts), `spend`, `trigger` (cli/agent) |
| `metric_snapshots` | One row per fetch: `post_id`, `run_id`, `provider`, `fetched_at`, `age_s`, `impressions` (owner: `impression_count`; peers: `viewCount`, tagged), `likes`, `reposts`, `quotes`, `replies`, `bookmarks`, owner-only `profile_clicks`, `link_clicks`, `video_q25..q100` (nullable), `raw` (jsonb) |
| `analytics_buckets` | Owner `/2/tweets/analytics` hourly and daily buckets, including follows and unfollows per post. Populated only if the day-1 probe succeeds; when present they give exact I48. |
| `maturity_curves` | Versioned curve parameters per metric, type and band, frozen with each model version (MODEL §1.3) |
| `follower_snapshots`, `follower_events` | Follower count per account at each run; new-follower and unfollower diffs for the owner per run interval |
| `activities` | Owner actions taken in a real client and logged: replies with target and target tier, follows, unfollows. Used by the follower model's activity covariates, guardrails and E10/E11. |
| `engagers` | Accounts that engaged with owner posts, with follow status, used for the "share of engagers who don't follow us" feature |
| `labels` | `post_id`, `label_set_version`, `field`, `value`, `source` (rule/llm/owner), `confidence`. The latest owner label wins. |
| `forecasts` | Append-only. `subject` (slot or post), `kind` (treatment_blind / live / as_of / prior_only_uncalibrated), `model_version`, `curve_version`, `snapshot_run_id` (as-of only), `metric`, `horizon_h`, `quantiles` (jsonb, for display), `draws_ref` and its hash (1,000 joint posterior draws in an append-only draw store, used for scoring at the actual capture age), `p_ge_goal`, `input_manifest` and its hash, `frozen_at` |
| `scores` | At maturity: `forecast_id`, `outcome`, `capture_age_h`, `log_g_offset`, `exact` (from analytics buckets), `late_maturity`, `pit_mid`, `pit_rand` (seeded by post id), `crps_log`, `in_80`, `in_50` |
| `model_versions`, `evaluations` | The model card, LFO-CV ΔELPD, release-gate results, promotion decisions |
| `monitors` | D0, D1 (location, scale, follower gains), D2, D3 state; alarm history; acknowledgements |
| `goals`, `goal_status` | Goal definitions; status per scope with `p_hi`, `p_lo`, `reason`, `hysteresis_state` |
| `ranker_changes`, `regimes` | Parsed diffs with class, confidence (param.rs vs vm-ranker default), sync-header timestamp, revert flag; regimes seeded with MODEL §2e dates |
| `experiments`, `assignments`, `experiment_events`, `readouts` | Versioned protocol (frozen at start, amendments versioned), seed, per-slot receipts with the blind-forecast hash, deviations, readouts with e-values |
| `insights`, `insight_citations` | Claim, scope, effect, evidence level, review state, `recheck_on`; citations to experiments, posts, code paths and research notes |
| `jobs`, `job_attempts`, `cost_ledger`, `budgets` | Logical jobs, attempts, spend per provider, caps per workspace, per client and per day |
| `mcp_clients`, `mcp_calls`, `approvals`, `agent_decisions` | Hashed client tokens and scopes; every tool call with arguments, returned record IDs, latency and cost; approvals bound to an exact revision, with route and assurance level; agent conclusions linked to evidence IDs |
| `receipt_anchors` | Hash of new forecasts and assignment receipts at the end of every run and every registration session, with where it was published (journal page or git commit), so "receipts predate posts" can be checked externally |
| `journal_entries` | Owner-written or agent-drafted public notes, publish state |
| `audit_log` | Every consequential action, its actor, the approver and the route |

**Retention and deletion:**
- Raw provider payloads are kept for 30 days; after that only the normalised columns remain.
- Peer data stays local.
- **Compliance deletion** is a separate, logged path. When a post is found deleted at a run, or a peer asks for removal, its content and identifiers are removed in that run (or within 24h of a request), while a hash tombstone keeps frozen forecasts and receipts auditable. Between runs nothing is refreshed, so a deleted post's copy can persist for up to one run gap. Whether that satisfies the Developer Agreement's deletion terms is a legal check before M2 (§14).

---

## 5. Collection

### 5.1 Providers

All rows run once per run. The third column gives the official-API path for anything twitterapi.io would otherwise do.

| Need | Provider | Endpoint pattern | Official-API path |
|---|---|---|---|
| Owner posts and metrics | Official X API, owner OAuth 2.0 (`tweet.read users.read follows.read offline.access`; `follows.read` is needed for the official follower path) | `GET /2/users/{id}/tweets` with public, non-public and organic metrics for every post under 30 days old. Verify the rate limit on day 1. | — |
| Owner analytics | Official X API | `/2/tweets/analytics`: hourly buckets once a post is ≥ 7 days old, daily buckets at about 30 days. **Availability and price are not listed: probe on day 1** (MODEL A3). If unavailable, outcomes come through the maturity curve and follows per post are dropped. | — |
| Owner follower count | Official X API | `GET /2/users/me` with public metrics | — |
| Owner new followers and unfollowers | twitterapi.io, or official | `user/followers` newest-first since the last run; `user/followers_ids` diff | Owned follower list, ≈ $0.001 per follower read |
| Owner replies and quotes | twitterapi.io, or official | `tweet/replies` and `tweet/quotes` with `sinceTime` = last run | Owned mentions ($0.001) and quote posts ($0.005) |
| Peer discovery | twitterapi.io, or official | grouped `from:a OR from:b … -filter:replies since_time:<last run>` (≤ 25 handles per query) | Per-peer user timelines with `since_id` ($0.005 per post) |
| Peer re-checks | twitterapi.io, or official | `tweets?tweet_ids=` in batches of 50, every run until a capture at age ≥ 7d exists | Post lookup by ID ($0.005 per post) |
| Peer follower counts | twitterapi.io, or official | `user/batch_info_by_ids` with more than 100 IDs (1-credit rate) | User lookup ($0.01 per user) |
| Backfill | twitterapi.io, or official | `user/tweet_timeline` (about the last 3,200 posts) | User timelines ($0.005 per post) |

SocialData (≈ $0.20 per 1k) is a third adapter for later. It is unresearched, and probably carries the same legal exposure as twitterapi.io.

**Rules:**
- **Never** use twitterapi.io write or login endpoints, and never handle the owner's cookies.
- **Token refresh.** X access tokens last 2h, and refresh tokens are single-use. Every run refreshes first, under a lock, and stores the new refresh token before any other call. A failed refresh pauses owner collection; `setup_status` and the briefing ask the owner to re-consent in the browser.

**Day-1 probes:**
1. `/2/tweets/analytics` availability and billing.
2. Owner timeline rate limit.
3. **Equivalence:** fetch 30 owner posts through both providers and compare `impression_count` with `viewCount` (MODEL A11). If they differ systematically, the peer prior gets a calibration factor. This probe runs only if twitterapi.io is chosen.
4. **Refresh-token lifetime across gaps** (over the first weeks, not day 1): confirm a stored refresh token still works after 7 days unused, then, with a second grant kept unused, after 14 days. Refresh tokens are single-use, so one grant can't test both. X doesn't document this clearly for batch use. If it lapses, the reminder fires before the limit.

### 5.2 Batch capture (MODEL §1.3)

- **Every count carries its age.** A run reads each post at whatever age it has reached. The model maps that to 48h and 7d with the maturity curve, which is fitted from posts captured at two or more ages.
- **Owner:** every post under 30 days old is read at each run, so a post is captured at several ages. A gap above about 25 days loses non-public metrics for the oldest posts. The briefing warns before that happens.
- **Peers:** discovered at the first run after they post, then re-read at each run until a capture at age ≥ 7d exists. At a 3-day gap that is about 2.3 re-reads per post.
- **Target cadence:** a run every 3 days (2–4 is fine). **Keep it at 3 days or less while an experiment runs**, because guardrail breaches are only seen at a run.
- **Nothing is back-filled.** A post first captured after 14 days is still scored, flagged `late_maturity`, and left out of D1 and the release gates (MODEL §1.3).
- **Catch-up after a long gap.** A run reserves its budget up front and collects in priority order: owner posts first, then peer posts nearest to their 7-day capture, then peer discovery. Official calls respect `x-rate-limit-reset`; anything left over carries to the next run, and the report says so.

### 5.3 Cost per month (modelled for batch runs every 3 days, inputs partly assumed)

| Item | twitterapi.io for peers and owner extras | Official API only |
|---|---|---|
| Owner metrics and analytics (BYO app), at 1k followers | ≈ $1.90 | ≈ $1.90 |
| Owner new followers, unfollowers, replies, quotes | ≈ $0.23 | ≈ $11.80 |
| Per peer (discovery + re-checks until 7d + follower count) | ≈ $0.045 | ≈ $1.60 |
| 100 peers | ≈ $4.50 | ≈ $160 |
| 50 peers | ≈ $2.25 | ≈ $80 |
| LLM labelling plus weekly agent analysis | ≈ $3 | ≈ $3 |
| **Workspace, 100 peers, no on-demand research** | **≈ $10** | **≈ $177** |
| On-demand agent research allowance (20 searches, 10 threads, 2 timelines a day) | ≈ $13 | ≈ $435 |
| One-off backfill, 200 peers × 200 posts (100 peers on official) | ≈ $6 | ≈ $100 |

At a 2-day gap, 100 peers cost about $6.10 on twitterapi.io or about $217 on the official API; at a 4-day gap, $3.70 or $131. The model is `docs/research/cost_model_batch.py` (run it with `python3`). Its inputs include assumptions:
- the `/2/tweets/analytics` price ($0.005 × 2 pulls per post);
- 10 replies and 2 quotes per post, and 30 new followers a day at 1k followers;
- LLM token counts;
- each peer posting about 3 times a day.

**Caveats that stay visible in the product:**
- Without BYO keys, owner reads cost $0.005 instead of $0.001, so the owner line rises from about $1.90 to about $5.50 at a 3-day gap.
- Peer cost scales linearly with each peer's posting rate.
- `/twitter/tweets` bills per requested ID, deleted posts included.
- Failed calls are not reliably free.

**Budget.**
- Default caps on the twitterapi.io path: $40 in month 1 (backfill plus the unknown analytics cost), $25 after.
- On the official path, the cap must be set when the owner chooses it. The default there is 50 peers and $120 a month.
- The Collection screen shows the ledger against these figures after every run.

### 5.4 Budget and job safety

- Reserve the worst-case cost before dispatch, and settle per attempt.
- Treat ambiguous timeouts as billed until reconciled.
- Use idempotency keys per (workspace, principal, operation).
- Back off with jitter: twitterapi.io sends no `Retry-After`.
- Pause a credential on authorisation errors.
- Read `pricing.json` at startup and alert when it changes.
- Cross-check 50 peer posts a week against official `public_metrics`.

### 5.5 Deletions

- **Owner deletions:** detected by diffing the owner timeline at each run, restricted to posts still inside the 30-day read window, so posts that age out don't look deleted.
- **Peer deletions:** detected when IDs go missing at a re-check. After 7 days, a budgeted re-check samples older posts every few runs.
- **Handling:** a detected deletion goes through the compliance-deletion path (§4) in the same run.

---

## 6. Labelling

Labels are versioned (`label_set_version`), and re-labelling never overwrites history.

- **Deterministic fields from the API:** type, `has_url`, media kind and duration, text length (with URLs weighted as 23), newlines, thread position, gap since the previous post.
- **LLM fields:**
  - Fields: format (tutorial, demo, build log, explainer, commentary, question, reference), topic from the owner's topic list, hook type, CTA, in-niche (bool), language.
  - Labelled **synchronously** when a planned post is registered (`register_planned_post`) or an unplanned post is first seen at a run, so they are available for the live or as-of forecast.
  - Each label carries a confidence score; anything below 0.7 is shown as "needs review" and is left out of the frozen forecast's features.
- **Owner fields:** experiment arm (written by the experiment engine, never by the LLM), and manual corrections through `set_label`.
- **Near-duplicates:**
  - MinHash against the owner's last 200 posts and replies, blocking above the guardrail threshold.
  - Text embeddings are optional, for topic clustering.

---

## 7. Modelling service

The full spec is MODEL §1–5. Components become eligible by owner post count (MODEL §3, §9). **Promotion also needs:**
- LFO-CV, 1-step-ahead, refitting when Pareto k > 0.7, with ΔELPD > 2 SE over the champion;
- a Berkowitz p > 0.01;
- every release gate below.

**Minimum peer data:** at least 30 matured posts per peer and at least 2,000 peer posts in total.

| Owner matured posts | Becomes eligible |
|---|---|
| 0 | Before peers exist (M1): forecasts from the MODEL §2a priors, labelled `prior_only_uncalibrated` and kept out of calibration (MODEL A22). Then: joint peer fit and prior-only forecasts; nowcast baseline trained on peers; breakout tail (ξ, u) from peers; follower model from the peer prior; per-post surprise (mid-PIT); goal status as `insufficient_owner_data`; D0 peer-panel monitor; regime registry |
| ≥ 20 | Owner intercepts and dispersion. D1 with **K = 3 paging charts**: a two-sided location CUSUM on I48, a scale CUSUM on z² − 1, and a CUSUM on follower gains per run interval. Each is at ARL0 = 300, so the location chart uses **h = 7.62** (each side at ARL0 600; exact Brook–Evans, MODEL §4.3). A non-paging likes chart. D3 ACI. Goal status with owner data. Breakout labels calibrated on peers. |
| ≥ 60 | Owner deviations for type, format, link and video; D2 (goal is wrong); follower DGLM (from 4 weeks, about 10 run intervals); pooled breakdowns; suppression HMM in shadow mode |
| ≥ 150 | Interactions, hour splines, cluster effects, full suppression HMM, bandit mode. The two-phase nowcast and the 30m–2h breakout alarm wait for hosting, because they need dense early captures. |

**Release gates for any model version** (MODEL §5, all must pass):
1. Log-scale CRPS skill above the trailing-median baseline, with the lower bound of the week-block bootstrap 90% interval above 0.
2. 50% and 80% coverage inside the binomial bands.
3. Berkowitz and PIT χ² p-values above 0.01.
4. No CUSUM alarm in the backtest.
5. 80% interval width at most 1.1× the baseline's.
6. R̂ < 1.01, ESS > 400, and SBC passes. SBC runs once per model version.
7. Brier score no worse than the baseline.

**Component gates:**
- Maturity curve: held-out capture-pair coverage inside the binomial band, with no age trend in the residuals (MODEL §5).
- Nowcast skill in every capture-age band.
- Follower model vs persistence + drift and vs a local linear trend.
- Breakout:
  - Brier skill;
  - calibration slope between 0.8 and 1.2;
  - tail coverage.
- Experiment engine:
  - A/A size between 0.04 and 0.06;
  - SRM check;
  - randomization test agrees with the posterior at least 97% of the time.

A failed gate keeps the previous version live and logs the failure on the Forecasts screen (model-card panel, M2).

**When things happen:**
- **At slot or planned-post registration** (during a run, or when the agent starts the app in between): freeze and hash the treatment-blind forecast first, using the protocol's frozen model version, then draw the arm. It uses only information available before assignment: slot time, follower count, regime, and the planned levers outside the experiment. Planned posts outside experiments get a live forecast from the current champion. The session ends by writing a receipt anchor and a backup.
- **At each run, in order:**
  1. refresh tokens, then collect (owner, owner extras, peers) and apply deletions;
  2. process ranker-repo commits since the last run, opening regimes before anything is fitted;
  3. match posts to slots;
  4. label new posts;
  5. freeze as-of forecasts for unplanned posts, each from the snapshot of the last run before the post plus its own labels and schedule features;
  6. score posts with a maturity capture, under their frozen forecasts;
  7. refit (maturity curve and reach model together), evaluate, then promote or hold;
  8. update provisional nowcasts for posts under 48h with the current champion;
  9. update monitors and guardrails, pausing arms if needed;
  10. run the follower Monte Carlo (4,000 paths) and recompute goal status;
  11. write the briefing, notify, and back up.

---

## 8. Experiment engine

The spec is MODEL §6.

- **Slots.**
  - Experiments assign `planned_slots`, not posts.
  - The owner (or their agent) registers slots for whole local days, up to 7 days ahead, at a run or in between. The engine freezes the blind forecast with the protocol's frozen model version, draws the arm with a seeded server-side draw blocked within the local day, and writes the receipt. Posts that weren't registered are not randomized and stay out of the experiment.
  - The owner sees each slot's arm ahead of time (Account screen, "Next posts"; `get_schedule`).
- **Matching.**
  - A published post is matched to its slot by a short slot tag the owner or agent adds to the draft (optional), or by the nearest planned time within ±30 min.
  - **Rule for the owner:** don't delete experiment posts. A post deleted before the next run is never seen and counts as a missed slot; the readout compares missed-slot rates by arm.
  - Unmatched posts and missed slots are logged as deviations and analysed as assigned (intention to treat).
- **Protocol object.**
  - Contents: hypothesis, lever, design (per post, switchback, ITS), arms, π, eligibility, primary metric and horizon, δ, guardrails, stop rule, frozen model version.
  - It is versioned and frozen on start.
  - `amend_experiment` creates a new version that keeps the original assignments, and needs owner approval for any change to metric, δ, model or exclusions (MODEL §6.9).
- **Readout.**
  - It reports the posterior P(θ > δ), expected loss, the 80% interval, a randomization test (9,999 re-draws) and an e-value.
  - Health checks run first:
    - SRM;
    - a maturity capture (age ≥ 48h) for every assigned post, with its age offset recorded;
    - missed-slot and deleted-post rates equal across arms;
    - blind-forecast hash before assignment;
    - no posting delay that depends on treatment;
    - change-points inside the window.
- **Decision.**
  - Adopt, reject or near threshold (MODEL §6.3), with at most one extension of up to 50%.
  - Evaluated only at the first run after each experiment-week boundary, and finally at the first run after the horizon plus 48h. Evaluating at every run would raise false adoption above the simulated rate.
  - Only e-LOND rejections at FDR 0.10 may be called "confirmed".
- **Guardrails** (MODEL §6.4), checked at every run. Confidence sequences stay valid at any stopping time.
  - **Arm harm:** a confidence sequence on the treated-minus-control mean of log(outcome ÷ blind forecast). The arm pauses when its upper bound falls below log 0.8. (The earlier "3 consecutive posts below 0.5×" rule is dropped: it would trip in 29–44% of 4-week experiments with no real harm.)
  - **Unfollows:** experiment-level, as a rate per day against the pre-experiment baseline. A breach pauses the whole experiment, because unfollows can't be attributed to an arm.
  - **Reply-sentiment and negative-feedback proxies:** per arm where they attach to posts.
  - **Pausing** stops new draws. Already-drawn slots for the paused arm revert to control, logged as deviations, and the owner is notified.
  - A breach on post outcomes is seen up to 48h + one run gap after the post, so the briefing asks for runs at most 3 days apart while an experiment is live.
- **Power preview.** Shown on every suggestion card and computed, never written by an agent. It shows three numbers, so a classical-power figure can't oversell the labels:
  1. classical power (SE = 2σ√(1−R²)/√n) across an R² range of 0–0.5;
  2. P(adopt) under the decision rule (MODEL §6.3: 18% / 48% / 91% at 1.25× / 1.5× / 2× over 4 weeks);
  3. a simulated P(confirmed) under e-LOND, which at n = 84 is low for anything short of a 2× effect.
- **Lever calendar.**
  - One experiment per lever at a time.
  - More than 3 concurrent experiments needs owner approval rather than being blocked.
  - Interference rules follow MODEL §6.6.
- **First 12 experiments** come from MODEL §6.10 and are pre-loaded as suggestions. E10, E11 and E12 need owner approval.

---

## 9. MCP server: full agent access

### 9.1 Principles

1. **Parity.**
   - Every query, analysis and action in the web app is available as an MCP tool, with the same data and permissions.
   - A feature is not done until its tool exists and has a contract test.
   - The web app and the MCP server call the same application services.
   - Exceptions:
     - creating the X developer app and granting OAuth consent, which X requires in a browser;
     - entering secrets, which happens in a local form the server opens and never in the agent's chat;
     - managing MCP client tokens, which is web or CLI only.
2. **Agent-only operation is a supported mode.**
   - `setup_status` lists what is missing in plain language, and `open_setup_page` opens the local form for the browser-only steps.
   - Everything after that works through the agent.
3. **Grounding.**
   - Every response carries `model_version`, `regime_id`, `as_of`, `data_sufficiency`, `caveats[]` and stable record IDs (`post:…`, `forecast:…`, `exp:…`, `ins:…`).
   - Server instructions tell agents to cite those IDs.
4. **Plain-language layer.**
   - Every analytical result includes a short `summary` and a `confidence` label (`solid`, `suggestive`, `too early`).
   - The wording comes from templates driven by the same thresholds as the status rules, never free-written by an LLM, and never quoting third-party text.
   - A summary may say "confirmed" only after an e-LOND rejection.
5. **Tracing.**
   - Every call is logged in `mcp_calls`.
   - Agents record what they concluded and told the owner with `log_decision`, linked to the IDs they relied on.
   - `get_trace` replays the evidence chain behind any insight, decision or journal entry.
6. **Push, not just pull.**
   - At the end of each run, alarms, guardrail pauses, failed token refreshes and pending approvals reach the owner through OS notifications on their machine (optionally email), and through `get_briefing` and an MCP resource.
   - An overdue run triggers a reminder.
   - An agent-only owner doesn't have to ask in order to find out.
7. **Server-enforced limits:**
   - No tool can post, like, reply or follow on X.
   - Frozen forecasts and assignment receipts can't be edited.
   - Evidence is removed only through the compliance-deletion path (§4), never by a tool.
   - Spend is reserved against per-client and per-workspace caps.
   - Third-party text (peer posts, replies, threads) comes back in fields tagged `untrusted`. **This is a mitigation, not enforcement:** the server can't control how an agent reads text, which is why approvals use a separate channel.

### 9.2 Owner confirmation without the dashboard

**What needs owner approval.** Consequential actions create an `approvals` row bound to the exact revision, cost and expiry:
- spend above the cap;
- account-level experiments (E12);
- experiments touching other accounts (E10, E11);
- horizons over 8 weeks;
- more than 3 concurrent experiments;
- amending a frozen metric, model or δ;
- excluding posts from analysis;
- overriding a guardrail;
- adding peers;
- changing goals or budgets;
- publishing a journal entry or a public "confirmed" claim;
- marking an insight reviewed.

**How the owner confirms.** The code always travels through a channel the requesting agent cannot read:
1. **Chat relay:**
   1. The tool returns a plain-language description, and the server delivers a one-time code to the owner by OS notification (or email).
   2. The owner reads the code and gives it to the agent, which calls `confirm_action(approval_id, code)`.
   3. The server records the route as "chat relay".
2. **Elicitation:** if the client renders MCP elicitation, the server asks the owner directly in the client UI.
3. **Web app:** the Agents screen's approval panel.

**Assurance:**
- For spend and publishing, the client that proposed an action can't also relay its approval unless the owner has allowed that in settings.
- Chat-relay approvals are recorded at a lower assurance level than web or elicitation approvals.

**Policies** can be set per category: "auto-approve per-post experiments inside the content plan" (MODEL §6.9), "always ask", or "web app only". Every approval and its route goes to the audit log.

### 9.3 Tools

| Group | Tools | Scope |
|---|---|---|
| Orientation | `setup_status`, `open_setup_page`, `get_briefing(period)` (what happened, what's on track, what needs a decision), `start_run`, `get_run_status` (long runs return a run ID to poll) | read / spend |
| Posts and forecasts | `get_posts`, `get_post`, `get_forecast` (post or draft; MODEL §8 calls it `get_expected_range`), `register_planned_post`, `get_schedule`, `set_label` | read / write |
| Account and goals | `get_account_overview`, `get_follower_forecast`, `set_goal`, `log_activity` | read / write / approval |
| Patterns | `breakdown`, `compare(groups)` | read |
| Model | `model_status`, `get_calibration`, `get_monitors`, `acknowledge_alarm`, `change_log`, `run_backtest` | read / write / compute |
| Experiments | `get_experiments`, `get_experiment`, `power_check` (MODEL §8 `power_preview`), `propose_experiment`, `schedule_experiment`, `amend_experiment`, `stop_experiment`, `get_readout` | read / draft / approval |
| Insights | `get_insights`, `write_insight` (needs at least one citation), `review_insight` | read / draft / approval |
| Content plan | `get_content_plan`, `set_content_plan`, `get_topics`, `set_topics` | read / approval |
| Collection | `get_collection_status`, `get_costs`, `search_peers`, `fetch_thread`, `add_peer`, `remove_peer`, `backfill_account`, `set_budget`, `set_provider` | read / spend / approval |
| Data | `export_data`, `request_deletion` | read / approval |
| Journal | `draft_journal_entry`, `publish_journal_entry` | draft / approval |
| Tracing | `log_decision`, `get_trace(record_id)`, `get_call_log` | write / read |
| Confirmation | `list_approvals`, `confirm_action`, `reject_action` | owner-relayed |

**Resources:** the model card, the method notes, `socialmgr://briefing/latest` and `socialmgr://alerts`.

**Prompts:** "weekly review", "plan next week's posts", "explain this experiment".

**Transport and auth:**
- stdio and Streamable HTTP on localhost.
- Each registered client has a revocable token with scopes (`read`, `draft`, `spend`, `relay-approvals`), stored hashed. Host and Origin are validated.
- Hosted mode (M7) adds OAuth with PKCE and exact resource audience.

---

## 10. X ranker change tracker and regimes

1. Fetch `xai-org/x-algorithm` at every run, and process every commit since the previous run.
2. Parse `home-mixer/params/param.rs` and the README milestones as high-confidence changes. Also parse the `vm-ranker` defaults as a low-confidence class: since 09-24 they are the only source for the OON factor, author diversity and DPP settings (x-algorithm-verification.md §2), so ignoring them would miss real changes.
3. Classify each diff as weight, cold start, source, model or safety, and timestamp it with the sync header, not the commit time.
4. Write `ranker_changes`, and open a regime for weight, cold-start and source changes. Down-weight any that revert within 7 days.
5. Seed the registry with MODEL §2e's dates: 08-13, 08-25, 09-18, 09-29, 09-30, 10-07, 10-08/13, 10-14, 10-22, 11-05, 11-13, 11-18.
6. Models get regime effects. Peer backfill from before 09-30 enters only with regime fixed effects.
7. Readouts flag change-points inside their window. Insights that touch an affected feature are marked "recheck".
8. D0, the peer-panel CUSUM, catches unannounced platform shifts and labels a D1 alarm as "platform" or "owner-specific".

---

## 11. Milestones

Each milestone ends with something the owner can use, through the web app or their agent. The estimates assume one engineer working with coding agents.

**Clocks.**
- *Build weeks* count from the start of M0.
- *Account weeks* count from the owner's first post, which happens when M1 ships, at about build week 3.5.

Per-post experiments start when M3 ships: about build week 9.5, which is about account week 6. Until then, posts are still captured and scored, so the forecast record is in place before experiments begin. E12 (Premium) needs account week 9 or later (MODEL §6.10).

| # | Milestone | Ships | Acceptance | Est. |
|---|---|---|---|---|
| **M0** | Foundations | Repo layout, config, Postgres schema with append-only enforcement, `XDataProvider` with a fake provider, fixture workspace, CI (unit + real-Postgres integration), keychain secrets, backups | `docker compose up` gives an empty app; fixture data renders the Posts table; append-only tables reject UPDATE; restore from backup works | 1 wk |
| **M1** | See my posts against a baseline, in the app or from my agent | Owner OAuth (BYO app) with locked token refresh, `socialmgr run` orchestrator with resumable steps, owner batch collector, follower snapshots and diffs, rule labels and synchronous LLM labels, MinHash guardrail, `register_planned_post` with live forecasts, as-of forecasts for unplanned posts, trailing-median baseline with a first maturity curve fitted from owner captures, scoring, day-1 probes, run reminders and notifications, Overview and Posts screens. **MCP core:** stdio server that starts the app on demand, hashed client tokens, `setup_status`, `start_run`, `get_briefing`, post and forecast tools, call log, plain-language summaries. | On a **simulated clock with irregular run gaps (1–7 days)**, every post gets a maturity capture and is scored or flagged `late_maturity`. An interrupted run resumes without double-billing within the same UTC day. Every as-of forecast recomputes bit-for-bit from its manifest, and is unchanged when everything captured after its snapshot run is perturbed. A run, `serve` and an MCP session can overlap without stopping each other's database. A planned post's forecast is frozen before its `created_at`. The Owned Reads cost matches the X console. An owner completes setup (with the browser steps), runs the platform and reads last week's results from Claude Code. | 2.5 wk |
| **M2** | Peers, priors, regimes and patterns | The peer adapter the owner chose (twitterapi.io, or the official API), peer panel (50–200 accounts in the ≤1k / 1–5k / 5–50k bands, none above 50k), per-run discovery and re-checks, backfill, hierarchical maturity curve, regime registry and ranker tracker, joint hierarchical NB2 fit, prior-only forecasts, D0, Patterns and Collection screens, model-card panel on Forecasts, budget guard | Peer cost on the ledger stays within ±20% of the batch cost model for the measured posting rates. The maturity curve passes its gate on held-out capture pairs. The prior-only model passes the leave-one-peer-out backtest gates with regime effects. Patterns shows pooled effects with n and 80% intervals. | 3 wk |
| **M3** | Experiments | Planned slots, blind-forecast freeze, randomization and receipts, matching, protocol versioning, readout with randomization test and e-values, e-LOND stream, three-number power preview, guardrail sequences, receipt anchors, Experiments screen, "Next posts" on Account, the first 12 suggestions | A simulated 4-week per-post experiment, with batch captures and looks at the first run after each experiment-week boundary, reproduces MODEL §6.3's operating characteristics within ±3 points over ≥ 1,000 replicates. A/A size is 0.04–0.06. The arm-harm guardrail trips in ≤ 5% of null experiments. Every receipt's blind-forecast hash predates its arm draw, and anchors written at registration sessions predate the matched posts. | 3 wk |
| **M4** | Agents can run it end to end | The full tool set (§9.3), approvals with codes sent through a separate channel, elicitation where supported, policies per category, `log_decision` and `get_trace`, resources and prompts, insight store, Agents and Insights screens | A **parity test** walks every web API route through MCP. In an agent-only session the owner adds peers, sets a goal and approves a budget change. A prompt-injected approval attempt from peer text fails without the out-of-band code. `get_trace` reconstructs an insight's evidence. A client is refused past its cap. | 2.5 wk |
| **M5** | Model health, goals and the ranker in full | Batch nowcast, follower DGLM on run intervals and goal status with hysteresis, D1 (K = 3 charts; two-sided location chart at h = 7.62), D2, D3, breakout labels and tail model, suppression HMM (shadow), model promotion pipeline, Forecasts and Account screens completed | Under NB-simulated null data, each chart's run length to a false alarm matches its target (300 posts; 100 days ÷ G intervals for the follower chart) within ±10% over ≥ 1,000 replicates. For a synthetic 30% drop at φ ≈ 3, the run length in posts is reported against an NB-simulated reference. Goal probabilities match a 100,000-path reference within ±1 point. The follower DGLM recovers known parameters from simulated run-interval data. A `param.rs` diff opens a regime at the next run. | 3 wk |
| **M6** | Public journal | Static export of the personal blog (home, experiments index, a page per experiment with protocol, stats and readout, method page, note pages), owner approval per entry, peer-data redaction, published receipt anchors | Nothing about peers or any unpublished post leaves the machine. Each entry needs an explicit publish. Readers can verify a receipt hash against the anchor. | 1 wk |
| **M7** | Hosted, continuous (later) | Always-on host with a scheduler, the dense capture schedule (MODEL §1.3), the two-phase nowcast and 30m–2h breakout alarm, HTTPS, OAuth for MCP, row-level security per workspace, hardened job leases, restore with deletion replay, an Enterprise agreement with X if beyond "a limited number of end-users" (Developer Agreement §III.L/M), legal review of peer collection | Cross-workspace access tests pass. A restore replays deletions before serving traffic. Dense captures agree with the maturity curve. | 3–4 wk |

**Critical path:** M0 → M1 → M2 → M3. M4 can start in parallel with M3 once the read models exist. D1 needs about 20 matured owner posts: roughly 9 days after the owner starts posting at 3 a day, plus up to one run gap. It is built in M5 and switches on when M5 lands. From M1 on, every milestone's acceptance includes its MCP tools: no feature ships web-only.

---

## 12. Testing

- **Unit:**
  - label rules;
  - run orchestrator (step order, resume, idempotency);
  - capture ages and maturity offsets;
  - cost estimator, against `docs/research/cost_model_batch.py`;
  - randomization;
  - slot matching;
  - e-LOND bookkeeping;
  - MinHash thresholds.
- **Integration (real Postgres):**
  - a run killed mid-step and resumed;
  - last remaining budget;
  - crash before and after dispatch;
  - token refresh after a multi-day gap, and a refresh interrupted mid-write;
  - stale writes after revocation;
  - partial provider responses;
  - exact zero vs omitted fields;
  - compliance deletion keeping tombstones.
- **Simulation (replicated, never single runs):**
  - the model recovers known β and effects on synthetic accounts captured at irregular run gaps;
  - the maturity curve recovers a known shape from capture pairs;
  - as-of forecasts leak nothing (a test that perturbs every value captured after the snapshot run, including follower counts and other posts' outcomes, and checks the forecast is unchanged);
  - the maturity-curve likelihood is calibrated when captures are nested (shape from conditional BetaBinomial pairs, level from one capture per post);
  - experiment operating characteristics (MODEL §6.3);
  - detector ARL0 and ARL1 at the deployed thresholds (MODEL §4.4);
  - power formula vs simulation within ±0.03.
- **Contract:**
  - MCP schemas for every tool;
  - a refused call returns a structured reason;
  - no tool path reaches an X write endpoint (static check on provider adapters).
- **Parity:** a generated test checks that every web API route has an MCP tool with the same permissions and equivalent output.
- **Agent-only scenarios:**
  - scripted sessions run onboarding, a weekly review, experiment approval and journal publishing purely over MCP;
  - every `summary` string must match its numeric thresholds;
  - injected instructions in peer text must not produce an approval.
- **Backtest gates:** run in CI on the fixture panel; a model can't be promoted from a branch.

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| **twitterapi.io legal exposure.** It is an undisclosed scraper. X's terms forbid scraping. The vendor's terms (§04) put compliance with X's terms on the customer. X has sued scrapers. X's Aug–Sep 2026 cease-and-desist letters took Nitter and XCancel offline (not twitterapi.io). Scraper accounts used by twscrape and Nitter were suspended on 2026-11-21; the vendor says it was unaffected. The vendor's domain changed registrar on 2026-11-09. | Local and single-user only. Store minimal peer data. Keep a small prepaid balance. Make the official API a one-setting fallback (≈ $160/month for 100 peers, ≈ $80 for 50, at a 3-day run gap). Get legal review before M7. **The owner decides** between the cheap path and the legally clean one (§14). |
| SocialData as a fallback is unresearched | Treat it as carrying the same risk until researched; the official API is the clean fallback |
| `/2/tweets/analytics` is unavailable or expensive | Probe on day 1. Outcomes come through the maturity curve, and the follower model uses run-interval diffs (MODEL A3, §2c). |
| `viewCount` and `impression_count` differ | Day-1 equivalence probe and a calibration factor for the peer prior |
| The peer prior doesn't transfer to an account starting at 0 followers | Robust MAP mixture (0.8 / 0.2) and a prior–data conflict check after 10 posts (MODEL §3) |
| Experiments are underpowered, and "confirmed" is rarely reachable at small n | Three-number power preview, early preference for 2× hypotheses, plain "inconclusive" readouts |
| The ranker changes mid-experiment | Regimes from the tracker, a regime fixed effect, and a flag on the readout |
| Agents over-claim or are prompt-injected by peer text | Templated summaries; citations required; out-of-band approval codes; untrusted-field tagging |
| Batch measurement is noisier than fixed-age snapshots: outcomes come through a fitted maturity curve | The curve is fitted from repeated captures and gated (§7). Owner analytics buckets give exact values if available. The hosted mode (M7) restores dense captures. |
| Long gaps between runs | Reminders after 3 days. Guardrails are only checked at runs, so experiments ask for gaps of 3 days or less. Owner non-public metrics are lost after about 25 days. The briefing shows each gap and its cost. |
| Owner effort | One command (or one agent request) per run. The schedule shows arms ahead of time, labelling is automatic, and agents can register slots. |

---

## 14. Open decisions for the owner

None of these blocks starting M0. Each has a default and a deadline.

| # | Decision | Options | Default if not decided | Needed by |
|---|---|---|---|---|
| 1 | **Peer data source** | twitterapi.io: ≈ $4.50/month for 100 peers at a 3-day gap; breaks X's terms (§13). Official API: ≈ $160 for 100 peers, or ≈ $80 for 50; legally clean for private competitive analysis. | None. M2 can't start without it. | Start of M2 (build week 3.5) |
| 2 | **Premium** | Start at launch (a reputation shield), or at account week 9 or later (allows a causal test, MODEL A14) | Account week 9, so the test stays possible | The owner's first post (M1 ships) |
| 3 | **Peer panel** | The agent proposes qualifying peers in the MODEL §3 bands, or the owner hand-picks them | The agent proposes; each addition needs the owner's approval (§9.2) | Start of M2 |
| 4 | **Budgets** | Data cap and agent daily cap | twitterapi.io path: $40 in month 1, then $25. Official path: 50 peers, $120. Agent cap $1 a day. | Start of M2 |
| 5 | **Approval route for spend and publishing** | Chat relay with out-of-band codes, elicitation, or the web app only | Web app or elicitation; chat relay off for spend and publishing until enabled | Start of M4 |
| 6 | **Public journal** | Part of v1, or deferred | Stays as M6, after the core is in use | Start of M6 |
| 7 | **Run cadence** | How often the owner runs the platform | Every 3 days, with a reminder; at most 3 days apart while an experiment runs | The owner's first post |
| 8 | **Deletion compliance in batch mode** | Accept up to one run gap before a deleted post's copy is removed, or add a lightweight deletion check between runs | Legal check of the Developer Agreement's deletion terms first; if a gap isn't acceptable, add a small scheduled ID check between runs (the reminder job itself never collects) | Start of M2 |

Research notes, verification and the full source register are in `docs/research/` and on the canvas Reports page.
