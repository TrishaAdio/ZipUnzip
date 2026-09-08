#!/usr/bin/env python3
"""One command to go from a fresh clone to a running bot.

    python3 setup.py

Finds a suitable interpreter, builds .venv, installs requirements, makes sure
.env exists, then launches the bot. Re-running is cheap: the venv is reused and
pip is skipped unless requirements.txt changed.

    python3 setup.py --no-run       set everything up, do not launch
    python3 setup.py --selftest     set up, run the offline checks, then launch
    python3 setup.py --recreate     throw the venv away and rebuild it
    python3 setup.py --upgrade      force pip to reinstall / upgrade packages
    python3 setup.py --dev          also install ruff
    python3 setup.py --token 123:AB write BOT_TOKEN into .env non-interactively

Stdlib only, and deliberately compatible with old interpreters: this script has
to be able to run on the system python before it can tell you that the system
python is too old.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
STAMP = VENV / ".requirements-stamp"

MIN_PYTHON = (3, 11)
CANDIDATES = ("python3.14", "python3.13", "python3.12", "python3.11")

# setuptools verbs, in case someone points pip at this directory.
PACKAGING_VERBS = {
    "install",
    "develop",
    "sdist",
    "bdist_wheel",
    "egg_info",
    "build",
    "build_ext",
    "dist_info",
    "check",
}

# ── output ───────────────────────────────────────────────────────────────────

_COLOR = (
    hasattr(sys.stderr, "isatty")
    and sys.stderr.isatty()
    and os.getenv("NO_COLOR") is None
    and os.getenv("TERM") != "dumb"
) or os.getenv("FORCE_COLOR") not in (None, "", "0")


def _paint(text, code):
    return "\033[{}m{}\033[0m".format(code, text) if _COLOR else text


def step(text):
    print("\n" + _paint("── " + text, "1;36"), file=sys.stderr, flush=True)


def info(text):
    print(_paint("   " + text, "2"), file=sys.stderr, flush=True)


def ok(text):
    print("   " + _paint("done", "32") + " " + text, file=sys.stderr, flush=True)


def warn(text):
    print("   " + _paint("warn", "33") + " " + text, file=sys.stderr, flush=True)


def die(text, hint=""):
    print("\n" + _paint("error", "1;31") + " " + text, file=sys.stderr)
    if hint:
        print(_paint("      " + hint, "2"), file=sys.stderr)
    raise SystemExit(1)


# ── interpreter ──────────────────────────────────────────────────────────────


def _version_of(executable):
    try:
        out = subprocess.check_output(
            [executable, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        major, minor = (int(part) for part in out.decode().strip().split("."))
    except ValueError:
        return None
    return (major, minor)


def find_interpreter():
    """The running python if it is new enough, otherwise hunt for one."""
    if sys.version_info[:2] >= MIN_PYTHON:
        return sys.executable, sys.version_info[:2]

    seen = []
    for name in CANDIDATES:
        path = shutil.which(name)
        if path:
            version = _version_of(path)
            if version and version >= MIN_PYTHON:
                return path, version
            seen.append(name)

    # pyenv installs are often not on PATH
    pyenv = Path(os.getenv("PYENV_ROOT", Path.home() / ".pyenv")) / "versions"
    if pyenv.is_dir():
        for directory in sorted(pyenv.iterdir(), reverse=True):
            candidate = directory / "bin" / "python3"
            if not candidate.is_file():
                continue
            version = _version_of(str(candidate))
            if version and version >= MIN_PYTHON:
                return str(candidate), version

    running = "%d.%d" % sys.version_info[:2]
    die(
        "python {}.{}+ is required, this is {}".format(MIN_PYTHON[0], MIN_PYTHON[1], running),
        "install one of: " + ", ".join(CANDIDATES) + "  (checked PATH and pyenv)",
    )


# ── venv ─────────────────────────────────────────────────────────────────────


def venv_python():
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def build_venv(interpreter, recreate):
    if recreate and VENV.exists():
        info("removing " + str(VENV))
        shutil.rmtree(VENV)

    python = venv_python()
    if python.is_file():
        version = _version_of(str(python))
        if version and version >= MIN_PYTHON:
            ok("reusing .venv (python {}.{})".format(*version))
            return python
        warn("existing .venv is python {} — rebuilding".format(version or "unknown"))
        shutil.rmtree(VENV)

    info("creating .venv")
    result = subprocess.run([interpreter, "-m", "venv", str(VENV)])
    if result.returncode != 0 or not python.is_file():
        die(
            "could not create the virtualenv",
            "on Debian/Ubuntu: apt install python3-venv",
        )
    ok(str(VENV))
    return python


# ── pip ──────────────────────────────────────────────────────────────────────


def _stamp_value(python):
    digest = hashlib.sha256(REQUIREMENTS.read_bytes())
    version = _version_of(str(python)) or (0, 0)
    digest.update(("py%d.%d" % version).encode())
    return digest.hexdigest()


def _stream(command, prefix="pip"):
    """Run a command, echoing its output dimmed so it never drowns the steps."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        line = line.replace("\r", "").rstrip()
        if not line:
            continue
        if len(line) > 118:
            line = line[:117] + "…"
        print(_paint("   {} {}".format(prefix, line), "2"), file=sys.stderr, flush=True)
    return process.wait()


