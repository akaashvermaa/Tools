"""
Aura Tools — local web app.

Start it with launch.py (or `python -m aura`); it opens http://127.0.0.1:5000.
"""

import base64
import io
import os
import signal
import socket
import sys
import threading
import webbrowser

from urllib.parse import urlparse

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from . import deps, pdf_tools, yt_jobs

HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("AURA_PORT", 5000))

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(PACKAGE_DIR, "templates"),
    static_folder=os.path.join(PACKAGE_DIR, "static"),
)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB per request

MIMETYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "zip": "application/zip",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def uploaded_files(field: str = "files"):
    files = [(f.filename or "file", f.read()) for f in request.files.getlist(field) if f]
    if not files:
        raise ValueError("Please add at least one file.")
    return files


def send_bytes(data: bytes, filename: str):
    return send_file(
        io.BytesIO(data),
        mimetype=MIMETYPES.get(pdf_tools.ext_of(filename), "application/octet-stream"),
        as_attachment=True,
        download_name=filename,
    )


def send_one_or_zip(files, zip_name: str):
    if len(files) == 1:
        return send_bytes(files[0][1], files[0][0])
    return send_bytes(pdf_tools.zip_files(files), zip_name)


def form_int(name: str, default: int) -> int:
    try:
        return int(request.form.get(name, default))
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' must be a number.")


def form_bool(name: str) -> bool:
    return request.form.get(name) in ("1", "true", "on")


@app.errorhandler(ValueError)
def handle_value_error(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(Exception)
def handle_error(e):
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    app.logger.exception("Request failed")
    return jsonify(error=f"Error: {e}"), 500


@app.before_request
def only_local_pages():
    """
    Block other websites from driving this local server: reject unknown Host
    headers (DNS rebinding) and cross-site POSTs (CSRF).
    """
    host = (request.host or "").rsplit(":", 1)[0]
    if host not in ("127.0.0.1", "localhost"):
        abort(403, "Forbidden host.")
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            abort(403, "Cross-site request blocked.")
        if origin and urlparse(origin).netloc != request.host:
            abort(403, "Cross-site request blocked.")


# ── Pages ───────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template(
        "index.html",
        routes=pdf_tools.available_routes(),
        docx_support=pdf_tools.DOCX_SUPPORT,
        missing_deps=[d.key for d in deps.missing()],
        deps_info={k: {"label": d.label, "size": d.size_hint, "purpose": d.purpose} for k, d in deps.DEPS.items()},
        cookie_browsers=yt_jobs.COOKIE_BROWSERS,
        qualities=yt_jobs.QUALITIES,
        containers=yt_jobs.CONTAINERS,
    )


# ── PDF tools ───────────────────────────────────────────────────────────────

@app.post("/api/convert")
def api_convert():
    files = uploaded_files()
    pdf = pdf_tools.files_to_pdf(files, fit_a4=form_bool("fit_a4"), compress=form_bool("compress"))
    return send_bytes(pdf, "output.pdf")


@app.post("/api/fileconv")
def api_fileconv():
    outputs = pdf_tools.convert_files(
        uploaded_files(),
        route_id=request.form.get("route", ""),
        quality=form_int("quality", 92),
        dpi=form_int("dpi", 150),
        merge=form_bool("merge"),
    )
    return send_one_or_zip(outputs, "converted.zip")


@app.post("/api/merge")
def api_merge():
    files = uploaded_files()
    if len(files) < 2:
        raise ValueError("Please add at least 2 PDF files to merge.")
    return send_bytes(pdf_tools.merge_pdfs(files), "merged.pdf")


@app.post("/api/pdf/info")
def api_pdf_info():
    name, data = uploaded_files("file")[0]
    return jsonify(pages=pdf_tools.page_count(data))


