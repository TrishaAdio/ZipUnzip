from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import AiogramError, TelegramNetworkError, TelegramUnauthorizedError
from aiohttp import ClientError
from dotenv import load_dotenv

from . import __version__, logs
from .config import Config, ConfigError
from .handlers import build_router
from .state import Store

log = logging.getLogger("bot")


def build_bot(config: Config) -> Bot:
    session = None
    if config.local_api:
        session = AiohttpSession(
            api=TelegramAPIServer.from_base(config.api_base_url, is_local=True)
        )
    return Bot(
        token=config.token,
        session=session,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )


async def run(config: Config) -> None:
    bot = build_bot(config)
    try:
        await _serve(bot, config)
    finally:
        await bot.session.close()
        log.info("stopped")


async def _serve(bot: Bot, config: Config) -> None:
    endpoint = config.api_base_url or "https://api.telegram.org"

    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        log.error("token rejected by %s", endpoint)
        if config.local_api:
            log.error(
                "a token still bound to the cloud API cannot be used locally — "
                "call https://api.telegram.org/bot<TOKEN>/logOut once, then restart"
            )
        return
    except (TelegramNetworkError, ClientError, OSError) as exc:
        log.error("cannot reach %s (%s)", endpoint, exc)
        if config.local_api:
            log.error("is telegram-bot-api running? systemctl status telegram-bot-api")
        return
    except AiogramError as exc:
        # Something answered, but it does not speak Bot API: wrong port, a proxy,
        # or an unrelated service sitting on 8081.
        log.error("%s is not a Bot API server (%s)", endpoint, type(exc).__name__)
        log.debug("%s", exc)
        return

    store = Store(config.max_concurrent_jobs)
    dispatcher = Dispatcher()
    dispatcher["config"] = config
    dispatcher["store"] = store
    dispatcher.include_router(build_router())

    config.work_dir.mkdir(parents=True, exist_ok=True)
    if config.local_api and config.api_dir is not None and not config.api_dir.is_dir():
        log.warning("API_DIR %s is not readable from here; large archives will fail", config.api_dir)

    logs.banner(
        f"unzipper-bot {__version__}",
        [
            ("bot", f"@{me.username} ({me.id})"),
            ("api", endpoint),
            ("mode", "local" if config.local_api else "cloud"),
            ("archive cap", f"{config.max_archive_bytes // (1024 * 1024)} MB"),
            ("unpack cap", f"{config.max_total_unpacked_bytes // (1024 * 1024)} MB"),
            ("workdir", str(config.work_dir)),
            ("jobs", str(config.max_concurrent_jobs)),
        ],
        color=config.log_color,
    )
    if not config.local_api:
        log.warning("cloud API: downloads are capped at 20 MB, uploads at 50 MB")

    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(
        bot,
        allowed_updates=dispatcher.resolve_used_update_types(),
        handle_signals=True,
    )


def main() -> int:
    load_dotenv()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    logs.install(config.log_level, color=config.log_color, log_file=config.log_file)
    try:
        asyncio.run(run(config))
    except (KeyboardInterrupt, SystemExit):
        log.info("interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
