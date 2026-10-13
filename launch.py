#!/usr/bin/env python3
"""
Cross-platform launcher for Aura Tools (Windows, macOS, Linux).

    python launch.py      (Windows)
    python3 launch.py     (macOS / Linux)

First run creates a .venv and installs requirements; later runs start instantly.
Every launch (at most once a day) it updates yt-dlp, since YouTube changes often
and an outdated yt-dlp is the main cause of "HTTP 403 Forbidden" errors.
If ffmpeg is missing it offers to download it (or pass --install-ffmpeg).
"""

import hashlib
import os
import subprocess
import sys
import time
import venv

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(ROOT, ".venv")
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
MARKER = os.path.join(VENV_DIR, ".aura-requirements.sha1")
YTDLP_STAMP = os.path.join(VENV_DIR, ".aura-ytdlp-checked")
YTDLP_CHECK_EVERY = 24 * 3600

if sys.platform.startswith("win"):
    VENV_PYTHON = os.path.join(VENV_DIR, "Scripts", "python.exe")
else:
    VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python")


def fail(msg: str):
    print(f"\n[ERROR] {msg}", flush=True)
    if sys.platform.startswith("win"):
        input("Press Enter to close...")
    sys.exit(1)


def requirements_hash() -> str:
    with open(REQUIREMENTS, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def in_project_venv() -> bool:
    return os.path.normcase(os.path.abspath(sys.prefix)) == os.path.normcase(VENV_DIR)


def ensure_venv():
    if os.path.exists(VENV_PYTHON):
        return
    print("[+] Creating virtual environment (first run only)...", flush=True)
    # clear=True only matters when .venv exists but was made on another OS
    venv.create(VENV_DIR, with_pip=True, clear=os.path.isdir(VENV_DIR))


def ensure_requirements():
    wanted = requirements_hash()
    if os.path.exists(MARKER) and open(MARKER).read().strip() == wanted:
        return
    print("[+] Installing requirements (this can take a minute)...", flush=True)
    result = subprocess.run([VENV_PYTHON, "-m", "pip", "install", "-r", REQUIREMENTS])
    if result.returncode != 0:
        fail("Installing requirements failed. Check your internet connection and try again.")
    with open(MARKER, "w") as f:
        f.write(wanted)


def update_ytdlp(force: bool = False):
    """Upgrade yt-dlp at most once a day. Never blocks startup if offline."""
    try:
        last = os.path.getmtime(YTDLP_STAMP)
    except OSError:
        last = 0
    if not force and time.time() - last < YTDLP_CHECK_EVERY:
        return
    print("[+] Checking for yt-dlp updates...", flush=True)
    try:
        result = subprocess.run(
            [VENV_PYTHON, "-m", "pip", "install", "-q", "-U", "--disable-pip-version-check", "yt-dlp[default]"],
            capture_output=True, text=True, timeout=180,
        )
        ok = result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if ok:
        with open(YTDLP_STAMP, "w") as f:
            f.write(time.ctime())
    else:
        print("[!] Couldn't update yt-dlp (offline?). Continuing with the installed version.")


def offer_deps(force: bool):
    from aura import deps

    missing = deps.missing()
    if not missing:
        if force:
            print(f"[+] ffmpeg already available: {deps.FFMPEG.find()}")
        return
    names = ", ".join(f"{d.label} ({d.size_hint}, for {d.purpose})" for d in missing)
    if not force:
        print(f"[!] Missing for the YouTube downloader: {names}")
        if not sys.stdin or not sys.stdin.isatty():
            print("    You can install it later from the YouTube downloader page.")
            return
        answer = input("    Download and set it up now? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            print("    Skipped. You can install it later from the YouTube downloader page.")
            return
    for dep in missing:
        print(f"[+] Installing {dep.label}...", flush=True)
        deps.install_in_terminal(dep)


def run_app():
    from aura import server

    try:
        server.main()
    except KeyboardInterrupt:
        pass


def main():
    install_deps = "--install-ffmpeg" in sys.argv

    if getattr(sys, "frozen", False):  # packaged by build.py: dependencies are bundled
        offer_deps(install_deps)  # yt-dlp is bundled in builds: rebuild to update it
        return run_app()

    if sys.version_info < (3, 9):
        fail(f"Python 3.9 or newer is required (found {sys.version.split()[0]}).")

    os.chdir(ROOT)
    if not in_project_venv():
        ensure_venv()
        # Re-run this script inside the venv so the app uses its packages.
        args = [VENV_PYTHON, os.path.abspath(__file__)] + sys.argv[1:]
        if not sys.platform.startswith("win"):
            os.execv(VENV_PYTHON, args)  # replace this process: one process, clean Ctrl+C
        try:
            sys.exit(subprocess.call(args))
        except KeyboardInterrupt:
            sys.exit(0)

    ensure_requirements()
    update_ytdlp(force="--update" in sys.argv)
    offer_deps(install_deps)
    run_app()


if __name__ == "__main__":
    main()
