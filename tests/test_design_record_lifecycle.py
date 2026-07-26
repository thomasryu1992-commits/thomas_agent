"""A design record must say where it stands, and must not contradict the live flags.

A design record is written before the code and reviewed as a proposal. Then the code lands
and the record keeps its original tense forever, because nothing ever asks it to change.
Found 2026-07-26: `LP4_ORDER_ADAPTER_DESIGN_V0.1.md` still opened with *"No code exists
yet"* and *"`financial_transaction_execution_implemented: false`, `ORDER_PATH_IMPLEMENTED =
False`"* — three months of increments after LP4 shipped and both flags flipped true. The
same was true of `LP5_POSITION_KERNEL_DESIGN_V0.1.md`.

That is not a tidiness problem. Those two sentences are a **safety claim** — "no code here
can send an order" — and they were false. A reader (or a future session) taking the header
at its word would reason from a system that no longer exists.

Two things are pinned here, both mechanical:

1. every design record opens with a lifecycle state from a closed vocabulary, so "is this
   still the plan?" is answerable without reading the whole document;
2. no record's **header** asserts a value for a live governance flag that disagrees with
   the flag's actual value. Scoped to the header on purpose — the body of a record may
   legitimately narrate history ("flipped true on 2026-07-25 when…"), and a check that
   failed on narration would be suppressed within a week.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from runtime.mvp_runtime.crypto.live_readiness import ORDER_PATH_IMPLEMENTED

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "docs" / "runtime-contracts"
POLICY = REPO_ROOT / "governance" / "GOVERNANCE_POLICY.yaml"

# PROPOSED — reviewed but unbuilt. PARTIALLY IMPLEMENTED — some increments landed; the
# record must say which. IMPLEMENTED — the code exists; the record is now a decision trail.
# SUPERSEDED — a later record replaced it, and must be named.
STATES = ("PROPOSED", "PARTIALLY IMPLEMENTED", "IMPLEMENTED", "SUPERSEDED")

DESIGN_RECORDS = sorted(CONTRACTS.glob("*_DESIGN_*.md"))

# The flags a design record is most likely to quote, and where the truth lives. Both are
# "can this repo place a live order at all", which is why a stale copy is worth failing on.
_POLICY_FLAG = "financial_transaction_execution_implemented"
_CODE_FLAG = "ORDER_PATH_IMPLEMENTED"


def _header(text: str) -> str:
    """Everything before the first ``##`` section — the status/authority block."""
    return text.split("\n## ", 1)[0]


def _status_line(text: str) -> str | None:
    for line in _header(text).splitlines():
        if line.startswith("**Status:**"):
            return line[len("**Status:**"):].strip()
    return None


def _policy_flag_value() -> bool:
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    financial = policy.get("financial_authority") or {}
    if _POLICY_FLAG in financial:
        return bool(financial[_POLICY_FLAG])
    # The block moved: find it wherever it lives rather than silently passing.
    found = re.search(rf"^\s*{_POLICY_FLAG}:\s*(true|false)\s*$",
                      POLICY.read_text(encoding="utf-8"), re.M)
    assert found, f"{_POLICY_FLAG} not found in the governance policy"
    return found.group(1) == "true"


def test_there_are_design_records_to_check():
    """A glob that matches nothing would make every test below vacuously green."""
    assert DESIGN_RECORDS, "no *_DESIGN_*.md under docs/runtime-contracts/"


@pytest.mark.parametrize("path", DESIGN_RECORDS, ids=lambda p: p.name)
def test_a_design_record_opens_with_a_lifecycle_state(path):
    status = _status_line(path.read_text(encoding="utf-8"))
    assert status is not None, f"{path.name} has no '**Status:**' line in its header"
    assert status.startswith(STATES), (
        f"{path.name} opens with {status!r}; a design record must start its status with one "
        f"of {STATES} so 'is this still the plan?' is answerable at a glance"
    )


@pytest.mark.parametrize("path", DESIGN_RECORDS, ids=lambda p: p.name)
def test_a_superseded_record_names_its_successor(path):
    status = _status_line(path.read_text(encoding="utf-8")) or ""
    if not status.startswith("SUPERSEDED"):
        return
    referenced = re.findall(r"`([A-Z0-9_.]+\.md)`", status)
    assert referenced, f"{path.name} is SUPERSEDED but names no successor"
    for name in referenced:
        assert (CONTRACTS / name).is_file(), f"{path.name} names a successor that does not exist: {name}"


@pytest.mark.parametrize("path", DESIGN_RECORDS, ids=lambda p: p.name)
def test_a_header_never_contradicts_the_live_order_path_flags(path):
    """The specific false claim that prompted this file: a header asserting the order path
    does not exist, long after it did."""
    header = _header(path.read_text(encoding="utf-8"))

    for quoted in re.findall(rf"{_POLICY_FLAG}:\s*(true|false)", header):
        assert (quoted == "true") is _policy_flag_value(), (
            f"{path.name} header says {_POLICY_FLAG}: {quoted}, but the governance policy "
            f"says {str(_policy_flag_value()).lower()}"
        )

    for quoted in re.findall(rf"{_CODE_FLAG}\s*=\s*(True|False)", header):
        assert (quoted == "True") is ORDER_PATH_IMPLEMENTED, (
            f"{path.name} header says {_CODE_FLAG} = {quoted}, but the code says "
            f"{ORDER_PATH_IMPLEMENTED}"
        )


@pytest.mark.parametrize("path", DESIGN_RECORDS, ids=lambda p: p.name)
def test_a_header_does_not_claim_unwritten_code_once_the_code_exists(path):
    """'No code exists yet' is the other half of the same false claim, in prose. Only
    checked for records that declare themselves implemented — a PROPOSED record saying it
    has no code is exactly right.

    Claims about **existence** only. *"Nothing here enables trading"* is deliberately not on
    this list: it is a claim about authority, and it stays true for every record in this
    stack however much code ships, because `financial_executor_enabled` is false and acting
    needs a per-machine grant. Conflating the two would force a record to delete a sentence
    that is both true and the most important one in it — and "the code exists" vs "this
    machine may act" is exactly the distinction the R8/R10 precedent keeps separate.
    """
    text = path.read_text(encoding="utf-8")
    status = _status_line(text) or ""
    if status.startswith("PROPOSED"):
        return
    header = _header(text).lower()
    for claim in ("no code exists yet", "no code exists", "no code;", "no code is written"):
        assert claim not in header, (
            f"{path.name} is {status.split(chr(32))[0]} but its header still claims {claim!r}"
        )
