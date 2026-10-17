"""CLOCK-SOURCE = A (Thomas 2026-10-17): the actor's clock is confirmed only by the kernel's own NTP state.

Synthetic: ``adjtimex`` is injected, so every answer the kernel can give is held to its verdict without
touching the host clock. One test calls the real one, on Linux only, and asserts the answer is a verdict —
never what this host's clock happens to say.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

from runtime.mvp_runtime.deploy_runner import clock_source as cs


def kernel(state=0, status=0, maxerror=150_000, raises=None):
    def adjtimex(buffer):
        assert buffer.modes == 0                                   # a read: never a mode that sets anything
        if raises is not None:
            raise raises
        buffer.status = status
        buffer.maxerror = maxerror
        return state
    return adjtimex


def test_a_synchronised_kernel_within_the_limit_is_confirmed():
    assert cs.kernel_sync_confirmed(adjtimex=kernel()) is True
    assert cs.kernel_sync_confirmed(adjtimex=kernel(maxerror=cs.SYNC_MAX_ERROR_US)) is True
    assert cs.kernel_sync_confirmed(adjtimex=kernel(state=1)) is True          # TIME_INS: a leap second ahead


@pytest.mark.parametrize("answer", [
    kernel(status=cs.STA_UNSYNC),                                   # the kernel says unsynchronised
    kernel(status=cs.STA_UNSYNC | 0x2001),
    kernel(state=cs.TIME_ERROR),
    kernel(state=-1),                                               # the call failed
    kernel(maxerror=cs.SYNC_MAX_ERROR_US + 1),                      # past the kernel's own limit
    kernel(maxerror=-1),
    kernel(raises=OSError("no adjtimex")),
    kernel(raises=AttributeError("no symbol")),
    lambda buffer: "0",                                             # not an int
])
def test_anything_else_is_not_confirmed(answer):
    assert cs.kernel_sync_confirmed(adjtimex=answer) is False


def test_the_buffer_is_large_enough_for_the_kernel_to_write_into():
    # struct timex is 208 bytes on LP64 Linux; a short buffer would let the kernel write past it.
    assert ctypes.sizeof(cs.Timex) >= 208


def test_off_linux_it_is_not_confirmed(monkeypatch):
    monkeypatch.setattr(cs.sys, "platform", "win32")
    assert cs.kernel_sync_confirmed() is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="adjtimex is Linux-only")
def test_the_real_kernel_gives_a_verdict():
    assert cs.kernel_sync_confirmed() in (True, False)
    described = cs.describe()
    assert described["readable"] is True and set(described) == {"readable", "state", "unsync",
                                                                 "maxerror_within_limit", "confirmed"}
