"""Every user-facing string lives here. HTML parse mode, no italics, no emoji."""

from __future__ import annotations

from collections import Counter
from html import escape

from ..core.classify import Kind
from ..core.humanize import duration, ellipsis, rate, size
from . import symbols as sym

_KIND_LABEL = {
    Kind.PHOTO: ("photo", "photos"),
    Kind.VIDEO: ("video", "videos"),
    Kind.AUDIO: ("audio", "audio"),
    Kind.ANIMATION: ("animation", "animations"),
    Kind.DOCUMENT: ("document", "documents"),
}


def esc(text: str) -> str:
    return escape(str(text), quote=False)


def code(text: str) -> str:
    return f"<code>{esc(text)}</code>"


def start(*, max_mb: int, local_api: bool) -> str:
    lines = [
        f"{sym.ACCENT} <b>Unzipper</b>",
        "Send a zip archive. Everything inside comes back in its own format — "
        "images as photos, clips as videos, tracks as audio.",
        "",
        f"{sym.BULLET} archives up to <b>{max_mb} MB</b>"
        + ("" if local_api else f" {sym.DOT} <b>20 MB</b> on the public API"),
        f"{sym.BULLET} {code('/pass <secret>')} for encrypted archives",
        f"{sym.BULLET} {code('/raw')} to receive files as documents",
    ]
    return "\n".join(lines)


def steps_block(steps: list[tuple[str, str, str]]) -> str:
    """steps: (state, label, detail) where state is done|active|pending|fail."""
    glyph = {
        "done": sym.DONE,
        "active": sym.ACTIVE,
        "pending": sym.PENDING,
        "fail": sym.FAIL,
    }
    rows = []
    for state, label, detail in steps:
        row = f"{glyph[state]} {label:<7}"
        if detail:
            row = f"{row} {detail}"
        rows.append(row.rstrip())
    return "\n".join(rows)


def card(
    *,
    title: str,
    header_bits: list[str],
    steps: list[tuple[str, str, str]],
    note: str = "",
) -> str:
    out = [f"{sym.ACCENT} <b>{esc(ellipsis(title, 56))}</b>"]
    if header_bits:
        out.append(f" {sym.DOT} ".join(esc(bit) for bit in header_bits))
    out.append("")
    out.append(steps_block(steps))
    if note:
        out.append("")
        out.append(code(ellipsis(note, 44)))
    return "\n".join(out)


def summary(
    *,
    title: str,
    counts: Counter,
    total_bytes: int,
    elapsed: float,
    failed: int = 0,
    skipped: int = 0,
) -> str:
    sent = sum(counts.values())
    head = [f"{sent} file" if sent == 1 else f"{sent} files", size(total_bytes), duration(elapsed)]
    speed = rate(total_bytes, elapsed)
    if speed:
        head.append(speed)

    out = [
        f"{sym.ACCENT} <b>{esc(ellipsis(title, 56))}</b>",
        f" {sym.DOT} ".join(head),
        "",
    ]
    order = [Kind.PHOTO, Kind.VIDEO, Kind.ANIMATION, Kind.AUDIO, Kind.DOCUMENT]
    for kind in order:
        count = counts.get(kind, 0)
        if not count:
            continue
        one, many = _KIND_LABEL[kind]
        out.append(f"{sym.BULLET} {count} {one if count == 1 else many}")
    if skipped:
        out.append(f"{sym.PENDING} {skipped} skipped")
    if failed:
        out.append(f"{sym.FAIL} {failed} failed")
    return "\n".join(out)


def failure(title: str, reason: str) -> str:
    return f"{sym.FAIL} <b>{esc(ellipsis(title, 56))}</b>\n{code(reason)}"


def plain_failure(reason: str) -> str:
    return f"{sym.FAIL} {code(reason)}"


def password_saved() -> str:
    return f"{sym.DONE} password set"


def password_cleared() -> str:
    return f"{sym.DONE} password cleared"


def raw_mode(enabled: bool) -> str:
    return f"{sym.DONE} document mode {'on' if enabled else 'off'}"


def busy() -> str:
    return f"{sym.PENDING} one archive at a time"
