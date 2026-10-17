"""The actor's one way to start a process: an argv from ``docker_argv``, no shell, nothing inherited.

The command's stdout is returned for the actor to parse into fixed fields; stderr goes nowhere. The
environment is ``PATH`` and ``LANG`` only — no ``DOCKER_*``, no ``GIT_*``, nothing from the caller.
A command that cannot start or does not finish in time raises; the actor decides what that means.
"""

from __future__ import annotations

import subprocess
from typing import Protocol

MAX_STDOUT_BYTES = 64 * 1024


class CommandTimeout(Exception):
    """The command did not finish before its deadline."""


class CommandUnavailable(Exception):
    """The command could not be started."""


class CommandRunner(Protocol):
    def __call__(self, argv: list[str], *, timeout_s: float) -> tuple[int, str]: ...


def run_command(argv: list[str], *, timeout_s: float) -> tuple[int, str]:
    if timeout_s <= 0:
        raise CommandTimeout()
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=timeout_s, env={"PATH": "/usr/bin:/bin", "LANG": "C"}, check=False)
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
