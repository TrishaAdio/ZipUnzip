"""Colorama-backed logging: aligned columns, one colour per severity.

Layout
    12:04:31.882 │ INFO  │ bot.core.archive    │ unpacked 119 entries
Columns are fixed width so the message column stays readable while scanning.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from colorama import Fore, Style
from colorama import init as _colorama_init

RESET = Style.RESET_ALL
DIM = Style.DIM
BRIGHT = Style.BRIGHT

_LEVEL_STYLE: dict[int, str] = {
    logging.DEBUG: DIM + Fore.CYAN,
    logging.INFO: Fore.GREEN,
    logging.WARNING: BRIGHT + Fore.YELLOW,
    logging.ERROR: BRIGHT + Fore.RED,
    logging.CRITICAL: BRIGHT + Fore.MAGENTA,
}

_LEVEL_LABEL: dict[int, str] = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO ",
    logging.WARNING: "WARN ",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "FATAL",
}

_NAME_WIDTH = 22
_SEP = "│"

#: Loggers that are chatty and rarely interesting above WARNING.
_QUIET = ("aiogram.event", "aiohttp.access", "asyncio", "PIL")


class _Plain(logging.Formatter):
    """No-colour formatter used for log files."""

    default_msec_format = "%s.%03d"

    def format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        stamp = f"{stamp}.{int(record.msecs):03d}"
        label = _LEVEL_LABEL.get(record.levelno, record.levelname[:5].ljust(5))
        name = record.name if record.name != "root" else "bot"
        line = f"{stamp} {_SEP} {label} {_SEP} {name:<{_NAME_WIDTH}} {_SEP} {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            line += "\n" + self.formatStack(record.stack_info)
        return line


class ColorFormatter(logging.Formatter):
    def __init__(self, *, color: bool = True) -> None:
        super().__init__()
        self.color = color

    def _paint(self, text: str, style: str) -> str:
        if not self.color or not style:
            return text
        return f"{style}{text}{RESET}"

    def format(self, record: logging.LogRecord) -> str:
        style = _LEVEL_STYLE.get(record.levelno, "")
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        stamp = f"{stamp}.{int(record.msecs):03d}"

        name = record.name if record.name != "root" else "bot"
        if len(name) > _NAME_WIDTH:
            name = "…" + name[-(_NAME_WIDTH - 1) :]

        message = record.getMessage()
        if record.levelno >= logging.ERROR:
            message = self._paint(message, BRIGHT + Fore.RED)
        elif record.levelno >= logging.WARNING:
            message = self._paint(message, Fore.YELLOW)
        elif record.levelno <= logging.DEBUG:
            message = self._paint(message, DIM)

        sep = self._paint(_SEP, DIM)
        parts = [
            self._paint(stamp, DIM),
            sep,
            self._paint(_LEVEL_LABEL.get(record.levelno, record.levelname[:5].ljust(5)), style),
            sep,
            self._paint(f"{name:<{_NAME_WIDTH}}", Fore.CYAN),
            sep,
            message,
        ]
        line = " ".join(parts)

        if record.exc_info:
            trace = self.formatException(record.exc_info)
            line += "\n" + self._paint(trace, DIM + Fore.RED)
        if record.stack_info:
            line += "\n" + self._paint(self.formatStack(record.stack_info), DIM)
        return line


def _want_color(mode: str, stream) -> bool:
    if mode == "never" or os.getenv("NO_COLOR"):
        return False
    if mode == "always":
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


def install(level: str = "INFO", *, color: str = "auto", log_file: Path | None = None) -> None:
    _colorama_init(strip=False, convert=None, autoreset=False)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = sys.stderr
    console = logging.StreamHandler(stream)
    console.setFormatter(ColorFormatter(color=_want_color(color, stream)))
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(_Plain())
        root.addHandler(file_handler)

    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)


def banner(title: str, rows: list[tuple[str, str]], *, color: str = "auto") -> None:
    """Startup summary block. Written straight to stderr, not through logging."""
    stream = sys.stderr
    paint = (lambda t, s: f"{s}{t}{RESET}") if _want_color(color, stream) else (lambda t, s: t)

    width = max([len(title)] + [len(k) + len(v) + 3 for k, v in rows]) + 2
    top = "─" * width
    print(paint(top, DIM + Fore.CYAN), file=stream)
    print(paint(f" {title}", BRIGHT + Fore.CYAN), file=stream)
    print(paint(top, DIM + Fore.CYAN), file=stream)
    pad = max((len(k) for k, _ in rows), default=0)
    for key, value in rows:
        print(
            f" {paint(f'{key:<{pad}}', DIM)}  {paint(value, Fore.WHITE)}",
            file=stream,
        )
    print(paint(top, DIM + Fore.CYAN), file=stream, flush=True)
