# Social Manager: implementation plan

Last revised 2026-11-25, **revision 5 (simple v1)**. Revision 4 is in git history (commit 3795702).

The maths lives in [`docs/MODEL.md`](docs/MODEL.md) (Model v2) and governs all statistics, except the peer-dependent parts that §6 replaces. The evidence lives in `docs/research/`; the review of the first plan is [`REVIEW.md`](REVIEW.md). The screens are on the canvas (Social Manager file, Designs page).

**Revision 5: simple and quick to build.** The tool is for the owner and their team for now. Each person runs their own copy on their own machine, for their own X account. Anything that only made sense for a hosted, multi-user or public product is removed:
- **removed:** peers and twitterapi.io, hosting (old M7), the public journal (old M6), insights, the Patterns, Collection, Agents and Insights screens, approval codes and assurance levels, MCP client tokens and parity tests, receipt anchors, the budget reservation and job machinery, reminders and notifications, Docker and PostgreSQL, the OS keychain;
- **kept:** MODEL.md's statistics in full (forecasts, maturity curve, scoring, goal status, detectors, follower model, experiments with e-LOND), batch runs, frozen forecasts, MCP agent access and experiments;
- **built to extend (§13):** peers, other models, a self-adapting model, other platforms and hosting are later additions behind interfaces that exist from M0, not rework.

---

## 1. What we are building

A local analytics and experimentation workbench for **one X account per install**. Each team member clones the repo, connects their own X account through their own X developer app, and runs it on their own machine. There is no shared server and no shared data.

**A run** (`socialmgr run`, or the `start_run` MCP tool), about **once a day**:
1. refreshes the X token and collects the account's posts, metrics and follower changes since the last run;
2. labels new posts;
3. freezes as-of forecasts for posts that weren't registered in advance;
4. scores posts that have matured (first capture at age ≥ 48h);
5. refits and evaluates the model, promoting it only if it passes the gates;
6. updates monitors, goal status and experiment guardrails;
7. writes a briefing and backs up the database.

Between runs, the agent can still start the MCP server (stdio) to read data, register planned posts or schedule experiment slots.

**What it does:**
1. **Posts against an expected range.** Every post gets a frozen forecast and is read as above / as / below expected (MODEL §4.1).
2. **Goals and followers.** Goal status (Working / Near threshold / Not working, MODEL §4.2), a follower forecast and drift monitors.
3. **Experiments.** Randomized per-post and switchback experiments with honest readouts (MODEL §6).
4. **Agent access.** An MCP server so an agent (Claude Code, Codex, any MCP client) can read everything and drive runs, planned posts and experiments.

**Out of scope for v1** (each lands later as an addition, §13): peers and competitor tracking, posting or engaging on X, LinkedIn and Instagram, hosting, shared team dashboards, the public journal, an insight library.

### Screens (canvas: Designs page)

| Screen | Serves | Built in |
|---|---|---|
| Overview | KPI tiles against expected ranges, follower trend with forecast band, post status counts, recent posts, last briefing, model-health summary | M1, extended in M3 |
| Posts | Every post with its frozen forecast, outcome, percentile and labels, plus a detail drawer with its trajectory | M1 |
| Experiments | Running, suggested and concluded experiments, readout, power preview, pending approvals | M2 |
| Account | Next posts with their experiment arms (M2); follower funnel, goal probability (M3) | M2, completed M3 |
| Forecasts | Model card, release gates, calibration, drift monitors, regime log | M3 |

The Patterns, Collection, Agents, Insights and public pages on the canvas are out of scope for v1. The sidebar shows only the five screens above. Its footer shows the last run, the gap since it and the month's spend, in place of the sync status.

React components mirror the Platform Components board one to one, but only those the five screens use.

---

## 2. Decisions

| Decision | Why |
|---|---|
| **Local, one account per install; each team member runs their own copy.** | Owner decision, 2026-11-25. No server, auth or tenancy to build. |
| **Owner data from the official X API only, through the member's own developer app.** No peers, no twitterapi.io. | Owner decision. Removes the legal exposure, a provider and the peer pipeline. Owned Reads cost $0.001 per post, deduplicated per UTC day. |
| **Runs about daily, started by hand or by the agent.** Members may add their own cron line; the platform has no scheduler. | Owner decision. Daily runs keep guardrail delay short and give many capture ages for the maturity curve. |
| **SQLite, not PostgreSQL.** | Hosting is gone, which was the reason Postgres was chosen. SQLite needs no Docker, backs up as one file and enforces append-only with triggers. A run, the web app and an MCP session coexist under WAL mode with a busy timeout. SQLAlchemy keeps a path back to Postgres. |
| **MODEL.md statistics kept as written**, minus the parts that need peers (§6). | Owner decision. |
| **Without peers, forecasts are prior-only until ~20 matured posts.** | Owner decision. MODEL §2a priors, labelled `prior_only_uncalibrated` and kept out of calibration, D1 and the gates (MODEL A22) until owner-level terms are estimated. |
| **Primary outcome I48 through the maturity curve.** | MODEL §1.3. Shown as an estimate with its capture age, exact when owner analytics buckets exist; "provisional" under 48h. |
| **Forecasts are frozen and never edited.** Live at registration for planned posts and slots (treatment-blind for slots); as-of for unplanned posts. | MODEL §1.3. |
| **The server enforces the limits, not the prompt.** No X write endpoint exists in the code; frozen rows are append-only in the database; MODEL §6.9's approval items wait for the owner in the web app or CLI. | Agents make confident mistakes. Approvals are the one check an agent can't satisfy alone. |
| **Premium from launch.** | Owner decision. Premium is a constant covariate; experiment E12 is dropped. |
| **Deleted posts: removed at the next run.** | Owner decision; at daily runs a copy persists for under a day. |

---

