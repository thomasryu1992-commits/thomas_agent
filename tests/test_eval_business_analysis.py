"""The business-analysis evaluation harness (system review B3): a fixed set, floor scores, no ledger."""

from __future__ import annotations

import json

from scripts import eval_business_analysis as harness
from tests._helpers import requires_local_core

NOW = "2026-09-25T09:00:00Z"


def test_the_set_is_well_formed():
    """A regression baseline is only one while it stays the same set: unique ids, every item an
    analysis request, every theme answerable by at least one phrase."""
    items = harness.load_items()
    assert len(items) == 24
    assert len({item["id"] for item in items}) == len(items)
    for item in items:
        assert item["request"].startswith("이 사업 아이디어를 분석해줘: ")
        assert item["themes"]
        for theme in item["themes"]:
            assert theme["name"] and theme["any_of"] and all(isinstance(p, str) and p for p in theme["any_of"])


def test_a_theme_is_met_by_any_of_its_phrases_anywhere_in_the_analysis():
    item = {"id": "X", "themes": [{"name": "물류", "any_of": ["배송비", "물류"]},
                                  {"name": "규제", "any_of": ["규제"]}]}
    result = {"status": "COMPLETED", "records": {
        "agent_output": {"summary": "요약", "risks": ["배송비 부담이 크다"], "assumptions": ["a"],
                         "next_actions": [], "role_specific_output": {"key_findings": ["f"]}},
        "validation_result": {"validation": {"result": "REVISE", "checks": [
            {"check_id": "evidence_grounding", "result": "REVISE"}, {"check_id": "x", "result": "PASS"}]}},
    }}
    scored = harness.score(item, result)
    assert scored["themes_met"] == ["물류"] and scored["theme_coverage"] == 0.5
    assert scored["sections_present"] == ["risks", "assumptions"]
    assert scored["withheld_by"] == ["evidence_grounding"]
    assert harness.summarize([scored])["delivered"] == 0


@requires_local_core
def test_a_mock_run_writes_a_report_and_no_ledger(tmp_path, capsys):
    out = tmp_path / "report.json"
    assert harness.main(["--limit", "2", "--output", str(out)], now=NOW) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["provider"] == "mock" and report["summary"]["items"] == 2
    assert [s["id"] for s in report["items"]] == ["BA01", "BA02"]


def test_a_live_run_is_refused_while_the_runtime_is_not_active(capsys):
    """The kill switch binds an evaluation exactly as it binds the intake CLI: live runs spend
    quota and invoke models, and a KILLED runtime does neither."""
    class _State:
        execution_allowed = False
        mode = "KILLED"

        def refusal_reason_code(self):
            return "RUNTIME_KILLED"

    class _Control:
        def load(self):
            return _State()

    assert harness.main(["--live"], now=NOW, control_store=_Control()) == harness.EXIT_BLOCKED
    assert "RUNTIME_KILLED" in capsys.readouterr().err
