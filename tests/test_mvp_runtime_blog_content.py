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

from pathlib import Path

import pytest

from runtime.mvp_runtime import blog_content
from runtime.mvp_runtime.paths import repo_root
from runtime.read_only_kernel.schema_validation import validate_against_schema
from tests._helpers import requires_local_core

SCHEMA = repo_root() / "schemas" / "blog_content_package.v0.3.schema.json"
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


def _target_brief(target="미리캔버스 포스터", **over):
    """The content run's own brief on the target: Search Ad hands the target's row back
    space-stripped, and it is NOT the top-volume row."""
    base = {
        "created_at": "2026-08-23T08:58:00Z",
        "degraded": False,
        "degraded_legs": {},
        "metrics": [
            {"keyword": "미리캔버스", "monthly_pc": 40000, "monthly_mobile": 90000,
             "monthly_total": 130000, "competition": "높음", "low_volume": False,
             "source": "naver_searchad", "competing_posts": 1_750_000,
             "competing_posts_query": "미리캔버스"},
            {"keyword": "미리캔버스포스터", "monthly_pc": 3000, "monthly_mobile": 6000,
             "monthly_total": 9000, "competition": "중간", "low_volume": False,
             "source": "naver_searchad", "competing_posts": 120,
             "competing_posts_query": target},
        ],
        "trend_keyword": target,
        "trend_points": [{"period": "2026-08-01", "ratio": 55.0}],
    }
    base.update(over)
    return base


def _build(draft=DRAFT, *, target="미리캔버스 포스터", selection_record=None, target_record=None):
    selection_record = _brief() if selection_record is None else selection_record
    target_record = _target_brief(target) if target_record is None else target_record
    _k, reasoning = blog_content.select_target_keyword(selection_record.get("metrics") or [])
    interpreted = blog_content.interpret_draft(draft, target)
    return blog_content.build_package(
        target_keyword=target, draft=interpreted,
        quality=blog_content.quality_record(interpreted, first_failures=interpreted["failures"],
                                            revision_count=0, revision_outcome=None),
        selection=blog_content.selection_evidence(
            selection_record, reasoning, selected_keyword=target, mode="rule",
            seeds=["미리캔버스 포스터"], now=NOW),
        target=blog_content.target_evidence(target, target_record or None, now=NOW),
        lineage={"selection_research_trace_id": "trace-sel", "target_research_trace_id": "trace-c",
                 "content_trace_id": "trace-c", "revision_trace_id": None},
        now=NOW)


# --- the package satisfies its own schema ------------------------------------

def test_the_assembled_package_validates_against_the_closed_schema():
    """The whole point of the producer. A closed schema with `additionalProperties: false`
    rejects a package that carries one key the contract did not name, so assembling by hand
    and hoping is not a strategy."""
    package = _build()
    validate_against_schema(package, SCHEMA, "blog_content_package")          # raises on any drift
    assert package["publish_state"] == "draft"
    assert package["package_id"].startswith("bcp_")


def test_a_draft_with_nothing_extractable_still_produces_a_valid_package():
    """`title_candidates` has minItems 1. A draft with no heading, no marker and no tag must
    still validate, or a plain answer from the model kills the fire at the validator."""
    package = _build("문단 하나뿐인 초안입니다.", target="포스터", target_record={})
    validate_against_schema(package, SCHEMA, "blog_content_package")
    assert package["title_candidates"]
    assert package["target_evidence"]["status"] == "missing"
    assert package["target_evidence"]["degraded"] is True


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
    package = _build(body, target="포스터")
    assert len(package[field]) <= cap
    validate_against_schema(package, SCHEMA, "blog_content_package")


def test_the_paste_body_carries_no_editor_markers():
    """`body_paste` goes into SmartEditor, which interprets none of this. The markers become
    instructions beside the text instead of noise inside it."""
    package = _build()
    assert "[캡처:" not in package["body_paste"]
    assert "##" not in package["body_paste"]
    assert "#미리캔버스" not in package["body_paste"]
    # ...and they are not lost — each one became an instruction that names its paragraph.
    assert len(package["image_shots"]) == 2
    assert all(isinstance(s["after_paragraph"], int) for s in package["image_shots"])
    assert "미리캔버스" in package["tags"]
    assert [b["action"] for b in package["body_blocks"]] == ["heading", "heading", "heading"]


