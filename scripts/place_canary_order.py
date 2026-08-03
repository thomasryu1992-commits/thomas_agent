"""Place ONE deliberate live canary order (LP4 increment 2b) — an operator tool.

    python -m scripts.place_canary_order --symbol BTCUSDT --quantity <qty> --notional <qty x price>
    python -m scripts.place_canary_order --symbol BTCUSDT --quantity <qty> --notional <qty x price> --json

``--notional`` must be **this order's real notional at the current price**. It is verified
against ``--quantity x`` the venue's own last close before anything measures it, and an
under-declaration is refused with the number to use instead — because ``--quantity`` is what
reaches the venue while ``--notional`` is only what the guard compares to the registered
budget's caps, so a low declaration would walk a larger real position past the per-order and
exposure limits. A worked constant used to stand here (``--quantity 0.001 --notional 60``):
correct at BTC 60,000, 7% short at 64,512, so following the documentation produced exactly
that under-declaration. Any constant is wrong at tomorrow's price.

**The read-only market-data feed is therefore a canary precondition.** Without a usable price
this command refuses rather than skipping the check — on the runs where market data is broken,
"nothing to check" must not read as "approved".

A canary is one small **real** order, placed on purpose, to prove that signing, submission, and
reconciliation actually work at the venue. Three clean canaries are what the autonomous path's
promotion gate requires — so this is the only door that is *not* gated on that evidence, because
it is what earns it. Everything else still applies: `MVP_LIVE_TRADING=real`, the **canary**
confirmation phrase (distinct from the autonomous one), a valid registered budget, both kill
switches, the daily-loss breaker, and the size / daily-count / exposure caps.

**Real money. Every step here is Thomas's.** Claude does not run this, does not handle the keys,
and does not enable live trading. Without the opt-in and the canary phrase this command refuses;
with the default (opt-in unset) it selects the inert dry-run adapter and sends nothing.

Since 2026-07-28 the opt-in is the whole switch — the per-machine `live_trading` grant that used
to also be required was removed by Thomas. One fewer thing to set, and one fewer thing standing
between an exported variable and a real order.

Deliberately one order per invocation. A canary opens a small position, which the operator then
closes on the venue. It is not wired into any autonomous cycle.

**``--with-bracket`` is the reason this stopped being entry-only.** Being entry-only is what
made three clean canaries the wrong evidence: they proved signing, submission and
reconciliation, and when the runtime first traded autonomously on 2026-08-02 the thing that was
broken was none of those — the venue refused the protective STOP, twice, because Binance had
moved conditional orders to the Algo Order API eight months earlier. The gate had measured a
property that was not the one at risk.

So with the flag, once the entry is CONFIRMED FILLED this places the real protective stop
through the same ``place_bracket_leg`` the autonomous leg uses, checks the venue reports it
RESTING, and withdraws it through the same ``cancel_bracket_legs``. That is the one thing that
cannot be proved without an open position — a ``closePosition`` order is refused outright when
there is nothing to close (``-4509``), which is why it cannot be checked on a flat account and
why it has to happen here.

The proving stop is not protection and is never left in place: it sits ``--bracket-distance-pct``
away so it cannot trigger in the seconds it is alive, and it is cancelled immediately. A cancel
that fails prints the order id and fails the run — a resting order this tool left behind counts
against the venue's per-symbol conditional cap and must never read as a pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.cli_common import (
    EXIT_BLOCKED,
    EXIT_OK,
    EXIT_USAGE,
    force_utf8_io,
    gate_banners,
)
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.audit import AuditError
from runtime.mvp_runtime.crypto import live_execution, live_governance, live_promotion
from runtime.mvp_runtime.crypto.account import read_account
from runtime.mvp_runtime.crypto.live_order import (
    CANARY_CONFIRMATION_ENV,
    build_live_order_intent,
    check_declared_notional,
    count_today,
    select_live_order_counter,
    enrich_order_identity,
    evaluate_live_order_guard,
    render_guard_text,
    resolve_live_order_limits,
)
from runtime.mvp_runtime.crypto.live_pnl import live_risk_snapshot, venue_daily_realized_net
from runtime.mvp_runtime.crypto.live_entry import BRACKET_WORKING_TYPE
from runtime.mvp_runtime.crypto.live_leg import (
    build_bracket_intent,
    cancel_bracket_legs,
    place_bracket_leg,
)
from runtime.mvp_runtime.crypto.market_data import (
    read_reference_price,
    select_market_data_collector,
)
from runtime.mvp_runtime.crypto.live_position import compute_open_notional_usdt
from runtime.mvp_runtime.errors import MvpRuntimeError
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run
from runtime.mvp_runtime.store import LedgerStore


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="place_canary_order",
        description="Place ONE deliberate live canary order and record the reconciled result.",
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--direction", default="LONG", choices=["LONG", "SHORT"])
    parser.add_argument("--quantity", type=float, required=True,
                        help="base-asset quantity (keep a canary small)")
    parser.add_argument("--notional", type=float, required=True,
                        help="this order's real notional in USDT at the current price "
                             "(quantity x price; never back-filled from the cap). Verified "
                             "against the venue's last close; under-declaring is refused, "
                             "over-declaring passes since it only tightens every cap")
    parser.add_argument(
        "--with-bracket", action="store_true",
        help="after the fill, place the protective STOP leg, confirm it RESTS and withdraw it "
             "— the evidence an entry-only canary cannot produce",
    )
    parser.add_argument(
        "--bracket-distance-pct", type=float, default=DEFAULT_BRACKET_DISTANCE_PCT,
        help=f"how far the proving stop sits from the fill (min {MIN_BRACKET_DISTANCE_PCT})",
    )
    parser.add_argument("--timeout-seconds", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="emit the full result as JSON")
    parser.add_argument("--root", type=Path, default=None, help="state root (defaults to the repo)")
    return parser.parse_args(argv)


# How far the PROVING stop sits from the fill. It protects nothing — it exists to be accepted by
# the venue and then withdrawn — so it is placed far enough away that it cannot trigger in the
# seconds it is alive. A real bracket's stop is `stop_atr x ATR` from the entry and is the spec's
# decision, not this tool's.
DEFAULT_BRACKET_DISTANCE_PCT = 10.0
MIN_BRACKET_DISTANCE_PCT = 2.0


def prove_bracket(
    *, result, symbol: str, direction: str, adapter, distance_pct: float, timeout_seconds: int
) -> dict:
    """Place the real protective stop against the position just opened, confirm it RESTS, and
    withdraw it. Never raises: every failure here is a finding, and one that has to be reported
    beside an order that is already at the venue.

    Placed through `place_bracket_leg` and withdrawn through `cancel_bracket_legs` — the same
    functions the autonomous leg uses. A parallel implementation would prove the wrong thing,
    which is the whole lesson of the canary that proved the wrong property.
    """
    fill = result.get("fill") or {}
    fill_price = float(fill.get("avg_price") or 0.0)
    filled = float(fill.get("executed_qty") or 0.0)
    if result.get("reconcile_status") != live_promotion.RECONCILED or fill_price <= 0 or filled <= 0:
        # No position means the venue refuses a closePosition order outright. Saying "not
        # attempted" is the honest answer; attempting it would manufacture a -4509 that says
        # nothing about the bracket.
        return {"attempted": False,
                "reason": "the entry did not confirm a fill, so there is nothing to protect"}

    # A LONG is stopped BELOW the fill and a SHORT ABOVE — the bracket's own rule.
    long = direction.upper() == "LONG"
    factor = (1.0 - distance_pct / 100.0) if long else (1.0 + distance_pct / 100.0)
    trigger = round(fill_price * factor, 8)
    intent = build_bracket_intent(
        symbol=symbol, leg="SL", side="SELL" if long else "BUY", price=trigger,
        working_type=BRACKET_WORKING_TYPE, position_seed=str(result["client_order_id"]),
    )
    placement = place_bracket_leg(intent, adapter=adapter, timeout_seconds=timeout_seconds)
    # Attempted whatever the placement reported: a rejected submit may still have landed, which
    # is exactly why `place_bracket_leg` asks the venue rather than trusting the submit.
    cancels = cancel_bracket_legs(
        {"symbol": symbol, "stop_client_order_id": placement.get("client_order_id")},
        adapter=adapter, timeout_seconds=timeout_seconds,
    )
    cancel = cancels[0] if cancels else {"cancelled": False, "error": "no cancel attempted"}
    return {
        "attempted": True,
        "trigger_price": trigger,
        "rested": bool(placement.get("placed")),
        "status": placement.get("status"),
        "client_order_id": placement.get("client_order_id"),
        "error": placement.get("error"),
        "error_detail": placement.get("error_detail"),
        "withdrawn": bool(cancel.get("cancelled")),
        "cancel_error": cancel.get("error"),
        "cancel_error_detail": cancel.get("error_detail"),
        "left_behind": bool(placement.get("placed")) and not cancel.get("cancelled"),
    }


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    if args.with_bracket and args.bracket_distance_pct < MIN_BRACKET_DISTANCE_PCT:
        sys.stderr.write(
            f"BLOCKED: --bracket-distance-pct must be at least {MIN_BRACKET_DISTANCE_PCT}. A "
            "proving stop close enough to trigger would stop proving the bracket and start "
            "closing the canary.\n"
        )
        return EXIT_USAGE
    if args.quantity <= 0 or args.notional <= 0:
        sys.stderr.write("ERROR: --quantity and --notional must both be positive\n")
        return EXIT_USAGE

    root = args.root
    now = timeutil.utc_now_iso()

    try:
        # 0. Before anything else, and emphatically before the venue: a host-side root run
        #    would leave this canary's registry row and audit event root-owned, and the uid the
        #    services run as could never write them again. That refusal has to come BEFORE the
        #    order, because afterwards the only options are a broken registry or a real
        #    position with no local record of it.
        assert_not_foreign_root_run(root)

        # 1. Caps come from the REGISTERED budget (never the env cap vars).
        limits, budget = resolve_live_order_limits(root, now=now)

        # 2. The live facts the guard judges. Each is read, never assumed.
        control_state = ControlStore(root).load() if root is not None else ControlStore.default().load()
        runtime_active = control_state.execution_allowed
        clean_count, canary_error = live_promotion.clean_canary_order_count(root)
        submitted_today = count_today(root)

        # 2b. Verify the declaration before anything measures it. `--quantity` is what reaches
        #     the venue; `--notional` is only what the guard compares to the registered budget's
        #     caps, and until now nothing related the two — so an under-declared notional walked
        #     a larger real position through the per-order and exposure limits. This is the only
        #     place the two numbers are independent: the autonomous path derives both from one
        #     `size_live_order` call, so they agree by construction, and the guard's other
        #     callers have no price to give it. If a second independent-declaration caller ever
        #     appears, this belongs in the shared guard instead.
        reference_price, price_error = read_reference_price(
            args.symbol,
            collector=select_market_data_collector(now=now, root=root),
            now=now,
            timeout_seconds=args.timeout_seconds,
        )
        notional_check = check_declared_notional(
            quantity=args.quantity,
            declared_notional_usdt=args.notional,
            reference_price=reference_price,
        )
        if not notional_check["ok"]:
            detail = f" (price read: {price_error})" if price_error else ""
            sys.stderr.write(
                f"BLOCKED {notional_check['reason_code']}: {notional_check['message']}{detail}\n"
            )

        # 3. Open exposure from the VENUE, via LP5's fail-closed supplier (an unreadable account
        #    reports at-cap, never 0.0 — the guard's one fail-open path). A canary additionally
        #    refuses outright, because "configure the account feed" is the actionable answer for a
        #    deliberate operator tool, rather than a cap refusal it has to decode.
        snapshot, account_use = read_account(timeout_seconds=args.timeout_seconds, root=root)
        open_notional = compute_open_notional_usdt(
            snapshot, at_cap=limits.max_open_notional_usdt
        )
        if snapshot is None:
            sys.stderr.write(
                "BLOCKED NO_ACCOUNT_VISIBILITY: cannot read the venue account, so open exposure "
                "is unknown and the exposure cap cannot be honored. Configure the read-only "
                f"account feed first (account_use={account_use.get('degraded_reason_code')}).\n"
            )
            return EXIT_BLOCKED

        # Both refusals are printed before either returns, so an operator who has to fix the
        # declaration *and* the account feed sees both in one run rather than one per attempt.
        if not notional_check["ok"]:
            return EXIT_BLOCKED

        # 3b. The daily-loss breaker, measured against what the VENUE realized today.
        #     Deliberately after the account read rather than beside the other guard inputs:
        #     the local outcome ledger is written only by the autonomous leg nothing may import,
        #     and this path is entry-only, so reading it here yields 0.0 forever and the limit
        #     bounds nothing. The snapshot above already carries the venue's own realized figure
        #     — fees and funding included — at no extra request, so the breaker gets a real
        #     number from a read this tool was making anyway. It reads the STRICTER of the UTC
        #     calendar day and the rolling 24h: this used to take `1d` alone, and a net sum over
        #     a wider window can be less negative, so yesterday evening's profit could mask
        #     today's loss on the one measure meant to stop the day.
        venue_realized = venue_daily_realized_net(snapshot.realized_windows)
        risk = live_risk_snapshot(
            limit_usdt=limits.daily_loss_limit_usdt, root=root, now=now,
            venue_realized_pnl_usdt=venue_realized,
        )

        # 4. The gate. Selecting the adapter is what proves the grant: an inert dry-run adapter
        #    means no grant is active, so nothing can be sent — and the guard is told so rather
        #    than being handed an optimistic assumption.
        adapter = live_execution.select_order_adapter(now=now, root=root)
        grant_open = bool(getattr(adapter, "network_egress", False))
        # Say so, on the one path that can move real money. Every other gated capability in
        # the repo prints its authorization notice; this one did not, because `gate_banners`
        # took a named parameter per role and no one added a fifth. The operator running this
        # deserves the same line the search tool gets.
        gate_banners(live_order_adapter=adapter)

        # 5. The intent, then the guard in CANARY mode: every check except the promotion gate,
        #    authorized by the canary phrase.
        intent = enrich_order_identity(build_live_order_intent(
            {"direction": args.direction}, symbol=args.symbol, quantity=args.quantity,
            notional_usdt=args.notional, now=now,
        ))
        verdict = evaluate_live_order_guard(
            intent,
            gate_open=grant_open,
            runtime_active=runtime_active,
            daily_loss_breached=bool(risk["daily_loss_limit_breached"]),
            clean_canary_orders=clean_count,
            submitted_today=submitted_today,
            current_open_notional_usdt=open_notional,
            budget_registered=bool(budget.get("valid")),
            allowed_symbols=budget.get("symbol_allowlist") or (),
            canary=True,
            limits=limits,
        )

        sys.stdout.write(render_guard_text(verdict) + "\n")
        if canary_error:
            sys.stdout.write(f"canary history: {canary_error}\n")
        if not verdict["approved"]:
            sys.stderr.write(
                "BLOCKED: the canary guard refused; fix what it names rather than working around it.\n"
                f"(the canary phrase env is {CANARY_CONFIRMATION_ENV})\n"
            )
            return EXIT_BLOCKED

        # 5b. The governance record, BEFORE the order. `p5_policy_gate` requires
        #     `post_action_report_and_audit` for FINANCIAL_APPROVED_TRADING_USE, and until this
        #     landed a real order left nothing on the hash chain. Preparing it first means a
        #     governance failure (no active Core, an authority conflict) costs nothing — it
        #     refuses before any money moves. It grants nothing; the guard above is what permits.
        governance = live_governance.prepare_live_order_governance(
            intent, purpose=live_governance.PURPOSE_CANARY, now=now, repo_root=root,
        )

        # 6. Send exactly one order, then learn the truth from the venue.
        #
        #    The daily counter is incremented HERE, by this tool. It used to be incremented
        #    only by `live_leg.execute_live_entry` — the autonomous leg, which no entry point
        #    may import — so the one door that can actually place an order never counted its
        #    own orders. `count_today` reads a file nothing wrote, returns 0 for a missing
        #    file, and the registered `max_daily_order_count` therefore refused nothing: two
        #    real canaries went out on 2026-07-26 and `live_order_counter.json` did not exist.
        counter = select_live_order_counter(now=now, root=root)
        counter_error = None
        try:
            result = live_execution.submit_and_reconcile(
                intent, adapter=adapter, guard_verdict=verdict, now=now,
                timeout_seconds=args.timeout_seconds,
            )
        finally:
            # In `finally`, and deliberately: what consumes daily budget is an order that MAY
            # have reached the venue, not one confirmed to have done so. A submit that raised
            # is precisely the ambiguous case — the flapping connection LiveOrderCounter's own
            # rule is about — and skipping it there would let one cap be spent many times.
            # Over-counting a submit that never left is the safe direction for a risk limit.
            try:
                counter.record_submission()
            except Exception as exc:  # noqa: BLE001 — past the venue; report, never raise
                counter_error = getattr(exc, "reason_code", type(exc).__name__)

        # 6b. Prove the protective bracket against the position that now exists. This is the
        #     one check that CANNOT be made on a flat account: a `closePosition` order is
        #     refused outright when there is nothing to close (-4509), so the only moment it can
        #     be tested is while a real position is open — exactly the moment an entry-only
        #     canary used to hand back to the operator untested.
        bracket_proof = None
        if args.with_bracket:
            bracket_proof = prove_bracket(
                result=result, symbol=args.symbol, direction=args.direction, adapter=adapter,
                distance_pct=args.bracket_distance_pct, timeout_seconds=args.timeout_seconds,
            )

        # 7. Record it. `clean` is derived by the record from the reconcile facts — this tool
        #    cannot assert that its own canary was clean.
        record = live_promotion.build_canary_order_record(
            reconcile_status=result["reconcile_status"],
            symbol=result["symbol"],
            exchange_order_id=result["exchange_order_id"],
            client_order_id=result["client_order_id"],
            mismatches=result["mismatches"],
            notional_usdt=args.notional,
            # The venue's own numbers, so the evidence can be re-derived instead of trusted.
            # `--notional` is the operator's declaration; `fill.cum_quote` is what the order
            # actually cost. Step 3 checks them against a reference price before the order,
            # and this records what the venue said after it.
            quantity=args.quantity,
            fill=result.get("fill"),
            now=now,
        )
        registry_error = None
        try:
            live_promotion.select_canary_registry(now=now, root=root).append_canary_order(record)
        except MvpRuntimeError as exc:
            # The order is already at the venue; say so rather than implying nothing happened.
            registry_error = exc.reason_code

        # 8. The report half of EXECUTE_AND_REPORT: one audit event on the durable chain,
        #    carrying what the VENUE said. Best-effort by the same reasoning as the registry
        #    write above — the money has already moved, so a failure here is reported, never
        #    swallowed and never allowed to imply the order did not happen.
        #
        #    `LedgerStore.default(root)` rather than `LedgerStore(root)`: the constructor takes
        #    the ledger DIRECTORY, so the bare form both crashed on the `--root` default of None
        #    (`Path(None)`) and, when given a root, would have written the chain beside the repo
        #    instead of under `.runtime_governance_state/runtime_ledger/`. Every other caller
        #    appends `LEDGER_REL` for exactly this reason; `default()` is that resolution.
        audit_error = None
        try:
            event, _sha = live_governance.report_live_order(
                governance, result, guard_verdict=verdict, now=now, repo_root=root,
            )
            LedgerStore.default(root).append_audit_events([event])
        except (MvpRuntimeError, AuditError) as exc:
            audit_error = getattr(exc, "reason_code", type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 — see below; breadth is the point
            # Past the point of no return: the order is AT THE VENUE. An unexpected exception
            # here must not become a traceback, because a traceback is indistinguishable from
            # "the order never happened" to the operator reading it — and that is the single
            # most expensive thing this tool can communicate wrongly. The `TypeError` above
            # escaped the narrow tuple and did exactly that: a filled canary reported as a
            # crash, with the audit event built but never appended. Report and carry on to the
            # summary; the caller learns the order landed AND that its record did not.
            audit_error = f"UNEXPECTED_{type(exc).__name__}"
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc.reason}\n")
        return EXIT_BLOCKED

    payload = {"guard": verdict, "result": result, "canary_record": record,
               "registry_error": registry_error, "audit_error": audit_error,
               "counter_error": counter_error, "bracket_proof": bracket_proof,
               "permission_decision_id": governance["permission_decision"]["permission_decision_id"]}
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1, default=str) + "\n")
    else:
        sys.stdout.write(
            f"\ncanary {record['canary_order_id']}\n"
            f"  reconcile : {record['reconcile_status']}\n"
            f"  clean     : {record['clean']}\n"
            f"  venue id  : {result['exchange_order_id']}\n"
            f"  fill      : {result['fill']}\n"
        )
        if result["mismatches"]:
            sys.stdout.write(f"  MISMATCH  : {'; '.join(result['mismatches'])}\n")
        if registry_error:
            sys.stdout.write(f"  REGISTRY  : NOT recorded ({registry_error}) — the order IS placed\n")
        sys.stdout.write(
            f"  permdec   : {governance['permission_decision']['permission_decision_id']} (P5)\n"
        )
        if audit_error:
            sys.stdout.write(f"  AUDIT     : NOT recorded ({audit_error}) — the order IS placed\n")
        if counter_error:
            sys.stdout.write(
                f"  DAILY CAP : NOT counted ({counter_error}) — the order IS placed, so the "
                "daily cap now under-reports until this is repaired\n"
            )
        if bracket_proof is not None:
            if not bracket_proof.get("attempted"):
                sys.stdout.write(f"\nbracket   : NOT TESTED — {bracket_proof.get('reason')}\n")
            else:
                rested = "RESTED" if bracket_proof["rested"] else "REFUSED"
                sys.stdout.write(
                    f"\nbracket   : {rested} at {bracket_proof['trigger_price']} "
                    f"(status {bracket_proof.get('status')})\n"
                    f"  withdrawn: {bracket_proof['withdrawn']}\n"
                )
                if not bracket_proof["rested"]:
                    sys.stdout.write(
                        f"  refused  : {bracket_proof.get('error_detail') or bracket_proof.get('error')}\n"
                        "  The protective stop still does not place. Do NOT enable autonomous\n"
                        "  entries — every one of them will fill and be closed again.\n"
                    )
                if bracket_proof["left_behind"]:
                    sys.stdout.write(
                        f"  LEFT BEHIND: {bracket_proof['client_order_id']} is resting and the\n"
                        "  cancel failed. Withdraw it by hand — it counts against this symbol's\n"
                        "  conditional order cap until you do.\n"
                    )
        sys.stdout.write(
            "\nClose this canary position on the venue yourself — a canary only opens.\n"
        )
    # A placed-but-unrecorded canary, or one that did not reconcile, is not a success.
    # `audit_error` counts too: `post_action_report_and_audit` is what `p5_policy_gate` requires
    # of FINANCIAL_APPROVED_TRADING_USE, so an order the durable chain never recorded has left a
    # governance obligation unmet — exiting 0 would report that as a clean canary.
    # `counter_error` likewise: an uncounted order leaves the daily cap reading low for every
    # later run, which is a risk limit quietly widened rather than a bookkeeping nicety.
    # A bracket that was asked for and did not rest is a failed canary, and one this tool left
    # resting at the venue is worse than a failure — both must refuse to exit 0.
    bracket_failed = bracket_proof is not None and (
        not bracket_proof.get("attempted")
        or not bracket_proof.get("rested")
        or bracket_proof.get("left_behind")
    )
    if registry_error or audit_error or counter_error or not record["clean"] or bracket_failed:
        return EXIT_BLOCKED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
