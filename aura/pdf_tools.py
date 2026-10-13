"""
PDF / image / DOCX processing used by the web app.

Every function works on in-memory bytes so the Flask layer can pass uploads
straight in and stream results straight back.
"""

import io
import os
import zipfile
from typing import List, Tuple

import pymupdf as fitz
from PIL import Image
from pypdf import PdfReader, PdfWriter

from . import image_tools

# --- Optional DOCX support (graceful degradation) ---
try:
    from docx import Document as DocxDocument
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4 as RL_A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    DOCX_SUPPORT = True
except ImportError:
    DOCX_SUPPORT = False

IMAGE_EXTS = set(image_tools.INPUT_EXTS)  # png, jpg, webp, bmp, tiff, gif and (if available) heic

def _image_label(target: str, exclude: tuple) -> str:
    exts = [e for e in image_tools.INPUT_EXTS if e not in exclude]
    names = dict.fromkeys(("JPG" if e in ("jpg", "jpeg") else "TIFF" if e in ("tif", "tiff") else
                           "HEIC" if e in ("heic", "heif") else e.upper()) for e in exts)
    return f"Image → {target} ({', '.join(names)})"


# (label, input extensions, output format) for each File Converter route
CONVERT_ROUTES = {
    "img2pdf":  ("Images → PDF", sorted(IMAGE_EXTS), "pdf"),
    "any2jpg":  (_image_label("JPG", ("jpg", "jpeg")), [e for e in image_tools.INPUT_EXTS if e not in ("jpg", "jpeg")], "jpg"),
    "any2png":  (_image_label("PNG", ("png",)), [e for e in image_tools.INPUT_EXTS if e != "png"], "png"),
    "any2webp": (_image_label("WebP", ("webp",)), [e for e in image_tools.INPUT_EXTS if e != "webp"], "webp"),
    "any2ico":  ("Image → ICO (favicon)", list(image_tools.INPUT_EXTS), "ico"),
    "pdf2jpg":  ("PDF → JPG", ["pdf"], "jpg"),
    "pdf2png":  ("PDF → PNG", ["pdf"], "png"),
    "docx2pdf": ("DOCX → PDF", ["docx"], "pdf"),
}

UploadedFile = Tuple[str, bytes]  # (filename, data)


