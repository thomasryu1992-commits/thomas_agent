"""`scripts/record_published_url.py` — the writer `published_url` never had.

The schema has said since it shipped that the URL "is written back by the operator after
publishing", and REMAINING_WORK §J names the missing writer as Phase 4's prerequisite. These
tests pin the writer's contract: it appends (never edits), the appended row satisfies the
schema's published-requires-URL rule, and every refusal path refuses for its own named reason.
"""

from __future__ import annotations

import json

from runtime.mvp_runtime import blog_content
from runtime.mvp_runtime.store import LedgerStore
from scripts import record_published_url

URL = "https://blog.naver.com/thomasai/224000000001"

DRAFT_PACKAGE = {
    "schema_version": "blog_content_package.v0.1",
    "package_id": "bcp_0123456789abcdef0123",
    "created_at_utc": "2026-08-09T09:00:00Z",
    "target_keyword": "AI 회계 자동화",
    "keyword_evidence": {
        "as_of": "2026-08-09T09:00:00Z",
        "degraded": False,
        "metrics": [{
            "keyword": "AI 회계 자동화",
            "monthly_pc": 900,
            "monthly_mobile": 4200,
            "monthly_total": 5100,
            "competition": "중간",
            "low_volume": False,
            "source": "naver_searchad",
        }],
        "total_competing_posts": 48213,
        "trend_points": [{"period": "2026-07-01", "ratio": 100.0}],
    },
    "title_candidates": ["AI 회계 자동화, 소상공인이 30분 만에 시작하는 법"],
    "body_paste": "요즘 장부 정리에 쓰는 시간이 아깝다면.\n\n오늘은 무료로 쓸 수 있는 방법을 정리했습니다.",
    "body_blocks": [{"paragraph_index": 0, "action": "heading", "note": "소제목으로 지정"}],
    "tags": ["AI회계", "소상공인"],
    "image_shots": [{
        "after_paragraph": 1,
        "what_to_capture": "무료 요금제 한도가 보이는 요금제 화면",
        "tool_name": "예시 회계 서비스",
    }],
    "fact_checks": [{"claim": "무료 플랜은 월 100건까지", "why": "무료 한도는 공지 없이 바뀝니다"}],
    "publish_state": "draft",
}


def _seed(tmp_path, package=DRAFT_PACKAGE):
    store = LedgerStore.default(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    store.append_records(package["package_id"], {blog_content.PACKAGE_RECORD_KIND: package})
    return store


def _rows(store):
    return [row for row in store.iter_records_with_archive()
            if row.get("kind") == blog_content.PACKAGE_RECORD_KIND]


def test_recording_a_publish_appends_a_published_row_and_keeps_the_draft(tmp_path, capsys):
    store = _seed(tmp_path)
    rc = record_published_url.main([
        "--package-id", DRAFT_PACKAGE["package_id"], "--url", URL, "--root", str(tmp_path),
    ])
    assert rc == 0, capsys.readouterr().err
    rows = _rows(store)
    # Append, never edit: the draft row is still there, the published row is newest.
    assert [r["record"]["publish_state"] for r in rows] == ["draft", "published"]
    published = rows[-1]["record"]
    assert published["published_url"] == URL
    assert published["package_id"] == DRAFT_PACKAGE["package_id"]
    assert "published_at_utc" in published
    # The newest-row-per-id reader resolves to the published revision.
    assert record_published_url.latest_packages(store)[
        DRAFT_PACKAGE["package_id"]]["publish_state"] == "published"


def test_the_appended_row_satisfies_the_schema_published_rule(tmp_path):
    package = json.loads(json.dumps(DRAFT_PACKAGE))
    row = record_published_url.build_published_row(
        package, url=URL, now="2026-08-30T09:00:00Z")
    # build_published_row validates internally; reaching here means the closed schema —
    # including its published-requires-URL if/then — accepted the row.
    assert row["publish_state"] == "published"
    assert package["publish_state"] == "draft"  # the input was copied, not mutated


def test_an_unknown_package_id_refuses_by_name(tmp_path, capsys):
    _seed(tmp_path)
    rc = record_published_url.main([
        "--package-id", "bcp_ffffffffffffffffffff", "--url", URL, "--root", str(tmp_path),
    ])
    assert rc == 2
    assert "PACKAGE_NOT_FOUND" in capsys.readouterr().err


def test_a_non_naver_url_refuses_before_touching_the_ledger(tmp_path, capsys):
    store = _seed(tmp_path)
    rc = record_published_url.main([
        "--package-id", DRAFT_PACKAGE["package_id"],
        "--url", "https://example.com/post", "--root", str(tmp_path),
    ])
    assert rc == 2
    assert "URL_INVALID" in capsys.readouterr().err
    assert len(_rows(store)) == 1  # nothing appended


def test_a_second_publish_refuses_and_replace_appends_a_correction(tmp_path, capsys):
    store = _seed(tmp_path)
    args = ["--package-id", DRAFT_PACKAGE["package_id"], "--root", str(tmp_path)]
    assert record_published_url.main([*args, "--url", URL]) == 0
    # Plain re-run refuses, naming the URL already recorded.
    rc = record_published_url.main([*args, "--url", URL + "-typo"])
    assert rc == 2
    assert "ALREADY_PUBLISHED" in capsys.readouterr().err
    # --replace appends the corrected row; history keeps all three revisions.
    corrected = URL + "-corrected"
    assert record_published_url.main([*args, "--url", corrected, "--replace"]) == 0
    rows = _rows(store)
    assert len(rows) == 3
    assert record_published_url.latest_packages(store)[
        DRAFT_PACKAGE["package_id"]]["published_url"] == corrected


def test_replace_without_an_existing_publish_refuses(tmp_path, capsys):
    _seed(tmp_path)
    rc = record_published_url.main([
        "--package-id", DRAFT_PACKAGE["package_id"], "--url", URL,
        "--replace", "--root", str(tmp_path),
    ])
    assert rc == 2
    assert "REPLACE_WITHOUT_PUBLISH" in capsys.readouterr().err


def test_list_is_read_only_and_names_the_state(tmp_path, capsys):
    store = _seed(tmp_path)
    rc = record_published_url.main(["--list", "--root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert DRAFT_PACKAGE["package_id"] in out and "draft" in out
    assert len(_rows(store)) == 1
