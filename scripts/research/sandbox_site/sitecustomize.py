"""Loaded first by every Python process the research sandbox starts (RESEARCH_SANDBOX_V0.1.md §4.3).

Two things, both before any user code runs:

- **No network.** ``connect`` and ``connect_ex`` refuse every socket family but AF_UNIX. This is discipline,
  not a boundary (the account can still reach the network outside the sandbox): it stops sandbox code from
  hitting the venue on the IP the live runtime shares.
- **The lake, not the mock.** When ``RESEARCH_CACHE`` is set, the runtime's collector and feed selectors
  return the cache-backed ones (``scripts/research/lake.py``) and the synthetic mock collector refuses.
"""

import os
import socket
from pathlib import Path

_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex


def _allowed(sock: socket.socket) -> bool:
    return sock.family == getattr(socket, "AF_UNIX", object())


def _deny(self, address):  # noqa: ANN001
    if _allowed(self):
        return _connect(self, address)
    raise PermissionError(f"research sandbox: network is off ({address!r})")


def _deny_ex(self, address):  # noqa: ANN001
    if _allowed(self):
        return _connect_ex(self, address)
    raise PermissionError(f"research sandbox: network is off ({address!r})")


socket.socket.connect = _deny
socket.socket.connect_ex = _deny_ex

if os.environ.get("RESEARCH_CACHE"):
    from scripts.research import lake as _lake

    _lake.install(cache=Path(os.environ["RESEARCH_CACHE"]),
                  lake=Path(os.environ["RESEARCH_LAKE"]) if os.environ.get("RESEARCH_LAKE") else None)
