"""Rank snapshots for published blog packages — Phase 4's record, with nothing switched on.

Pinned: a published URL is found in the result list however it is spelled, a post that is not
there ranks null (not zero, not last), a draft package is not tracked, checkpoints are D+1/7/14/28
and never back-filled, every observation is a new row, and nothing here registers a schedule or
runs on the Mock.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import blog_content, blog_rank, naver_research
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.paths import repo_root
from runtime.mvp_runtime.store import LedgerStore
from runtime.read_only_kernel.schema_validation import validate_against_schema
from scripts import record_published_url, track_blog_rank

SCHEMA = repo_root() / "schemas" / "blog_rank_snapshot.v0.1.schema.json"
URL = "https://blog.naver.com/thomasai/224000000001"
PUBLISHED_AT = "2026-09-01T09:00:00Z"


def _published():
    from tests.test_mvp_runtime_blog_content import _build
    return record_published_url.build_published_row(_build(), url=URL, now=PUBLISHED_AT)


class _Search:
    """The blog search's shape: result links in relevance order."""

    tool_id, tool_version, network_egress = naver_research.COMPETITION_TOOL_ID, "0.1.0", True

    def __init__(self, links=(), error=None):
        self.links, self.error, self.calls = list(links), error, []

    def competition(self, keyword, *, display, timeout_seconds):
        self.calls.append((keyword, display))
        if self.error:
            raise ToolError(self.error, "api hub unreachable")
        return naver_research.CompetitionResult(keyword=keyword, total_posts=999, links=self.links)


# --- 25. URL normalization ---------------------------------------------------------------

@pytest.mark.parametrize("spelling", [
    "https://blog.naver.com/thomasai/224000000001",
    "http://blog.naver.com/thomasai/224000000001/",
    "https://m.blog.naver.com/thomasai/224000000001?referrerCode=0&searchKeyword=x",
    "https://blog.naver.com/PostView.naver?blogId=thomasai&logNo=224000000001&redirect=Dlog",
    "https://m.blog.naver.com/PostView.nhn?blogId=ThomasAI&logNo=224000000001#comment",
    "blog.naver.com/thomasai/224000000001",
])
def test_every_spelling_of_one_post_normalizes_to_one_key(spelling):
    assert blog_rank.normalize_post_url(spelling) == "blog.naver.com/thomasai/224000000001"


def test_a_different_post_or_a_blog_home_is_not_the_post():
    assert blog_rank.normalize_post_url("https://blog.naver.com/thomasai/224000000002") != \
        blog_rank.normalize_post_url(URL)
    assert blog_rank.normalize_post_url("https://blog.naver.com/thomasai") is None
    assert blog_rank.normalize_post_url("") is None


# --- 23/24. the rank itself ---------------------------------------------------------------

def test_a_published_url_in_the_results_gets_its_position():
    links = ["https://blog.naver.com/other/1", "https://blog.naver.com/other/2",
             "https://m.blog.naver.com/PostView.naver?blogId=thomasai&logNo=224000000001"]
    record = blog_rank.check_rank(_published(), tool=_Search(links), now="2026-09-08T09:00:00Z",
                                  checkpoint="D+7")
    assert record["rank_position"] == 3
    assert record["matched_url"] == links[2]
    assert (record["result_window"], record["degraded"]) == (3, False)
    validate_against_schema(record, SCHEMA, "blog_rank_snapshot")


def test_a_post_not_in_the_window_ranks_null_not_zero():
    search = _Search(["https://blog.naver.com/other/1"])
    record = blog_rank.check_rank(_published(), tool=search, now="2026-09-08T09:00:00Z",
                                  checkpoint="D+7")
    assert record["rank_position"] is None and record["matched_url"] is None
    assert record["degraded"] is False
    assert search.calls == [("미리캔버스 포스터", blog_rank.RESULT_WINDOW)]   # its own keyword
    validate_against_schema(record, SCHEMA, "blog_rank_snapshot")


def test_a_failed_lookup_is_recorded_as_degraded_not_as_absent():
    record = blog_rank.check_rank(_published(), tool=_Search(error="TOOL_TRANSPORT"),
                                  now="2026-09-08T09:00:00Z", checkpoint="D+7")
    assert (record["rank_position"], record["degraded"], record["degraded_reason"]) == (
        None, True, "TOOL_TRANSPORT")
    validate_against_schema(record, SCHEMA, "blog_rank_snapshot")


# --- 26. only published packages ----------------------------------------------------------

def test_an_unpublished_package_is_not_tracked():
    from tests.test_mvp_runtime_blog_content import _build
    draft = _build()
    assert blog_rank.trackable(draft) is False
    with pytest.raises(ToolError) as exc:
        blog_rank.check_rank(draft, tool=_Search(), now="2026-09-08T09:00:00Z", checkpoint="D+7")
    assert exc.value.reason_code == blog_rank.PACKAGE_NOT_PUBLISHED


