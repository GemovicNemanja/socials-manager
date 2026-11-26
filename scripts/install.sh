#!/usr/bin/env bash
# Social Manager installer: tools, dependencies and the web app, then the guided setup.
#
#   scripts/install.sh [socialmgr setup options]
#
# Safe to run again: it updates the dependencies and the web build and keeps every answer you gave
# (`--reconfigure` asks again). Teammates normally get here through scripts/bootstrap.sh.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="${SOCIALMGR_TOOLS:-$HOME/.local/share/socialmgr}"
NODE_MAJOR=24

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die() {
  printf '\033[31m%s\033[0m\n' "$*" >&2
  exit 1
}
ask_yes() { # ask_yes "Question?" -> 0 for yes (default yes)
  local answer
  if [ -n "${SOCIALMGR_YES:-}" ]; then return 0; fi
  read -r -p "  $1 [Y/n] " answer </dev/tty || answer=""
  [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

bold "Social Manager installer"
echo "  Folder: $REPO"
[ "$(uname -s)" = "Darwin" ] || warn "This installer is made for macOS; the app and the daily run will be skipped."

# ------------------------------------------------------------------ git
if ! git --version >/dev/null 2>&1; then
  die "git isn't available. Install Apple's Command Line Tools (xcode-select --install), then run this again."
fi

# ------------------------------------------------------------------ uv (Python and dependencies)
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -z "$UV" ]; then
  bold "Installing uv (Python manager) into ~/.local/bin"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
  UV="$HOME/.local/bin/uv"
fi
[ -x "$UV" ] || die "uv didn't install; see https://docs.astral.sh/uv/."
ok "uv $("$UV" --version | awk '{print $2}')"

# ------------------------------------------------------------------ Node and pnpm (web app build)
node_major() { "$1" -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0; }

install_node() {
  local arch dist name sums
  case "$(uname -m)" in arm64) arch=arm64 ;; *) arch=x64 ;; esac
  dist="https://nodejs.org/dist/latest-v${NODE_MAJOR}.x"
  sums="$(curl -fsSL "$dist/SHASUMS256.txt")"
  name="$(printf '%s\n' "$sums" | awk '{print $2}' | grep -E "^node-v${NODE_MAJOR}\.[0-9.]+-darwin-${arch}\.tar\.gz$" | head -1)"
  [ -n "$name" ] || die "Couldn't find a Node ${NODE_MAJOR} download for darwin-${arch}."
  bold "Installing ${name%.tar.gz} into $TOOLS/node (just for Social Manager)"
  mkdir -p "$TOOLS"
  local tmp
  tmp="$(mktemp -d)"
  curl -fsSL "$dist/$name" -o "$tmp/$name"
  (cd "$tmp" && printf '%s\n' "$sums" | grep " $name\$" | shasum -a 256 -c - >/dev/null) ||
    die "The Node download failed its checksum; run the installer again."
  rm -rf "$TOOLS/node"
  mkdir -p "$TOOLS/node"
  tar -xzf "$tmp/$name" -C "$TOOLS/node" --strip-components 1
  rm -rf "$tmp"
}

NODE="$(command -v node || true)"
if [ -x "$TOOLS/node/bin/node" ] && [ "$(node_major "$TOOLS/node/bin/node")" -eq "$NODE_MAJOR" ]; then
  NODE="$TOOLS/node/bin/node"
elif [ -z "$NODE" ] || [ "$(node_major "$NODE")" -lt "$NODE_MAJOR" ] ||
  { ! command -v pnpm >/dev/null 2>&1 && ! command -v corepack >/dev/null 2>&1; }; then
  install_node
  NODE="$TOOLS/node/bin/node"
fi
PATH="$(dirname "$NODE"):$PATH"
export PATH
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
if command -v pnpm >/dev/null 2>&1; then PNPM=(pnpm); else PNPM=(corepack pnpm); fi
ok "Node $("$NODE" --version), pnpm $("${PNPM[@]}" --version)"

# ------------------------------------------------------------------ keep local data out of iCloud
# iCloud hides and evicts files in synced folders (Desktop & Documents), which breaks the virtualenv
# and can corrupt the database. It skips folders whose names end in .nosync.
case "$REPO" in
"$HOME/Documents"* | "$HOME/Desktop"* | "$HOME/Library/Mobile Documents"*)
  for d in .venv data; do
    if [ ! -L "$REPO/$d" ]; then
      if [ -d "$REPO/$d" ]; then mv "$REPO/$d" "$REPO/$d.nosync"; else mkdir -p "$REPO/$d.nosync"; fi
      ln -s "$d.nosync" "$REPO/$d"
    fi
  done
  ok "The virtualenv and data/ are kept out of iCloud sync (.nosync)"
  ;;
esac

# ------------------------------------------------------------------ dependencies and the web app
WAS_RUNNING=""
if [ -x "$REPO/.venv/bin/socialmgr" ]; then
  (cd "$REPO" && .venv/bin/socialmgr stop 2>/dev/null | grep -q '^Stopped') && WAS_RUNNING=1
fi

bold "Installing Python dependencies"
(cd "$REPO" && "$UV" sync --locked --quiet)
ok "Python environment ready"

bold "Building the web app"
(cd "$REPO/web" && "${PNPM[@]}" install --frozen-lockfile --silent && "${PNPM[@]}" run --silent build >/dev/null)
ok "Web app built"

[ -n "$WAS_RUNNING" ] && (cd "$REPO" && .venv/bin/socialmgr launch --no-browser >/dev/null) && ok "Restarted the server"

# ------------------------------------------------------------------ Claude Code
if ! command -v claude >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/claude" ] && [ ! -x "$HOME/.claude/local/claude" ]; then
  echo "  Claude Code labels your posts each day and gives your agents the socialmgr tools."
  if ask_yes "Install Claude Code now?"; then
    curl -fsSL https://claude.ai/install.sh | bash
  else
    warn "Skipped; labels wait until Claude Code is installed."
  fi
fi
export PATH="$HOME/.local/bin:$PATH"

# ------------------------------------------------------------------ guided setup
cd "$REPO"
if [ ! -t 0 ] && (exec </dev/tty) 2>/dev/null; then
  exec "$UV" run --quiet socialmgr setup --uv "$UV" "$@" </dev/tty # stdin was the downloaded script
fi
exec "$UV" run --quiet socialmgr setup --uv "$UV" "$@"