def ext_of(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().lstrip(".")


def base_of(filename: str) -> str:
    return os.path.splitext(os.path.basename(filename))[0] or "file"


def available_routes() -> dict:
    return {k: v for k, v in CONVERT_ROUTES.items() if k != "docx2pdf" or DOCX_SUPPORT}


# ── Single-file converters ──────────────────────────────────────────────────

def image_to_pdf_bytes(data: bytes, fit_a4: bool, compress: bool, name: str = "image") -> bytes:
    """
    Convert an image to a one-page PDF.
    fit_a4 centers it on an A4 page; compress re-encodes it as JPEG (lossy).
    JPEG/PNG files are embedded untouched when possible; others, and phone photos that rely on
    the EXIF rotation flag, are re-encoded so they come out upright.
    """
    raw = Image.open(io.BytesIO(data))
    dpi = raw.info.get("dpi", (72, 72))
    needs_turn = raw.getexif().get(0x0112, 1) != 1
    img, _, _ = image_tools.open_image(data, name)
    alpha = image_tools.has_alpha(img)

    if compress or alpha or needs_turn or raw.format not in ("JPEG", "PNG"):
        buf = io.BytesIO()
        if alpha and not compress:
            img.convert("RGBA").save(buf, format="PNG")
        else:
            image_tools.flatten_to_rgb(img).save(buf, format="JPEG", quality=75 if compress else 95)
        img_bytes = buf.getvalue()
    else:
        img_bytes = data

    doc = fitz.open()
    if fit_a4:
        a4_w, a4_h = fitz.paper_size("A4")
        page = doc.new_page(width=a4_w, height=a4_h)
        px, py = a4_w * 0.03, a4_h * 0.03
        page.insert_image(fitz.Rect(px, py, a4_w - px, a4_h - py), stream=img_bytes, keep_proportion=True)
    else:
        dpi_x, dpi_y = (dpi[0], dpi[1]) if isinstance(dpi, tuple) and len(dpi) >= 2 else (72, 72)
        page = doc.new_page(width=img.width / dpi_x * 72, height=img.height / dpi_y * 72)
        page.insert_image(page.rect, stream=img_bytes)

    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


def docx_to_pdf_bytes(data: bytes) -> bytes:
    """Convert a .docx to PDF with basic formatting (bold, italic, headings, alignment)."""
    if not DOCX_SUPPORT:
        raise ValueError("DOCX support needs python-docx and reportlab installed.")

    doc = DocxDocument(io.BytesIO(data))
    buffer = io.BytesIO()
    pdf_doc = SimpleDocTemplate(
        buffer, pagesize=RL_A4, leftMargin=60, rightMargin=60, topMargin=60, bottomMargin=60
    )

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("DocxBody", parent=styles["Normal"], fontSize=11, leading=16, spaceAfter=8)
    heading_style = ParagraphStyle(
        "DocxHeading", parent=styles["Heading1"], fontSize=16, leading=22, spaceAfter=12, spaceBefore=16
    )
    alignment_map = {0: TA_LEFT, 1: TA_CENTER, 2: TA_RIGHT, 3: TA_JUSTIFY}

    story = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            story.append(Spacer(1, 8))
            continue

        fragments = []
        for run in para.runs:
            if not run.text:
                continue
            run_text = run.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if run.bold:
                run_text = f"<b>{run_text}</b>"
            if run.italic:
                run_text = f"<i>{run_text}</i>"
            fragments.append(run_text)
        styled_text = "".join(fragments) or text

        if para.style is not None and para.style.name and para.style.name.startswith("Heading"):
            story.append(Paragraph(styled_text, heading_style))
        else:
            style = ParagraphStyle("tmp", parent=body_style)
            if para.alignment is not None:
                style.alignment = alignment_map.get(int(para.alignment), TA_LEFT)
            story.append(Paragraph(styled_text, style))

    if not story:
        story.append(Paragraph("(Empty document)", body_style))

    pdf_doc.build(story)
    return buffer.getvalue()


def _image_to_image(data: bytes, out_fmt: str, quality: int, name: str = "image") -> bytes:
    return image_tools.convert_image(data, out_fmt, quality, name)


def _pdf_to_images(data: bytes, out_fmt: str, quality: int, dpi: int) -> List[bytes]:
    doc = fitz.open(stream=data, filetype="pdf")
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pages = []
    for page in doc:
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        buf = io.BytesIO()
        if out_fmt == "jpg":
            img.save(buf, format="JPEG", quality=quality)
        else:
            img.save(buf, format="PNG")
        pages.append(buf.getvalue())
    doc.close()
    return pages


# ── PDF helpers ─────────────────────────────────────────────────────────────

def _reader(data: bytes) -> PdfReader:
    try:
        return PdfReader(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"Could not read PDF: {e}")


def _append_pdf(writer: PdfWriter, pdf_bytes: bytes):
    for page in _reader(pdf_bytes).pages:
        writer.add_page(page)


def _write(writer: PdfWriter) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def zip_files(files: List[UploadedFile]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used = set()
        for name, data in files:
            unique, n = name, 1
            while unique in used:
                n += 1
                unique = f"{base_of(name)} ({n}).{ext_of(name)}"
            used.add(unique)
            zf.writestr(unique, data)
    return buf.getvalue()


# ── Tool operations ─────────────────────────────────────────────────────────

def files_to_pdf(files: List[UploadedFile], fit_a4: bool, compress: bool) -> bytes:
    """Convert Tab: images and DOCX files → one PDF, in the given order."""
    writer = PdfWriter()
    for name, data in files:
        ext = ext_of(name)
        if ext in IMAGE_EXTS:
            _append_pdf(writer, image_to_pdf_bytes(data, fit_a4, compress, name))
        elif ext == "docx":
            _append_pdf(writer, docx_to_pdf_bytes(data))
        else:
            raise ValueError(f"Unsupported file type: {name}")
    return _write(writer)


def convert_files(
    files: List[UploadedFile], route_id: str, quality: int, dpi: int, merge: bool
) -> List[UploadedFile]:
    """File Converter tab. Returns output files; a merged PDF comes back as a single file."""
    routes = available_routes()
    if route_id not in routes:
        raise ValueError("Unknown conversion route.")
    _, src_exts, out_fmt = routes[route_id]
    quality = max(10, min(100, quality))
    dpi = max(36, min(600, dpi))

    outputs: List[UploadedFile] = []
    merged = PdfWriter()
    for name, data in files:
        ext = ext_of(name)
        if ext not in src_exts:
            raise ValueError(f"{name} doesn't match the selected route.")
        base = base_of(name)

        if out_fmt == "pdf":
            pdf = image_to_pdf_bytes(data, fit_a4=True, compress=quality < 95, name=name) if ext in IMAGE_EXTS \
                else docx_to_pdf_bytes(data)
            if merge:
                _append_pdf(merged, pdf)
            else:
                outputs.append((f"{base}.pdf", pdf))
        elif ext == "pdf":
            pages = _pdf_to_images(data, out_fmt, quality, dpi)
            if len(pages) == 1:
                outputs.append((f"{base}.{out_fmt}", pages[0]))
            else:
                outputs.extend((f"{base}_page{i + 1}.{out_fmt}", p) for i, p in enumerate(pages))
        else:
            outputs.append((f"{base}.{out_fmt}", _image_to_image(data, out_fmt, quality, name)))

    if out_fmt == "pdf" and merge:
        return [("converted.pdf", _write(merged))]
    return outputs


def merge_pdfs(files: List[UploadedFile]) -> bytes:
    writer = PdfWriter()
    for _, data in files:
        _append_pdf(writer, data)
    return _write(writer)


def page_count(data: bytes) -> int:
    return len(_reader(data).pages)


def parse_page_ranges(text: str, max_page: int) -> List[int]:
    """Parse '1-3, 5, 8-10' into sorted 0-indexed page numbers."""
    pages = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        try:
            if "-" in part:
                start, end = (int(x) for x in part.split("-", 1))
                pages.update(p - 1 for p in range(max(1, start), min(max_page, end) + 1))
            else:
                p = int(part)
                if 1 <= p <= max_page:
                    pages.add(p - 1)
        except ValueError:
            raise ValueError(f"Invalid page range: '{part}'")
    return sorted(pages)


def extract_pages(data: bytes, range_text: str) -> Tuple[bytes, int]:
    reader = _reader(data)
    pages = parse_page_ranges(range_text, len(reader.pages))
    if not pages:
        raise ValueError("No valid pages in that range.")
    writer = PdfWriter()
    for p in pages:
        writer.add_page(reader.pages[p])
    return _write(writer), len(pages)


def split_every_n(data: bytes, n: int, base: str) -> List[UploadedFile]:
    if n < 1:
        raise ValueError("Pages per file must be at least 1.")
    reader = _reader(data)
    total = len(reader.pages)
    parts = []
    for idx, start in enumerate(range(0, total, n)):
        writer = PdfWriter()
        for p in range(start, min(start + n, total)):
            writer.add_page(reader.pages[p])
        parts.append((f"{base}_part{idx + 1}.pdf", _write(writer)))
    return parts


def page_thumbnails(data: bytes, width: int = 140) -> List[bytes]:
    """Render each page as a small PNG."""
    doc = fitz.open(stream=data, filetype="pdf")
    thumbs = []
    for page in doc:
        scale = width / page.rect.width
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        thumbs.append(pix.tobytes("png"))
    doc.close()
    return thumbs


def organize_pages(data: bytes, pages: List[dict]) -> bytes:
    """Rebuild a PDF from `pages`: [{"page": 0-based source page, "rotate": degrees to turn it}].

    The order of the list is the new page order; pages left out are deleted."""
    reader = _reader(data)
    total = len(reader.pages)
    if not pages:
        raise ValueError("A PDF needs at least one page; you can't delete them all.")
    writer = PdfWriter()
    for item in pages:
        p, turn = int(item["page"]), int(item.get("rotate", 0)) % 360
        if not 0 <= p < total or turn % 90:
            raise ValueError("Invalid page order.")
        page = writer.add_page(reader.pages[p])
        if turn:
            page.rotate(turn)
    return _write(writer)


def reorder_pages(data: bytes, order: List[int]) -> bytes:
    return organize_pages(data, [{"page": p} for p in order])


# ── Compress ────────────────────────────────────────────────────────────────

# level -> (max image dpi, JPEG quality); None = leave the images alone
COMPRESS_LEVELS = {"Light": None, "Balanced": (150, 70), "Strong": (96, 50)}


def _open_pdf(data: bytes, password: str = "") -> "fitz.Document":
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Could not read PDF: {e}")
    if doc.needs_pass and not (password and doc.authenticate(password)):
        doc.close()
        raise ValueError("This PDF is password-protected. Unlock it first (Protect / unlock tool).")
    return doc


def _shrink_images(doc: "fitz.Document", max_dpi: int, quality: int):
    """Downsample and re-encode large images, but only keep the result when it is smaller."""
    done = set()
    for page in doc:
        for info in page.get_images(full=True):
            xref, smask = info[0], info[1]
            if xref in done or smask:  # images with transparency are left alone
                continue
            done.add(xref)
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            width_in = max(r.width for r in rects) / 72
            try:
                raw = doc.extract_image(xref)
                img = Image.open(io.BytesIO(raw["image"]))
                img.load()
            except Exception:
                continue  # exotic formats (JPX, masks...) stay as they are
            if width_in <= 0 or img.width / width_in <= max_dpi * 1.1:
                continue
            target_w = max(1, int(width_in * max_dpi))
            img = img.resize((target_w, max(1, round(img.height * target_w / img.width))), Image.LANCZOS)
            img = img.convert("L" if img.mode == "L" else "RGB")
            out = io.BytesIO()
            img.save(out, "JPEG", quality=quality, optimize=True)
            if out.tell() < len(raw["image"]):
                page.replace_image(xref, stream=out.getvalue())


def compress_pdf(data: bytes, level: str) -> bytes:
    """Smaller PDF. Never returns something bigger than the input."""
    if level not in COMPRESS_LEVELS:
        raise ValueError("Unknown compression level.")
    doc = _open_pdf(data)
    try:
        if COMPRESS_LEVELS[level]:
            _shrink_images(doc, *COMPRESS_LEVELS[level])
        out = doc.tobytes(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True)
    finally:
        doc.close()
    return out if len(out) < len(data) else data


# ── Password protect / unlock ───────────────────────────────────────────────

def protect_pdf(data: bytes, password: str) -> bytes:
    if not password:
        raise ValueError("Please enter a password.")
    doc = _open_pdf(data)
    try:
        return doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password, garbage=3,
                           deflate=True)
    finally:
        doc.close()


def unlock_pdf(data: bytes, password: str) -> bytes:
    """Remove the password from a PDF, using the password the user knows."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Could not read PDF: {e}")
    try:
        if not doc.needs_pass:
            raise ValueError("This PDF isn't password-protected.")
        if not doc.authenticate(password or ""):
            raise ValueError("Wrong password.")
        return doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE, garbage=3, deflate=True)
    finally:
        doc.close()


# ── Watermark and page numbers ──────────────────────────────────────────────

NUMBER_FORMATS = {"1": "{n}", "Page 1": "Page {n}", "Page 1 of N": "Page {n} of {total}"}
NUMBER_POSITIONS = ("Bottom center", "Bottom right", "Bottom left", "Top center", "Top right", "Top left")


def _latin(text: str, what: str):
    if any(ord(c) > 255 for c in text):
        raise ValueError(f"{what} can only use standard Latin letters, digits and punctuation for now.")


def _upright(page: "fitz.Page", point: "fitz.Point") -> "fitz.Point":
    """A point as the viewer sees it -> the page's own (unrotated) coordinates."""
    return point * page.derotation_matrix


def stamp_pdf(data: bytes, watermark: str = "", opacity: float = 0.2, size: int = 64,
              numbering: str = "", position: str = "Bottom center") -> bytes:
    """Add a diagonal text watermark and/or page numbers to every page."""
    watermark = watermark.strip()
    if not watermark and not numbering:
        raise ValueError("Enter watermark text or choose a page-number style.")
    if numbering and numbering not in NUMBER_FORMATS:
        raise ValueError("Unknown page-number style.")
    if position not in NUMBER_POSITIONS:
        raise ValueError("Unknown page-number position.")
    _latin(watermark, "The watermark")
    opacity = min(max(float(opacity), 0.05), 1.0)
    size = min(max(int(size), 12), 200)

    doc = _open_pdf(data)
    try:
        total = len(doc)
        for number, page in enumerate(doc, start=1):
            view = page.rect  # PyMuPDF already reports the page as displayed (rotation applied)
            rot = page.rotation
            if watermark:
                width = fitz.get_text_length(watermark, fontname="helv", fontsize=size)
                # Shrink to fit across the diagonal so long text doesn't run off the page.
                limit = (view.width ** 2 + view.height ** 2) ** 0.5 * 0.8
                fs = size if width <= limit else size * limit / width
                width = min(width, limit)
                cx, cy = view.width / 2, view.height / 2
                start = _upright(page, fitz.Point(cx - width / 2, cy + fs / 3))
                pivot = _upright(page, fitz.Point(cx, cy))
                page.insert_text(start, watermark, fontsize=fs, fontname="helv", color=(0.45, 0.45, 0.45),
                                 fill_opacity=opacity, stroke_opacity=opacity, rotate=rot,
                                 morph=(pivot, fitz.Matrix(45)), overlay=True)
            if numbering:
                label = NUMBER_FORMATS[numbering].format(n=number, total=total)
                fs = 10
                width = fitz.get_text_length(label, fontname="helv", fontsize=fs)
                margin = 28
                x = {"center": (view.width - width) / 2, "right": view.width - margin - width,
                     "left": margin}[position.split()[1]]
                y = view.height - margin / 1.5 if position.startswith("Bottom") else margin
                page.insert_text(_upright(page, fitz.Point(x, y)), label, fontsize=fs, fontname="helv",
                                 color=(0.2, 0.2, 0.2), rotate=rot, overlay=True)
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
