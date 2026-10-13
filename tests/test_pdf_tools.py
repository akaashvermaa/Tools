import io
import os
import random
import sys

import pymupdf as fitz
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aura import pdf_tools  # noqa: E402


def _noise_jpeg(size=1600) -> bytes:
    rnd = random.Random(1)
    img = Image.frombytes("RGB", (size, size), bytes(rnd.randrange(256) for _ in range(size * size * 3)))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=95)
    return out.getvalue()


@pytest.fixture(scope="module")
def photo_pdf() -> bytes:
    """3 pages; page 1 carries a big photo drawn small (lots of wasted pixels)."""
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} text", fontsize=18)
    doc[0].insert_image(fitz.Rect(72, 100, 372, 400), stream=_noise_jpeg())
    out = doc.tobytes()
    doc.close()
    return out


def _texts(data: bytes):
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return [p.get_text() for p in doc]
    finally:
        doc.close()


def test_compress_shrinks_image_heavy_pdf(photo_pdf):
    small = pdf_tools.compress_pdf(photo_pdf, "Strong")
    assert len(small) < len(photo_pdf) * 0.5
    assert len(_texts(small)) == 3 and "Page 2 text" in _texts(small)[1]


def test_compress_never_grows(photo_pdf):
    again = pdf_tools.compress_pdf(pdf_tools.compress_pdf(photo_pdf, "Strong"), "Strong")
    assert len(again) <= len(pdf_tools.compress_pdf(photo_pdf, "Strong"))


def test_compress_rejects_unknown_level(photo_pdf):
    with pytest.raises(ValueError):
        pdf_tools.compress_pdf(photo_pdf, "Extreme")


def test_organize_reorders_rotates_and_deletes(photo_pdf):
    out = pdf_tools.organize_pages(photo_pdf, [{"page": 2, "rotate": 90}, {"page": 0}])
    doc = fitz.open(stream=out, filetype="pdf")
    assert len(doc) == 2
    assert doc[0].rotation == 90 and doc[1].rotation == 0
    assert "Page 3" in doc[0].get_text() and "Page 1" in doc[1].get_text()


def test_organize_rejects_empty_and_bad_pages(photo_pdf):
    with pytest.raises(ValueError):
        pdf_tools.organize_pages(photo_pdf, [])
    with pytest.raises(ValueError):
        pdf_tools.organize_pages(photo_pdf, [{"page": 9}])
    with pytest.raises(ValueError):
        pdf_tools.organize_pages(photo_pdf, [{"page": 0, "rotate": 45}])


def test_protect_then_unlock_round_trip(photo_pdf):
    locked = pdf_tools.protect_pdf(photo_pdf, "s3cret")
    doc = fitz.open(stream=locked, filetype="pdf")
    assert doc.needs_pass and doc.is_encrypted
    with pytest.raises(ValueError, match="Wrong password"):
        pdf_tools.unlock_pdf(locked, "nope")
    opened = pdf_tools.unlock_pdf(locked, "s3cret")
    assert not fitz.open(stream=opened, filetype="pdf").needs_pass
    assert "Page 1 text" in _texts(opened)[0]


def test_locked_pdf_is_refused_by_other_tools(photo_pdf):
    locked = pdf_tools.protect_pdf(photo_pdf, "x")
    with pytest.raises(ValueError, match="password"):
        pdf_tools.compress_pdf(locked, "Light")


def test_unlock_unprotected_pdf_says_so(photo_pdf):
    with pytest.raises(ValueError, match="isn't password-protected"):
        pdf_tools.unlock_pdf(photo_pdf, "")


def test_stamp_adds_watermark_and_page_numbers(photo_pdf):
    out = pdf_tools.stamp_pdf(photo_pdf, watermark="CONFIDENTIAL", numbering="Page 1 of N")
    texts = _texts(out)
    assert all("CONFIDENTIAL" in t for t in texts)
    assert "Page 2 of 3" in texts[1] and "Page 3 of 3" in texts[2]


def test_stamp_validates_input(photo_pdf):
    with pytest.raises(ValueError):
        pdf_tools.stamp_pdf(photo_pdf)
    with pytest.raises(ValueError, match="Latin"):
        pdf_tools.stamp_pdf(photo_pdf, watermark="नमस्ते")
    with pytest.raises(ValueError):
        pdf_tools.stamp_pdf(photo_pdf, numbering="Page 1", position="Middle")


def test_stamp_on_rotated_page_keeps_text_readable(photo_pdf):
    rotated = pdf_tools.organize_pages(photo_pdf, [{"page": 0, "rotate": 90}])
    out = pdf_tools.stamp_pdf(rotated, numbering="Page 1")
    doc = fitz.open(stream=out, filetype="pdf")
    # Upright text means PyMuPDF extracts the whole label as one line in reading order.
    assert "Page 1" in doc[0].get_text()
