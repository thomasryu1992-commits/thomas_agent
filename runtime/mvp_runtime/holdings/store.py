"""The holdings snapshot the doors serve — written by the lane that holds the broker key.

P1-b of ``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``, ``crypto/account_store.py``'s shape for its
reason: the doors (the operator's Telegram verb, the assistant's read bridge) hold no broker key and
must not, so they never call the broker. ``scheduler-maint`` holds the key (Thomas 2026-10-02: a
holdings read is maintenance — late costs freshness, never money — and keeping the key there keeps it
out of the container that holds the Binance order key). Its ``holdings_refresh`` fire reads the Toss
account (appendix B, 2026-10-07) and writes what it saw here, and the doors render this file. The
figure is therefore as old as the last fire, and every render says how old.

**Only aggregates are ever written, in two files split by who reads them** (H3, Thomas 2026-10-08,
``disclosure``). No symbol, name, per-symbol number or exchange rate exists on disk in either.

- ``holdings_snapshot.json`` — what the doors render. One table (the allocation's asset classes against
  the NAV) with the single-holding rule applied, and the status words. :func:`disclosure.external_view`
  makes it; :data:`disclosure.LOCAL_ONLY_TOP_KEYS` and its siblings name what it leaves out.
- ``holdings_local.json`` (:data:`LOCAL_FILENAME`) — the whole fire: Toss by market, Binance by wallet
  and sub-class, every class amount. Read by ``scripts/holdings_board.py --local`` on the account
  holder's terminal and by nothing else; a test pins that no door module names it.

The terminal's per-symbol board is a live read (``scripts/holdings_board.py --full``).

**Two files, as ``account_store`` keeps them.** The snapshot is the last SUCCESSFUL read, and the mark
is the last ATTEMPT. A failed read moves the mark and leaves the last good figure in place.

**This lane is the one token issuer.** Toss allows one valid token per client, and issuing one revokes
the previous one. The scheduler runs this kind in its own process (only factory fires fork), so the
capable feed — and its in-memory token — is kept here between fires and serves until the token nears
expiry. If something else issued a token for the same client meanwhile, the feed reissues once and
retries once (``toss_account``). The cache is dropped the moment the gate no longer selects the
capable feed. Revocation is unchanged: unset the variable and restart the container.

A state file on ``account_store``'s precedent, not a ledger record: no closed schema is owed, and
nothing judges by it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from .. import timeutil
from ..errors import MvpRuntimeError, ToolError
from ..filelock import locked
from ..paths import repo_root as _repo_root
from . import allocation, binance_wallet, cash_flows, classification, combined, disclosure, overseas_gain, rebalance
from . import candidates as candidates_mod
from .board import aggregate_view, render_view
from .toss_account import MARKET_OVERSEAS, TossHoldingsFeed, read_holdings, select_holdings_feed

STATE_REL = ".runtime_governance_state/holdings"
SNAPSHOT_FILENAME = "holdings_snapshot.json"
LOCAL_FILENAME = "holdings_local.json"
LOCAL_RECORD_TYPE = "holdings_local.v1"
REFRESH_MARK_FILENAME = "holdings_refresh.json"
RECORD_TYPE = "holdings_snapshot.v1"

# When a rendered figure stops being a statement about now. Three hourly fires missed in a row.
STALE_AFTER_SECONDS = 3 * 60 * 60

# What the snapshot's `cash_flows.readiness` may hold, for a door that projects it (the Claude door's
# `holdings_status`, HOLDINGS_STATUS_READ_AND_AUTO_FLOWS R1). Handed out here so no door imports the ledger
# module (tests/test_mvp_runtime_holdings_cash_flows.py::test_no_door_module_reads_the_ledger).
READINESS_CHECKS = cash_flows.READINESS_CHECKS
READINESS_STATUSES = cash_flows.READINESS_STATUSES
TOSS_NEEDS = cash_flows.TOSS_NEEDS

HOLDINGS_SNAPSHOT_MISSING = "HOLDINGS_SNAPSHOT_MISSING"
HOLDINGS_SNAPSHOT_UNREADABLE = "HOLDINGS_SNAPSHOT_UNREADABLE"

# The capable feed, kept between fires so its token is too (module docstring).
_cached_feed: TossHoldingsFeed | None = None


def state_dir(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / STATE_REL


def snapshot_path(root: Path | None = None) -> Path:
    return state_dir(root) / SNAPSHOT_FILENAME


POSITIONS_FILENAME = "holdings_positions.json"
POSITIONS_RECORD_TYPE = "holdings_positions.v1"


TAX_ESTIMATE_FILENAME = "holdings_tax_estimate.json"
TAX_ESTIMATE_RECORD_TYPE = "holdings_tax_estimate.v1"
# The whole closed-order history, once a KST day (RB3'). 50 pages of 100: a longer history is truncated and
# the gain refused, never counted from part of it.
TAX_ESTIMATE_MAX_PAGES = 50


def tax_estimate_path(root: Path | None = None) -> Path:
    """The USD/KRW rate the fire saw each KST day, and this year's realised overseas gain: amounts, no symbol;
    LOCAL_ONLY all the same (``--rebalance`` only)."""
    return state_dir(root) / TAX_ESTIMATE_FILENAME


def positions_path(root: Path | None = None) -> Path:
    """Each classified holding of the last fire, for ``--rebalance`` only. Names instruments: LOCAL_ONLY."""
    return state_dir(root) / POSITIONS_FILENAME


def local_path(root: Path | None = None) -> Path:
    return state_dir(root) / LOCAL_FILENAME


def refresh_mark_path(root: Path | None = None) -> Path:
    return state_dir(root) / REFRESH_MARK_FILENAME


def _write_json(path: Path, body: Mapping[str, Any], *, code: str, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code=code, label=label):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


def refresh_tax_estimate(feed: Any, snapshot: Any, *, now: str, root: Path | None = None) -> None:
    """RB3' (Thomas 2026-10-26): record today's USD/KRW rate (the first fire of a KST day), and once a day
    rebuild this year's realised overseas gain from the whole closed-order history. A failed read keeps the
    last block, which then names an earlier day, and the next fire tries again."""
    path = tax_estimate_path(root)
    body = _read_json(path) or {}
    today = overseas_gain.kst_day(now)
    rates = dict(body.get("fx_rates") or {})
    if snapshot.usd_krw_rate and today not in rates:
        rates[today] = snapshot.usd_krw_rate
    gain = body.get("overseas_gain")
    if not isinstance(gain, dict) or gain.get("computed_on") != today:
        try:
            orders, truncated = feed.closed_orders(date_from=None, date_to=None, max_pages=TAX_ESTIMATE_MAX_PAGES)
            held = {h.symbol: (h.quantity, h.average_price) for h in snapshot.holdings if h.market == MARKET_OVERSEAS}
            result = overseas_gain.realised(orders, truncated=truncated, year=int(today[:4]), rates=rates,
                                            current_rate=snapshot.usd_krw_rate, held=held)
            gain = {**result.as_record(), "computed_on": today}
        except Exception:  # noqa: BLE001 — a failed read keeps yesterday's block; the rate is still recorded
            pass
    _write_json(path, {"record_type": TAX_ESTIMATE_RECORD_TYPE, "written_at": now, "fx_rates": rates,
                       "overseas_gain": gain},
                code="HOLDINGS_TAX_ESTIMATE_LOCKED", label="holdings tax estimate")


def _feed() -> Any:
    """The feed this fire reads through: the cached capable one while the gate still selects it."""
    global _cached_feed
    selected = select_holdings_feed()
    if not isinstance(selected, TossHoldingsFeed):
        _cached_feed = None
        return selected
    if _cached_feed is None:
        _cached_feed = selected
    return _cached_feed


def refresh(*, now: str, root: Path | None = None, timeout_seconds: int = 10) -> dict[str, Any]:
    """Read the broker once, combine it with the Binance snapshot file (P2), store the aggregate.

    Returns ``{"status": <one line>, "alert": (state, text) | None}``. The alert is the drawdown edge
    since the last DELIVERED message (``combined.alert``); the caller sends it and only then records it
    with :func:`mark_told`, so a message the channel did not take is offered again next fire.

    Never raises: a holdings read is bookkeeping, and a failure becomes a status string and a moved
    mark. A failed read keeps the previous snapshot."""
    try:
        _write_json(refresh_mark_path(root), {"attempted_at": now},
                    code="HOLDINGS_REFRESH_MARK_LOCKED", label="holdings refresh mark")
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return {"status": f"holdings snapshot: mark not written ({type(exc).__name__})", "alert": None}
    try:
        snapshot, reason = read_holdings(timeout_seconds=timeout_seconds, feed=_feed())
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return {"status": f"holdings snapshot: read failed ({type(exc).__name__})", "alert": None}
    if snapshot is None:
        if reason == "NOT_CONFIGURED":
            return {"status": "holdings snapshot: no broker account configured", "alert": None}
        return {"status": f"holdings snapshot: degraded ({reason}); kept the previous one", "alert": None}
    body = dict(aggregate_view(snapshot))
    body["record_type"] = RECORD_TYPE
    body["as_of"] = snapshot.collected_at or now
    body["written_at"] = now
    base = root if root is not None else _repo_root()
    # The Binance spot wallet and Simple Earn (appendix C), behind their own gate. A failure here costs
    # the combined total its completeness, never the Toss snapshot.
    try:
        wallet, wallet_reason = binance_wallet.read_wallet()
    except Exception as exc:  # noqa: BLE001 — see the docstring
        wallet, wallet_reason = None, type(exc).__name__
    wallet_status = (combined.WALLET_NOT_CONFIGURED if wallet is None and wallet_reason == "NOT_CONFIGURED"
                     else f"failed ({wallet_reason})" if wallet is None else combined.WALLET_OK)
    # H6b (shadow): the cash-flow histories, appended to the local ledger. A failure costs this fire the
    # ledger and — because the transfers are then unknown — its coherence, never the Toss snapshot.
    transfers: list[int] | None = ()
    flows_note = None
    readiness_alert = exception_alert = rule_flow_alert = band_alert = candidate_alert = None
    binance_ok = toss_ok = False      # a verified fire needs both (H6b readiness-hardening)
    flow_feed = binance_wallet.select_wallet_feed() if wallet is not None else None
    if isinstance(flow_feed, binance_wallet.BinanceWalletFeed):
        try:
            pass_result = cash_flows.collect_binance(flow_feed, state_dir(root), now=now)
            transfers = pass_result["internal_ms"]
            binance_ok = not pass_result["errors"]
            if "transfer_spot_to_futures" in pass_result["errors"] or "transfer_futures_to_spot" in pass_result["errors"]:
                transfers = None
        except Exception as exc:  # noqa: BLE001 — see the comment above
            transfers, flows_note = None, _reason(exc)
    # H5a entries, read once per fire: the allocation places by them, and a new baseline records their
    # version (H5b).
    try:
        entries, entries_error = classification.load(state_dir(root)), None
    except ToolError as exc:
        entries, entries_error = None, exc.reason_code
    try:
        block = combined.combine(body, usd_krw_rate=snapshot.usd_krw_rate, root=base, now=now,
                                 state_dir=state_dir(root), wallet=wallet, wallet_status=wallet_status,
                                 toss_warnings=len(snapshot.warnings), toss_as_of=snapshot.collected_at,
                                 toss_reconciliation_failures=combined.toss_reconciliation(snapshot),
                                 mapping_version=entries.mapping_version if entries else None,
                                 internal_transfer_ms=transfers)
    except Exception as exc:  # noqa: BLE001 — the combined total must not cost the Toss snapshot
        block = None
        combined_note = f"combined not computed ({type(exc).__name__})"
    else:
        combined_note = (f"combined {block['drawdown_state']}"
                         + ("" if block["portfolio_nav_complete"] else "; portfolio NAV incomplete"))
    body["combined"] = block
    # Q9 (TOTAL_ASSET_ALLOCATION §11.1): class totals against the target, display only. Same rule as
    # the combined total: a failure costs the board this block, never the Toss snapshot.
    parts: dict[str, Any] = {}
    try:
        body["allocation"], parts = allocation.allocate_with_parts(
            snapshot, body, block, wallet, entries, classification_error=entries_error)
    except Exception as exc:  # noqa: BLE001 — see the comment above
        body["allocation"] = None
        combined_note += f"; allocation not computed ({type(exc).__name__})"
    # REBALANCING_PROPOSAL RB1 (Thomas 2026-10-26): each classified holding for `--rebalance`. It names
    # instruments, so it goes to its own local file (:func:`positions_path`), never the board or the snapshot.
    try:
        positions = rebalance.toss_positions(snapshot, entries)
    except Exception:  # noqa: BLE001 — the calculator's input must not cost the board
        positions = None
    feed = None
    try:
        feed = _feed()
        if isinstance(feed, TossHoldingsFeed):
            cash_flows.collect_toss(feed, snapshot, state_dir(root), now=now)
            toss_ok = True
    except Exception as exc:  # noqa: BLE001 — a Toss shadow failure costs that pass, not the summary
        flows_note = flows_note or f"toss {_reason(exc)}"
    try:
        if isinstance(feed, TossHoldingsFeed):
            refresh_tax_estimate(feed, snapshot, now=now, root=root)
    except Exception:  # noqa: BLE001 — the calculator's input must not cost the board
        pass
    try:
        if isinstance(feed, TossHoldingsFeed):
            refresh_auto_candidates(feed, now=now, root=root)
    except Exception:  # noqa: BLE001 — a failed screen keeps the last pick; the next fire tries again
        pass
    try:
        flows = cash_flows.summary(state_dir(root))
        # H6d-min §11: readiness judges this fire's health, never a past PASS — every history read, the
        # Toss pass ended, and the combined NAV's own freshness and coherence held.
        nav_checks = (block or {}).get("checks") or {}
        current = {"binance": binance_ok, "toss": toss_ok,
                   "freshness": nav_checks.get(combined.CHECK_FRESHNESS) == combined.PASS,
                   "coherence": nav_checks.get(combined.CHECK_COHERENCE) == combined.PASS}
        ready = cash_flows.update_readiness(state_dir(root), now=now, current=current)
        flows["readiness"] = {key: ready[key] for key in ("checks", "ready", "shadow_days", "shadow_success_dates",
                                                          "exceptions", "toss_needs", "next_step")}
        body["cash_flows"] = flows
        if ready["ready"] and not ready["told"]:
            readiness_alert = (
                f"[보유 자산] {cash_flows.READY_NAME} — 실제 입출금 형식 확인, 토스 현금 의미 확인"
                f"({ready['toss_note']}), shadow 관찰 {ready['shadow_days']:.0f}일·정상 실행 "
                f"{ready['shadow_success_dates']}일, 미해소 예외 0건. 다음 단계: {ready['next_step']}. "
                "자동으로 켜지지 않습니다. 진행하려면 승인해 주세요.")
        if ready["exceptions_untold"]:
            # Counts only: which event, what asset and how much stay in the local ledger. The token names
            # the set this message covers; the scheduler hands it back once the message was delivered.
            exception_alert = (ready["exceptions_token"], (
                f"[보유 자산] H6 예외 {ready['exceptions']}건(새 {ready['exceptions_new']}건) — cutover 이후 "
                f"설명되지 않은 입출금이 있어 {cash_flows.READY_NAME}가 막혀 있습니다. Thomas 터미널에서 "
                "확인(--flows) 후 해소(--resolve)할 수 있습니다."))
        # F2 (Thomas 2026-10-26): what the quiet-window rule closed, by direction and count, never an amount.
        token, counts = cash_flows.rule_flows_untold(state_dir(root))
        if token:
            rule_flow_alert = (token, (
                f"[보유 자산] 토스 입금 {counts.get('deposit', 0)}건·출금 {counts.get('withdrawal', 0)}건을 "
                "규칙(quiet_window.v1)으로 자동 분류했습니다. 틀렸다면 Thomas 터미널에서 확인(--flows) 후 "
                "다시 분류(--resolve)할 수 있습니다."))
        if binance_ok and toss_ok:
            # Last, so only a fire whose histories, ledger, Toss pass and readiness all ended counts.
            cash_flows.mark_fire_verified(state_dir(root), now=now)
    except Exception as exc:  # noqa: BLE001 — shadow bookkeeping must not cost the board
        flows_note = flows_note or _reason(exc)
    if flows_note:
        body.setdefault("cash_flows", {"mode": cash_flows.ACCOUNTING_MODE})["error"] = flows_note
    try:
        band_alert = band_notice(body, now=now, root=root)
        candidate_alert = candidate_notice(root)
    except Exception:  # noqa: BLE001 — a notice must not cost the board; the next fire offers it again
        band_alert = candidate_alert = None
    # H3: the doors get one table under the single-holding rule; the whole fire goes to the local file.
    # Without the allocation's makeup there is no table to judge, so nothing leaves but status words.
    try:
        outward = disclosure.external_view(body, parts)
        if body.get("allocation") is None and isinstance(outward.get("combined"), dict):
            outward["combined"].update({"combined_total_krw": None, "peak_total_krw": None})
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return {"status": f"holdings snapshot: not disclosed ({type(exc).__name__})", "alert": None}
    try:
        _write_json(local_path(root), {**body, "record_type": LOCAL_RECORD_TYPE},
                    code="HOLDINGS_LOCAL_LOCKED", label="holdings local board")
        if positions is not None:
            _write_json(positions_path(root), {"record_type": POSITIONS_RECORD_TYPE, "as_of": body["as_of"],
                                               "positions": positions},
                        code="HOLDINGS_POSITIONS_LOCKED", label="holdings positions")
        _write_json(snapshot_path(root), outward, code="HOLDINGS_SNAPSHOT_LOCKED", label="holdings snapshot")
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return {"status": f"holdings snapshot: not persisted ({type(exc).__name__})", "alert": None}
    edge = None
    if block is not None:
        # The alert is a Telegram message, so it is built from what may leave.
        edge = combined.alert(outward["combined"], told=combined.read_told(state_dir(root)), as_of=body["as_of"])
    return {"status": f"holdings snapshot: refreshed; {combined_note}", "alert": edge,
            "readiness_alert": readiness_alert, "exception_alert": exception_alert,
            "rule_flow_alert": rule_flow_alert, "band_alert": band_alert,
            "candidate_alert": candidate_alert}


AUTO_CANDIDATES_FILENAME = "holdings_auto_candidates.json"
AUTO_CANDIDATES_RECORD_TYPE = "holdings_auto_candidates.v1"


def auto_candidates_path(root: Path | None = None) -> Path:
    """RB2'': the fire's own pick per class (instrument ids, LOCAL_ONLY). Thomas's ``--approve-candidates`` lists
    live in the classification file and win for their class; this file never holds his choice."""
    return state_dir(root) / AUTO_CANDIDATES_FILENAME


def effective_candidates(root: Path | None = None) -> dict[str, list[str]]:
    """What the calculator buys into: Thomas's list where he set one, the automatic pick everywhere else."""
    auto = (_read_json(auto_candidates_path(root)) or {}).get("candidates") or {}
    try:
        manual = dict(classification.load(state_dir(root)).candidates)
    except ToolError:
        manual = {}
    return {**{cls: list(ids) for cls, ids in auto.items() if ids}, **manual}


