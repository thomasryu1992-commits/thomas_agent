"""P2: the Toss account and the Binance futures account as one KRW total, and its drawdown from peak.

Thomas 2026-10-07 (``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``, "결정 (Thomas 2026-10-07) — P2"):
alert only, a drawdown limit of -20 % from peak, no asset-class weight cap, and Toss's display mid-rate
for the USDT conversion. The limit acts on nothing: it is a line on the board and one Telegram message
per edge. No door reads it, so it stays inside the research pause's "measurement and display" exemption.

**The Binance side is a file, not an import** (`EXPANSION_READINESS_REVIEW_V0.1.md` Q4, Thomas
2026-10-06). The scheduler's crypto fire writes ``crypto/account_snapshot.json`` every 15 minutes
(``crypto/account_store.py``, the format's authority); this module reads it by path and never imports
``crypto/``, so the board loads no live-path module. (This sentence used to end "and the Binance key and
the Toss key stay in different processes". Since 2026-10-07 they do not: the wallet read below shares the
account key pair with the risk lane — Thomas, option A, appendix C of ``MULTI_ASSET_EXPANSION_V0.1.md``.
The order key and the live switch still stay on the risk lane.) The fields read are :data:`BINANCE_SNAPSHOT_FIELDS`, pinned by a test against the
authority, as are the stale window and the record type.

**USDT is taken as one US dollar** and converted with the same Toss mid-rate the Toss side used in the
same read — one rate per fire, so the two KRW figures on one board never rest on two rates. The rate
itself is never stored.

**The Binance spot wallet and Simple Earn join the total when their gate is open** (Thomas 2026-10-07,
appendix C; ``binance_wallet``). They are valued at the same Toss mid-rate, USDT as one US dollar. With
the gate open, the wallet is a required part: a failed read, an unread Earn or no rate makes the total
incomplete, exactly as a missing futures file does. With the gate closed the wallet is a declared source
left out, so since H2 the total is not a portfolio NAV either (below).

**Peak and drawdown are judged only on a complete, fresh pair.** A Toss read that is partial, or a
Binance file that is absent, unreadable, not USDT, unconfigured or older than the stale window, makes
the state ``unknown`` and leaves the peak alone: a missing part would otherwise read as a drop, and a
peak raised on a partial total would hide a real drop later. The first complete total initializes the
peak with no verdict.

**H2: the total is a portfolio NAV only when the declared scope is all in it** (Thomas 2026-10-08,
``MULTI_ASSET_EXPANSION_V0.1.md`` H2, D-H2-1…8). Completeness is judged against
:data:`PORTFOLIO_SCOPE` — the sources Thomas declared — never against which readers exist or which gates
are on. So one ``complete`` is split in four, and the old key stays as an alias of the last:

- ``source_fetch_complete``: every source whose gate is on answered. This is what ``complete`` used to mean.
- ``coverage_complete``: every declared source is in the total. A declared source whose gate is off is
  *excluded*, with its reason under ``sources``.
- ``valuation_complete``: every included source is fully valued — no partial Toss read or parse warning,
  a rate, USDT margin, and a wallet with no unpriced asset, no truncated Earn list and no invalid row.
- ``freshness_complete``: the one input that is not this fire's own read — the futures snapshot file — is
  inside its stale window. Toss and the wallet are read live by this fire, so they have no window.

``portfolio_nav_complete`` is the AND of :data:`REQUIRED_CHECKS`, and ``checks`` lists every check with
``PASS``/``FAIL``/``NOT_EVALUATED``. Snapshot coherence and reconciliation are H4's; they show
``NOT_EVALUATED`` and are not required until H4 adds them to :data:`REQUIRED_CHECKS` (D-H2-6). While the
NAV is incomplete, ``combined_total_krw`` is ``None``, the peak does not move and there is no drawdown
verdict (D-H2-8). No summed partial figure is produced: the parts are each on the board already, and a
sum of some of them is the number most easily mistaken for the whole (D-H2-7).

**The peak belongs to a scope.** ``holdings_peak.json`` carries :data:`PORTFOLIO_SCOPE_VERSION`. A peak
from another scope (or from before H2, which has none) is not compared against: the next complete
total initializes a new one, so a change of scope never reads as a gain or a drop.

**Deposits and withdrawals are drawdowns to this metric.** These two snapshots carry no cash flows,
so a withdrawal reads as a fall from peak. The documented answer is ``scripts/holdings_board.py
--reset-peak`` after a deposit or withdrawal; the alert says so.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import timeutil

# The Binance snapshot's format authority is crypto/account_store.py; these mirror it by value, and
# tests/test_mvp_runtime_holdings.py asserts each one equals the authority's.
BINANCE_SNAPSHOT_REL = ".runtime_governance_state/crypto/account_snapshot.json"
BINANCE_RECORD_TYPE = "account_snapshot.v0"
BINANCE_STALE_SECONDS = 2 * 60 * 60
BINANCE_ASSET = "USDT"
# The fields this module reads, exactly.
BINANCE_SNAPSHOT_FIELDS = frozenset({"record_type", "configured", "asset", "margin_balance", "as_of"})

DRAWDOWN_LIMIT_PCT = -20.0

# H2 (Thomas 2026-10-08, D-H2-1/2): the declared portfolio scope. A reader existing in the code does not
# put its source here; Thomas's scope change does, with a new version.
SOURCE_TOSS = "toss"
SOURCE_FUTURES = "binance_futures"
SOURCE_SPOT = "binance_spot"
SOURCE_EARN = "binance_earn"
PORTFOLIO_SCOPE_VERSION = "v1"
PORTFOLIO_SCOPE = (SOURCE_TOSS, SOURCE_FUTURES, SOURCE_SPOT, SOURCE_EARN)

CHECK_COVERAGE = "coverage"
CHECK_VALUATION = "valuation"
CHECK_FRESHNESS = "freshness"
CHECK_COHERENCE = "coherence"
CHECK_RECONCILIATION = "reconciliation"
CHECKS = (CHECK_COVERAGE, CHECK_VALUATION, CHECK_FRESHNESS, CHECK_COHERENCE, CHECK_RECONCILIATION)
# D-H2-4/5: H4 appends coherence and reconciliation here, and implements them.
REQUIRED_CHECKS = (CHECK_COVERAGE, CHECK_VALUATION, CHECK_FRESHNESS)
PASS = "PASS"
FAIL = "FAIL"
NOT_EVALUATED = "NOT_EVALUATED"

# Why a declared source is not in the total (``sources[<id>]["reason"]``).
EXCLUDED_GATE_OFF = "gate_off"
EXCLUDED_READ_FAILED = "read_failed"

PEAK_FILENAME = "holdings_peak.json"
ALERT_MARK_FILENAME = "holdings_drawdown_told.json"

STATE_INITIALIZED = "initialized"
STATE_CLEAR = "clear"
STATE_BREACHED = "breached"
STATE_UNKNOWN = "unknown"

# The combined block's keys, exactly — it rides the stored snapshot, so a key here is a decision about
# what may leave the process (the same rule as board.AGGREGATE_KEYS).
COMBINED_KEYS = frozenset({
    # The wallet side (Thomas 2026-10-07, appendix C): KRW only; ``crypto_wallet_status`` says why a value
    # is absent. No USDT figure, asset, quantity or price is stored.
    "crypto_spot_krw",
    "crypto_earn_krw",
    "crypto_classes_krw",
    "crypto_wallet_status",
    "crypto_futures_krw",
    "crypto_as_of",
    "crypto_status",
    "combined_total_krw",
    # H2 (Thomas 2026-10-08): status words only, no amount. ``complete`` is ``portfolio_nav_complete``,
    # kept so a reader of an older shape still reads the strict meaning.
    "complete",
    "scope_version",
    "sources",
    "checks",
    "source_fetch_complete",
    "coverage_complete",
    "valuation_complete",
    "freshness_complete",
    "portfolio_nav_complete",
    "peak_total_krw",
    "peak_at",
    "drawdown_pct",
    "drawdown_limit_pct",
    "drawdown_state",
})


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _age_seconds(stamp: Any, now: str) -> float | None:
    try:
        return (timeutil.parse_iso(now) - timeutil.parse_iso(str(stamp))).total_seconds()
    except (TypeError, ValueError):
        return None


def read_binance(root: Path, *, now: str) -> tuple[float | None, str | None, str]:
    """(margin balance in USDT, its as_of, status). ``status`` is ``ok`` or why it cannot be used."""
    path = root / BINANCE_SNAPSHOT_REL
    if not path.exists():
        return None, None, "absent"
    body = _read_json(path)
    if body is None or body.get("record_type") != BINANCE_RECORD_TYPE:
        return None, None, "unreadable"
    as_of = body.get("as_of")
    if not body.get("configured"):
        return None, as_of, "not_configured"
    if body.get("asset") != BINANCE_ASSET:
        return None, as_of, "not_usdt"
    age = _age_seconds(as_of, now)
    if age is None or age > BINANCE_STALE_SECONDS:
        return None, as_of, "stale"
    try:
        balance = float(body.get("margin_balance"))
    except (TypeError, ValueError):
        return None, as_of, "unreadable"
    return balance, as_of, "ok"


WALLET_NOT_CONFIGURED = "not_configured"
WALLET_OK = "ok"
# Read, but its value is a lower bound (H2). The KRW parts are still computed, for the board's lines.
WALLET_UNPRICED = "unpriced"
WALLET_TRUNCATED = "earn_truncated"
WALLET_INVALID_ROWS = "invalid_rows"
WALLET_UNDERVALUED = (WALLET_UNPRICED, WALLET_TRUNCATED, WALLET_INVALID_ROWS)


def wallet_part(wallet: Any, *, wallet_status: str, usd_krw_rate: float | None) -> dict[str, Any]:
    """The wallet's KRW figures and status. ``wallet`` is a ``binance_wallet.WalletSnapshot`` or ``None``;
    ``wallet_status`` is what the read said when it is ``None`` (``not_configured`` or ``failed (CODE)``)."""
    part: dict[str, Any] = {"crypto_spot_krw": None, "crypto_earn_krw": None, "crypto_classes_krw": None,
                            "crypto_wallet_status": wallet_status}
    if wallet is None:
        return part
    if usd_krw_rate is None:
        part["crypto_wallet_status"] = "no_rate"
        return part
    part["crypto_spot_krw"] = sum(wallet.spot_usdt.values()) * usd_krw_rate
    if wallet.earn_usdt is None:
        part["crypto_wallet_status"] = "earn_unread"
        return part
    part["crypto_earn_krw"] = sum(wallet.earn_usdt.values()) * usd_krw_rate
    part["crypto_classes_krw"] = {
        name: (wallet.spot_usdt.get(name, 0.0) + wallet.earn_usdt.get(name, 0.0)) * usd_krw_rate
        for name in sorted(set(wallet.spot_usdt) | set(wallet.earn_usdt))
    }
    if getattr(wallet, "invalid_rows", 0):
        part["crypto_wallet_status"] = WALLET_INVALID_ROWS
    elif getattr(wallet, "earn_truncated", False):
        part["crypto_wallet_status"] = WALLET_TRUNCATED
    elif getattr(wallet, "unpriced_assets", 0):
        part["crypto_wallet_status"] = WALLET_UNPRICED
    else:
        part["crypto_wallet_status"] = WALLET_OK
    return part


def _source(included: bool, reason: str | None = None) -> dict[str, Any]:
    return {"included": included, "reason": None if included else reason}


def judge(toss_view: dict[str, Any], *, crypto_status: str, crypto_krw: float | None,
          wallet_block: dict[str, Any], toss_warnings: int = 0) -> dict[str, Any]:
    """H2's verdict over the declared scope: ``sources``, ``checks`` and the five flags. Amount-free."""
    wallet_status = wallet_block["crypto_wallet_status"]
    toss_total = toss_view.get("known_total_krw")
    toss_read = toss_total is not None and not toss_view.get("partial")
    futures_read = crypto_status in ("ok", "no_rate", "not_usdt")
    wallet_read = wallet_status not in (WALLET_NOT_CONFIGURED,) and not wallet_status.startswith("failed")
    spot_counted = wallet_read and wallet_block["crypto_spot_krw"] is not None
    earn_counted = wallet_read and wallet_block["crypto_earn_krw"] is not None
    wallet_off_reason = EXCLUDED_GATE_OFF if wallet_status == WALLET_NOT_CONFIGURED else EXCLUDED_READ_FAILED

    sources = {
        SOURCE_TOSS: _source(toss_total is not None, EXCLUDED_READ_FAILED),
        SOURCE_FUTURES: _source(crypto_krw is not None, crypto_status),
        SOURCE_SPOT: _source(spot_counted, wallet_off_reason if not wallet_read else wallet_status),
        SOURCE_EARN: _source(earn_counted, wallet_off_reason if not wallet_read else wallet_status),
    }
    fetch = toss_read and futures_read and (wallet_status == WALLET_NOT_CONFIGURED or wallet_read) \
        and wallet_status != "earn_unread"
    coverage = all(row["included"] for row in sources.values())
    valuation = (toss_read and not toss_warnings and crypto_status not in ("no_rate", "not_usdt")
                 and (not wallet_read or wallet_status == WALLET_OK))
    freshness = crypto_status != "stale"
    checks = {name: NOT_EVALUATED for name in CHECKS}
    checks.update({CHECK_COVERAGE: PASS if coverage else FAIL,
                   CHECK_VALUATION: PASS if valuation else FAIL,
                   CHECK_FRESHNESS: PASS if freshness else FAIL})
    nav = all(checks[name] == PASS for name in REQUIRED_CHECKS)
    return {
        "scope_version": PORTFOLIO_SCOPE_VERSION,
        "sources": sources,
        "checks": checks,
        "source_fetch_complete": fetch,
        "coverage_complete": coverage,
        "valuation_complete": valuation,
        "freshness_complete": freshness,
        "portfolio_nav_complete": nav,
        "complete": nav,
    }


