"""
Video and audio tools (convert, extract audio, trim, compress, GIF, mute) using ffmpeg.

The browser uploads one file, the job runs ffmpeg in the background, and the page polls
its progress and then fetches the result, exactly like the downloaders do. ffmpeg is the
copy the app already finds or installs (see deps.py). The original file is never changed.
"""

import os
import re
import subprocess
import threading
import time
from typing import List, Optional

from . import deps
from .job_progress import ProgressTracker
from .jobs import JobRegistry

VIDEO_EXTS = ("mp4", "mkv", "mov", "avi", "webm", "m4v", "wmv", "flv", "mpg", "mpeg", "3gp", "ts")
AUDIO_EXTS = ("mp3", "m4a", "wav", "aac", "ogg", "opus", "flac", "wma")
INPUT_EXTS = VIDEO_EXTS + AUDIO_EXTS

OPERATIONS = {
    "convert": "Convert to another format",
    "audio": "Extract the audio",
    "trim": "Trim / cut a section",
    "compress": "Make the video smaller",
    "gif": "Make a GIF",
    "mute": "Remove the sound",
}
CONVERT_FORMATS = ("mp4", "mkv", "webm", "mp3", "m4a", "wav")
AUDIO_FORMATS = ("mp3", "m4a", "wav")
AUDIO_BITRATES = (128, 192, 256, 320)
COMPRESS_LEVELS = {  # level -> (x264 CRF, AAC kbit/s); higher CRF = smaller, softer picture
    "Light": (24, 128),
    "Balanced": (28, 96),
    "Strong": (32, 64),
}
MAX_HEIGHTS = {"Original": 0, "1080p": 1080, "720p": 720, "480p": 480}
MAX_GIF_SECONDS = 30
EVEN = "scale=trunc(iw/2)*2:trunc(ih/2)*2"  # H.264 with yuv420p needs even width and height

JOBS = JobRegistry("media-jobs")
MAX_LOG_LINES = 40
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


class MediaError(ValueError):
    """Bad input or a failed conversion, worded for the user."""


# ── Building the ffmpeg command ─────────────────────────────────────────────

def parse_time(text, what: str = "time") -> float:
    """'90', '1:30' or '01:02:03.5' -> seconds."""
    text = str(text or "").strip()
    if not text:
        raise MediaError(f"Please enter the {what}.")
    try:
        total = 0.0
        for part in text.split(":"):
            total = total * 60 + float(part)
        if total < 0 or len(text.split(":")) > 3:
            raise ValueError
        return total
    except ValueError:
        raise MediaError(f"'{text}' isn't a valid {what}. Use seconds (90) or minutes:seconds (1:30).")


def _num(params: dict, key: str, default, cast=float, what: str = ""):
    raw = params.get(key)
    if raw in (None, ""):
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        raise MediaError(f"'{raw}' isn't a valid number for {what or key}.")


def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower().lstrip(".")