## 3. Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.13, `uv` | |
| API | FastAPI + Pydantic v2 | One process serves the REST API and the built SPA. |
| MCP | Official Python MCP SDK, **stdio only** | The client launches it; no tokens, no HTTP transport. |
| Database | **SQLite** (WAL), SQLAlchemy 2, Alembic | One file under the data folder. Append-only tables (`forecasts`, `forecast_draws`, `assignments`, `scores`, `metric_snapshots`) reject UPDATE and DELETE via triggers. |
| Run orchestrator | One process, steps run in order, each idempotent and checkpointed in `runs` | A run fixes its collection cutoff when it starts. An interrupted run resumes within 24h, else it closes and a new one starts. A file lock stops two runs overlapping. |
| Modelling | NumPyro 0.22 + JAX 0.12.1, ArviZ 1.3, statsmodels 0.15, scoringrules 0.12, ruptures 1.1.10, polars | As MODEL §9. One fit per run on CPU. |
| Labelling | Deterministic rules first, then a `Labeler` interface. Default: headless Claude Code (`claude -p --model haiku --json-schema …`) on the member's own subscription, so no API key is needed; alternative: the Anthropic API with `claude-haiku-5-5`. Agents registering a planned post can pass labels themselves. | About 5 s per post; owner overrides win. Validated 2026-11-25. |
| Near-duplicates | MinHash | No embeddings in v1. |
| Frontend | React + TypeScript + Vite, pnpm, Tailwind v4, TanStack Query, uPlot | Dark only, desktop ≥ 1280px. Tokens from the canvas `dashboard/*` set as CSS variables. TS client generated from FastAPI's OpenAPI. |
| Secrets | `.env` (gitignored) for the X app client ID (and the Anthropic key only if that labeller is chosen); X OAuth tokens in a `credentials` table | Local single user, so no keychain. |
| X connection | `socialmgr connect`: opens the browser for OAuth 2.0 PKCE, catches the localhost callback, stores tokens | Replaces the setup page. |
| Backups | SQLite online backup to a configured folder at the end of every run and every session that registers slots or planned posts; `socialmgr restore` | Frozen forecasts and assignments can't be recreated. |
| CI | GitHub Actions: lint, type check, unit, integration and simulation tests | |

### Repository layout

```
socialmgr/
  core/          # domain types, config (Pydantic), the extension interfaces in §13
  platforms/     # platform profiles (YAML, MODEL §1.6): x.yaml in v1
  providers/     # DataProvider adapters: x_official/ and fake/ in v1
  runs/          # orchestrator and its step registry, collector, capture ages, cost tally, deletions, backups
  labelling/     # rules, Haiku labeller, label-set versions, MinHash
  modelling/     # features (versioned), priors/, models/ (nb2, baselines), maturity_curve, nowcast, followers, detectors, evaluation, registry, regimes
  experiments/   # slots, designs/, randomization and allocation policies, readout, power, e-LOND, guardrails
  services/      # application services shared by the API, MCP and CLI
  mcp/           # tool definitions (Pydantic schemas)
  api/           # FastAPI routers
  cli.py         # socialmgr connect | run | serve | mcp | approve | restore
web/             # React app
migrations/
tests/
```

**Time.** Timestamps are stored in UTC. The account has one IANA zone in config; "day" in blocking and switchbacks means its local day. The UI shows times in that zone, labelled.

---

## 4. Data model

Platform IDs are strings; times are UTC. The schema is already shaped for several accounts, platforms and providers (§13): v1 simply has one owner row and one provider.

Every table carries `workspace_id` (a constant `1` in v1), and every account-level row carries `account_id`, so peers, other platforms or a shared hosted install later add rows, not migrations.

| Table | Purpose and key columns |
|---|---|
| `accounts` | `platform`, `external_id`, `handle`, `role` (owner in v1; peer/competitor later), `tz`, `premium`, `follower_band`, `tracked_since`, `active` |
| `credentials` | Per account and provider: access and refresh tokens, `refreshed_at` |
| `providers` | Provider adapters enabled for this install, their capabilities and pricing version |
| `runs` | `run_id`, `started_at`, `cutoff_at`, `finished_at`, `gap_since_prev_h`, `steps` (json), `spend`, `trigger` (cli/agent) |
| `posts` | `external_id`, `created_at`, `type` (original/quote/reply/repost/self_reply), `conversation_id`, `in_reply_to_user_id`, `text`, `text_hash`, `minhash`, `has_url`, `media` (json), `text_len_weighted`, `newlines`, `gap_since_prev_min`, `own_posts_48h`, `followers_at_post`, `slot_id`, `deleted_at` |
| `metric_snapshots` | Append-only, one row per post per run: `post_id`, `run_id`, `provider`, `impressions_source` (`impression_count` in v1; `viewCount` for public providers later), `fetched_at`, `age_s`, `impressions`, `likes`, `reposts`, `quotes`, `replies`, `bookmarks`, `profile_clicks`, `link_clicks`, `video_q25..q100` |
| `analytics_buckets` | Hourly/daily buckets from `/2/tweets/analytics`, only if the day-1 probe succeeds |
| `follower_snapshots` | Follower count at each run; `new_followers` since the last run (newest-first list) |
| `labels` | `post_id`, `label_set_version`, `field`, `value`, `source` (rule/llm/owner), `confidence` |
| `planned_slots` | `planned_at`, `lever_context`, `experiment_id`, `arm`, `matched_post_id`, `state` (planned/assigned/posted/missed/unmatched) |
| `forecasts` | Append-only: `subject` (slot/post), `kind` (treatment_blind/live/as_of/prior_only_uncalibrated), `model_version`, `curve_version`, `snapshot_run_id`, `metric`, `horizon_h`, `quantiles`, `p_ge_goal`, `input_manifest`, `manifest_hash`, `draws_hash`, `frozen_at` |
| `forecast_draws` | Append-only: 1,000 joint posterior draws per forecast (compressed blob), keyed by hash |
| `scores` | `forecast_id`, `outcome`, `capture_age_h`, `log_g_offset`, `exact`, `late_maturity`, `pit_mid`, `pit_rand`, `crps_log`, `in_80`, `in_50` |
| `maturity_curves`, `model_versions`, `evaluations` | Versioned curve and model parameters; `model_versions` records `family`, `prior_source`, `feature_set_version`, `code_version` and `role` (champion/challenger/retired); gate results and promotion decisions |
| `feature_sets` | Versioned feature definitions; every forecast's manifest names the version it used |
| `monitors` | D1–D3 state, alarms, acknowledgements |
| `goals`, `goal_status` | Goal definitions; status with `p_hi`, `p_lo`, `reason`, `hysteresis_state` |
| `regimes` | Seeded with MODEL §2e dates, plus entries opened when `param.rs` changes |
| `experiments`, `assignments`, `readouts`, `elond_stream` | Versioned protocol, seed, per-slot assignments with the blind-forecast hash, deviations, readouts with e-values |
| `approvals` | Pending items from MODEL §6.9, approved or rejected by the owner in the web app or `socialmgr approve` |
| `costs` | Spend per run and endpoint |

