"""
Image resize / compress / convert for the web app. Like pdf_tools, everything works on bytes.

Phone photos need three things the plain Pillow calls don't do: honour the EXIF
"rotate me" flag (otherwise portrait shots come out sideways), keep the colour
profile, and optionally drop the location and camera data before sharing.
"""

import io
import os
import threading
from typing import List, Optional, Tuple

from PIL import Image, ImageChops, ImageOps

from . import deps

# --- Optional HEIC/HEIF support (iPhone photos); the app works without it ---
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORT = True
except Exception:  # not installed, or no wheel for this platform
    HEIC_SUPPORT = False

try:
    import numpy as np
    import onnxruntime as ort
except (ImportError, OSError):  # optional CPU runtime or its native libraries are unavailable
    ort = None

BACKGROUND_SUPPORT = ort is not None
_background_session = None
_background_lock = threading.Lock()

INPUT_EXTS = ["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"] + (["heic", "heif"] if HEIC_SUPPORT else [])
LOSSY = ("jpg", "webp")
MIN_QUALITY = 40  # below this JPEG/WebP turn blocky; shrinking the picture looks better than going lower
MAX_SIDE = 12000
UPLOAD_MAX_PIXELS = 16_000_000

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


def _background_mask(img: Image.Image) -> Image.Image:
    global _background_session
    with _background_lock:
        if _background_session is None:
            model = deps.background_model()
            _background_session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    session = _background_session

    # U²-Net takes a 320 × 320 RGB tensor normalized with ImageNet statistics.
    pixels = np.asarray(img.resize((320, 320), Image.Resampling.LANCZOS), dtype=np.float32)
    pixels /= max(float(pixels.max()), 1.0)
    pixels -= np.array([0.485, 0.456, 0.406], dtype=np.float32)
    pixels /= np.array([0.229, 0.224, 0.225], dtype=np.float32)
    tensor = np.ascontiguousarray(pixels.transpose(2, 0, 1)[None])
    prediction = session.run(None, {session.get_inputs()[0].name: tensor})[0][0, 0]
    low, high = float(prediction.min()), float(prediction.max())
    if high > low:
        prediction = (prediction - low) / (high - low)
    mask = Image.fromarray((prediction.clip(0, 1) * 255).astype(np.uint8))
    return mask.resize(img.size, Image.Resampling.LANCZOS)


def remove_background(data: bytes, name: str) -> bytes:
    """Keep the foreground as a PNG, respecting the image's existing transparency."""
    if not BACKGROUND_SUPPORT:
        setup = "launch.bat" if os.name == "nt" else "python3 launch.py --update"
        raise ValueError(f"Background removal needs the optional ONNX Runtime. Run {setup} to try installing it.")
    if _ext(name) not in INPUT_EXTS:
        raise ValueError(f"{name}: unsupported file type.")
    img, icc, _ = open_image(data, name)
    mask = _background_mask(flatten_to_rgb(img))
    out = img.convert("RGBA")
    out.putalpha(ImageChops.multiply(out.getchannel("A"), mask))
    out.info.clear()
    return encode(out, "png", icc=icc)


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


def _quality_under_limit(img, fmt, low, high, max_bytes, icc=None, exif=None):
    best = None
    while low <= high:
        quality = (low + high) // 2
        trial = encode(img, fmt, quality, icc, exif)
        if len(trial) <= max_bytes:
            best = quality, trial
            low = quality + 1
        else:
            high = quality - 1
    return best


def fit_to_size(img: Image.Image, fmt: str, quality: int, max_bytes: int, icc, exif) -> Tuple[bytes, Optional[str]]:
    """Encode so the result is at most `max_bytes`: lower the quality first, then the dimensions."""
    data = encode(img, fmt, quality, icc, exif)
    if len(data) <= max_bytes:
        return data, None
    if fmt in LOSSY:
        best = _quality_under_limit(img, fmt, MIN_QUALITY, quality - 1, max_bytes, icc, exif)
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


def _clean_signature(img: Image.Image, size: Tuple[int, int], cutoff: int, transparent: bool) -> Image.Image:
    # Turn the darkness of the pen stroke into alpha, leaving light paper behind.
    gray = ImageOps.grayscale(flatten_to_rgb(img))
    ink = gray.point([max(0, min(255, (cutoff - value) * 5)) for value in range(256)])
    bounds = ink.getbbox()
    if not bounds:
        raise ValueError("No dark pen strokes found. Move the cleanup slider toward Keep lighter ink and try again.")
    ink = ink.crop(bounds)
    width, height = size
    margin = min(size) // 20
    scale = min((width - 2 * margin) / ink.width, (height - 2 * margin) / ink.height)
    ink = ink.resize((max(1, round(ink.width * scale)), max(1, round(ink.height * scale))), Image.LANCZOS)
    mask = Image.new("L", size)
    mask.paste(ink, ((width - ink.width) // 2, (height - ink.height) // 2))
    if transparent:
        out = Image.new("RGBA", size)
        out.putalpha(mask)
        return out
    return ImageOps.invert(mask)


def prepare_form_image(file: UploadedFile, kind: str, width: int, height: int, fmt: str, max_kb: int,
                       center_x: int = 50, center_y: int = 50, cutoff: int = 220,
                       transparent: bool = False) -> UploadedFile:
    """Prepare one upload without changing its requested dimensions or format to meet the cap."""
    name, data = file
    if kind not in ("photo", "signature") or fmt not in ("jpg", "png", "webp"):
        raise ValueError("Choose a photo or signature and a supported format.")
    if _ext(name) not in INPUT_EXTS:
        raise ValueError(f"{name}: unsupported file type.")
    if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE):
        raise ValueError(f"Enter a width and height between 1 and {MAX_SIDE} pixels.")
    if width * height > UPLOAD_MAX_PIXELS:
        raise ValueError("The requested dimensions exceed 16 megapixels.")
    if max_kb < 0:
        raise ValueError("The file-size limit can't be negative.")
    if not (0 <= center_x <= 100 and 0 <= center_y <= 100 and 100 <= cutoff <= 250):
        raise ValueError("Invalid crop position or cleanup setting.")

    img, _, _ = open_image(data, name)
    if kind == "signature":
        img = _clean_signature(img, (width, height), cutoff, transparent and fmt != "jpg")
    else:
        img = img.convert("RGBA" if has_alpha(img) else "RGB")
        img = ImageOps.fit(img, (width, height), Image.LANCZOS, centering=(center_x / 100, center_y / 100))
    img.info.clear()  # Remove camera and location metadata from form uploads.

    max_bytes = max_kb * 1000
    output_name = f"{_stem(name)}_upload.{fmt}"
    packed = encode(img, fmt, quality=92)
    if not max_bytes or len(packed) <= max_bytes:
        return output_name, packed
    if fmt in LOSSY:
        best = _quality_under_limit(img, fmt, 5, 91, max_bytes)
        if best:
            return output_name, best[1]
    else:
        for colors in (256, 128, 64, 32, 16):
            with io.BytesIO() as out:
                img.quantize(colors=colors, dither=Image.Dither.NONE).save(out, "PNG", optimize=True)
                if out.tell() <= max_bytes:
                    return output_name, out.getvalue()
    raise ValueError(f"Couldn't fit a {width} × {height} {fmt.upper()} under {max_kb} KB. "
                     "Check the form for a larger limit or another accepted format.")


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
