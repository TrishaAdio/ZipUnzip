"""The only glyphs allowed in user-facing text.

One meaning per glyph. No emoji, no italics, no unicode small-caps — enforced by
scripts/check_symbols.py, which fails the build on violations.
"""

from __future__ import annotations

# ── state ────────────────────────────────────────────────────────────────────
DONE = "▪"      # step finished
ACTIVE = "◈"    # step running
PENDING = "∙"   # step not started
FAIL = "✕"      # step failed / job aborted
ACCENT = "⟡"    # single header accent

# ── structure ────────────────────────────────────────────────────────────────
BULLET = "▸"    # list item, and the moving head of a progress bar
PIPE = "│"      # vertical rule / bar edge
RULE = "─"      # horizontal rule
DOT = "·"       # inline field separator

#: Everything above, for the linter and for tests.
ALLOWED = frozenset({DONE, ACTIVE, PENDING, FAIL, ACCENT, BULLET, PIPE, RULE, DOT})


def bar(fraction: float, width: int = 10) -> str:
    """Determinate bar: │▪▪▪▪▸∙∙∙∙│"""
    fraction = min(1.0, max(0.0, fraction))
    filled = int(round(fraction * width))
    if filled >= width:
        return PIPE + DONE * width + PIPE
    body = DONE * max(0, filled - 1)
    head = BULLET if filled else ""
    rest = PENDING * (width - len(body) - len(head))
    return PIPE + body + head + rest + PIPE


def sweep(frame: int, width: int = 10) -> str:
    """Indeterminate bar: the head bounces across an empty track."""
    span = max(1, (width - 1) * 2)
    pos = frame % span
    if pos >= width:
        pos = span - pos
    return PIPE + PENDING * pos + BULLET + PENDING * (width - pos - 1) + PIPE
