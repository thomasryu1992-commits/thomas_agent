"""The secrets file and every copy of it stay out of git.

`.env` holds the compose secrets. Sessions back it up in place before editing it, and on 2026-09-28
`.env.bak-20260928-vault` sat untracked in the primary checkout because the rule matched `.env`
exactly. Nothing had committed it yet, but one `git add -A` would have.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", [".env", ".env.bak-20260928-vault", ".env.local", ".env.backup"])
def test_the_env_file_and_its_copies_are_ignored(name):
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.fail("git and a checkout are needed to read the ignore rules")
    result = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", "--no-index", name])
    assert result.returncode == 0, f"{name} is not ignored"
