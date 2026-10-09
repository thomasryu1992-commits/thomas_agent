"""What the Toss account holds — the read-only multi-account board's account.

    python -m scripts.holdings_board            # the stored aggregate (no call to Toss)
    python -m scripts.holdings_board --json     # the same, as JSON
    python -m scripts.holdings_board --full     # a LIVE read with every holding, terminal only
    python -m scripts.holdings_board --local    # the last fire in full (by market, wallet, class), terminal only
    python -m scripts.holdings_board --reset-peak --reason "withdrew 5M KRW"   # after a deposit/withdrawal
    python -m scripts.holdings_board --unclassified                 # wallet assets no class places (terminal only)
    python -m scripts.holdings_board --classify binance:SOL=coin    # Thomas's entry (H5a), as the service user
    python -m scripts.holdings_board --unclassify binance:SOL
    python -m scripts.holdings_board --cash-flow-cutover 2026-10-09T04:30:00Z   # once, at H6b activation
    python -m scripts.holdings_board --flows                        # H6 exceptions, terminal only (H6d-min)
    python -m scripts.holdings_board --resolve KEY --kind deposit --by thomas --reason "..."   # terminal only
    python -m scripts.holdings_board --semantics-epoch --change-ref "#1203" --by thomas --reason "..."

Read-only. It places nothing and has no flag that would let it.

**Default is the stored snapshot, not a live read.** Toss allows one valid token per client, and
issuing one revokes the previous one. scheduler-maint is that one issuer: its ``holdings_refresh``
fire reads the account and stores the aggregate, and this command renders that file without touching
the network. The default output is also the aggregate, because the data may serve only the investor's
own trading purpose (appendix B of ``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``). Output that might
be pasted into a chat, a model prompt or a channel should be this one.

**``--full`` is a live read**, with per-symbol values from which a price follows, meant for the
account holder's terminal. It issues its own token, which revokes the lane's. The lane's next fire
reissues once, so nothing breaks, but the command says so when it runs. It needs the lane's variables:
run it where they are (``docker exec thomas-scheduler-maint python -m scripts.holdings_board --full``).
Without the gate it prints that it is not configured and exits 0. A failed read exits
``EXIT_BLOCKED`` with the error's reason code.

**``--local``** (H3, Thomas 2026-10-08) renders the last fire's local file: the Toss account by market,
Binance by wallet and sub-class and every class amount, which the stored snapshot leaves out under the
single-holding rule (``holdings/disclosure.py``). No network. Terminal only, like ``--full``.

**``--unclassified`` / ``--classify`` / ``--unclassify``** (H5a, Thomas 2026-10-08). ``--unclassified``
reads the Binance wallet live in this process (the lane's gate and read key, so run it in scheduler-maint)
and prints the ids of the held assets that neither the sub-class map nor an entry places, with the file's
version — names only, no amount. Toss codes are on ``--full``'s board. ``--classify ID=CLASS`` writes
entries to the host-local classification file (``holdings/classification.py``) and ``--unclassify ID``
removes them; each moves ``mapping_version`` once, and the next ``holdings_refresh`` fire uses it. Both
write governed state, so they run as the service user (``docker exec -u 10001 thomas-scheduler-maint
...``) and refuse a host-side root run. The ids are holdings: these commands are Thomas's, on his
terminal. Claude does not run them and does not see their output.

**``--cash-flow-cutover AT``** (H6b-hardening, Thomas 2026-10-09) records the cash-flow ledger's accounting
boundary once, at activation: events before it are observed only. A second call is refused; moving it
needs ``--migrate --reason TEXT`` and writes a ``cutover_migrated`` line. Service user only, like the
other writes. It names no holding, so Claude may run it at a deploy.

**``--reset-peak --reason TEXT``** (P2; reason required since H5b, Thomas 2026-10-08) logs the request
with the peak it forgets in the host-local baseline log (``holdings/baseline_log.py``), then forgets the
combined total's peak and the drawdown alert's told state; the
next complete ``holdings_refresh`` fire starts a new peak. The drawdown metric cannot tell a withdrawal
from a loss, so this is the answer after any deposit or withdrawal. It writes governed state, so it runs
in the lane as the service user (``docker exec -u 10001 thomas-scheduler-maint python -m
scripts.holdings_board --reset-peak``) and refuses a host-side root run.

**``--flows`` / ``--resolve`` / ``--semantics-epoch``** (H6d-min, Thomas 2026-10-09). ``--flows`` lists the
H6 exceptions — key, category, phase, review state and the kinds each may be closed as; no amount.
``--resolve KEY --kind K`` closes one exception's review with a ``flow_resolved`` line; the event stays
HELD (a review is not evidence, and nothing is eligible for accounting in H6b). ``--semantics-epoch
--change-ref REF`` starts a new Toss semantics epoch after a reconciliation change: it prints the
reconciliation digest and asks for its first 8 characters back. Both writes need an interactive
terminal, ``--by`` and ``--reason``, and run as the service user (``docker exec -it -u 10001
thomas-scheduler-maint ...``). The keys name venue events: these are Thomas's, on his terminal.

Claude does not run the live read, ``--local``, ``--unclassified``, ``--classify``, ``--unclassify``,
``--flows``, ``--resolve`` or ``--semantics-epoch`` (their output or input names holdings and would reach a model provider), and does not handle the keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError  # noqa: E402
from runtime.mvp_runtime.holdings import classification, combined  # noqa: E402
from runtime.mvp_runtime.holdings.board import render_full  # noqa: E402
from runtime.mvp_runtime.holdings.store import load_holdings_view, load_local_view, state_dir  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.holdings.toss_account import read_holdings  # noqa: E402

LIVE_READ_NOTICE = (
    "note        : this live read issued its own Toss token, which revoked scheduler-maint's; "
    "the lane reissues once on its next fire"
)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="the stored aggregate as JSON")
    mode.add_argument("--full", action="store_true", help="a live read with every holding (terminal only)")
    mode.add_argument("--local", action="store_true",
                      help="the last fire in full, from the local file (terminal only; no network)")
    mode.add_argument("--reset-peak", action="store_true",
                      help="forget the combined peak and the drawdown told-state (after a deposit/withdrawal)")
    mode.add_argument("--unclassified", action="store_true",
                      help="wallet assets no class places, read live (terminal only; names, no amounts)")
    mode.add_argument("--classify", nargs="+", metavar="ID=CLASS",
                      help="add or change classification entries (Thomas, as the service user)")
    mode.add_argument("--unclassify", nargs="+", metavar="ID", help="remove classification entries")
    mode.add_argument("--cash-flow-cutover", metavar="AT", help="record the cash-flow ledger's boundary (once)")
    mode.add_argument("--flows", action="store_true", help="H6 exceptions and their review state (terminal only)")
    mode.add_argument("--resolve", metavar="KEY", help="close one H6 exception's review (Thomas, terminal only)")
    mode.add_argument("--semantics-epoch", action="store_true",
                      help="start a new Toss semantics epoch after a reconciliation change (Thomas, terminal only)")
    parser.add_argument("--kind", help="what the exception was (--resolve; --flows lists the allowed kinds)")
    parser.add_argument("--change-ref", help="the merged reconciliation change, #<PR> or a commit (--semantics-epoch)")
    parser.add_argument("--migrate", action="store_true", help="move an existing cutover (--cash-flow-cutover)")
    parser.add_argument("--shadow-started", metavar="AT", help="when the shadow ledger first ran (recorded once)")
    parser.add_argument("--reason", help="why the peak is reset (--reset-peak; required, logged)")
    parser.add_argument("--by", help="who asks (logged; --reset-peak and --cash-flow-cutover default to thomas, "
                                     "--resolve and --semantics-epoch require it)")
    parser.add_argument("--timeout", type=int, default=10, help="seconds per request (--full)")
    args = parser.parse_args(argv)

    if args.classify or args.unclassify:
        try:
            assert_not_foreign_root_run()
            assign = {}
            for item in args.classify or ():
                instrument, sep, asset_class = item.partition("=")
                if not sep:
                    raise ToolError(classification.CLASSIFICATION_REFUSED, f"expected ID=CLASS, got {item!r}")
                assign[instrument.strip()] = asset_class.strip()
            result = classification.apply(state_dir(), assign=assign, remove=tuple(args.unclassify or ()))
        except MvpRuntimeError as exc:
            print(f"refused ({exc.reason_code}): {exc}")
            return EXIT_BLOCKED
        print(f"classification: mapping_version {result.mapping_version}, {len(result.entries)} entries; "
              "the next holdings_refresh fire uses it")
        return EXIT_OK

    if args.unclassified:
        return _unclassified()

    if args.cash_flow_cutover:
        from runtime.mvp_runtime.holdings import cash_flows
        try:
            assert_not_foreign_root_run()
            state = cash_flows.set_cutover(state_dir(), at=args.cash_flow_cutover, requested_by=args.by or "thomas",
                                           reason=args.reason, migrate=args.migrate,
                                           shadow_started_at=args.shadow_started)
        except MvpRuntimeError as exc:
            print(f"refused ({exc.reason_code}): {exc}")
            return EXIT_BLOCKED
        print(f"cash-flow cutover: {state['cutover_at']} (shadow started {state.get('shadow_started_at')})")
        return EXIT_OK

    if args.flows or args.resolve or args.semantics_epoch:
        return _exceptions(args)

    if args.reset_peak:
        try:
            assert_not_foreign_root_run()
        except MvpRuntimeError as exc:
            print(f"refused ({exc.reason_code}): {exc}")
            return EXIT_BLOCKED
        try:
            removed = combined.reset_peak(state_dir(), reason=args.reason or "", requested_by=args.by or "thomas")
        except MvpRuntimeError as exc:
            print(f"refused ({exc.reason_code}): {exc}")
            return EXIT_BLOCKED
        print("reset: " + (", ".join(removed) if removed else "nothing to reset")
              + " — the next complete holdings_refresh fire starts a new peak")
        return EXIT_OK

    if args.full:
        snapshot, reason_code = read_holdings(timeout_seconds=args.timeout)
        print(render_full(snapshot, reason_code=reason_code))
        if snapshot is not None:
            print(LIVE_READ_NOTICE)
        if snapshot is None and reason_code != "NOT_CONFIGURED":
            return EXIT_BLOCKED
        return EXIT_OK

    if args.local:
        try:
            print(load_local_view(now=timeutil.utc_now_iso()))
        except ToolError as exc:
            print(f"holdings    : local board unreadable ({exc.reason_code})")
            return EXIT_BLOCKED
        return EXIT_OK

    try:
        text, data = load_holdings_view(now=timeutil.utc_now_iso())
    except ToolError as exc:
        print(f"holdings    : snapshot unreadable ({exc.reason_code})")
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(text)
    return EXIT_OK


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _exceptions(args: argparse.Namespace) -> int:
    """H6d-min: list, resolve, or start an epoch. The writes refuse a host-side root run, a missing
    terminal, ``--by`` or ``--reason`` before they read anything."""
    from runtime.mvp_runtime.holdings import cash_flows

    try:
        if args.flows:
            rows = cash_flows.flows_view(state_dir())
            print(f"h6 exceptions: {sum(r['review'] == 'OPEN' for r in rows)} open, "
                  f"{sum(r['review'] == 'CLOSED' for r in rows)} closed")
            for row in rows:
                print(f"  {row['review']:6} {row['phase']:12} {row['category']:18} {row['direction'] or '-':3} "
                      f"{row['key']}  allowed: {', '.join(row['allowed']) or 'none'}"
                      + (f"  closed as: {row['kind']}" if row["kind"] else ""))
            return EXIT_OK
        assert_not_foreign_root_run()
        if args.resolve:
            written = cash_flows.resolve(state_dir(), target=args.resolve, kind=args.kind or "",
                                         reason=args.reason or "", requested_by=args.by or "",
                                         interactive=_interactive())
            print(f"resolved: {written['resolves']} as {written['kind']} (review closed; still HELD, "
                  "not evidence, not eligible)")
            return EXIT_OK
        if not _interactive():
            raise ToolError(cash_flows.EPOCH_REFUSED, "an epoch starts at an interactive terminal")
        if not (args.by or "").strip() or not (args.reason or "").strip():
            raise ToolError(cash_flows.EPOCH_REFUSED, "an epoch needs --by and --reason")
        digest = cash_flows.reconciliation_digest()
        print(f"reconciliation fingerprint {cash_flows.reconciliation_fingerprint()}, digest {digest}")
        confirm = input("type the digest's first 8 characters to start the epoch: ")
        written = cash_flows.start_semantics_epoch(state_dir(), requested_by=args.by, reason=args.reason,
                                                   change_ref=args.change_ref or "", confirm=confirm,
                                                   interactive=True)
        print(f"toss semantics epoch {written['epoch']} started (previous: {written['previous_epoch_status']}); "
              "it starts WAITING")
        return EXIT_OK
    except MvpRuntimeError as exc:
        print(f"refused ({exc.reason_code}): {exc}")
        return EXIT_BLOCKED


def _unclassified() -> int:
    from runtime.mvp_runtime.holdings import allocation, binance_wallet

    try:
        entries = classification.load(state_dir())
    except ToolError as exc:
        print(f"classification: unreadable ({exc.reason_code})")
        return EXIT_BLOCKED
    wallet, reason = binance_wallet.read_wallet()
    if wallet is None:
        print(f"wallet      : not read ({reason})")
        return EXIT_OK if reason == "NOT_CONFIGURED" else EXIT_BLOCKED
    missing = sorted(classification.wallet_id(asset) for asset, usdt in wallet.asset_usdt.items()
                     if usdt and allocation.WALLET_CLASS_MAP.get(binance_wallet.asset_class(asset)) is None
                     and classification.wallet_id(asset) not in entries.entries)
    print(f"classification: mapping_version {entries.mapping_version}, {len(entries.entries)} entries")
    print(f"unclassified  : {len(missing)} wallet asset(s)" + (": " + ", ".join(missing) if missing else ""))
    print(f"classes       : {', '.join(sorted(classification.ASSIGNABLE_CLASSES))}")
    print("toss          : unclassified codes are on --full's board (toss:<market>:<SYMBOL>)")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
