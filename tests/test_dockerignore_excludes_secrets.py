"""`.dockerignore` must exclude secret files, including the ones that grew a suffix.

The build context is the repo root. Anything the patterns do not exclude is readable inside the
image by anyone who can pull it, and this repo's root is where the live `.env` sits — venue API
key, model provider keys, bot token.

The miss this pins: `**/.env` matched the live file and nothing beside it, so `.env.bak-pre-605`
(a copy of the same secrets, left by a session about to edit the original) was in the context in
full. The class of failure is a secret file gaining a suffix — `~`, `.bak`, a dated copy — and
the pattern that named the original silently ceasing to match.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERIGNORE = ROOT / ".dockerignore"


def _patterns():
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _excluded(path: str) -> bool:
    """Would docker drop ``path`` from the context?

    Only the two pattern shapes this file uses are implemented — a `**/`-prefixed basename
    match, and a plain prefix (directory or literal). A pattern shape that arrives later and is
    not handled here reads as "not excluded", so the assertions below fail rather than passing
    on a matcher that quietly stopped understanding the file.
    """
    name = path.rsplit("/", 1)[-1]
    for pattern in _patterns():
        if pattern.startswith("!"):
            return False  # a negation would need real precedence handling; refuse to guess
        if pattern.startswith("**/"):
            if fnmatch.fnmatch(name, pattern[3:]):
                return True
        elif path == pattern.rstrip("/") or path.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


@pytest.mark.parametrize("path", [
    # The live files.
    ".env",
    "deploy/.env",
    # The suffixed copies — the actual miss. `.env.bak-pre-605` was present in this repo's root
    # on 2026-08-08 while the pattern was `**/.env`.
    ".env.bak-pre-605",
    ".env.bak",
    ".env~",
    ".env.local",
    ".env.2026-08-08",
    # Credential material, and the same suffix problem applied to it.
    "server.key", "server.key.bak", "client.pem", "client.pem.old", "cert.p12", "cert.p12~",
])
def test_secret_shaped_files_are_excluded_from_the_build_context(path):
    assert _excluded(path), f".dockerignore would ship {path} into the image"


@pytest.mark.parametrize("path", [
    ".runtime_governance_state/crypto/live_outcomes.jsonl",
    "THOMAS_CORE/approvals/anything.yaml",
    "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml",
])
def test_per_machine_state_is_excluded(path):
    """The original reason this file exists — an image must never carry an activation that could
    enable a network call. Asserted alongside the secrets so a future edit cannot widen one set
    while dropping the other."""
    assert _excluded(path)


def test_the_runtime_source_is_still_shipped():
    """The other direction, because a `.dockerignore` that excludes everything also passes every
    assertion above. These paths are what the service actually runs."""
    for path in (
        "runtime/mvp_runtime/crypto/cycle.py",
        "runtime/read_only_kernel/integrity.py",
        "scripts/register_crypto_risk_limits.py",
        "governance/GOVERNANCE_POLICY.yaml",
    ):
        assert not _excluded(path), f".dockerignore drops {path}, which the image needs"


def test_no_tracked_file_is_lost_to_the_widened_secret_patterns():
    """The widening from `**/.env` to `**/.env*` (and the credential extensions likewise) is only
    free if nothing tracked matches. Checked against the index rather than the working tree, so
    an untracked local file cannot make this pass or fail by accident."""
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    secret_patterns = ("**/.env*", "**/*.pem*", "**/*.key*", "**/*.p12*")
    hit = [
        path for path in tracked
        if any(fnmatch.fnmatch(path.rsplit("/", 1)[-1], p[3:]) for p in secret_patterns)
    ]
    assert not hit, f"the secret patterns now exclude tracked files the image may need: {hit}"
