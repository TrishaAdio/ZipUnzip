"""Send extracted files back, each in its native Telegram format.

Grouping rules Telegram imposes:
  * photos and videos may share one media group
  * audio may only be grouped with audio, documents only with documents
  * animations cannot be grouped at all
So we walk the entries in order and flush a batch whenever the flavour changes
or the batch is full, which keeps archive ordering intact inside each album.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge, TelegramRetryAfter
from aiogram.types import FSInputFile
from aiogram.utils.media_group import MediaGroupBuilder

from . import photos
from .archive import Entry
from .classify import Kind, album_flavour
from .humanize import natural_key

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, int, str], None]


@dataclass(slots=True)
class SendReport:
    counts: Counter
    bytes_sent: int = 0
    failed: int = 0
    skipped: int = 0


class Sender:
    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        *,
        scratch: Path,
        album_size: int = 10,
        delay: float = 1.0,
        normalize_photos: bool = True,
        force_document: bool = False,
        reply_to: int | None = None,
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.scratch = scratch
        self.album_size = max(2, min(10, album_size))
        self.delay = delay
        self.normalize_photos = normalize_photos
        self.force_document = force_document
        self.reply_to = reply_to
        self.report = SendReport(counts=Counter())

    # ── preparation ──────────────────────────────────────────────────────────

    async def _prepare(self, entry: Entry) -> tuple[Kind, Path]:
        """Resolve the final kind and the file that will actually be uploaded."""
        if self.force_document:
            return Kind.DOCUMENT, entry.path
        if entry.size == 0:
            return Kind.DOCUMENT, entry.path
        if entry.kind is not Kind.PHOTO:
            return entry.kind, entry.path

        ready = await asyncio.to_thread(
            photos.normalize, entry.path, self.scratch, enabled=self.normalize_photos
        )
        if ready is None:
            log.info("%s cannot be a photo, sending as document", entry.name)
            return Kind.DOCUMENT, entry.path
        return Kind.PHOTO, ready

    # ── single item ──────────────────────────────────────────────────────────

    async def _send_one(self, kind: Kind, path: Path, caption: str) -> bool:
        file = FSInputFile(path)
        try:
            if kind is Kind.PHOTO:
                await self.bot.send_photo(self.chat_id, file, caption=caption)
            elif kind is Kind.VIDEO:
                await self.bot.send_video(
                    self.chat_id, file, caption=caption, supports_streaming=True
                )
            elif kind is Kind.AUDIO:
                await self.bot.send_audio(self.chat_id, file, caption=caption)
            elif kind is Kind.ANIMATION:
                await self.bot.send_animation(self.chat_id, file, caption=caption)
            else:
                await self.bot.send_document(self.chat_id, file, caption=caption)
            return True
        except TelegramRetryAfter as exc:
            log.warning("flood wait %ss before %s", exc.retry_after, path.name)
            await asyncio.sleep(exc.retry_after + 0.5)
            return await self._send_one(kind, path, caption)
        except TelegramEntityTooLarge:
            log.error("%s exceeds the upload limit of this API server", path.name)
            return False
        except TelegramBadRequest as exc:
            if kind is Kind.DOCUMENT:
                log.error("cannot send %s: %s", path.name, exc)
                return False
            log.warning("%s rejected as %s (%s), retrying as document", path.name, kind.value, exc)
            return await self._send_one(Kind.DOCUMENT, path, caption)

    # ── albums ───────────────────────────────────────────────────────────────

    async def _send_group(self, batch: list[tuple[Kind, Path, str]]) -> int:
        if len(batch) == 1:
            kind, path, caption = batch[0]
            return 1 if await self._send_one(kind, path, caption) else 0

        builder = MediaGroupBuilder()
        for kind, path, caption in batch:
            file = FSInputFile(path)
            if kind is Kind.PHOTO:
                builder.add_photo(media=file, caption=caption)
            elif kind is Kind.VIDEO:
                builder.add_video(media=file, caption=caption, supports_streaming=True)
            elif kind is Kind.AUDIO:
                builder.add_audio(media=file, caption=caption)
            else:
                builder.add_document(media=file, caption=caption)

        try:
            await self.bot.send_media_group(self.chat_id, media=builder.build())
            return len(batch)
        except TelegramRetryAfter as exc:
            log.warning("flood wait %ss on album of %d", exc.retry_after, len(batch))
            await asyncio.sleep(exc.retry_after + 0.5)
            return await self._send_group(batch)
        except (TelegramBadRequest, TelegramEntityTooLarge) as exc:
            log.warning("album of %d rejected (%s), falling back to one by one", len(batch), exc)
            ok = 0
            for kind, path, caption in batch:
                if await self._send_one(kind, path, caption):
                    ok += 1
                await asyncio.sleep(self.delay)
            return ok

    # ── driver ───────────────────────────────────────────────────────────────

    async def send_all(self, entries: list[Entry], on_progress: ProgressCb | None = None) -> SendReport:
        ordered = sorted(entries, key=lambda e: natural_key(e.name))
        total = len(ordered)
        batch: list[tuple[Kind, Path, str]] = []
        batch_flavour: str | None = None
        done = 0

        async def flush() -> None:
            nonlocal batch, batch_flavour, done
            if not batch:
                return
            sent = await self._send_group(batch)
            self.report.failed += len(batch) - sent
            for kind, path, _ in batch[:sent]:
                self.report.counts[kind] += 1
                try:
                    self.report.bytes_sent += path.stat().st_size
                except OSError:
                    pass
            done += len(batch)
            if on_progress:
                on_progress(done, total, "")
            batch = []
            batch_flavour = None
            if self.delay:
                await asyncio.sleep(self.delay)

        for entry in ordered:
            if not entry.path.exists():
                self.report.skipped += 1
                continue

            kind, path = await self._prepare(entry)
            caption = f"<code>{_escape(entry.name)}</code>"
            flavour = album_flavour(kind)

            if on_progress:
                on_progress(done, total, Path(entry.name).name)

            if flavour is None:  # animation: always on its own
                await flush()
                if await self._send_one(kind, path, caption):
                    self.report.counts[kind] += 1
                    self.report.bytes_sent += entry.size
                else:
                    self.report.failed += 1
                done += 1
                if on_progress:
                    on_progress(done, total, "")
                if self.delay:
                    await asyncio.sleep(self.delay)
                continue

            if batch_flavour is not None and flavour != batch_flavour:
                await flush()
            batch_flavour = flavour
            batch.append((kind, path, caption))
            if len(batch) >= self.album_size:
                await flush()

        await flush()
        return self.report


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
