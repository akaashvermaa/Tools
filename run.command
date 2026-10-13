#!/usr/bin/env bash
# macOS: double-click this file to start Aura Tools (Finder opens it in Terminal).
# In a terminal, on macOS or Linux, you can run ./run.sh instead.

cd "$(dirname "$0")" || exit 1

# Run through bash so this works even when a zip download has dropped run.sh's "executable" flag.
bash ./run.sh "$@"
status=$?

# 0 = closed normally, 130 = stopped with Ctrl+C. Anything else: keep the window open so the message can be read.
if [ "$status" -ne 0 ] && [ "$status" -ne 130 ]; then
  echo
  echo "Aura Tools stopped with an error (code $status). The message above says why."
  read -r -p "Press Enter to close this window..." _
fi
exit "$status"
