#!/usr/bin/env python3
"""Generate ``docs/proposals/STATUS.md`` — every proposal, its lifecycle state, and what waits on Thomas.

The system review (``docs/proposals/SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md`` §2 D3, 2026-09-25)
counted 16 of 37 proposals whose header said DRAFT or "awaiting a Thomas decision", and at least
six of those were already decided **and built** — ``FORWARD_EVIDENCE_CONFIRMATION_V0.1.md`` said
"DRAFT, not implemented, awaiting Thomas" while ``crypto/forward_confirmation.py`` quoted Thomas's
2026-08-11 approval of it. There was no list of open decisions at all; the only way to find one
was to open every proposal and trust a header nothing ever asked to change.

``tests/test_design_record_lifecycle.py`` already solves the same problem for
``docs/runtime-contracts/``: a closed state vocabulary on the header's status line. This extends
that owner rather than adding a gate — the vocabulary, the parser and the renderer live here so the
test and the page cannot disagree about what a status line means, and the test in that file fails
when a header leaves the vocabulary or this page goes stale.

The proposal vocabulary is about the **decision**, not the build, because that is what a proposal
exists to obtain (a runtime contract's PROPOSED/IMPLEMENTED is about the build):

- ``DRAFT``             no decision taken yet — it waits on Thomas.
- ``PARTIALLY DECIDED`` some decision items taken, others open — the summary names the open ones.
- ``DECIDED``           every item decided; what was decided is not (fully) built — the summary
                        names what remains. This column is the build queue that decisions made.
- ``IMPLEMENTED``       decided and built; the document is now a decision trail.
- ``SUPERSEDED``        replaced; the status line names the successor, which must exist.
- ``RECORD``            a measurement or observation record; there is nothing to decide.

A status line is ``**상태:** <STATE> <YYYY-MM-DD> — <summary>`` (``**Status:**`` in an English
record). The date is the decision date, or for ``DRAFT``/``RECORD`` the date of writing; where no
decision record exists, the build or go-live date, and the summary says so. The summary may wrap
onto following lines; it ends at a blank line or the next ``**field:**``.

Run:  python scripts/build_proposal_status.py          # writes the page
      python scripts/build_proposal_status.py --check  # exits 1 if stale
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import sys
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROPOSALS_DIR = ROOT / "docs" / "proposals"
OUTPUT_NAME = "STATUS.md"
OUTPUT_REL = f"docs/proposals/{OUTPUT_NAME}"

# Longest first where one state is a suffix of another, so "PARTIALLY DECIDED" is never read as
# a bare "DECIDED".
STATES = ("DRAFT", "PARTIALLY DECIDED", "DECIDED", "IMPLEMENTED", "SUPERSEDED", "RECORD")
OPEN_STATES = ("DRAFT", "PARTIALLY DECIDED")
STATUS_PREFIXES = ("**상태:**", "**Status:**")

_VALUE = re.compile(
    r"^(?P<state>PARTIALLY DECIDED|DRAFT|DECIDED|IMPLEMENTED|SUPERSEDED|RECORD)"
    r" (?P<date>\d{4}-\d{2}-\d{2}) — (?P<summary>\S.*)$"
)
_SUCCESSOR = re.compile(r"([A-Z][A-Z0-9_]*_V\d+\.\d+\.md)")


class StatusError(ValueError):
    """A proposal's header cannot be read as a lifecycle status."""


class Status(NamedTuple):
    name: str
    title: str
    state: str
    date: str
    summary: str


def proposal_paths(directory: pathlib.Path = PROPOSALS_DIR) -> list[pathlib.Path]:
    """Every proposal. The versioned name is what makes a file a proposal; the generated page is
    the one other file allowed here, and the test fails on anything else rather than skipping it."""
    return sorted(directory.glob("*_V[0-9]*.md"))


def header(text: str) -> str:
    """Everything before the first ``##`` section — the same split the contract check uses."""
    return text.split("\n## ", 1)[0]


def status_lines(text: str) -> list[int]:
    """Indexes (into the header's lines) of every line that opens a status field."""
    return [i for i, line in enumerate(header(text).splitlines()) if line.startswith(STATUS_PREFIXES)]


