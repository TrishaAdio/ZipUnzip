"""Map a file inside the archive onto the Telegram media type it should be sent as."""

from __future__ import annotations

from enum import Enum
from pathlib import Path


class Kind(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    ANIMATION = "animation"
    DOCUMENT = "document"


PHOTO_EXT = {
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
}
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".3gp", ".mpg", ".mpeg", ".ts", ".flv", ".wmv"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".alac"}
ANIMATION_EXT = {".gif"}

#: Extensions Telegram renders natively as a photo without re-encoding.
NATIVE_PHOTO_EXT = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp"}

#: Magic numbers, checked when the extension is missing or lies.
_MAGIC: tuple[tuple[bytes, Kind, str], ...] = (
    (b"\xff\xd8\xff", Kind.PHOTO, ".jpg"),
    (b"\x89PNG\r\n\x1a\n", Kind.PHOTO, ".png"),
    (b"BM", Kind.PHOTO, ".bmp"),
    (b"GIF87a", Kind.ANIMATION, ".gif"),
    (b"GIF89a", Kind.ANIMATION, ".gif"),
    (b"ID3", Kind.AUDIO, ".mp3"),
    (b"fLaC", Kind.AUDIO, ".flac"),
    (b"OggS", Kind.AUDIO, ".ogg"),
    (b"\x1a\x45\xdf\xa3", Kind.VIDEO, ".mkv"),
    (b"RIFF", Kind.DOCUMENT, ""),  # resolved below (WEBP / WAVE / AVI)
)


def by_extension(name: str) -> Kind:
    ext = Path(name).suffix.lower()
    if ext in ANIMATION_EXT:
        return Kind.ANIMATION
    if ext in PHOTO_EXT:
        return Kind.PHOTO
    if ext in VIDEO_EXT:
        return Kind.VIDEO
    if ext in AUDIO_EXT:
        return Kind.AUDIO
    return Kind.DOCUMENT


def by_magic(head: bytes) -> tuple[Kind, str] | None:
    if head[:4] == b"RIFF" and len(head) >= 12:
        tag = head[8:12]
        if tag == b"WEBP":
            return Kind.PHOTO, ".webp"
        if tag == b"WAVE":
            return Kind.AUDIO, ".wav"
        if tag == b"AVI ":
            return Kind.VIDEO, ".avi"
        return None
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"heim"):
            return Kind.PHOTO, ".heic"
        if brand == b"avif":
            return Kind.PHOTO, ".avif"
        return Kind.VIDEO, ".mp4"
    for prefix, kind, ext in _MAGIC:
        if prefix == b"RIFF":
            continue
        if head.startswith(prefix):
            return kind, ext
    return None


def classify(name: str, head: bytes = b"") -> Kind:
    """Extension first; magic only decides when the extension has nothing to say."""
    kind = by_extension(name)
    if kind is not Kind.DOCUMENT:
        return kind
    if head:
        guess = by_magic(head)
        if guess:
            return guess[0]
    return Kind.DOCUMENT


#: Media types Telegram accepts inside one media group, keyed by group flavour.
_ALBUM_FLAVOUR = {
    Kind.PHOTO: "visual",
    Kind.VIDEO: "visual",
    Kind.AUDIO: "audio",
    Kind.DOCUMENT: "document",
}


def album_flavour(kind: Kind) -> str | None:
    """None means the item cannot travel in an album (animations)."""
    return _ALBUM_FLAVOUR.get(kind)