def test_the_target_evidence_is_the_targets_own_row_not_the_top_volume_row():
    """`metrics[0]` of the target's brief is '미리캔버스' (130,000/mo); the target's own row is
    the space-stripped '미리캔버스포스터' (9,000/mo). Only the latter describes the target."""
    evidence = _build()["target_evidence"]
    assert evidence["status"] == "measured"
    assert evidence["matched_keyword"] == "미리캔버스포스터"
    assert (evidence["monthly_pc"], evidence["monthly_mobile"], evidence["monthly_total"]) == (
        3000, 6000, 9000)
    assert evidence["ad_competition"] == "중간"
    assert evidence["as_of"] == "2026-08-23T08:58:00Z"      # the target brief, not selection's


def test_the_blog_post_count_is_the_targets_alone_never_a_sum():
    """v0.1 summed the top three rows — three different keywords — into one 'competing posts'
    number (120 + 900 = 1,020 in this fixture). The target's count is 120, from its own row."""
    package = _build()
    evidence = package["target_evidence"]
    assert evidence["blog_competing_posts"] == 120
    assert evidence["blog_competing_posts_query"] == "미리캔버스 포스터"
    assert 1020 not in [evidence.get("blog_competing_posts")]
    assert "total_competing_posts" not in evidence
    validate_against_schema(package, SCHEMA, "blog_content_package")


def test_the_trend_belongs_to_the_target_or_is_absent():
    assert _build()["target_evidence"]["trend_keyword"] == "미리캔버스 포스터"
    other = _build(target_record=_target_brief(trend_keyword="미리캔버스"))["target_evidence"]
    assert "trend_points" not in other and "TARGET_TREND_ABSENT" in other["degraded_reason_code"]


def test_a_missing_target_row_is_missing_not_a_neighbours_numbers():
    brief = _target_brief()
    brief["metrics"] = brief["metrics"][:1]                  # only '미리캔버스' came back
    package = _build(target_record=brief)
    evidence = package["target_evidence"]
    assert evidence["status"] == "missing"
    assert evidence["degraded_reason_code"].startswith("TARGET_ROW_ABSENT")
    for field in ("monthly_total", "monthly_pc", "blog_competing_posts", "ad_competition"):
        assert field not in evidence
    validate_against_schema(package, SCHEMA, "blog_content_package")
    post = blog_content.render_post_md(package)
    assert "130000" not in post and "1750000" not in post.split("## 선정 근거")[0]
    assert "타깃 키워드 자체의 검색량 행이 조사 결과에 없다" in post


def test_a_target_row_without_its_counts_is_not_read_as_zero_demand():
    brief = _target_brief()
    brief["metrics"][1] = {"keyword": "미리캔버스포스터", "monthly_total": 9000}
    evidence = _build(target_record=brief)["target_evidence"]
    assert evidence["status"] == "missing"
    assert evidence["degraded_reason_code"].startswith("TARGET_ROW_INCOMPLETE")
    assert "monthly_pc" not in evidence


def test_the_selection_evidence_keeps_the_candidates_under_their_own_names():
    selection = _build()["selection_evidence"]
    assert selection["mode"] == "rule" and selection["selected_keyword"] == "미리캔버스 포스터"
    names = {c["keyword"]: c for c in selection["candidates"]}
    assert names["포스터 만들기"]["ad_competition"] == "높음"
    assert names["포스터 만들기"]["blog_competing_posts"] == 900


def test_a_degraded_target_brief_says_which_leg_failed():
    package = _build(target_record=_target_brief(degraded=True, degraded_legs={"trend": "X"}))
    validate_against_schema(package, SCHEMA, "blog_content_package")
    assert "trend" in package["target_evidence"]["degraded_reason_code"]