def parse(path: pathlib.Path) -> Status:
    text = path.read_text(encoding="utf-8")
    lines = header(text).splitlines()
    title = next((line[2:].strip() for line in lines if line.startswith("# ")), path.stem)
    found = status_lines(text)
    if not found:
        raise StatusError(f"{path.name}: no '**상태:**' line in the header")
    if len(found) > 1:
        raise StatusError(
            f"{path.name}: {len(found)} status lines in the header; keep one and rename the "
            "historical one (e.g. '**원래 상태(작성 당시):**')"
        )
    first = lines[found[0]]
    value_parts = [first.split(":**", 1)[1].strip()]
    for line in lines[found[0] + 1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith(("**", ">", "#", "|", "---")):
            break
        value_parts.append(stripped)
    value = " ".join(value_parts)
    match = _VALUE.match(value)
    if not match:
        raise StatusError(
            f"{path.name}: status {value[:80]!r} is not '<STATE> <YYYY-MM-DD> — <summary>' "
            f"with STATE one of {STATES}"
        )
    try:
        datetime.date.fromisoformat(match["date"])
    except ValueError:
        raise StatusError(f"{path.name}: {match['date']!r} is not a real date") from None
    if match["state"] == "SUPERSEDED":
        successors = _SUCCESSOR.findall(match["summary"])
        if not successors:
            raise StatusError(f"{path.name}: SUPERSEDED but names no successor")
        for successor in successors:
            if not (path.parent / successor).is_file():
                raise StatusError(f"{path.name}: names a successor that does not exist: {successor}")
    return Status(path.name, title, match["state"], match["date"], match["summary"].strip())


def collect(directory: pathlib.Path = PROPOSALS_DIR) -> list[Status]:
    return [parse(path) for path in proposal_paths(directory)]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


_SECTIONS = (
    ("Thomas 결정 대기", OPEN_STATES,
     "결정이 하나라도 남은 제안서. 오래 기다린 것부터 적는다."),
    ("결정됨 — 구현 남음", ("DECIDED",),
     "결정은 끝났고 결정된 것이 아직 다 지어지지 않았다. 결정이 만든 구현 대기열이다."),
    ("구현됨", ("IMPLEMENTED",), "결정되고 지어졌다. 문서는 결정의 근거 기록으로 남는다."),
    ("대체됨", ("SUPERSEDED",), "후속 문서가 대신한다."),
    ("측정 기록", ("RECORD",), "결정할 것이 없는 관측 기록."),
)


def render(statuses: list[Status]) -> str:
    counts = {state: sum(1 for s in statuses if s.state == state) for state in STATES}
    out: list[str] = []
    out.append("# 제안서 상태 — 자동 생성, 손으로 고치지 않는다")
    out.append("")
    out.append(f"`python scripts/build_proposal_status.py`로 다시 만든다. 원본은 각 제안서 머리의 "
               f"`**상태:**` 줄이고, `tests/test_design_record_lifecycle.py`가 그 줄이 닫힌 어휘를 "
               f"벗어나거나 이 파일이 원본과 어긋나면 실패한다. 상태가 바뀌면 제안서의 상태 줄을 "
               f"고치고 이 파일을 다시 만든다.")
    out.append("")
    out.append("날짜는 결정일이다. `DRAFT`와 `RECORD`는 작성일이고, 결정 기록을 찾지 못한 것은 "
               "구현·가동일이다(요약에 그렇게 적는다).")
    out.append("")
    out.append(f"제안서 **{len(statuses)}**건: " + " · ".join(
        f"`{state}` {counts[state]}" for state in STATES))
    for heading, states, blurb in _SECTIONS:
        rows = sorted((s for s in statuses if s.state in states), key=lambda s: (s.date, s.name))
        out.append("")
        out.append(f"## {heading} ({len(rows)})")
        out.append("")
        out.append(blurb)
        out.append("")
        if not rows:
            out.append("없음.")
            continue
        out.append("| 제안서 | 상태 | 날짜 | 요약 |")
        out.append("|---|---|---|---|")
        for s in rows:
            out.append(f"| [{s.name}]({s.name}) | `{s.state}` | {s.date} | {_cell(s.summary)} |")
    out.append("")
    return "\n".join(out)


def build(directory: pathlib.Path = PROPOSALS_DIR) -> str:
    return render(collect(directory))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the committed page is stale, writing nothing")
    args = parser.parse_args(argv)
    try:
        rendered = build()
    except StatusError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    target = ROOT / OUTPUT_REL
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != rendered:
            print(f"STALE: {OUTPUT_REL} does not match the proposal headers; "
                  "run scripts/build_proposal_status.py", file=sys.stderr)
            return 1
        print(f"OK: {OUTPUT_REL} is current")
        return 0
    target.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT_REL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