def install_requirements(python, upgrade, dev):
    if not REQUIREMENTS.is_file():
        die("requirements.txt is missing")

    wanted = _stamp_value(python)
    if not upgrade and STAMP.is_file() and STAMP.read_text().strip() == wanted:
        ok("requirements already satisfied")
    else:
        info("upgrading pip")
        _stream([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-U", "pip"])

        info("installing requirements.txt")
        command = [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-color",
            "--progress-bar",
            "off",
            "-r",
            str(REQUIREMENTS),
        ]
        if upgrade:
            command.append("-U")
        if _stream(command) != 0:
            die(
                "pip failed",
                "no network? behind a proxy? try: {} -m pip install -r requirements.txt".format(python),
            )
        STAMP.write_text(wanted + "\n")
        ok("dependencies installed")

    if dev:
        info("installing dev extras")
        _stream([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", "ruff"])
        ok("ruff installed")

    missing = _stream(
        [
            str(python),
            "-c",
            "import aiogram, colorama, PIL, dotenv;"
            "print('aiogram', aiogram.__version__);"
            "print('pillow', PIL.__version__)",
        ],
        prefix="check",
    )
    if missing != 0:
        die("the virtualenv is incomplete", "try: python3 setup.py --recreate")


# ── .env ─────────────────────────────────────────────────────────────────────

TOKEN_PATTERN = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")


def _read_token():
    if not ENV_FILE.is_file():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return None


def _write_token(token):
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("BOT_TOKEN="):
            lines[index] = "BOT_TOKEN=" + token
            break
    else:
        lines.append("BOT_TOKEN=" + token)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_env(token_argument):
    if not ENV_FILE.is_file():
        if not ENV_EXAMPLE.is_file():
            die(".env.example is missing, cannot generate .env")
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        os.chmod(ENV_FILE, 0o600)
        ok("created .env from .env.example")
    else:
        ok(".env present")

    token = token_argument or _read_token()
    placeholder = token in (None, "", "123456:ABC-DEF")

    if placeholder and token_argument is None and sys.stdin.isatty():
        info("get a token from @BotFather")
        try:
            entered = input(_paint("   BOT_TOKEN: ", "1;36")).strip()
        except (EOFError, KeyboardInterrupt):
            entered = ""
        if entered:
            token = entered
            placeholder = False

    if not placeholder and token and token != _read_token():
        _write_token(token)
        os.chmod(ENV_FILE, 0o600)
        ok("BOT_TOKEN written to .env")

    if placeholder:
        warn("BOT_TOKEN is not set in .env — the bot will refuse to start")
        return False

    if token and not TOKEN_PATTERN.match(token):
        warn("BOT_TOKEN does not look like a bot token (expected 123456:AA...)")
    return True


def report_api_mode():
    if not ENV_FILE.is_file():
        return
    base = ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("API_BASE_URL=") and not line.startswith("#"):
            base = line.split("=", 1)[1].strip()
    if base:
        info("local Bot API: " + base + "  (archives up to 2000 MB)")
    else:
        warn("no API_BASE_URL: api.telegram.org caps downloads at 20 MB")
        info("see README and deploy/install-bot-api.sh to lift that")


# ── run ──────────────────────────────────────────────────────────────────────


def run_selftest(python):
    script = ROOT / "scripts" / "selftest.py"
    if not script.is_file():
        warn("scripts/selftest.py is missing, skipping")
        return
    code = subprocess.run([str(python), str(script)]).returncode
    if code != 0:
        die("selftest failed")
    ok("selftest passed")


def launch(python):
    step("starting the bot")
    info("ctrl-c to stop")
    print(file=sys.stderr, flush=True)
    environment = dict(os.environ)
    environment.setdefault("PYTHONUNBUFFERED", "1")
    environment.setdefault("FORCE_COLOR", "1")
    if os.name == "nt":
        raise SystemExit(subprocess.run([str(python), "-m", "bot"], cwd=str(ROOT), env=environment).returncode)
    os.chdir(str(ROOT))
    os.execve(str(python), [str(python), "-m", "bot"], environment)  # replaces this process


# ── main ─────────────────────────────────────────────────────────────────────


def main(argv):
    verbs = PACKAGING_VERBS.intersection(argv[1:])
    if verbs:
        die(
            "this setup.py is a bootstrapper, not a package definition",
            "run: python3 setup.py        (no build backend here)",
        )

    parser = argparse.ArgumentParser(
        prog="setup.py",
        description="Create .venv, install requirements, launch unzipper-bot.",
    )
    parser.add_argument("--no-run", action="store_true", help="set up but do not start the bot")
    parser.add_argument("--recreate", action="store_true", help="delete and rebuild .venv")
    parser.add_argument("--upgrade", action="store_true", help="force pip to upgrade packages")
    parser.add_argument("--selftest", action="store_true", help="run the offline checks first")
    parser.add_argument("--dev", action="store_true", help="also install ruff")
    parser.add_argument("--token", metavar="TOKEN", help="write BOT_TOKEN into .env")
    arguments = parser.parse_args(argv[1:])

    print(_paint("\nunzipper-bot setup", "1;36"), file=sys.stderr)
    info(str(ROOT))

    step("checking python")
    interpreter, version = find_interpreter()
    ok("{}  (python {}.{})".format(interpreter, version[0], version[1]))

    step("virtualenv")
    python = build_venv(interpreter, arguments.recreate)

    step("dependencies")
    install_requirements(python, arguments.upgrade, arguments.dev)

    step("configuration")
    ready = ensure_env(arguments.token)
    report_api_mode()

    if arguments.selftest:
        step("selftest")
        run_selftest(python)

    if arguments.no_run:
        step("ready")
        info("start it with:  {} -m bot".format(python))
        return 0

    if not ready:
        die(
            "cannot start without BOT_TOKEN",
            "python3 setup.py --token <token>   or edit .env",
        )

    launch(python)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except KeyboardInterrupt:
        print(file=sys.stderr)
        raise SystemExit(130)
