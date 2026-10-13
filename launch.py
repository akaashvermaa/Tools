#!/usr/bin/env python3
"""
Cross-platform launcher for Aura Tools (Windows, macOS, Linux).

    python launch.py      (Windows)       or double-click run.bat
    ./run.sh              (Linux / macOS) or double-click run.command on macOS

Options:  --no-browser   --port N   --update   --install-ffmpeg   --help

First run creates a .venv and installs requirements; later runs start instantly.
Every launch (at most once a day) it updates yt-dlp, since YouTube changes often
and an outdated yt-dlp is the main cause of "HTTP 403 Forbidden" errors.
If ffmpeg is missing it offers to download it (or pass --install-ffmpeg).
"""

import hashlib
import os
import shutil
import subprocess
import sys
import time
import venv

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(ROOT, ".venv")
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
OPTIONAL_REQUIREMENTS = os.path.join(ROOT, "requirements-optional.txt")  # nice-to-haves: the app runs without them
MARKER = os.path.join(VENV_DIR, ".aura-requirements.sha1")
OPTIONAL_MARKER = os.path.join(VENV_DIR, ".aura-optional.sha1")
YTDLP_STAMP = os.path.join(VENV_DIR, ".aura-ytdlp-checked")
YTDLP_CHECK_EVERY = 24 * 3600

if sys.platform.startswith("win"):
    VENV_PYTHON = os.path.join(VENV_DIR, "Scripts", "python.exe")
else:
    VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python")


def fail(msg: str):
    print(f"\n[ERROR] {msg}", flush=True)
    if sys.platform.startswith("win") and sys.stdin and sys.stdin.isatty():
        input("Press Enter to close...")  # a double-clicked window would otherwise vanish before it can be read
    sys.exit(1)


def file_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def venv_hint() -> str:
    """What to do when Python can't make a virtual environment, for this operating system."""
    if sys.platform.startswith("linux"):
        return ("Python's venv/pip parts are often a separate package on Linux. Install them and run this again:\n"
                "    Debian/Ubuntu:  sudo apt install python3-venv python3-pip\n"
                "    Fedora:         sudo dnf install python3-pip\n"
                "    Arch:           sudo pacman -S python-pip")
    if sys.platform == "darwin":
        return ("Install Python from https://www.python.org/downloads/macos/ (or: brew install python) "
                "and run this again.")
    return "Reinstall Python from https://www.python.org/downloads/ with the 'pip' option ticked."


USAGE = """Aura Tools launcher

  --no-browser     don't open a browser tab (useful over SSH or on a server)
  --port N         start on port N instead of 5000
  --update         update yt-dlp now instead of at most once a day
  --install-ffmpeg download ffmpeg now
  -h, --help       show this help
"""


def apply_options(argv):
    """Turn the command-line options into the settings the app reads (they survive the jump into the venv)."""
    if "-h" in argv or "--help" in argv:
        print(USAGE)
        sys.exit(0)
    if "--no-browser" in argv:
        os.environ["AURA_NO_BROWSER"] = "1"
    if "--port" in argv:
        i = argv.index("--port")
        value = argv[i + 1] if i + 1 < len(argv) else ""
        if not value.isdigit() or not 1 <= int(value) <= 65535:
            fail("--port needs a number between 1 and 65535, for example:  --port 8080")
        os.environ["AURA_PORT"] = value


def in_project_venv() -> bool:
    return os.path.normcase(os.path.abspath(sys.prefix)) == os.path.normcase(VENV_DIR)


def venv_has_pip() -> bool:
    try:
        return subprocess.run([VENV_PYTHON, "-c", "import pip"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_venv():
    # A .venv left half-made by a failed earlier run has a python but no pip; it can't be trusted.
    # (Checked only until the requirements are installed once, so normal starts stay instant.)
    if os.path.exists(VENV_PYTHON) and (os.path.exists(MARKER) or venv_has_pip()):
        return
    print("[+] Creating virtual environment (first run only)...", flush=True)
    try:
        # clear=True only matters when .venv exists but was made on another OS or is broken
        venv.create(VENV_DIR, with_pip=True, clear=os.path.isdir(VENV_DIR))
    except Exception as e:
        shutil.rmtree(VENV_DIR, ignore_errors=True)  # don't leave something that looks usable next time
        fail(f"Couldn't create the virtual environment ({e}).\n    {venv_hint()}")
    if not venv_has_pip():
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        fail(f"The virtual environment was created without pip.\n    {venv_hint()}")


def ensure_requirements():
    wanted = file_hash(REQUIREMENTS)
    if os.path.exists(MARKER) and open(MARKER).read().strip() == wanted:
        return
    print("[+] Installing requirements (this can take a minute)...", flush=True)
    result = subprocess.run([VENV_PYTHON, "-m", "pip", "install", "-r", REQUIREMENTS])
    if result.returncode != 0:
        fail("Installing requirements failed. Check your internet connection and try again.\n"
             "    If it keeps failing, delete the .venv folder and run this again.")
    with open(MARKER, "w") as f:
        f.write(wanted)


def ensure_optional(force: bool = False):
    """Extras like HEIC photo support. If one can't be installed here, say so and carry on without it."""
    if not os.path.exists(OPTIONAL_REQUIREMENTS):
        return
    wanted = file_hash(OPTIONAL_REQUIREMENTS)
    if not force and os.path.exists(OPTIONAL_MARKER) and open(OPTIONAL_MARKER).read().strip() == wanted:
        return  # already tried for this list (it may have failed; --update tries again)
    print("[+] Installing optional extras (HEIC/iPhone photo support)...", flush=True)
    try:
        ok = subprocess.run([VENV_PYTHON, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                             "-r", OPTIONAL_REQUIREMENTS], capture_output=True, text=True, timeout=300).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        print("[!] The optional extras couldn't be installed on this system (HEIC photos won't open).\n"
              "    Everything else works. Run with --update to try again.")
    with open(OPTIONAL_MARKER, "w") as f:
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
    apply_options(sys.argv[1:])
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
            try:
                os.execv(VENV_PYTHON, args)  # replace this process: one process, clean Ctrl+C
            except OSError as e:
                fail(f"Couldn't start the app's Python ({e}).\n"
                     "    The .venv folder may have been made on another computer. Delete it and run this again.")
        try:
            sys.exit(subprocess.call(args))
        except KeyboardInterrupt:
            sys.exit(0)

    ensure_requirements()
    ensure_optional(force="--update" in sys.argv)
    update_ytdlp(force="--update" in sys.argv)
    offer_deps(install_deps)
    run_app()


if __name__ == "__main__":
    main()