def test_the_lineage_names_real_traces_and_null_for_runs_that_did_not_happen():
    lineage = dict(_build()["lineage"])
    prompt = lineage.pop("prompt")
    assert lineage == {"selection_research_trace_id": "trace-sel",
                       "target_research_trace_id": "trace-c", "content_trace_id": "trace-c",
                       "revision_trace_id": None}
    # v0.3: which prompt, profile, standards and schema produced it (no request was sent here).
    assert prompt["platform"] == "naver" and prompt["schema_version"] == blog_content.PACKAGE_SCHEMA_VERSION
    assert prompt["request_sha256"] is None and prompt["revision_request_sha256"] is None


def as_v02_row(package):
    """What a ledger row written before 2026-10-05 looks like: the same package without
    v0.3's additions. Every such row is a Naver package."""
    row = {k: v for k, v in package.items()
           if k not in ("platform", "content_intent", "overlap", "platform_metadata")}
    row["schema_version"] = "blog_content_package.v0.2"
    row["lineage"] = {k: v for k, v in package["lineage"].items() if k != "prompt"}
    row["quality"] = {k: v for k, v in package["quality"].items() if k != "layers"}
    return row


def test_a_v0_2_package_row_stays_valid_and_reads_as_naver():
    """v0.3 is a new version: the rows already in the ledger are not rewritten, still satisfy
    their own schema, and every reader takes them for what they were — Naver packages."""
    row = as_v02_row(_build())
    validate_against_schema(row, blog_content.package_schema_path("blog_content_package.v0.2"),
                            "blog_content_package")
    assert blog_content.package_platform(row) == "naver"
    assert blog_content.package_dir(row).startswith("blog/2026-")


def test_a_v0_1_package_row_stays_valid_against_its_own_schema():
    """v0.2 is a new version, not a reinterpretation: the old contract still holds its rows."""
    v01 = repo_root() / "schemas" / "blog_content_package.v0.1.schema.json"
    assert blog_content.package_schema_path("blog_content_package.v0.1") == v01
    from tests.test_blog_content_package_schema import VALID
    validate_against_schema(VALID, v01, "blog_content_package")


# --- which keyword, and why --------------------------------------------------

def test_the_most_searched_unused_keyword_wins_whatever_its_ad_competition():
    """`compIdx` is advertiser bid competition, not blog difficulty (§J). 22,000 used to lose to
    9,000 because Search Ad rated it 높음; it wins now, and 300 is still below the floor."""
    keyword, reasoning = blog_content.select_target_keyword(_brief()["metrics"])
    assert keyword == "포스터 만들기"
    assert reasoning["rule"]


def test_every_exclusion_is_recorded_with_its_reason():
    """A week's choice must be arguable. A rule that silently drops rows is a rule nobody can
    check against the week it produced."""
    _keyword, reasoning = blog_content.select_target_keyword(
        _brief()["metrics"], already_written=["미리캔버스 포스터"])
    excluded = {c["keyword"]: c["excluded_because"] for c in reasoning["considered"]}
    assert excluded["미리캔버스 포스터"].startswith("already written")
    assert excluded["포스터 만들기"] is None
    assert excluded["무료 포스터 템플릿"] == "volume below the venue's reporting floor"
    # The two competition columns ride along, under names that say what they measure.
    row = next(c for c in reasoning["considered"] if c["keyword"] == "포스터 만들기")
    assert row["ad_competition"] == "높음" and row["blog_competing_posts"] == 900


def test_no_eligible_keyword_is_an_outcome_not_a_fallback():
    """Drafting against a keyword the rule excluded would be worse than reporting none."""
    keyword, _ = blog_content.select_target_keyword(
        _brief()["metrics"], already_written=["미리캔버스 포스터", "포스터 만들기"])
    assert keyword is None