**Retention:** keep everything; it's the member's own data on their own machine. A post found deleted on X has its text removed at that run; its hash, forecasts and scores stay.

---

## 5. Collection (official X API, per run)

| Need | Endpoint |
|---|---|
| Posts and metrics | `GET /2/users/{id}/tweets` with public, non-public and organic metrics, every post under 30 days old |
| Owner analytics | `/2/tweets/analytics` hourly buckets once a post is ≥ 7 days old. **Probe availability and price on day 1**; without it, outcomes come through the maturity curve only. |
| Follower count | `GET /2/users/me` with public metrics |
| New followers | Owned follower list, newest first, paged only until a known follower appears. Losses = new followers − net change. **Verify newest-first ordering on day 1.** |

Replies to the owner, quotes by others, engagers and the owner's follows aren't collected. The follower model uses the owner's own replies per day (from the timeline) as its activity covariate, without the target tier.

**Rules:** refresh the token first under the run lock, and store the new single-use refresh token before any other call. A failed refresh stops collection and the briefing asks the member to run `socialmgr connect`. Respect `x-rate-limit-reset`; whatever is left over carries to the next run.

**Day-1 probes:** analytics availability and price; owner timeline rate limit; follower list order; whether a refresh token survives 7 days unused.

**Cost per member, per month, at daily runs and 3 posts a day (inputs partly assumed):**

| Item | ≈ |
|---|---|
| Timeline metrics (Owned Reads) | $2.70 |
| Analytics, if available (assumed $0.005 × 2 pulls per post) | $0.90 |
| Follower count and new followers | $1.20 |
| Labelling | < $0.10 |
| **Total** | **≈ $5** |

A monthly cap in config (default $15) stops collection when reached; the Overview footer shows spend against it.

---

## 6. Modelling (MODEL.md, without peers)

MODEL.md applies as written, with these replacements for parts that need a peer panel:

| MODEL.md part | Needs peers because | v1 replacement |
|---|---|---|
| §3 peer prior; §2a joint fit | Priors come from the panel | §2a priors only, labelled `prior_only_uncalibrated` (A22), until 20 matured owner posts; then owner-level intercepts and dispersion. The prior–data conflict check runs against the §2a priors. |
| §1.3 maturity curve "fitted on peers" | Early capture pairs | Fitted from owner capture pairs, which daily runs supply quickly; its prior until then |
| §2b nowcast "trained on peers" | Trajectories | Plain precision weighting until 50 owner trajectories (MODEL §2b fallback) |
| §2c follower prior from peers | π_I prior | MODEL's weakly informative priors; labelled prior-only for the first 4 weeks |
| §2d breakout model | u and ξ from 2,000 peer posts | **Deferred** |
| §2e feature × regime interactions; D0 | ≥ 300 peer posts per regime; peer panel | **Dropped.** Regimes still enter as fixed effects. |
| §4.2 default goals | Peer-band medians | No default: goal status reads "no goal set" until the member sets one |
| §4.3 D2 proposal check | Peer percentiles | Hit-count test only |
| §5 cold-start gate (leave one peer out) | Peers | Dropped; the other gates apply on owner data |
| §6.1 account-level ITS with peer controls | Controls | **Deferred** with E10–E12 |
| §8 `data_sufficiency.peer_n` | — | Dropped |

Also deferred: the suppression HMM, bandits and off-policy evaluation, embeddings and embedding features.

**Eligibility by matured owner posts:**

| Posts | Becomes eligible |
|---|---|
| 0 | Prior-only forecasts, maturity curve from its prior, per-post surprise (mid-PIT), goal status `insufficient_owner_data`, regime registry |
| ≥ 20 | Owner intercepts and dispersion; LFO-CV and the first promotion review; D1 (K = 3: two-sided location CUSUM at h = 7.62, scale CUSUM, follower-gain CUSUM, each at ARL0 = 300); D3; goal status with owner data |
| ≥ 50 | Nowcast regression |
| ≥ 60 | Owner deviations for type, format, link and video; D2; follower DGLM (from 4 weeks) |
| ≥ 150 | Interactions, hour splines |

**Release gates** (MODEL §5, all must pass): CRPS skill over the trailing-median baseline with the bootstrap lower bound above 0; 50%/80% coverage inside binomial bands; Berkowitz and PIT χ² p > 0.01; no backtest CUSUM alarm; 80% width ≤ 1.1× baseline; R̂ < 1.01, ESS > 400, SBC per version; Brier no worse than baseline. A failed gate keeps the previous version and is logged on Forecasts.

**At registration** (planned post or slot): freeze and hash the forecast first (treatment-blind for slots, with the protocol's frozen model version), then draw the arm. End the session with a backup.

**At each run, in order:** refresh and collect, apply deletions → check `param.rs` in `xai-org/x-algorithm` and open a regime if it changed → match posts to slots → label → freeze as-of forecasts → score matured posts → refit, evaluate, promote or hold → provisional nowcasts under 48h → monitors and guardrails (pause arms if needed) → follower Monte Carlo (4,000 paths) and goal status → briefing → backup.

---

**Always show how the past did, not only what's expected next** (owner's rule, 2026-11-25). Every expectation the platform makes is frozen and later scored, and every screen, briefing and agent response that shows an expectation also shows how the corresponding past result landed against its frozen expectation: above, as expected or below (MODEL §4.1 thresholds).
- **Posts:** each post's status against its frozen forecast (Posts, drawer, Overview, `get_posts`/`get_post`). Shipped in M1.
- **Account:** the 7-day impressions and the median 48h impressions against the expected range combined from the posts' frozen draws, with an explicit outcome label; status counts for 7 and 28 days; an "Account" scorecard at the top of every briefing and in `get_account_overview`. Shipped in M1.
- **Followers and goals (M3):** follower forecasts are frozen at each run (like post forecasts) and scored when their horizon passes, so the Account screen shows past follower growth against what was expected for that period, not just the forward band. Goal status keeps its history.
- **Experiments (M2):** each readout shows the treated posts' outcomes against their frozen treatment-blind forecasts.

