"""Where Aura Tools keeps its own files, on every OS."""

import os
import sys


def app_dir() -> str:
    """Folder next to the executable when frozen, otherwise the project root."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tools_bin() -> str:
    """Downloaded helper programs (ffmpeg)."""
    return os.path.join(app_dir(), "tools", "bin")


def cache_dir() -> str:
    """
    A disk-backed per-user cache folder for in-progress downloads.
    (Not the system temp dir: on some Linux distros /tmp lives in RAM.)
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        path = os.path.join(base, "AuraTools", "Cache")
    elif sys.platform == "darwin":
        path = os.path.expanduser("~/Library/Caches/AuraTools")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        path = os.path.join(base, "aura-tools")
    os.makedirs(path, exist_ok=True)
    return path
