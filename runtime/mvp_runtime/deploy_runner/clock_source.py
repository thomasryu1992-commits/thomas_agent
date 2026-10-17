"""Where the actor learns that the host clock is synchronised: the kernel's own NTP state.

CLOCK-SOURCE = A (Thomas 2026-10-17: ``결정: CLOCK-SOURCE = A(커널 adjtimex, STA_UNSYNC 해제 + maxerror ≤ 16초),
DESIGN_ONLY``). The actor reads ``adjtimex(2)`` with ``modes = 0`` — a read; it changes nothing and needs no
capability — and calls the clock synchronised only when all three hold:

- the call succeeds and its state is not ``TIME_ERROR``;
- ``STA_UNSYNC`` is clear in ``status``;
- ``maxerror`` is at most ``SYNC_MAX_ERROR_US`` (16 s).

The kernel is where every time daemon reports (chrony here, systemd-timesyncd elsewhere), so the answer
does not depend on which one a host runs. Nothing is spawned and no bus is asked, so it works inside the
actor's systemd sandbox. Anything that cannot be read — another platform, no C library, a failing call — is
"not confirmed", and ``prepare_candidate`` stays refused with ``CLOCK_UNTRUSTED``: a check that cannot run
never reads as fine.

16 s is the kernel's own limit: past it the kernel marks the clock unsynchronised by itself
(``NTP_PHASE_LIMIT``). The approval-window numbers (CR-10, CR-11, CR-19) are separate and still deferred.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import Any, Callable

SYNC_MAX_ERROR_US = 16_000_000
STA_UNSYNC = 0x0040
TIME_ERROR = 5


class _Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class Timex(ctypes.Structure):
    """``struct timex`` (Linux, LP64), with the kernel's trailing padding and room to spare."""

    _fields_ = [("modes", ctypes.c_uint), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
                ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long), ("status", ctypes.c_int),
                ("constant", ctypes.c_long), ("precision", ctypes.c_long), ("tolerance", ctypes.c_long),
                ("time", _Timeval), ("tick", ctypes.c_long), ("ppsfreq", ctypes.c_long), ("jitter", ctypes.c_long),
                ("shift", ctypes.c_int), ("stabil", ctypes.c_long), ("jitcnt", ctypes.c_long),
                ("calcnt", ctypes.c_long), ("errcnt", ctypes.c_long), ("stbcnt", ctypes.c_long),
                ("tai", ctypes.c_int), ("_reserved", ctypes.c_int * 11), ("_spare", ctypes.c_byte * 64)]


def _libc_adjtimex(buffer: Timex) -> int:
    if not sys.platform.startswith("linux"):
        raise OSError("adjtimex is Linux-only")
    name = ctypes.util.find_library("c")
    if not name:
        raise OSError("no C library")
    libc = ctypes.CDLL(name, use_errno=True)
    return int(libc.adjtimex(ctypes.byref(buffer)))


def kernel_sync_confirmed(*, adjtimex: Callable[[Timex], int] | None = None) -> bool:
    """True only when the kernel reports the clock synchronised within ``SYNC_MAX_ERROR_US``."""
    buffer = Timex()
    buffer.modes = 0                                      # read only
    try:
        state = (adjtimex or _libc_adjtimex)(buffer)
    except Exception:  # noqa: BLE001 — unreadable is "not confirmed", never fine
        return False
    if not isinstance(state, int) or state < 0 or state == TIME_ERROR:
        return False
    if buffer.status & STA_UNSYNC:
        return False
    return 0 <= buffer.maxerror <= SYNC_MAX_ERROR_US


def describe() -> dict[str, Any]:
    """What the kernel says, as kinds — for a probe or a test record, never a decision."""
    buffer = Timex()
    try:
        state = _libc_adjtimex(buffer)
    except Exception as exc:  # noqa: BLE001
        return {"readable": False, "kind": type(exc).__name__}
    return {"readable": True, "state": state, "unsync": bool(buffer.status & STA_UNSYNC),
            "maxerror_within_limit": 0 <= buffer.maxerror <= SYNC_MAX_ERROR_US,
            "confirmed": kernel_sync_confirmed()}