---

## 7. Experiments (MODEL §6)

Kept as MODEL §6 specifies: slots registered for whole local days up to 7 days ahead; treatment-blind forecast frozen before a seeded draw blocked within the day; matching by slot tag or nearest planned time within ±30 min; intention to treat; the analysis model and a 9,999-draw randomization test; the decision rule evaluated at the first run after each experiment-week boundary; at most one 50% extension; arm-harm and unfollow guardrails as confidence sequences; e-LOND at FDR 0.10, the only route to "confirmed"; the three-number power preview.

**Concurrent experiments** (MODEL §6.1, §6.2, §6.6; confirmed with the owner 2026-11-25): up to 3 per-post factors run at once as a factorial. Each slot draws every factor's arm independently (π = 0.5, blocked within the day), so every post informs every factor and each one reaches its sample size as fast as if it ran alone. The analysis model estimates one effect per factor (Σ τ_k A_ik) on the same posts, and the readout reports each factor separately. Interactions aren't estimated by default (they cost power); the carryover term λ and `own_posts_48h` flag interference. At most one time-level (switchback) experiment runs at a time, and a frequency experiment never overlaps a per-post impressions experiment unless the per-post design is blocked within each frequency condition. A 4th concurrent experiment needs the owner's approval. Each experiment's conclusion enters the e-LOND stream on its own (FDR 0.10).

