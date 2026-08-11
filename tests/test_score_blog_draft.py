"""The draft scorer measures what it claims to measure.

Worth testing rather than eyeballing: the whole reason this script exists is that two
drafts looked fine and were both half the required length. A scorer whose own counting is
wrong would reproduce exactly that failure one level up — a confident number nobody checks.

The heuristics (headings, tables, sources) are asserted against hand-built text where the
right answer is not in doubt, so a later tweak to a regex has to face a case that states
what the rule was supposed to be.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import _scripts_bridge  # noqa: F401  (puts scripts/ on sys.path)

from score_blog_draft import STANDARDS, main, measure, scorecard

DRAFT = """미리캔버스 포스터 만들기

가게 이벤트를 알리고 싶은데 디자인 외주 견적이 부담스럽다면, 직접 만드는 편이 빠릅니다.

1단계. 템플릿 고르기

검색창에 이벤트 포스터를 입력하면 완성된 디자인이 나옵니다. 마음에 드는 것을 고르세요.

[캡처: 미리캔버스 템플릿 검색 화면]

항목 | 무료 | 유료

출처: https://example.invalid/guide

#미리캔버스 #포스터제작 #소상공인
"""


def test_character_count_excludes_whitespace():
    assert measure("가 나\n다")["body_chars"] == 3


def test_a_short_standalone_line_is_a_heading_and_a_sentence_is_not():
    m = measure(DRAFT)
    # "미리캔버스 포스터 만들기" and "1단계. 템플릿 고르기" — not the prose paragraphs.
    assert m["headings"] == 2


@pytest.mark.parametrize("paragraph", [
    "이 문장은 마침표로 끝나므로 제목이 아닙니다.",
    "이 문단은 길이가 충분히 길어서 제목으로 보기 어렵고 본문 산문에 해당하는 아주 긴 줄이며 사십자를 넘습니다",
    "[캡처: 화면]",
    "#해시태그",
    # Both of these are short, standalone and unpunctuated — the shape rule alone called
    # them headings, which is how the count came out at 4 on a draft with 2.
    "항목 | 무료 | 유료",
    "출처: https://example.invalid/guide",
])
def test_prose_markers_and_long_lines_are_not_headings(paragraph):
    assert measure(paragraph)["headings"] == 0


def test_a_heading_glued_to_its_prose_is_still_a_heading():
    """The shape real drafts use: a section title, one newline, straight into the sentence.
    Requiring a heading to stand alone in its paragraph scored a four-section draft as zero
    — a false FAIL, the worse direction for a gate."""
    glued = "2. 문구 바꾸기\n템플릿의 글자를 클릭하면 바로 수정할 수 있습니다."
    assert measure(glued)["headings"] == 1


def test_a_mid_paragraph_short_line_is_a_wrapped_clause_not_a_title():
    wrapped = "첫 문장은 충분히 깁니다.\n짧은 줄\n마지막 문장입니다."
    assert measure(wrapped)["headings"] == 0


def test_a_list_is_not_a_stack_of_headings():
    listing = "무료 요금제\n유료 요금제\n기업 요금제"
    assert measure(listing)["headings"] == 0


def test_paragraph_average_excludes_headings():
    """Headings are short by construction; counting them would drag the average below the
    floor and report a failure the body does not have."""
    m = measure(DRAFT)
    assert m["para_chars"] > 20


def test_the_countable_things_are_counted():
    m = measure(DRAFT, keyword="미리캔버스")
    assert m["images"] == 1
    assert m["tables"] == 1
    assert m["sources"] == 1
    assert m["hashtags"] == 3
    assert m["keyword_hits"] == 3  # heading + capture marker + hashtag


def test_keyword_is_skipped_rather_than_scored_as_zero():
    """Reporting 0 hits for a keyword nobody supplied would be a false failure."""
    assert measure(DRAFT)["keyword_hits"] == -1
    lines, _ = scorecard(measure(DRAFT))
    assert any("SKIPPED" in line for line in lines)


# --- the gate ------------------------------------------------------------------------

def _measured(**over):
    base = {"body_chars": 2000, "headings": 5, "paragraphs": 14, "para_chars": 100,
            "images": 5, "tables": 1, "sources": 3, "keyword_hits": 4, "hashtags": 5}
    base.update(over)
    return base


def test_a_compliant_draft_passes():
    _, ok = scorecard(_measured())
    assert ok is True


@pytest.mark.parametrize("key", [k for k, s in STANDARDS.items() if s.critical])
def test_every_critical_miss_fails_the_gate(key):
    _, ok = scorecard(_measured(**{key: 1}))
    assert ok is False, f"{key} is marked critical but a miss did not fail"


def test_a_non_critical_miss_is_reported_but_does_not_fail():
    """Advice that blocks gets ignored; advice that reports gets read."""
    lines, ok = scorecard(_measured(tables=0, hashtags=0))
    assert ok is True
    assert any("under" in line for line in lines)


def test_the_length_floor_is_the_one_that_caught_the_real_drafts():
    """990 and 1,019 chars — the two drafts that looked fine. This is the check that
    would have caught them before they were called finished."""
    _, ok = scorecard(_measured(body_chars=990))
    assert ok is False


def test_the_cli_exits_nonzero_on_a_failing_draft(tmp_path, capsys):
    short = tmp_path / "short.txt"
    short.write_text("짧은 초안입니다.\n\n두 번째 문단.", encoding="utf-8")
    assert main([str(short)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_the_cli_refuses_an_empty_draft(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("   \n", encoding="utf-8")
    assert main([str(empty)]) == 1
