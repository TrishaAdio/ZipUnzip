"""Runtime configuration, resolved once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MB = 1024 * 1024


class ConfigError(RuntimeError):
    pass


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _bool(name: str, default: bool) -> bool:
    raw = _str(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on", "y"}


def _int(name: str, default: int, *, lo: int | None = None, hi: int | None = None) -> int:
    raw = _str(name)
    try:
        value = int(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _float(name: str, default: float, *, lo: float | None = None) -> float:
    raw = _str(name)
    try:
        value = float(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if lo is not None:
        value = max(lo, value)
    return value


def _ids(name: str) -> frozenset[int]:
    raw = _str(name)
    if not raw:
        return frozenset()
    out: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError as exc:
            raise ConfigError(f"{name} contains a non-numeric id: {chunk!r}") from exc
    return frozenset(out)


@dataclass(frozen=True, slots=True)
class Config:
    token: str
    allowed_users: frozenset[int] = field(default_factory=frozenset)
    allowed_chats: frozenset[int] = field(default_factory=frozenset)

    api_base_url: str = ""
    api_dir: Path | None = None
    api_cleanup: bool = True

    work_dir: Path = Path("/var/tmp/unzipper")
    max_archive_bytes: int = 2000 * MB
    max_total_unpacked_bytes: int = 4096 * MB
    max_entries: int = 600
    max_compression_ratio: int = 250
    max_concurrent_jobs: int = 2

    normalize_photos: bool = True
    album_size: int = 10
    send_delay: float = 1.0

    anim_interval: float = 1.8

    log_level: str = "INFO"
    log_color: str = "auto"
    log_file: Path | None = None

    @property
    def local_api(self) -> bool:
        return bool(self.api_base_url)

    @classmethod
    def from_env(cls) -> Config:
        token = _str("BOT_TOKEN")
        if not token:
            raise ConfigError("BOT_TOKEN is not set")

        api_base_url = _str("API_BASE_URL").rstrip("/")
        api_dir_raw = _str("API_DIR")
        log_file_raw = _str("LOG_FILE")

        return cls(
            token=token,
            allowed_users=_ids("ALLOWED_USERS"),
            allowed_chats=_ids("ALLOWED_CHATS"),
            api_base_url=api_base_url,
            api_dir=Path(api_dir_raw) if api_dir_raw else None,
            api_cleanup=_bool("API_CLEANUP", True),
            work_dir=Path(_str("WORK_DIR", "/var/tmp/unzipper")),
            max_archive_bytes=_int("MAX_ARCHIVE_MB", 2000, lo=1) * MB,
            max_total_unpacked_bytes=_int("MAX_TOTAL_UNPACKED_MB", 4096, lo=1) * MB,
            max_entries=_int("MAX_ENTRIES", 600, lo=1),
            max_compression_ratio=_int("MAX_COMPRESSION_RATIO", 250, lo=2),
            max_concurrent_jobs=_int("MAX_CONCURRENT_JOBS", 2, lo=1, hi=16),
            normalize_photos=_bool("NORMALIZE_PHOTOS", True),
            album_size=_int("ALBUM_SIZE", 10, lo=2, hi=10),
            send_delay=_float("SEND_DELAY", 1.0, lo=0.0),
            anim_interval=_float("ANIM_INTERVAL", 1.8, lo=0.7),
            log_level=_str("LOG_LEVEL", "INFO").upper(),
            log_color=_str("LOG_COLOR", "auto").lower(),
            log_file=Path(log_file_raw) if log_file_raw else None,
        )

    def check_access(self, user_id: int | None, chat_id: int | None) -> bool:
        if self.allowed_users and (user_id is None or user_id not in self.allowed_users):
            return False
        if self.allowed_chats and (chat_id is None or chat_id not in self.allowed_chats):
            return False
        return True