def refresh_auto_candidates(feed: Any, *, now: str, root: Path | None = None, sleep: Any = None) -> str | None:
    """RB2'' (Thomas 2026-10-26): screen again once a KST month, and at once when an automatic pick is no longer
    ACTIVE on Toss. A class Thomas set is never screened over; a class the screen finds nothing for keeps its last
    pick. Returns the trigger it acted on, or None. A failed read changes nothing; the next fire tries again."""
    path = auto_candidates_path(root)
    body = _read_json(path) or {}
    month = overseas_gain.kst_day(now)[:7]
    try:
        manual = dict(classification.load(state_dir(root)).candidates)
    except ToolError:
        return None                       # an unreadable classification file is Thomas's to fix first
    auto = {cls: list(ids) for cls, ids in (body.get("candidates") or {}).items()}
    watched = sorted({i for ids in auto.values() for i in ids} | {i for ids in manual.values() for i in ids})
    status: dict[str, str] = {}
    if watched:
        for row in feed.stock_info([i.split(":")[2] for i in watched]):
            status[str(row.get("symbol"))] = str(row.get("status") or "")
    # Only an explicit non-ACTIVE status counts: a row the answer leaves out is not evidence of a delisting.
    untradable = sorted(i for i in watched if status.get(i.split(":")[2], "ACTIVE") != "ACTIVE")
    manual_ids = {i for ids in manual.values() for i in ids}
    # A pick from before the US bond was recorded apart (G6 §2e) is screened again once, to record it.
    trigger = ("monthly" if body.get("screened_month") != month
               else "untradable" if any(i not in manual_ids for i in untradable)
               else "bond_split" if "us_bonds" not in body else None)
    if trigger is not None:
        ranked, _ = candidates_mod.collect(feed, **({"sleep": sleep} if sleep else {}))
        picks = candidates_mod.auto_picks(ranked)
        auto = {cls: (ids or auto.get(cls, [])) for cls, ids in picks.items() if cls not in manual}
        us_bonds = candidates_mod.us_bond_pick(ranked)
        body["us_bonds"] = us_bonds if picks.get(allocation.BONDS) else body.get("us_bonds", [])
        body.update({"screened_at": now, "screened_month": month, "trigger": trigger,
                     "kept": sorted(cls for cls, ids in picks.items() if not ids and cls not in manual)})
    body.update({"record_type": AUTO_CANDIDATES_RECORD_TYPE, "candidates": auto,
                 "manual_untradable": sorted(i for i in untradable if i in manual_ids)})
    _write_json(path, body, code="HOLDINGS_AUTO_CANDIDATES_LOCKED", label="holdings auto candidates")
    return trigger