def combine(toss_view: dict[str, Any], *, usd_krw_rate: float | None, root: Path, now: str,
            state_dir: Path, wallet: Any = None,
            wallet_status: str = WALLET_NOT_CONFIGURED, toss_warnings: int = 0) -> dict[str, Any]:
    """The combined block, and the peak file moved when (and only when) the portfolio NAV is complete (H2).

    ``toss_warnings`` is the Toss read's parse-warning count: a field the Toss total rests on was missing
    or not numeric, so the total is a lower bound."""
    usdt, crypto_as_of, crypto_status = read_binance(root, now=now)
    crypto_krw = None if usdt is None or usd_krw_rate is None else usdt * usd_krw_rate
    if usdt is not None and usd_krw_rate is None:
        crypto_status = "no_rate"
    wallet_block = wallet_part(wallet, wallet_status=wallet_status, usd_krw_rate=usd_krw_rate)
    wallet_status = wallet_block["crypto_wallet_status"]
    wallet_krw = (wallet_block["crypto_spot_krw"] or 0) + (wallet_block["crypto_earn_krw"] or 0)
    toss_total = toss_view.get("known_total_krw")
    verdict = judge(toss_view, crypto_status=crypto_status, crypto_krw=crypto_krw, wallet_block=wallet_block,
                    toss_warnings=toss_warnings)
    complete = verdict["portfolio_nav_complete"]
    total = (toss_total or 0) + (crypto_krw or 0) + wallet_krw if complete else None

    block: dict[str, Any] = {
        **wallet_block,
        **verdict,
        "crypto_futures_krw": crypto_krw,
        "crypto_as_of": crypto_as_of,
        "crypto_status": crypto_status,
        "combined_total_krw": total,
        "peak_total_krw": None,
        "peak_at": None,
        "drawdown_pct": None,
        "drawdown_limit_pct": DRAWDOWN_LIMIT_PCT,
        "drawdown_state": STATE_UNKNOWN,
    }
    peak_path = state_dir / PEAK_FILENAME
    peak = _read_json(peak_path) or {}
    if peak.get("scope_version") != PORTFOLIO_SCOPE_VERSION:
        peak = {}   # a peak from another scope is not this portfolio's peak (module docstring)
    peak_total = peak.get("peak_total_krw") if isinstance(peak.get("peak_total_krw"), (int, float)) else None
    block["peak_total_krw"], block["peak_at"] = peak_total, peak.get("peak_at")
    if not complete or total is None or total <= 0:
        return block
    if peak_total is None or peak_total <= 0:
        _write_peak(peak_path, total, now)
        block.update({"peak_total_krw": total, "peak_at": now, "drawdown_pct": 0.0,
                      "drawdown_state": STATE_INITIALIZED})
        return block
    if total > peak_total:
        _write_peak(peak_path, total, now)
        peak_total = total
        block.update({"peak_total_krw": total, "peak_at": now})
    drawdown = round((total / peak_total - 1.0) * 100.0, 2)
    block["drawdown_pct"] = drawdown
    block["drawdown_state"] = STATE_BREACHED if drawdown <= DRAWDOWN_LIMIT_PCT else STATE_CLEAR
    return block


