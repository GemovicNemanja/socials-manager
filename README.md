# Social Manager

A local analytics and experimentation workbench for one X account. Each team member runs their own copy for their own account. See [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for the plan and [`docs/MODEL.md`](docs/MODEL.md) for the statistics.

## Status

Milestone M0 (foundations): schema, extension interfaces, fake data providers, a fixture account and the Posts screen. Connecting a real X account arrives in M1.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), Python 3.13, Node 24 and pnpm.

```sh
uv sync                                  # Python dependencies
uv run socialmgr init                    # create the local database (data/socialmgr.sqlite3)
uv run socialmgr fixture                 # load a deterministic fixture account
(cd web && pnpm install && pnpm build)   # build the web app
uv run socialmgr serve                   # http://127.0.0.1:8765
```

Other commands: `socialmgr backup`, `socialmgr restore <file>`, `socialmgr openapi` (writes `openapi.json` for the web client).

## Configuration

Settings come from environment variables prefixed `SOCIALMGR_` or a local `.env` file (see `.env.example`). Nested settings use `__`, for example `SOCIALMGR_ACCOUNT__TZ=Europe/Belgrade`.

Providers, the prior source and models are chosen by name in config (`SOCIALMGR_PLUGINS__...`). A name is either a built-in (`fake_owner`, `fake_public`, `hand_set`, `trailing_median`) or a `module:attribute` path to your own implementation of the interfaces in `socialmgr/core/interfaces.py`.

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```
