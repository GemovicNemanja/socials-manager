#!/usr/bin/env bash
# Every check a change must pass before it is called done (see CLAUDE.md, "Definition of done").
# Usage: scripts/check.sh            all checks
#        scripts/check.sh --fast     skip the browser end-to-end tests
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

step "Python: lint, format, types"
uv run ruff check .
uv run ruff format --check .
uv run mypy

step "Python: unit, integration and simulation tests"
uv run pytest -q

step "API schema and generated web types are current"
uv run socialmgr openapi --out openapi.json >/dev/null
(cd web && pnpm --silent gen:api >/dev/null)
git diff --exit-code -- openapi.json web/src/api/types.ts

step "Web: lint, types, build"
(cd web && pnpm --silent lint && pnpm --silent build >/dev/null)

if [[ "${1:-}" != "--fast" ]]; then
  step "End to end: real backend + fixture data + browser"
  (cd web && pnpm --silent e2e)
fi

printf '\n\033[32mAll checks passed.\033[0m\n'
