#!/usr/bin/env python3
"""Offline end-to-end check: build synthetic archives, unpack them, and drive the
sender against a stub bot. No token and no network needed.

    python scripts/selftest.py
"""

from __future__ import annotations

import asyncio
import io
import sys
import tempfile
import zipfile
from collections import Counter
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import logs  # noqa: E402
from bot.core import archive as arch  # noqa: E402
from bot.core.classify import Kind  # noqa: E402
from bot.core.sender import Sender  # noqa: E402
from bot.ui import render  # noqa: E402
from bot.ui.progress import Card  # noqa: E402
from bot.ui import symbols as sym  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


# ── fixtures ─────────────────────────────────────────────────────────────────


def jpeg(width: int, height: int, colour: tuple[int, int, int]) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, "JPEG", quality=70)
    return buf.getvalue()


def png(width: int, height: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (width, height), (10, 20, 30, 128)).save(buf, "PNG")
    return buf.getvalue()


def build_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for index in range(1, 13):  # 12 photos: expect albums of 10 + 2
            zf.writestr(f"shots/IMG_{index}.jpg", jpeg(320, 240, (index * 15 % 255, 90, 140)))
        zf.writestr("shots/wide.png", png(9000, 1400))  # dim sum > 10000, must be re-encoded
        zf.writestr("clip.gif", b"GIF89a" + b"\x00" * 512)
        zf.writestr("track.mp3", b"ID3\x03\x00\x00\x00" + b"\x00" * 4096)
        zf.writestr("movie.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048)
        zf.writestr("notes.txt", "hello\n" * 100)
        zf.writestr("noext", b"\xff\xd8\xff\xe0" + b"\x00" * 800)  # jpeg by magic only
        zf.writestr("../escape.txt", b"nope")  # zip slip
        zf.writestr("/abs/escape2.txt", b"nope")  # absolute path
        zf.writestr("empty/", b"")


# ── stub bot ─────────────────────────────────────────────────────────────────


class StubBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def send_photo(self, chat_id, file, caption=None, **kw):
        self.calls.append(("photo", Path(file.path).name))

    async def send_video(self, chat_id, file, caption=None, **kw):
        self.calls.append(("video", Path(file.path).name))

    async def send_audio(self, chat_id, file, caption=None, **kw):
        self.calls.append(("audio", Path(file.path).name))

    async def send_animation(self, chat_id, file, caption=None, **kw):
        self.calls.append(("animation", Path(file.path).name))

    async def send_document(self, chat_id, file, caption=None, **kw):
        self.calls.append(("document", Path(file.path).name))

    async def send_media_group(self, chat_id, media, **kw):
        kinds = [item.type for item in media]
        self.calls.append(("group", tuple(kinds)))


class CardBot:
    """Captures the edits the progress card makes."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edits: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return SimpleNamespace(message_id=42)

    async def edit_message_text(self, text, chat_id=None, message_id=None, **kw):
        self.edits.append(text)
        return True


# ── the run ──────────────────────────────────────────────────────────────────


async def main() -> int:
    logs.install("WARNING", color="always")
    work = Path(tempfile.mkdtemp(prefix="unzipper-selftest-"))
    archive_path = work / "sample.zip"
    build_zip(archive_path)
    print(f"\narchive: {archive_path} ({archive_path.stat().st_size} bytes)")

    print("\ninspect")
    plan = arch.inspect(archive_path, max_entries=100, max_total=1 << 30, max_ratio=1000)
    check("entry count excludes directories", plan.entries == 20, f"{plan.entries}")
    check("not flagged as encrypted", plan.encrypted is False)

    print("\nlimits are enforced")
    try:
        arch.inspect(archive_path, max_entries=3, max_total=1 << 30, max_ratio=1000)
        check("max_entries rejected", False)
    except arch.TooLarge as exc:
        check("max_entries rejected", True, str(exc))
    try:
        arch.inspect(archive_path, max_entries=100, max_total=1024, max_ratio=1000)
        check("max_total rejected", False)
    except arch.TooLarge as exc:
        check("max_total rejected", True, str(exc))

    print("\nextract")
    progress_samples: list[tuple[int, int]] = []
    entries = arch.extract(
        archive_path,
        work / "unpacked",
        password=None,
        max_entries=100,
        max_total=1 << 30,
        hook=lambda p: progress_samples.append((p.done_bytes, p.done_files)),
    )
    names = {e.name for e in entries}
    # 12 jpg + wide.png + gif + mp3 + mp4 + txt + noext + 2 sanitised paths
    check("all safe entries extracted", len(entries) == 20, f"{len(entries)} files")
    check("zip slip neutralised", "escape.txt" in names and "../escape.txt" not in names)
    check("absolute path neutralised", "abs/escape2.txt" in names)
    check("nothing written outside the target", not (work / "escape.txt").exists())
    check("progress hook fired", len(progress_samples) > 10, f"{len(progress_samples)} samples")

    kinds = Counter(e.kind for e in entries)
    check("14 images classified", kinds[Kind.PHOTO] == 14, f"{kinds[Kind.PHOTO]}")
    check("gif is an animation", kinds[Kind.ANIMATION] == 1)
    check("mp3 is audio", kinds[Kind.AUDIO] == 1)
    check("mp4 is video", kinds[Kind.VIDEO] == 1)
    by_name = {e.name: e for e in entries}
    check("extensionless jpeg found by magic", by_name["noext"].kind is Kind.PHOTO)

    print("\nsend")
    bot = StubBot()
    sender = Sender(
        bot,  # type: ignore[arg-type]
        chat_id=1,
        scratch=work / "encoded",
        album_size=10,
        delay=0.0,
    )
    seen: list[tuple[int, int]] = []
    report = await sender.send_all(entries, on_progress=lambda d, t, n: seen.append((d, t)))

    groups = [c for c in bot.calls if c[0] == "group"]
    singles = [c for c in bot.calls if c[0] != "group"]
    check("albums were built", len(groups) >= 2, f"{len(groups)} groups")
    check("no album exceeds 10", all(len(c[1]) <= 10 for c in groups))
    check(
        "albums never mix audio with visuals",
        all(len({"audio"} & set(c[1])) == 0 or set(c[1]) == {"audio"} for c in groups),
    )
    check("animation sent on its own", ("animation", "clip.gif") in singles)
    check("every file accounted for", sum(report.counts.values()) == len(entries), f"{report.counts}")
    check("nothing failed", report.failed == 0)
    check("progress reported to the last file", seen[-1][0] == len(entries))

    photo_total = report.counts[Kind.PHOTO]
    check("wide.png still went out as a photo", photo_total >= 13, f"{photo_total} photos")

    print("\npassword handling")
    try:
        import pyzipper

        enc = work / "secret.zip"
        with pyzipper.AESZipFile(
            enc, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
        ) as zf:
            zf.setpassword(b"hunter2")
            zf.writestr("inside.jpg", jpeg(64, 64, (200, 40, 40)))

        plan = arch.inspect(enc, max_entries=10, max_total=1 << 30, max_ratio=1000)
        check("encryption detected", plan.encrypted is True)
        try:
            arch.extract(enc, work / "enc1", password=None, max_entries=10, max_total=1 << 30)
            check("missing password refused", False)
        except arch.PasswordRequired:
            check("missing password refused", True)
        try:
            arch.extract(enc, work / "enc2", password="wrong", max_entries=10, max_total=1 << 30)
            check("wrong password refused", False)
        except (arch.WrongPassword, arch.ArchiveError) as exc:
            check("wrong password refused", True, type(exc).__name__)
        ok = arch.extract(enc, work / "enc3", password="hunter2", max_entries=10, max_total=1 << 30)
        check("correct password unlocks", len(ok) == 1 and ok[0].kind is Kind.PHOTO)
    except ImportError:
        print("  [skip] pyzipper not installed")

    print("\nprogress card animation")
    card_bot = CardBot()
    card = await Card.open(
        card_bot,  # type: ignore[arg-type]
        chat_id=1,
        title="sample.zip",
        steps=[("fetch", "fetch"), ("unpack", "unpack"), ("send", "send")],
        interval=0.7,
        header_bits=["8.3 KB"],
    )
    await card.start()
    card.step("unpack", total=1000, unit="bytes")
    for done in (150, 400, 720):
        card.advance("unpack", done, 1000, note=f"IMG_{done}.jpg")
        await asyncio.sleep(0.75)
    edits_while_moving = len(card_bot.edits)
    await asyncio.sleep(1.6)  # progress frozen: identical text must not be re-sent
    frozen_edits = len(card_bot.edits) - edits_while_moving
    check("card edits while progress moves", edits_while_moving >= 2, f"{edits_while_moving} edits")
    check("identical frames are not re-sent", frozen_edits == 0, f"{frozen_edits} extra")

    card.step("send", total=4, unit="count")
    await asyncio.sleep(0.75)
    check("indeterminate steps keep animating", len(card_bot.edits) > edits_while_moving)
    await card.close(
        render.summary(
            title="sample.zip", counts=Counter({Kind.PHOTO: 4}), total_bytes=4096, elapsed=3.0
        )
    )
    after_close = len(card_bot.edits)
    await asyncio.sleep(1.0)
    check("animation stops after close", len(card_bot.edits) == after_close)
    check("final frame is the summary", "4 photos" in card_bot.edits[-1])
    check("earlier frames showed a bar", any(sym.PIPE + sym.DONE in e for e in card_bot.edits))

    print("\nrendered output")
    frames = [
        render.card(
            title="holiday-photos.zip",
            header_bits=["84.2 MB", "119 files"],
            steps=[
                ("done", "fetch", ""),
                ("active", "unpack", f"{sym.bar(0.48)}  48% {sym.DOT} 40.1 MB {sym.DOT} 84.2 MB"),
                ("pending", "send", ""),
            ],
            note="IMG_0421.jpg",
        ),
        render.summary(
            title="holiday-photos.zip",
            counts=Counter({Kind.PHOTO: 96, Kind.VIDEO: 12, Kind.DOCUMENT: 11}),
            total_bytes=88_300_000,
            elapsed=102.0,
            failed=1,
        ),
        render.failure("holiday-photos.zip", "wrong password"),
        render.start(max_mb=2000, local_api=True),
    ]
    for frame in frames:
        print("  " + "\n  ".join(frame.splitlines()))
        print()
    check("sweep animates", sym.sweep(0) != sym.sweep(1))
    check("bar fills", sym.bar(0.0).count(sym.DONE) == 0 and sym.bar(1.0).count(sym.DONE) == 10)

    arch.wipe(work)
    check("workspace removed", not work.exists())

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
