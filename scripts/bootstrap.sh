#!/usr/bin/env bash
# Social Manager bootstrap: everything from nothing, in one command.
#
#   /bin/bash -c "$(curl -fsSL <this file's raw URL>)"
#
# Gets GitHub CLI if needed, signs you in to GitHub, clones the (private) repository into
# ~/socials-manager, and runs its installer. Run the same command again to update.
# It contains no secrets and no app code: everything else comes from the repository.
set -euo pipefail

SLUG="${SOCIALMGR_REPO:-GemovicNemanja/socials-manager}"
DEST="${SOCIALMGR_HOME:-$HOME/socials-manager}"
TOOLS="${SOCIALMGR_TOOLS:-$HOME/.local/share/socialmgr}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die() {
  printf '\033[31m%s\033[0m\n' "$*" >&2
  exit 1
}

bold "Social Manager setup"
[ "$(uname -s)" = "Darwin" ] || die "This setup is for macOS."

# ------------------------------------------------------------------ git (Apple Command Line Tools)
if ! xcode-select -p >/dev/null 2>&1; then
  bold "Installing Apple's Command Line Tools (git). Follow the dialog that opens."
  xcode-select --install >/dev/null 2>&1 || true
  until xcode-select -p >/dev/null 2>&1; do sleep 10; done
fi
ok "git $(git --version | awk '{print $3}')"

# ------------------------------------------------------------------ GitHub CLI
GH="$(command -v gh || true)"
if [ -z "$GH" ]; then
  GH="$(find "$TOOLS/gh" -path '*/bin/gh' -type f 2>/dev/null | head -1 || true)"
fi
if [ -z "$GH" ]; then
  bold "Installing GitHub CLI into $TOOLS/gh (just for Social Manager)"
  tag="$(curl -fsSL https://api.github.com/repos/cli/cli/releases/latest | grep -m1 '"tag_name"' | cut -d'"' -f4)"
  [ -n "$tag" ] || die "Couldn't look up the GitHub CLI version; check your connection and run this again."
  v="${tag#v}"
  zip="gh_${v}_macOS_universal.zip"
  base="https://github.com/cli/cli/releases/download/$tag"
  tmp="$(mktemp -d)"
  curl -fsSL "$base/$zip" -o "$tmp/$zip"
  curl -fsSL "$base/gh_${v}_checksums.txt" -o "$tmp/sums.txt"
  (cd "$tmp" && grep " $zip\$" sums.txt | shasum -a 256 -c - >/dev/null) ||
    die "The GitHub CLI download failed its checksum; run this again."
  rm -rf "$TOOLS/gh"
  mkdir -p "$TOOLS/gh"
  unzip -q "$tmp/$zip" -d "$TOOLS/gh"
  rm -rf "$tmp"
  GH="$(find "$TOOLS/gh" -path '*/bin/gh' -type f | head -1)"
fi
ok "GitHub CLI $("$GH" --version | head -1 | awk '{print $3}')"

# ------------------------------------------------------------------ GitHub sign-in and access
if ! "$GH" auth status --hostname github.com >/dev/null 2>&1; then
  bold "Sign in to GitHub (your browser opens; paste the code shown here)"
  "$GH" auth login --hostname github.com --git-protocol https --web
fi
"$GH" auth setup-git --hostname github.com >/dev/null 2>&1 || true
login="$("$GH" api user --jq .login)"
ok "Signed in to GitHub as $login"
"$GH" repo view "$SLUG" >/dev/null 2>&1 ||
  die "Your GitHub account ($login) can't see $SLUG yet. Ask the owner to add you as a collaborator,
accept the invitation (https://github.com/notifications), then run this command again."

# ------------------------------------------------------------------ get or update the code
if [ -d "$DEST/.git" ]; then
  bold "Updating $DEST"
  git -C "$DEST" pull --ff-only --quiet ||
    die "Couldn't update $DEST (local changes?). Commit or discard them, then run this again."
else
  bold "Downloading Social Manager into $DEST"
  "$GH" repo clone "$SLUG" "$DEST" -- --quiet
fi
ok "Code at $DEST ($(git -C "$DEST" rev-parse --short HEAD))"

if [ ! -t 0 ] && (exec </dev/tty) 2>/dev/null; then
  exec /bin/bash "$DEST/scripts/install.sh" "$@" </dev/tty # stdin was this script (curl | bash)
fi
exec /bin/bash "$DEST/scripts/install.sh" "$@"