def test_a_keyword_already_drafted_is_read_off_the_ledger_archives_included(tmp_path):
    """The weekly schedule never passed ``already_written``, so the exclusion above was dead and
    every fire could re-draft last week's keyword. The ledger's packages are the record of what
    was drafted — and packages are records, which rotate, so the archive counts too."""
    from runtime.mvp_runtime import retention
    from runtime.mvp_runtime.store import RECORDS_FILE, LedgerStore

    ledger = LedgerStore(tmp_path / "ledger")
    package = _package()
    ledger.append_records(package["package_id"], {blog_content.PACKAGE_RECORD_KIND: package})
    for index in range(5):
        ledger.append_records(f"other{index}", {"crypto_cycle": {"n": index}})
    retention.rotate_file(ledger, RECORDS_FILE, keep_rows=2, now=NOW)

    assert blog_content.written_keywords(ledger) == ["미리캔버스 포스터"]
    assert blog_content.written_keywords(None) == []


def test_the_weekly_run_does_not_redraft_a_keyword_the_ledger_or_the_vault_holds(
        tmp_path, monkeypatch):
    """End of the wiring: last week's package is on the ledger, the other winnable keyword was
    published from the vault, and the fire reports that none qualifies instead of drafting
    either twice."""
    from runtime.mvp_runtime.errors import ToolError
    from runtime.mvp_runtime.store import LedgerStore

    ledger = LedgerStore(tmp_path / "ledger")
    package = _package()
    ledger.append_records(package["package_id"], {blog_content.PACKAGE_RECORD_KIND: package})
    kinds_run: list[str] = []

    def fake_run(kind, request, **kwargs):
        kinds_run.append(kind)
        return {"records": {"keyword_research": _brief()}}

    monkeypatch.setattr(blog_content, "_run", fake_run)
    with pytest.raises(ToolError) as exc:
        blog_content.run_content_ideation(
            {"seeds": "미리캔버스 포스터, 포스터 만들기"}, ledger=ledger, now=NOW,
            repo_root=tmp_path, published_source=_StaticSource(["포스터만들기"]))
    assert exc.value.reason_code == blog_content.NO_ELIGIBLE_KEYWORD
    assert "already written: 2" in exc.value.reason
    assert kinds_run == ["research"]            # no content draft was asked for


class _StaticSource:
    """A published-keyword source with fixed contents — the vault adapter's interface."""

    def __init__(self, keywords=(), tags=()):
        self._published = blog_content.PublishedKeywords(tuple(keywords), tuple(tags))

    def load(self):
        return self._published


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
    return _build()


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
    for section in ("## 타깃 키워드 근거", "## 선정 근거", "## 제목 후보", "## 본문", "## 발행 전 확인"):
        assert section in post
    # The evidence numbers ride along — a package whose numbers cannot be traced is a guess.
    assert "9000" in post and "naver_searchad" in post
    # ...and the target block carries only the target's numbers.
    target_block = post.split("## 선정 근거")[0]
    assert "130000" not in target_block and "블로그 문서수 ('미리캔버스 포스터' 검색): 120" in target_block
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


def test_a_tistory_package_writes_post_md_and_its_markdown_paste_in_its_own_folder(tmp_path):
    from tests.test_mvp_runtime_blog_tistory import tistory_package

    package = tistory_package()
    written, files, note = blog_content._write_package_files(
        package, writer=_real_writer(), ledger=_ListLedger(), now=NOW, repo_root=tmp_path)
    assert written is True and [Path(f).name for f in files] == ["POST.md", "PASTE.md"]
    assert all("/blog/tistory/" in f"/{Path(f).as_posix()}" for f in files)
    paste = (workspace_root(tmp_path) / files[1]).read_text(encoding="utf-8")
    assert paste == blog_content.render_paste_txt(package) and "\n## " in paste
    assert note.startswith("workspace/blog/tistory/")


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


# --- the weekly run survives the pipeline it calls -----------------------------

