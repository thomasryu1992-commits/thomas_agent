"""H5a — Thomas's classification entries, host-local (Thomas 2026-10-08, MULTI_ASSET_EXPANSION H5a).

The mechanism is code; the entries name holdings, so they live in a state file Thomas writes from his
terminal and never leave this host. Asserted here: the file's rules, that an entry places a wallet asset
asset by asset, that conflicts and an unreadable file withhold the verdict, that the unclassified count is
assets not sub-classes, and that no entry reaches the doors' file or a door module.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import allocation, binance_wallet, classification, store
from runtime.mvp_runtime.holdings.model import HoldingsSnapshot

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = "2026-10-08T09:00:00Z"
RATE = 1000.0


def test_the_assignable_classes_are_the_allocations_less_the_engine_margin():
    assert classification.ASSIGNABLE_CLASSES == set(allocation.CLASSES) - {allocation.ENGINE_MARGIN}


def test_no_file_is_nothing_classified(tmp_path):
    loaded = classification.load(tmp_path)
    assert loaded.mapping_version == 0 and dict(loaded.entries) == {}


def test_apply_writes_moves_the_version_once_and_removes(tmp_path):
    first = classification.apply(tmp_path, assign={"binance:SOL": "coin", "binance:LINK": "coin"}, now=NOW)
    assert first.mapping_version == 1 and dict(first.entries) == {"binance:SOL": "coin", "binance:LINK": "coin"}
    second = classification.apply(tmp_path, remove=("binance:LINK",), now=NOW)
    assert second.mapping_version == 2 and dict(second.entries) == {"binance:SOL": "coin"}
    raw = json.loads(classification.path(tmp_path).read_text(encoding="utf-8"))
    assert raw["entries"]["binance:SOL"]["approved_at"] == NOW


@pytest.mark.parametrize("assign", [{"binance:SOL": "engine_margin"}, {"binance:SOL": "crypto"},
                                    {"SOL": "coin"}, {"toss:asia:X": "gold"}])
def test_an_entry_it_cannot_place_is_refused_before_anything_is_written(tmp_path, assign):
    with pytest.raises(ToolError) as exc:
        classification.apply(tmp_path, assign=assign)
    assert exc.value.reason_code == classification.CLASSIFICATION_REFUSED
    assert not classification.path(tmp_path).exists()


@pytest.mark.parametrize("body", ["{not json", json.dumps({"record_type": "x"}),
                                  json.dumps({"record_type": classification.RECORD_TYPE, "mapping_version": 1,
                                              "entries": {"binance:SOL": {"asset_class": "crypto"}}})])
def test_an_unreadable_file_raises_and_names_no_entry(tmp_path, body):
    classification.path(tmp_path).write_text(body, encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        classification.load(tmp_path)
    assert exc.value.reason_code == classification.CLASSIFICATION_UNREADABLE and "SOL" not in str(exc.value)


# --- the allocation, asset by asset ---------------------------------------------------------------

def _wallet(**asset_usdt):
    classes = {name: 0.0 for name in binance_wallet.CLASSES}
    members: dict[str, set[str]] = {}
    for asset, usdt in asset_usdt.items():
        sub = binance_wallet.asset_class(asset)
        classes[sub] += usdt
        members.setdefault(sub, set()).add(asset)
    return binance_wallet.WalletSnapshot(
        spot_usdt=classes, earn_usdt={name: 0.0 for name in binance_wallet.CLASSES}, unpriced_assets=0,
        collected_at=NOW, latency_ms=0, class_assets={k: frozenset(v) for k, v in members.items()},
        asset_usdt=dict(asset_usdt))


def _block(wallet):
    return {"crypto_status": "ok", "crypto_futures_krw": 0.0, "crypto_wallet_status": "ok",
            "crypto_classes_krw": {k: v * RATE for k, v in wallet.spot_usdt.items()},
            "portfolio_nav_complete": True}


def _toss():
    return HoldingsSnapshot(account="****", broker="toss", domestic=None, overseas=None, holdings=(),
                            collected_at=NOW, latency_ms=0, usd_krw_rate=RATE)


def _allocate(wallet, entries=None, error=None):
    return allocation.allocate_with_parts(_toss(), {"krw_cash": 100_000.0}, _block(wallet), wallet,
                                          entries, classification_error=error)[0]


def test_unclassified_wallet_assets_are_counted_one_by_one():
    block = _allocate(_wallet(BTC=10.0, SOL=5.0, LINK=3.0))
    assert block["unclassified_count"] == 2 and block["unclassified_krw"] == 8.0 * RATE
    assert block["complete"] is False and block["mapping_version"] == 0


def test_entries_place_the_rest_and_restore_the_band_verdict():
    entries = classification.Classification(mapping_version=3,
                                            entries={"binance:SOL": "coin", "binance:LINK": "coin"})
    block = _allocate(_wallet(BTC=10.0, SOL=5.0, LINK=3.0, USDT=2.0), entries)
    assert block["unclassified_count"] == 0 and block["complete"] is True
    assert block["classes"]["coin"]["krw"] == 18.0 * RATE
    assert block["classes"]["cash"]["krw"] == 100_000.0 + 2.0 * RATE
    assert block["bands"] is not None and block["mapping_version"] == 3


def test_an_entry_against_the_built_in_placement_is_a_conflict():
    entries = classification.Classification(mapping_version=1, entries={"binance:BTC": "gold"})
    block = _allocate(_wallet(BTC=10.0), entries)
    assert block["complete"] is False and "classification conflict" in block["notes"][0]


def test_an_unreadable_file_withholds_the_verdict():
    block = _allocate(_wallet(BTC=10.0), None, error=classification.CLASSIFICATION_UNREADABLE)
    assert block["complete"] is False and "classification file unreadable" in block["notes"][0]


def test_a_toss_entry_places_a_toss_symbol(monkeypatch):
    from runtime.mvp_runtime.holdings.model import Holding
    snap = HoldingsSnapshot(account="****", broker="toss", domestic=None, overseas=None, collected_at=NOW,
                            latency_ms=0, usd_krw_rate=RATE,
                            holdings=(Holding(market="domestic", symbol="069500", name="x", quantity=1.0,
                                              value=50_000.0, unrealized_pnl=0.0, currency="KRW"),))
    entries = classification.Classification(1, {"toss:domestic:069500": "domestic_equity"})
    wallet = _wallet(BTC=10.0)
    block = allocation.allocate(snap, {"krw_cash": 0.0}, _block(wallet), wallet, entries)
    assert block["classes"]["domestic_equity"]["krw"] == 50_000.0 and block["unclassified_count"] == 0


# --- the boundary -------------------------------------------------------------------------------

def test_an_entry_never_reaches_the_doors_file(monkeypatch, tmp_path, gate_open):
    from tests.test_mvp_runtime_holdings import _binance_file, _toss as fake_toss

    _binance_file(tmp_path, margin=100.0, monkeypatch=monkeypatch)
    fake_toss(monkeypatch)
    wallet = _wallet(BTC=10.0, ZZTOP=5.0)
    monkeypatch.setattr(binance_wallet, "read_wallet", lambda **_kw: (wallet, None))
    classification.apply(store.state_dir(tmp_path), assign={"binance:ZZTOP": "coin"}, now=NOW)
    store.refresh(now=NOW, root=tmp_path)
    raw = store.snapshot_path(tmp_path).read_text(encoding="utf-8")
    assert "ZZTOP" not in raw and json.loads(raw)["allocation"]["mapping_version"] == 1
    assert "ZZTOP" not in store.local_path(tmp_path).read_text(encoding="utf-8")


from tests.test_mvp_runtime_holdings import creds, gate_open  # noqa: E402,F401 — fixtures


def test_no_door_module_reads_the_classification_file():
    allowed = {ROOT / "runtime" / "mvp_runtime" / "holdings" / name
               for name in ("store.py", "allocation.py", "classification.py")} | {ROOT / "scripts" / "holdings_board.py"}
    offenders = []
    for path in [*(ROOT / "runtime").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        imports = any(isinstance(n, ast.ImportFrom) and ("classification" in (n.module or "")
                      or any(a.name == "classification" for a in n.names) and "holdings" in (n.module or ""))
                      for n in ast.walk(tree))
        literal = any(isinstance(n, ast.Constant) and n.value == classification.FILENAME for n in ast.walk(tree))
        if literal or (imports and "classification" in names):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


# --- the terminal commands ------------------------------------------------------------------------

def test_classify_writes_as_the_service_user_and_refuses_a_bad_entry(monkeypatch, tmp_path, capsys):
    from runtime.mvp_runtime.cli_common import EXIT_BLOCKED
    from scripts import holdings_board

    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(holdings_board, "assert_not_foreign_root_run", lambda *a, **k: None)
    assert holdings_board.main(["--classify", "binance:SOL=coin"]) == 0
    assert "mapping_version 1" in capsys.readouterr().out
    assert holdings_board.main(["--classify", "binance:SOL=crypto"]) == EXIT_BLOCKED
    assert holdings_board.main(["--unclassify", "binance:SOL"]) == 0
    assert dict(classification.load(store.state_dir(tmp_path)).entries) == {}


def test_classify_refuses_a_foreign_root_run(monkeypatch, tmp_path):
    from runtime.mvp_runtime.cli_common import EXIT_BLOCKED
    from runtime.mvp_runtime.errors import MvpRuntimeError
    from scripts import holdings_board

    def _refuse(*_a, **_k):
        raise MvpRuntimeError("STATE_FOREIGN_ROOT_RUN", "run it in the container")

    monkeypatch.setattr(holdings_board, "assert_not_foreign_root_run", _refuse)
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    assert holdings_board.main(["--classify", "binance:SOL=coin"]) == EXIT_BLOCKED
    assert not classification.path(store.state_dir(tmp_path)).exists()


def test_unclassified_lists_ids_and_no_amount(monkeypatch, tmp_path, capsys):
    from scripts import holdings_board

    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    wallet = _wallet(BTC=10.0, SOL=5.25, LINK=3.0)
    monkeypatch.setattr(binance_wallet, "read_wallet", lambda **_kw: (wallet, None))
    classification.apply(store.state_dir(tmp_path), assign={"binance:LINK": "coin"}, now=NOW)
    assert holdings_board.main(["--unclassified"]) == 0
    out = capsys.readouterr().out
    assert "1 wallet asset(s): binance:SOL" in out and "LINK" not in out.split("unclassified")[1].split("\n")[0]
    assert "5.25" not in out and "5250" not in out


# --- D-C step 3: won/dollar exposure, local only (Thomas 2026-10-20) -------------------------------

def test_an_entry_may_name_its_currency_and_a_file_without_one_reads_as_before(tmp_path):
    classification.apply(tmp_path, assign={"binance:SOL": "coin"}, now=NOW)
    assert dict(classification.load(tmp_path).currencies) == {}
    loaded = classification.apply(tmp_path, assign={"toss:domestic:T10": "bonds"},
                                  currency={"toss:domestic:T10": "usd"}, now=NOW)
    assert dict(loaded.currencies) == {"toss:domestic:T10": "usd"} and loaded.entries["binance:SOL"] == "coin"
    raw = json.loads(classification.path(tmp_path).read_text(encoding="utf-8"))
    assert raw["entries"]["toss:domestic:T10"]["currency"] == "usd" and "currency" not in raw["entries"]["binance:SOL"]
    again = classification.apply(tmp_path, assign={"toss:domestic:T10": "bonds"}, now=NOW)
    assert dict(again.currencies) == {}   # re-entered without a currency: the old one does not linger


@pytest.mark.parametrize("currency", [{"toss:domestic:T10": "jpy"}, {"binance:OTHER": "usd"}])
def test_a_currency_it_cannot_place_is_refused_before_anything_is_written(tmp_path, currency):
    with pytest.raises(ToolError) as exc:
        classification.apply(tmp_path, assign={"toss:domestic:T10": "bonds"}, currency=currency)
    assert exc.value.reason_code == classification.CLASSIFICATION_REFUSED
    assert not classification.path(tmp_path).exists()


def test_a_file_with_an_unknown_currency_is_unreadable_and_names_no_entry(tmp_path):
    classification.path(tmp_path).write_text(json.dumps({
        "record_type": classification.RECORD_TYPE, "mapping_version": 1,
        "entries": {"toss:domestic:T10": {"asset_class": "bonds", "currency": "jpy"}}}), encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        classification.load(tmp_path)
    assert exc.value.reason_code == classification.CLASSIFICATION_UNREADABLE and "T10" not in str(exc.value)


def _toss_with(*holdings, usd_cash=0.0):
    from runtime.mvp_runtime.holdings.model import Holding
    rows = tuple(Holding(market="domestic", symbol=symbol, name="x", quantity=1.0, value=value,
                         unrealized_pnl=0.0, currency="KRW") for symbol, value in holdings)
    snap = HoldingsSnapshot(account="****", broker="toss", domestic=None, overseas=None, holdings=rows,
                            collected_at=NOW, latency_ms=0, usd_krw_rate=RATE)
    return snap, {"krw_cash": 100_000.0, "usd_cash_krw": usd_cash}


def test_exposure_follows_the_class_default_and_an_entry_overrides_it():
    snap, view = _toss_with(("KB3", 200_000.0), ("UST", 100_000.0), ("GLH", 50_000.0), usd_cash=30_000.0)
    entries = classification.Classification(
        2, {"toss:domestic:KB3": "bonds", "toss:domestic:UST": "bonds", "toss:domestic:GLH": "global_equity"},
        {"toss:domestic:KB3": "krw", "toss:domestic:UST": "usd", "toss:domestic:GLH": "krw"})
    wallet = _wallet(BTC=10.0, USDT=2.0)
    block = allocation.allocate(snap, view, {**_block(wallet), "crypto_futures_krw": 5_000.0}, wallet, entries)
    exposure = block["currency_exposure"]
    # won: Toss KRW cash, the won bond, the hedged (krw-tagged) equity fund
    assert exposure["rows"]["krw"]["krw"] == 100_000.0 + 200_000.0 + 50_000.0
    # dollar: the won-listed Treasury fund, Toss USD cash, the futures margin, BTC and the stablecoin
    assert exposure["rows"]["usd"]["krw"] == 100_000.0 + 30_000.0 + 5_000.0 + 12.0 * RATE
    assert exposure["complete"] is True and exposure["notes"] == []
    assert exposure["total_krw"] == block["total_krw"]
    assert sum(row["weight_pct"] for row in exposure["rows"].values()) == pytest.approx(100.0, abs=0.02)


def test_a_bond_without_a_currency_leaves_the_line_partial_not_guessed():
    snap, view = _toss_with(("KB3", 200_000.0))
    entries = classification.Classification(1, {"toss:domestic:KB3": "bonds"})
    wallet = _wallet(BTC=10.0)
    block = allocation.allocate(snap, view, _block(wallet), wallet, entries)
    exposure = block["currency_exposure"]
    assert block["complete"] is True                       # the class table does not depend on it
    assert exposure["complete"] is False and exposure["rows"]["usd"]["weight_pct"] is None
    assert "1 bond(s) without a currency entry" in exposure["notes"][0]
    assert exposure["rows"]["krw"]["krw"] == 100_000.0     # the untagged bond is in neither row


def test_a_partial_allocation_gives_no_currency_weights():
    block = _allocate(_wallet(BTC=10.0, SOL=5.0))          # SOL unclassified
    assert block["complete"] is False
    assert block["currency_exposure"]["complete"] is False
    assert "allocation partial" in block["currency_exposure"]["notes"][0]


def test_the_currency_line_reaches_the_local_board_and_never_the_doors_file(monkeypatch, tmp_path, gate_open):
    from runtime.mvp_runtime.holdings import board
    from tests.test_mvp_runtime_holdings import _binance_file, _toss as fake_toss

    _binance_file(tmp_path, margin=100.0, monkeypatch=monkeypatch)
    fake_toss(monkeypatch)
    wallet = _wallet(BTC=10.0, USDT=3.0)
    monkeypatch.setattr(binance_wallet, "read_wallet", lambda **_kw: (wallet, None))
    store.refresh(now=NOW, root=tmp_path)
    outward = json.loads(store.snapshot_path(tmp_path).read_text(encoding="utf-8"))
    local = json.loads(store.local_path(tmp_path).read_text(encoding="utf-8"))
    assert "currency_exposure" not in outward["allocation"]
    assert set(local["allocation"]["currency_exposure"]) == allocation.CURRENCY_KEYS
    assert not any(line.startswith("currency") for line in board.render_allocation(outward["allocation"]))
    # The commands themselves: the terminal's --local board shows it; the doors' view does not.
    local_board = store.load_local_view(now=NOW, root=tmp_path).splitlines()
    assert any(line.startswith("currency") and "USD" in line for line in local_board)
    door_text, door_data = store.load_holdings_view(now=NOW, root=tmp_path)
    assert not any(line.startswith("currency") for line in door_text.splitlines())
    assert "currency_exposure" not in json.dumps(door_data)


def test_classify_takes_an_optional_currency(monkeypatch, tmp_path):
    from runtime.mvp_runtime.cli_common import EXIT_BLOCKED
    from scripts import holdings_board

    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(holdings_board, "assert_not_foreign_root_run", lambda *a, **k: None)
    assert holdings_board.main(["--classify", "toss:domestic:T10=bonds:usd", "binance:SOL=coin"]) == 0
    loaded = classification.load(store.state_dir(tmp_path))
    assert dict(loaded.currencies) == {"toss:domestic:T10": "usd"} and loaded.entries["binance:SOL"] == "coin"
    assert holdings_board.main(["--classify", "toss:domestic:T10=bonds:jpy"]) == EXIT_BLOCKED
    assert classification.load(store.state_dir(tmp_path)).mapping_version == 1
