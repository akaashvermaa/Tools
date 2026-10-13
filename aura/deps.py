"""
ffmpeg (+ ffprobe) for the YouTube downloader: found on the system or
downloaded on demand into <app dir>/tools/bin. Needed for merging HD
video/audio, MP3 conversion and subtitles.

Installs are atomic: archives are unpacked into a temp folder, every binary
is test-run, and only then moved into place. Each tool has fallback sources
and retries, so one flaky mirror doesn't break setup.
"""

import functools
import glob
import os
import platform
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from typing import Callable, Dict, List, Optional

from .paths import tools_bin

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
EXE = ".exe" if IS_WIN else ""

ProgressFn = Callable[[str, float], None]


def _arch() -> str:
    m = platform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86_64", "amd64", "x64"):
        return "x64"
    return m


def _runs(path: str, flag: str) -> bool:
    """True if the executable starts and exits cleanly with `flag`."""
    try:
        kwargs = {"creationflags": 0x08000000} if IS_WIN else {}  # CREATE_NO_WINDOW
        return subprocess.run([path, flag], capture_output=True, timeout=30, **kwargs).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _local(name: str) -> str:
    return os.path.join(tools_bin(), name + EXE)


# ── Dependency definitions ──────────────────────────────────────────────────

class Dep:
    key = ""
    label = ""
    binaries: List[str] = []
    version_flag = "-version"
    size_hint = ""
    purpose = ""

    def find(self) -> Optional[str]:
        raise NotImplementedError

    def sources(self) -> List[List[str]]:
        """Fallback chain: each entry is the list of archive URLs for one source."""
        raise NotImplementedError

    def find_local(self) -> Optional[str]:
        paths = [_local(b) for b in self.binaries]
        return paths[0] if all(os.path.isfile(p) for p in paths) else None


class FFmpeg(Dep):
    key = "ffmpeg"
    label = "ffmpeg"
    binaries = ["ffmpeg", "ffprobe"]
    version_flag = "-version"
    size_hint = "about 150 MB"
    purpose = "HD video, MP3 and subtitles"

    def find(self) -> Optional[str]:
        local = self.find_local()
        if local:
            return local
        found = shutil.which("ffmpeg")
        if found:
            return found
        candidates = []
        if IS_WIN:
            winget = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
            if os.path.isdir(winget):
                candidates += glob.glob(os.path.join(winget, "**", "ffmpeg.exe"), recursive=True)
            candidates += [
                r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
                r"C:\ffmpeg\bin\ffmpeg.exe",
                os.path.expandvars(r"%USERPROFILE%\ffmpeg\bin\ffmpeg.exe"),
            ]
        elif IS_MAC:
            # Finder-launched apps don't get the PATH that Homebrew sets up.
            candidates += ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
        return next((c for c in candidates if os.path.isfile(c)), None)

    def sources(self) -> List[List[str]]:
        arch = _arch()
        if IS_MAC:
            a = "arm64" if arch == "arm64" else "amd64"
            base = "https://ffmpeg.martin-riedl.de/redirect/latest/macos/{a}/release/{t}.zip"
            return [[base.format(a=a, t=t) for t in ("ffmpeg", "ffprobe")]]
        if IS_WIN:
            name = "ffmpeg-master-latest-winarm64-gpl.zip" if arch == "arm64" else "ffmpeg-master-latest-win64-gpl.zip"
        elif sys.platform.startswith("linux") and arch in ("x64", "arm64"):
            name = "ffmpeg-master-latest-linuxarm64-gpl.tar.xz" if arch == "arm64" else "ffmpeg-master-latest-linux64-gpl.tar.xz"
        else:
            raise RuntimeError(f"No prebuilt ffmpeg for {sys.platform}/{arch}. Install it with your package manager.")
        return [
            [f"https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/{name}"],  # patched for yt-dlp
            [f"https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/{name}"],
        ]


FFMPEG = FFmpeg()
DEPS: Dict[str, Dep] = {FFMPEG.key: FFMPEG}


# ── JavaScript runtime for yt-dlp ───────────────────────────────────────────
# Not downloaded by us — but if one is already installed, yt-dlp uses it to
# unlock more YouTube formats and avoid some 403 errors.

NODE_MIN = (23, 5, 0)  # yt-dlp's YouTube solver rejects older Node


@functools.lru_cache(maxsize=4)
def _node_version(path: str) -> tuple:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15).stdout.strip()
        return tuple(int(x) for x in out.lstrip("v").split(".")[:3])
    except (OSError, ValueError, subprocess.SubprocessError):
        return ()


@functools.lru_cache(maxsize=1)
def js_runtimes() -> Optional[dict]:
    """yt-dlp `js_runtimes` option for an already-installed runtime, or None."""
    deno = shutil.which("deno") or next(
        (p for p in [os.path.join(os.path.expanduser("~"), ".deno", "bin", "deno" + EXE)] if os.path.isfile(p)), None)
    if deno:
        return {"deno": {"path": deno}}
    node = shutil.which("node")
    if node and _node_version(node) >= NODE_MIN:
        return {"node": {"path": node}}
    return None


def status() -> dict:
    """What's available, for the UI and launcher."""
    path = FFMPEG.find()
    return {"ffmpeg": {"ok": bool(path), "path": path}}


