# Social Manager

A local analytics and experimentation workbench for one X account. Each team member runs their own copy on their own Mac, for their own account. Every post gets a forecast frozen before it is published (an 80% range of 48-hour impressions) and is scored against it once it is 48 hours old. See [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for the plan and [`docs/MODEL.md`](docs/MODEL.md) for the statistics.

## Status

**M1, M2 and M3 are built** (the status sections in the plan, §9, have the details). What works today:

- `socialmgr connect` links your X account (OAuth 2.0 with PKCE; tokens stay in the local database).
- `socialmgr run` collects your posts, metrics and followers, labels new posts, freezes forecasts, scores posts that reached 48 hours, reviews the model, gives posts under 48 hours a provisional estimate, runs the drift monitors and experiments, freezes a follower forecast and updates goal status, then writes a briefing and makes a backup. An interrupted run picks up where it stopped.
- Five screens: Overview, Posts, Experiments, Account (followers against their forecast, goal, next posts) and Forecasts (model card, release gates, calibration, drift monitors); planned posts and experiment slots; the MCP server for Claude Code.

Know before you read the numbers:

- **Forecasts start prior-only.** They come from the model's priors until 40 of your posts have matured; then a promotion review backtests the owner-level model and switches to it only if every release gate passes (Forecasts shows each gate). Until then forecasts are labelled `prior_only_uncalibrated` and agent answers say confidence "too early".
- **Drift monitors switch on at 20 matured posts**, and the follower forecast is labelled prior-only for its first 4 weeks of counts.
- **Reposts are stored but never forecast, scored or shown.** They are someone else's content (X reports 0 impressions for them).

## Setting it up on your Mac

### 1. Prerequisites

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.13 for you), Node 24 and [pnpm](https://pnpm.io/).
- [Claude Code](https://claude.com/claude-code), installed and logged in. Runs label new posts with it (headless `claude -p --model haiku`, on your own subscription, no API key). Without it, runs still work; the briefing says labels are pending and the next run retries.

### 2. Install

```sh
git clone <repo URL> socials-manager && cd socials-manager
uv sync
(cd web && pnpm install && pnpm build)
uv run socialmgr init        # creates data/socialmgr.sqlite3
```

Run every `socialmgr` command from the repo folder: `.env` and the default `data/` folder are read from the current directory.

### 3. Create your X developer app

In the X developer portal:

1. Create an app. Under **User authentication settings**, choose **OAuth 2.0**, app type **Native App** (a public client, no secret), permissions **Read**.
2. Callback URI: `http://127.0.0.1:8765/auth/x/callback`. X also asks for a website URL; any URL you own (your X profile is fine) works.
3. Copy the **OAuth 2.0 Client ID**. You don't need the client secret, API key or bearer token.
4. Buy API credits and **set a spending limit** in the portal. The X API bills per resource read.

### 4. Configure `.env`

```sh
cp .env.example .env
```

Then set at least:

```sh
SOCIALMGR_ACCOUNT__HANDLE=@your_handle
SOCIALMGR_ACCOUNT__TZ=Europe/Belgrade
SOCIALMGR_BACKUP_DIR="/Users/you/Library/CloudStorage/ProtonDrive-you@proton.me-folder/socialmgr"   # or an iCloud Drive folder
SOCIALMGR_PLUGINS__PROVIDERS=["x"]
SOCIALMGR_X__CLIENT_ID=your-client-id
```

`.env.example` lists every other setting with a comment. Environment variables override `.env`; nested settings use `__`. `.env` is never committed.

### 5. Connect

```sh
uv run socialmgr connect
```

It opens X in your browser, waits for you to approve (5 minutes), checks that the account matches `SOCIALMGR_ACCOUNT__HANDLE` and stores the tokens. It listens on port 8765, so stop `socialmgr serve` first. If a run ever says to reconnect, run it again.

## Daily use

### Run once a day

```sh
uv run socialmgr run
```

In order: refresh access and collect posts, metrics and followers → check X's published ranker parameters (`param.rs` in `xai-org/x-algorithm`) for changes → match new posts to planned posts → label → freeze forecasts for posts seen for the first time → score posts that reached 48 hours → refit the maturity curve → write the briefing (printed at the end) → back up.

Daily is the target, but skipped days are fine: posts are still captured and scored at the next run. A post is flagged `late_maturity` only if no run captures it between 48 hours and 14 days old. A second run on the same UTC day isn't billed again for what it already read.

**Cost:** about $5 a month of X credit at daily runs and 3 posts a day (plan §5); one run is roughly $0.05–0.20. Collection stops when the month's spend reaches `SOCIALMGR_MONTHLY_CAP_USD` (default $15).

### The web app

```sh
uv run socialmgr serve       # http://127.0.0.1:8765
```

- **Overview:** KPIs against their expected ranges, followers with their forecast band, recent posts, model health, spend.
- **Posts:** every post with its frozen range, outcome and labels (posts under 48 hours show a provisional estimate); click a row for the detail drawer.
- **Experiments:** running, suggested and concluded experiments with readouts and approvals.
- **Account:** follower tiles and funnel, followers against their frozen forecast, how past follower forecasts landed, your goal (**Set goal**: a follower count by a date up to 90 days ahead, or 48h impressions per post), and the next posts.
- **Forecasts:** the model card, the latest promotion review and its gates, calibration of the last 63 originals, drift monitors and alarms to acknowledge, and changes that can shift performance.

### Plan a post

```sh
uv run socialmgr plan --at "2026-11-26T09:30" --text "Draft text"
```

Freezes the forecast now, before the post exists; a later run matches it to the real post (same text, or within 30 minutes) and scores it. Times without an offset are in your `SOCIALMGR_ACCOUNT__TZ`. Near-duplicates of earlier posts are refused; posts within 3 hours of each other are a warning (`--force` accepts it). `--type` is `original`, `quote`, `reply` or `self_reply`.

### Day-1 probes

```sh
uv run socialmgr probe
```

Run it on your first day and again about a day later (the second run checks the follower list order against the first). It reports the timeline rate limit, the follower order and whether X's analytics endpoint is available; reports are saved under `data/probes/`.

## Public journal

A static site of your account growing in public (Journal, weekly entries, Experiments, Method), published to GitHub Pages from a separate public repository.

```sh
uv run socialmgr journal draft --week 0     # writes data/journal/entries/week-00.md (the plan)
# edit it; replace every TODO with your own words
uv run socialmgr serve                      # preview at http://127.0.0.1:8765/journal/ (drafts marked)
uv run socialmgr journal publish --week 0   # marks it published and pushes the site
uv run socialmgr journal status
```

Set the public repository in `.env` once (create it on GitHub first and turn on Pages for its `main` branch):

```sh
SOCIALMGR_JOURNAL__REPO=git@github.com-personal:you/growing-in-public.git
SOCIALMGR_JOURNAL__SITE_URL=https://you.github.io/growing-in-public/
```

After the first publish, every daily run refreshes the public numbers (followers, scored posts, hit rate, experiments). Weekly entries go public only when you publish them, and drafts never leave your Mac. Agents can draft entries (`draft_journal_entry`) but can't publish.

## Using it from Claude Code (MCP)

```sh
claude mcp add socialmgr -- uv run --directory /path/to/socials-manager socialmgr mcp
```

Tools: `get_briefing`, `start_run`, `get_run_status`, `get_posts`, `get_post`, `get_forecast` (frozen, or a draft for a post you might write), `register_planned_post`, `get_schedule`, `set_label`, `get_account_overview`, `export_data`; experiments (`get_experiments`, `get_experiment`, `power_check`, `propose_experiment`, `register_slots`, `schedule_experiment`, `amend_experiment`, `stop_experiment`, `get_readout`, `list_approvals`); followers, goals and the model (`get_follower_forecast`, `set_goal`, `model_status`, `get_calibration`, `get_monitors`, `acknowledge_alarm`); the journal (`get_journal`, `draft_journal_entry`). Resources: `socialmgr://briefing/latest`, `socialmgr://model/card`. Prompts: `weekly_review` ("summarise last week against the forecasts") and `plan_next_week` (draft forecasts, then freeze the ones you confirm).

Try: *"Use the socialmgr weekly_review prompt"*, *"How did my posts do this week?"*, *"Plan three posts for next week and freeze their forecasts."* No tool can post, like, reply or follow on X, change a frozen forecast or approve anything.

## Backups

Every run writes a backup to `SOCIALMGR_BACKUP_DIR` (default `data/backups`) and keeps one a day for 14 days and one a week for 12 weeks (`SOCIALMGR_RUN__BACKUP_KEEP_DAILY`, `SOCIALMGR_RUN__BACKUP_KEEP_WEEKLY`). By hand:

```sh
uv run socialmgr backup
uv run socialmgr restore <backup file>    # replaces the database after an integrity check
```

## Trying it with fake data

The fixture simulates daily runs over a fake account. Keep it in its own data folder and force the fake provider, so it never touches your real data or X:

```sh
export SOCIALMGR_DATA_DIR=demo-data SOCIALMGR_PLUGINS__PROVIDERS='["fake_owner"]'
uv run socialmgr fixture
uv run socialmgr serve
```

## Configuration and extending

Settings come from `SOCIALMGR_`-prefixed environment variables or `.env` (`socialmgr/core/config.py` has them all). Providers, the prior source, models and the labeller are chosen by name (`SOCIALMGR_PLUGINS__...`): a built-in (`x`, `fake_owner`, `fake_public`, `hand_set`, `nb2`, `trailing_median`, `claude_code`, `anthropic_api`, `none`) or a `module:attribute` path to your own implementation of the interfaces in `socialmgr/core/interfaces.py`.

To label through the Anthropic API instead of Claude Code: `uv sync --extra api`, then `SOCIALMGR_PLUGINS__LABELER=anthropic_api` and `SOCIALMGR_ANTHROPIC_API_KEY`.

## Troubleshooting

- **`ModuleNotFoundError: No module named 'socialmgr'` from `uv run socialmgr`.** macOS marked the virtualenv's `.pth` files hidden, and Python 3.13 skips hidden `.pth` files. This happens when the repository sits in a folder iCloud syncs (Desktop & Documents): iCloud hides and evicts files inside `.venv`, and it can evict or conflict-copy the SQLite database too. Keep both out of sync (iCloud skips folders ending in `.nosync`):

  ```sh
  rm -rf .venv && mkdir .venv.nosync && ln -s .venv.nosync .venv
  mv data data.nosync && ln -s data.nosync data
  mv .env .env.nosync && ln -s .env.nosync .env   # keeps your X credentials out of iCloud too
  uv sync
  ```

  Outside iCloud, `chflags nohidden .venv/lib/python3.13/site-packages/*.pth` is enough.
- **"Run `socialmgr connect`" in a briefing.** X refused the stored sign-in (revoked, or unused for too long). Run `socialmgr connect` again; nothing else is lost.
- **One install per account.** X refresh tokens are single-use: don't run two copies of the same data folder (for example a second checkout with a copied `data/`), or one will sign the other out.

## Development

```sh
scripts/check.sh --fast      # lint, format, types, tests, schema freshness, web build
scripts/check.sh             # the same plus the Playwright end-to-end tests; run before committing
```

The end-to-end tests pin the handle, time zone, labeller and data folders but not the provider. If your `.env` sets `SOCIALMGR_PLUGINS__PROVIDERS=["x"]`, override it for the check: `SOCIALMGR_PLUGINS__PROVIDERS='["fake_owner"]' scripts/check.sh`.

`CLAUDE.md` has the definition of done and the conventions. After an API change: `uv run socialmgr openapi`, then `cd web && pnpm gen:api`.
