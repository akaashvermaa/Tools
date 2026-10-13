"""
YouTube, Facebook and Instagram downloads via yt-dlp, run as background jobs.

Each job downloads into its own folder in the user cache dir (see jobs.py); the
browser polls the job's status and then fetches the finished files.
"""

import os
import re
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import yt_dlp

from . import deps
from .job_progress import ProgressTracker
from .jobs import JobRegistry

QUALITIES = ["Best Available", "4K (2160p)", "2K (1440p)", "1080p", "720p", "480p", "360p", "Audio Only (MP3)"]
CONTAINERS = ["mp4", "mkv", "webm", "mp3", "m4a"]
AUDIO_CONTAINERS = ("mp3", "m4a")
COOKIE_BROWSERS = ["chrome", "firefox", "edge", "brave", "opera", "vivaldi", "chromium", "safari"]

# Sites the downloader pages are built for. "hosts" are matched against the URL's domain
# (and its subdomains), so a Facebook link can't be pasted into the YouTube page by mistake.
PLATFORMS = {
    "youtube": {
        "name": "YouTube",
        "prefix": "yt",  # element-id prefix and tab id (<prefix>dl) in the page
        "hosts": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
        "placeholder": "https://www.youtube.com/watch?v=…",
        "lead": "Paste a link, choose a quality, and download.",
        "subtitles": True,
        "playlist_hint": "Download the whole playlist",
        "cookie_hint": "Use only if YouTube asks you to sign in. Firefox works best.",
    },
    "facebook": {
        "name": "Facebook",
        "prefix": "fb",
        "hosts": ("facebook.com", "fb.watch", "fb.com"),
        "placeholder": "https://www.facebook.com/watch/?v=…  or  https://fb.watch/…",
        "lead": "Paste a link to a public Facebook video or Reel, choose a quality, and download.",
        "subtitles": False,
        "playlist_hint": "Download every video if the link has several",
        "cookie_hint": "Needed for friends-only or group videos. Be signed in to Facebook in that browser.",
    },
    "instagram": {
        "name": "Instagram",
        "prefix": "ig",
        "hosts": ("instagram.com", "instagr.am"),
        "placeholder": "https://www.instagram.com/reel/…  or  /p/…",
        "lead": "Paste a link to an Instagram Reel, post or story video, choose a quality, and download.",
        "subtitles": False,
        "playlist_hint": "Download every video in the post (carousels)",
        "cookie_hint": "Instagram often asks for a login. Pick a browser where you're signed in to Instagram.",
    },
}

# How long to pause between videos (and between page requests) when downloading a playlist.
# Hammering a site with back-to-back requests is what gets an IP rate-limited or asked to
# "confirm you're not a bot", so the default is a polite, randomised pause.
PACES = {
    "Balanced": {"sleep_interval": 3, "max_sleep_interval": 8, "sleep_interval_requests": 1},
    "Gentle": {"sleep_interval": 8, "max_sleep_interval": 20, "sleep_interval_requests": 2},
    "Fast": {},
}
DEFAULT_PACE = "Balanced"

# Messages meaning "the site is pushing back". Carrying on only makes it worse, so the job stops.
RATE_LIMIT_HINTS = ("429", "too many requests", "rate-limit", "rate limit", "not a bot",
                    "sign in to confirm", "temporarily blocked", "unusual traffic")

MAX_LOG_LINES = 300
JOBS = JobRegistry("yt-jobs")


def is_valid_url(url: str, platform: str = "youtube") -> bool:
    """True if `url` is an http(s) link on one of the platform's domains."""
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https"):
        return False
    return any(host == h or host.endswith("." + h) for h in PLATFORMS[platform]["hosts"])


def _base_opts(cookies_browser: Optional[str]) -> dict:
    """Options shared by info lookups and downloads."""
    opts = {"retries": 10, "fragment_retries": 10, "extractor_retries": 3, "socket_timeout": 30}
    # Wait 2, 4, 8, 16, 30, 30... seconds between retries instead of hammering the server.
    backoff = lambda attempt: min(2 * 2 ** attempt, 30)  # noqa: E731
    opts["retry_sleep_functions"] = {"http": backoff, "fragment": backoff, "extractor": backoff}
    runtime = deps.js_runtimes()
    if runtime:
        opts["js_runtimes"] = runtime
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser, None, None, None)
    return opts