@app.post("/api/split")
def api_split():
    name, data = uploaded_files("file")[0]
    base = pdf_tools.base_of(name)
    if request.form.get("mode") == "every_n":
        parts = pdf_tools.split_every_n(data, form_int("n", 1), base)
        return send_one_or_zip(parts, f"{base}_split.zip")
    range_text = request.form.get("range", "").strip()
    if not range_text:
        raise ValueError("Please enter a page range (e.g., 1-3, 5).")
    pdf, _ = pdf_tools.extract_pages(data, range_text)
    return send_bytes(pdf, f"{base}_extracted.pdf")


@app.post("/api/pdf/thumbnails")
def api_thumbnails():
    _, data = uploaded_files("file")[0]
    thumbs = pdf_tools.page_thumbnails(data)
    return jsonify(pages=["data:image/png;base64," + base64.b64encode(t).decode() for t in thumbs])


@app.post("/api/reorder")
def api_reorder():
    name, data = uploaded_files("file")[0]
    try:
        order = [int(x) for x in request.form.get("order", "").split(",") if x != ""]
    except ValueError:
        raise ValueError("Invalid page order.")
    return send_bytes(pdf_tools.reorder_pages(data, order), f"{pdf_tools.base_of(name)}_reordered.pdf")


# ── YouTube downloader ──────────────────────────────────────────────────────

def _yt_body() -> dict:
    body = request.get_json(silent=True) or {}
    url = str(body.get("url", "")).strip()
    if not yt_jobs.is_valid_url(url):
        raise ValueError("Please enter a valid YouTube URL.")
    browser = body.get("cookies") or None
    if browser is not None and browser not in yt_jobs.COOKIE_BROWSERS:
        raise ValueError("Unknown browser for cookies.")
    return {**body, "url": url, "cookies": browser}


@app.post("/api/yt/info")
def api_yt_info():
    body = _yt_body()
    return jsonify(yt_jobs.fetch_info(body["url"], body["cookies"]))


@app.post("/api/yt/download")
def api_yt_download():
    body = _yt_body()
    quality = body.get("quality", "Best Available")
    container = body.get("container", "mp4")
    if quality not in yt_jobs.QUALITIES or container not in yt_jobs.CONTAINERS:
        raise ValueError("Invalid quality or container.")
    job = yt_jobs.start_download(
        body["url"], quality, container,
        subtitles=bool(body.get("subtitles")), playlist=bool(body.get("playlist")),
        cookies_browser=body["cookies"],
    )
    return jsonify(job_id=job.id)


@app.get("/api/yt/jobs/<job_id>")
def api_yt_job(job_id):
    job = yt_jobs.get_job(job_id)
    if not job:
        abort(404, "Download not found (it may have expired).")
    return jsonify(job.to_dict())


@app.get("/api/yt/jobs/<job_id>/files/<int:idx>")
def api_yt_file(job_id, idx):
    job = yt_jobs.get_job(job_id)
    if not job or not 0 <= idx < len(job.files):
        abort(404, "File not found (it may have expired).")
    return send_file(job.files[idx], as_attachment=True, download_name=os.path.basename(job.files[idx]))


# ── ffmpeg setup ────────────────────────────────────────────────────────────

@app.get("/api/deps")
def api_deps():
    st = deps.status()
    return jsonify({k: {**st[k], "install": deps.install_state(k)} for k in deps.DEPS})


@app.post("/api/deps/<key>/install")
def api_deps_install(key):
    if key not in deps.DEPS:
        abort(404, "Unknown tool.")
    if deps.status()[key]["ok"]:
        return jsonify(install={"status": "done", "message": "Already installed", "progress": 1, "error": None})
    return jsonify(install=deps.start_background_install(key))


# ── Entry point ─────────────────────────────────────────────────────────────

def free_port(start: int) -> int:
    """First free port from `start` (port 5000 is taken by AirPlay on macOS)."""
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port between {start} and {start + 19}.")


def main():
    # Closing the terminal sends SIGHUP / SIGTERM; exit normally so cleanup (atexit) runs.
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: sys.exit(0))

    port = free_port(DEFAULT_PORT)
    url = f"http://{HOST}:{port}"
    print(f"Aura Tools running at {url}  (Ctrl+C to quit)")
    if not os.environ.get("AURA_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=port, threaded=True)

