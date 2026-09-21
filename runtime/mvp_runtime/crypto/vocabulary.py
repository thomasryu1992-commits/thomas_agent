"""Names more than one layer of the crypto lane reads, kept below all of them (crypto PR7b-2).

Each lived in `live_pnl`, the module that builds the records they label, which sits near the top of
the lane, so every lower reader imported upward (`cost`, `paper`, the order path). They carry no
logic: the live-trading opt-in's names, the labels an outcome's R is measured on, the exits that
leave through a stop, and the UTC day a record belongs to. `live_pnl` re-exports every one, so its
importers keep their import lines and read the same objects. Since crypto PR7c the paper plane's
record labels that two layers read are here too, re-exported by `paper`.
"""

from __future__ import annotations

from .. import timeutil
from ..safety_gate import FILESYSTEM_WRITE, NETWORK_ACCESS

# THE live-trading switch, and the only one: `MVP_LIVE_TRADING=real` in the process
# environment (Thomas, 2026-07-28). It used to ALSO require a per-machine grant record minted
# by the since-removed scripts/activate_safety_flag.py; Thomas removed that requirement because the
# deployment already places these vars under operator-only control and the grant's expiry could
# trap an open position — an expired grant closed the gate on the CLOSE path too. Under the
# grant this was one switch across two mechanisms; now it is one switch, full stop.
#
# The provider id and flag pair survive the removal. They are what `assert_authorization`
# re-checks at every egress and what each capable class declares, so they still keep the
# capability from being half-enabled — network_access to reach the venue, filesystem_write to
# record what happened, never one without the other. What changed is what opens the gate, not
# what the gate covers.
LIVE_TRADING_ENV = "MVP_LIVE_TRADING"
REAL_LIVE_TRADING = "real"
LIVE_TRADING_PROVIDER_ID = "live_trading"
LIVE_TRADING_FLAGS = (NETWORK_ACCESS, FILESYSTEM_WRITE)

# How an outcome's `result_R` was measured. Recorded on every row so a consumer pooling
# populations can see it is doing so — and, since 2026-07-30, so it can tell a paper row that
# paid costs from one that did not.
#
# - `intent`             intended fills, NO costs. Every paper row written before 2026-07-30.
# - `intent_net_of_costs` intended fills, fees + slippage charged (`cost.apply_cost_model`).
#                        Paper rows written since. This is the basis the factory backtest has
#                        always used, so a paper expectancy is finally comparable to the
#                        backtest expectancy that scored the same strategy.
# - `filled`             actual venue fills, slippage included, fees still excluded (`live_leg`).
#
# A window spanning the 2026-07-30 boundary mixes `intent` and `intent_net_of_costs`, and
# therefore UNDER-states its losses by the legacy rows' unpaid costs. There is no backfill: the
# stored outcome keeps `entry_price`/`exit_price`/`direction` but not `risk`, and the cost model
# is denominated in risk-per-unit — so an old row cannot be re-priced, only labelled.
R_BASIS_INTENT = "intent"
R_BASIS_INTENT_NET = "intent_net_of_costs"
R_BASIS_FILLED = "filled"

# The bases that already carry costs. `filled` is deliberately absent: live R includes venue
# slippage but not fees, so it is neither of the two paper bases and must not be read as one.
R_BASES_NET_OF_COSTS = frozenset({R_BASIS_INTENT_NET})

# The close reasons that leave through a triggered STOP — the leg whose market order races the
# move that fired it, which is not the microstructure any other exit has. `cost.apply_cost_model`
# prices this leg with its own slippage rate, and `build_live_outcome_record` measures the
# realized figure against the trigger on exactly these rows.
#
# It lives here, beside the R-basis labels, and not beside `cost.MAKER_EXIT_REASONS`, where
# symmetry says it belongs, because it labels outcome rows: `live_settlement` builds the rows and
# measures stop slippage on exactly these, and the cost model prices them. Kept with the other row labels,
# neither reader loads the other to name a stop. Until crypto PR7b-2 it and the labels lived in
# `live_pnl`, which put the outcome row's vocabulary above every layer that reads it. A new close
# reason that exits at market has to decide whether it is stop-shaped (a trigger chased through a
# moving book) or merely immediate, and membership here is that decision.
STOP_EXIT_REASONS = frozenset({"stop_loss"})


def utc_day(stamp: str | None = None) -> str:
    """The UTC calendar day a timestamp belongs to. The breaker resets at UTC midnight."""
    return (stamp or timeutil.utc_now_iso())[:10]


# --- the paper plane's record labels (crypto PR7c) -----------------------------------------------------
# Each is read by two layers: the trade-plan maths (`trade_plan`, strategy) stamps or tests it and the
# paper kernel (`paper`, decision) reads it back, and the forward book (strategy) reads the entry status
# and the occupying statuses. Kept here, below all of them; `paper` re-exports every one.

PAPER_PROVENANCE = "mvp_paper_kernel"

# The trading venue a position was opened on. One value today (the C2 collector is
# Binance futures), carried explicitly so a later exchange adapter is a new context
# rather than a second migration of every stored position.
DEFAULT_VENUE = "binance_futures"

# The router's entry status (source S7). The rest of the router's statuses and its rule code stay in
# `paper`, the only module that reads them.
STATUS_ENTRY_CANDIDATE = "ENTRY_CANDIDATE"

OCCUPYING_STATUSES = frozenset({"PAPER_ACTIVE", "WARNING", "PROBATION"})
