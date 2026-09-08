#!/usr/bin/env python3
"""Fail the build on emoji, italics and fake small-caps in user-facing text.

Rules
  1. The only non-alphanumeric symbols allowed are the ones declared in
     bot/ui/symbols.py:ALLOWED. Every emoji, pictograph, variation selector and
     skin-tone modifier is rejected.
  2. No <i>/<em> markup. Emphasis is <b>, values are <code>.
  3. No unicode small-caps or mathematical alphanumerics: they break screen
     readers and search, and cost roughly three tokens per character.

    python scripts/check_symbols.py [paths...]
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.ui.symbols import ALLOWED  # noqa: E402

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".ruff_cache"}
SELF = Path(__file__).resolve()

#: Categories that carry decoration rather than meaning.
BAD_CATEGORIES = {"So", "Sk", "Co", "Cs"}

BAD_RANGES = (
    (0x1D00, 0x1D7F, "unicode small-caps / phonetic"),
    (0xA730, 0xA739, "unicode small-caps"),
    (0x1D400, 0x1D7FF, "mathematical alphanumerics (fake bold/italic)"),
    (0xFE00, 0xFE0F, "variation selector"),
    (0x1F3FB, 0x1F3FF, "skin tone modifier"),
    (0x1F1E6, 0x1F1FF, "regional indicator"),
    (0x20E3, 0x20E3, "combining keycap"),
)

ITALIC = re.compile(r"</?(?:i|em)\s*>", re.IGNORECASE)


def offenders(text: str) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in ITALIC.finditer(line):
            found.append((lineno, match.group(0), "italic markup"))
        for char in line:
            if char.isascii() or char in ALLOWED:
                continue
            code = ord(char)
            reason = ""
            for lo, hi, label in BAD_RANGES:
                if lo <= code <= hi:
                    reason = label
                    break
            if not reason and unicodedata.category(char) in BAD_CATEGORIES:
                reason = "symbol outside the allowlist"
            if reason:
                name = unicodedata.name(char, "unnamed")
                found.append((lineno, f"U+{code:04X} {name}", reason))
    return found


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [ROOT / "bot"]
    files: list[Path] = []
    for target in targets:
        if target.is_file():
            files.append(target)
            continue
        for path in sorted(target.rglob("*.py")):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            files.append(path)

    problems = 0
    for path in files:
        if path.resolve() == SELF:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"{path}: cannot read ({exc})", file=sys.stderr)
            problems += 1
            continue
        for lineno, token, reason in offenders(text):
            rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
            print(f"{rel}:{lineno}: {reason}: {token}", file=sys.stderr)
            problems += 1

    if problems:
        print(f"\n{problems} violation(s). Allowed glyphs: {''.join(sorted(ALLOWED))}", file=sys.stderr)
        return 1
    print(f"checked {len(files)} file(s), clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
