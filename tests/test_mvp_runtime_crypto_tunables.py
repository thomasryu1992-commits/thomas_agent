"""The decision-constant index, and the one test that stops it rotting.

`REMAINING_WORK.md` §G1: 656 upper-case constants across the crypto package and no module owns
them — 602 four days earlier, so the population grows unowned. An index that only *described*
today's constants would be another such artifact by next week.

So the load-bearing test here is not "the values are right" — `tunables` imports them, so it
cannot be wrong about a value. It is **coverage**: a decision-shaped constant appearing in a
swept module must be indexed with its provenance, or explicitly named as mechanics with a
reason. Adding a new threshold to the money path without recording where the number came from
fails the suite.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from runtime.mvp_runtime.crypto import tunables

CRYPTO = pathlib.Path(__file__).resolve().parents[1] / "runtime" / "mvp_runtime" / "crypto"

# The modules this first slice covers: the cost premise, the breakers, the promotion door, the
# scorer's thresholds, the mint space and the live caps — §G1's own list. The rest of the package
# is deliberately NOT swept yet, and naming the boundary here is what keeps that a decision
# rather than an oversight.
# The first slice named twelve modules here and said the rest was deliberately not swept — the
# list WAS the decision. The second slice (2026-08-08) closed the gap, so the boundary is now the
# package and a hand-maintained list would only be a way to forget a module. It is derived, and
# `UNSWEPT` is where an exclusion would have to be argued for rather than achieved by omission.
#
# Adding the remaining modules cost nothing to argue: 21 of the 31 had **zero** decision-shaped
# constants and most had no module-level numeric constant at all.
UNSWEPT: dict[str, str] = {}

SWEPT_MODULES = tuple(sorted(
    p.name for p in CRYPTO.glob("*.py")
    if p.name not in {"__init__.py", "tunables.py"} and p.name not in UNSWEPT
))

# Names shaped like a decision — a threshold, a rate, a window, a multiple.
_DECISION_SHAPED = re.compile(
    r"MAX_|MIN_|_THRESHOLD|_FLOOR|_CAP\b|_BPS|_FEE|_ATR|_RATIO|_FRACTION|_PCT|_DAYS|_R$"
    r"|CONFIDENCE|ALPHA|_TRADES|CRITICAL|HEALTHY"
)
# ...and the ones that only look like it. Every entry needs a reason, so this cannot quietly
# become the place difficult constants go to avoid being explained.
MECHANICS: dict[str, str] = {
    "MIN_CANDLE_COUNT": "data hygiene: how much history makes a snapshot judgeable at all",
    "MAX_ALLOWED_CANDLE_GAP_MULTIPLE": "data hygiene: gap detection, not a trading choice",
    "_MAX_ATTEMPTS_PER_SPEC": "private retry bound on spec generation; no money touches it",
    "NOTIONAL_TOLERANCE_FRACTION": "float comparison tolerance against the venue's own rounding",
    "DERIVATIVE_HISTORY_DAYS": "how much history to fetch; bounded by the venue, not chosen",
    "MAX_CANDLES": "fetch ceiling — a page budget, and the depth premise is FACTORY_DEPTH_DAYS",
    "DERIVATIVE_MAX_PAGES": "page budget bounding one collection's egress",
    "FUNDING_MAX_PAGES": "page budget bounding one collection's egress",
    "POSITIONING_MAX_PAGES": "page budget bounding one collection's egress",
    # Borderline, and recorded as such: it bounds how stale a price may be when verifying a
    # HAND-DECLARED notional, so it constrains an operator input rather than an order. If it
    # ever gates an order path it belongs in the index instead.
    "REFERENCE_PRICE_MAX_AGE_SECONDS": "staleness tolerance on a notional verification read",

    # --- second slice, 2026-08-08 -------------------------------------------------------------
    # Estimator warm-up floors: how many observations before a series will answer at all. Below
    # one the column is None and no mined condition on it can fire, which is the honest state
    # rather than a level estimated from three points. They decide nothing about a trade — the
    # same class as MIN_CANDLE_COUNT above.
    "FUNDING_Z_MIN_PERIODS": "estimator warm-up: observations before the funding z will answer",
    "OI_Z_MIN_PERIODS": "estimator warm-up: observations before the OI z will answer",
    "LIQ_MA_MIN_PERIODS": "estimator warm-up: observations before the liquidation MA will answer",
    "TAKER_FLOW_MA_MIN_PERIODS": "estimator warm-up: observations before the taker-flow MA answers",
    "TRADE_SIZE_Z_MIN_PERIODS": "estimator warm-up: observations before the trade-size z answers",
    "PREMIUM_Z_MIN_PERIODS": "estimator warm-up: observations before the premium z will answer",
    "POSITIONING_Z_MIN_PERIODS": "estimator warm-up: observations before the positioning z answers",
    "REFERENCE_CORR_MIN_PERIODS": "estimator warm-up: observations before the correlation answers",
    # Borderline, and recorded as such like the one above it. Its comment argues the trade-off
    # (20 rather than 100, so a live sizing decision is not blocked for 100 bars) — but what it
    # sets is still only when the reference exists, and below it the multiplier is an unscaled
    # 1.0 rather than a guess. If it ever selects BETWEEN references it belongs in the index.
    "ATR_REFERENCE_MIN_PERIODS": "estimator warm-up: observations before the ATR reference answers",

    # Collection budgets — how much one call or one refresh asks for. Bounded by the vendor's
    # retention and its page cap, not chosen against anything this runtime trades.
    "USER_TRADES_PAGE_LIMIT": "venue cap per /fapi/v1/userTrades call",
    "REFRESH_DAYS": "collection budget: how much history one OI refresh re-asks for",
    "SEED_DAYS": "collection budget: a seed takes whatever the vendor's retention wall is",

    # Reporting and display shapes. They change what a reader is shown, never what is traded.
    "PNL_WINDOW_DAYS": "which realized-P&L windows a snapshot reports; one query, three buckets",
    "GRANT_EXPIRY_WARNING_DAYS": "when the board starts showing a grant instead of hiding it",
    "MIN_INTERVAL_SAMPLE": "below it the spread cannot be estimated, so no interval is produced",

    # Output caps bounding one run's volume. Neither gates money: a counterfactual is
    # observational by construction, and a suggestion is evidence for a human.
    "MAX_OPEN_COUNTERFACTUALS": "bounds the shadow book so a stuck gate cannot grow it forever",
    "MAX_SUGGESTIONS_PER_RUN": "output cap on a review aid; nothing acts on a suggestion",
}


def _numeric_constants(path: pathlib.Path) -> list[str]:
    names: list[str] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        for target in targets:
            name = target.id
            if not (name.isupper() and len(name) >= 4):
                continue
            value = node.value
            numeric = (
                (isinstance(value, ast.Constant)
                 and isinstance(value.value, (int, float)) and not isinstance(value.value, bool))
                or (isinstance(value, ast.UnaryOp) and isinstance(value.operand, ast.Constant))
                or (isinstance(value, ast.Tuple) and value.elts
                    and all(isinstance(e, ast.Constant) and isinstance(e.value, (int, float))
                            for e in value.elts))
            )
            if numeric:
                names.append(name)
    return names


def test_every_decision_shaped_constant_in_a_swept_module_is_owned():
    """The test with teeth. A new threshold on the money path must record where it came from.

    §G1's incident is the argument: `MAX_DAYS_TO_LIFECYCLE_WINDOW = 14` sat in `pool.py` with a
    premise the cost model had already killed, and the board read 0 promotable against 900
    candidates until someone went looking. The value was checkable the whole time; the premise
    was not written anywhere a reader would meet it.
    """
    indexed = tunables.names()
    unowned = []
    for module in SWEPT_MODULES:
        for name in _numeric_constants(CRYPTO / module):
            if not _DECISION_SHAPED.search(name):
                continue
            if name in indexed or name in MECHANICS:
                continue
            unowned.append(f"{module}:{name}")
    assert not unowned, (
        "decision-shaped constants with no recorded provenance: " + ", ".join(sorted(unowned))
        + " — add a Tunable to crypto/tunables.py, or name it in MECHANICS with a reason"
    )


def test_the_sweep_covers_every_crypto_module():
    """The second slice's real deliverable: the boundary is the package, and stays there.

    While the sweep was a subset, a decision-shaped constant could avoid the coverage test simply
    by living in an unswept module. Deriving `SWEPT_MODULES` removes that route — a new module
    arrives swept — and this test is what stops the derivation being quietly narrowed later: an
    exclusion has to be written into `UNSWEPT` with a reason, where a reader will meet it.
    """
    present = {p.name for p in CRYPTO.glob("*.py")} - {"__init__.py", "tunables.py"}
    assert set(SWEPT_MODULES) == present - set(UNSWEPT), "the sweep no longer derives from the package"
    assert not set(UNSWEPT) - present, f"UNSWEPT names modules that do not exist: {sorted(set(UNSWEPT) - present)}"
    for module, reason in UNSWEPT.items():
        assert reason.strip(), f"{module} is excluded from the sweep with no reason given"


def test_the_index_points_at_modules_that_exist_and_own_the_name():
    """`owner` is a path a reader follows to the full argument. A wrong one sends them nowhere."""
    for entry in tunables.TUNABLES:
        owner = CRYPTO / pathlib.Path(entry.owner).name
        assert owner.is_file(), f"{entry.name}: owner {entry.owner} does not exist"
        assert entry.name in _numeric_constants(owner), (
            f"{entry.name} is indexed as living in {entry.owner} and does not"
        )


def test_every_entry_declares_a_known_provenance_and_says_what_reopens_it():
    for entry in tunables.TUNABLES:
        assert entry.provenance in tunables.PROVENANCE, f"{entry.name}: {entry.provenance}"
        assert entry.premise.strip(), f"{entry.name} has no premise"
        assert entry.reopens_when.strip(), f"{entry.name} does not say what would reopen it"


def test_no_constant_is_indexed_twice():
    names = [entry.name for entry in tunables.TUNABLES]
    assert len(names) == len(set(names))


def test_the_numbers_that_stop_the_money_are_the_ones_nobody_here_has_decided():
    """Not a rule — a finding, pinned so it cannot quietly stop being true either way.

    The cost model, the ladder and the promotion door have each been re-measured against this
    runtime's own record. The four breakers that halt trading have not: they are the predecessor
    system's `config/settings.py` values, carried across and never examined here.
    """
    inherited = {entry.name for entry in tunables.by_provenance(tunables.INHERITED)}
    breakers = {"DAILY_MAX_LOSS_R", "WEEKLY_MAX_LOSS_R",
                "MAX_CONSECUTIVE_LOSSES", "MAX_DRAWDOWN_PCT"}
    assert breakers <= inherited, (
        "a breaker default stopped being INHERITED — if it was re-decided here, say so in its "
        "entry and in REMAINING_WORK §G1, because that is the open item closing"
    )


@pytest.mark.parametrize("provenance", sorted(tunables.PROVENANCE))
def test_each_provenance_is_actually_used(provenance):
    """A vocabulary with unused terms invites a wrong one to be picked for the nearest fit."""
    assert tunables.by_provenance(provenance), f"no constant is classified {provenance}"
