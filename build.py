#!/usr/bin/env python3
"""
Package Aura Tools as a standalone app for the current OS (PyInstaller).

    python build.py      (Windows)
    python3 build.py     (macOS / Linux)

PyInstaller can only build for the OS it runs on, so run this once on each
system you want a build for. Output goes to dist/Aura Tools/.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "Aura Tools"


def main():
    os.chdir(ROOT)
    import launch  # reuse its venv helpers

    if not launch.in_project_venv():
        launch.ensure_venv()
        sys.exit(subprocess.call([launch.VENV_PYTHON, os.path.abspath(__file__)] + sys.argv[1:]))
    py = sys.executable

    print("[+] Installing build dependencies...", flush=True)
    subprocess.check_call([py, "-m", "pip", "install", "-q", "-r", "requirements.txt", "pyinstaller"])

    print("[+] Cleaning previous build...", flush=True)
    for path in ("build", "dist", f"{NAME}.spec"):
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)

    sep = os.pathsep  # ';' on Windows, ':' elsewhere
    cmd = [
        py, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--console",  # the window shows the URL and closing it stops the server
        "--name", NAME,
        "--add-data", f"aura/templates{sep}aura/templates",
        "--add-data", f"aura/static{sep}aura/static",
        "--collect-submodules", "aura",
        "--collect-all", "yt_dlp_ejs",  # YouTube JS solver scripts (used if a JS runtime is installed)
        "launch.py",
    ]
    if sys.platform.startswith("win"):
        cmd[3:3] = ["--icon", os.path.join("assets", "logo.ico")]

    print("[+] Running PyInstaller (this takes a few minutes)...", flush=True)
    subprocess.check_call(cmd)

    exe = f"{NAME}.exe" if sys.platform.startswith("win") else NAME
    print(f"\n[+] Done! Run: {os.path.join('dist', NAME, exe)}")


if __name__ == "__main__":
    main()