def missing() -> List[Dep]:
    return [DEPS[k] for k, v in status().items() if not v["ok"]]


# ── Downloading & installing ────────────────────────────────────────────────

def _ssl_context() -> ssl.SSLContext:
    # python.org builds on macOS ship without CA certs; certifi comes with yt-dlp.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _fetch(url: str, dest: str, on_bytes: Callable[[int, Optional[int]], None]):
    req = urllib.request.Request(url, headers={"User-Agent": "AuraTools"})
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=60) as res, open(dest, "wb") as f:
        total = res.headers.get("Content-Length")
        total = int(total) if total else None
        done = 0
        while True:
            chunk = res.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            on_bytes(done, total)
    if total and done < total:
        raise IOError(f"Download was cut off ({done} of {total} bytes).")


def _extract(archive: str, wanted: List[str], out_dir: str):
    """Copy the wanted executables out of a .zip / .tar.xz, wherever they sit inside it."""
    names = {w + EXE for w in wanted}
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                if os.path.basename(name) in names:
                    with zf.open(name) as src, open(os.path.join(out_dir, os.path.basename(name)), "wb") as dst:
                        shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if member.isfile() and os.path.basename(member.name) in names:
                    with tf.extractfile(member) as src, open(os.path.join(out_dir, os.path.basename(member.name)), "wb") as dst:
                        shutil.copyfileobj(src, dst)


def _try_source(dep: Dep, urls: List[str], tmp: str, on_progress: ProgressFn):
    staging = os.path.join(tmp, "staging")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)

    for i, url in enumerate(urls):
        archive = os.path.join(tmp, f"download{i}")

        def report(done, total, i=i):
            frac = (i + (done / total if total else 0)) / len(urls)
            size = f"{done / 1e6:.0f} / {total / 1e6:.0f} MB" if total else f"{done / 1e6:.0f} MB"
            on_progress(f"Downloading {dep.label}… {size}", frac * 0.95)

        _fetch(url, archive, report)
        on_progress(f"Unpacking {dep.label}…", 0.96)
        _extract(archive, dep.binaries, staging)
        os.remove(archive)

    on_progress(f"Checking {dep.label}…", 0.98)
    for b in dep.binaries:
        path = os.path.join(staging, b + EXE)
        if not os.path.isfile(path):
            raise RuntimeError(f"{b} wasn't found in the download.")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if not _runs(path, dep.version_flag):
            raise RuntimeError(f"Downloaded {b} doesn't run on this system.")

    # Everything verified: move into place.
    os.makedirs(tools_bin(), exist_ok=True)
    for b in dep.binaries:
        os.replace(os.path.join(staging, b + EXE), _local(b))


def install(dep: Dep, on_progress: ProgressFn = lambda msg, frac: None) -> str:
    """Download and install `dep` into tools/bin. Returns the main executable's path."""
    errors = []
    with tempfile.TemporaryDirectory(prefix=f"aura-{dep.key}-") as tmp:
        for urls in dep.sources():
            for attempt in range(1, 4):
                try:
                    _try_source(dep, urls, tmp, on_progress)
                    on_progress(f"{dep.label} installed", 1.0)
                    return _local(dep.binaries[0])
                except Exception as e:
                    errors.append(f"{urls[0].split('/')[2]}: {e}")
                    if attempt < 3:
                        on_progress(f"Retrying {dep.label} download ({attempt + 1}/3)…", 0)
                        time.sleep(2 * attempt)
    raise RuntimeError(f"Couldn't install {dep.label}. " + " | ".join(errors[-2:]))


# ── Background installs (web UI) ────────────────────────────────────────────

class _InstallState:
    def __init__(self):
        self.status = "idle"  # idle | running | done | error
        self.message = ""
        self.progress = 0.0
        self.error = None

    def to_dict(self) -> dict:
        return {"status": self.status, "message": self.message, "progress": self.progress, "error": self.error}


_states: Dict[str, _InstallState] = {k: _InstallState() for k in DEPS}
_lock = threading.Lock()


def install_state(key: str) -> dict:
    return _states[key].to_dict()


def start_background_install(key: str) -> dict:
    dep, state = DEPS[key], _states[key]
    with _lock:
        if state.status == "running":
            return state.to_dict()
        state.status, state.progress, state.error, state.message = "running", 0.0, None, "Starting download…"

    def progress(msg, frac):
        state.message, state.progress = msg, frac

    def run():
        try:
            install(dep, progress)
            state.status = "done"
        except Exception as e:
            state.status, state.error = "error", str(e)

    threading.Thread(target=run, daemon=True).start()
    return state.to_dict()


# ── Terminal install (launch.py) ───────────────────────────────────────────

def install_in_terminal(dep: Dep) -> bool:
    last = [""]

    def progress(msg, frac):
        line = f"\r    [{'#' * int(frac * 30):<30}] {msg}"
        if line != last[0]:
            sys.stdout.write(line.ljust(len(last[0])))
            sys.stdout.flush()
            last[0] = line

    try:
        path = install(dep, progress)
        print(f"\n[+] {dep.label} installed: {path}")
        return True
    except Exception as e:
        print(f"\n[!] {e}")
        return False
