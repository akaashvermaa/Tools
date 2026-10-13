import os
import re
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aura import deps, media_jobs  # noqa: E402
from aura.media_jobs import MediaError  # noqa: E402

FFMPEG = deps.FFMPEG.find()
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")


# ── command building (no ffmpeg needed) ─────────────────────────────────────

def test_parse_time_accepts_seconds_and_clock_formats():
    assert media_jobs.parse_time("90") == 90
    assert media_jobs.parse_time("1:30") == 90
    assert media_jobs.parse_time("01:02:03.5") == 3723.5
    for bad in ("", "abc", "-5", "1:2:3:4"):
        with pytest.raises(MediaError):
            media_jobs.parse_time(bad)


def test_trim_validates_and_plans():
    plan = media_jobs.build_command("trim", {"start": "10", "end": "25"}, "clip.mp4")
    assert plan["start"] == 10 and plan["limit"] == 15 and plan["ext"] == "mp4"
    assert plan["input_opts"] == ["-ss", "10"] and "copy" in plan["output_opts"]
    assert media_jobs.build_command("trim", {"start": "5"}, "clip.mp4")["limit"] is None
    with pytest.raises(MediaError, match="after the start"):
        media_jobs.build_command("trim", {"start": "30", "end": "10"}, "clip.mp4")
    precise = media_jobs.build_command("trim", {"start": "0", "end": "5", "mode": "precise"}, "clip.mkv")
    assert precise["ext"] == "mp4" and "libx264" in precise["output_opts"]


def test_operations_reject_the_wrong_kind_of_file():
    with pytest.raises(MediaError, match="no picture"):
        media_jobs.build_command("convert", {"format": "mp4"}, "song.mp3")
    for op in ("compress", "gif", "mute"):
        with pytest.raises(MediaError):
            media_jobs.build_command(op, {}, "song.mp3")
    with pytest.raises(MediaError, match="unsupported"):
        media_jobs.build_command("convert", {}, "notes.txt")
    with pytest.raises(MediaError):
        media_jobs.build_command("explode", {}, "a.mp4")


def test_option_validation():
    with pytest.raises(MediaError):
        media_jobs.build_command("audio", {"format": "ogg"}, "a.mp4")
    with pytest.raises(MediaError):
        media_jobs.build_command("audio", {"format": "mp3", "bitrate": 999}, "a.mp4")
    with pytest.raises(MediaError):
        media_jobs.build_command("gif", {"length": 99}, "a.mp4")
    with pytest.raises(MediaError):
        media_jobs.build_command("gif", {"width": 5000}, "a.mp4")
    with pytest.raises(MediaError):
        media_jobs.build_command("compress", {"level": "Insane"}, "a.mp4")


def test_same_format_conversion_does_not_reuse_the_original_name():
    assert media_jobs.build_command("convert", {"format": "mp4"}, "a.mp4")["suffix"] == "_converted"
    assert media_jobs.build_command("convert", {"format": "mp3"}, "a.mp4")["suffix"] == ""


def test_safe_stem_removes_path_tricks():
    assert media_jobs.safe_stem("../../etc/pass:wd?.mp4") == "pass_wd_"
    assert media_jobs.safe_stem("") == "video"
    assert len(media_jobs.safe_stem("x" * 500 + ".mp4")) == 100


# ── real runs ───────────────────────────────────────────────────────────────

def probe(path):
    """(duration seconds, has video, has audio, (w, h)) read from ffmpeg's own report."""
    text = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace").stderr
    d = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", text)
    dur = int(d[1]) * 3600 + int(d[2]) * 60 + float(d[3]) if d else None
    size = re.search(r"Video:.*?, (\d{2,5})x(\d{2,5})", text)
    return dur, "Video:" in text, "Audio:" in text, (int(size[1]), int(size[2])) if size else None


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """4 s, 321x241 (odd on purpose), with a sine-wave sound track."""
    if not FFMPEG:
        pytest.skip("ffmpeg not installed")
    path = str(tmp_path_factory.mktemp("media") / "sample.mp4")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=321x241:rate=15:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "mpeg4", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", path], check=True)
    return path


