"""The blog department's feedback shadow (BLOG_DEPARTMENT v0.2 P5): a keyword-priority guideline from the recorded
statistics week, judged under #1183's evidence floor and applied in shadow to the production keyword run.

The fixture vault has one statistics week (Sunday 2026-11-01) and these Naver clusters, all past D+14 unless noted:

* ``aa`` — five posts with 50 views each, well above the week's median of 10 (factor up);
* ``bb`` — five posts the week's top table left out (``below_table``, factor down);
* ``cc`` — five posts at the median (no difference);
* ``dd`` — three posts (under the five-post floor);
* ``ee`` — eight posts, six of them with no ``url:`` while the table has a row it could not match (unknown, so the
  cluster is over the 30% missing ceiling);
* a post of ``aa`` published a week before the Sunday (too young to count) and one post with no cluster."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mvp_runtime import blog_department as bd
from runtime.mvp_runtime.blog import analysis as ba
from runtime.mvp_runtime.blog import feedback as fb
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.scheduler import (
    KIND_BLOG_FEEDBACK, KINDS, MAINTENANCE_KINDS, ScheduleStore, build_schedule, run_due,
)
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore

NOW = "2026-11-02T05:00:00Z"          # 14:00 KST on Monday 11-02
SUNDAY = "2026-11-01"
OLD, YOUNG = "2026-10-10", "2026-10-25"

# post number -> (cluster, published, has url, views in the week's table or None)
POSTS = {
    **{n: ("aa", OLD, True, 50) for n in range(1, 6)},
    **{n: ("bb", OLD, True, None) for n in range(6, 11)},
    **{n: ("cc", OLD, True, 10) for n in range(11, 16)},
    **{n: ("dd", OLD, True, 10) for n in range(16, 19)},
    **{n: ("ee", OLD, False, None) for n in range(19, 25)},
    **{n: ("ee", OLD, True, 10) for n in range(25, 27)},
    30: ("aa", YOUNG, True, None),
    40: ("", OLD, True, 10),
}

HISTORY = {
    "date": "2026-11-02", "has_vol": True,
    "candidates": [
        {"kw": "가 키워드", "lane": "실무 가이드", "score": 10.0, "decision": {"cluster": "aa"}},
        {"kw": "나 키워드", "lane": "실무 가이드", "score": 30.0, "decision": {"cluster": "bb"}},
        {"kw": "다 키워드", "lane": "실무 가이드", "score": 20.0, "decision": {"cluster": "cc"}},
        {"kw": "라 키워드", "lane": "실무 가이드", "score": 25.0, "decision": {"cluster": "unresolved"}},
        {"kw": "마 키워드", "lane": "실무 가이드", "score": 5.0, "decision": {"cluster": "zz"}},
        {"kw": "바 키워드", "lane": "AI 활용", "score": 40.0, "decision": {"cluster": "aa"}},
    ],
    "tistory": [{"kw": "티스토리 후보", "lane": "AI 활용"}],
    "ranks": [{"file": "content/naver/01편-가.md", "kw": "가 키워드", "rank": 3, "parsed": True}],
}


def _post(vault: Path, number: int, cluster: str, published: str, url: bool) -> None:
    lines = ["---", "status: published", f"published: {published}"]
    if cluster:
        lines.append(f"cluster: {cluster}")
    if url:
        lines.append(f'url: "https://blog.naver.com/thomasai/2244{number:07d}"')
    lines += ["---", "", "본문"]
    (vault / "content" / "naver" / f"{number:02d}편-글-{number}.md").write_text("\n".join(lines), encoding="utf-8")


def _week() -> dict:
    rows = [{"순위": i, "제목": f"글 {n}", "조회": views, "편": f"{n:02d}편-글-{n}",
             "url": f"http://blog.naver.com/thomasai/2244{n:07d}", "발행": published}
            for i, (n, (_, published, _, views)) in enumerate(
                sorted(((n, p) for n, p in POSTS.items() if p[3] is not None), key=lambda x: -x[1][3]), 1)]
    rows.append({"순위": len(rows) + 1, "제목": "막 나온 글", "조회": 9, "편": "", "url": None, "발행": "2026-11-01"})
    return {"summary": {"조회수": {"이번주": 900, "지난주": 800}, "순방문자": {"이번주": 700},
                        "유입수": 600, "검색유입": 500, "공식검색유입률": 80.0, "홈판": 50,
                        "글별": rows, "검색어": []}}


@pytest.fixture
def vault(tmp_path):
    for p in ("content/naver", "content/tistory", "analytics/naver-weekly", "analytics/keywords/history"):
        (tmp_path / "vault" / p).mkdir(parents=True)
    root = tmp_path / "vault"
    for number, (cluster, published, url, _) in POSTS.items():
        _post(root, number, cluster, published, url)
    (root / "content" / "naver" / "99편-초안.md").write_text("---\nstatus: draft\ncluster: aa\n---\n", encoding="utf-8")
    (root / "analytics" / "naver-weekly" / f"{SUNDAY}.json").write_text(
        json.dumps(_week(), ensure_ascii=False), encoding="utf-8")
    _history(root, HISTORY)
    return root


def _history(root: Path, data: dict, day: str = "2026-11-02") -> None:
    (root / "analytics" / "keywords" / "history" / f"{day}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def ledger(tmp_path, vault):
    store = LedgerStore(tmp_path / "state" / LEDGER_REL)
    assert ba.fire(store, now=NOW, vault_root=vault).startswith(f"blog_analysed={SUNDAY}")
    return store


def _record(ledger, vault, history=HISTORY):
    week = fb.latest_week(ledger, today="2026-11-02")
    return fb.build_record(week, "2026-11-02", fb.read_posts(vault), history, now=NOW)


def _clusters(record):
    return {c["cluster"]: c for c in record["clusters"]}


def test_each_cluster_is_judged_under_the_1183_floor(ledger, vault):
    record = _record(ledger, vault)
    assert record["baseline"] == {"posts": 27, "median": 10, "measured": 16, "below_table": 5, "unknown": 6}
    c = _clusters(record)
    assert (c["aa"]["verdict"], c["aa"]["factor"], c["aa"]["ratio"], c["aa"]["posts"]) == ("CANDIDATE", 1.2, 5.0, 5)
    assert (c["bb"]["verdict"], c["bb"]["factor"], c["bb"]["median"]) == ("CANDIDATE", 0.8, "below_table")
    assert (c["cc"]["verdict"], c["cc"]["factor"], c["cc"]["ratio"]) == ("NO_DIFFERENCE", 1.0, 1.0)
    assert (c["dd"]["verdict"], c["dd"]["reason"], c["dd"]["factor"]) == ("INSUFFICIENT_EVIDENCE", "posts_below_floor", 1.0)
    assert (c["ee"]["verdict"], c["ee"]["reason"], c["ee"]["missing_rate"]) == \
        ("INSUFFICIENT_EVIDENCE", "missing_above_ceiling", 0.75)
    assert record["summary"]["clusters_candidate"] == ["aa", "bb"]


def test_a_post_younger_than_d14_or_unpublished_is_not_counted(ledger, vault):
    assert _clusters(_record(ledger, vault))["aa"]["posts"] == 5            # not 30 (young), not 99 (draft)


def test_a_post_with_a_url_is_below_the_table_even_when_some_rows_are_unmatched(ledger, vault):
    rows = {r["post"]: r for r in fb.post_states(fb.read_posts(vault), fb.latest_week(ledger, today="2026-11-02"))}
    assert rows[6]["state"] == "below_table" and rows[19]["state"] == "unknown" and rows[1]["state"] == "measured"


def test_without_unmatched_rows_an_absent_post_is_below_the_table_whatever_its_url(ledger, vault):
    week = dict(fb.latest_week(ledger, today="2026-11-02"))
    week["naver"] = {**week["naver"], "posts_unmapped": 0}
    rows = {r["post"]: r for r in fb.post_states(fb.read_posts(vault), week)}
    assert rows[19]["state"] == "below_table"


def test_the_median_is_the_upper_middle_with_below_table_posts_lowest():
    def row(state, views=None):
        return {"state": state, "views": views}

    assert fb.median([row("measured", 4), row("measured", 10)]) == 10                 # upper middle, as P3's
    assert fb.median([row("measured", 0), row("below_table")]) == 0                   # below the table sorts lowest
    assert fb.median([row("below_table"), row("below_table"), row("measured", 9)]) == "below_table"
    assert fb.median([row("unknown")]) is None


def test_only_judged_clusters_move_scores_and_the_ranks_they_change_are_recorded(ledger, vault, monkeypatch):
    monkeypatch.setattr(fb, "TOP_PER_LANE", 1)
    record = _record(ledger, vault)
    cands = {c["kw"]: c for c in record["candidates"]}
    assert (cands["가 키워드"]["score_after"], cands["가 키워드"]["effect"]) == (12.0, "adjusted")
    assert (cands["나 키워드"]["score_after"], cands["나 키워드"]["rank_before"], cands["나 키워드"]["rank_after"]) == (24.0, 1, 2)
    assert (cands["다 키워드"]["score_after"], cands["다 키워드"]["effect"]) == (20.0, "unchanged")
    assert (cands["라 키워드"]["score_after"], cands["라 키워드"]["effect"], cands["라 키워드"]["rank_after"]) == \
        (25.0, "unresolved", 1)
    assert cands["마 키워드"]["effect"] == "no_guideline"
    assert record["top_before"] == {"AI 활용": ["바 키워드"], "실무 가이드": ["나 키워드"]}
    assert record["top_after"] == {"AI 활용": ["바 키워드"], "실무 가이드": ["라 키워드"]}
    summary = record["summary"]
    assert (summary["adjusted"], summary["unchanged"], summary["no_guideline"], summary["unresolved"]) == (3, 1, 1, 1)
    assert (summary["rank_moved"], summary["top_entered"], summary["top_left"]) == (2, ["라 키워드"], ["나 키워드"])
    assert "티스토리 후보" not in cands                                      # Tistory is left alone


def test_the_unverified_rank_table_is_never_read(ledger, vault):
    without = {k: v for k, v in HISTORY.items() if k != "ranks"}
    assert _record(ledger, vault)["inputs_id"] == _record(ledger, vault, without)["inputs_id"]


def test_the_record_carries_no_title_and_no_url(ledger, vault):
    text = json.dumps(_record(ledger, vault), ensure_ascii=False)
    assert "http" not in text and "thomasai" not in text and "글 1" not in text and "content/" not in text


def test_the_daily_fire_records_once_and_again_when_the_history_changes(ledger, vault):
    assert fb.fire(ledger, now=NOW, vault_root=vault) == \
        "blog_feedback=2026-11-01/2026-11-02(candidate_clusters=2,rank_moved=2)"
    assert fb.fire(ledger, now=NOW, vault_root=vault) == "blog_feedback_nothing_new"
    changed = json.loads(json.dumps(HISTORY))
    changed["candidates"][2]["score"] = 31.0
    _history(vault, changed)
    assert fb.fire(ledger, now=NOW, vault_root=vault).startswith("blog_feedback=2026-11-01/2026-11-02(")
    _history(vault, HISTORY, day="2026-11-03")                              # tomorrow's file is not read today
    assert fb.fire(ledger, now=NOW, vault_root=vault) == "blog_feedback_nothing_new"
    assert len([r for r in ledger.iter_records(kinds=[fb.RECORD_KIND]) if r["kind"] == fb.RECORD_KIND]) == 2


def test_missing_inputs_are_named_and_an_absent_vault_refuses(tmp_path, vault):
    empty = LedgerStore(tmp_path / "empty" / LEDGER_REL)
    assert fb.fire(empty, now=NOW, vault_root=vault) == "blog_feedback_no_analysis"
    store = LedgerStore(tmp_path / "state" / LEDGER_REL)
    ba.fire(store, now=NOW, vault_root=vault)
    (vault / "analytics" / "keywords" / "history" / "2026-11-02.json").unlink()
    assert fb.fire(store, now=NOW, vault_root=vault) == "blog_feedback_no_history"
    with pytest.raises(ToolError) as caught:
        fb.fire(store, now=NOW, vault_root=tmp_path / "absent")
    assert caught.value.reason_code == "BLOG_VAULT_UNAVAILABLE"


def test_the_kind_is_maintenance_and_fires_through_the_scheduler(ledger, vault, tmp_path, monkeypatch):
    assert KIND_BLOG_FEEDBACK in KINDS and KIND_BLOG_FEEDBACK in MAINTENANCE_KINDS
    monkeypatch.setenv(bd.BLOG_VAULT_ROOT_ENV, str(vault))
    state = tmp_path / "state"
    store = ScheduleStore(state)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(build_schedule(kind=KIND_BLOG_FEEDBACK, request="", interval_seconds=86400, created_by="op",
                             now="2026-11-01T05:00:00Z"))
    run_due(store, now=NOW, control_store=ControlStore(state), ledger=ledger, repo_root=state)
    terminal = [e for e in ledger.read_scheduler_events() if e["action"] in ("fired", "failed")][-1]
    assert terminal["action"] == "fired" and terminal["status"].startswith("blog_feedback=")
