"""CLI for the LLM strategy-family proposer — deliberately manual, never scheduled.

    python -m runtime.mvp_runtime.crypto.proposer_cli --focus liquidation
    python -m runtime.mvp_runtime.crypto.proposer_cli --symbol ETHUSDT --timeframe 4h
    python -m runtime.mvp_runtime.crypto.proposer_cli --json

Thomas runs this when he wants proposals, which is why it is a command and not a
schedule (explicit decision 2026-07-24): proposals need reading, and a schedule would
accumulate them faster than anyone reviews them. The output is a review sheet — the
record installs nothing, and adding a family to ``factory.TEMPLATES`` stays a code
change in Thomas's PR.

Two gated reads, both through the existing chokepoints: market data for the candles the
proposals are scored on (``MVP_MARKET_DATA``), and the validator provider for the model
call itself (``MVP_VALIDATOR_PROVIDER`` — the cheap quota, reused rather than given a
new grant). Without either, the deterministic mock paths run and the command still
works end to end.
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import timeutil
from ..errors import MvpRuntimeError
from ..providers import select_validator_provider
from ..store import LedgerStore
from . import factory, proposer
from .market_data import (
    DEFAULT_CANDLES,
    MAX_CANDLES,
    collect_market_data,
    select_market_data_collector,
)

EXIT_OK = 0
EXIT_ERROR = 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Propose new strategy families with a model; judge them deterministically.",
    )
    parser.add_argument("--symbol", default="BTCUSDT", help="symbol to score proposals on")
    parser.add_argument("--timeframe", default="1h", help="timeframe to score proposals on")
    parser.add_argument("--focus", default=None,
                        help="steer the proposals (e.g. 'liquidation', 'funding')")
    parser.add_argument("--count", type=int, default=proposer.MAX_PROPOSALS_PER_RUN,
                        help=f"proposals to ask for (max {proposer.MAX_PROPOSALS_PER_RUN})")
    parser.add_argument("--candles", type=int, default=500,
                        help=f"backtest window in candles (max {MAX_CANDLES})")
    parser.add_argument("--json", action="store_true", help="emit the full record as JSON")
    parser.add_argument("--no-ledger", action="store_true",
                        help="do not append the proposal record to the run ledger")
    args = parser.parse_args(argv)

    now = timeutil.utc_now_iso()
    try:
        collector = select_market_data_collector(now=now)
        snapshot, _ = collect_market_data(
            args.symbol, args.timeframe, collector=collector, now=now,
            limit=max(1, min(int(args.candles), MAX_CANDLES)) if args.candles else DEFAULT_CANDLES,
        )
    except MvpRuntimeError as exc:
        sys.stderr.write(f"market data unavailable: {exc}\n")
        return EXIT_ERROR

    # The validator provider (typically groq) or the deterministic mock. Reused, not
    # granted anew: a proposal is an INTERNAL_ANALYSIS-tier read, the same tier the R7.2
    # triage call already runs at on this provider.
    provider = select_validator_provider(now=now) or proposer.MockProposerProvider()

    record = proposer.propose_strategy_families(
        snapshot,
        provider=provider,
        now=now,
        existing_families=[t.family for t in factory.TEMPLATES],
        focus=args.focus,
        count=max(1, min(int(args.count), proposer.MAX_PROPOSALS_PER_RUN)),
    )

    if not args.no_ledger:
        try:
            LedgerStore.default().append_records(record["proposal_id"], {"crypto_strategy_proposal": record})
        except MvpRuntimeError as exc:
            # Reporting honesty (QA wave 7): say the persist failed rather than printing
            # a report that implies it was kept.
            record["persist_error"] = str(exc)

    if args.json:
        sys.stdout.write(json.dumps(record, ensure_ascii=False, indent=1) + "\n")
    else:
        sys.stdout.write(proposer.format_proposal_report(record) + "\n")
        if record.get("persist_error"):
            sys.stdout.write(f"LEDGER: NOT recorded ({record['persist_error']})\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
