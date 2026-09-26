"""A stale safety claim about the live-order flags, wherever it is written.

A design record is written before the code and reviewed as a proposal. Then the code lands
and the record keeps its original tense forever, because nothing ever asks it to change.
Found 2026-07-26: `LP4_ORDER_ADAPTER_DESIGN_V0.1.md` still opened with *"No code exists
yet"* and *"`financial_transaction_execution_implemented: false`, `ORDER_PATH_IMPLEMENTED =
False`"* — three months of increments after LP4 shipped and both flags flipped true. The
same was true of `LP5_POSITION_KERNEL_DESIGN_V0.1.md`.

That is not a tidiness problem. Those two sentences are a **safety claim** — "no code here
can send an order" — and they were false. A reader (or a future session) taking the header
at its word would reason from a system that no longer exists.

Three things are pinned here, all mechanical:

1. every design record opens with a lifecycle state from a closed vocabulary, so "is this
   still the plan?" is answerable without reading the whole document;
2. no record's **header** asserts a value for a live governance flag that disagrees with
   the flag's actual value. Scoped to the header on purpose — the body of a record may
   legitimately narrate history ("flipped true on 2026-07-25 when…"), and a check that
   failed on narration would be suppressed within a week;
3. no **runtime module docstring** asserts one of those flags is off while it is on.

(3) exists because the first gate was scoped to `docs/` and the same claim was sitting in
code the whole time. Found 2026-07-26 in `crypto/live_execution.py` — the one module that
can actually send an order — whose docstring still read *"``ORDER_PATH_IMPLEMENTED`` and
``financial_transaction_execution_implemented`` deliberately stay **OFF** in this increment
— the readiness board therefore cannot report READY, so no autonomous path routes here."*
Both flags had been true since 2026-07-25. Note that (2)'s regexes would **not** have caught
it: the claim was prose ("stay OFF"), never `flag: false` syntax, which is why (3) matches a
closed vocabulary of off-words rather than a value literal.

A module docstring has no header/body split, so (3) borrows the same trick at the next
granularity down: it scopes to the **paragraph** naming the flag. That is what keeps
`crypto/live_governance.py` green — its docstring says the flag *"had already been flipped
to ``true``"*, which is history, in a paragraph containing no off-word. And the check only
fires for a flag that is currently **on**: a docstring saying a false flag is off is right.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Callable

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


def _policy_flag_value(name: str = _POLICY_FLAG) -> bool:
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    financial = policy.get("financial_authority") or {}
    if name in financial:
        return bool(financial[name])
    # The block moved: find it wherever it lives rather than silently passing.
    found = re.search(rf"^\s*{name}:\s*(true|false)\s*$",
                      POLICY.read_text(encoding="utf-8"), re.M)
    assert found, f"{name} not found in the governance policy"
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


# --- (3) the same claim, in code --------------------------------------------------------

RUNTIME = REPO_ROOT / "runtime"

# The flags whose "off" is a safety claim, and where each one's truth lives. Read lazily so
# a collection-time import can never decide the answer.
GOVERNED_FLAGS: dict[str, Callable[[], bool]] = {
    "ORDER_PATH_IMPLEMENTED": lambda: ORDER_PATH_IMPLEMENTED,
    "financial_transaction_execution_implemented": _policy_flag_value,
    "financial_executor_enabled": lambda: _policy_flag_value("financial_executor_enabled"),
}

# Present-tense ways of saying "this is not on". Closed on purpose: a broad match (e.g. bare
# "disabled", or any occurrence of "false") fires on ordinary narration, and a check that
# cries wolf gets deleted. Every entry here is a claim that is simply wrong once the flag it
# sits beside is true.
OFF_CLAIMS = (
    "stay off", "stays off", "staying off", "remain off", "remains off",
    "is off", "are off", "kept off", "left off", "turned off", "switched off",
    "= false", ": false", "is false", "are false",
    "not implemented", "does not exist", "no code exists",
)

RUNTIME_MODULES = sorted(RUNTIME.rglob("*.py"))


def _claim_units(doc: str) -> list[str]:
    """The docstring split into the units a claim is scoped to.

    Blank-line paragraphs, then each bullet within a paragraph on its own — a module
    docstring has no header/body split, so this is the finest honest scope. Splitting
    bullets matters: a five-bullet list is one paragraph, and without this a true statement
    about one flag would be read as sitting beside another flag's name.
    """
    units: list[str] = []
    for block in re.split(r"\n\s*\n", doc):
        current: list[str] = []
        for line in block.splitlines():
            if re.match(r"\s*[-*]\s+\S", line) and current:
                units.append("\n".join(current))
                current = []
            current.append(line)
        if current:
            units.append("\n".join(current))
    return units


def _normalize(unit: str) -> str:
    """Strip reST/Markdown emphasis so ``stay **OFF**`` reads as ``stay off``.

    The claim that prompted this check was bolded mid-phrase, which is exactly how a
    literal substring search misses the sentence a human cannot miss.

    Backticks and asterisks only — **not** underscores, though they are also an emphasis
    marker. Every flag this file looks for is a snake_case identifier, so stripping ``_``
    turns ``financial_transaction_execution_implemented`` into a word that appears in no
    docstring, and the check silently matches nothing. It did exactly that when first
    written, and passed against the very docstring it was built to fail on.
    """
    return re.sub(r"\s+", " ", re.sub(r"[`*]", "", unit)).strip().lower()


def test_there_are_runtime_modules_to_check():
    """As above: a glob that matches nothing is a green test that checks nothing."""
    assert RUNTIME_MODULES, "no *.py under runtime/"


@pytest.mark.parametrize("path", RUNTIME_MODULES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_a_module_docstring_never_says_a_live_flag_is_off_while_it_is_on(path):
    try:
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
    except SyntaxError:  # not this test's job to report — the suite has compilers for that
        return
    if not doc:
        return
    rel = path.relative_to(REPO_ROOT)
    for unit in _claim_units(doc):
        normalized = _normalize(unit)
        for flag, actual in GOVERNED_FLAGS.items():
            if flag.lower() not in normalized or not actual():
                continue
            for claim in OFF_CLAIMS:
                assert claim not in normalized, (
                    f"{rel} docstring says {claim!r} beside {flag}, which is currently ON.\n"
                    f"  in: {unit.strip()[:300]}\n"
                    f"A module docstring is where the next reader forms their model of what "
                    f"this code can do; saying a live flag is off while it is on is the same "
                    f"false safety claim this file already pins in docs/."
                )


# --- (4) the same vocabulary, for proposals -----------------------------------------------
#
# The system review (`docs/proposals/SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md` §2 D3, 2026-09-25)
# found the failure (1) exists to prevent, one directory over: 16 of 37 proposals said DRAFT or
# "awaiting a Thomas decision", at least six of them decided and built, and no list anywhere of
# what actually waited on Thomas. A proposal's vocabulary is about the *decision* rather than the
# build, so it is its own closed set — kept with its parser and the generated page in
# `scripts/build_proposal_status.py`, so this test and the page cannot read a line differently.

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_proposal_status as proposal_status  # noqa: E402

PROPOSALS = proposal_status.proposal_paths()


def test_there_are_proposals_to_check():
    assert PROPOSALS, "no *_V<n>.md under docs/proposals/"


def test_every_file_in_proposals_is_a_proposal_or_the_generated_page():
    """The glob is what makes a file a proposal. An unversioned proposal would be skipped by it —
    silently, which is the one way this check could go green while a header rots."""
    others = sorted(
        p.name for p in proposal_status.PROPOSALS_DIR.iterdir()
        if p.is_file() and p not in PROPOSALS and p.name != proposal_status.OUTPUT_NAME
    )
    assert not others, f"files in docs/proposals/ that are not versioned proposals: {others}"


@pytest.mark.parametrize("path", PROPOSALS, ids=lambda p: p.name)
def test_a_proposal_opens_with_one_lifecycle_state(path):
    try:
        proposal_status.parse(path)
    except proposal_status.StatusError as exc:
        pytest.fail(
            f"{exc}\nA proposal header carries exactly one "
            f"'**상태:** <STATE> <YYYY-MM-DD> — <summary>' line, STATE one of "
            f"{proposal_status.STATES}; see scripts/build_proposal_status.py for what each means."
        )


def test_the_parser_refuses_what_it_cannot_read(tmp_path):
    """The rules above, each shown failing — a parser that accepts anything is a green test that
    checks nothing."""
    cases = {
        "NO_STATUS_V0.1.md": "# t\n\nbody\n",
        "OLD_WORDING_V0.1.md": "# t\n\n**상태:** DRAFT — no date\n",
        "UNKNOWN_STATE_V0.1.md": "# t\n\n**상태:** APPROVED 2026-08-01 — built\n",
        "BAD_DATE_V0.1.md": "# t\n\n**상태:** DECIDED 2026-13-01 — x\n",
        "TWO_LINES_V0.1.md": "# t\n\n**상태:** DRAFT 2026-08-01 — a\n**상태:** DECIDED 2026-08-02 — b\n",
        "ORPHAN_V0.1.md": "# t\n\n**상태:** SUPERSEDED 2026-08-01 — see `GONE_V0.2.md`\n",
        "NAMELESS_V0.1.md": "# t\n\n**상태:** SUPERSEDED 2026-08-01 — replaced\n",
    }
    for name, text in cases.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
        with pytest.raises(proposal_status.StatusError):
            proposal_status.parse(tmp_path / name)


def test_the_parser_reads_a_wrapped_line_and_keeps_partially_decided_apart(tmp_path):
    (tmp_path / "NEXT_V0.2.md").write_text(
        "# next\n\n**Status:** DRAFT 2026-08-02 — x\n", encoding="utf-8")
    (tmp_path / "WRAPPED_V0.1.md").write_text(
        "# 제목\n\n**상태:** PARTIALLY DECIDED 2026-08-10 — D4 채택,\nD5·D6 미결.\n"
        "**작성:** 2026-08-10\n\n## 1\n\n**상태:** DRAFT 2026-08-01 — body lines are not the header\n",
        encoding="utf-8",
    )
    (tmp_path / "OLD_V0.1.md").write_text(
        "# old\n\n**상태:** SUPERSEDED 2026-08-03 — `NEXT_V0.2.md`가 대신한다\n", encoding="utf-8")
    wrapped = proposal_status.parse(tmp_path / "WRAPPED_V0.1.md")
    assert (wrapped.state, wrapped.date, wrapped.title) == ("PARTIALLY DECIDED", "2026-08-10", "제목")
    assert wrapped.summary == "D4 채택, D5·D6 미결."
    assert proposal_status.parse(tmp_path / "OLD_V0.1.md").state == "SUPERSEDED"

    page = proposal_status.build(tmp_path)
    waiting = page.split("## Thomas 결정 대기", 1)[1].split("\n## ", 1)[0]
    assert "WRAPPED_V0.1.md" in waiting and "NEXT_V0.2.md" in waiting
    assert "OLD_V0.1.md" not in waiting


def test_the_committed_status_page_matches_the_headers():
    """The page is the list of open decisions the review found missing. A copy that can go stale
    is the stale header again, one file up."""
    committed = (REPO_ROOT / proposal_status.OUTPUT_REL).read_text(encoding="utf-8")
    assert committed == proposal_status.build(), (
        f"{proposal_status.OUTPUT_REL} is stale — run `python scripts/build_proposal_status.py`"
    )
