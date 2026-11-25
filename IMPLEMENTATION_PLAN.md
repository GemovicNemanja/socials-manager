# Social Manager: implementation plan

Last revised 2026-11-25, **revision 5 (simple v1)**. Revision 4 is in git history (commit 3795702).

The maths lives in [`docs/MODEL.md`](docs/MODEL.md) (Model v2) and governs all statistics, except the peer-dependent parts that §6 replaces. The evidence lives in `docs/research/`; the review of the first plan is [`REVIEW.md`](REVIEW.md). The screens are on the canvas (Social Manager file, Designs page).

**Revision 5: simple and quick to build.** The tool is for the owner and their team for now. Each person runs their own copy on their own machine, for their own X account. Anything that only made sense for a hosted, multi-user or public product is removed:
- **removed:** peers and twitterapi.io, hosting (old M7), the public journal (old M6), insights, the Patterns, Collection, Agents and Insights screens, approval codes and assurance levels, MCP client tokens and parity tests, receipt anchors, the budget reservation and job machinery, reminders and notifications, Docker and PostgreSQL, the OS keychain;
- **kept:** MODEL.md's statistics in full (forecasts, maturity curve, scoring, goal status, detectors, follower model, experiments with e-LOND), batch runs, frozen forecasts, MCP agent access and experiments.

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

**Out of scope for v1:** peers and competitor tracking, posting or engaging on X, LinkedIn and Instagram, hosting, shared team dashboards, the public journal, an insight library.

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
| Labelling | Claude Haiku 5.5 (`claude-haiku-5-5`), called synchronously; deterministic rules first | No batch API (no backfill). Owner overrides win. |
| Near-duplicates | MinHash | No embeddings in v1. |
| Frontend | React + TypeScript + Vite, pnpm, Tailwind v4, TanStack Query, uPlot | Dark only, desktop ≥ 1280px. Tokens from the canvas `dashboard/*` set as CSS variables. TS client generated from FastAPI's OpenAPI. |
| Secrets | `.env` (gitignored) for the X app keys and the Anthropic key; X OAuth tokens in a `credentials` table | Local single user, so no keychain. |
| X connection | `socialmgr connect`: opens the browser for OAuth 2.0 PKCE, catches the localhost callback, stores tokens | Replaces the setup page. |
| Backups | SQLite online backup to a configured folder at the end of every run and every session that registers slots or planned posts; `socialmgr restore` | Frozen forecasts and assignments can't be recreated. |
| CI | GitHub Actions: lint, type check, unit, integration and simulation tests | |

### Repository layout

