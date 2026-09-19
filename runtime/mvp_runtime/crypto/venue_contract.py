"""PR4a — the venue contract sentinel: what this runtime assumes about the exchange, asked of the
exchange itself, on a schedule (Thomas decisions 43-46, 2026-09-19).

**Why.** Every check this repository ran before an order pointed at its own model of the venue:
closed schemas, filters, the request builder's rules. On 2026-08-02 all of them passed while the
venue refused both protective stops, because the one fact that had changed — conditional order
types had moved off ``/fapi/v1/order`` to the Algo API — lives only at the venue. A unit test cannot
see drift in a contract it never calls. This module calls it.

**What it can see without creating an order.** Three verbs: a public GET, a signed GET, and
``POST /fapi/v1/order/test`` — the order API's own validator, which creates nothing.
``exchangeInfo`` alone would not have caught 2026-08-02: on 2026-09-19 it still lists
``STOP_MARKET`` and ``TAKE_PROFIT_MARKET`` for every traded symbol, although the order API refuses
both with -4120. The -4120 is the observable: the 2026-08-03 diagnostic request, sent to the
validator, must still be refused with it. What none of the three can show is written into every
record (:data:`NOT_VERIFIED`) so a PASS never reads as more than it is: an algo order's placement
(the Algo API has no validator; the signed testnet cycle, PR1d, is that evidence), the codes only a
real order or cancel produces, and account state, which the validator does not judge.

**Judged and observed.** A check may fail the contract only where what it expects has been measured
at this venue (:data:`JUDGED_CHECKS`). The others are hypotheses the runtime's reads rest on — the
answer to an unknown id, a take-profit LIMIT beyond the price band while flat — and are recorded,
never judged (:data:`OBSERVED_CHECKS`), until the host's own answers are on record.

**Two files**, as :mod:`account_store` keeps them and for its reason: the last DECIDED verification
(PASS or FAIL) and the last ATTEMPT. A run that could not ask moves only the attempt, so a venue
hiccup never erases a good verification; a run the venue answered with a violation writes FAIL at
once (decision 44). A PASS stands for :data:`MAX_AGE_SECONDS`.

**Not the API breaker's, and not stage evidence.** The breaker counts the money path's signed calls
(decision 27), and the sentinel is not the money path: it asks the raw adapter, and its failures are
its own record. And ``/order/test`` is never evidence for an execution-stage transition (decision 3).

4a records; nothing refuses an entry on the record yet (4b). The reader half of this module
imports nothing heavier than the state and integrity helpers, so a door can read it; the checks
import the venue modules where they run.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import timeutil
from ..errors import MvpRuntimeError, ToolError
from ..filelock import locked
from ..paths import repo_root as _repo_root
from ..schema_cache import validate_against_schema
from .state import VENUE_MAINNET, venue_state_dir

CONTRACT_VERSION = "binance_futures_contract.v1"
RECORD_TYPE = "venue_contract_verification.v0.1"
SCHEMA_FILE = "venue_contract_verification.v0.1.schema.json"
CONTRACT_FILENAME = "venue_contract.json"
REFRESH_MARK_FILENAME = "venue_contract_refresh.json"

# Decision 44: hourly, a PASS good for six hours. 55 minutes rather than 60 because the fire that asks
# is the 15-minute pipeline: at 60 the hour-mark fire lands a few seconds short, and the ask slips to
# the next one — an effective 75. Six hours is five failed asks in a row before a PASS stops being one.
REFRESH_AFTER_SECONDS = 55 * 60
MAX_AGE_SECONDS = 6 * 60 * 60
# A verification dated this far past the clock is not one to trust either: clocks disagree by
# seconds, not minutes.
FUTURE_SKEW_SECONDS = 5 * 60

# Per call, and per run. The run rides the risk lane's pipeline fire, after its cycles: about fifteen
# calls at the venue's usual latency is a few seconds, and the budget bounds the unusual case — a
# venue slow on every call must not add a minute to the fire that manages positions.
CALL_TIMEOUT_SECONDS = 4
RUN_BUDGET_SECONDS = 15.0
# What the last judged check (the two resting-order reads) keeps for itself: the observed checks stop
# starting calls once less than this is left, so a decision is never hostage to a hypothesis.
RESERVED_SECONDS = 2.0 * CALL_TIMEOUT_SECONDS

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_UNVERIFIED = "UNVERIFIED"
# An observed check's result: recorded, never judged.
RESULT_OBSERVED = "OBSERVED"

VENUE_CONTRACT_UNREADABLE = "VENUE_CONTRACT_UNREADABLE"
VENUE_CONTRACT_TAMPERED = "VENUE_CONTRACT_TAMPERED"
VENUE_CONTRACT_INVALID = "VENUE_CONTRACT_INVALID"

CHECK_EXCHANGE_INFO = "exchange_info"
CHECK_CONDITIONAL_REFUSED = "conditional_type_refused_on_order_api"
CHECK_LEVERAGE = "configured_leverage"
CHECK_POSITION_MODE = "position_mode"
CHECK_NOTHING_RESTING = "nothing_left_resting"
CHECK_ENTRY_TEST = "order_test_market_entry"
CHECK_TARGET_TEST = "order_test_target_limit"
CHECK_ORDER_QUERY = "order_query_unknown_id"
CHECK_ALGO_QUERY = "algo_query_unknown_id"

# What may fail the contract, each because its expectation was measured here:
# - exchange_info: the traded symbols' listing, as read 2026-09-19;
# - the -4120: the 2026-08-03 diagnostic request's own answer;
# - the leverage: every traded symbol at 5x, read 2026-09-02, and decision 45's bound;
# - the position mode: not a venue behaviour but the runtime's requirement — no request carries a
#   `positionSide` and the book nets per symbol (`live_position`), so a hedge-mode account refuses
#   every entry (-4061) and breaks the reconcile's premise;
# - nothing left resting: by construction — the sentinel creates nothing, and this measures it.
JUDGED_CHECKS = (CHECK_EXCHANGE_INFO, CHECK_CONDITIONAL_REFUSED, CHECK_LEVERAGE, CHECK_POSITION_MODE,
                 CHECK_NOTHING_RESTING)
# What is recorded for 4b to judge once the host's answers are known. The entry and target requests
# are the runtime's own, through its own builder; the two queries ask for an id no order carries.
OBSERVED_CHECKS = (CHECK_ENTRY_TEST, CHECK_TARGET_TEST, CHECK_ORDER_QUERY, CHECK_ALGO_QUERY)
CHECK_IDS = (*JUDGED_CHECKS, *OBSERVED_CHECKS)

NOT_VERIFIED = (
    "algo order placement: the Algo API has no validator; the signed testnet cycle (PR1d) is that "
    "evidence",
    "-4116 duplicate id, -2011 unknown order on cancel, unknown-outcome codes: only a real order or "
    "cancel produces them",
    "account state: /order/test validates the request, not the account (margin, reduceOnly against a "
    "position, open-order counts)",
)

# The outcome of one attempt, on the refresh mark.
OUTCOME_STARTED = "started"
OUTCOME_DECIDED = "decided"
OUTCOME_INCOMPLETE = "incomplete"
OUTCOME_NOT_OPTED_IN = "live_trading_not_opted_in"
OUTCOME_NO_SCOPE = "no_registered_symbols"
OUTCOME_ERROR = "error"

# Every client id the sentinel sends starts with this. The runtime's own are `TAI_<SYMBOL>_<LEG>_…`
# and no symbol is `VC`, so a sentinel id can neither name a real order nor be mistaken for one — and
# the nothing-left-resting check can tell the sentinel's leavings from the runtime's legs.
SENTINEL_ID_PREFIX = "TAI_VC_"

# The request that measured the migration, 2026-08-03 (`scripts/diagnose_bracket_leg.py`): the
# protective stop as the order API took it before conditional types moved to the Algo API. FROZEN,
# never rebuilt: today's builder emits the Algo shape (`algoType`, `triggerPrice`, `clientAlgoId`),
# which the order API may refuse as unknown parameters before it judges the type — an answer that
# would read as "the venue refused it" and prove nothing about where conditional orders live.
LEGACY_CONDITIONAL_PROBE_KEYS = ("symbol", "side", "type", "stopPrice", "closePosition", "workingType",
                                 "newClientOrderId")
# "Order type not supported for this endpoint. Please use the Algo Order API endpoints instead."
VENUE_CONDITIONAL_MOVED = -4120


def legacy_conditional_probe(symbol: str, *, stop_price: float, client_id: str) -> dict[str, Any]:
    """The 2026-08-03 diagnostic stop for ``symbol``: a SELL ``STOP_MARKET`` Close-All below the
    market, in the order API's pre-migration spelling. No ``algoType``, so the validator's own
    routing (`live_execution.is_algo_request`) sends it to ``/order/test``."""
    return {"symbol": symbol, "side": "SELL", "type": "STOP_MARKET", "stopPrice": stop_price,
            "closePosition": "true", "workingType": "MARK_PRICE", "newClientOrderId": client_id}


# --- files -------------------------------------------------------------------------------------

def contract_path(root: Path | None = None) -> Path:
    return venue_state_dir(root, venue=VENUE_MAINNET) / CONTRACT_FILENAME


def refresh_mark_path(root: Path | None = None) -> Path:
    return venue_state_dir(root, venue=VENUE_MAINNET) / REFRESH_MARK_FILENAME


def _schema_path(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / "schemas" / SCHEMA_FILE


def _validate(record: Mapping[str, Any]) -> None:
    # The schema ships with the code, so it is read from the code's repo — never from a state root a
    # caller redirected (the budget's reader does the same).
    try:
        validate_against_schema(dict(record), _schema_path(), "venue contract verification")
    except RuntimeSchemaError as exc:
        raise ToolError(VENUE_CONTRACT_INVALID, f"venue contract record is not schema-valid: {exc}") from exc


def _write_json(path: Path, body: Mapping[str, Any], *, code: str, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code=code, label=label):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


def _age_seconds(stamp: Any, now: str) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return (timeutil.parse_iso(now) - timeutil.parse_iso(stamp)).total_seconds()
    except (ValueError, TypeError):
        return None


def read_refresh_mark(root: Path | None = None) -> dict[str, Any] | None:
    """The last attempt, or None for absent AND for damaged: the mark only says when to ask again,
    and a mark nobody can read must not be able to stop the asking."""
    path = refresh_mark_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def is_due(mark: Mapping[str, Any] | None, now: str) -> bool:
    """Whether to ask the venue again, given when it was last ASKED — not last answered. Never asked,
    or a mark that cannot be read, is due: being wrong costs one run."""
    attempted = mark.get("attempted_at") if isinstance(mark, Mapping) else None
    age = _age_seconds(attempted, now)
    return age is None or age < 0 or age >= REFRESH_AFTER_SECONDS


def build_record(*, status: str, checks: Sequence[Mapping[str, Any]], symbols: Sequence[str],
                 now: str) -> dict[str, Any]:
    """One decided verification, self-hashed and schema-valid. Only PASS or FAIL is ever recorded:
    a run that could not decide moves the attempt mark alone."""
    if status not in (STATUS_PASS, STATUS_FAIL):
        raise ToolError(VENUE_CONTRACT_INVALID, f"only a decided verification is recorded, not {status!r}")
    body: dict[str, Any] = {
        "record_type": RECORD_TYPE,
        "contract_version": CONTRACT_VERSION,
        "venue": VENUE_MAINNET,
        "status": status,
        "verified_at": now,
        "symbols": list(symbols),
        "failed_checks": [str(c["check"]) for c in checks
                          if c.get("check") in JUDGED_CHECKS and c.get("result") == STATUS_FAIL],
        "checks": [dict(c) for c in checks],
        "not_verified": list(NOT_VERIFIED),
    }
    body["record_sha256"] = integrity.sha256_record(body)
    _validate(body)
    return body


def read_verification(root: Path | None = None) -> dict[str, Any] | None:
    """The last decided verification, VERIFIED — or None when none was ever recorded.

    Anything unparseable, failing its self-hash or not schema-valid raises: the record is what a
    door will read to let an entry through (4b), and one that cannot prove itself must not."""
    path = contract_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ToolError(VENUE_CONTRACT_UNREADABLE, f"venue contract record is unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise ToolError(VENUE_CONTRACT_UNREADABLE, "venue contract record is not a JSON object")
    stored = data.get("record_sha256")
    body = {key: value for key, value in data.items() if key != "record_sha256"}
    try:
        recomputed = integrity.sha256_record(body)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ToolError(VENUE_CONTRACT_TAMPERED, f"venue contract record cannot be hashed: {exc}") from None
    if not isinstance(stored, str) or stored != recomputed:
        raise ToolError(VENUE_CONTRACT_TAMPERED, "venue contract record fails its self-hash")
    _validate(data)
    return data


def verification_status(root: Path | None = None, *, now: str) -> dict[str, Any]:
    """What the last decided verification says at ``now``. Raises on a record that cannot prove
    itself (see :func:`read_verification`); the board names that, a door refuses on it.

    ``usable`` is the whole answer a door needs: PASS, this code's contract version, and no older
    than :data:`MAX_AGE_SECONDS` (nor dated in the future)."""
    record = read_verification(root)
    if record is None:
        return {"recorded": False, "status": None, "contract_version": None, "verified_at": None,
                "age_seconds": None, "stale": True, "version_current": False, "usable": False,
                "failed_checks": []}
    age = _age_seconds(record.get("verified_at"), now)
    stale = age is None or age < -FUTURE_SKEW_SECONDS or age > MAX_AGE_SECONDS
    version_current = record.get("contract_version") == CONTRACT_VERSION
    return {
        "recorded": True,
        "status": record.get("status"),
        "contract_version": record.get("contract_version"),
        "verified_at": record.get("verified_at"),
        "age_seconds": age,
        "stale": stale,
        "version_current": version_current,
        "usable": record.get("status") == STATUS_PASS and not stale and version_current,
        "failed_checks": list(record.get("failed_checks") or []),
    }


# --- the checks --------------------------------------------------------------------------------

def _check(check_id: str, result: str, *, expected: str, observed: Mapping[str, Any] | None = None,
           detail: str = "") -> dict[str, Any]:
    return {"check": check_id, "judged": check_id in JUDGED_CHECKS, "result": result,
            "expected": expected, "observed": dict(observed or {}), "detail": detail}


def judge(checks: Sequence[Mapping[str, Any]]) -> str:
    """FAIL when a judged check failed; PASS when every judged check ran and passed; otherwise
    UNVERIFIED. Pure. Judged-ness is the check's id, never a flag a record carries."""
    results = {str(c.get("check")): c.get("result") for c in checks if c.get("check") in JUDGED_CHECKS}
    if any(result == STATUS_FAIL for result in results.values()):
        return STATUS_FAIL
    if set(results) == set(JUDGED_CHECKS) and all(result == STATUS_PASS for result in results.values()):
        return STATUS_PASS
    return STATUS_UNVERIFIED


