"""
Image resize / compress / convert for the web app. Like pdf_tools, everything works on bytes.

Phone photos need three things the plain Pillow calls don't do: honour the EXIF
"rotate me" flag (otherwise portrait shots come out sideways), keep the colour
profile, and optionally drop the location and camera data before sharing.
"""

import io
import os
from typing import List, Optional, Tuple

from PIL import Image, ImageOps

# --- Optional HEIC/HEIF support (iPhone photos); the app works without it ---
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORT = True
except Exception:  # not installed, or no wheel for this platform
    HEIC_SUPPORT = False

INPUT_EXTS = ["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"] + (["heic", "heif"] if HEIC_SUPPORT else [])
LOSSY = ("jpg", "webp")
MIN_QUALITY = 40  # below this JPEG/WebP turn blocky; shrinking the picture looks better than going lower
MAX_SIDE = 12000

# name -> (mode, width, height). "fit" keeps the shape and never enlarges; "exact" crops to fill.
PRESETS = {
    "Passport photo (35 × 45 mm)": ("exact", 413, 531),
    "Square, Instagram (1080 × 1080)": ("exact", 1080, 1080),
    "Full HD (1920 × 1080)": ("fit", 1920, 1080),
    "Email / web (max 1600 px)": ("fit", 1600, 1600),
    "Thumbnail (max 400 px)": ("fit", 400, 400),
}

UploadedFile = Tuple[str, bytes]


def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower().lstrip(".")


def _stem(name: str) -> str:
    return os.path.splitext(os.path.basename(name))[0] or "image"


# ── Opening and saving ──────────────────────────────────────────────────────

def flatten_to_rgb(img: Image.Image) -> Image.Image:
    """Drop transparency onto white so the image can be saved as JPEG."""
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def has_alpha(img: Image.Image) -> bool:
    return "A" in img.getbands() or (img.mode == "P" and "transparency" in img.info)