def candidate_notice(root: Path | None = None) -> tuple[str, str] | None:
    """The Telegram line for a new automatic pick or a manual pick that stopped trading: counts only, never an
    instrument. (token, text) until :func:`mark_candidates_told` records the token."""
    body = _read_json(auto_candidates_path(root)) or {}
    if not body.get("screened_at"):
        return None
    bad = body.get("manual_untradable") or []
    token = f"{body['screened_at']}|{len(bad)}"
    if body.get("told_token") == token:
        return None
    label = {"monthly": "월 점검", "untradable": "거래 불가 후보 교체"}.get(body.get("trigger"), "갱신")
    text = (f"[보유 자산] 리밸런싱 후보 자동 선정({label}) — 자산군 {len(body.get('candidates') or {})}개"
            + (f", 찾지 못해 이전 후보 유지 {len(body['kept'])}개" if body.get("kept") else "") + ".")
    if bad:
        text += f" 수동 지정 후보 중 거래 불가 {len(bad)}개 — 수동 지정을 지우면 자동 선정으로 바뀝니다."
    return token, text


def mark_candidates_told(token: str, *, now: str, root: Path | None = None) -> None:
    """Record that the candidate notice ``token`` names was delivered — only after delivery."""
    body = _read_json(auto_candidates_path(root)) or {}
    body.update({"told_token": token, "told_at": now})
    _write_json(auto_candidates_path(root), body, code="HOLDINGS_AUTO_CANDIDATES_LOCKED",
                label="holdings auto candidates")