def _failure(exc: BaseException) -> dict[str, Any]:
    """Why a call could not be answered, in the fields a reader needs and nothing more — never the
    message of a transport error, which is generic by design (the signed URL never leaves)."""
    data = getattr(exc, "data", None)
    data = data if isinstance(data, Mapping) else {}
    failure: dict[str, Any] = {"error": str(getattr(exc, "reason_code", type(exc).__name__))}
    if isinstance(data.get("venue_code"), int):
        failure["venue_code"] = data["venue_code"]
    if isinstance(data.get("http_status"), int):
        failure["http_status"] = data["http_status"]
    return failure


def _percent_price(row: Mapping[str, Any]) -> dict[str, float] | None:
    for entry in row.get("filters") or []:
        if isinstance(entry, Mapping) and entry.get("filterType") == "PERCENT_PRICE":
            try:
                return {"up": float(entry["multiplierUp"]), "down": float(entry["multiplierDown"])}
            except (KeyError, TypeError, ValueError):
                return None
    return None


def check_exchange_info(payload: Any, symbols: Sequence[str], *,
                        failure: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Every traded symbol is listed, TRADING, a USDT-margined PERPETUAL whose filters this runtime
    can read, and takes MARKET and LIMIT orders with GTC. Records each symbol's filters and its
    ``PERCENT_PRICE`` band, which the filter reader does not use.

    A payload the collector could not fetch or parse is UNVERIFIED; one that parses and does not say
    these things is FAIL — the venue answered, in a shape this runtime cannot trade on."""
    from .live_filters import parse_symbol_filters

    expected = ("listed, TRADING, PERPETUAL, USDT quote and margin, filters readable, "
                "MARKET and LIMIT order types, GTC")
    if failure is not None:
        return _check(CHECK_EXCHANGE_INFO, STATUS_UNVERIFIED, expected=expected, observed=failure,
                      detail="exchangeInfo could not be read")
    rows = payload.get("symbols") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return _check(CHECK_EXCHANGE_INFO, STATUS_FAIL, expected=expected,
                      detail="exchangeInfo carries no symbols list")
    by_symbol = {str(row.get("symbol")): row for row in rows if isinstance(row, Mapping)}
    facts: dict[str, Any] = {}
    problems: dict[str, list[str]] = {}
    for symbol in symbols:
        row = by_symbol.get(symbol)
        if row is None:
            problems[symbol] = ["not listed"]
            continue
        found: list[str] = []
        for key, want in (("status", "TRADING"), ("contractType", "PERPETUAL"),
                          ("quoteAsset", "USDT"), ("marginAsset", "USDT")):
            if row.get(key) != want:
                found.append(f"{key} {row.get(key)!r}")
        filters, reason = parse_symbol_filters(payload, symbol)
        if filters is None:
            found.append(f"filters: {reason}")
        order_types = [str(t) for t in row.get("orderTypes") or [] if isinstance(t, str)]
        missing = sorted({"MARKET", "LIMIT"} - set(order_types))
        if missing:
            found.append(f"no order type {', '.join(missing)}")
        if "GTC" not in (row.get("timeInForce") or []):
            found.append("no GTC")
        facts[symbol] = {
            "order_types": order_types,
            "percent_price": _percent_price(row),
            **({"step_size": filters.step_size, "min_qty": filters.min_qty,
                "min_notional": filters.min_notional, "tick_size": filters.tick_size}
               if filters is not None else {}),
        }
        if found:
            problems[symbol] = found
    return _check(CHECK_EXCHANGE_INFO, STATUS_FAIL if problems else STATUS_PASS, expected=expected,
                  observed={"symbols": facts, "problems": problems},
                  detail="; ".join(f"{s}: {', '.join(p)}" for s, p in problems.items()))


def check_conditional_refused(answer: Mapping[str, Any] | None, *,
                              failure: Mapping[str, Any] | None = None,
                              detail: str = "") -> dict[str, Any]:
    """The 2026-08-03 diagnostic stop, sent to ``/order/test``, is still refused with -4120: the
    migration the runtime places every protective stop around is still in force.

    Accepted, or refused with another business code, is FAIL — the venue no longer answers the way
    the runtime's routing assumes. A code that means the venue could not be asked (rate limit, clock,
    key, the venue's own failure: `live_order.API_ERROR_VENUE_CODES`) is UNVERIFIED, as is a call
    that did not complete. The validator's answer carries the venue's code but not the HTTP status,
    so a status-only failure is judged by its code alone."""
    from .live_order import API_ERROR_VENUE_CODES

    expected = f"refused with {VENUE_CONDITIONAL_MOVED} (conditional types live on the Algo API)"
    if failure is not None or not isinstance(answer, Mapping):
        return _check(CHECK_CONDITIONAL_REFUSED, STATUS_UNVERIFIED, expected=expected,
                      observed=dict(failure or {}), detail=detail or "the validator could not be asked")
    observed = {"accepted": answer.get("accepted"), "code": answer.get("code"),
                "msg": str(answer.get("msg"))[:200] if answer.get("msg") is not None else None}
    if answer.get("supported") is False or answer.get("dry_run"):
        # Never reached with the frozen probe on the live adapter; named so it can never pass.
        return _check(CHECK_CONDITIONAL_REFUSED, STATUS_UNVERIFIED, expected=expected, observed=observed,
                      detail="the validator did not judge the request")
    code = answer.get("code")
    if answer.get("accepted") is True:
        return _check(CHECK_CONDITIONAL_REFUSED, STATUS_FAIL, expected=expected, observed=observed,
                      detail="the order API ACCEPTS a conditional type again: the routing this runtime "
                             "is built on no longer matches the venue")
    if code == VENUE_CONDITIONAL_MOVED:
        return _check(CHECK_CONDITIONAL_REFUSED, STATUS_PASS, expected=expected, observed=observed)
    if isinstance(code, int) and code in API_ERROR_VENUE_CODES:
        return _check(CHECK_CONDITIONAL_REFUSED, STATUS_UNVERIFIED, expected=expected, observed=observed,
                      detail="the venue could not be asked")
    return _check(CHECK_CONDITIONAL_REFUSED, STATUS_FAIL, expected=expected, observed=observed,
                  detail=f"refused with {code}, not {VENUE_CONDITIONAL_MOVED}: the venue's answer "
                         "about conditional orders changed")


def check_leverage(snapshot: Mapping[str, Any] | None, symbols: Sequence[str], *, now: str,
                   max_leverage: float, stale_after_seconds: float) -> dict[str, Any]:
    """Every traded symbol's configured leverage is at most the backtests' (decision 45). Read off the
    account snapshot the same fire writes, so it asks the venue nothing.

    Higher is FAIL: the liquidation price sits nearer than the evidence assumed. Lower is safer and
    passes. A symbol the account does not report, or a snapshot missing, degraded or older than the
    account board's own staleness, is UNVERIFIED."""
    expected = f"configured leverage <= {max_leverage:g}x on every traded symbol"
    if not isinstance(snapshot, Mapping):
        return _check(CHECK_LEVERAGE, STATUS_UNVERIFIED, expected=expected, detail="no account snapshot")
    age = _age_seconds(snapshot.get("as_of"), now)
    if age is None or age > stale_after_seconds or snapshot.get("degraded"):
        return _check(CHECK_LEVERAGE, STATUS_UNVERIFIED, expected=expected,
                      observed={"as_of": snapshot.get("as_of")}, detail="the account snapshot is not current")
    configured = snapshot.get("configured_leverage")
    configured = configured if isinstance(configured, Mapping) else {}
    seen: dict[str, Any] = {}
    above: list[str] = []
    unknown: list[str] = []
    for symbol in symbols:
        value = configured.get(symbol)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            unknown.append(symbol)
            continue
        seen[symbol] = float(value)
        if value > max_leverage:
            above.append(symbol)
    observed = {"leverage": seen, "as_of": snapshot.get("as_of"), "unreported": unknown}
    if above:
        return _check(CHECK_LEVERAGE, STATUS_FAIL, expected=expected, observed=observed,
                      detail="above the backtests' leverage: " + ", ".join(f"{s} {seen[s]:g}x" for s in above))
    if unknown:
        return _check(CHECK_LEVERAGE, STATUS_UNVERIFIED, expected=expected, observed=observed,
                      detail="the account does not report " + ", ".join(unknown))
    return _check(CHECK_LEVERAGE, STATUS_PASS, expected=expected, observed=observed)


def check_position_mode(hedge: Any, *, failure: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The account is in one-way mode (``dualSidePosition`` false).

    Anything but an answer is UNVERIFIED in 4a — including an HTTP 404 on the documented path. A 404
    that persists on a path this runtime depends on is the 2026-08-03 failure class (the
    `openAlgoOrders` spelling), and is the first candidate for a FAIL once the host has shown what
    this endpoint answers."""
    expected = "one-way mode (dualSidePosition false)"
    if failure is not None or not isinstance(hedge, bool):
        return _check(CHECK_POSITION_MODE, STATUS_UNVERIFIED, expected=expected, observed=dict(failure or {}),
                      detail="the position mode could not be read")
    if hedge:
        return _check(CHECK_POSITION_MODE, STATUS_FAIL, expected=expected, observed={"dual_side_position": True},
                      detail="hedge mode: no request carries a positionSide, so every entry is refused, "
                             "and the book's one-position-per-symbol premise does not hold")
    return _check(CHECK_POSITION_MODE, STATUS_PASS, expected=expected, observed={"dual_side_position": False})


def _client_id(row: Mapping[str, Any]) -> str:
    return str(row.get("clientAlgoId") or row.get("clientOrderId") or "")


def check_nothing_resting(plain: Sequence[Mapping[str, Any]] | None, algo: Sequence[Mapping[str, Any]] | None, *,
                          failures: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """No order resting at the venue carries the sentinel's prefix: what ``/order/test`` promises —
    that it creates nothing — measured every run rather than trusted. Either list unreadable is
    UNVERIFIED."""
    expected = f"no resting order whose client id starts {SENTINEL_ID_PREFIX}"
    if failures or plain is None or algo is None:
        return _check(CHECK_NOTHING_RESTING, STATUS_UNVERIFIED, expected=expected, observed=dict(failures or {}),
                      detail="the resting orders could not be read")
    left = sorted(cid for cid in (_client_id(r) for r in [*plain, *algo]) if cid.startswith(SENTINEL_ID_PREFIX))
    observed = {"plain_resting": len(plain), "algo_resting": len(algo), "sentinel_left": left}
    if left:
        return _check(CHECK_NOTHING_RESTING, STATUS_FAIL, expected=expected, observed=observed,
                      detail="the validator left an order resting: " + ", ".join(left))
    return _check(CHECK_NOTHING_RESTING, STATUS_PASS, expected=expected, observed=observed)


def _observed(check_id: str, expected: str, observed: Mapping[str, Any], detail: str = "") -> dict[str, Any]:
    return _check(check_id, RESULT_OBSERVED, expected=expected, observed=observed, detail=detail)


def _validator_answer(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, Mapping):
        return {"answer": "none"}
    return {"accepted": answer.get("accepted"), "code": answer.get("code"),
            "msg": str(answer.get("msg"))[:200] if answer.get("msg") is not None else None}


# --- one run -----------------------------------------------------------------------------------

class _Budget:
    """The run's deadline. A call starts only with at least a second left beyond what it must leave
    for later; its timeout is what remains, at most :data:`CALL_TIMEOUT_SECONDS`."""

    def __init__(self, clock: Callable[[], float], seconds: float):
        self._clock = clock
        self._deadline = clock() + seconds

    def timeout(self, *, reserve: float = 0.0) -> int | None:
        left = self._deadline - self._clock() - reserve
        if left < 1.0:
            return None
        return int(min(float(CALL_TIMEOUT_SECONDS), left))


_SPENT = {"error": "RUN_BUDGET_SPENT"}


def _sentinel_id(kind: str, now: str, symbol: str) -> str:
    digest = hashlib.sha256(f"{now}|{kind}|{symbol}".encode("utf-8")).hexdigest()[:16]
    return f"{SENTINEL_ID_PREFIX}{kind}_{digest}"


def _test_quantity(filters: Any, price: float) -> float | None:
    """A quantity worth about twice the venue's minimum notional at ``price``, on the lot step — the
    smallest order the filters certainly admit. None when the filters cannot size one."""
    from .live_sizing import floor_to_step

    if price <= 0 or filters is None or not filters.valid():
        return None
    quantity = max(floor_to_step(2.0 * filters.min_notional / price, filters.step_size), filters.min_qty)
    if quantity * price < filters.min_notional:
        quantity = floor_to_step(quantity + filters.step_size, filters.step_size)
    if filters.max_qty > 0 and quantity > filters.max_qty:
        return None
    return quantity


def run_checks(*, symbols: Sequence[str], adapter: Any, collector: Any, now: str, root: Path | None,
               clock: Callable[[], float] = time.monotonic,
               snapshot: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every check, in the order that keeps a decision reachable: the judged ones that ask the venue,
    then the observed ones while the budget allows, then the resting-order reads, which run last so
    they can see what the validator calls before them left behind — on the budget kept back for them.

    The adapter is asked only through its validator and its reads; never raises for a venue answer."""
    from . import account_store, paper
    from .live_execution import build_order_request
    from .live_filters import parse_symbol_filters
    from .live_sizing import round_price_to_tick
    from .market_data import read_reference_quote

    budget = _Budget(clock, RUN_BUDGET_SECONDS)
    checks: list[dict[str, Any]] = []
    first = symbols[0]

    def ask(call: Callable[[int], Any], *, reserve: float = 0.0) -> tuple[Any, dict[str, Any] | None]:
        timeout = budget.timeout(reserve=reserve)
        if timeout is None:
            return None, dict(_SPENT)
        try:
            return call(timeout), None
        except MvpRuntimeError as exc:
            return None, _failure(exc)
        except Exception as exc:  # noqa: BLE001 — a check that cannot complete is UNVERIFIED, never a crash
            return None, {"error": type(exc).__name__}

    # 1. exchangeInfo (public).
    reader = getattr(collector, "exchange_info", None)
    if callable(reader):
        payload, failure = ask(lambda t: reader(timeout_seconds=t))
    else:
        payload, failure = None, {"error": "NO_EXCHANGE_INFO_READER"}
    checks.append(check_exchange_info(payload, symbols, failure=failure))
    filters = {s: parse_symbol_filters(payload, s)[0] for s in symbols} if failure is None else {}

    prices: dict[str, float] = {}

    def price_of(symbol: str, *, reserve: float = 0.0) -> float | None:
        if symbol not in prices:
            quote, _ = ask(lambda t: read_reference_quote(symbol, collector=collector, now=now, timeout_seconds=t),
                           reserve=reserve)
            value = quote.get("price") if isinstance(quote, Mapping) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                prices[symbol] = float(value)
        return prices.get(symbol)

    # 2. The -4120 (signed validator).
    price, tick = price_of(first), getattr(filters.get(first), "tick_size", 0.0)
    stop = round_price_to_tick(price * 0.9, tick, mode="down") if price and tick else 0.0
    if stop > 0:
        probe = legacy_conditional_probe(first, stop_price=stop, client_id=_sentinel_id("C", now, first))
        answer, failure = ask(lambda t: adapter.validate_order(probe, timeout_seconds=t))
        checks.append(check_conditional_refused(answer, failure=failure))
    else:
        checks.append(check_conditional_refused(None, detail=f"no price or tick for {first} to shape the probe"))

    # 3. Position mode (signed read).
    hedge, failure = ask(lambda t: adapter.position_mode(timeout_seconds=t))
    checks.append(check_position_mode(hedge, failure=failure))

    # 4. Leverage (the account snapshot this fire already wrote; no call).
    if snapshot is None:
        snapshot = account_store.read_snapshot(root)
    checks.append(check_leverage(snapshot, symbols, now=now, max_leverage=float(paper.ASSUMED_LEVERAGE),
                                 stale_after_seconds=float(account_store.STALE_AFTER_SECONDS)))

    # 5-8. The hypotheses, while the budget leaves the resting reads their share. Each request is
    # the runtime's own, built by the money path's builder inside the call, so a builder refusal is
    # this check's answer rather than the run's end.
    entries: dict[str, Any] = {}
    for symbol in symbols:
        if budget.timeout(reserve=RESERVED_SECONDS) is None:
            # Said as the budget, not as a missing price: "the venue gave no price" and "there was no
            # time to ask" are different facts about a run.
            entries[symbol] = dict(_SPENT)
            continue
        symbol_price = price_of(symbol, reserve=RESERVED_SECONDS)
        quantity = _test_quantity(filters.get(symbol), symbol_price or 0.0)
        if quantity is None:
            entries[symbol] = {"skipped": "no filters or price to size a request"}
            continue
        entry_intent = {"symbol": symbol, "side": "BUY", "order_type_exchange": "MARKET", "quantity": quantity,
                        "reduce_only": False, "client_order_id": _sentinel_id("E", now, symbol)}
        answer, failure = ask(lambda t: adapter.validate_order(build_order_request(entry_intent), timeout_seconds=t),
                              reserve=RESERVED_SECONDS)
        entries[symbol] = failure if failure is not None else {"quantity": quantity, **_validator_answer(answer)}
    checks.append(_observed(CHECK_ENTRY_TEST, "the runtime's MARKET entry request is accepted", {"symbols": entries}))

    targets: dict[str, Any] = {}
    facts = checks[0]["observed"].get("symbols") or {}
    band = (facts.get(first) or {}).get("percent_price")
    target_filters = filters.get(first)
    if price and band and target_filters is not None:
        for leg, side, factor, mode in (("LONG_TP", "SELL", 1.0 + 2.0 * (band["up"] - 1.0), "down"),
                                        ("SHORT_TP", "BUY", 1.0 - 2.0 * (1.0 - band["down"]), "up")):
            limit = round_price_to_tick(price * factor, target_filters.tick_size, mode=mode)
            quantity = _test_quantity(target_filters, limit)
            if limit <= 0 or quantity is None:
                targets[leg] = {"skipped": "no price or filters to shape the request"}
                continue
            target_intent = {"symbol": first, "side": side, "order_type_exchange": "LIMIT", "price": limit,
                             "time_in_force": "GTC", "quantity": quantity, "reduce_only": True,
                             "client_order_id": _sentinel_id("T" + side[0], now, first)}
            answer, failure = ask(
                lambda t: adapter.validate_order(build_order_request(target_intent), timeout_seconds=t),
                reserve=RESERVED_SECONDS)
            targets[leg] = {"side": side, "price_over_reference": round(factor, 6), "band": band,
                            **(failure if failure is not None else _validator_answer(answer))}
    else:
        targets["skipped"] = f"no price or PERCENT_PRICE band for {first}"
    checks.append(_observed(
        CHECK_TARGET_TEST, "the runtime's take-profit LIMIT twice the price band away is accepted",
        {"symbol": first, "legs": targets},
        detail="reduceOnly while flat: a refusal may be the account's state (-2022), not the band"))

    for check_id, algo in ((CHECK_ORDER_QUERY, False), (CHECK_ALGO_QUERY, True)):
        unknown = _sentinel_id("QA" if algo else "Q", now, first)
        found, failure = ask(lambda t: adapter.fetch_order(first, unknown, timeout_seconds=t, algo=algo),
                             reserve=RESERVED_SECONDS)
        observed = failure if failure is not None else {"answer": "not_found" if found is None else "found"}
        checks.append(_observed(check_id, "an id no order carries reads as not found (None)", observed))

    # 9. Nothing left resting (signed reads, on the budget kept for them).
    plain, plain_failure = ask(lambda t: adapter.open_orders(None, timeout_seconds=t))
    algo_rows, algo_failure = ask(lambda t: adapter.algo_open_orders(None, timeout_seconds=t))
    failures = {**({"plain": plain_failure} if plain_failure else {}), **({"algo": algo_failure} if algo_failure else {})}
    checks.append(check_nothing_resting(plain, algo_rows, failures=failures or None))
    return checks


def _registered_symbols(root: Path | None, now: str) -> list[str]:
    """The symbols a live order may target: the registered budget's allowlist, when the budget is
    valid. No valid budget, no live order — and nothing to verify."""
    from .live_order import resolve_live_order_limits

    _, budget = resolve_live_order_limits(root, now=now)
    if not budget.get("valid"):
        return []
    return [s for s in budget.get("symbol_allowlist") or [] if isinstance(s, str) and s]


def refresh_verification(*, collector: Any, now: str, root: Path | None = None, adapter: Any = None,
                         clock: Callable[[], float] = time.monotonic) -> str:
    """Verify once and store what was decided. Returns a one-line status for the fire.

    **Never raises**: this runs inside the pipeline fire, a RISK kind, and an exception here would
    take down the loop that manages real positions in order to fail at bookkeeping. Every failure is
    a status line and a moved mark.

    Asks nothing unless live trading is opted in (the live adapter exists only then) and a valid budget
    names the symbols; asks through the raw adapter, never the one the API breaker records."""
    try:
        _write_json(refresh_mark_path(root), {"attempted_at": now, "outcome": OUTCOME_STARTED},
                    code="VENUE_CONTRACT_MARK_LOCKED", label="venue contract refresh mark")
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return f"venue contract: mark not written ({type(exc).__name__})"

    mark: dict[str, Any] = {"attempted_at": now}
    try:
        symbols = _registered_symbols(root, now)
        if not symbols:
            mark["outcome"] = OUTCOME_NO_SCOPE
        else:
            if adapter is None:
                from .live_execution import select_order_adapter

                adapter = select_order_adapter(now=now, root=root)
            if getattr(adapter, "network_egress", False) is not True:
                mark["outcome"] = OUTCOME_NOT_OPTED_IN
            else:
                checks = run_checks(symbols=symbols, adapter=adapter, collector=collector, now=now,
                                    root=root, clock=clock)
                status = judge(checks)
                mark.update(status=status, symbols=symbols, checks=checks,
                            outcome=OUTCOME_DECIDED if status != STATUS_UNVERIFIED else OUTCOME_INCOMPLETE)
                if status != STATUS_UNVERIFIED:
                    _write_json(contract_path(root), build_record(status=status, checks=checks,
                                                                  symbols=symbols, now=now),
                                code="VENUE_CONTRACT_LOCKED", label="venue contract record")
    except Exception as exc:  # noqa: BLE001 — see the docstring
        mark.update(outcome=OUTCOME_ERROR, error=str(getattr(exc, "reason_code", type(exc).__name__)))
    try:
        _write_json(refresh_mark_path(root), mark, code="VENUE_CONTRACT_MARK_LOCKED",
                    label="venue contract refresh mark")
    except Exception:  # noqa: BLE001 — the line below still says what happened
        pass
    return status_line(mark)


def status_line(mark: Mapping[str, Any]) -> str:
    outcome = mark.get("outcome")
    if outcome in (OUTCOME_DECIDED, OUTCOME_INCOMPLETE):
        failed = [str(c.get("check")) for c in mark.get("checks") or []
                  if c.get("check") in JUDGED_CHECKS and c.get("result") != STATUS_PASS]
        return f"venue contract: {mark.get('status')}" + (f" ({', '.join(failed)})" if failed else "")
    if outcome == OUTCOME_ERROR:
        return f"venue contract: not verified ({mark.get('error')})"
    return f"venue contract: not verified ({outcome})"


__all__ = [
    "CHECK_IDS", "CONTRACT_VERSION", "JUDGED_CHECKS", "MAX_AGE_SECONDS", "NOT_VERIFIED",
    "OBSERVED_CHECKS", "REFRESH_AFTER_SECONDS", "SENTINEL_ID_PREFIX", "STATUS_FAIL", "STATUS_PASS",
    "STATUS_UNVERIFIED", "VENUE_CONTRACT_INVALID", "VENUE_CONTRACT_TAMPERED", "VENUE_CONTRACT_UNREADABLE",
    "build_record", "check_conditional_refused", "check_exchange_info", "check_leverage",
    "check_nothing_resting", "check_position_mode", "is_due", "judge", "legacy_conditional_probe",
    "read_refresh_mark", "read_verification", "refresh_verification", "run_checks", "status_line",
    "verification_status",
]
