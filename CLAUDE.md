# Working on Social Manager

The plan is `IMPLEMENTATION_PLAN.md` (revision 5); the statistics are `docs/MODEL.md`, which governs where they differ. Designs: Wonder file "Social Manager", Designs page, the "V1" artboards.

## Definition of done

Validate each part as it is finished, not at the end of a milestone. A change is done only when:

1. **It has tests at the level it lives at.** Unit tests for pure logic; integration tests against a real SQLite file for anything that touches the database; simulation tests (replicated, never single runs) for statistical claims; a contract test for every API route and MCP tool.
2. **The screens it touches have an end-to-end test** in `web/e2e/` that runs against the real backend and fixture data, and the screenshot in `web/e2e/.results/` has been looked at against the canvas design.
3. **`scripts/check.sh` passes** (lint, format, types, tests, schema freshness, web build, end-to-end). Use `--fast` while iterating; run the full script before committing.
4. **Milestone acceptance criteria are checked as soon as the pieces they need exist**, by a test where possible, otherwise by a scripted run recorded in the commit message.
5. **Anything not yet verified is said so** in the commit message or the report, with the reason.

## Conventions

- Core code depends only on `socialmgr/core/interfaces.py`; providers, priors and models are chosen in config (plan §13).
- Forecasts are frozen and append-only; never edit or delete rows in `metric_snapshots`, `forecasts`, `forecast_draws`, `scores`.
- All timestamps are timezone-aware UTC.
- The web app reads API types generated from `openapi.json` (`uv run socialmgr openapi`, then `pnpm gen:api`); never hand-edit `web/src/api/types.ts`.
- Git: remote `origin` uses the personal account (SSH alias `github.com-personal`); commit email `gemovic.nemanja@gmail.com`.