def run_autoinvest(*, now: str, root: Path | None = None) -> dict[str, Any]:
    """G6-buy (Thomas 2026-10-26): one look at Thomas's approved buy scenario, through this fire's Toss feed and
    the order adapter its own gate builds (``MVP_TOSS_ORDERS=buy_only``). Returns the step's status and, until it
    is marked told, its notice (counts and state only)."""
    from . import autoinvest, toss_orders

    feed = _feed()
    result = autoinvest.run(feed, toss_orders.select_buy_orders(feed), state_dir(root), now=now) if isinstance(
        feed, TossHoldingsFeed) else {"status": "no_feed"}
    return {**result, "alert": autoinvest.notice(state_dir(root))}


def refresh_autoinvest_proposal(*, now: str, root: Path | None = None) -> dict[str, Any]:
    """G6 §2e (Thomas 2026-10-27): today's drafted buy scenario, after the executor's look, over the fire's own local
    board and positions. Returns its status word and, until marked told, its notice (counts only). It orders
    nothing: Thomas confirms the draft on his terminal."""
    from . import autoinvest_proposal

    feed = _feed()
    if not isinstance(feed, TossHoldingsFeed):
        return {"status": "no_feed", "alert": None}
    try:
        currencies = dict(classification.load(state_dir(root)).currencies)
    except ToolError:
        currencies = None
    if currencies is None:
        status = "classification_unreadable"
    else:
        positions = (_read_json(positions_path(root)) or {}).get("positions")
        status = autoinvest_proposal.refresh(
            feed, state_dir(root), now=now, board=_read_json(local_path(root)),
            positions=positions if isinstance(positions, list) else None, currencies=currencies,
            candidates=effective_candidates(root), us_bonds=(_read_json(auto_candidates_path(root)) or {}).get("us_bonds") or [])
    return {"status": status, "alert": autoinvest_proposal.notice(state_dir(root))}


