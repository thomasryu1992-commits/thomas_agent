"""Supply-chain pins: every GitHub Action by commit SHA, the Docker base image by digest.

A tag can be moved under us — an Action's `v6` by its owner, `python:3.12-slim` by every upstream
rebuild. Before these pins CI (which pulls) and the host (which builds on its cached copy) were
building on different Python patch releases. Moving a pin is a deliberate PR; a bare tag fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)", re.MULTILINE)
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def test_the_workflows_exist():
    assert WORKFLOWS, "no workflow files found — the pin check would pass vacuously"


def test_every_action_is_pinned_to_a_full_commit_sha():
    unpinned = []
    for path in WORKFLOWS:
        for ref in USES.findall(path.read_text(encoding="utf-8")):
            if ref.startswith("./") or ref.startswith("docker://"):
                continue
            if not PINNED_ACTION.match(ref):
                unpinned.append(f"{path.name}: {ref}")
    assert not unpinned, "actions not pinned to a commit SHA:\n" + "\n".join(unpinned)


def test_every_pinned_action_names_its_version():
    """The SHA is what runs; the trailing comment is what a reader (and an upgrade) needs."""
    missing = []
    for path in WORKFLOWS:
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"uses:\s*\S+@[0-9a-f]{40}", line) and not re.search(r"#\s*v\d", line):
                missing.append(f"{path.name}: {line.strip()}")
    assert not missing, "pinned actions without a version comment:\n" + "\n".join(missing)


def test_the_base_image_is_pinned_by_digest():
    froms = re.findall(r"^FROM\s+(\S+)", (ROOT / "Dockerfile").read_text(encoding="utf-8"), re.MULTILINE)
    assert froms, "Dockerfile has no FROM"
    for image in froms:
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"base image not pinned by digest: {image}"