**Simplified:**
- Designs: per-post micro-randomization and 4-day switchbacks. Account-level designs are deferred.
- Suggestions: E01–E09 preloaded. E10–E11 (other accounts) and E12 (Premium) are dropped.
- The reply-sentiment guardrail is dropped (replies aren't collected).
- **Approvals** (MODEL §6.9, the items that still apply): horizons over 8 weeks, more than 3 concurrent experiments, amending a frozen metric, model or δ, overriding a guardrail, excluding posts. The MCP tool creates a pending approval; the owner approves it on the Experiments screen or with `socialmgr approve <id>`. No MCP tool can approve. Everything else in §6.9's "may auto-start" list starts directly.

---

## 8. MCP server

**Principles:**
- The web app and MCP tools call the same application services.
- Every response carries `model_version`, `regime_id`, `as_of`, `data_sufficiency`, `caveats[]` and stable record IDs (`post:…`, `forecast:…`, `exp:…`), plus a templated `summary` and a `confidence` label (`solid`, `suggestive`, `too early`) driven by the same thresholds as the status rules. "Confirmed" only after an e-LOND rejection.
- No tool can post, like, reply or follow on X. No tool edits a frozen forecast or assignment, and none approves.
- Tool schemas are Pydantic models, written in the milestone that builds the feature, each with a contract test.

| Group | Tools | Milestone |
|---|---|---|
| Orientation | `get_briefing`, `start_run`, `get_run_status` | M1 |
| Posts and forecasts | `get_posts`, `get_post`, `get_forecast` (post or draft), `register_planned_post`, `get_schedule`, `set_label` | M1 |
| Account and goals | `get_account_overview`, `set_goal`, `get_follower_forecast` | M1 / M3 |
| Experiments | `get_experiments`, `get_experiment`, `power_check`, `propose_experiment`, `register_slots`, `schedule_experiment`, `amend_experiment`, `stop_experiment`, `get_readout`, `list_approvals` | M2 |
| Model | `model_status`, `get_calibration`, `get_monitors`, `acknowledge_alarm` | M3 |
| Data | `export_data` | M1 |

**Resources:** `socialmgr://briefing/latest`, the model card. **Prompts:** "weekly review", "plan next week's posts".

---

## 9. Milestones

Estimates assume one engineer with coding agents.

| # | Milestone | Ships | Acceptance | Est. |
|---|---|---|---|---|
| **M0** | Foundations | Repo layout, config, SQLite schema with append-only triggers, fake X client, fixture account, web shell with the shared components, CI | `socialmgr serve` shows the fixture data in the Posts table; append-only tables reject UPDATE and DELETE; backup and restore round-trip; a second fake provider, `PriorSource` and `ForecastModel` plug in through config alone (§13.3) | 4–5 days |
| **M1** | My posts against an expected range, in the app or from my agent | `socialmgr connect` (OAuth PKCE, locked refresh), run orchestrator with resumable steps, collector, follower snapshots, rule and Haiku labels, MinHash guardrail, `register_planned_post` with live forecasts, as-of forecasts, maturity curve, prior-only NB2 and the trailing-median baseline, scoring, regime registry with the `param.rs` check, backups, day-1 probes, Overview and Posts screens, MCP core tools | On a simulated clock with irregular gaps (1–3 days), every post gets a maturity capture and is scored or flagged `late_maturity`; an interrupted run resumes without re-billing in the same UTC day; every as-of forecast recomputes bit-for-bit from its manifest and doesn't change when later captures are perturbed; a planned post's forecast is frozen before its `created_at`; a member connects, runs and reads last week from Claude Code | 2 wk |
| **M2** | Experiments | Planned slots, blind-forecast freeze, randomization, matching, protocol versioning, readout with randomization test and e-values, e-LOND, power preview, guardrails, approvals, Experiments screen, "Next posts" on Account, E01–E09 suggestions, experiment MCP tools | A simulated 4-week per-post experiment reproduces MODEL §6.3's operating characteristics within ±3 points over ≥ 1,000 replicates; A/A size 0.04–0.06; arm-harm trips in ≤ 5% of null experiments; every blind-forecast hash predates its draw; an agent can't approve its own amendment | 2.5 wk |
| **M3** | Model health and goals | Owner-level model at 20 posts, LFO-CV, release gates and promotion, D1–D3, nowcast, follower DGLM and goal status with hysteresis, Forecasts screen, Account follower panels, model MCP tools | Each detector's false-alarm run length matches its target within ±10% over ≥ 1,000 NB-simulated replicates; goal probabilities match a 100,000-path reference within ±1 point; the follower DGLM recovers known parameters; a failed gate keeps the previous version | 2.5 wk |

**About 7–8 weeks** in total (revision 4: about 19). M2 and M3 can overlap once M1's read models exist. D1 switches on when M3 lands and the account has 20 matured posts.

### M1 status (2026-11-25)

In progress on branch `m1-daily-runs` (commits `e97f425`..`841a480`); not yet merged into `main`.

**Shipped:**

| Area | What | Commit |
|---|---|---|
| X integration | `socialmgr connect` (OAuth 2.0 PKCE, Native App, handle check), token refresh under a file lock with the rotated refresh token committed first, owner provider (30-day timeline with non-public metrics, follower count, newest-first follower paging), `socialmgr probe` | `e97f425` |
| Run engine | `socialmgr run`: ordered, resumable steps (collect → regimes → match planned → label → freeze as-of → score → fit curve → briefing → backup), file run lock, billing once per resource per UTC day, monthly cap | `c5aa21b` |
| Labels | Rule labels and text features, MinHash near-duplicate flags, LLM labeller seam: `claude_code` (default, headless `claude -p --model haiku`), `anthropic_api` (`uv sync --extra api`), `none` | `c5aa21b`, `1123198` |
| Models | Prior-only NB2 champion, trailing-median challenger, versioned Weibull maturity curve fitted from capture pairs, scoring (PIT, CRPS, 50/80% hits), regime registry seeded with MODEL §2e dates plus the `param.rs` check | `c5aa21b` |
| Planned posts | `socialmgr plan` and `register_planned_post`: live forecast frozen at registration, guardrails (near-duplicate block, 3h spacing warning), matched by text or time ±30 min | `c5aa21b` |
| Screens | Overview (and its day-zero state) with `GET /api/overview`; Posts detail drawer with `GET /api/posts/{id}` | `1b516d1`, `e6c35f7` |
| First real run | Reposts excluded from forecasts and scores, expected X partial errors silenced, back-fill follower count, labeller hardening | `1123198` |
| MCP | `socialmgr mcp` (stdio): the eleven M1 tools of §8, `socialmgr://briefing/latest`, prompts `weekly_review` and `plan_next_week` | `841a480` |

Still placeholders until their milestone: the Overview briefing panel, follower forecast band, model health, goals and planned-posts areas (M3, M2); `set_goal`, `get_follower_forecast` and the model-card resource (M3).

**Acceptance criteria:**

| Criterion | How it is checked |
|---|---|
| Irregular 1–3 day gaps: every post gets a maturity capture and is scored or flagged `late_maturity` | `tests/test_m1_acceptance.py`: `test_irregular_gaps_every_post_matures_and_is_scored`, `test_a_long_gap_flags_late_maturity` (16-day gap) |
| An interrupted run resumes without re-billing in the same UTC day | `tests/test_runs.py`: `test_interrupted_run_resumes_without_recollecting_or_rebilling`, `test_second_run_same_utc_day_bills_only_new_reads`; on the real account a second run the same UTC day was billed $0 |
| Every as-of forecast recomputes bit-for-bit from its manifest and doesn't change when later captures are perturbed | `tests/test_m1_acceptance.py`: `test_as_of_forecasts_recompute_and_ignore_perturbed_later_captures` |
| A planned post's forecast is frozen before its `created_at` | `tests/test_m1_acceptance.py`: `test_planned_post_forecast_is_frozen_before_the_post_and_scored`; `tests/test_mcp.py`: `test_register_planned_post_freezes_and_guards` |
| A member connects, runs and reads last week from Claude Code | Connect and real runs verified on the owner's account on 2026-11-25 (18 posts: 17 reposts and 1 reply; labels from Claude Code; backup written to the configured synced folder). The Claude Code read goes through the MCP server: a contract test per tool in `tests/test_mcp.py`, and a stdio smoke test against the real connected database (initialize, list tools, `get_briefing`, `get_posts`) |

Also checked: the pipeline recovers a non-prior maturity curve within 5% (`test_pipeline_recovers_a_non_prior_maturity_curve`), and the curve fit over 20 replicates per curve (`tests/test_m1_models.py`).

**Not yet verified:**
- A whole session inside Claude Code (`claude mcp add`, then the `weekly_review` prompt) is not recorded; only the stdio smoke test is.
- The full `scripts/check.sh` (with end to end) last passed at `e6c35f7` (120 tests, 15 e2e). The two later commits record 123 tests and the MCP contract tests, not a full run.
- The live GitHub `param.rs` call is mocked in tests; a real call is not recorded.
- The day-1 and day-2 probes on a real account, and whether a refresh token survives 7 days unused, are not recorded yet.
- The owner's account has no original post yet, so no original post has been forecast and scored on real data.

---

### M3 status (2026-11-26)

On branch `m3-model-health`. Everything below runs in the daily run, in this order after scoring: `review_model` → `nowcast` → `monitors` → `experiments` → `fit_curve` → `followers` → `briefing`.

**Shipped:**

| Area | What |
|---|---|
| Owner-level model | `nb2_owner` (seeded multi-chain sampler, R̂/ESS reported); identical to the prior-only model below 20 owner posts. Forecast manifests name their model plugin, so promotion switches new forecasts while old ones still recompute |
| Promotion reviews | LFO-CV backtest against the trailing median and all MODEL §5 gates; first review at 40 matured posts, then every 21; a failed gate keeps the champion; reviews and gates on Forecasts and in `model_status` |
| Nowcast | Provisional I48 for posts under 48h, with P(I48 > the frozen median), on Posts and in `get_posts` |
| Monitors | D1 location, scale and follower-gain CUSUMs, D3 ACI, D2 goal check, diagnostics every 21 posts; armed at 20 matured posts; alarms to acknowledge on Forecasts or with `acknowledge_alarm`; restart after a promotion |
| Followers | Follower model per run interval, 7/30/90-day forecasts from 4,000 paths frozen every run and scored when their horizon passes (Account shows past forecasts against actuals), posting scenarios, `get_follower_forecast` |
| Goals | Followers (by a date up to 90 days ahead) and posts (48h impressions); status with hysteresis kept per run; Set goal dialog with a live preview; `set_goal` |
| Screens | Forecasts (model card, gates, calibration, drift monitor, changes), Account follower panels, Overview follower band and model health |
| MCP | `get_follower_forecast`, `set_goal`, `model_status`, `get_calibration`, `get_monitors`, `acknowledge_alarm`, resource `socialmgr://model/card` |

**Acceptance criteria:**

| Criterion | How it is checked |
|---|---|
| Each detector's false-alarm run length matches its target within ±10% over ≥ 1,000 NB-simulated replicates | `tests/test_monitors.py` (slow): location 300.9 vs 300, scale 297.8 vs 300, follower chart 100.0 / 49.7 / 33.4 vs 100 / 50 / 33.3 at G = 1 / 2 / 3, 36,000 replicates per chart |
| Goal probabilities match a 100,000-path reference within ±1 point | `tests/test_followers.py` (slow): 3,600 of 3,600 estimates within ±1 point (largest error 0.51) over 9 goals × 100 seeds × prior-only and fitted fits |
| The follower DGLM recovers known parameters | `tests/test_followers.py` (slow): 90% interval coverage 0.89–0.95 for π, φ_G, φ_L, organic level and loss rate over 1,000 simulated histories with irregular gaps |
| A failed gate keeps the previous version | `tests/test_m3_release.py`: miscalibrated or short histories are held; `tests/test_m3_runs.py`: the fixture's review is held and the champion stays `nb2`; `tests/test_champion.py`: a promotion switches new forecasts only |

**Not yet verified:**
- No real promotion: the owner's account has fewer than 40 matured posts. On the fixture the owner-level model is held (it loses to the trailing median, which matches the fixture's generating process).
- Per-post follows (`/2/tweets/analytics`) still need the X app in a Project, so "Posts that brought followers" is an empty state.
- Activity covariates and the breakout impulse in the follower model have no inputs yet; owner deviations by type, format and link (60 posts) and D2's peer check are not built.