def mark_autoinvest_told(token: str, *, now: str, root: Path | None = None) -> None:
    from . import autoinvest

    autoinvest.mark_told(state_dir(root), token, now=now)


def mark_proposal_told(token: str, *, now: str, root: Path | None = None) -> None:
    from . import autoinvest_proposal

    autoinvest_proposal.mark_told(state_dir(root), token, now=now)


BAND_NOTICE_FILENAME = "holdings_band_notice.json"


def band_notice_path(root: Path | None = None) -> Path:
    return state_dir(root) / BAND_NOTICE_FILENAME


def _first_weekday(month: str) -> str:
    day = date.fromisoformat(f"{month}-01")
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def band_notice(body: Mapping[str, Any], *, now: str, root: Path | None = None) -> tuple[str, str] | None:
    """RB5 (Thomas 2026-10-26): once a month, from the first weekday (KST) on, how many bands are outside — a
    count, never a class or an amount. The verdict is the calculator's (engine margin left out), so the count
    and ``--rebalance`` agree. Weekdays stand in for business days: a public holiday is not known here, and the
    notice then comes on that holiday. Returns (month, text) until :func:`mark_band_told` records the month."""
    today = overseas_gain.kst_day(now)
    month = today[:7]
    if today < _first_weekday(month) or (_read_json(band_notice_path(root)) or {}).get("told_month") == month:
        return None
    table = body.get("allocation") if isinstance(body.get("allocation"), Mapping) else {}
    class_krw = {name: float((row or {}).get("krw") or 0.0) for name, row in (table.get("classes") or {}).items()}
    result = rebalance.plan(class_krw=class_krw, positions=[], complete=table.get("complete") is True)
    if result.status == "refused":
        return month, ("[보유 자산] 월 리밸런싱 점검 — 이번 달은 판정할 수 없습니다(자산 합계가 완전하지 않음). "
                       "분류와 읽기를 확인한 뒤 Thomas 터미널에서 --rebalance로 보세요.")
    outside = sum(1 for row in result.drift.values() if row["outside"])
    return month, (f"[보유 자산] 월 리밸런싱 점검 — 밴드 이탈 {outside}개. "
                   + ("Thomas 터미널에서 --rebalance로 계획을 확인하세요." if outside else "리밸런싱할 것이 없습니다."))


