"""Zip inspection and extraction.

Runs blocking work in a worker thread; progress is published by mutating a
callback-owned counter, which the UI loop samples on its own schedule.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .classify import Kind, classify
from .humanize import size as human

try:  # AES (WinZip) support, optional
    import pyzipper  # type: ignore

    _ZIP_IMPLS = (pyzipper.AESZipFile, zipfile.ZipFile)
except Exception:  # pragma: no cover - pyzipper not installed
    pyzipper = None  # type: ignore
    _ZIP_IMPLS = (zipfile.ZipFile,)

log = logging.getLogger(__name__)

CHUNK = 1024 * 1024
_HEAD = 32

ARCHIVE_SUFFIXES = {".zip", ".cbz", ".zipx", ".apk", ".epub"}


class ArchiveError(Exception):
    """Anything the user can act on; the message is shown verbatim."""


class NotAnArchive(ArchiveError):
    pass


class PasswordRequired(ArchiveError):
    pass


class WrongPassword(ArchiveError):
    pass


class TooLarge(ArchiveError):
    pass


@dataclass(slots=True)
class Entry:
    name: str  # path as it appears inside the archive
    path: Path  # where it was written on disk
    size: int
    kind: Kind


@dataclass(slots=True)
class Plan:
    entries: int
    unpacked: int
    encrypted: bool


@dataclass(slots=True)
class Progress:
    """Written from the worker thread, read from the event loop."""

    done_bytes: int = 0
    total_bytes: int = 0
    done_files: int = 0
    total_files: int = 0
    current: str = ""


ProgressHook = Callable[[Progress], None]


def looks_like_archive(name: str, mime: str | None = None) -> bool:
    if Path(name).suffix.lower() in ARCHIVE_SUFFIXES:
        return True
    return bool(mime and "zip" in mime.lower())


def _open(path: Path):
    last: Exception | None = None
    for impl in _ZIP_IMPLS:
        try:
            return impl(path)
        except (zipfile.BadZipFile, OSError) as exc:
            last = exc
    raise NotAnArchive("not a readable zip archive") from last


def _is_encrypted(info: zipfile.ZipInfo) -> bool:
    return bool(info.flag_bits & 0x1)


def _decode(info: zipfile.ZipInfo, fallback: str) -> str:
    """zipfile assumes CP437 when the UTF-8 flag is absent; most zips are UTF-8."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for encoding in ("utf-8", fallback):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return info.filename


_UNSAFE = {"", ".", ".."}


def safe_relpath(name: str) -> PurePosixPath | None:
    """Neutralise zip-slip: absolute paths, drive letters, .. and symlink names."""
    cleaned = name.replace("\\", "/")
    parts: list[str] = []
    for part in PurePosixPath(cleaned).parts:
        if part in ("/", *_UNSAFE):
            continue
        if ":" in part:  # C:\ style prefixes
            part = part.split(":")[-1]
        part = part.strip().rstrip(".")
        part = "".join(ch for ch in part if ch >= " " and ch != "\x7f")
        if not part or part in _UNSAFE:
            continue
        parts.append(part[:120])
    if not parts:
        return None
    return PurePosixPath(*parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return bool(mode & 0o170000 == 0o120000)


def inspect(
    path: Path,
    *,
    max_entries: int,
    max_total: int,
    max_ratio: int,
) -> Plan:
    """Read the central directory only. Rejects bombs before writing a byte."""
    with _open(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos:
            raise ArchiveError("archive is empty")
        if len(infos) > max_entries:
            raise TooLarge(f"{len(infos)} files, limit is {max_entries}")

        total = 0
        encrypted = False
        for info in infos:
            encrypted = encrypted or _is_encrypted(info)
            total += info.file_size
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > max_ratio and info.file_size > 8 * 1024 * 1024:
                    raise TooLarge(f"compression ratio {ratio:.0f}x on {_decode(info, 'cp437')}")
        if total > max_total:
            raise TooLarge(f"unpacks to {human(total)}, limit is {human(max_total)}")
        return Plan(entries=len(infos), unpacked=total, encrypted=encrypted)


def extract(
    path: Path,
    dest: Path,
    *,
    password: str | None,
    max_entries: int,
    max_total: int,
    hook: ProgressHook | None = None,
    fallback_encoding: str = "cp437",
) -> list[Entry]:
    """Blocking. Call through asyncio.to_thread."""
    dest.mkdir(parents=True, exist_ok=True)
    pwd = password.encode("utf-8") if password else None
    progress = Progress()
    entries: list[Entry] = []
    written = 0

    with _open(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and not _is_symlink(i)]
        progress.total_files = len(infos)
        progress.total_bytes = sum(i.file_size for i in infos)
        if hook:
            hook(progress)

        needs_pwd = any(_is_encrypted(i) for i in infos)
        if needs_pwd and not pwd:
            raise PasswordRequired("archive is password protected")
        if pwd:
            zf.setpassword(pwd)

        for index, info in enumerate(infos, start=1):
            if index > max_entries:
                break
            name = _decode(info, fallback_encoding)
            rel = safe_relpath(name)
            if rel is None:
                log.warning("skipping unusable entry name: %r", name)
                continue

            target = (dest / rel).resolve()
            root = dest.resolve()
            if not str(target).startswith(str(root)):
                log.warning("skipping path traversal entry: %r", name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)

            progress.current = rel.name
            if hook:
                hook(progress)

            head = b""
            try:
                with zf.open(info, "r") as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        if not head:
                            head = chunk[:_HEAD]
                        written += len(chunk)
                        if written > max_total:
                            raise TooLarge("unpacked size exceeded the limit mid-extraction")
                        dst.write(chunk)
                        progress.done_bytes = written
                        if hook:
                            hook(progress)
            except RuntimeError as exc:
                text = str(exc).lower()
                target.unlink(missing_ok=True)
                if "password required" in text:
                    raise PasswordRequired("archive is password protected") from exc
                if "bad password" in text or "password" in text:
                    raise WrongPassword("wrong password") from exc
                raise ArchiveError(f"cannot read {rel.name}") from exc
            except (zipfile.BadZipFile, ValueError) as exc:
                target.unlink(missing_ok=True)
                if "bad password" in str(exc).lower():
                    raise WrongPassword("wrong password") from exc
                log.warning("corrupt entry %r: %s", name, exc)
                continue
            except OSError as exc:
                target.unlink(missing_ok=True)
                log.warning("write failed for %r: %s", name, exc)
                continue

            entries.append(
                Entry(
                    name=str(rel),
                    path=target,
                    size=target.stat().st_size,
                    kind=classify(rel.name, head),
                )
            )
            progress.done_files = len(entries)
            if hook:
                hook(progress)

    if not entries:
        raise ArchiveError("nothing could be extracted")
    return entries


def wipe(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
