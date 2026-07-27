"""Shared test scaffolding — the one definition of the local-Core skip guard.

Seventeen test files each carried their own ``LOCAL_POINTER`` + ``requires_local_core``
copy; the next change to how a local Core activation is detected would have meant
seventeen edits (or, worse, sixteen). Import from here instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_POINTER = REPO_ROOT / DEFAULT_POINTER_REL

# Binding-dependent tests skip on a core-neutral checkout; they run on any machine with a
# local Core activation (see the `verify` skill, "Core activation (local, per-machine)").
#
# Not on CI, despite what this comment used to say. Both workflows that run pytest
# (mvp-runtime-tests, thomas-agent-runtime-validation) call
# scripts/ci_activate_core_for_tests.py first, precisely so these run rather than skip —
# that script's own docstring says so. The checkout this skips on is a *developer's*: run
# the suite without a local activation and ~195 tests quietly vanish, which is worth
# knowing before reading a green local run as full coverage.
requires_local_core = pytest.mark.skipif(
    not LOCAL_POINTER.is_file(), reason="no local Core activation")