def mark_band_told(month: str, *, now: str, root: Path | None = None) -> None:
    """Record that this month's band notice was delivered — only after delivery, like every holdings notice."""
    _write_json(band_notice_path(root), {"told_month": month, "told_at": now},
                code="HOLDINGS_BAND_NOTICE_LOCKED", label="holdings band notice")


def mark_readiness_told(*, now: str, root: Path | None = None) -> None:
    """Record that the H6 readiness message was delivered — once; it is not sent again."""
    cash_flows.mark_readiness_told(state_dir(root), now=now)


def mark_rule_flows_told(token: str | None, *, now: str, root: Path | None = None) -> None:
    """Record that Thomas was told of the rule-classified Toss flows ``token`` names — only after delivery."""
    cash_flows.mark_rule_flows_told(state_dir(root), token=token, now=now)


def mark_exceptions_told(token: str | None, *, now: str, root: Path | None = None) -> None:
    """Record that Thomas was told of the H6 exceptions ``token`` names — only after delivery (H6d-min:
    by exception, so one found while the message was in flight is still told)."""
    cash_flows.mark_exceptions_told(state_dir(root), token=token, now=now)


def _reason(exc: BaseException) -> str:
    """The reason code a runtime error carries, else the exception's class (C7 PR-D: a refused ledger
    said only "ToolError" on the board, which named nothing)."""
    return exc.reason_code if isinstance(exc, MvpRuntimeError) else type(exc).__name__


