#!/usr/bin/env bash
# Aura Tools launcher for macOS and Linux.
#
#   ./run.sh                    start the app (the first run sets itself up)
#   ./run.sh --no-browser       don't open a browser tab (handy over SSH / on a server)
#   ./run.sh --port 8080        use this port instead of 5000
#   ./run.sh --update           update yt-dlp now      ./run.sh --install-ffmpeg   fetch ffmpeg now
#
# macOS users can double-click run.command instead.

cd "$(dirname "$0")" || exit 1

# The first python that is version 3.9 or newer. Some systems have a "python3" that is too old, or only a
# stub (the Microsoft Store one in Git Bash), so each candidate is actually run rather than just looked up.
find_python() {
  local candidate
  for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python \
                   /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

explain_missing_python() {
  echo "Aura Tools needs Python 3.9 or newer, and none was found on this computer."
  echo
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "Install it from https://www.python.org/downloads/macos/"
    echo "or, if you use Homebrew:   brew install python"
  elif command -v apt-get >/dev/null 2>&1; then
    echo "Install it with:   sudo apt install python3 python3-venv python3-pip"
  elif command -v dnf >/dev/null 2>&1; then
    echo "Install it with:   sudo dnf install python3 python3-pip"
  elif command -v pacman >/dev/null 2>&1; then
    echo "Install it with:   sudo pacman -S python python-pip"
  elif command -v zypper >/dev/null 2>&1; then
    echo "Install it with:   sudo zypper install python3 python3-pip"
  elif command -v apk >/dev/null 2>&1; then
    echo "Install it with:   sudo apk add python3 py3-pip"
  else
    echo "Install Python 3.9+ with your package manager, or from https://www.python.org/downloads/"
  fi
  echo "Then run this again."
}

if ! PYTHON=$(find_python); then
  explain_missing_python
  exit 1
fi

exec "$PYTHON" launch.py "$@"
