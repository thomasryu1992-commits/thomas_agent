"""The actor's one way to start a process: an argv from ``docker_argv``, no shell, nothing inherited.

The command's stdout is returned for the actor to parse into fixed fields; stderr goes nowhere. The
environment is ``PATH`` and ``LANG``, and — when the actor was built with one — ``DOCKER_CONFIG``: no
other ``DOCKER_*`` (``DOCKER_HOST`` least of all), no ``GIT_*``, nothing from the caller. A command that
cannot start or does not finish in time raises; the actor decides what that means.

``DOCKER_CONFIG`` (BUILD-HOME = A, Thomas 2026-10-17): Docker's BuildKit client needs a directory it can
write, and X has no home (C4 found ``docker image build`` failing with ``mkdir /nonexistent``). The value
is a fixed subdirectory of X's own private records directory, taken from the root-owned configuration —
never from a request — and it must be an absolute, plain path or no command starts.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

MAX_STDOUT_BYTES = 64 * 1024
DOCKER_CONFIG_DIR = "docker-config"
BASE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C"}


class CommandTimeout(Exception):
    """The command did not finish before its deadline."""


class CommandUnavailable(Exception):
    """The command could not be started."""


class CommandRunner(Protocol):
    def __call__(self, argv: list[str], *, timeout_s: float) -> tuple[int, str]: ...


def command_environment(docker_config: Path | None = None) -> dict[str, str]:
    """``BASE_ENV``, plus ``DOCKER_CONFIG`` when given. A path that is not absolute and plain refuses."""
    env = dict(BASE_ENV)
    if docker_config is not None:
        raw = str(docker_config)                       # checked before Path folds "//" away
        path = Path(raw)
        text = str(path)
        if (not path.is_absolute() or ".." in path.parts or "//" in raw or raw != text
                or any(c.isspace() for c in raw)):
            raise CommandUnavailable()
        env["DOCKER_CONFIG"] = text
    return env


def run_command(argv: list[str], *, timeout_s: float, docker_config: Path | None = None) -> tuple[int, str]:
    if timeout_s <= 0:
        raise CommandTimeout()
    env = command_environment(docker_config)
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=timeout_s, env=env, check=False)
    except subprocess.TimeoutExpired:
        raise CommandTimeout() from None
    except OSError:
        raise CommandUnavailable() from None
    out = result.stdout[:MAX_STDOUT_BYTES]
    try:
        text = out.decode("ascii")
    except UnicodeDecodeError:
        text = ""
    return result.returncode, text