def run_op(clip_path, op, **params):
    job = media_jobs.create_job(os.path.basename(clip_path), op, params)
    shutil.copy(clip_path, job.input_path)
    media_jobs.start(job)
    deadline = time.time() + 120
    while job.status not in ("done", "error") and time.time() < deadline:
        time.sleep(0.1)
    return job


@needs_ffmpeg
@pytest.mark.parametrize("fmt, video", [("mp4", True), ("mkv", True), ("webm", True), ("mp3", False), ("m4a", False), ("wav", False)])
def test_convert_each_format(clip, fmt, video):
    job = run_op(clip, "convert", format=fmt)
    assert job.status == "done", job.error
    out = job.files[0]
    assert out.endswith("." + fmt)
    dur, has_v, has_a, _ = probe(out)
    assert has_v is video and has_a and 3.5 < dur < 4.6


@needs_ffmpeg
def test_odd_sized_video_survives_h264(clip):
    job = run_op(clip, "convert", format="mp4")
    assert job.status == "done", job.error
    w, h = probe(job.files[0])[3]
    assert w % 2 == 0 and h % 2 == 0


@needs_ffmpeg
def test_extract_audio_mp3(clip):
    job = run_op(clip, "audio", format="mp3", bitrate=128)
    assert job.status == "done", job.error
    dur, has_v, has_a, _ = probe(job.files[0])
    assert not has_v and has_a and os.path.basename(job.files[0]) == "sample_audio.mp3"


@needs_ffmpeg
def test_trim_fast_and_precise(clip):
    fast = run_op(clip, "trim", start="1", end="3")
    assert fast.status == "done", fast.error
    assert 1.0 < probe(fast.files[0])[0] < 3.5          # keyframe-snapped, so not exact
    precise = run_op(clip, "trim", start="1", end="3", mode="precise")
    assert precise.status == "done", precise.error
    assert abs(probe(precise.files[0])[0] - 2.0) < 0.3  # exact


@needs_ffmpeg
def test_compress_with_max_height(clip):
    job = run_op(clip, "compress", level="Strong", max_height="Original")
    assert job.status == "done", job.error
    small = run_op(clip, "compress", level="Balanced", max_height="480p")
    assert small.status == "done" and probe(small.files[0])[3][1] <= 241   # never enlarged past the original
    assert os.path.basename(small.files[0]) == "sample_small.mp4"


@needs_ffmpeg
def test_gif(clip):
    job = run_op(clip, "gif", start="0", length=2, width=160, fps=10)
    assert job.status == "done", job.error
    dur, has_v, has_a, size = probe(job.files[0])
    assert job.files[0].endswith(".gif") and not has_a and size[0] == 160 and 1.5 < dur < 2.6


@needs_ffmpeg
def test_mute_keeps_picture_drops_sound(clip):
    job = run_op(clip, "mute")
    assert job.status == "done", job.error
    _, has_v, has_a, _ = probe(job.files[0])
    assert has_v and not has_a


@needs_ffmpeg
def test_progress_reaches_100_and_input_is_removed(clip):
    job = run_op(clip, "convert", format="mp4")
    assert job.to_dict()["progress"] == 1.0 and job.to_dict()["files"] == ["sample_converted.mp4"]
    assert not os.path.exists(job.input_path)


@needs_ffmpeg
def test_broken_file_gives_readable_error(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"this is not a video" * 100)
    job = run_op(str(bad), "convert", format="mp3")
    assert job.status == "error" and job.error and "Traceback" not in job.error
    assert job.finished_at


@needs_ffmpeg
def test_no_audio_track_is_explained(tmp_path):
    silent = str(tmp_path / "silent.mp4")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", silent], check=True)
    job = run_op(silent, "audio", format="mp3")
    assert job.status == "error" and "audio" in job.error.lower()


@needs_ffmpeg
def test_cancel_stops_a_running_job(tmp_path):
    long_clip = str(tmp_path / "long.mp4")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=40",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", long_clip], check=True)
    job = media_jobs.create_job("long.mp4", "convert", {"format": "webm"})  # VP9 is slow enough to catch
    shutil.copy(long_clip, job.input_path)
    media_jobs.start(job)
    time.sleep(1.5)
    job.cancel()
    deadline = time.time() + 20
    while job.status not in ("done", "error") and time.time() < deadline:
        time.sleep(0.1)
    assert job.status == "error" and job.error == "Cancelled."
