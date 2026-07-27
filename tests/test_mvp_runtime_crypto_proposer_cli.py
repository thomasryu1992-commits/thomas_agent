"""proposer_cli tests — the on-demand strategy-proposal entry point.

`proposer.py` itself is covered; the CLI wrapping it was not covered at all — the module
measured 0% with no test file so much as importing it. What that left untested is the part
only the CLI does: clamping `--count`/`--candles` before they reach the proposer, choosing
between the report and `--json`, the `--no-ledger` switch, the fail-closed exit when market
data is unavailable, and the reporting-honesty path where the record is produced but the
ledger append fails.

The proposals themselves come from `MockProposerProvider` via the ordinary offline
selection path, so none of this needs a grant, a network call, or a model.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK
from runtime.mvp_runtime.crypto import proposer, proposer_cli
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.store import LedgerStore


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    """Point the module-level `LedgerStore.default()` at a tmp store.

    The CLI has no injection seam, so this is how a test drives the real entry point
    without touching the machine's ledger.
    """
    store = LedgerStore(tmp_path / "ledger")
    monkeypatch.setattr(LedgerStore, "default", classmethod(lambda cls, root=None: store))
    return store


def _proposal_records(store):
    # A ledger row is {"kind": ..., "record": ..., "trace_id": ...} — the kind is a value,
    # not a key, so a `in r` membership test would silently match nothing and make every
    # "did not persist" assertion below vacuously true.
    return [r for r in store.read_records() if r.get("kind") == proposer.PROPOSAL_LEDGER_KIND]


# === the ordinary run ===============================================================

def test_report_run_records_the_proposal(ledger, capsys):
    assert proposer_cli.main(["--count", "2", "--candles", "60"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "strategy family proposals" in out
    # The CLI must not imply an install; the record is a review sheet.
    assert "installs nothing" in out
    assert len(_proposal_records(ledger)) == 1


def test_json_run_emits_the_record(ledger, capsys):
    assert proposer_cli.main(["--json", "--count", "1", "--candles", "60"]) == EXIT_OK
    record = json.loads(capsys.readouterr().out)
    assert record["proposal_id"]
    assert record["record_type"] == "crypto_strategy_family_proposal.v0"


def test_no_ledger_leaves_no_record(ledger, capsys):
    assert proposer_cli.main(["--no-ledger", "--count", "1", "--candles", "60"]) == EXIT_OK
    assert _proposal_records(ledger) == []


# === the CLI's own clamping =========================================================

def test_count_is_clamped_to_the_per_run_maximum(ledger, monkeypatch, capsys):
    """`--count 99` must not reach the proposer as 99 — the cap is the CLI's job."""
    seen = {}
    real = proposer.propose_strategy_families
    monkeypatch.setattr(
        proposer, "propose_strategy_families",
        lambda snapshot, **kw: seen.update(kw) or real(snapshot, **kw),
    )
    assert proposer_cli.main(["--count", "99", "--candles", "60", "--no-ledger"]) == EXIT_OK
    assert seen["count"] == proposer.MAX_PROPOSALS_PER_RUN

    seen.clear()
    assert proposer_cli.main(["--count", "0", "--candles", "60", "--no-ledger"]) == EXIT_OK
    assert seen["count"] == 1, "a non-positive count must floor to 1, not ask for nothing"


def test_candles_are_clamped_to_the_window_maximum(ledger, monkeypatch, capsys):
    seen = {}
    real = proposer_cli.collect_market_data
    monkeypatch.setattr(
        proposer_cli, "collect_market_data",
        lambda symbol, timeframe, **kw: seen.update(kw) or real(symbol, timeframe, **kw),
    )
    assert proposer_cli.main(["--candles", "999999", "--no-ledger", "--count", "1"]) == EXIT_OK
    assert seen["limit"] == proposer_cli.MAX_CANDLES


# === fail-closed and honesty paths ==================================================

def test_market_data_unavailable_blocks(ledger, monkeypatch, capsys):
    """The one refusal path. It is a fail-closed block, so it exits EXIT_BLOCKED (2).

    It used to be a module-private `EXIT_ERROR = 1` declared beside the shared vocabulary;
    nothing consumed it, and it is the shared `EXIT_BLOCKED` now.
    """
    def _unavailable(*a, **kw):
        raise ToolError("MARKET_DATA_UNAVAILABLE", "feed down")

    monkeypatch.setattr(proposer_cli, "select_market_data_collector", _unavailable)
    assert proposer_cli.main([]) == EXIT_BLOCKED
    captured = capsys.readouterr()
    assert "market data unavailable" in captured.err
    assert captured.out == "", "a blocked run must not print a report"
    assert _proposal_records(ledger) == []


def test_ledger_failure_is_reported_not_hidden(ledger, monkeypatch, capsys):
    """A record that was produced but not kept must say so.

    The run still succeeds — the proposals are real and printed — but a report that stayed
    silent here would read as though the record was persisted.
    """
    def _refuse(*a, **kw):
        raise ToolError("LEDGER_WRITE_FAILED", "disk full")

    monkeypatch.setattr(LedgerStore, "append_records", _refuse)
    assert proposer_cli.main(["--count", "1", "--candles", "60"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "LEDGER: NOT recorded" in out
    assert "disk full" in out


def test_ledger_failure_is_also_visible_in_json(ledger, monkeypatch, capsys):
    def _refuse(*a, **kw):
        raise ToolError("LEDGER_WRITE_FAILED", "disk full")

    monkeypatch.setattr(LedgerStore, "append_records", _refuse)
    assert proposer_cli.main(["--json", "--count", "1", "--candles", "60"]) == EXIT_OK
    assert "disk full" in json.loads(capsys.readouterr().out)["persist_error"]
