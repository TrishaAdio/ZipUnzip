"""Make images satisfy Telegram's photo constraints, or admit they can't.

Telegram rejects a photo when it is over 10 MB, when width + height exceeds
10000, or when the side ratio is worse than 20:1. Anything we cannot bend into
those limits is sent as a document instead, so nothing is silently dropped.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .classify import NATIVE_PHOTO_EXT

log = logging.getLogger(__name__)

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_DIM_SUM = 10000
MAX_SIDE_RATIO = 20
MAX_SIDE = 4096

try:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = 300_000_000  # keep the decompression-bomb guard, raise the bar
    _PIL = True
except Exception:  # pragma: no cover
    _PIL = False

try:  # HEIC/HEIF support is a separate wheel
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    _HEIF = True
except Exception:  # pragma: no cover
    _HEIF = False


def probe(path: Path) -> tuple[int, int] | None:
    if not _PIL:
        return None
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def _acceptable(path: Path, size: tuple[int, int] | None) -> bool:
    if path.suffix.lower() not in NATIVE_PHOTO_EXT:
        return False
    if path.stat().st_size > MAX_PHOTO_BYTES:
        return False
    if size is None:
        return True  # no Pillow: let Telegram be the judge
    w, h = size
    if w + h > MAX_DIM_SUM:
        return False
    if min(w, h) == 0 or max(w, h) / min(w, h) > MAX_SIDE_RATIO:
        return False
    return True


def normalize(path: Path, scratch: Path, *, enabled: bool = True) -> Path | None:
    """Return a path safe to send as a photo, or None to fall back to a document.

    The returned path may be a freshly encoded JPEG inside `scratch`; the
    original file is never modified.
    """
    size = probe(path)
    if _acceptable(path, size):
        return path
    if not enabled or not _PIL:
        return None

    ext = path.suffix.lower()
    if ext in (".heic", ".heif") and not _HEIF:
        return None

    if size is not None:
        w, h = size
        if min(w, h) and max(w, h) / min(w, h) > MAX_SIDE_RATIO:
            return None  # a panorama strip is a document, not a photo

    scratch.mkdir(parents=True, exist_ok=True)
    out = scratch / (path.stem[:60] + ".jpg")
    counter = 1
    while out.exists():
        out = scratch / f"{path.stem[:60]}-{counter}.jpg"
        counter += 1

    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                flat = Image.new("RGB", im.size, (255, 255, 255))
                flat.paste(im, mask=im.split()[-1])
                im = flat
            elif im.mode != "RGB":
                im = im.convert("RGB")

            im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            for quality in (88, 78, 68, 55):
                im.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
                if out.stat().st_size <= MAX_PHOTO_BYTES:
                    break
            else:
                im.thumbnail((2048, 2048), Image.LANCZOS)
                im.save(out, "JPEG", quality=70, optimize=True)
    except Exception as exc:
        log.warning("cannot normalize %s: %s", path.name, exc)
        out.unlink(missing_ok=True)
        return None

    if out.stat().st_size > MAX_PHOTO_BYTES:
        out.unlink(missing_ok=True)
        return None
    log.info("normalized %s to %s", path.name, out.name)
    return out
