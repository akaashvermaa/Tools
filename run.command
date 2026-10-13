#!/usr/bin/env bash
# macOS / Linux: start Aura Tools (run.command is the same file, double-clickable in macOS Finder)
cd "$(dirname "$0")" || exit 1
exec python3 launch.py "$@"
