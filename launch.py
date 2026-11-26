#!/usr/bin/env python3
"""
Cross-platform launcher for Aura Tools (Windows, macOS, Linux).

    launch.bat            (Windows setup)  then run.bat to open Aura
    ./run.sh              (Linux / macOS) or double-click run.command on macOS

On Windows, launch.bat installs the dependencies and run.bat opens the app.
A regular Python or shell launch can still install and open Aura in one step.
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
REQUIREMENTS = os.path.join(ROOT, "requirements.in")
OPTIONAL_REQUIREMENTS = os.path.join(ROOT, "requirements-optional.in")
BACKGROUND_REQUIREMENTS = os.path.join(ROOT, "requirements-background.in")
MARKER = os.path.join(VENV_DIR, ".aura-requirements.sha1")
OPTIONAL_MARKER = os.path.join(VENV_DIR, ".aura-optional.sha1")
BACKGROUND_MARKER = os.path.join(VENV_DIR, ".aura-background.sha1")
YTDLP_STAMP = os.path.join(VENV_DIR, ".aura-ytdlp-checked")
YTDLP_CHECK_EVERY = 24 * 3600

if sys.platform.startswith("win"):
    VENV_PYTHON = os.path.join(VENV_DIR, "Scripts", "python.exe")
else:
    VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python")


def fail(msg: str):
    print(f"\n[ERROR] {msg}", flush=True)
    if sys.platform.startswith("win") and not os.environ.get("AURA_BATCH") and sys.stdin and sys.stdin.isatty():
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
  --setup-only     install dependencies and models, then exit
  --run-only       start an existing installation without installing or updating
  --quick-start    skip the automatic yt-dlp update check and ffmpeg prompt
  --update         update yt-dlp now and retry optional dependencies
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


def ensure_venv(verify=False):
    # A .venv left half-made by a failed earlier run has a python but no pip; it can't be trusted.
    # (Checked only until the requirements are installed once, so normal starts stay instant.)
    if os.path.exists(VENV_PYTHON) and ((not verify and os.path.exists(MARKER)) or venv_has_pip()):
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


def requirements_ready(wanted: str) -> bool:
    try:
        with open(MARKER) as marker:
            return marker.read().strip() == wanted
    except (OSError, UnicodeError):
        return False


def ensure_requirements():
    wanted = file_hash(REQUIREMENTS)
    if requirements_ready(wanted):
        return
    print("[+] Installing requirements (this can take a minute)...", flush=True)
    result = subprocess.run([VENV_PYTHON, "-m", "pip", "install", "-r", REQUIREMENTS])
    if result.returncode != 0:
        fail("Installing requirements failed. Check your internet connection and try again.\n"
             "    If it keeps failing, delete the .venv folder and run this again.")
    with open(MARKER, "w") as f:
        f.write(wanted)


def ensure_optional(force: bool = False):
    """Install each optional feature independently and carry on if a wheel is missing."""
    install_optional(OPTIONAL_REQUIREMENTS, OPTIONAL_MARKER, "optional extras", "HEIC photo support", force)
    install_optional(BACKGROUND_REQUIREMENTS, BACKGROUND_MARKER, "background removal extras", "background removal", force)


def install_optional(requirements: str, marker: str, label: str, feature: str, force: bool):
    if not os.path.exists(requirements):
        return
    wanted = file_hash(requirements)
    if not force and os.path.exists(marker) and open(marker).read().strip() == wanted:
        return  # already tried for this list (it may have failed; --update tries again)
    print(f"[+] Installing {label} ({feature})...", flush=True)
    try:
        ok = subprocess.run([VENV_PYTHON, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                             "-r", requirements], capture_output=True, text=True, timeout=300).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        print(f"[!] The {label} couldn't be installed on this system.\n"
              "    Everything else works. Run with --update to try again.")
    with open(marker, "w") as f:
        f.write(wanted)


def update_ytdlp(force: bool = False):
    """Check for yt-dlp updates and keep the installed version if the check fails."""
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


def prepare_models():
    from aura import deps, image_tools

    models = [("English OCR", deps.ocr_model)]
    if image_tools.BACKGROUND_SUPPORT:
        models.append(("background removal", deps.background_model))
    for label, fetch in models:
        try:
            fetch()
            print(f"[+] {label} data is ready.", flush=True)
        except (OSError, ValueError) as e:
            print(f"[!] {e}", flush=True)


def main():
    options = sys.argv[1:]
    apply_options(options)
    quick_start = "--quick-start" in options
    setup_only = "--setup-only" in options
    run_only = "--run-only" in options
    force_update = "--update" in options
    install_deps = "--install-ffmpeg" in options

    if run_only and (setup_only or force_update or install_deps):
        fail("Run setup separately. On Windows, use launch.bat to install or update Aura Tools.")

    if getattr(sys, "frozen", False):  # packaged by build.py: dependencies are bundled
        if setup_only:
            offer_deps(True)
            prepare_models()
            return
        if not run_only and (not quick_start or install_deps):
            offer_deps(install_deps)
        return run_app()

    if sys.version_info < (3, 9):
        fail(f"Python 3.9 or newer is required (found {sys.version.split()[0]}).")

    os.chdir(ROOT)
    if run_only:
        if not in_project_venv() or not requirements_ready(file_hash(REQUIREMENTS)):
            fail("Aura Tools needs setup. Run launch.bat first on Windows, or launch.py --setup-only on macOS or Linux.")
        return run_app()

    if not in_project_venv():
        ensure_venv(verify=setup_only)
        # Re-run this script inside the venv so the app uses its packages.
        args = [VENV_PYTHON, os.path.abspath(__file__)] + options
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
    ensure_optional(force=force_update)
    if setup_only or not quick_start or force_update:
        update_ytdlp(force=force_update)
    if setup_only:
        offer_deps(True)
        prepare_models()
        command = "run.bat" if sys.platform.startswith("win") else "./run.sh"
        print(f"[+] Base setup is ready. Open Aura Tools with {command}.", flush=True)
        return
    if not quick_start or install_deps:
        offer_deps(install_deps)
    run_app()


if __name__ == "__main__":
    main()
