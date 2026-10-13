"""
YouTube downloads via yt-dlp, run as background jobs.

Each job downloads into its own folder in the user cache dir; the browser
polls the job's status and then fetches the finished files. Finished jobs
are cleaned up after an hour, at startup and at exit.
"""

import atexit
import os
import shutil
import threading
import time
import uuid
from typing import Optional

import yt_dlp

from . import deps
from .paths import cache_dir

QUALITIES = ["Best Available", "4K (2160p)", "2K (1440p)", "1080p", "720p", "480p", "360p", "Audio Only (MP3)"]
CONTAINERS = ["mp4", "mkv", "webm", "mp3", "m4a"]
AUDIO_CONTAINERS = ("mp3", "m4a")
COOKIE_BROWSERS = ["chrome", "firefox", "edge", "brave", "opera", "vivaldi", "chromium", "safari"]

JOB_TTL_SECONDS = 60 * 60  # finished jobs (and their files) are removed after an hour
MAX_LOG_LINES = 300
JOBS_DIR = os.path.join(cache_dir(), "yt-jobs")


def is_valid_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _base_opts(cookies_browser: Optional[str]) -> dict:
    """Options shared by info lookups and downloads."""
    opts = {"retries": 10, "fragment_retries": 10, "extractor_retries": 3, "socket_timeout": 30}
    runtime = deps.js_runtimes()
    if runtime:
        opts["js_runtimes"] = runtime
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser, None, None, None)
    return opts


def friendly_error(msg: str) -> str:
    """Turn common yt-dlp failures into advice a user can act on."""
    low = msg.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube wants to confirm you're not a bot. Pick your browser under 'Browser cookies' "
                "(be signed in to YouTube there) and try again.")
    if ("could not copy" in low and "cookie" in low) or "failed to decrypt" in low:
        return ("Couldn't read that browser's cookies. Close the browser completely and retry, "
                "or choose Firefox.")
    if "403" in low or "forbidden" in low:
        return ("YouTube refused the download (HTTP 403). Restart the app — it updates yt-dlp, "
                "which fixes most of these — then try again. If it keeps happening, use 'Browser cookies'.")
    if "private video" in low or "members-only" in low or "confirm your age" in low:
        return msg + " — try 'Browser cookies' with an account that can watch it."
    if "unsupported url" in low:
        return "That link isn't supported. Paste a YouTube video or playlist URL."
    return msg


def fetch_info(url: str, cookies_browser: Optional[str] = None) -> dict:
    opts = {**_base_opts(cookies_browser), "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise ValueError(friendly_error(str(e)))
    return {
        "title": info.get("title") or "Unknown Title",
        "uploader": info.get("uploader") or "Unknown",
        "duration": info.get("duration"),
        "view_count": info.get("view_count"),
        "thumbnail": info.get("thumbnail"),
    }


def build_format_string(quality: str, container: str, has_ffmpeg: bool) -> str:
    if quality == "Audio Only (MP3)" or container in AUDIO_CONTAINERS:
        return "bestaudio/best"

    if has_ffmpeg:
        if container == "mp4":
            quality_map = {
                "Best Available": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
                "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best",
                "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best",
                "480p": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best",
                "360p": "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=360]+bestaudio/best",
                "4K (2160p)": "bestvideo[height<=2160]+bestaudio/best",
                "2K (1440p)": "bestvideo[height<=1440]+bestaudio/best",
            }
        else:
            quality_map = {
                "Best Available": "bestvideo+bestaudio/best",
                "4K (2160p)": "bestvideo[height<=2160]+bestaudio/best",
                "2K (1440p)": "bestvideo[height<=1440]+bestaudio/best",
                "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
                "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
                "360p": "bestvideo[height<=360]+bestaudio/best[height<=360]",
            }
    else:
        quality_map = {
            "Best Available": "best[ext=mp4]/best",
            "1080p": "best[height<=1080][ext=mp4]/best[height<=1080]/best",
            "720p": "best[height<=720][ext=mp4]/best[height<=720]/best",
            "480p": "best[height<=480][ext=mp4]/best[height<=480]/best",
            "360p": "best[height<=360][ext=mp4]/best[height<=360]/best",
        }
    return quality_map.get(quality, quality_map["Best Available"])


class _Job:
    def __init__(self, url: str):
        self.id = uuid.uuid4().hex
        self.url = url
        self.dir = os.path.join(JOBS_DIR, self.id)
        os.makedirs(self.dir)
        self.status = "starting"  # starting | downloading | processing | done | error
        self.progress = 0.0
        self.speed = None
        self.eta = None
        self.total = None
        self.downloaded = None
        self.log = []
        self.files = []
        self.error = None
        self.finished_at = None

    def add_log(self, msg: str):
        self.log.append(msg)
        del self.log[:-MAX_LOG_LINES]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "total": self.total,
            "downloaded": self.downloaded,
            "log": self.log,
            "files": [os.path.basename(f) for f in self.files],
            "error": self.error,
        }