---

### M4: public journal (2026-11-26)

Added after v1 at the owner's request: the canvas's five public pages (Journal, Journal Entry, Experiments, Experiment, Method) as a static site, so the account can grow in public.

**Decisions (owner, 2026-11-26):**

| Question | Decision |
|---|---|
| Hosting | GitHub Pages, from a separate public repository; the app's repository stays private |
| Updates | The numbers (followers, scored posts, hit rate, experiments) refresh after each daily run, once the first entry is published; weekly entries become public only when the owner publishes them |
| Entries | The app drafts each weekly entry from the week's data (week 0 introduces the plan); the owner edits and publishes. No MCP tool can publish |
| Pages | All five designed pages |

**How it works:**
- Entries are markdown files with a small front matter in `data/journal/entries/` (local, private). Experiment write-ups ("answer", "why I tested it", "what I changed") live in `data/journal/experiments/<key>.md`.
- `socialmgr journal draft [--week N]` writes a draft with TODO markers where only the owner knows the story. `socialmgr journal publish --week N` refuses an entry that still has them, marks it published and pushes the site.
- The site is the prebuilt public app (`web/dist-journal`) plus one `data.json` snapshot built with `preview=false`, so drafts never leave the machine. `socialmgr serve` previews it at `/journal/`, drafts included and marked.
- Publishing commits to the configured repository (`SOCIALMGR_JOURNAL__REPO`) with the owner's git identity, never force-pushes, and never touches the app's own repository. A failed refresh is a caveat in the next briefing, not a failed run.
- Public data is limited to followers, impressions, likes per 1k, forecast ranges and outcomes of the owner's posts, experiment results, and published entries.

---

## 10. Testing

- **Unit:** label rules, orchestrator (order, resume, idempotency), capture ages and maturity offsets, randomization, slot matching, e-LOND bookkeeping, MinHash thresholds, cost tally.
- **Integration (real SQLite file):** a run killed mid-step and resumed; token refresh after a multi-day gap and interrupted mid-write; partial API responses; exact zero vs omitted fields; deletion keeps hashes and scores; run, `serve` and MCP open at once.
- **Simulation (replicated):** model recovers known effects at irregular gaps; maturity curve recovers a known shape; as-of forecasts leak nothing; experiment operating characteristics; detector ARL0/ARL1; power formula vs simulation within ±0.03.
- **Extension seams:** a fake peer provider, a second prior source and a second model family run end to end through config (§13.3).
- **Contract:** an MCP schema test per tool; refused calls return a structured reason; a static check that no code path reaches an X write endpoint.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Prior-only forecasts for the first ~20 posts are uncalibrated | Labelled as such, kept out of calibration, D1 and gates; peers can be added later behind the `XClient` interface |
| `/2/tweets/analytics` unavailable or expensive | Day-1 probe; outcomes through the maturity curve |
| Batch measurement is noisier than fixed-age snapshots | Maturity curve fitted from repeated captures and gated; daily runs give dense capture ages |
| Experiments are underpowered at small n | Three-number power preview, preference for 2× hypotheses, plain "inconclusive" readouts |
| The ranker changes mid-experiment | `param.rs` check opens a regime; regime fixed effects; readouts flag change-points |
| Agents over-claim | Templated summaries, record IDs, approvals only in the web app or CLI |
| A member skips runs | Guardrails are checked only at runs; the briefing shows the gap; ask for daily runs while an experiment is live |

---

## 12. Decision log (2026-11-25)