@requires_local_core
def test_the_weekly_run_reaches_the_pipeline_as_the_scheduler_and_hands_it_strings(monkeypatch):
    """Three defects shipped together and nothing here ran the producer end to end: the run
    named a requester type intake does not admit (`human`), handed the keyword brief a list
    where the venue splits a string, and passed the content run a keyword argument `run_task`
    does not have. Any one of them fails the first weekly fire before a model is asked anything
    — this test failed on the unfixed source at the first `_run` (`IDEATION_RESEARCH_BLOCKED`).

    The brief is the one network leg; it is replaced with the fixture and made to insist on the
    type the real one splits. Everything else is the real pipeline on the deterministic
    provider."""
    from runtime.mvp_runtime import pipeline, pipeline_worker
    from runtime.mvp_runtime.worker import MockProvider

    briefs: list[str] = []

    def fake_brief(seeds, *, now, keyword_tool=None):
        assert isinstance(seeds, str), f"the brief splits a string, got {type(seeds).__name__}"
        briefs.append(seeds)
        return list(_brief()["metrics"]), _brief()

    monkeypatch.setattr(pipeline.naver_research, "run_keyword_brief", fake_brief)

    calls: list[dict] = []
    real_run_task = blog_content.run_task

    def recording_run_task(request, **kwargs):
        calls.append(dict(kwargs))
        return real_run_task(request, **kwargs)

    monkeypatch.setattr(blog_content, "run_task", recording_run_task)

    sheet = blog_content.run_content_ideation(
        {"seeds": "미리캔버스 포스터, 포스터 만들기"},
        providers={"provider": MockProvider()},
        now=NOW,
        published_source=_StaticSource(["포스터 만들기"]),
    )

    assert sheet["target_keyword"] == "미리캔버스 포스터"
    assert briefs == ["미리캔버스 포스터, 포스터 만들기", "미리캔버스 포스터"]
    profile = pipeline_worker._ACTOR_PROFILES[pipeline_worker.SCHEDULER_PROFILE]
    # The mock answers in prose, not the structured contract, so the one revision runs — and
    # only one — without re-running the Naver brief.
    assert [c["request_kind"] for c in calls] == ["research", "content", "content"]
    assert "keyword_seeds" not in calls[2]
    assert sheet["package"]["quality"]["revision_count"] == 1
    assert sheet["package"]["quality"]["quality_state"] == "needs_edit"
    assert sheet["lineage"]["revision_trace_id"] not in (None, sheet["lineage"]["content_trace_id"])
    for call in calls:
        assert (call["requester_id"], call["requester_type"], call["channel"]) == (
            profile["requester_id"], profile["requester_type"], profile["channel"])
        assert "naver_keywords" not in call
    assert calls[0]["keyword_seeds"] == "미리캔버스 포스터, 포스터 만들기"
    assert calls[1]["keyword_seeds"] == "미리캔버스 포스터"
    # The package's target evidence came from the content run's brief, and the lineage names
    # the two real runs — not one trace for both, not an invented id.
    lineage = sheet["lineage"]
    assert lineage["selection_research_trace_id"] and lineage["content_trace_id"]
    assert lineage["selection_research_trace_id"] != lineage["content_trace_id"]
    assert lineage["target_research_trace_id"] == lineage["content_trace_id"]
    assert sheet["target_evidence"]["keyword"] == "미리캔버스 포스터"
    assert set(sheet["trace_ids"]) == {lineage["selection_research_trace_id"],
                                       lineage["content_trace_id"], lineage["revision_trace_id"]}


def test_the_content_run_searches_the_target_not_the_drafting_brief(monkeypatch):
    """The drafting brief sent whole as the web query found posts about "문단" and GPT prompts
    for '스티커제작업체' (bcp_b02b7c5579faabd23420, 2026-09-30)."""
    calls: list[tuple[str, dict]] = []

    def fake_run(kind, request, **kwargs):
        calls.append((kind, kwargs))
        raise blog_content.ToolError(blog_content.IDEATION_CONTENT_BLOCKED, "stop after the call")

    monkeypatch.setattr(blog_content, "_run", fake_run)
    with pytest.raises(blog_content.ToolError):
        blog_content.run_content_ideation(
            {"seeds": "target=미리캔버스 포스터"}, now=NOW, published_source=_StaticSource())
    (kind, kwargs), = calls
    assert kind == "content" and kwargs["search_query"] == "미리캔버스 포스터"