```
socialmgr/
  core/          # domain types, config (Pydantic), XClient interface
  x_api/         # official X API client + fake client for tests
  runs/          # orchestrator, collector, capture ages, cost tally, deletions, backups
  labelling/     # rules, Haiku labeller, label-set versions, MinHash
  modelling/     # features, maturity_curve, nb2, nowcast, followers, detectors, evaluation, regimes
  experiments/   # slots, protocol, randomization, readout, power, e-LOND, guardrails
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

Platform IDs are strings; times are UTC.

| Table | Purpose and key columns |
|---|---|
| `account` | One row: `external_id`, `handle`, `tz`, `premium`, `created_at` |
| `credentials` | X access and refresh tokens, `refreshed_at` |
| `runs` | `run_id`, `started_at`, `cutoff_at`, `finished_at`, `gap_since_prev_h`, `steps` (json), `spend`, `trigger` (cli/agent) |
| `posts` | `external_id`, `created_at`, `type` (original/quote/reply/repost/self_reply), `conversation_id`, `in_reply_to_user_id`, `text`, `text_hash`, `minhash`, `has_url`, `media` (json), `text_len_weighted`, `newlines`, `gap_since_prev_min`, `own_posts_48h`, `followers_at_post`, `slot_id`, `deleted_at` |
| `metric_snapshots` | Append-only, one row per post per run: `post_id`, `run_id`, `fetched_at`, `age_s`, `impressions`, `likes`, `reposts`, `quotes`, `replies`, `bookmarks`, `profile_clicks`, `link_clicks`, `video_q25..q100` |
| `analytics_buckets` | Hourly/daily buckets from `/2/tweets/analytics`, only if the day-1 probe succeeds |
| `follower_snapshots` | Follower count at each run; `new_followers` since the last run (newest-first list) |
| `labels` | `post_id`, `label_set_version`, `field`, `value`, `source` (rule/llm/owner), `confidence` |
| `planned_slots` | `planned_at`, `lever_context`, `experiment_id`, `arm`, `matched_post_id`, `state` (planned/assigned/posted/missed/unmatched) |
| `forecasts` | Append-only: `subject` (slot/post), `kind` (treatment_blind/live/as_of/prior_only_uncalibrated), `model_version`, `curve_version`, `snapshot_run_id`, `metric`, `horizon_h`, `quantiles`, `p_ge_goal`, `input_manifest`, `manifest_hash`, `draws_hash`, `frozen_at` |
| `forecast_draws` | Append-only: 1,000 joint posterior draws per forecast (compressed blob), keyed by hash |
| `scores` | `forecast_id`, `outcome`, `capture_age_h`, `log_g_offset`, `exact`, `late_maturity`, `pit_mid`, `pit_rand`, `crps_log`, `in_80`, `in_50` |
| `maturity_curves`, `model_versions`, `evaluations` | Versioned curve and model parameters, gate results, promotion decisions |
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

## 7. Experiments (MODEL §6)

Kept as MODEL §6 specifies: slots registered for whole local days up to 7 days ahead; treatment-blind forecast frozen before a seeded draw blocked within the day; matching by slot tag or nearest planned time within ±30 min; intention to treat; the analysis model and a 9,999-draw randomization test; the decision rule evaluated at the first run after each experiment-week boundary; at most one 50% extension; arm-harm and unfollow guardrails as confidence sequences; e-LOND at FDR 0.10, the only route to "confirmed"; the three-number power preview.

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
| **M0** | Foundations | Repo layout, config, SQLite schema with append-only triggers, fake X client, fixture account, web shell with the shared components, CI | `socialmgr serve` shows the fixture data in the Posts table; append-only tables reject UPDATE and DELETE; backup and restore round-trip | 3–4 days |
| **M1** | My posts against an expected range, in the app or from my agent | `socialmgr connect` (OAuth PKCE, locked refresh), run orchestrator with resumable steps, collector, follower snapshots, rule and Haiku labels, MinHash guardrail, `register_planned_post` with live forecasts, as-of forecasts, maturity curve, prior-only NB2 and the trailing-median baseline, scoring, regime registry with the `param.rs` check, backups, day-1 probes, Overview and Posts screens, MCP core tools | On a simulated clock with irregular gaps (1–3 days), every post gets a maturity capture and is scored or flagged `late_maturity`; an interrupted run resumes without re-billing in the same UTC day; every as-of forecast recomputes bit-for-bit from its manifest and doesn't change when later captures are perturbed; a planned post's forecast is frozen before its `created_at`; a member connects, runs and reads last week from Claude Code | 2 wk |
| **M2** | Experiments | Planned slots, blind-forecast freeze, randomization, matching, protocol versioning, readout with randomization test and e-values, e-LOND, power preview, guardrails, approvals, Experiments screen, "Next posts" on Account, E01–E09 suggestions, experiment MCP tools | A simulated 4-week per-post experiment reproduces MODEL §6.3's operating characteristics within ±3 points over ≥ 1,000 replicates; A/A size 0.04–0.06; arm-harm trips in ≤ 5% of null experiments; every blind-forecast hash predates its draw; an agent can't approve its own amendment | 2.5 wk |
| **M3** | Model health and goals | Owner-level model at 20 posts, LFO-CV, release gates and promotion, D1–D3, nowcast, follower DGLM and goal status with hysteresis, Forecasts screen, Account follower panels, model MCP tools | Each detector's false-alarm run length matches its target within ±10% over ≥ 1,000 NB-simulated replicates; goal probabilities match a 100,000-path reference within ±1 point; the follower DGLM recovers known parameters; a failed gate keeps the previous version | 2.5 wk |

**About 7–8 weeks** in total (revision 4: about 19). M2 and M3 can overlap once M1's read models exist. D1 switches on when M3 lands and the account has 20 matured posts.

---

## 10. Testing

- **Unit:** label rules, orchestrator (order, resume, idempotency), capture ages and maturity offsets, randomization, slot matching, e-LOND bookkeeping, MinHash thresholds, cost tally.
- **Integration (real SQLite file):** a run killed mid-step and resumed; token refresh after a multi-day gap and interrupted mid-write; partial API responses; exact zero vs omitted fields; deletion keeps hashes and scores; run, `serve` and MCP open at once.
- **Simulation (replicated):** model recovers known effects at irregular gaps; maturity curve recovers a known shape; as-of forecasts leak nothing; experiment operating characteristics; detector ARL0/ARL1; power formula vs simulation within ±0.03.
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
| Journal | Out of v1 |
| Run cadence | About daily |
| Deletions | Removed at the next run |
| MCP schemas | Pydantic, per milestone |
| Draw store | Append-only database table |
| Embeddings | None in v1 |
| Notifications | None in v1 (revision 5 removes them; the briefing covers it) |
| Setup screen | Replaced by `socialmgr connect` (revision 5) |
| Database | SQLite instead of PostgreSQL (revision 5) |
