"""P7's affiliate pick, carried by the morning instruction (BLOG_DEPARTMENT v0.2, Thomas 2026-11-05 "지시에 싣기").

The gate is the server script ``blog-affiliate-place.py`` itself, generated into ``blog/affiliate_place.py`` with the
clip parser (``blog/affiliate_clips.py``). These tests run the generated code on a small vault (so a broken import or
``configure`` fails here, not on the host), and pin the selection the instruction adds around it: the first stock
ready to place, in the script's order, or the script's deferral code. The port's equality with the script on the real
vault (ten affiliate files, six of them ready) is recorded in docs/history/2026-11-05-blog-affiliate-pick.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.blog import affiliate_place, instruction as bi


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    for p in ("content/naver/제휴", "analytics/keywords", "제휴/키워드", "제휴/클립"):
        (root / p).mkdir(parents=True)
    kw = root / "analytics" / "keywords"
    (kw / "clusters.tsv").write_text("eq-fridge\t장비\tequipment\t냉장고\t-\t냉장고\tACTIVE\t\n", encoding="utf-8")
    (kw / "entities.tsv").write_text("# 없음\n", encoding="utf-8")
    return root


def _stock(root: Path, slug: str, front: str, body: str = "## AI로 고르기\n본문\n") -> Path:
    path = root / "content" / "naver" / "제휴" / f"제휴-{slug}.md"
    path.write_text(f"---\n{front}\naffiliate: true\n---\n{body}", encoding="utf-8")
    return path


def test_the_generated_gate_runs_and_no_ready_stock_is_the_scripts_deferral(vault):
    _stock(vault, "링크-대기", 'title: "AI로 고르는 냉장고"\nkeywords: [테스트 냉장고]', "## AI로 고르기\n[제휴링크: 상품]\n")
    _stock(vault, "링크-없음", 'title: "AI로 고르는 냉동고"\nkeywords: [테스트 냉동고]')
    a = bi.affiliate_pick(vault)
    assert (a["state"], a["error"], a["stock"], a["ready"]) == ("built", None, 2, 0)
    assert (a["link_unready"], a["needs_review"], a["held"]) == (1, 1, 0)
    assert a["pick"] is None and a["deferred"] == "NO_READY_STOCK"


def test_a_stock_the_script_itself_cannot_judge_is_an_error_not_a_pick(vault):
    # The script raises on a stock without keywords (its bridge reads the first one); so does the port.
    _stock(vault, "키워드-없음", 'title: "냉장고"')
    a = bi.affiliate_pick(vault)
    assert (a["state"], a["error"], a["pick"], a["deferred"]) == ("error", "IndexError", None, None)


def test_the_pick_is_the_first_ready_stock_in_the_scripts_order(vault, monkeypatch):
    _stock(vault, "셋째", 'title: "c"\nkeywords: [셋째 키워드]\ncreated: 2026-11-01')
    _stock(vault, "둘째", 'title: "b"\nkeywords: [둘째 키워드]\nstock_order: 2')
    _stock(vault, "첫째", 'title: "a"\nkeywords: [첫째 키워드]\nstock_order: 1')
    states = {"제휴-첫째.md": "보류", "제휴-둘째.md": "배치 가능", "제휴-셋째.md": "배치 가능"}
    monkeypatch.setattr(affiliate_place, "readiness", lambda path, ctx: {"state": states[Path(path).name]})
    a = bi.affiliate_pick(vault)
    # stock_order 1 is not ready; stock_order 2 is, ahead of the stock with no order.
    assert a["pick"] == {"id": bi.stock_id("둘째"), "keyword": "둘째 키워드"} and a["deferred"] is None
    assert (a["stock"], a["ready"], a["needs_review"]) == (3, 2, 1)


def test_a_held_stock_names_the_editorial_deferral(vault, monkeypatch):
    _stock(vault, "보류됨", 'title: "a"\nkeywords: [보류 키워드]')
    monkeypatch.setattr(affiliate_place, "readiness", lambda path, ctx: {"state": "편집 보류"})
    a = bi.affiliate_pick(vault)
    assert (a["held"], a["pick"], a["deferred"]) == (1, None, "NO_EDITORIALLY_ELIGIBLE_READY_STOCK")


def test_without_the_clip_folders_it_is_unavailable_never_a_deferral(vault):
    _stock(vault, "링크-대기", 'title: "a"\nkeywords: [a]')
    (vault / "제휴" / "클립").rmdir()
    a = bi.affiliate_pick(vault)
    assert (a["state"], a["stock"], a["pick"], a["deferred"]) == ("unavailable", 0, None, None)


def test_a_registry_the_gate_needs_and_cannot_read_is_an_error_not_a_guess(vault):
    _stock(vault, "링크-대기", 'title: "a"\nkeywords: [a]')
    (vault / "analytics" / "keywords" / "clusters.tsv").unlink()
    a = bi.affiliate_pick(vault)
    assert (a["state"], a["error"], a["pick"], a["deferred"]) == ("error", "FileNotFoundError", None, None)


def _placed(*names):
    return {"affiliate_placed": [{"path": f"content/naver/제휴/{n}"} for n in names]}


def test_each_placement_is_compared_with_the_pick_by_the_stocks_slug():
    built = {"affiliate": {"state": "built", "pick": {"id": bi.stock_id("냉장고"), "keyword": "k"}}}
    a = bi.affiliate_check(_placed("202편-냉장고.md", "203편-다른-재고.md"), built)
    assert (a["state"], a["placed"], a["matched"], a["differed"], a["unjudged"]) == ("compared", 2, 1, 1, 0)
    assert a["placements"] == [{"episode": 202, "matched": True}, {"episode": 203, "matched": False}]
    # The instruction deferred, and the cron placed one anyway: not a match.
    deferred = {"affiliate": {"state": "built", "pick": None}}
    assert bi.affiliate_check(_placed("204편-냉장고.md"), deferred)["differed"] == 1


def test_a_placement_without_a_pick_to_compare_is_unjudged_and_an_old_report_is_not_reported():
    unavailable = {"affiliate": {"state": "unavailable", "pick": None}}
    assert bi.affiliate_check(_placed("202편-냉장고.md"), unavailable)["unjudged"] == 1
    assert bi.affiliate_check(_placed("202편-냉장고.md"), {"run_date": "v0.1, no section"})["unjudged"] == 1
    assert bi.affiliate_check(_placed("202편-냉장고.md"), None)["unjudged"] == 1
    assert bi.affiliate_check({"drafts": []}, unavailable)["state"] == "not_reported"
    assert bi.affiliate_check({"affiliate_placed": []}, unavailable)["state"] == "none_placed"
