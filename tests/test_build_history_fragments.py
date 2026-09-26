"""`docs/history/` — one file per delivered increment (system review D6, 2026-09-25).

Two properties keep the directory readable without an aggregate file: names sort by date, so
`ls -r` is newest first, and each file opens with a heading saying what was delivered.
"""

from __future__ import annotations

import re
from pathlib import Path

HISTORY = Path(__file__).resolve().parents[1] / "docs" / "history"
_NAME = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md")


def _entries() -> list[Path]:
    return sorted(p for p in HISTORY.iterdir() if p.name != "README.md")


def test_the_directory_states_its_rule_and_holds_entries():
    assert (HISTORY / "README.md").is_file()
    assert _entries(), "the convention ships with its own first entry"


def test_every_entry_is_named_by_date_and_slug():
    bad = [p.name for p in _entries() if not _NAME.fullmatch(p.name)]
    assert not bad, f"name entries YYYY-MM-DD-<lowercase-slug>.md so `ls -r` is newest first: {bad}"


def test_every_entry_opens_with_a_heading():
    bad = [p.name for p in _entries()
           if not p.read_text(encoding="utf-8").lstrip().startswith("# ")]
    assert not bad, f"open each entry with a '# ' heading saying what was delivered: {bad}"
