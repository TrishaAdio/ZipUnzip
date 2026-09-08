from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

from aiogram import F, Router
from aiogram.types import Message

from ..config import Config
from ..core import archive as arch
from ..core import fetch as fetcher
from ..core.humanize import size
from ..core.sender import Sender
from ..state import Store
from ..ui import render
from ..ui.progress import Card

log = logging.getLogger(__name__)
router = Router(name="archives")

STEPS = [("fetch", "fetch"), ("unpack", "unpack"), ("send", "send")]

_CAPTION_PASSWORD = re.compile(r"\b(?:pass|passwd|password|pw)\s*[:=]\s*(\S+)", re.IGNORECASE)


def _password_from_caption(caption: str | None) -> str | None:
    if not caption:
        return None
    found = _CAPTION_PASSWORD.search(caption)
    return found.group(1) if found else None


@router.message(F.document)
async def on_document(message: Message, config: Config, store: Store) -> None:
    document = message.document
    if document is None or message.from_user is None:
        return
    user_id = message.from_user.id

    if not config.check_access(user_id, message.chat.id):
        log.warning("rejected %s in chat %s", user_id, message.chat.id)
        return

    name = document.file_name or "archive.zip"
    if not arch.looks_like_archive(name, document.mime_type):
        return

    if (document.file_size or 0) > config.max_archive_bytes:
        cap = config.max_archive_bytes // (1024 * 1024)
        await message.reply(
            render.failure(name, f"{size(document.file_size or 0)} exceeds the {cap} MB limit")
        )
        return

    if not store.acquire_user(user_id):
        await message.reply(render.busy())
        return

    try:
        async with store.slots:
            await _run_job(message, config, store, name)
    finally:
        store.release_user(user_id)


async def _run_job(message: Message, config: Config, store: Store, name: str) -> None:
    document = message.document
    assert document is not None and message.from_user is not None
    bot = message.bot
    assert bot is not None

    state = store.user(message.from_user.id)
    password = _password_from_caption(message.caption) or state.password
    job_id = uuid.uuid4().hex[:10]
    work = config.work_dir / job_id
    unpacked = work / "unpacked"
    scratch = work / "encoded"
    started = time.monotonic()

    log.info(
        "job %s | %s | %s | user %s",
        job_id,
        name,
        size(document.file_size or 0),
        message.from_user.id,
    )

    card = await Card.open(
        bot,
        message.chat.id,
        title=name,
        steps=STEPS,
        interval=config.anim_interval,
        header_bits=[size(document.file_size or 0)],
        reply_to=message.message_id,
    )
    await card.start()

    fetched: fetcher.Fetched | None = None
    try:
        # ── fetch ────────────────────────────────────────────────────────────
        card.step("fetch", total=document.file_size or 0, unit="bytes")
        fetched = await fetcher.fetch(
            bot,
            document.file_id,
            dest_dir=work,
            filename="archive.zip",
            api_dir=config.api_dir,
            on_progress=lambda done, total: card.advance("fetch", done, total),
        )
        card.complete("fetch")

        # ── inspect ──────────────────────────────────────────────────────────
        plan = await asyncio.to_thread(
            arch.inspect,
            fetched.path,
            max_entries=config.max_entries,
            max_total=config.max_total_unpacked_bytes,
            max_ratio=config.max_compression_ratio,
        )
        card.header_bits = [
            size(document.file_size or 0),
            f"{plan.entries} files",
            size(plan.unpacked),
        ]
        log.info(
            "job %s | %d entries | %s unpacked | encrypted=%s",
            job_id,
            plan.entries,
            size(plan.unpacked),
            plan.encrypted,
        )
        if plan.encrypted and not password:
            raise arch.PasswordRequired("archive is password protected")

        # ── unpack ───────────────────────────────────────────────────────────
        card.step("unpack", total=plan.unpacked or 1, unit="bytes")

        def hook(progress: arch.Progress) -> None:
            card.advance(
                "unpack", progress.done_bytes, progress.total_bytes or 1, note=progress.current
            )
            card.detail("unpack", f"{progress.done_files} of {progress.total_files}")

        entries = await asyncio.to_thread(
            arch.extract,
            fetched.path,
            unpacked,
            password=password,
            max_entries=config.max_entries,
            max_total=config.max_total_unpacked_bytes,
            hook=hook,
        )
        card.complete("unpack")
        card.detail("unpack", "")
        log.info("job %s | unpacked %d files in %.1fs", job_id, len(entries), time.monotonic() - started)

        if fetched.borrowed:
            fetcher.release(fetched, cleanup=config.api_cleanup)
            fetched = None

        # ── send ─────────────────────────────────────────────────────────────
        card.step("send", total=len(entries), unit="count")
        sender = Sender(
            bot,
            message.chat.id,
            scratch=scratch,
            album_size=config.album_size,
            delay=config.send_delay,
            normalize_photos=config.normalize_photos,
            force_document=state.force_document,
        )
        report = await sender.send_all(
            entries,
            on_progress=lambda done, total, note: card.advance("send", done, total, note=note),
        )
        card.complete("send")

        elapsed = time.monotonic() - started
        await card.close(
            render.summary(
                title=name,
                counts=report.counts,
                total_bytes=report.bytes_sent,
                elapsed=elapsed,
                failed=report.failed,
                skipped=report.skipped,
            )
        )
        log.info(
            "job %s | done | %s | %s | %.1fs | failed=%d",
            job_id,
            dict(report.counts),
            size(report.bytes_sent),
            elapsed,
            report.failed,
        )

    except arch.ArchiveError as exc:
        log.warning("job %s | %s: %s", job_id, type(exc).__name__, exc)
        await card.fail(_active_key(card), str(exc))
    except fetcher.FetchError as exc:
        log.error("job %s | fetch failed: %s", job_id, exc)
        await card.fail("fetch", str(exc))
    except asyncio.CancelledError:
        await card.fail(None, "cancelled")
        raise
    except Exception as exc:
        log.exception("job %s | unhandled failure", job_id)
        await card.fail(_active_key(card), f"{type(exc).__name__}: {exc}"[:180])
    finally:
        if fetched is not None and fetched.borrowed:
            fetcher.release(fetched, cleanup=config.api_cleanup)
        await asyncio.to_thread(arch.wipe, work)
        log.debug("job %s | workspace removed", job_id)


def _active_key(card: Card) -> str | None:
    for step in card._steps:  # noqa: SLF001 - the card is ours
        if step.state == "active":
            return step.key
    return None
