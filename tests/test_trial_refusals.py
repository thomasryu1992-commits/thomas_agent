"""The hypothesis channel's refusal tally (`scripts/trial_refusals.py`): it counts where proposals fall at the
proposer and at the trial screen, over a window of time, and lets nothing but counts and held names out.
Synthetic ledger rows only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE
from runtime.mvp_runtime.crypto import factory
from runtime.mvp_runtime.crypto.proposer import FACTORY_LEDGER_KIND, PROPOSAL_LEDGER_KIND
from runtime.mvp_runtime.errors import MvpRuntimeError
from runtime.mvp_runtime.store import LedgerStore
from scripts import trial_refusals as tool

SENTINEL = "LEAK-7731"                       # planted in every field the tally must not carry out
NUMBER = 987654.321                          # the same, for numeric fields


def _proposal(created: str, verdicts: list[dict], *, timeframe: str = "4h", version: str = "v3") -> dict:
    return {"kind": PROPOSAL_LEDGER_KIND, "trace_id": SENTINEL, "record": {
        "record_type": "crypto_strategy_proposal", "symbol": SENTINEL, "timeframe": timeframe,
        "prompt_version": version, "prompt_atr_bps": NUMBER, "focus": SENTINEL, "proposals": verdicts,
        "proposed_count": 99, "accepted_count": 99, "invocation": {"model": SENTINEL}, "created_at": created}}


def _accepted(family: str) -> dict:
    return {"family": family, "accepted": True, "rationale": SENTINEL, "expectancy": NUMBER, "closed_count": 987654,
            "hypothesis": {"payer": SENTINEL}, "spec": {"strategy_id": SENTINEL}, "strategy_rule_hash": SENTINEL}


def _rejected(family: str, reason: str) -> dict:
    return {"family": family, "accepted": False, "rationale": SENTINEL, "reject_reason": reason,
            "block_reasons": [SENTINEL], "missing_fields": [SENTINEL]}


def _fire(created: str, trial: dict | None) -> dict:
    record = {"generation_id": SENTINEL, "accepted_count": 987654, "candidates": [{"champion_score": NUMBER}],
              "rejected": [{"reason": SENTINEL}], "created_at": created}
    if trial is not None:
        record["trial"] = trial
    return {"kind": FACTORY_LEDGER_KIND, "trace_id": SENTINEL, "record": record}


def _refusal(family: str, reason: str, **detail) -> dict:
    return {"proposal_id": SENTINEL, "family": family, "strategy_rule_hash": SENTINEL, "proposed_at": SENTINEL,
            "rationale": SENTINEL, "hypothesis": {"payer": SENTINEL}, "reason": reason, **detail}


def _minted(family: str) -> dict:
    return {"proposal_id": SENTINEL, "family": family, "strategy_rule_hash": SENTINEL, "candidate_id": SENTINEL,
            "strategy_family": SENTINEL, "rationale": SENTINEL}


ROWS = [
    _proposal("2026-11-10T06:18:00Z", [_accepted("old_idea")], version="v2"),                      # before the window
    _fire("2026-11-10T08:10:00Z", {"status": "refused", "refused": [_refusal("old_idea", "no_trades")], "minted": []}),
    _proposal("2026-11-13T06:18:00Z", [
        _accepted("funding_flip"), _rejected("no_form", "form"), _rejected("bad_json", "parse: Unexpected key " + SENTINEL),
        _rejected("slow_one", "validator"), "not a mapping"]),
    _proposal("2026-11-13T14:46:00Z", [_accepted("basis_gap"), _rejected("wrong_frame", "timeframe")], timeframe="1h"),
    _fire("2026-11-13T08:09:00Z", None),                                                           # a single-symbol fire
    _fire("2026-11-13T08:40:00Z", {"status": "minted", "open_before": 987654, "rows": [{"champion_score": NUMBER}],
                                   "refused": [_refusal("funding_flip", "too_slow", expected_trades=NUMBER, trade_floor=987654),
                                               _refusal("funding_flip", "no_trades"),
                                               _refusal("rsi_again", "known_family"),
                                               _refusal(SENTINEL + " bad name", "too_slow"),
                                               _refusal("odd", "Scoring Error: " + SENTINEL)],
                                   "minted": [_minted("basis_gap")]}),
    _fire("2026-11-14T08:41:00Z", {"status": "skipped:not_cohort", "refused": [], "minted": []}),
    _fire("2026-11-14T08:50:00Z", {"status": SENTINEL, "refused": [], "minted": []}),
    _fire("no time", {"status": "refused", "refused": [_refusal("lost", "no_trades")], "minted": []}),
    {"kind": "crypto_cycle", "record": {"created_at": "2026-11-13T09:00:00Z", "trial": {"refused": [_refusal("x", "y")]}}},
    {"kind": FACTORY_LEDGER_KIND, "record": "not a mapping"},
]
SINCE = "2026-11-13T00:00:00Z"


def test_the_tally_counts_both_stages_inside_the_window():
    out = tool.tally(ROWS, since=SINCE)
    proposer, screen = out["proposer"], out["trial_screen"]
    assert (proposer["fires"], proposer["proposed"], proposer["accepted"], proposer["rejected"]) == (2, 6, 2, 4)
    assert proposer["rejected_by_reason"] == {"form": 1, "parse": 1, "validator": 1, "timeframe": 1}, \
        "a parse rejection keeps its name and drops the exception text"
    assert proposer["by_timeframe"] == {"4h": 1, "1h": 1} and proposer["by_prompt_version"] == {"v3": 2}
    assert (proposer["first"], proposer["last"]) == ("2026-11-13T06:18:00Z", "2026-11-13T14:46:00Z")
    assert screen["fires"] == 4 and screen["fires_by_status"] == {
        tool.NOT_ASKED: 1, "minted": 1, "skipped:not_cohort": 1, tool.OTHER: 1}
    assert (screen["screened"], screen["minted"], screen["refused"]) == (6, 1, 5)
    assert screen["refused_by_reason"] == {"too_slow": 2, "no_trades": 1, "known_family": 1, tool.OTHER: 1}
    assert screen["refused_by_family"] == {"funding_flip": {"too_slow": 1, "no_trades": 1}, "rsi_again": {"known_family": 1},
                                           tool.OTHER: {"too_slow": 1}, "odd": {tool.OTHER: 1}}
    assert screen["minted_by_family"] == {"basis_gap": 1}
    assert out["by_day"] == {"2026-11-13": {"proposed": 6, "accepted": 2, "screened": 6, "minted": 1, "refused": 5},
                             "2026-11-14": {"proposed": 0, "accepted": 0, "screened": 0, "minted": 0, "refused": 0}}
    assert out["undated_rows"] == 1, "a record with no readable time is counted as left out, not guessed into a day"
    everything = tool.tally(ROWS, since="2026-01-01T00:00:00Z")
    assert everything["trial_screen"]["refused"] == 6 and everything["proposer"]["by_prompt_version"] == {"v2": 1, "v3": 2}
    assert list(everything["by_day"]) == ["2026-11-10", "2026-11-13", "2026-11-14"]


def test_nothing_but_counts_and_held_names_leaves_a_row(capsys, monkeypatch):
    monkeypatch.setattr(tool, "_rows", lambda root, since: ROWS)
    for flags in ([], ["--json"]):
        assert tool.main(["--since", "2026-01-01", *flags]) == EXIT_OK
        printed = capsys.readouterr().out
        assert SENTINEL not in printed and "987654" not in printed, "no text, score or count from another field"
        assert "funding_flip" in printed and "too_slow" in printed and tool.OTHER in printed
    out = tool.tally(ROWS, since="2026-01-01T00:00:00Z")

    def leaves(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from leaves(value)
        else:
            yield node

    for leaf in leaves(out):
        assert leaf is None or isinstance(leaf, int) or (isinstance(leaf, str) and len(leaf) <= 64 and SENTINEL not in leaf), leaf
    assert json.loads(json.dumps(out)) == out, "the tally is plain JSON"


def test_the_reasons_it_counts_are_the_ones_the_screen_writes():
    """Called, not grepped: a proposal the screen cannot parse is refused under a name this tool keeps."""
    block = factory._screen_trials(
        [{"proposal_id": "p1", "family": "broken_one", "strategy_rule_hash": "h", "proposed_at": "2026-11-13T06:18:00Z",
          "rationale": SENTINEL, "spec": {"nonsense": True}}],
        legs=[{"symbol": "BTCUSDT", "candles": []}, {"symbol": "ETHUSDT", "candles": []}],
        frames_for_legs=lambda: pytest.fail("a proposal that does not parse needs no frame"),
        open_trials=0, fire_hashes=set(), generation_id="g", venue="binance_usdm", now="2026-11-13T08:40:00Z")
    assert block["status"] == "refused" and [r["reason"] for r in block["refused"]] == ["parse"]
    out = tool.tally([_fire("2026-11-13T08:40:00Z", block)], since=SINCE)["trial_screen"]
    assert out["fires_by_status"] == {"refused": 1} and out["refused_by_reason"] == {"parse": 1}
    assert out["refused_by_family"] == {"broken_one": {"parse": 1}}
    not_cohort = factory._screen_trials(
        [{"proposal_id": "p1", "family": "f", "spec": {}}], legs=[{"symbol": "BTCUSDT"}], frames_for_legs=list,
        open_trials=0, fire_hashes=set(), generation_id="g", venue="binance_usdm", now="2026-11-13T08:40:00Z")
    assert tool.tally([_fire("2026-11-13T08:40:00Z", not_cohort)], since=SINCE)["trial_screen"]["fires_by_status"] == {
        "skipped:not_cohort": 1}


def test_a_record_the_proposer_itself_writes_is_counted_with_no_name_lost():
    """The record's writer is called, so the time, the timeframe, the prompt version and the rejection reasons are
    the ones the runtime stores. A name that missed its shape would print as ``(other)`` and hide what this is for."""
    from runtime.mvp_runtime.crypto import proposer

    rows = []
    for timeframe, created in (("4h", "2026-11-13T06:18:00Z"), ("1h", "2026-11-13T14:46:00Z")):
        record = proposer.propose_strategy_families(
            {"symbol": "BTCUSDT", "timeframe": timeframe, "candles": []}, provider=proposer.MockProposerProvider(),
            now=created, existing_families=[])
        rows.append({"kind": PROPOSAL_LEDGER_KIND, "trace_id": "t", "record": record})
    out = tool.tally(rows, since=SINCE)
    block = out["proposer"]
    assert out["undated_rows"] == 0 and block["fires"] == 2 and block["proposed"] == 4
    assert block["by_prompt_version"] == {proposer.PROPOSER_PROMPT_VERSION: 2} and block["by_timeframe"] == {"4h": 1, "1h": 1}
    assert tool.OTHER not in json.dumps(out), "every name the proposer wrote fits the shape it is held to"
    assert block["rejected_by_reason"].get("timeframe") == 2, "the mock's 1h rules scored on a 4h frame"
    assert block["rejected_by_reason"].get("validator") == 1, "the invented indicator, on the 1h frame"
    assert block["accepted"] + block["rejected"] == 4
    for reason in ("ablation_winner_duplicate_rule_hash", "ablation_winner_failed_validation", "unsuppliable_feature",
                   "duplicate_rule_hash", "pace_unreadable", "known_family", "scoring_error", "no_trades", "too_slow"):
        assert tool._reason({"reason": reason}, "reason") == reason, "a reason the screen writes is printed by its name"


def test_it_reads_a_real_ledger_archives_included_and_only_its_two_kinds(tmp_path, capsys):
    ledger = LedgerStore.default(tmp_path)
    for row in ROWS[:8]:
        ledger.append_records("trace", {row["kind"]: row["record"]})
    ledger.append_records("trace", {"crypto_cycle": {"created_at": "2026-11-13T09:00:00Z"}})
    assert tool.main(["--since", "2026-11-13", "--json"], root=tmp_path) == EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["since"] == SINCE and out["trial_screen"]["refused"] == 5 and out["proposer"]["proposed"] == 6
    assert tool.main(["--days", "2"], root=tmp_path, now="2026-11-15T00:00:00Z") == EXIT_OK
    text = capsys.readouterr().out
    assert "기간: 2026-11-13T00:00:00Z 이후" in text and "심사한 제안 6개 → 트라이얼 1개, 거절 5개" in text
    assert tool.main([], root=tmp_path / "empty", now="2026-11-15T00:00:00Z") == EXIT_OK
    assert "제안 0개" in capsys.readouterr().out, "no ledger yet is an empty tally, not an error"


def test_a_typo_is_a_usage_error_and_a_damaged_ledger_blocks(capsys, monkeypatch):
    for argv in (["--nope"], ["--days", "0"], ["--days", "x"], ["--since", "yesterday"], ["--since", "2026-11-13", "--days", "3"]):
        assert tool.main(argv, root=Path("/nonexistent")) == EXIT_USAGE, argv
    capsys.readouterr()
    assert tool.main(["--help"]) == EXIT_OK and "--since" in capsys.readouterr().out

    def damaged(root, since):
        raise MvpRuntimeError("LEDGER_DAMAGED", "a line does not parse")
    monkeypatch.setattr(tool, "_rows", damaged)
    assert tool.main([]) == EXIT_BLOCKED and "BLOCKED LEDGER_DAMAGED" in capsys.readouterr().err
