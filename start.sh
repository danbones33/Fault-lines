#!/bin/sh
# FAULT LINES for Mac and Linux: run ./start.sh (or double-click it where your system allows).
# The first time it installs what it needs (a few minutes), then it opens the app in your browser.
# Keep this window open while you use the app; press Ctrl+C to stop.
cd "$(dirname "$0")" || exit 1
sh scripts/install.sh || { echo; echo "Setup did not finish. Read the message above, then run ./start.sh again."; exit 1; }
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
exec uv run run.py
