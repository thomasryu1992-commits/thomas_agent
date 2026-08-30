"""The content lane's producer: the thing `blog_content_package.v0.1` never had.

The schema shipped in #645 with a closed shape and no code that builds one. So the lane's
terminal artifact existed as a contract nobody could satisfy, and the one draft the lane ever
produced (2026-08-10) reached its operator as a chat reply — no package, no scorecard, no
record. What is pinned here is that the assembled package actually satisfies that schema, that
the parser truncates at the schema's ceilings instead of failing validation, and that the two
judgements the lane makes for itself — which keyword, and whether the draft cleared the
standards — are recorded rather than implied.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import blog_content
from runtime.mvp_runtime.paths import repo_root
from runtime.read_only_kernel.schema_validation import validate_against_schema

SCHEMA = repo_root() / "schemas" / "blog_content_package.v0.1.schema.json"
NOW = "2026-08-23T09:00:00Z"

DRAFT = """# 미리캔버스로 포스터 만들기

미리캔버스는 무료로 쓸 수 있는 디자인 도구입니다. 처음 여는 사람도 템플릿만 고르면
포스터 한 장을 삼십 분 안에 끝낼 수 있습니다.

## 템플릿 고르기

[캡처: 템플릿 검색창에 '포스터'를 입력한 화면]

검색창에 포스터를 넣으면 비율별로 정리된 템플릿이 나옵니다.

## 글자 바꾸기

글상자를 두 번 누르면 바로 편집됩니다. 폰트는 상단에서 고릅니다.

[캡처: 글상자를 선택해 폰트를 바꾸는 화면]