_jobs = {}
_lock = threading.Lock()


def _cleanup_old_jobs():
    now = time.time()
    with _lock:
        expired = [j for j in _jobs.values() if j.finished_at and now - j.finished_at > JOB_TTL_SECONDS]
        for job in expired:
            del _jobs[job.id]
    for job in expired:
        shutil.rmtree(job.dir, ignore_errors=True)


def _remove_stale_job_dirs(max_age: float = 6 * 3600):
    """Leftovers from crashed runs. Recent folders may belong to another running instance."""
    os.makedirs(JOBS_DIR, exist_ok=True)
    now = time.time()
    for name in os.listdir(JOBS_DIR):
        path = os.path.join(JOBS_DIR, name)
        try:
            if now - os.path.getmtime(path) > max_age:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def _remove_own_job_dirs():
    for job in list(_jobs.values()):
        shutil.rmtree(job.dir, ignore_errors=True)


_remove_stale_job_dirs()
atexit.register(_remove_own_job_dirs)


def get_job(job_id: str) -> Optional[_Job]:
    with _lock:
        return _jobs.get(job_id)


def start_download(url: str, quality: str, container: str, subtitles: bool, playlist: bool,
                   cookies_browser: Optional[str] = None) -> _Job:
    _cleanup_old_jobs()
    job = _Job(url)
    with _lock:
        _jobs[job.id] = job
    threading.Thread(
        target=_run, args=(job, quality, container, subtitles, playlist, cookies_browser), daemon=True
    ).start()
    return job


def _run(job: _Job, quality: str, container: str, subtitles: bool, playlist: bool, cookies_browser: Optional[str]):
    is_audio = quality == "Audio Only (MP3)" or container in AUDIO_CONTAINERS
    ffmpeg = deps.FFMPEG.find()

    postprocessors = []
    if ffmpeg:
        if is_audio:
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a" if container == "m4a" else "mp3",
                "preferredquality": "192",
            })
        if subtitles:
            postprocessors.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
    elif is_audio:
        job.add_log("[WARN] Audio extraction needs ffmpeg. Saving best available audio stream instead.")

    class _Logger:
        def debug(self, msg):
            if not msg.startswith("[debug]"):
                job.add_log(msg)

        def info(self, msg):
            job.add_log(msg)

        def warning(self, msg):
            job.add_log(f"[WARN] {msg}")

        def error(self, msg):
            job.add_log(f"[ERROR] {msg}")

    def progress_hook(d: dict):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            job.status = "downloading"
            job.total = total
            job.downloaded = downloaded
            job.speed = d.get("speed")
            job.eta = d.get("eta")
            job.progress = min(downloaded / total, 1) if total else 0
        elif d.get("status") == "finished":
            job.status = "processing"
            job.progress = 1
            job.add_log(f"[OK] Finished: {os.path.basename(d.get('filename', ''))}")

    ydl_opts = {
        **_base_opts(cookies_browser),
        "format": build_format_string(quality, container, bool(ffmpeg)),
        # .150B caps the title at 150 bytes: long (esp. non-English) titles otherwise
        # exceed the 255-byte filename limit and fail after downloading.
        "outtmpl": os.path.join(job.dir, "%(title).150B.%(ext)s"),
        "windowsfilenames": True,
        "postprocessors": postprocessors,
        "noplaylist": not playlist,
        "writesubtitles": subtitles and bool(ffmpeg),
        "progress_hooks": [progress_hook],
        "logger": _Logger(),
    }
    if ffmpeg:
        ydl_opts["ffmpeg_location"] = os.path.dirname(ffmpeg)
        if not is_audio:
            ydl_opts["merge_output_format"] = container

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([job.url])
        job.files = sorted(
            os.path.join(job.dir, f) for f in os.listdir(job.dir)
            if not f.endswith((".part", ".ytdl", ".temp")) and os.path.isfile(os.path.join(job.dir, f))
        )
        if not job.files:
            raise RuntimeError("Nothing was downloaded.")
        job.status = "done"
        job.add_log(f"[OK] All done! {len(job.files)} file(s) ready.")
    except Exception as e:
        job.status = "error"
        job.error = friendly_error(str(e))
        job.add_log(f"[ERROR] {e}")
    finally:
        job.speed = job.eta = None
        job.finished_at = time.time()