def _write_peak(path: Path, total: float, now: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"peak_total_krw": total, "peak_at": now, "scope_version": PORTFOLIO_SCOPE_VERSION},
                              sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def read_told(state_dir: Path) -> str:
    """The last drawdown state Thomas was told. Never told reads as clear: no message for a quiet start."""
    body = _read_json(state_dir / ALERT_MARK_FILENAME) or {}
    return body.get("told_state") if body.get("told_state") in (STATE_CLEAR, STATE_BREACHED) else STATE_CLEAR


def write_told(state_dir: Path, state: str, *, now: str) -> None:
    path = state_dir / ALERT_MARK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"told_state": state, "told_at": now}, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _krw(value: Any) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{value:,.0f}원"


def alert(block: dict[str, Any], *, told: str, as_of: str) -> tuple[str, str] | None:
    """``(state, text)`` when the state crossed an edge since the last delivered message, else None.
    ``unknown`` and ``initialized`` never alert and never move the mark."""
    state = block.get("drawdown_state")
    if state not in (STATE_CLEAR, STATE_BREACHED) or state == told:
        return None
    if state == STATE_BREACHED:
        text = (f"[보유 자산] 계좌 전체가 고점 대비 {block['drawdown_pct']:+.1f}% "
                f"(한도 {DRAWDOWN_LIMIT_PCT:.0f}%) — 합계 {_krw(block['combined_total_krw'])}, "
                f"고점 {_krw(block['peak_total_krw'])} ({as_of} 기준). 알림만이며 아무것도 막지 않습니다. "
                "고점은 입출금을 구분하지 못합니다 — 입출금 뒤라면 holdings_board --reset-peak로 다시 잡으세요.")
    else:
        text = (f"[보유 자산] 계좌 전체 낙폭이 한도 안으로 돌아왔습니다: 고점 대비 {block['drawdown_pct']:+.1f}% "
                f"(한도 {DRAWDOWN_LIMIT_PCT:.0f}%), 합계 {_krw(block['combined_total_krw'])} ({as_of} 기준).")
    return state, text


def reset_peak(state_dir: Path) -> list[str]:
    """Forget the peak and the told mark; the next complete fire starts a new peak. Returns what went."""
    removed = []
    for name in (PEAK_FILENAME, ALERT_MARK_FILENAME):
        path = state_dir / name
        if path.exists():
            path.unlink()
            removed.append(name)
    return removed
