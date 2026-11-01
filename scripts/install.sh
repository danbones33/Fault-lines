#!/bin/sh
# FAULT LINES installer for Mac and Linux. start.sh runs this, then starts the app.
# Safe to run again: each step is skipped when it is already done.
#   1. uv (installs Python and the app's packages for you)
#   2. MiroFish-Offline, the simulation layer, downloaded into ./mirofish (pinned version)
#   3. MiroFish's packages (the first time takes a few minutes and a few GB)
# Nothing here needs an API key. Keys, voices and the local model are set from the app's Settings button.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIROFISH_COMMIT="313fe642853ff9fff05e3ecae2e439886c2d29f4"
MIROFISH_ZIP="https://github.com/nikmcfly/MiroFish-Offline/archive/$MIROFISH_COMMIT.zip"

step() { printf '\n== %s\n' "$1"; }
say() { printf '  %s\n' "$1"; }

printf '\nFAULT LINES setup\n'

step "Checking for uv (it installs Python and the packages)"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv from astral.sh (one time)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Could not install uv. Install it by hand from https://docs.astral.sh/uv/ and run start.sh again."; exit 1
fi
say "uv is ready"

step "Checking for the simulation engine (MiroFish-Offline)"
INSIDE="$ROOT/mirofish/backend"
BESIDE="$(dirname "$ROOT")/mirofish/backend"
if [ -n "$MIROFISH_BACKEND" ] && [ -d "$MIROFISH_BACKEND" ]; then BACKEND="$MIROFISH_BACKEND"
elif [ -d "$INSIDE" ]; then BACKEND="$INSIDE"
elif [ -d "$BESIDE" ]; then BACKEND="$BESIDE"
else
  say "Downloading MiroFish-Offline (about 5 MB)..."
  TMP="$(mktemp -d)"
  curl -LsSf "$MIROFISH_ZIP" -o "$TMP/mirofish.zip"
  uv run --no-project python -m zipfile -e "$TMP/mirofish.zip" "$TMP"
  SRC="$(find "$TMP" -maxdepth 1 -type d -name 'MiroFish-Offline-*' | head -n 1)"
  [ -n "$SRC" ] || { echo "The MiroFish download looked wrong. Try again later."; exit 1; }
  mv "$SRC" "$ROOT/mirofish"
  rm -rf "$TMP"
  BACKEND="$INSIDE"
fi
say "engine at $BACKEND"

if [ ! -x "$BACKEND/.venv/bin/python" ]; then
  step "Installing the engine's packages (first time only: a few minutes, a few GB)"
  (cd "$BACKEND" && uv sync)
fi
if [ ! -f "$BACKEND/.venv/.faultlines-ok" ]; then
  say "checking the engine (once, about half a minute)..."
  if ! "$BACKEND/.venv/bin/python" -c "import oasis, camel, transformers.models.auto" >/dev/null 2>&1; then
    echo "The engine's packages are incomplete. Delete $BACKEND/.venv and run start.sh again."; exit 1
  fi
  echo ok > "$BACKEND/.venv/.faultlines-ok"
fi
say "engine packages are ready"

step "Checking for Ollama (optional: free local AI)"
if command -v ollama >/dev/null 2>&1; then
  say "Ollama is installed. Pick and download a model from the app's Settings."
else
  say "Ollama is not installed. That's fine: use an OpenRouter key instead, or just watch a demo."
  say "For free local AI later: https://ollama.com/download"
fi

printf '\nSetup done. Starting FAULT LINES...\n'