def friendly_error(msg: str, platform: str = "youtube") -> str:
    """Turn common yt-dlp failures into advice a user can act on."""
    low = msg.lower()
    name = PLATFORMS[platform]["name"]
    if "sign in to confirm" in low or "not a bot" in low:
        return (f"{name} wants to confirm you're not a bot. Pick your browser under 'Browser cookies' "
                f"(be signed in to {name} there) and try again.")
    if ("could not copy" in low and "cookie" in low) or "failed to decrypt" in low:
        return ("Couldn't read that browser's cookies. Close the browser completely and retry, "
                "or choose Firefox.")
    if "429" in low or "too many requests" in low or "rate-limit" in low or "rate limit" in low:
        return (f"{name} is rate-limiting this computer (too many requests). Wait 10-15 minutes, set Pace to "
                "'Gentle', and try again. Anything that finished before this is still available.")
    if "403" in low or "forbidden" in low:
        return (f"{name} refused the download (HTTP 403). Restart the app — it updates yt-dlp, "
                "which fixes most of these — then try again. If it keeps happening, use 'Browser cookies'.")
    if "private video" in low or "members-only" in low or "confirm your age" in low:
        return msg + " — try 'Browser cookies' with an account that can watch it."
    if platform != "youtube" and any(k in low for k in (
            "login required", "log in", "login", "rate-limit", "cookies", "private", "not available",
            "no video formats", "empty media response", "requested content is not available")):
        return (f"{name} didn't give out this video. It may be private, or {name} wants you signed in. "
                f"Pick your browser under 'Browser cookies' (signed in to {name} there) and try again. "
                f"Only videos you're allowed to watch can be saved. Original error: {msg}")
    if "unsupported url" in low:
        return f"That link isn't supported. Paste a {name} video URL."
    return msg


