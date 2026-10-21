"""The research lake export (RESEARCH_SANDBOX_V0.1.md §4.1): an allowlist of research records, nothing else.

Every test passes explicit temporary paths: the defaults name the live state directory and the host's
measurement directory, and a test must never reach either.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ops"))
import export_research_lake as lake  # noqa: E402

NOW = 1_792_499_000.0
# The export runs from root's cron on the Linux host: symlinks, modes and ownership are POSIX. The
# allowlist tests stay platform-independent.
posix_only = pytest.mark.skipif(sys.platform != "linux", reason="the export runs on the Linux host only")  # 2026-10-20T…Z, fixed so ids are deterministic


def _owners():
    from runtime.mvp_runtime.crypto import (
        counterfactual, forward_book, forward_cohort, forward_cohort_null, forward_trial, oi_store,
        orderbook_store, paper, pool_state, positioning_store,
    )
    return {
        pool_state.CANDIDATES_FILENAME,
        forward_cohort.COHORTS_FILENAME, forward_cohort.OUTCOMES_FILENAME, forward_cohort.POSITIONS_FILENAME,
        forward_cohort_null.NULLS_FILENAME, forward_cohort_null.NULL_OUTCOMES_FILENAME,
        forward_cohort_null.NULL_POSITIONS_FILENAME,
        forward_trial.TRIAL_OUTCOMES_FILENAME, forward_trial.TRIAL_POSITIONS_FILENAME,
        forward_trial.TRIAL_NULL_OUTCOMES_FILENAME, forward_trial.TRIAL_NULL_POSITIONS_FILENAME,
        forward_trial.TRIAL_CLOSES_FILENAME,
        forward_book.OUTCOMES_FILENAME, forward_book.BOOK_FILENAME,
        paper.OUTCOMES_FILENAME, paper.POSITION_FILENAME,
        counterfactual.OUTCOMES_FILENAME, counterfactual.BOOK_FILENAME,
        oi_store.OI_1H_FILENAME, positioning_store.POSITIONING_FILENAME, orderbook_store.ORDERBOOK_FILENAME,
    }


def _sensitive():
    from runtime.mvp_runtime.crypto import (
        account_store, execution_stage, live_budget, live_correction, live_evidence, live_ledger,
        live_order_stores, pool_state, pre_order_gate, probe, protection_watch, risk_limits,
        testnet_evidence, venue_contract,
    )
    return {
        account_store.SNAPSHOT_FILENAME, account_store.REFRESH_MARK_FILENAME,
        execution_stage.EXECUTION_STAGE_FILENAME, execution_stage.LEDGER_FILENAME, execution_stage.ANCHOR_FILENAME,
        live_budget.LIVE_BUDGET_FILENAME, live_correction.CORRECTIONS_FILENAME,
        live_evidence.CANARY_ORDERS_FILENAME, live_ledger.LIVE_OUTCOMES_FILENAME,
        live_order_stores.API_BREAKER_FILENAME, live_order_stores.BRACKET_BREAKER_FILENAME,
        live_order_stores.COUNTER_FILENAME, live_order_stores.ENTRY_MARKS_FILENAME,
        pool_state.POOL_FILENAME, pre_order_gate.SNAPSHOT_FILENAME, probe.PLAN_FILENAME,
        protection_watch.WATCH_FILENAME, risk_limits.RISK_LIMITS_FILENAME,
        testnet_evidence.EVIDENCE_FILENAME, venue_contract.CONTRACT_FILENAME,
    }


def test_the_allowlist_is_exactly_the_research_stores_named_by_their_owners():
    assert set(lake.ALLOWED) == _owners()
    assert len(lake.ALLOWED) == len(set(lake.ALLOWED))


def test_no_account_live_stage_or_approval_file_is_allowed():
    assert not set(lake.ALLOWED) & _sensitive()


def _state(tmp_path: Path) -> tuple[Path, Path]:
    """A fake state root: research files, sensitive files, a testnet tree, holdings, a symlink out."""
    root = tmp_path / "state"
    crypto = root / "crypto"
    crypto.mkdir(parents=True)
    for name in lake.ALLOWED[:-1]:   # the last one is absent on purpose
        (crypto / name).write_text(f'{{"name": "{name}"}}\n', encoding="utf-8")
    for name in ("account_snapshot.json", "live_outcomes.jsonl", "execution_stage.json",
                 "live_trading_budget.json", "routing_marks.json"):
        (crypto / name).write_text("SECRET-LOOKING\n", encoding="utf-8")
    (crypto / "venues" / "binance_futures_testnet").mkdir(parents=True)
    (crypto / "venues" / "binance_futures_testnet" / lake.ALLOWED[0]).write_text("testnet\n", encoding="utf-8")
    (root / "holdings").mkdir()
    (root / "holdings" / "holdings_local.json").write_text("HOLDINGS\n", encoding="utf-8")
    outside = tmp_path / "outside.jsonl"
    outside.write_text("OUTSIDE\n", encoding="utf-8")
    victim = crypto / lake.ALLOWED[1]
    victim.unlink()
    victim.symlink_to(outside)
    return crypto, tmp_path / "lake"


@posix_only
def test_a_snapshot_holds_the_allowed_files_and_nothing_else(tmp_path):
    crypto, dest = _state(tmp_path)
    before = {p.name: p.read_bytes() for p in crypto.iterdir() if p.is_file() and not p.is_symlink()}
    status = lake.export(crypto, dest, now=NOW, group=None)
    snap = dest / status["snapshot"]
    copied = {p.name for p in snap.iterdir()} - {lake.MANIFEST}
    assert status["result"] == "ok"
    assert copied == set(lake.ALLOWED) - {lake.ALLOWED[1], lake.ALLOWED[-1]}
    assert status["skipped"] == {lake.ALLOWED[1]: "symlink, not followed", lake.ALLOWED[-1]: "absent"}
    text = "".join(p.read_text(encoding="utf-8") for p in snap.iterdir())
    assert "SECRET-LOOKING" not in text and "HOLDINGS" not in text and "OUTSIDE" not in text
    assert "testnet" not in text
    # The source is untouched.
    assert before == {p.name: p.read_bytes() for p in crypto.iterdir() if p.is_file() and not p.is_symlink()}


@posix_only
def test_the_manifest_hashes_what_was_written_and_the_modes_are_group_read_only(tmp_path):
    crypto, dest = _state(tmp_path)
    status = lake.export(crypto, dest, now=NOW, group=None)
    snap = dest / status["snapshot"]
    manifest = json.loads((snap / lake.MANIFEST).read_text(encoding="utf-8"))
    for name, row in manifest["files"].items():
        data = (snap / name).read_bytes()
        assert row["sha256"] == hashlib.sha256(data).hexdigest() and row["bytes"] == len(data)
        assert oct((snap / name).stat().st_mode & 0o777) == "0o640"
    assert oct(snap.stat().st_mode & 0o777) == "0o750"
    assert os.readlink(dest / lake.CURRENT) == status["snapshot"]


@posix_only
def test_only_the_newest_snapshots_are_kept_and_current_moves(tmp_path):
    crypto, dest = _state(tmp_path)
    ids = [lake.export(crypto, dest, now=NOW + 3600 * k, keep=2, group=None)["snapshot"] for k in range(4)]
    kept = sorted(p.name for p in dest.iterdir() if lake.SNAPSHOT_ID.match(p.name))
    assert kept == ids[-2:] and os.readlink(dest / lake.CURRENT) == ids[-1]
    (dest / "notes").mkdir()                       # anything not shaped like an id is left alone
    lake.prune(dest, keep=1)
    assert (dest / "notes").is_dir()


@posix_only
def test_over_the_size_cap_nothing_is_written_and_the_status_says_why(tmp_path):
    crypto, dest = _state(tmp_path)
    status = lake.export(crypto, dest, now=NOW, max_bytes=10, group=None)
    assert status["result"].startswith("skipped: ") and not (dest / status["snapshot"]).exists()
    assert json.loads((dest / lake.STATUS).read_text(encoding="utf-8"))["result"] == status["result"]


@posix_only
@pytest.mark.parametrize("make_src,make_dest", [
    (lambda t: t / "state", lambda t: t / "lake"),                     # not the crypto directory
    (lambda t: t / "state" / "crypto", lambda t: t / "state" / "crypto" / "lake"),   # dest inside src
])
def test_a_misconfigured_run_is_refused(tmp_path, make_src, make_dest):
    _state(tmp_path)
    with pytest.raises(ValueError):
        lake.export(make_src(tmp_path), make_dest(tmp_path), now=NOW, group=None)


@posix_only
def test_main_with_explicit_paths(tmp_path, capsys):
    crypto, dest = _state(tmp_path)
    assert lake.main(["--src", str(crypto), "--dest", str(dest), "--group", ""]) == 0
    assert "ok" in capsys.readouterr().out
    assert lake.main(["--src", str(crypto), "--dest", str(dest), "--group", "", "--list"]) == 0
    assert "skip  " + lake.ALLOWED[1] in capsys.readouterr().out
