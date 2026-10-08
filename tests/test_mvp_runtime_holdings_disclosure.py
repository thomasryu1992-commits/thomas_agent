"""H3 — the single-holding rule at the doors' boundary (Thomas 2026-10-08, MULTI_ASSET_EXPANSION H3).

    No externally visible aggregate may reveal a single instrument's amount by construction.

Asserted as the property itself, over synthetic shapes: in what leaves, no row holds one instrument,
and what the total minus the published rows gives back covers at least two holdings — or the total
is withheld too. Then through a real fire: the stored file carries one table, the local file the rest,
and no door module names the local file.
"""

from __future__ import annotations

import ast
import json
import pathlib
import random

import pytest

from runtime.mvp_runtime.holdings import allocation, disclosure, store
from runtime.mvp_runtime.holdings.disclosure import Part

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _covered(parts, names):
    instruments, cash = set(), 0
    for name in names:
        if parts[name].value:
            instruments |= parts[name].instruments
            cash += parts[name].cash_parts
    return len(instruments) + cash


def _assert_no_single_instrument_leaves(parts):
    hidden = disclosure.withheld(parts)
    published = {n for n in parts if n not in hidden and parts[n].value}
    for name in published:   # no published row is one instrument
        part = parts[name]
        assert not (len(part.instruments) == 1 and part.cash_parts == 0), (name, parts)
    if disclosure.whole_is_one_instrument(parts):
        return               # the total is withheld as well; nothing to subtract from
    if hidden and any(parts[n].value for n in hidden):
        # total - sum(published) = sum(hidden): that sum must not be one holding
        assert _covered(parts, hidden) >= 2, (hidden, parts)


SHAPES = {
    "one stock and cash": {"domestic_equity": Part(5.0, frozenset({"toss:d:A"})), "cash": Part(3.0, cash_parts=1)},
    "one stock alone": {"domestic_equity": Part(5.0, frozenset({"toss:d:A"}))},
    "btc in spot and earn, two cash balances": {
        "coin": Part(9.0, frozenset({"binance:BTC"})), "cash": Part(4.0, cash_parts=2)},
    "two singletons": {"gold": Part(2.0, frozenset({"toss:o:GLD"})), "coin": Part(7.0, frozenset({"binance:BTC"}))},
    "two and two": {"global_equity": Part(8.0, frozenset({"toss:o:A", "toss:o:B"})),
                    "coin": Part(3.0, frozenset({"binance:BTC", "binance:ETH"}))},
    "singleton beside a cash-only class": {"bonds": Part(6.0, frozenset({"toss:o:TLT"})),
                                           "engine_margin": Part(1.0, cash_parts=1),
                                           "cash": Part(2.0, cash_parts=2)},
}


@pytest.mark.parametrize("shape", SHAPES.values(), ids=list(SHAPES))
def test_no_single_instrument_amount_leaves_a_decided_shape(shape):
    _assert_no_single_instrument_leaves(shape)


def test_the_decided_shapes_withhold_what_they_must():
    assert disclosure.withheld(SHAPES["two and two"]) == set()
    assert disclosure.withheld(SHAPES["one stock and cash"]) == {"domestic_equity", "cash"}   # the complement
    assert disclosure.whole_is_one_instrument(SHAPES["one stock alone"])
    assert disclosure.withheld(SHAPES["two singletons"]) == {"gold", "coin"}
    # Two singletons hide each other: their sum covers two holdings, so the cash rows stay published.
    shape = SHAPES["singleton beside a cash-only class"]
    assert disclosure.withheld(shape) == {"bonds", "engine_margin"}


def test_no_single_instrument_amount_leaves_any_random_shape():
    rng = random.Random(20261008)
    pool = [f"i{n}" for n in range(6)]
    for _ in range(3000):
        parts = {}
        used: set[str] = set()
        for name in ("a", "b", "c", "d", "e"):
            free = [i for i in pool if i not in used]
            chosen = frozenset(rng.sample(free, rng.randint(0, min(2, len(free)))))
            used |= chosen
            cash = rng.choice((0, 0, 1, 2))
            value = 0.0 if not chosen and not cash else rng.uniform(1, 100)
            parts[name] = Part(value, chosen, cash)
        _assert_no_single_instrument_leaves(parts)


# --- through a real fire ----------------------------------------------------------------------

from tests.test_mvp_runtime_holdings import creds, gate_open  # noqa: E402,F401 — fixtures


@pytest.fixture
def fire(monkeypatch, tmp_path, gate_open):
    from tests.test_mvp_runtime_holdings import NOW, _binance_file, _toss

    _binance_file(tmp_path, margin=100.0, monkeypatch=monkeypatch)
    _toss(monkeypatch)
    return tmp_path, NOW


