import io
import os
import random
import sys

import pymupdf as fitz
import pytest
from PIL import Image, ImageChops

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aura import image_tools, pdf_tools  # noqa: E402


def photo(size=(1200, 900), seed=3) -> Image.Image:
    """Smooth colour blobs plus fine noise: compresses like a real photo."""
    rnd = random.Random(seed)
    small = Image.frombytes("RGB", (30, 22), bytes(rnd.randrange(256) for _ in range(30 * 22 * 3)))
    base = small.resize(size, Image.BICUBIC)
    noise = Image.effect_noise(size, 18).convert("RGB")
    return Image.blend(base, noise, 0.15)


def as_bytes(img: Image.Image, fmt="JPEG", **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def phone_jpeg(orientation=6) -> bytes:
    """A landscape pixel array tagged 'rotate 90° to view', with camera + GPS info like a phone shot."""
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "AcmePhone"
    exif[0x0110] = "X1"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[3] = "N", "E"
    return as_bytes(photo((800, 400)), "JPEG", quality=90, exif=exif)


def opened(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


# ── opening ─────────────────────────────────────────────────────────────────

def test_open_applies_exif_rotation():
    img, _, exif = image_tools.open_image(phone_jpeg(6))
    assert img.size == (400, 800)           # rotated to portrait
    assert exif.get(0x0112, 1) == 1          # flag cleared so viewers don't rotate it again


def test_unreadable_image_gives_a_clear_error():
    with pytest.raises(ValueError, match="Could not read broken.jpg"):
        image_tools.open_image(b"not an image", "broken.jpg")


# ── resize ──────────────────────────────────────────────────────────────────

def run(data, name="p.jpg", **kw):
    outs, notes = image_tools.resize_images([(name, data)], **kw)
    return outs[0], notes


def test_percent_resize():
    (name, out), _ = run(as_bytes(photo()), mode="percent", percent=50)
    assert opened(out).size == (600, 450) and name == "p_resized.jpg"


def test_fit_never_enlarges_and_keeps_shape():
    big, _ = run(as_bytes(photo()), mode="fit", width=600, height=600)
    assert opened(big[1]).size == (600, 450)
    small, _ = run(as_bytes(photo((300, 200))), mode="fit", width=600, height=600)
    assert opened(small[1]).size == (300, 200)
    only_h, _ = run(as_bytes(photo()), mode="fit", width=0, height=450)
    assert opened(only_h[1]).size == (600, 450)


def test_exact_crops_to_fill():
    (_, out), _ = run(as_bytes(photo()), mode="exact", width=413, height=531)  # passport photo
    assert opened(out).size == (413, 531)


def test_resize_validation():
    with pytest.raises(ValueError):
        run(as_bytes(photo()), mode="exact", width=0, height=500)
    with pytest.raises(ValueError):
        run(as_bytes(photo()), mode="fit", width=0, height=0)
    with pytest.raises(ValueError):
        run(as_bytes(photo()), mode="fit", width=99999, height=10)
    with pytest.raises(ValueError, match="unsupported"):
        run(b"x", name="notes.txt")


# ── size limit ──────────────────────────────────────────────────────────────

def test_size_limit_met_by_lowering_quality():
    data = as_bytes(photo(), "JPEG", quality=95)
    limit_kb = len(data) // 1024 // 2
    (_, out), notes = run(data, max_kb=limit_kb, quality=95)
    assert len(out) <= limit_kb * 1024
    assert opened(out).size == (1200, 900)          # dimensions untouched
    assert any("quality lowered" in n for n in notes)


def test_size_limit_falls_back_to_shrinking():
    rnd = random.Random(1)
    noisy = Image.frombytes("RGB", (900, 900), bytes(rnd.randrange(256) for _ in range(900 * 900 * 3)))
    (_, out), notes = run(as_bytes(noisy, "JPEG", quality=95), max_kb=60)
    assert len(out) <= 60 * 1024
    assert opened(out).width < 900 and any("reduced to" in n for n in notes)


def test_size_limit_works_for_png_by_shrinking():
    (_, out), notes = run(as_bytes(photo(), "PNG"), name="p.png", max_kb=150)
    assert len(out) <= 150 * 1024 and opened(out).format == "PNG" and notes


def test_impossible_limit_is_reported():
    with pytest.raises(ValueError, match="under 1 KB"):
        run(as_bytes(photo()), max_kb=1)


# ── privacy / metadata ──────────────────────────────────────────────────────

def test_strip_removes_camera_and_gps():
    (_, out), _ = run(phone_jpeg(), strip=True)
    exif = opened(out).getexif()
    assert not exif.get(0x010F) and not exif.get(0x0110) and not exif.get_ifd(0x8825)
    assert opened(out).size == (400, 800)           # still upright


def test_keeping_metadata_keeps_camera_but_not_the_rotation_flag():
    (_, out), _ = run(phone_jpeg(), strip=False)
    exif = opened(out).getexif()
    assert exif.get(0x010F) == "AcmePhone" and exif.get(0x0112, 1) == 1


# ── formats ─────────────────────────────────────────────────────────────────

def test_output_formats_and_same_as_original():
    png = as_bytes(photo((200, 100)), "PNG")
    assert run(png, name="a.png")[0][0] == "a_compressed.png"
    assert opened(run(png, name="a.png", fmt="webp")[0][1]).format == "WEBP"
    assert opened(run(png, name="a.png", fmt="jpg")[0][1]).format == "JPEG"


def test_transparent_png_becomes_white_not_black_as_jpg():
    img = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    (_, out), _ = run(as_bytes(img, "PNG"), name="t.png", fmt="jpg")
    assert opened(out).getpixel((25, 25)) == (255, 255, 255)


def test_transparent_webp_keeps_alpha():
    img = Image.new("RGBA", (50, 50), (255, 0, 0, 0))
    (_, out), _ = run(as_bytes(img, "PNG"), name="t.png", fmt="webp")
    assert opened(out).mode == "RGBA" and opened(out).getpixel((25, 25))[3] == 0


# ── Convert format tab ──────────────────────────────────────────────────────

def test_convert_files_any_to_jpg_and_webp():
    png = as_bytes(photo((120, 80)), "PNG")
    outs = pdf_tools.convert_files([("a.png", png)], "any2jpg", 90, 150, False)
    assert outs[0][0] == "a.jpg" and opened(outs[0][1]).format == "JPEG"
    outs = pdf_tools.convert_files([("a.png", png)], "any2webp", 80, 150, False)
    assert outs[0][0] == "a.webp" and opened(outs[0][1]).format == "WEBP"


def test_convert_wrong_input_type_is_refused():
    jpg = as_bytes(photo((60, 40)))
    with pytest.raises(ValueError, match="doesn't match"):
        pdf_tools.convert_files([("a.jpg", jpg)], "any2jpg", 90, 150, False)


def test_ico_has_standard_sizes():
    out = image_tools.convert_image(as_bytes(photo((300, 200)), "PNG"), "ico")
    ico = Image.open(io.BytesIO(out))
    assert ico.format == "ICO" and {(16, 16), (32, 32), (48, 48), (64, 64), (128, 128)} <= set(ico.info["sizes"])
    with pytest.raises(ValueError, match="too small"):
        image_tools.convert_image(as_bytes(Image.new("RGB", (8, 8)), "PNG"), "ico")


@pytest.mark.skipif(not image_tools.HEIC_SUPPORT, reason="pillow-heif not installed")
def test_heic_round_trip_and_routes():
    import pillow_heif

    buf = io.BytesIO()
    pillow_heif.from_pillow(photo((320, 240))).save(buf, format="HEIF", quality=80)
    heic = buf.getvalue()
    assert "heic" in pdf_tools.available_routes()["any2jpg"][1]
    (_, out), _ = run(heic, name="IMG_1.heic", fmt="jpg")
    assert opened(out).format == "JPEG" and opened(out).size == (320, 240)
    pdf = pdf_tools.files_to_pdf([("IMG_1.heic", heic)], fit_a4=True, compress=False)
    assert len(fitz.open(stream=pdf, filetype="pdf")) == 1


# ── Create PDF from phone photos ────────────────────────────────────────────

def test_create_pdf_keeps_phone_photo_upright():
    pdf = pdf_tools.image_to_pdf_bytes(phone_jpeg(6), fit_a4=False, compress=False, name="p.jpg")
    page = fitz.open(stream=pdf, filetype="pdf")[0]
    assert page.rect.height > page.rect.width        # portrait, not the sideways raw pixels


def test_create_pdf_embeds_plain_jpeg_untouched():
    data = as_bytes(photo((200, 100)))
    pdf = pdf_tools.image_to_pdf_bytes(data, fit_a4=False, compress=False)
    doc = fitz.open(stream=pdf, filetype="pdf")
    assert doc.extract_image(doc[0].get_images()[0][0])["image"] == data
