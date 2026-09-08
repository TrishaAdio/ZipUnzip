from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from ..config import Config
from ..state import Store
from ..ui import render

log = logging.getLogger(__name__)
router = Router(name="basic")


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message, config: Config) -> None:
    await message.answer(
        render.start(
            max_mb=config.max_archive_bytes // (1024 * 1024),
            local_api=config.local_api,
        )
    )


@router.message(Command("pass", "password"))
async def cmd_pass(message: Message, command: CommandObject, store: Store) -> None:
    if message.from_user is None:
        return
    state = store.user(message.from_user.id)
    secret = (command.args or "").strip()
    state.password = secret or None
    await message.answer(render.password_saved() if secret else render.password_cleared())
    try:  # a plaintext password should not linger in the chat
        await message.delete()
    except Exception:
        pass


@router.message(Command("raw", "asdoc"))
async def cmd_raw(message: Message, store: Store) -> None:
    if message.from_user is None:
        return
    state = store.user(message.from_user.id)
    state.force_document = not state.force_document
    await message.answer(render.raw_mode(state.force_document))
