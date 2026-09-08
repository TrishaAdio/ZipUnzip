from __future__ import annotations

import re

_UNITS = ("B", "KB", "MB", "GB", "TB")


def size(num: int | float) -> str:
    value = float(num)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def rate(done_bytes: int, elapsed: float) -> str:
    if elapsed <= 0.05 or done_bytes <= 0:
        return ""
    return f"{size(done_bytes / elapsed)}/s"


_NUM = re.compile(r"(\d+)")


def natural_key(text: str) -> tuple:
    """Sort IMG_2.jpg before IMG_10.jpg."""
    return tuple(
        int(part) if part.isdigit() else part.lower() for part in _NUM.split(text) if part != ""
    )


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def ellipsis(text: str, limit: int = 48) -> str:
    if len(text) <= limit:
        return text
    head = limit - 12
    return text[:head] + "…" + text[-11:]