def open_image(data: bytes, name: str = "image") -> Tuple[Image.Image, Optional[bytes], Image.Exif]:
    """Open, apply the EXIF rotation, and return (image, colour profile, exif)."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        hint = " (HEIC photos need the pillow-heif package)" if _ext(name) in ("heic", "heif") and not HEIC_SUPPORT else ""
        raise ValueError(f"Could not read {name}: unsupported or damaged image{hint}.")
    icc = img.info.get("icc_profile")
    fixed = ImageOps.exif_transpose(img)  # also clears the orientation flag, so it isn't applied twice
    return fixed, icc, fixed.getexif()


def encode(img: Image.Image, fmt: str, quality: int = 90, icc: Optional[bytes] = None,
           exif: Optional[Image.Exif] = None) -> bytes:
    """Encode as jpg / png / webp. `exif=None` writes no camera or location data."""
    extra = {}
    if icc:
        extra["icc_profile"] = icc
    if exif is not None and len(exif):
        extra["exif"] = exif
    buf = io.BytesIO()
    if fmt == "jpg":
        flatten_to_rgb(img).save(buf, "JPEG", quality=quality, optimize=True, **extra)
    elif fmt == "webp":
        img.convert("RGBA" if has_alpha(img) else "RGB").save(buf, "WEBP", quality=quality, method=4, **extra)
    elif fmt == "png":
        img.convert("RGBA" if has_alpha(img) else "RGB").save(buf, "PNG", optimize=True, **extra)
    else:
        raise ValueError("Unknown output format.")
    return buf.getvalue()


# ── Format conversion (the Convert format tab) ──────────────────────────────

def convert_image(data: bytes, out_fmt: str, quality: int = 92, name: str = "image") -> bytes:
    img, icc, exif = open_image(data, name)
    if out_fmt == "ico":
        return _to_ico(img)
    return encode(img, out_fmt, quality, icc, exif)


def _to_ico(img: Image.Image) -> bytes:
    """A favicon file with the usual sizes up to the image's own size."""
    side = max(img.size)
    if side < 16:
        raise ValueError("That image is too small for an icon (at least 16 px).")
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    rgba = img.convert("RGBA")
    square.paste(rgba, ((side - rgba.width) // 2, (side - rgba.height) // 2))
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= side]
    buf = io.BytesIO()
    square.save(buf, "ICO", sizes=sizes)
    return buf.getvalue()


# ── Resize & compress (its own tab) ─────────────────────────────────────────

def resize(img: Image.Image, mode: str, percent: int = 100, width: int = 0, height: int = 0) -> Image.Image:
    if mode == "keep":
        return img
    if mode == "percent":
        k = min(max(percent, 1), 100) / 100
        return img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
    if not (0 <= width <= MAX_SIDE and 0 <= height <= MAX_SIDE):
        raise ValueError(f"Width and height must be between 1 and {MAX_SIDE} pixels.")
    if mode == "exact":
        if width < 1 or height < 1:
            raise ValueError("Enter both a width and a height.")
        return ImageOps.fit(img, (width, height), Image.LANCZOS)
    if mode == "fit":
        if width < 1 and height < 1:
            raise ValueError("Enter a width, a height, or both.")
        k = min(width / img.width if width else 1, height / img.height if height else 1, 1)  # never enlarge
        if k >= 1:
            return img
        return img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
    raise ValueError("Unknown resize mode.")


def fit_to_size(img: Image.Image, fmt: str, quality: int, max_bytes: int, icc, exif) -> Tuple[bytes, Optional[str]]:
    """Encode so the result is at most `max_bytes`: lower the quality first, then the dimensions."""
    data = encode(img, fmt, quality, icc, exif)
    if len(data) <= max_bytes:
        return data, None
    note = None
    if fmt in LOSSY:
        lo, hi, best = MIN_QUALITY, quality - 1, None
        while lo <= hi:  # highest quality that still fits
            mid = (lo + hi) // 2
            trial = encode(img, fmt, mid, icc, exif)
            if len(trial) <= max_bytes:
                best, lo = (mid, trial), mid + 1
            else:
                hi = mid - 1
        if best:
            return best[1], f"quality lowered to {best[0]} to fit the limit"
    work, q = img, (min(quality, 70) if fmt in LOSSY else quality)
    for _ in range(10):
        data = encode(work, fmt, q, icc, exif)
        if len(data) <= max_bytes:
            return data, f"also reduced to {work.width} × {work.height} px to fit the limit"
        k = min(0.9, (max_bytes / len(data)) ** 0.5 * 0.95)
        if min(work.width, work.height) * k < 48:
            break
        work = work.resize((max(1, round(work.width * k)), max(1, round(work.height * k))), Image.LANCZOS)
    raise ValueError(f"Couldn't get this picture under {max_bytes // 1024} KB without making it tiny. Try a larger limit.")


def _out_format(fmt: str, source_ext: str, img: Image.Image) -> str:
    if fmt != "same":
        return fmt
    if source_ext in ("png", "webp"):
        return source_ext
    if source_ext in ("jpg", "jpeg"):
        return "jpg"
    return "png" if has_alpha(img) else "jpg"  # HEIC, BMP, TIFF, GIF


def resize_images(files: List[UploadedFile], mode: str = "keep", percent: int = 100, width: int = 0, height: int = 0,
                  fmt: str = "same", quality: int = 85, max_kb: int = 0, strip: bool = True
                  ) -> Tuple[List[UploadedFile], List[str]]:
    """Resize and/or recompress each file. Returns (outputs, notes about what was adjusted)."""
    if fmt not in ("same", "jpg", "png", "webp"):
        raise ValueError("Unknown output format.")
    quality = min(max(quality, 10), 100)
    if max_kb < 0:
        raise ValueError("The size limit can't be negative.")
    outputs, notes = [], []
    for name, data in files:
        if _ext(name) not in INPUT_EXTS:
            raise ValueError(f"{name}: unsupported file type.")
        img, icc, exif = open_image(data, name)
        img = resize(img, mode, percent, width, height)
        out_fmt = _out_format(fmt, _ext(name), img)
        keep_exif = None if strip else exif
        if max_kb:
            out, note = fit_to_size(img, out_fmt, quality, max_kb * 1024, icc, keep_exif)
            if note:
                notes.append(f"{name}: {note}")
        else:
            out = encode(img, out_fmt, quality, icc, keep_exif)
        suffix = "_resized" if mode != "keep" else "_compressed"
        outputs.append((f"{_stem(name)}{suffix}.{out_fmt}", out))
    return outputs, notes