def blocked_check(*, now: str, root: Path | None = None) -> dict[str, Any]:
    """C7 PR-D: whether the cash-flow ledger or its state is BLOCKED, judged on its own after the fire —
    read-only, whatever :func:`refresh` returned (an early return, a failed Toss read). Returns ``{"alert":
    (token, text) | None, "note": str | None}``; the caller sends the alert and only then records it with
    :func:`mark_blocked_told`. Never raises: a judgement that cannot run is told as BLOCKED, not as fine."""
    try:
        return cash_flows.blocked_alert(state_dir(root), now=now)
    except Exception as exc:  # noqa: BLE001 — see the docstring
        reason = type(exc).__name__
        return {"alert": (f"{cash_flows.BLOCKED_TOKEN_TOLD}|{cash_flows.BLOCKED_KEY_STATE}:check:{reason}",
                          f"[보유 자산] H6 현금흐름 원장 BLOCKED 판정이 실행되지 않았습니다 ({reason}, {now}). "
                          "정상으로 보지 않습니다. Thomas 터미널에서 확인해 주세요."),
                "note": f"check failed ({reason})"}


def mark_blocked_told(token: str, *, now: str, root: Path | None = None) -> bool:
    """Record a delivered BLOCKED or clearing message (C7 PR-D) — only after delivery. A write that fails
    raises, so it is never counted as recorded."""
    return cash_flows.mark_blocked_told(state_dir(root), token=token, now=now)


def mark_told(state: str, *, now: str, root: Path | None = None) -> None:
    """Record the drawdown state Thomas was told — called only after the message was delivered."""
    combined.write_told(state_dir(root), state, now=now)


def refresh_snapshot(*, now: str, root: Path | None = None, timeout_seconds: int = 10) -> str:
    """:func:`refresh`'s status line alone, for callers that send no alert."""
    return refresh(now=now, root=root, timeout_seconds=timeout_seconds)["status"]


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _unreadable(path) from None
    if not isinstance(body, dict):
        raise _unreadable(path)
    return body


def _unreadable(path: Path) -> ToolError:
    return ToolError(HOLDINGS_SNAPSHOT_UNREADABLE, f"{path.name} is not a readable snapshot")


def _age_seconds(stamp: Any, now: str) -> float | None:
    try:
        return (timeutil.parse_iso(now) - timeutil.parse_iso(str(stamp))).total_seconds()
    except (TypeError, ValueError):
        return None


def load_local_view(*, now: str, root: Path | None = None) -> str:
    """The whole last fire, for the account holder's terminal only (``holdings_board --local``). Never
    route this text to a door, a channel or a model prompt: that is what the stored snapshot is for."""
    body = _read_json(local_path(root))
    if body is None:
        return "holdings    : no local board yet (written by the next holdings_refresh fire)"
    age = _age_seconds(body.get("as_of"), now)
    age_text = "unknown age" if age is None else f"{int(age // 60)} min old"
    lines = ["LOCAL BOARD - terminal only; do not paste into a chat, a model prompt or a channel",
             *render_view(body, stamp_line=f"{'as of':12}: {body.get('as_of')} ({age_text})")]
    lines.extend(f"{'note':12}: {note}" for note in body.get("notes") or [])
    return "\n".join(lines)


