"""Getting the archive onto local disk.

Two very different paths:

  * public api.telegram.org — getFile refuses anything over 20 MB, and the file
    must be streamed down over HTTPS.
  * local telegram-bot-api (--local) — the server has already written the file
    to its own --dir and getFile hands back an absolute filesystem path. There
    is nothing to download; we read it in place, which is what makes 2 GB
    archives practical.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot

log = logging.getLogger(__name__)

CLOUD_GETFILE_LIMIT = 20 * 1024 * 1024


class FetchError(Exception):
    pass


@dataclass(slots=True)
class Fetched:
    path: Path
    borrowed: bool  # True when the file belongs to the local API server


def _remap(reported: Path, api_dir: Path | None) -> Path:
    """The bot may see the server's data dir at a different mount point."""
    if reported.exists():
        return reported
    if api_dir is None:
        raise FetchError("the API server returned a path this process cannot see")

    parts = reported.parts
    for index, part in enumerate(parts):
        if ":" in part:  # /var/lib/telegram-bot-api/<token>/documents/file_1.zip
            candidate = api_dir.joinpath(*parts[index:])
            if candidate.exists():
                return candidate
            tail = api_dir.joinpath(*parts[index + 1 :])
            if tail.exists():
                return tail
            break
    candidate = api_dir / reported.name
    if candidate.exists():
        return candidate
    raise FetchError(f"cannot locate {reported.name} under {api_dir}")


async def fetch(
    bot: Bot,
    file_id: str,
    *,
    dest_dir: Path,
    filename: str,
    api_dir: Path | None = None,
    on_progress=None,
) -> Fetched:
    file = await bot.get_file(file_id)
    if not file.file_path:
        raise FetchError("the API server did not return a file path")

    if bot.session.api.is_local:
        path = _remap(Path(file.file_path), api_dir)
        log.info("reading %s in place (%s bytes)", path, file.file_size or 0)
        if on_progress:
            on_progress(file.file_size or path.stat().st_size, file.file_size or path.stat().st_size)
        return Fetched(path=path, borrowed=True)

    if (file.file_size or 0) > CLOUD_GETFILE_LIMIT:
        raise FetchError(
            "api.telegram.org will not serve files above 20 MB — run a local Bot API server"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    log.info("downloading %s (%s bytes)", filename, file.file_size or 0)
    await bot.download_file(file.file_path, destination=dest)
    if on_progress:
        size = dest.stat().st_size
        on_progress(size, size)
    return Fetched(path=dest, borrowed=False)


def release(fetched: Fetched, *, cleanup: bool) -> None:
    """Local-mode files are ours to delete; the server never cleans up its dir."""
    if not cleanup:
        return
    try:
        fetched.path.unlink(missing_ok=True)
    except OSError as exc:
        log.debug("could not remove %s: %s", fetched.path, exc)