#미리캔버스 #포스터제작 #무료디자인
"""


def _brief(**over):
    base = {
        "created_at": "2026-08-23T08:55:00Z",
        "degraded": False,
        "degraded_legs": [],
        # The seven fields `run_keyword_brief` actually emits, plus the `competing_posts` it
        # attaches to the rows whose competition leg answered — which the package schema's
        # item does NOT allow. That eighth field is the reason this fixture is verbose.
        "metrics": [
            {"keyword": "미리캔버스 포스터", "monthly_pc": 3000, "monthly_mobile": 6000,
             "monthly_total": 9000, "competition": "중간", "low_volume": False,
             "source": "naver_searchad", "competing_posts": 120},
            {"keyword": "포스터 만들기", "monthly_pc": 7000, "monthly_mobile": 15000,
             "monthly_total": 22000, "competition": "높음", "low_volume": False,
             "source": "naver_searchad", "competing_posts": 900},
            {"keyword": "무료 포스터 템플릿", "monthly_pc": 100, "monthly_mobile": 200,
             "monthly_total": 300, "competition": "낮음", "low_volume": True,
             "source": "naver_searchad"},
        ],
        "trend_points": [{"period": "2026-08-01", "ratio": 55.0}],
    }
    base.update(over)
    return base


# --- the package satisfies its own schema ------------------------------------

def test_the_assembled_package_validates_against_the_closed_schema():
    """The whole point of the producer. A closed schema with `additionalProperties: false`
    rejects a package that carries one key the contract did not name, so assembling by hand
    and hoping is not a strategy."""
    package = blog_content.build_package(
        target_keyword="미리캔버스 포스터", draft=DRAFT, keyword_record=_brief(), now=NOW)
    validate_against_schema(package, SCHEMA, "blog_content_package")          # raises on any drift
    assert package["publish_state"] == "draft"
    assert package["package_id"].startswith("bcp_")


def test_a_draft_with_nothing_extractable_still_produces_a_valid_package():
    """`title_candidates` has minItems 1. A draft with no heading, no marker and no tag must
    still validate, or a plain answer from the model kills the fire at the validator."""
    package = blog_content.build_package(
        target_keyword="포스터", draft="문단 하나뿐인 초안입니다.", keyword_record=None, now=NOW)
    validate_against_schema(package, SCHEMA, "blog_content_package")
    assert package["title_candidates"]
    assert package["keyword_evidence"]["degraded"] is True


@pytest.mark.parametrize("field,cap", [
    ("image_shots", blog_content.MAX_IMAGE_SHOTS),
    ("tags", blog_content.MAX_TAGS),
    ("title_candidates", blog_content.MAX_TITLES),
])
def test_the_parser_truncates_at_the_schemas_ceiling(field, cap):
    """A model that emits 24 capture markers produced a usable draft with too many notes, not
    an invalid one. Truncating deterministically beats failing the fire on the validator."""
    body = "\n\n".join(
        f"## 제목 {i}\n\n문단 {i} 입니다. [캡처: 화면 {i}] #태그{i}" for i in range(40))
    package = blog_content.build_package(
        target_keyword="포스터", draft=body, keyword_record=_brief(), now=NOW)
    assert len(package[field]) <= cap
    validate_against_schema(package, SCHEMA, "blog_content_package")


def test_the_paste_body_carries_no_editor_markers():
    """`body_paste` goes into SmartEditor, which interprets none of this. The markers become
    instructions beside the text instead of noise inside it."""
    package = blog_content.build_package(
        target_keyword="미리캔버스 포스터", draft=DRAFT, keyword_record=_brief(), now=NOW)
    assert "[캡처:" not in package["body_paste"]
    assert "##" not in package["body_paste"]
    assert "#미리캔버스" not in package["body_paste"]
    # ...and they are not lost — each one became an instruction that names its paragraph.
    assert len(package["image_shots"]) == 2
    assert all(isinstance(s["after_paragraph"], int) for s in package["image_shots"])
    assert "미리캔버스" in package["tags"]
    assert [b["action"] for b in package["body_blocks"]] == ["heading", "heading", "heading"]


def test_the_keyword_evidence_is_the_brief_the_run_actually_made():
    package = blog_content.build_package(
        target_keyword="미리캔버스 포스터", draft=DRAFT, keyword_record=_brief(), now=NOW)
    evidence = package["keyword_evidence"]
    assert evidence["as_of"] == "2026-08-23T08:55:00Z"
    assert evidence["total_competing_posts"] == 1020        # 120 + 900
    assert evidence["trend_points"]


def test_the_briefs_extra_column_is_lifted_out_rather_than_passed_through():
    """`run_keyword_brief` attaches `competing_posts` to the rows whose competition leg
    answered, and the schema's metrics item is `additionalProperties: false` without it. Handing
    the rows straight through fails validation every time — nobody had hit it because nothing
    had ever assembled a package. The count is not lost: the schema carries it one level up."""
    package = blog_content.build_package(
        target_keyword="미리캔버스 포스터", draft=DRAFT, keyword_record=_brief(), now=NOW)
    validate_against_schema(package, SCHEMA, "blog_content_package")
    assert all("competing_posts" not in m for m in package["keyword_evidence"]["metrics"])
    assert package["keyword_evidence"]["total_competing_posts"] == 1020


def test_a_row_missing_a_required_column_is_dropped_not_emitted_half():
    """Half a row is not evidence, and emitting one fails the whole package on the validator —
    taking a perfectly good draft down with it."""
    brief = _brief()
    brief["metrics"] = brief["metrics"] + [{"keyword": "반쪽", "monthly_total": 500}]
    package = blog_content.build_package(
        target_keyword="미리캔버스 포스터", draft=DRAFT, keyword_record=brief, now=NOW)
    validate_against_schema(package, SCHEMA, "blog_content_package")
    assert [m["keyword"] for m in package["keyword_evidence"]["metrics"]] == [
        "미리캔버스 포스터", "포스터 만들기", "무료 포스터 템플릿"]


def test_a_degraded_brief_says_which_leg_failed():
    package = blog_content.build_package(
        target_keyword="포스터", draft=DRAFT,
        keyword_record=_brief(degraded=True, degraded_legs=["trend"]), now=NOW)
    validate_against_schema(package, SCHEMA, "blog_content_package")
    assert "trend" in package["keyword_evidence"]["degraded_reason_code"]


# --- which keyword, and why --------------------------------------------------

def test_the_most_searched_winnable_unused_keyword_wins():
    keyword, reasoning = blog_content.select_target_keyword(_brief()["metrics"])
    # 22,000 is bigger but its competition is 높음; 300 is below the reporting floor.
    assert keyword == "미리캔버스 포스터"
    assert reasoning["rule"]


def test_every_exclusion_is_recorded_with_its_reason():
    """A week's choice must be arguable. A rule that silently drops rows is a rule nobody can
    check against the week it produced."""
    _keyword, reasoning = blog_content.select_target_keyword(
        _brief()["metrics"], already_written=["미리캔버스 포스터"])
    excluded = {c["keyword"]: c["excluded_because"] for c in reasoning["considered"]}
    assert excluded["미리캔버스 포스터"] == "already written"
    assert excluded["포스터 만들기"] == "competition high"
    assert excluded["무료 포스터 템플릿"] == "volume below the venue's reporting floor"


def test_no_eligible_keyword_is_an_outcome_not_a_fallback():
    """Drafting against a keyword the rule excluded would be worse than reporting none."""
    keyword, _ = blog_content.select_target_keyword(
        _brief()["metrics"], already_written=["미리캔버스 포스터"])
    assert keyword is None


# --- the operator's override -------------------------------------------------

def test_the_schedule_request_carries_seeds_and_an_optional_target():
    seeds, target = blog_content.parse_seeds("미리캔버스, 포스터제작 , target=스마트스토어 ")
    assert seeds == ["미리캔버스", "포스터제작"]
    assert target == "스마트스토어"


def test_without_an_override_the_rule_decides():
    seeds, target = blog_content.parse_seeds("미리캔버스, 포스터제작")
    assert seeds == ["미리캔버스", "포스터제작"]
    assert target is None


# --- the package renders to files, behind the gate (§J decision 2) ------------------


from runtime.mvp_runtime.safety_gate import FILESYSTEM_WRITE
from runtime.mvp_runtime.workspace import DryRunWriter, RealWorkspaceWriter, workspace_root
from tests._helpers import make_gate_authorization


class _ListLedger:
    def __init__(self):
        self.rows = []

    def append_records(self, trace_id, records):
        for kind, record in records.items():
            self.rows.append({"kind": kind, "trace_id": trace_id, "record": record})


def _package():
    return blog_content.build_package(
        target_keyword="미리캔버스 포스터", draft=DRAFT, keyword_record=_brief(), now=NOW)


def _real_writer():
    return RealWorkspaceWriter(authorization=make_gate_authorization(
        flags=(FILESYSTEM_WRITE,), provider_id="workspace.writer"))


def test_the_paste_file_is_the_paste_body_and_nothing_else():
    package = _package()
    rendered = blog_content.render_paste_txt(package)
    assert rendered == package["body_paste"] + "\n"
    # The §4b founding rule: zero formatting symbols in what gets pasted.
    assert "##" not in rendered and "**" not in rendered


def test_the_reading_file_carries_the_4b_sections_and_the_return_path():
    package = _package()
    post = blog_content.render_post_md(package)
    for section in ("## 선정 근거", "## 제목 후보", "## 본문", "## 발행 전 확인"):
        assert section in post
    # The evidence numbers ride along — a package whose numbers cannot be traced is a guess.
    assert "9000" in post and "naver_searchad" in post
    # ...and the file tells the operator how to close the loop (#802's writer).
    assert package["package_id"] in post and "record_published_url" in post


def test_the_closed_gate_writes_nothing_and_says_so():
    written, files, note = blog_content._write_package_files(
        _package(), writer=DryRunWriter(), ledger=_ListLedger(), now=NOW, repo_root=None)
    assert (written, files) == (False, [])
    assert note == "not enabled on this deployment"


def test_the_open_gate_writes_both_files_and_records_each(tmp_path):
    package = _package()
    ledger = _ListLedger()
    written, files, note = blog_content._write_package_files(
        package, writer=_real_writer(), ledger=ledger, now=NOW, repo_root=tmp_path)
    assert written is True and len(files) == 2
    base = workspace_root(tmp_path)
    post = (base / blog_content.package_dir(package).removeprefix("blog/")).parent  # noqa: F841
    for rel in files:
        target = base / rel
        assert target.is_file(), rel
    assert (base / files[1]).read_text(encoding="utf-8") == blog_content.render_paste_txt(package)
    assert [r["kind"] for r in ledger.rows] == ["write_use", "write_use"]
    assert all(r["trace_id"] == package["package_id"] for r in ledger.rows)
    assert note.startswith("workspace/blog/")


def test_a_refused_write_degrades_instead_of_failing_the_fire(tmp_path):
    package = _package()
    # Pre-create the first target so the create-only rule refuses it.
    rel = blog_content.package_dir(package) + "/POST.md"
    target = workspace_root(tmp_path) / rel
    target.parent.mkdir(parents=True)
    target.write_text("이미 있음", encoding="utf-8")
    written, files, note = blog_content._write_package_files(
        package, writer=_real_writer(), ledger=_ListLedger(), now=NOW, repo_root=tmp_path)
    assert written is False and files == []
    assert "TARGET_EXISTS" in note and "ledger row" in note


def test_the_package_dir_is_dated_slugged_and_collision_free():
    package = _package()
    d = blog_content.package_dir(package)
    assert d.startswith(f"blog/{NOW[:10]}-")
    assert d.endswith(package["package_id"][-4:])
    assert " " not in d and "/" not in d.removeprefix("blog/")
