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

IMAGE_EXTS = {"png", "jpg", "jpeg"}

# (input extensions, output format) for each File Converter route
CONVERT_ROUTES = {
    "img2pdf":  ("PNG/JPG → PDF", ["png", "jpg", "jpeg"], "pdf"),
    "png2jpg":  ("PNG → JPG", ["png"], "jpg"),
    "jpg2png":  ("JPG → PNG", ["jpg", "jpeg"], "png"),
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

def _flatten_to_rgb(img: Image.Image) -> Image.Image:
    """Drop alpha onto a white background so the image can be saved as JPEG."""
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def image_to_pdf_bytes(data: bytes, fit_a4: bool, compress: bool) -> bytes:
    """
    Convert an image to a one-page PDF.
    fit_a4 centers it on an A4 page; compress re-encodes it as JPEG (lossy).
    """
    img = Image.open(io.BytesIO(data))
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)

    if compress or has_alpha:
        flat = _flatten_to_rgb(img)
        buf = io.BytesIO()
        if compress:
            flat.save(buf, format="JPEG", quality=75)
        else:
            flat.save(buf, format="PNG")
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
        dpi = img.info.get("dpi", (72, 72))
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


def _image_to_image(data: bytes, out_fmt: str, quality: int) -> bytes:
    img = Image.open(io.BytesIO(data))
    buf = io.BytesIO()
    if out_fmt == "jpg":
        _flatten_to_rgb(img).save(buf, format="JPEG", quality=quality)
    else:
        img = img.convert("RGBA") if "A" in img.getbands() else img.convert("RGB")
        img.save(buf, format="PNG")
    return buf.getvalue()


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
            _append_pdf(writer, image_to_pdf_bytes(data, fit_a4, compress))
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
            pdf = image_to_pdf_bytes(data, fit_a4=True, compress=quality < 95) if ext in IMAGE_EXTS \
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
            outputs.append((f"{base}.{out_fmt}", _image_to_image(data, out_fmt, quality)))

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


def reorder_pages(data: bytes, order: List[int]) -> bytes:
    reader = _reader(data)
    total = len(reader.pages)
    if not order or any(p < 0 or p >= total for p in order):
        raise ValueError("Invalid page order.")
    writer = PdfWriter()
    for p in order:
        writer.add_page(reader.pages[p])
    return _write(writer)