| Topic | Decision |
|---|---|
| Docs | Plan, review and research docs committed to this repo |
| Remote | `GemovicNemanja/socials-manager` (private) |
| Frontend | pnpm + Tailwind v4 |
| Platform and CI | macOS first; GitHub Actions |
| Who and where | Each team member runs locally, for their own X account |
| Account | Brand-new, no posts; each member sets handle and time zone in config |
| X developer app | Each member creates their own before M1 |
| Topics | Software development + AI default, editable |
| Backups | Configurable folder |
| Statistics | MODEL.md kept as written, minus peer-dependent parts (§6) |
| Peers | None in v1; prior-only forecasts until ~20 posts accepted |
| Scope | Posts vs expected range, MCP agent access, experiments |
| Post status | MODEL §4.1 wording: above / as / below expected; Working / Near / Not working only for goals |
| 48h values | Estimated with capture age; "provisional" under 48h |
| Follower chart | Interpolated line between runs, styled as estimated, display only (never fed to models) |
| Range control | 24h / 7d / 28d / 90d |
| Design additions | Run footer in the sidebar, briefing panel on Overview; core states and forms (day-0 empty, run in progress, API error, register planned post, set goal, approve) designed before building; canvas updated first, then built |
| Theme | Dark, desktop ≥ 1280px |
| Premium | From launch; E12 dropped |
| Budgets | Monthly cap in config, default $15 |
| Approvals | Web app or CLI only |
| Journal | Out of v1; added as M4 (static site on GitHub Pages, owner-published entries) |
| Run cadence | About daily |
| Deletions | Removed at the next run |
| MCP schemas | Pydantic, per milestone |
| Draw store | Append-only database table |
| Embeddings | None in v1 |
| Labelling backend | Headless Claude Code on the member's subscription by default; no Anthropic API key needed |
| Notifications | None in v1 (revision 5 removes them; the briefing covers it) |
| Setup screen | Replaced by `socialmgr connect` (revision 5) |
| Database | SQLite instead of PostgreSQL (revision 5) |
| Extensibility | Peers, other or self-adapting models, other platforms and hosting must land later without rework: interfaces and multi-account schema from M0 (§13) |
| Reposts | Stored, but never forecast, scored, LLM-labelled, shown on Posts or counted: they are someone else's content and X reports 0 impressions for them (M1) |
| First-run back-fill | Posts made before the first run (the 30-day back-fill) take the earliest follower count instead of 0, flagged in the manifest as `followers_source=first_run_backfill` (M1) |
| Billing | Billable reads are charged once per resource per UTC day (`resource_reads`), so a re-run or resumed run the same UTC day isn't billed again; collection stops at the monthly cap (M1) |
| Confidence | Capped at "too early" while forecasts are prior-only, whatever the post count (M1) |
| M1 models | Prior-only NB2 (§2a priors, `prior_only_uncalibrated`) is the champion; the trailing median runs as challenger and is the baseline for the M3 release gates (M1) |
| Maturity curve | Two-parameter Weibull, MAP fit from capture pairs with the BetaBinomial pair likelihood (MODEL §1.3); the prior curve until 30 pairs; each forecast keeps the curve version frozen with it. Curve-parameter uncertainty in the predictive draws is deferred (M1) |
| Regimes | The latest commit touching `param.rs` in `xai-org/x-algorithm`, via the GitHub API, opens a regime; classifying the change and sync-header dates are deferred (M1) |
| Labeller sandbox | Headless Claude Code runs from the system temp folder, outside any project, so a repository's `CLAUDE.md`, hooks or settings can't change the labelling call; a missing structured result falls back to the text result, and a failure reports its reason as a briefing caveat (M1) |
| e-values for e-LOND | The mSPRT likelihood ratio (MODEL §6.7's other allowed e-value) feeds e-LOND. The calibrator e = 1/(2√p) on a 9,999-draw randomization p caps at 50, below e-LOND's threshold after the first test, so nothing could ever be confirmed. The randomization p stays in every readout as MODEL §6.2's cross-check (M2) |
| Analysis posterior | Seeded Gibbs sampler for MODEL §6.2 (t errors as a scale mixture, day effects, Lin interaction, carryover), ~0.5 s per readout. τ prior sd fixed at s0 = 0.2 (the value §6.3's rule was calibrated with) and s_fam = 0.1, since one experiment has at most 3 factors. Measured over ≥ 1,000 replicates: false adoption 3.1% (target 2.3), 1.25×/1.5×/2× adoption 16.9/48.1/89.3% (17.7/48.4/91.3), A/A size 5.1%, arm harm under a null ≤ 0.1% (M2) |
| Owner-level model | `nb2_owner`: the §2a NB2 with a_in, a_oon, log φ, β, λ and ρ_th sampled (seeded multi-chain Metropolis in numpy; q integrated by adaptive Gauss–Hermite); below 20 owner posts it is exactly the prior-only model. Promotion is an explicit switch: each forecast manifest names its model plugin, and the champion is the latest promotion review's choice (M3) |
| Promotion reviews | First review at 40 matured owner posts (20 to train, 20 to score: the gates need 20 scored posts), then every 21. Rolling refits use a lighter sampler (300 warmup, 500 draws); gate 6 reads a full fit. Gate 4's CUSUM runs over the last 63 backtest posts: over a whole backtest its false-hold chance grows with length (13% at 57 posts, 63% at 300). A failed gate keeps the previous champion; a promotion restarts the monitors (M3) |
| Detectors | D1 location h = 7.62 (as MODEL); scale chart h = 16.08 upper / 9.59 lower for ARL0 300 two-sided; follower chart h = 5.60 / 4.42 / 3.77 for G = 1 / 2 / 3 days. Measured false-alarm run lengths within ±3% of target over 36,000 NB replicates per chart. ARL1 for a 30% drop at h = 7.62: 43 / 24 / 12 posts at φ = 1.5 / 3 / 8. D2's hit-count test uses a "goal at the typical level" null. While an alarm is open, the same chart firing the same way adds to it instead of paging again (M3) |
| Follower model | Gaussian log-level filter with Laplace updates (not a conjugate-gamma DGLM, because the gain mean exp(η) + π·X is additive); zero-inflated NB2 replaces Berry–West's DCMM when zeros dominate. Prior-only for the first 4 weeks of counts. Goal probabilities from 4,000 paths are within ±1 point of a 100,000-path reference (3,600/3,600) thanks to exact final counts, Gauss–Hermite over π and control variates. Follower goals reach at most 90 days, the forecast's longest horizon (M3) |
| Posts goal | θ_S = the champion's typical original now (a point value) plus the scope effect b_S, whose posterior from the last 21 originals' residuals against their frozen forecasts carries the uncertainty (M3) |
| Nowcast | Precision weighting until 50 owner trajectories; then the regression only while it beats the frozen forecast in every age band (the nowcast gate). Shown as provisional with P(I48 > the frozen median) (M3) |
| External backtest | `socialmgr backtest` reads other accounts' public posts with the owner's X access (cached, billed against the cap). First run (2026-11-26, 4 accounts, one per size band, $5.59): the starting assumptions are honest but too wide (91–100% inside the 80% range) and off-centre by up to 5×; the trailing median is overconfident (40–52%); the owner model converges once sampling continues until R-hat and ESS meet the bar (owner-0.2); MODEL §5 gate 5 now compares widths against the recalibrated baseline (owner decision) |
| Team install | One command (`scripts/bootstrap.sh`, a public gist since the repo is private) installs the tools, clones, builds and runs `socialmgr setup`: `.env`, X connection, Claude Code + MCP at user scope, a Social Manager launcher app and a daily-run launchd job for every member; re-running it updates and keeps answers. Each member creates their own X developer app and pays for their own reads (owner decision 2026-11-26). The journal refreshes only on installs with a public repository configured (the owner's) |
| Data retention | No code path deletes history (append-only triggers); backups now keep one a month for good after the daily/weekly tiers, mirror the journal markdown, never overwrite each other, and `restore` backs up the current database first (owner request 2026-11-26) |
| Arm-harm confidence sequence | Design-based (valid by the randomization blocks alone), computed only over blocks whose posts have all matured. It catches a 0.5× arm in 20% of 4-week experiments and 57% of 8-week ones: weak by construction, because the bar is 0.8× and there are about 28 blocks (M2) |

---

## 13. Built to extend

v1 is small, but its seams are the ones the later features need. Each future feature below is an **addition** (a new adapter, model, step, screen or tool) behind an interface that exists from M0. None of them changes the schema's shape, the run's structure, the forecast record or the scoring.

### 13.1 The interfaces (in `socialmgr/core`, from M0)

| Interface | v1 implementation | What it decouples |
|---|---|---|
| `DataProvider` (with capability flags: owner metrics, public metrics, search, follower lists, analytics) and a pricing model | `x_official`, `fake` | Where data comes from. The collector plans each run from the accounts' roles and the providers' capabilities, not from hard-coded endpoints. |
| `PlatformProfile` (YAML, MODEL §1.6) | `x.yaml` | Platform mechanics: horizon, cold start, observable metrics, change-tracker source. LinkedIn or Instagram add a profile and a provider. |
| `RunStep` registry | the 11 steps in §6 | What a run does. A new capability adds a step (e.g. "collect peers") in its place in the order; resume and checkpointing work unchanged. |
| `FeatureSet` (versioned) | MODEL §1.4 features without embeddings | Model inputs. New features add a version; old forecasts keep the version they were frozen with. |
| `PriorSource` | MODEL §2a hand-set priors | Where the model's priors come from. The peer-panel prior (MODEL §3) is a second implementation; the model code doesn't change. |
| `ForecastModel`: `fit(dataset, prior, as_of) → artifact`, `predict(subject, as_of) → draws` | hierarchical NB2 (MODEL §2a) and the trailing-median baseline | The maths. Any model that returns joint draws can be frozen, scored, gated and promoted the same way. |
| `ModelRegistry` (champion plus any number of challengers) | one champion, the baselines as benchmarks | Which model is live. Challengers are fitted and scored in shadow every run under the same rules. |
| `ExperimentDesign` and `AllocationPolicy` | per-post micro-randomization and 4-day switchback; fixed π = 0.5 | Experiment types and how arms are drawn. ITS with controls and bandits (MODEL §6.5) are later implementations. |
| Application services | used by the API, MCP tools and CLI | Interfaces. A new screen or tool calls an existing service; hosting later wraps the same services. |

Because forecasts store **joint posterior draws plus a hashed input manifest** (not model internals), scoring, calibration, monitors, goal status and experiment readouts are model-agnostic.

### 13.2 How each later feature lands

| Later feature | What gets added | What stays unchanged |
|---|---|---|
| **Tracking other accounts (peers, competitors)** | A peer provider adapter (official API or twitterapi.io), `accounts` rows with `role = peer`, a "collect peers" run step, a peer-panel `PriorSource`, the Collection screen and peer MCP tools | Posts, snapshots, forecasts and scores tables; the owner pipeline; every screen built in v1 |
| **Better predictions from peers** | The joint hierarchical fit and robust MAP prior (MODEL §3), the breakout model (§2d), D0, default goals from peer bands | The NB2 model code (it already takes a prior), the gates, the scoring |
| **A different or better model** | A new `ForecastModel` registered as a challenger | Forecast freezing, scoring, the release gates (MODEL §5), promotion, the UI |
| **A self-adapting model** | Already partly in v1: every run refits, and a challenger that passes all gates is promoted automatically, keeping the old version live otherwise. Later: an automatic challenger pool (hyperparameter and feature-set variants generated each week), online weighting of models by recent CRPS (stacking), and drift-triggered refits from D1–D3 alarms | The gates are the safety rail for any adaptation: nothing reaches the champion slot without passing them on out-of-time data |
| **Bandits and adaptive experiments** | A Thompson-sampling `AllocationPolicy` with logged propensities and AIPW readouts (MODEL §6.5) | Slots, blind forecasts, receipts, guardrails, e-LOND |
| **Account-level experiments** | An ITS/BSTS `ExperimentDesign` with peer controls once peers exist | The experiment engine and readout format |
| **LinkedIn, Instagram** | A platform profile and a provider per platform | Everything platform-generic in MODEL §1.6 |
| **Insights library, Patterns screen, public journal** | New services, tools and screens on top of existing records | The data they read |
| **Shared team install or hosting** | PostgreSQL via SQLAlchemy (same models, Alembic migrations), auth, a scheduler that calls `start_run`, the dense capture schedule (MODEL §1.3 hosted mode) | `workspace_id` and `account_id` are already on every row; services, tools and screens are unchanged |

### 13.3 Rules that keep this true

- **Core never imports a provider SDK**; providers and models are chosen in config.
- **Never store model internals in shared records.** Forecasts are draws plus a manifest; any model can produce them.
- **Every new model, prior or feature set enters as a challenger** and must pass the release gates before it serves forecasts.
- **Every new capability is a service first**, then a web route and an MCP tool on top of it.
- **M0 proves the seams:** the test suite plugs in a second fake provider (public metrics only, as a peer would be), a second `PriorSource` and a second `ForecastModel` through config, with no changes outside their own modules.