def fetch_info(url: str, cookies_browser: Optional[str] = None, platform: str = "youtube") -> dict:
    opts = {**_base_opts(cookies_browser), "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise ValueError(friendly_error(str(e), platform))
    return {
        "title": info.get("title") or info.get("description") or "Unknown Title",
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
    def __init__(self, url: str, platform: str):
        self.id, self.dir = JOBS.new_job_dir()
        self.url = url
        self.platform = platform
        self.save_dir = None      # folder the user chose; None = hand the files to the browser
        self.status = "starting"  # starting | downloading | processing | done | error
        self.tracker = ProgressTracker()
        self.note = None          # e.g. "pausing before the next video"
        self.skipped = 0          # playlist entries that failed and were passed over
        self.abort_reason = None  # set when the site starts rate-limiting us
        self.last_error = None    # latest yt-dlp error text, for when nothing downloads
        self.log = []
        self.files = []
        self.error = None
        self.warning = None       # finished, but not everything (e.g. stopped early)
        self.finished_at = None

    def add_log(self, msg: str):
        self.log.append(msg)
        del self.log[:-MAX_LOG_LINES]

    def to_dict(self) -> dict:
        t = self.tracker
        return {
            "id": self.id,
            "status": self.status,
            "progress": t.progress,
            "speed": t.current_speed(),
            "eta": t.current_eta(),
            "total": t.item_size() if t.item_count == 1 else None,
            "downloaded": t.bytes_done(),
            "item_index": t.item_index,
            "item_count": t.item_count,
            "item_title": t.item_title,
            "note": self.note,
            "skipped": self.skipped,
            "log": self.log,
            "files": [os.path.basename(f) for f in self.files],
            "error": self.error,
            "warning": self.warning,
            "saved_to": self.save_dir,
        }


def get_job(job_id: str) -> Optional[_Job]:
    return JOBS.get(job_id)


def start_download(url: str, quality: str, container: str, subtitles: bool, playlist: bool,
                   cookies_browser: Optional[str] = None, platform: str = "youtube",
                   pace: str = DEFAULT_PACE, save_dir: Optional[str] = None) -> _Job:
    job = _Job(url, platform)
    job.save_dir = save_dir
    if playlist:
        job.note = "Reading the playlist…"
    JOBS.add(job)
    threading.Thread(
        target=_run, args=(job, quality, container, subtitles, playlist, cookies_browser, pace), daemon=True
    ).start()
    return job


_SLEEP_RE = re.compile(r"\[download\] Sleeping ([\d.]+) seconds")


def _run(job: _Job, quality: str, container: str, subtitles: bool, playlist: bool,
         cookies_browser: Optional[str], pace: str = DEFAULT_PACE):
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
            # yt-dlp sends its normal progress messages here too, not only through info().
            if not msg.startswith("[debug]"):
                self.info(msg)

        def info(self, msg):
            job.add_log(msg)
            m = _SLEEP_RE.match(msg)
            if m:
                job.note = f"Pausing {float(m.group(1)):.0f}s before the next download so the site doesn't block you"

        def warning(self, msg):
            job.add_log(f"[WARN] {msg}")

        def error(self, msg):
            job.add_log(f"[ERROR] {msg}")
            if any(hint in msg.lower() for hint in RATE_LIMIT_HINTS):
                job.abort_reason = friendly_error(msg, job.platform)
            else:
                job.last_error = msg
                if playlist and job.tracker.item_index:  # a video failed (not the playlist lookup itself)
                    job.skipped += 1

    def progress_hook(d: dict):
        if d.get("status") not in ("downloading", "finished"):
            return
        job.note = None
        job.tracker.on_hook(d)
        job.status = "processing" if job.tracker.streams_finished() else "downloading"
        if d["status"] == "finished":
            job.add_log(f"[OK] Finished: {os.path.basename(d.get('filename', ''))}")

    def match_filter(info, incomplete=False):
        # Runs for every video before it is downloaded: stop the whole run if the site has
        # started refusing us, and let the progress tracker know which video is next.
        if job.abort_reason:
            raise yt_dlp.utils.DownloadCancelled(job.abort_reason)
        if not incomplete:
            job.tracker.start_item(info.get("playlist_index"), info.get("n_entries") or info.get("playlist_count"),
                                   info.get("title"))
            job.status = "downloading"
        return None

    ydl_opts = {
        **_base_opts(cookies_browser),
        "format": build_format_string(quality, container, bool(ffmpeg)),
        # .150B caps the title at 150 bytes: long (esp. non-English) titles otherwise
        # exceed the 255-byte filename limit and fail after downloading.
        "outtmpl": _filename_template(job.platform, playlist),
        # Straight into the chosen folder, so a long playlist keeps what it has finished even if the
        # app is closed; otherwise a private cache folder the browser fetches the files from.
        "paths": {"home": job.save_dir or job.dir},
        "windowsfilenames": True,
        "postprocessors": postprocessors,
        "noplaylist": not playlist,
        "writesubtitles": subtitles and bool(ffmpeg),
        "progress_hooks": [progress_hook],
        "match_filter": match_filter,
        "logger": _Logger(),
    }
    if playlist:
        ydl_opts.update(PACES.get(pace, PACES[DEFAULT_PACE]))
        if subtitles:
            ydl_opts["sleep_interval_subtitles"] = 1
        # One unavailable video shouldn't throw away the rest of a playlist.
        ydl_opts["ignoreerrors"] = "only_download"
    if ffmpeg:
        ydl_opts["ffmpeg_location"] = os.path.dirname(ffmpeg)
        if not is_audio:
            ydl_opts["merge_output_format"] = container

    failure = None
    finished_paths = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.add_post_hook(finished_paths.append)  # called with each final file (after merging/converting)
            ydl.download([job.url])
    except Exception as e:
        failure = job.abort_reason or friendly_error(str(e), job.platform)
        job.add_log(f"[ERROR] {e}")

    if job.save_dir:  # only what this job produced; the folder may hold anything else
        job.files = [f for f in dict.fromkeys(finished_paths) if os.path.isfile(f)]
    else:
        job.files = sorted(
            os.path.join(job.dir, f) for f in os.listdir(job.dir)
            if not f.endswith((".part", ".ytdl", ".temp")) and os.path.isfile(os.path.join(job.dir, f))
        )
    failure = failure or job.abort_reason
    if job.files:
        job.status = "done"
        if failure:
            job.warning = f"Stopped early with {len(job.files)} file(s) saved. {failure}"
        else:
            job.tracker.finish()
            if job.skipped:
                job.warning = f"{job.skipped} video(s) couldn't be downloaded and were skipped (see Details)."
        job.add_log(f"[OK] All done! {len(job.files)} file(s) " + (f"saved to {job.save_dir}" if job.save_dir else "ready."))
    else:
        job.status = "error"
        job.error = failure or (friendly_error(job.last_error, job.platform) if job.last_error else
                                "Nothing was downloaded.")
    job.note = None
    job.finished_at = time.time()


def _filename_template(platform: str, playlist: bool) -> str:
    if platform != "youtube":
        # Facebook/Instagram titles are often generic or repeated, so add the id to keep files apart.
        return "%(title).120B [%(id).30B].%(ext)s"
    if playlist:
        # Numbered in playlist order; the number is left off for a lone video.
        return "%(playlist_index&{:03d} - |)s%(title).120B.%(ext)s"
    return "%(title).150B.%(ext)s"
