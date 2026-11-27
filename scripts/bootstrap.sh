#!/usr/bin/env bash
# Social Manager bootstrap: everything from nothing, in one command.
#
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/GemovicNemanja/socials-manager/main/scripts/bootstrap.sh)"
#
# Makes sure git is available, downloads the repository into ~/socials-manager (or updates it), and
# runs its installer. Run the same command again to update. Options after `--` go to the installer,
# e.g. `... bootstrap.sh)" -- --reconfigure`.
#
# To try it next to an existing install without touching it, name an instance:
#   SOCIALMGR_INSTANCE=test /bin/bash -c "$(curl -fsSL .../bootstrap.sh)"
# It goes to ~/socials-manager-test with its own app, daily run, MCP server and dashboard port.
set -euo pipefail

REPO_URL="${SOCIALMGR_REPO_URL:-https://github.com/GemovicNemanja/socials-manager.git}"
INSTANCE="${SOCIALMGR_INSTANCE:-}"
DEST="${SOCIALMGR_HOME:-$HOME/socials-manager${INSTANCE:+-$INSTANCE}}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die() {
  printf '\033[31m%s\033[0m\n' "$*" >&2
  exit 1
}

bold "Social Manager setup${INSTANCE:+ (instance: $INSTANCE)}"
[ "$(uname -s)" = "Darwin" ] || die "This setup is for macOS."

# ------------------------------------------------------------------ git (Apple Command Line Tools)
if ! xcode-select -p >/dev/null 2>&1; then
  bold "Installing Apple's Command Line Tools (git). Follow the dialog that opens."
  xcode-select --install >/dev/null 2>&1 || true
  until xcode-select -p >/dev/null 2>&1; do sleep 10; done
fi
ok "git $(git --version | awk '{print $3}')"

# ------------------------------------------------------------------ get or update the code
if [ -d "$DEST/.git" ]; then
  bold "Updating $DEST"
  git -C "$DEST" pull --ff-only --quiet ||
    die "Couldn't update $DEST (local changes?). Commit or discard them, then run this again."
else
  bold "Downloading Social Manager into $DEST"
  git clone --quiet "$REPO_URL" "$DEST"
fi
ok "Code at $DEST ($(git -C "$DEST" rev-parse --short HEAD))"

if [ ! -t 0 ] && (exec </dev/tty) 2>/dev/null; then
  exec /bin/bash "$DEST/scripts/install.sh" "$@" </dev/tty # stdin was this script (curl | bash)
fi
exec /bin/bash "$DEST/scripts/install.sh" "$@"
