import io
import json
import os
import sys

import pymupdf as fitz
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aura import server  # noqa: E402

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


@pytest.fixture()
def client():
    server.app.config["TESTING"] = True
    return server.app.test_client()


@pytest.fixture(scope="module")
def pdf() -> bytes:
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}", fontsize=18)
    out = doc.tobytes()
    doc.close()
    return out


def post(client, url, data=None, **files):
    form = dict(data or {})
    for key, (name, content) in files.items():
        form[key] = (io.BytesIO(content), name)
    return client.post(url, data=form, headers=SAME_ORIGIN, content_type="multipart/form-data")


def test_home_page_lists_every_tool(client):
    html = client.get("/").get_data(as_text=True)
    for tab in ("tab-reorder", "tab-compress", "tab-protect", "tab-stamp", "tab-ytdl"):
        assert f'id="{tab}"' in html


def test_cross_site_post_is_blocked(client, pdf):
    res = client.post("/api/compress", data={"file": (io.BytesIO(pdf), "a.pdf")},
                      headers={"Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 403


def test_compress_route_reports_sizes(client, pdf):
    res = post(client, "/api/compress", {"level": "Light"}, file=("a.pdf", pdf))
    assert res.status_code == 200 and res.mimetype == "application/pdf"
    assert int(res.headers["X-Original-Size"]) == len(pdf)
    assert int(res.headers["X-New-Size"]) <= len(pdf)
    assert "a_compressed.pdf" in res.headers["Content-Disposition"]


def test_organize_route(client, pdf):
    pages = json.dumps([{"page": 2, "rotate": 90}, {"page": 0, "rotate": 0}])
    res = post(client, "/api/reorder", {"pages": pages}, file=("a.pdf", pdf))
    doc = fitz.open(stream=res.data, filetype="pdf")
    assert len(doc) == 2 and doc[0].rotation == 90
    assert post(client, "/api/reorder", {"pages": "[]"}, file=("a.pdf", pdf)).status_code == 400
    assert post(client, "/api/reorder", {"pages": "nope"}, file=("a.pdf", pdf)).status_code == 400


def test_protect_and_unlock_routes(client, pdf):
    locked = post(client, "/api/protect", {"mode": "protect", "password": "pw"}, file=("a.pdf", pdf))
    assert locked.status_code == 200
    bad = post(client, "/api/protect", {"mode": "unlock", "password": "wrong"}, file=("a.pdf", locked.data))
    assert bad.status_code == 400 and "Wrong password" in bad.get_json()["error"]
    ok = post(client, "/api/protect", {"mode": "unlock", "password": "pw"}, file=("a.pdf", locked.data))
    assert ok.status_code == 200 and not fitz.open(stream=ok.data, filetype="pdf").needs_pass
    assert post(client, "/api/protect", {"mode": "protect", "password": ""}, file=("a.pdf", pdf)).status_code == 400


def test_stamp_route(client, pdf):
    res = post(client, "/api/stamp", {"watermark": "DRAFT", "numbering": "Page 1 of N", "opacity": "30"},
               file=("a.pdf", pdf))
    assert res.status_code == 200
    assert "Page 3 of 3" in fitz.open(stream=res.data, filetype="pdf")[2].get_text()
    empty = post(client, "/api/stamp", {}, file=("a.pdf", pdf))
    assert empty.status_code == 400


def test_imgresize_route_single_and_zip(client):
    import random

    from PIL import Image

    rnd = random.Random(5)
    img = Image.frombytes("RGB", (600, 400), bytes(rnd.randrange(256) for _ in range(600 * 400 * 3)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    jpg = buf.getvalue()

    one = post(client, "/api/imgresize", {"mode": "fit", "width": "200", "height": "200", "format": "webp",
                                          "quality": "80", "strip": "1"}, files=("a.jpg", jpg))
    assert one.status_code == 200 and one.mimetype == "image/webp"
    assert Image.open(io.BytesIO(one.data)).size == (200, 133)
    assert int(one.headers["X-New-Size"]) < int(one.headers["X-Original-Size"])

    limited = post(client, "/api/imgresize", {"max_kb": "20", "strip": "1"}, files=("a.jpg", jpg))
    assert limited.status_code == 200 and len(limited.data) <= 20 * 1024 and limited.headers["X-Notes"]

    form = {"files": [(io.BytesIO(jpg), "a.jpg"), (io.BytesIO(jpg), "b.jpg")], "mode": "percent", "percent": "50"}
    two = client.post("/api/imgresize", data=form, headers=SAME_ORIGIN, content_type="multipart/form-data")
    assert two.mimetype == "application/zip"

    bad = post(client, "/api/imgresize", {"mode": "exact", "width": "0", "height": "0"}, files=("a.jpg", jpg))
    assert bad.status_code == 400 and "width" in bad.get_json()["error"].lower()


# ── video & audio routes ────────────────────────────────────────────────────

def _tiny_video(tmp_path):
    import subprocess

    from aura import deps

    ffmpeg = deps.FFMPEG.find()
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "tiny.mp4"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
                    "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest",
                    str(path)], check=True)
    return path.read_bytes()


def _wait(client, job_id):
    import time

    for _ in range(300):
        job = client.get(f"/api/media/jobs/{job_id}").get_json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    raise AssertionError("media job never finished")


def test_media_job_end_to_end(client, tmp_path):
    video = _tiny_video(tmp_path)
    res = post(client, "/api/media/start", {"op": "audio", "format": "mp3", "bitrate": "128"}, file=("holiday.mp4", video))
    assert res.status_code == 200
    job = _wait(client, res.get_json()["job_id"])
    assert job["status"] == "done" and job["files"] == ["holiday_audio.mp3"] and job["progress"] == 1.0
    dl = client.get(f"/api/media/jobs/{res.get_json()['job_id']}/file")
    assert dl.status_code == 200 and "holiday_audio.mp3" in dl.headers["Content-Disposition"] and len(dl.data) > 1000


def test_media_validation_errors(client, tmp_path):
    video = b"x" * 100
    assert post(client, "/api/media/start", {"op": "audio"}).status_code == 400            # no file
    bad_op = post(client, "/api/media/start", {"op": "explode"}, file=("a.mp4", video))
    assert bad_op.status_code == 400 and "operation" in bad_op.get_json()["error"].lower()
    bad_type = post(client, "/api/media/start", {"op": "convert"}, file=("a.exe", video))
    assert bad_type.status_code == 400 and "unsupported" in bad_type.get_json()["error"]
    bad_time = post(client, "/api/media/start", {"op": "trim", "start": "soon"}, file=("a.mp4", video))
    assert bad_time.status_code == 400 and "isn't a valid" in bad_time.get_json()["error"]
    bad_num = post(client, "/api/media/start", {"op": "gif", "width": "wide"}, file=("a.mp4", video))
    assert bad_num.status_code == 400 and "valid number" in bad_num.get_json()["error"]


def test_media_unknown_job_is_404(client):
    assert client.get("/api/media/jobs/nope").status_code == 404
    assert client.get("/api/media/jobs/nope/file").status_code == 404
    assert client.post("/api/media/jobs/nope/cancel", headers=SAME_ORIGIN).status_code == 404


def test_media_without_ffmpeg_explains_how_to_fix(client, monkeypatch):
    from aura import deps

    monkeypatch.setattr(deps.FFMPEG, "find", lambda: None)
    res = post(client, "/api/media/start", {"op": "convert", "format": "mp3"}, file=("a.mp4", b"x"))
    assert res.status_code == 400 and "Install ffmpeg" in res.get_json()["error"]


def test_ffmpeg_callout_shows_on_media_and_downloader_pages_when_missing(client, monkeypatch):
    from aura import deps

    monkeypatch.setattr(deps, "missing", lambda: [deps.FFMPEG])
    html = client.get("/").get_data(as_text=True)
    assert html.count('class="callout deps-callout"') == 4          # media page + the three downloaders
    assert "These tools need it." in html
