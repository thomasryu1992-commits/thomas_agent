#!/usr/bin/env python3
"""Score a Naver blog draft against the lane's operating standards.

**These numbers are not Naver's published cutoffs.** They are Thomas's practical operating
standards for this blog (stated 2026-08-10), and this file is where they live — one place,
checkable, rather than a table copied into a doc, a prompt and a reviewer's head. A doc
copy would drift the first time a number moved; a prompt copy already exists by design
(the request tells the model what to aim for) and this script is what says whether it hit.

Why a script at all: the first two drafts the lane produced both looked fine and were both
**half the minimum length** — 990 and 1,019 characters against a 1,800 floor. Nobody
noticed until it was measured. Reading a draft does not reveal its length; counting does.

Usage::

    python scripts/score_blog_draft.py draft.txt --keyword "미리캔버스 포스터"
    cat draft.txt | python scripts/score_blog_draft.py - --keyword "미리캔버스 포스터"

Exit code is 0 when every ``critical`` criterion passes, 1 otherwise — so it can gate a
publish step. Non-critical misses are reported and do not fail the run: they are advice,
and a script that failed on advice would teach its user to ignore it.

Measurement honesty: character counts and paragraph splits are exact. Headings, tables and
sources are **heuristics** over plain text — the paste format carries no markup by design
(SmartEditor does not interpret markdown), so there is nothing unambiguous to count. Each
heuristic names itself in the output so a surprising number can be argued with rather than
believed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The standards and the measurement moved into the runtime on 2026-08-23 so the lane can score
# its own drafts as it produces them (`runtime/mvp_runtime/blog_draft_score.py`). Re-exported
# here rather than duplicated: one place the numbers live was the point of this file.
#
# The bootstrap below is what keeps `python scripts/score_blog_draft.py` — the usage this
# file's own docstring documents, and how it has been run since 2026-08-10 — working after
# that move. Running a script by path puts `scripts/` on `sys.path` and not the repo root, so
# without it the import dies on `ModuleNotFoundError: No module named 'runtime'`; CLAUDE.md
# records that failure and prescribes the `python -m scripts.<name>` form, and twenty other
# scripts under this directory carry exactly this three-line bootstrap so both forms work.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.blog_draft_score import (  # noqa: E402
    STANDARDS,
    STANDARDS_VERSION,
    Standard,
    measure,
    scorecard,
)

__all__ = ["STANDARDS", "STANDARDS_VERSION", "Standard", "main", "measure", "scorecard"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("draft", help="path to the draft text, or - for stdin")
    parser.add_argument("--keyword", default=None,
                        help="target keyword, to count its repetitions in the body")
    args = parser.parse_args(argv)

    text = sys.stdin.read() if args.draft == "-" else Path(args.draft).read_text(encoding="utf-8")
    if not text.strip():
        print("EMPTY: nothing to score", file=sys.stderr)
        return 1

    measured = measure(text, args.keyword)
    lines, ok = scorecard(measured)
    print("네이버 블로그 초안 채점 (* = 통과 필수)")
    print()
    for line in lines:
        print(line)
    print()
    print("PASS — 발행 가능" if ok else "FAIL — 필수 기준 미달 (위 MISS 항목)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
