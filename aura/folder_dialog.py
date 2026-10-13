"""
"Browse for a folder", using the operating system's own dialog.

A web page can't learn a real folder path from the browser, but this app runs on the
user's own computer, so the server can open the native dialog itself. It runs as a
separate process (PowerShell, osascript, zenity or kdialog), which keeps the GUI off the
server's threads and needs no extra Python packages.
"""

import os
import shutil
import subprocess
import sys
import threading
from typing import Optional

TITLE = "Choose where to save"
TIMEOUT = 10 * 60  # seconds the dialog may stay open
_one_at_a_time = threading.Lock()

_WINDOWS_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true            # otherwise the dialog can open behind the browser
$owner.ShowInTaskbar = $false
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(0, 0)
$owner.Show()
$owner.Activate()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:AURA_DIALOG_TITLE
$dialog.ShowNewFolderButton = $true
if ($env:AURA_DIALOG_START -and (Test-Path -LiteralPath $env:AURA_DIALOG_START)) { $dialog.SelectedPath = $env:AURA_DIALOG_START }
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.SelectedPath) }
$owner.Close()
"""


class DialogUnavailable(RuntimeError):
    """No folder dialog could be opened on this system."""


def _start_dir(initial: Optional[str]) -> str:
    folder = os.path.expanduser(initial or "")
    return folder if folder and os.path.isdir(folder) else ""


def _run(cmd: list, env: Optional[dict] = None) -> Optional[str]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=TIMEOUT, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return None
    except OSError as e:
        raise DialogUnavailable(f"Couldn't open the folder dialog: {e}")
    # zenity/kdialog/osascript exit non-zero when the user cancels; that isn't an error.
    return done.stdout.strip() or None


def choose_folder(initial: Optional[str] = None) -> Optional[str]:
    """Show the dialog and return the chosen folder, or None if the user cancelled."""
    start = _start_dir(initial)
    with _one_at_a_time:
        if sys.platform.startswith("win"):
            env = {**os.environ, "AURA_DIALOG_TITLE": TITLE, "AURA_DIALOG_START": start}
            return _run(["powershell", "-NoProfile", "-NonInteractive", "-STA", "-WindowStyle", "Hidden",
                         "-Command", _WINDOWS_SCRIPT], env)
        if sys.platform == "darwin":
            quoted = start.replace("\\", "\\\\").replace('"', '\\"')
            default = f' default location (POSIX file "{quoted}")' if start else ""
            script = ['try', f'POSIX path of (choose folder with prompt "{TITLE}"{default})',
                      'on error number -128', 'return ""', 'end try']
            args = ["osascript"]
            for line in script:
                args += ["-e", line]
            path = _run(args)
            return (path.rstrip("/") or "/") if path else None
        if shutil.which("zenity"):
            args = ["zenity", "--file-selection", "--directory", f"--title={TITLE}"]
            if start:
                args.append(f"--filename={start}/")
            return _run(args)
        if shutil.which("kdialog"):
            return _run(["kdialog", "--title", TITLE, "--getexistingdirectory", start or os.path.expanduser("~")])
    raise DialogUnavailable("No folder picker is available on this system. Type or paste the folder path instead.")


def validate_save_dir(path: str) -> str:
    """Return the cleaned absolute folder path, creating it if needed; raise ValueError if unusable."""
    folder = os.path.expanduser(str(path or "").strip().strip('"'))
    if not os.path.isabs(folder):
        raise ValueError("Please give the full folder path (for example C:\\Users\\you\\Music), or use Browse.")
    folder = os.path.normpath(folder)
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, f".aura-write-test-{os.getpid()}")
        with open(probe, "w"):
            pass
        os.remove(probe)
    except OSError as e:
        raise ValueError(f"Can't save to that folder ({e.strerror or e}). Choose another one.")
    return folder