def test_a_classified_one_stock_class_is_withheld_at_the_door_and_kept_locally(monkeypatch, fire):
    root, now = fire
    # The fixture holds one domestic and one overseas symbol: classified, each class is one instrument.
    monkeypatch.setattr(allocation, "CLASSIFICATION",
                        {"005930": allocation.DOMESTIC_EQUITY, "AAPL": allocation.GLOBAL_EQUITY})
    store.refresh(now=now, root=root)
    outside = json.loads(store.snapshot_path(root).read_text(encoding="utf-8"))["allocation"]
    assert {"domestic_equity", "global_equity"} <= set(outside["withheld"])
    for name in ("domestic_equity", "global_equity"):
        assert outside["classes"][name]["krw"] is None and outside["classes"][name]["weight_pct"] is None
    assert "equity" not in (outside["bands"] or {})
    inside = json.loads(store.local_path(root).read_text(encoding="utf-8"))["allocation"]
    assert inside["classes"]["domestic_equity"]["krw"] == 7_200_000
    text, data = store.load_holdings_view(now=now, root=root)
    assert "7,200,000" not in text + json.dumps(data) and "withheld" in text


def test_the_doors_file_carries_no_other_breakdown(monkeypatch, fire):
    root, now = fire
    store.refresh(now=now, root=root)
    body = json.loads(store.snapshot_path(root).read_text(encoding="utf-8"))
    assert not disclosure.LOCAL_ONLY_TOP_KEYS & set(body)
    assert not disclosure.LOCAL_ONLY_COMBINED_KEYS & set(body["combined"])
    assert not disclosure.LOCAL_ONLY_ALLOCATION_KEYS & set(body["allocation"])


def test_a_portfolio_of_one_instrument_withholds_its_total():
    body = {"combined": {"combined_total_krw": 9.0, "peak_total_krw": 9.0, "drawdown_pct": 0.0},
            "allocation": {"complete": True, "total_krw": 9.0, "classes": {
                "coin": {"krw": 9.0, "weight_pct": 100.0, "target_pct": 2.5, "drift_pp": 97.5}},
                "bands": {"coin": {"weight_pct": 100.0}}, "outside_band": ["coin"], "notes": []}}
    out = disclosure.external_view(body, {"coin": Part(9.0, frozenset({"binance:BTC"}))})
    assert out["combined"]["combined_total_krw"] is None and out["combined"]["peak_total_krw"] is None
    assert out["allocation"]["total_krw"] is None and out["allocation"]["classes"]["coin"]["krw"] is None
    assert out["allocation"]["bands"] == {} and out["allocation"]["outside_band"] == []


def test_a_wallet_class_without_its_makeup_is_treated_as_one_instrument():
    """Fail closed: a value whose makeup is unknown is withheld rather than guessed safe."""
    from runtime.mvp_runtime.holdings.model import HoldingsSnapshot
    snap = HoldingsSnapshot(account="****", broker="toss", domestic=None, overseas=None, holdings=(),
                            collected_at="t", latency_ms=0, usd_krw_rate=1.0)
    block = {"crypto_status": "ok", "crypto_futures_krw": 0.0, "crypto_wallet_status": "ok",
             "crypto_classes_krw": {"btc": 5.0, "eth": 0.0, "stable": 0.0, "other": 0.0},
             "portfolio_nav_complete": True}
    _, parts = allocation.allocate_with_parts(snap, {}, block, wallet=None)
    assert len(parts["coin"].instruments) == 1


def test_no_door_module_names_the_local_file():
    """The local file is the terminal's. Only the store that writes it and the script that renders it
    may name it; a door that did would carry the whole fire past the boundary."""
    allowed = {ROOT / "runtime" / "mvp_runtime" / "holdings" / "store.py", ROOT / "scripts" / "holdings_board.py"}
    names = {"load_local_view", "LOCAL_FILENAME", "local_path"}
    offenders = []
    for path in [*(ROOT / "runtime").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports_store = any(isinstance(n, ast.ImportFrom) and (n.module or "").endswith("holdings.store")
                            or isinstance(n, ast.ImportFrom) and any(a.name == "store" for a in n.names)
                            and "holdings" in (n.module or "")
                            for n in ast.walk(tree))
        used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        literal = any(isinstance(n, ast.Constant) and n.value == store.LOCAL_FILENAME for n in ast.walk(tree))
        if literal or (imports_store and used & names):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []
    tree = ast.parse((ROOT / "runtime" / "mvp_runtime" / "domain_console.py").read_text(encoding="utf-8"))
    assert "load_holdings_view" in {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
