"""A single message that animates while the job runs.

The worker never awaits an edit. It mutates the card's counters; a background
task samples them every `interval` seconds and edits the message only when the
rendered text actually changed. Flood waits are absorbed by backing off, and a
message that has been deleted stops the animation instead of looping on errors.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import ReplyParameters

from ..core.humanize import size
from . import render
from . import symbols as sym

log = logging.getLogger(__name__)

BAR_WIDTH = 10


class Step:
    __slots__ = ("key", "label", "state", "done", "total", "unit", "started", "detail")

    def __init__(self, key: str, label: str) -> None:
        self.key = key
        self.label = label
        self.state = "pending"
        self.done = 0
        self.total = 0
        self.unit = "count"  # count | bytes | none
        self.started = 0.0
        self.detail = ""


class Card:
    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        message_id: int,
        *,
        title: str,
        steps: list[tuple[str, str]],
        interval: float = 1.8,
        header_bits: list[str] | None = None,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._message_id = message_id
        self._interval = max(0.7, interval)
        self.title = title
        self.header_bits = header_bits or []
        self._steps = [Step(key, label) for key, label in steps]
        self._index: dict[str, Step] = {s.key: s for s in self._steps}
        self._note = ""
        self._frame = 0
        self._last_text = ""
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._alive = True
        self.started = time.monotonic()

    # ── construction ─────────────────────────────────────────────────────────

    @classmethod
    async def open(
        cls,
        bot: Bot,
        chat_id: int,
        *,
        title: str,
        steps: list[tuple[str, str]],
        interval: float = 1.8,
        header_bits: list[str] | None = None,
        reply_to: int | None = None,
    ) -> Card:
        first_frame = render.card(
            title=title,
            header_bits=header_bits or [],
            steps=[("pending", label, "") for _, label in steps],
        )
        message = await bot.send_message(
            chat_id,
            first_frame,
            reply_parameters=(
                ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
                if reply_to
                else None
            ),
            disable_notification=True,
        )
        card = cls(
            bot,
            chat_id,
            message.message_id,
            title=title,
            steps=steps,
            interval=interval,
            header_bits=header_bits,
        )
        card._last_text = first_frame
        return card

    # ── state, all sync and thread-safe (plain attribute writes) ─────────────

    def step(self, key: str, *, total: int = 0, unit: str = "count") -> None:
        for candidate in self._steps:
            if candidate.key == key:
                break
            if candidate.state in ("active", "pending"):
                candidate.state = "done"
                candidate.detail = ""
        current = self._index[key]
        current.state = "active"
        current.total = total
        current.done = 0
        current.unit = unit
        current.started = time.monotonic()
        self._note = ""

    def advance(self, key: str, done: int, total: int | None = None, note: str = "") -> None:
        current = self._index.get(key)
        if current is None:
            return
        current.done = done
        if total is not None:
            current.total = total
        if note:
            self._note = note

    def note(self, text: str) -> None:
        self._note = text

    def detail(self, key: str, text: str) -> None:
        current = self._index.get(key)
        if current is not None:
            current.detail = text

    def complete(self, key: str) -> None:
        current = self._index.get(key)
        if current is not None:
            current.state = "done"
            current.detail = ""

    # ── rendering ────────────────────────────────────────────────────────────

    def _render_step(self, step: Step) -> tuple[str, str, str]:
        if step.state in ("pending", "fail"):
            return step.state, step.label, step.detail
        if step.state == "done":
            return "done", step.label, step.detail

        if step.unit == "none" or step.total <= 0:
            bar = sym.sweep(self._frame, BAR_WIDTH)
            detail = step.detail or (f"{step.done}" if step.done else "")
            return "active", step.label, f"{bar} {detail}".strip()

        fraction = step.done / step.total
        bar = sym.bar(fraction, BAR_WIDTH)
        percent = f"{int(fraction * 100):>3}%"
        if step.unit == "bytes":
            tail = f"{size(step.done)} {sym.DOT} {size(step.total)}"
        else:
            tail = f"{step.done}{sym.PIPE}{step.total}"
        extra = f" {sym.DOT} {step.detail}" if step.detail else ""
        return "active", step.label, f"{bar} {percent} {sym.DOT} {tail}{extra}"

    def text(self) -> str:
        return render.card(
            title=self.title,
            header_bits=self.header_bits,
            steps=[self._render_step(s) for s in self._steps],
            note=self._note,
        )

    # ── the animation ────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="progress-card")

    async def _run(self) -> None:
        try:
            while self._alive:
                await asyncio.sleep(self._interval)
                self._frame += 1
                await self.flush()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("progress animation stopped")

    async def flush(self, text: str | None = None, *, force: bool = False) -> None:
        if not self._alive and not force:
            return
        body = text if text is not None else self.text()
        if body == self._last_text and not force:
            return
        async with self._lock:
            try:
                await self._bot.edit_message_text(
                    body,
                    chat_id=self._chat_id,
                    message_id=self._message_id,
                )
                self._last_text = body
            except TelegramRetryAfter as exc:
                log.warning("flood wait %ss on card edit", exc.retry_after)
                self._interval = min(15.0, max(self._interval, float(exc.retry_after)) + 0.5)
                await asyncio.sleep(exc.retry_after)
            except TelegramBadRequest as exc:
                message = str(exc).lower()
                if "not modified" in message:
                    self._last_text = body
                elif "not found" in message or "can't be edited" in message:
                    self._alive = False
                    log.info("card message gone, animation off")
                else:
                    log.warning("card edit rejected: %s", exc)
            except TelegramForbiddenError:
                self._alive = False
            except Exception as exc:  # network hiccup: try again next tick
                log.debug("card edit failed: %s", exc)

    async def _stop_task(self) -> None:
        self._alive = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def close(self, final_text: str) -> None:
        await self._stop_task()
        self._alive = True
        await self.flush(final_text, force=True)
        self._alive = False

    async def fail(self, key: str | None, reason: str) -> None:
        if key and key in self._index:
            self._index[key].state = "fail"
            self._index[key].detail = ""
        await self.close(render.failure(self.title, reason))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def message_id(self) -> int:
        return self._message_id