# --- checkpoints -------------------------------------------------------------------------

@pytest.mark.parametrize("now,taken,due,missed", [
    ("2026-09-01T20:00:00Z", [], None, []),                       # before D+1
    ("2026-09-02T09:00:00Z", [], "D+1", []),
    ("2026-09-10T09:00:00Z", ["D+1"], "D+7", []),
    ("2026-09-10T09:00:00Z", [], "D+7", ["D+1"]),                  # D+1 missed, not back-filled
    ("2026-09-10T09:00:00Z", ["D+1", "D+7"], None, []),            # already taken
    ("2026-10-15T09:00:00Z", ["D+1", "D+7", "D+14"], "D+28", []),
])
def test_the_due_checkpoint_is_the_latest_arrived_and_untaken(now, taken, due, missed):
    status = blog_rank.checkpoint_status(PUBLISHED_AT, now, taken)
    assert (status["due"], status["missed"]) == (due, missed)


# --- 27. the CLI appends, refuses, and schedules nothing ----------------------------------

def _store_with_published(tmp_path):
    store = LedgerStore.default(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    package = _published()
    store.append_records(package["package_id"], {blog_content.PACKAGE_RECORD_KIND: package})
    return store, package


def _snapshots(store):
    return [r["record"] for r in store.iter_records_with_archive()
            if r.get("kind") == blog_rank.SNAPSHOT_RECORD_KIND]


def test_each_checkpoint_appends_a_row_and_none_is_rewritten(tmp_path, capsys):
    store, package = _store_with_published(tmp_path)
    args = ["--check", "--package-id", package["package_id"], "--root", str(tmp_path)]
    links = [URL]
    assert track_blog_rank.main(args, tool=_Search(links), now="2026-09-02T10:00:00Z") == 0
    first = _snapshots(store)
    assert [s["checkpoint"] for s in first] == ["D+1"]
    # The same checkpoint again is refused — one observation per checkpoint.
    assert track_blog_rank.main(args, tool=_Search(links), now="2026-09-03T10:00:00Z") == 2
    assert blog_rank.CHECKPOINT_ALREADY_TAKEN in capsys.readouterr().err
    assert track_blog_rank.main(args, tool=_Search([]), now="2026-09-08T10:00:00Z") == 0
    rows = _snapshots(store)
    assert [s["checkpoint"] for s in rows] == ["D+1", "D+7"]
    assert rows[0] == first[0]                                   # the D+1 row is untouched
    assert [s["rank_position"] for s in rows] == [1, None]


def test_the_cli_refuses_the_mock_and_a_checkpoint_not_yet_due(tmp_path, capsys):
    _store, package = _store_with_published(tmp_path)
    args = ["--check", "--package-id", package["package_id"], "--root", str(tmp_path)]
    assert track_blog_rank.main(args, tool=_Search(), now="2026-09-01T12:00:00Z") == 2
    assert blog_rank.CHECKPOINT_NOT_DUE in capsys.readouterr().err
    mock = naver_research.MockCompetitionTool()
    assert track_blog_rank.main(args, tool=mock, now="2026-09-02T12:00:00Z") == 2
    assert "RANK_SOURCE_MOCK" in capsys.readouterr().err


def test_the_due_listing_is_read_only_and_skips_drafts(tmp_path, capsys):
    from tests.test_mvp_runtime_blog_content import _build
    store, package = _store_with_published(tmp_path)
    draft = _build("다른 초안입니다.", target="포스터")
    store.append_records(draft["package_id"], {blog_content.PACKAGE_RECORD_KIND: draft})
    before = list(store.iter_records_with_archive())
    assert track_blog_rank.main(["--due", "--root", str(tmp_path)], now="2026-09-08T10:00:00Z") == 0
    out = capsys.readouterr().out
    assert package["package_id"] in out and "due=D+7" in out and "missed=D+1" in out
    assert draft["package_id"] not in out
    assert list(store.iter_records_with_archive()) == before


def test_nothing_in_the_rank_code_registers_a_schedule():
    """Phase 4 is a record and a hand-run CLI. The scheduler has no rank kind, and neither the
    module nor the CLI touches the schedule store."""
    from runtime.mvp_runtime import scheduler
    kinds = {v for k, v in vars(scheduler).items() if k.startswith("KIND_")}
    assert not any("rank" in str(k) for k in kinds)
    for path in (repo_root() / "runtime/mvp_runtime/blog_rank.py",
                 repo_root() / "scripts/track_blog_rank.py"):
        source = path.read_text(encoding="utf-8")
        for token in ("SCHEDULES_REL", "schedules.jsonl", "scheduler_cli", "from .scheduler", "import scheduler"):
            assert token not in source, (path.name, token)