def load_rebalance(*, now: str, root: Path | None = None, force: bool = False) -> str:
    """The rebalancing proposal over the last fire (``holdings_board --rebalance``), for Thomas's terminal only.
    Names instruments and amounts: never route it to a door, a channel or a model prompt."""
    body = _read_json(local_path(root))
    held = _read_json(positions_path(root))
    candidates = effective_candidates(root)
    age = _age_seconds(body.get("as_of"), now) if body else None
    # The board and the positions must come from one fire; a mismatch is a fire that wrote one and not the other.
    rows = held.get("positions") if held and body and held.get("as_of") == body.get("as_of") else None
    gain, gain_note = _overseas_gain_for(_read_json(tax_estimate_path(root)), now=now)
    result = rebalance.plan_from_local(body, positions=rows, candidates=candidates, age_seconds=age,
                                       stale_after_seconds=STALE_AFTER_SECONDS, overseas_gain_ytd_krw=gain,
                                       force=force)
    result.notes.append(gain_note)
    return "\n".join(rebalance.render(result, as_of=(body or {}).get("as_of")))


def _overseas_gain_for(estimate: Mapping[str, Any] | None, *, now: str) -> tuple[float | None, str]:
    """The FIFO gain (§3's sample line) when today's or yesterday's rebuild for this year succeeded, else None:
    the planner then skips step 3 (§2.3). The note says which, and that it is an estimate either way."""
    block = (estimate or {}).get("overseas_gain")
    today = overseas_gain.kst_day(now)
    yesterday = overseas_gain.kst_day((timeutil.parse_iso(now) - timedelta(days=1)).isoformat())
    if not isinstance(block, Mapping):
        return None, "overseas gain: not computed yet (the next holdings fire rebuilds it)"
    if block.get("computed_on") not in (today, yesterday) or block.get("year") != int(today[:4]):
        return None, f"overseas gain: last rebuilt {block.get('computed_on')}, too old to use"
    if block.get("status") != "ok" or block.get("fifo_krw") is None:
        return None, f"overseas gain: refused ({block.get('reason')})"
    return float(block["fifo_krw"]), (
        f"overseas gain: FIFO (method unconfirmed, estimate); moving average {block.get('moving_average_krw'):,} KRW; "
        f"{block.get('sells_counted')} sells this year, {block.get('unsettled_sells')} unsettled, "
        f"{block.get('approx_fx_fills')} fills at an approximate rate")


def load_snapshot_status(*, now: str, root: Path | None = None) -> dict[str, Any]:
    """The stored snapshot's freshness and its ``cash_flows`` block, for a door that projects them (the Claude
    door's ``holdings_status``). Renders nothing, so a block the board would choke on cannot sink this read.
    An unreadable snapshot raises, exactly as :func:`load_holdings_view` does."""
    body = _read_json(snapshot_path(root))
    if body is None:
        return {"available": False, "reason_code": HOLDINGS_SNAPSHOT_MISSING}
    age = _age_seconds(body.get("as_of"), now)
    return {"available": True, "as_of": body.get("as_of"), "age_seconds": age,
            "stale": age is None or age > STALE_AFTER_SECONDS, "partial": body.get("partial"),
            "cash_flows": body.get("cash_flows")}


def load_holdings_view(*, now: str, root: Path | None = None) -> tuple[str, dict[str, Any]]:
    """The board the doors render, and its data. Opens no socket: it reads a file.

    A missing snapshot is a normal state (no key yet, or no fire yet) and says so. An unreadable one
    raises: a board that rendered as empty because a file would not parse would be a false
    statement about money."""
    body = _read_json(snapshot_path(root))
    if body is None:
        mark = _read_json(refresh_mark_path(root))
        attempted = mark.get("attempted_at") if mark else None
        text = (
            "holdings    : no snapshot yet"
            + (f" (last attempt {attempted})" if attempted else " (no refresh has run)")
        )
        return text, {"available": False, "reason_code": HOLDINGS_SNAPSHOT_MISSING,
                      "last_attempt": attempted}
    age = _age_seconds(body.get("as_of"), now)
    stale = age is None or age > STALE_AFTER_SECONDS
    age_text = "unknown age" if age is None else f"{int(age // 60)} min old"
    lines = render_view(body, stamp_line=f"{'as of':12}: {body.get('as_of')} ({age_text})")
    if stale:
        lines.append(f"{'STALE':12}: older than {STALE_AFTER_SECONDS // 3600} h; not a statement about now")
    lines.extend(f"{'note':12}: {note}" for note in body.get("notes") or [])
    data = {key: body.get(key) for key in sorted(body) if key not in ("record_type", "written_at")}
    data.update({"available": True, "age_seconds": age, "stale": stale})
    return "\n".join(lines), data