def build_command(op: str, params: dict, in_name: str) -> dict:
    """
    Work out the ffmpeg options for an operation.

    Returns {"input_opts": [...], "output_opts": [...], "ext": output extension,
    "suffix": added to the file name, "start": seconds skipped at the start,
    "limit": seconds of output if fixed (else None = until the end)}.
    """
    src = _ext(in_name)
    if src not in INPUT_EXTS:
        raise MediaError(f"{in_name}: unsupported file type.")
    is_audio_in = src in AUDIO_EXTS
    if op not in OPERATIONS:
        raise MediaError("Unknown operation.")
    plan = {"input_opts": [], "output_opts": [], "start": 0.0, "limit": None}

    if op == "convert":
        fmt = params.get("format", "mp4")
        if fmt not in CONVERT_FORMATS:
            raise MediaError("Unknown output format.")
        if fmt in AUDIO_FORMATS:
            plan.update(output_opts=_audio_opts(fmt, 192), ext=fmt, suffix="")
        elif is_audio_in:
            raise MediaError("That file has no picture, so it can only become an audio format (MP3, M4A, WAV).")
        elif fmt == "webm":
            plan.update(output_opts=["-map", "0:v:0", "-map", "0:a:0?", "-vf", EVEN, "-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0",
                                     "-row-mt", "1", "-c:a", "libopus", "-b:a", "128k"], ext="webm", suffix="")
        else:  # mp4 / mkv
            plan.update(output_opts=["-map", "0:v:0", "-map", "0:a:0?", "-vf", EVEN, "-c:v", "libx264", "-preset", "medium",
                                     "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
                        + (["-movflags", "+faststart"] if fmt == "mp4" else []), ext=fmt, suffix="")
        if plan["ext"] == src:
            plan["suffix"] = "_converted"  # don't hand back a file named exactly like the original

    elif op == "audio":
        fmt = params.get("format", "mp3")
        kbps = _num(params, "bitrate", 192, int, "the bitrate")
        if fmt not in AUDIO_FORMATS or kbps not in AUDIO_BITRATES:
            raise MediaError("Choose MP3, M4A or WAV, and a standard bitrate.")
        plan.update(output_opts=_audio_opts(fmt, kbps), ext=fmt, suffix="_audio")

    elif op == "trim":
        start = parse_time(params.get("start") or "0", "start time")
        end_text = str(params.get("end", "")).strip()
        end = parse_time(end_text, "end time") if end_text else None
        if end is not None and end <= start:
            raise MediaError("The end time must be after the start time.")
        plan.update(start=start, limit=(end - start) if end is not None else None, suffix="_trimmed")
        if is_audio_in or params.get("mode", "fast") == "fast":
            # Copy the streams: instant and lossless, but cuts land on the nearest keyframe.
            plan.update(output_opts=["-map", "0:v?", "-map", "0:a?", "-c", "copy", "-avoid_negative_ts", "make_zero"], ext=src)
        else:
            plan.update(output_opts=["-map", "0:v:0", "-map", "0:a:0?", "-vf", EVEN, "-c:v", "libx264", "-preset", "medium",
                                     "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                                     "-movflags", "+faststart"], ext="mp4")

    elif op == "compress":
        if is_audio_in:
            raise MediaError("Choose a video file to compress. For audio, use 'Convert' and a lower bitrate.")
        level, height = params.get("level", "Balanced"), params.get("max_height", "Original")
        if level not in COMPRESS_LEVELS or height not in MAX_HEIGHTS:
            raise MediaError("Unknown compression level or size.")
        crf, abr = COMPRESS_LEVELS[level]
        opts = ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{abr}k", "-movflags", "+faststart"]
        shrink = f"scale=-2:'min({MAX_HEIGHTS[height]},ih)'," if MAX_HEIGHTS[height] else ""  # shrink only, keep the shape
        opts = ["-vf", shrink + EVEN] + opts
        plan.update(output_opts=opts, ext="mp4", suffix="_small")

    elif op == "gif":
        if is_audio_in:
            raise MediaError("A GIF needs a video file.")
        start = parse_time(params.get("start") or "0", "start time")
        length = _num(params, "length", 5.0, float, "the length")
        width, fps = _num(params, "width", 480, int, "the width"), _num(params, "fps", 12, int, "frames per second")
        if not 0 < length <= MAX_GIF_SECONDS:
            raise MediaError(f"A GIF can be 1 to {MAX_GIF_SECONDS} seconds long.")
        if not 120 <= width <= 1280 or not 5 <= fps <= 30:
            raise MediaError("Width must be 120–1280 px and frames per second 5–30.")
        flt = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];"
               f"[b][p]paletteuse=dither=bayer:bayer_scale=5")
        plan.update(output_opts=["-an", "-vf", flt, "-loop", "0"], ext="gif", suffix="", start=start, limit=length)

    else:  # mute
        if is_audio_in:
            raise MediaError("That file is only sound. Choose a video file.")
        plan.update(output_opts=["-map", "0:v", "-c:v", "copy"], ext=src, suffix="_muted")

    if plan["start"]:
        plan["input_opts"] += ["-ss", f"{plan['start']:g}"]
    return plan


def _audio_opts(fmt: str, kbps: int) -> List[str]:
    opts = ["-vn", "-map", "0:a:0"]
    if fmt == "mp3":
        return opts + ["-c:a", "libmp3lame", "-b:a", f"{kbps}k"]
    if fmt == "m4a":
        return opts + ["-c:a", "aac", "-b:a", f"{kbps}k"]
    return opts + ["-c:a", "pcm_s16le"]


def safe_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    return stem[:100] or "video"


# ── Running it ──────────────────────────────────────────────────────────────

class MediaJob:
    def __init__(self, original_name: str):
        self.id, self.dir = JOBS.new_job_dir()
        self.original_name = original_name
        self.input_path = os.path.join(self.dir, "input." + _ext(original_name))
        self.status = "starting"  # starting | working | done | error
        self.tracker = ProgressTracker()
        self.rate: Optional[str] = None   # ffmpeg's "2.3x" (how much faster than playback it runs)
        self.files: List[str] = []
        self.error: Optional[str] = None
        self.log: List[str] = []
        self.finished_at: Optional[float] = None
        self.cancelled = False
        self.proc: Optional[subprocess.Popen] = None
        self.plan: dict = {}

    def add_log(self, line: str):
        self.log.append(line)
        del self.log[:-MAX_LOG_LINES]

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def to_dict(self) -> dict:
        t = self.tracker
        return {
            "id": self.id,
            "status": self.status,
            "progress": t.progress,
            "rate": self.rate,
            "eta": t.current_eta(),
            "files": [os.path.basename(f) for f in self.files],
            "size": os.path.getsize(self.files[0]) if self.files else None,
            "error": self.error,
            "log": self.log,
        }


def create_job(original_name: str, op: str, params: dict) -> MediaJob:
    """Validate the request and make the job. The caller saves the upload to job.input_path, then calls start()."""
    if not deps.FFMPEG.find():
        raise MediaError("ffmpeg isn't installed yet. Use the 'Install ffmpeg' button at the top of this page.")
    plan = build_command(op, params, original_name)
    job = MediaJob(original_name)
    JOBS.add(job)
    job.plan = plan
    return job


def get_job(job_id: str) -> Optional[MediaJob]:
    return JOBS.get(job_id)


def start(job: MediaJob):
    threading.Thread(target=_run, args=(job,), daemon=True).start()


def _friendly_failure(lines: List[str]) -> str:
    text = "\n".join(lines).lower()
    if "does not contain any stream" in text or "matches no streams" in text:
        return "This file doesn't have the kind of stream this tool needs (for example, no audio track to extract)."
    if "unknown encoder" in text or ("encoder" in text and "not found" in text):
        return "This ffmpeg build lacks an encoder this needs. Install ffmpeg from the button at the top of the page."
    if "invalid data found" in text or "moov atom not found" in text:
        return "ffmpeg couldn't read that file. It may be damaged or incomplete."
    tail = [ln for ln in lines if ln.strip()][-2:]
    return "ffmpeg failed: " + " ".join(tail) if tail else "ffmpeg failed."


def _run(job: MediaJob):
    plan = job.plan
    out_path = os.path.join(job.dir, f"{safe_stem(job.original_name)}{plan['suffix']}.{plan['ext']}")
    cmd = [deps.FFMPEG.find(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats",
           *plan["input_opts"], "-i", job.input_path]
    if plan["limit"]:
        cmd += ["-t", f"{plan['limit']:g}"]
    cmd += plan["output_opts"] + [out_path]

    state = {"duration": None}
    stderr_lines: List[str] = []

    def read_stderr(stream):
        for line in stream:
            line = line.rstrip()
            stderr_lines.append(line)
            del stderr_lines[:-MAX_LOG_LINES]
            m = _DURATION_RE.search(line)
            if m and state["duration"] is None:
                h, mi, s = m.groups()
                state["duration"] = int(h) * 3600 + int(mi) * 60 + float(s)

    job.status = "working"
    try:
        job.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if job.cancelled:  # cancelled while starting
            job.proc.terminate()
        err_thread = threading.Thread(target=read_stderr, args=(job.proc.stderr,), daemon=True)
        err_thread.start()

        for line in job.proc.stdout:
            key, _, value = line.strip().partition("=")
            if key == "speed" and value.endswith("x"):
                job.rate = value
            elif key in ("out_time_us", "out_time_ms") and value.lstrip("-").isdigit() and int(value) >= 0:
                # (out_time_ms is microseconds too, despite the name)
                expected = plan["limit"] or (max(state["duration"] - plan["start"], 0) if state["duration"] else None)
                if expected:
                    job.tracker.on_hook({"status": "downloading", "filename": "out", "downloaded_bytes": int(value) / 1e6,
                                         "total_bytes": expected, "info_dict": {}})
        code = job.proc.wait()
        err_thread.join(timeout=2)
        job.log = stderr_lines[-MAX_LOG_LINES:]

        if job.cancelled:
            raise MediaError("Cancelled.")
        if code != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise MediaError(_friendly_failure(stderr_lines))
        job.files = [out_path]
        job.tracker.finish()
        job.status = "done"
    except MediaError as e:
        job.status, job.error = "error", str(e)
    except Exception as e:  # e.g. ffmpeg couldn't even be started
        job.status, job.error = "error", f"Couldn't run ffmpeg: {e}"
    finally:
        job.rate = None
        job.finished_at = time.time()
        try:
            os.remove(job.input_path)  # the upload is no longer needed
        except OSError:
            pass
