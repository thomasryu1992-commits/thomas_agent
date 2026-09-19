"""PR4a — the venue contract sentinel: what this runtime assumes about the exchange, asked of the
exchange itself, on a schedule (Thomas decisions 43-46, 2026-09-19).

**Why.** Every check this repository ran before an order pointed at its own model of the venue:
closed schemas, filters, the request builder's rules. On 2026-08-02 all of them passed while the
venue refused both protective stops, because the one fact that had changed — conditional order
types had moved off ``/fapi/v1/order`` to the Algo API — lives only at the venue. A unit test cannot
see drift in a contract it never calls. This module calls it.

**What it can see without creating an order.** Three verbs: a public GET, a signed GET, and
``POST /fapi/v1/order/test`` — the order API's own validator, which creates nothing. The sentinel
does not take that on trust: it asks for its own entry test by id afterwards and reads the resting
orders last (:data:`CHECK_ENTRY_LEFT_NO_ORDER`, :data:`CHECK_NOTHING_RESTING`). ``exchangeInfo``
alone would not have caught 2026-08-02: on 2026-09-19 it still lists ``STOP_MARKET`` and
``TAKE_PROFIT_MARKET`` for every traded symbol, although the order API refuses both with -4120. The
-4120 is the observable: the 2026-08-03 diagnostic request, sent to the validator, must still be
refused with it. What none of the three verbs can show is written into every record
(:data:`NOT_VERIFIED`) so a PASS never reads as more than it is: an algo order's placement (the Algo
API has no validator; the signed testnet cycle, PR1d, is that evidence), the codes only a real order
or cancel produces, and account state, which the validator does not judge.

**Judged and observed.** A check may fail the contract only where what it expects has been measured
at this venue (:data:`JUDGED_CHECKS`). The others are hypotheses the runtime's reads rest on — the
answer to an unknown algo id, a take-profit LIMIT beyond the price band while flat — and are
recorded, never judged (:data:`OBSERVED_CHECKS`), until the host's own answers are on record.

**It backs off.** The first answer that says the venue could not be asked — a rate limit, a ban, a
5xx, a transport failure, a key or clock refusal (`live_order.api_error_counts`, the breaker's own
test of "could not ask") — stops the run, and a fire whose market data was already rate limited is
not run at all: continuing to knock after a 429 is how this venue's throttling becomes an IP ban,
and a ban would refuse the money path's closes too. What was not asked is UNVERIFIED, never a
verdict.

**Two files**, as :mod:`account_store` keeps them and for its reason: the last DECIDED verification
(PASS or FAIL) and the last ATTEMPT. A run that could not decide moves only the attempt, so a venue
hiccup never erases a good verification; a run the venue answered with a violation writes FAIL at
once (decision 44), and while the decided record is a FAIL it is asked again on the next fire rather
than an hour later. A PASS stands for :data:`MAX_AGE_SECONDS`.

**Not the API breaker's, and not stage evidence.** The breaker counts the money path's signed calls
(decision 27), and the sentinel is not the money path: it asks the raw adapter, and its failures are
its own record. And ``/order/test`` is never evidence for an execution-stage transition (decision 3).

**What it gates (PR4b, decision 46).** A mainnet autonomous entry and a probe are decided on a usable
PASS (:func:`entry_fact`, :func:`entry_refusal`); closing, protecting and the testnet door never read
it. The reader half of this module imports nothing heavier than the state and integrity helpers, so
a door can read it; the checks import the venue modules where they run.
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
# While the decided record is a FAIL, the next fire asks again (review of #902): a symbol back from a
# break, or a venue answer that was a blip, must not hold the record at FAIL for another hour.
RETRY_AFTER_FAIL_SECONDS = 10 * 60
MAX_AGE_SECONDS = 6 * 60 * 60
# A verification dated this far past the clock is not one to trust either: clocks disagree by
# seconds, not minutes.
FUTURE_SKEW_SECONDS = 5 * 60
# The account snapshot the leverage is judged on is refreshed on this lane every 15 minutes (a fire
# a few seconds early skips one); older than three refreshes, it says nothing about the account now,
# so the leverage is not judged on it — in either direction.
LEVERAGE_SNAPSHOT_MAX_AGE_SECONDS = 45 * 60

# Per call, and per run. The run rides the risk lane's pipeline fire, after its cycles: about fifteen
# calls at the venue's usual latency is a few seconds. The budget bounds the unusual case. A call is
# started only with its full timeout still inside the budget, beyond what the checks after it keep
# back (:data:`FINAL_RESERVE_SECONDS`) — so a venue answering every call within its timeout always
# reaches a decision, and a slow one adds at most the budget and one call's socket timeouts to the
# fire. (The timeout bounds each socket operation, not a call as a whole; a DNS lookup is not bounded
# by it — the account refresh on the same fire has the same property.)
CALL_TIMEOUT_SECONDS = 4
RUN_BUDGET_SECONDS = 30.0
# The last three judged calls — the entry test's own id, and the two resting-order lists — run after
# every validator call, so they can see what those left. Everything before them starts only while
# their full timeouts are still in the budget: a decision is never hostage to a slow early call or to
# a hypothesis.
FINAL_RESERVE_SECONDS = 3.0 * CALL_TIMEOUT_SECONDS

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
CHECK_ENTRY_LEFT_NO_ORDER = "entry_test_left_no_order"
CHECK_NOTHING_RESTING = "nothing_left_resting"
CHECK_ENTRY_TEST = "order_test_market_entry"
CHECK_TARGET_TEST = "order_test_target_limit"
CHECK_ALGO_QUERY = "algo_query_unknown_id"

# What may fail the contract, each because its expectation was measured here:
# - exchange_info: the traded symbols' listing, as read 2026-09-19;
# - the -4120: the 2026-08-03 diagnostic request's own answer;
# - the leverage: every traded symbol at 5x, read 2026-09-02, and decision 45's bound;
# - the position mode: not a venue behaviour but the runtime's requirement — no request carries a
#   `positionSide` and the book nets per symbol (`live_position`), so a hedge-mode account refuses
#   every entry (-4061) and breaks the reconcile's premise;
# - the entry test left no order, and nothing is left resting: by construction — the validator
#   creates nothing, and these measure it. The entry test is an executable MARKET BUY in all but its
#   path; if the path ever pointed at the order endpoint, the order would be FOUND by its own id
#   (resting-order reads cannot see an order that filled).
JUDGED_CHECKS = (CHECK_EXCHANGE_INFO, CHECK_CONDITIONAL_REFUSED, CHECK_LEVERAGE, CHECK_POSITION_MODE,
                 CHECK_ENTRY_LEFT_NO_ORDER, CHECK_NOTHING_RESTING)
# What is recorded, never judged, until the host's answers are on record. The entry and target requests
# are the runtime's own, through its own builder; the algo query asks for an id no order carries.
OBSERVED_CHECKS = (CHECK_ENTRY_TEST, CHECK_TARGET_TEST, CHECK_ALGO_QUERY)
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
OUTCOME_RATE_LIMITED = "rate_limited_this_fire"
OUTCOME_ERROR = "error"

# Why a call was not made: the budget had no full slot left for it, or an earlier answer said the
# venue could not be asked and the run stopped there.
RUN_BUDGET_SPENT = "RUN_BUDGET_SPENT"
RUN_STOPPED = "RUN_STOPPED"

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


def _age_seconds(stamp: Any, now: Any) -> float | None:
    """``now - stamp`` in seconds, or None when either is not a timestamp. Never raises: a door
    judges on it, and an undated fact is one the door refuses, not an exception."""
    if not isinstance(stamp, str) or not stamp or not isinstance(now, str) or not now:
        return None
    try:
        return (timeutil.parse_iso(now) - timeutil.parse_iso(stamp)).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return None


def read_refresh_mark(root: Path | None = None) -> dict[str, Any] | None:
    """The last attempt, or None for absent AND for damaged: the mark only says when to ask again and
    what the last attempt saw, and a mark nobody can read must neither stop the asking nor take down
    a board that renders it. Its checks are kept only while they are a list of objects."""
    path = refresh_mark_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    checks = data.get("checks")
    if checks is not None and not (isinstance(checks, list) and all(isinstance(c, dict) for c in checks)):
        data = {key: value for key, value in data.items() if key != "checks"}
    return data


def is_due(mark: Mapping[str, Any] | None, now: str, *, symbols: Sequence[str] = ()) -> bool:
    """Whether to ask the venue again, given when it was last ASKED — not last answered. Never asked,
    or a mark that cannot be read, is due: being wrong costs one run. While the decided record refuses
    entries that the next ask can let through — a FAIL, a record under another contract version, one
    that does not name a symbol in ``symbols`` (the budget's, now) — the next fire asks
    (:data:`RETRY_AFTER_FAIL_SECONDS`)."""
    attempted = mark.get("attempted_at") if isinstance(mark, Mapping) else None
    age = _age_seconds(attempted, now)
    if age is None or age < 0:
        return True
    return age >= (RETRY_AFTER_FAIL_SECONDS if _asks_sooner(mark, symbols) else REFRESH_AFTER_SECONDS)


def _asks_sooner(mark: Mapping[str, Any], symbols: Sequence[str]) -> bool:
    """The decided record as the last attempt saw it (:func:`_decided`). Nothing decided keeps the
    hour (Thomas decision 44): the venue did not answer, and asking it sooner is not an answer. A mark
    written before PR4b names no version, and is asked sooner once."""
    decided = mark.get("decided_status")
    if decided is None:
        return False
    if decided == STATUS_FAIL or mark.get("decided_version") != CONTRACT_VERSION:
        return True
    return any(not covers(mark.get("decided_symbols"), symbol) for symbol in symbols)


def refresh_due(root: Path | None, now: str) -> bool:
    """:func:`is_due` for this machine: its mark, and the symbols its registered budget names now.
    **Never raises** — the pipeline fire asks it; a question it cannot answer is due, and the refresh
    that follows never raises either."""
    try:
        return is_due(read_refresh_mark(root), now, symbols=_registered_symbols(root, now))
    except Exception:  # noqa: BLE001 — see the docstring
        return True


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
    doors read to let an entry through (PR4b), and one that cannot prove itself must not."""
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


def _symbol_failures(record: Mapping[str, Any]) -> dict[str, list[str]]:
    """Which judged checks failed for which symbol — the listing and the leverage are per symbol —
    so a door can tell a failure of one symbol from one of the account (review of #902)."""
    failures: dict[str, list[str]] = {}
    for check in record.get("checks") or []:
        if not isinstance(check, Mapping) or check.get("result") != STATUS_FAIL:
            continue
        observed = check.get("observed") if isinstance(check.get("observed"), Mapping) else {}
        if check.get("check") == CHECK_EXCHANGE_INFO:
            symbols = list((observed.get("problems") or {}).keys())
        elif check.get("check") == CHECK_LEVERAGE:
            symbols = list(observed.get("above") or [])
        else:
            continue
        for symbol in symbols:
            failures.setdefault(str(symbol), []).append(str(check["check"]))
    return failures


def verification_status(root: Path | None = None, *, now: str) -> dict[str, Any]:
    """What the last decided verification says at ``now``. Raises on a record that cannot prove
    itself (see :func:`read_verification`); the board names that, a door refuses on it.

    ``usable`` is :func:`entry_refusal`'s answer for the record alone, and ``refusal`` its reason —
    the doors' own judge, so the board and a door cannot disagree about it. It speaks for the
    ``symbols`` the verification covered and no others: a door also requires its symbol among them
    (:func:`covers`), since the budget may name one the last run did not."""
    record = read_verification(root)
    if record is None:
        return {"recorded": False, "status": None, "contract_version": None, "verified_at": None,
                "age_seconds": None, "stale": True, "version_current": False, "usable": False,
                "refusal": ENTRY_CONTRACT_MISSING, "failed_checks": [], "symbols": [], "symbol_failures": {}}
    age = _age_seconds(record.get("verified_at"), now)
    refusal = entry_refusal(_fact_of(record), symbol=None, at=now)
    return {
        "recorded": True,
        "status": record.get("status"),
        "contract_version": record.get("contract_version"),
        "verified_at": record.get("verified_at"),
        "age_seconds": age,
        "stale": _stale(age),
        "version_current": record.get("contract_version") == CONTRACT_VERSION,
        "usable": refusal is None,
        "refusal": refusal["reason_code"] if refusal is not None else None,
        "failed_checks": list(record.get("failed_checks") or []),
        "symbols": list(record.get("symbols") or []),
        "symbol_failures": _symbol_failures(record),
    }


# --- the entry doors' reading (PR4b, Thomas decision 46) ---------------------------------------
#
# A mainnet autonomous entry and a probe are decided on a usable PASS: the last decided verification
# says the venue still honours what the runtime assumes, under this code's contract version, at most
# MAX_AGE_SECONDS before the decision, for the symbol the entry is on. Closing, protecting and
# settling never read it — a venue that changed is a reason to stop opening positions, never to
# strand the ones open. The testnet door does not read it either: the signed testnet cycle is itself
# a venue answer, with real orders (PR1d).

# Why a door refuses, one code per thing the operator does about it: wait for the next fire (or ask
# now, `scripts/venue_contract.py --run`), find out who damaged the record, read what the venue
# contradicted, or wait for the next fire to verify under this code's version or cover a symbol the
# budget gained since (both are asked sooner, :func:`is_due`).
ENTRY_CONTRACT_MISSING = "LIVE_ENTRY_VENUE_CONTRACT_MISSING"
ENTRY_CONTRACT_UNREADABLE = "LIVE_ENTRY_VENUE_CONTRACT_UNREADABLE"
ENTRY_CONTRACT_VERSION = "LIVE_ENTRY_VENUE_CONTRACT_VERSION"
ENTRY_CONTRACT_NOT_PASS = "LIVE_ENTRY_VENUE_CONTRACT_NOT_PASS"
ENTRY_CONTRACT_STALE = "LIVE_ENTRY_VENUE_CONTRACT_STALE"
ENTRY_CONTRACT_SYMBOL = "LIVE_ENTRY_VENUE_CONTRACT_SYMBOL_NOT_COVERED"
ENTRY_CONTRACT_CODES = frozenset({ENTRY_CONTRACT_MISSING, ENTRY_CONTRACT_UNREADABLE, ENTRY_CONTRACT_VERSION,
                                  ENTRY_CONTRACT_NOT_PASS, ENTRY_CONTRACT_STALE, ENTRY_CONTRACT_SYMBOL})
# What of the decided record a door judges and the gate seals. The per-check answers are not sealed:
# the record keeps them only until the next decided run overwrites it.
ENTRY_FACT_FIELDS = ("status", "contract_version", "verified_at", "symbols", "failed_checks", "record_sha256")


def _stale(age: float | None) -> bool:
    return age is None or age < -FUTURE_SKEW_SECONDS or age > MAX_AGE_SECONDS


def _fact_of(record: Mapping[str, Any]) -> dict[str, Any]:
    fact: dict[str, Any] = {"recorded": True, **{field: record.get(field) for field in ENTRY_FACT_FIELDS}}
    for field in ("symbols", "failed_checks"):
        fact[field] = list(fact[field]) if isinstance(fact[field], list) else fact[field]
    return fact


def entry_fact(root: Path | None = None) -> dict[str, Any]:
    """The decided verification as an entry door reads it: :data:`ENTRY_FACT_FIELDS`, or why it
    could not be read. **Never raises** — the reader runs in the leg that settles and protects open
    positions, and a record that cannot prove itself is a refusal the door names, not an exception
    there."""
    try:
        record = read_verification(root)
    except MvpRuntimeError as exc:
        return {"recorded": True, "error": exc.reason_code}
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return {"recorded": True, "error": type(exc).__name__}
    if record is None:
        return {"recorded": False}
    return _fact_of(record)


def entry_refusal(fact: Any, *, symbol: str | None, at: str) -> dict[str, Any] | None:
    """Why ``fact`` (:func:`entry_fact`'s) does not back an entry on ``symbol`` decided at ``at``, or
    None. Pure.

    One reason, the first of: no fact or no record; a record that could not prove itself; another
    contract version; a decided FAIL; a verification older than :data:`MAX_AGE_SECONDS` at ``at``, or
    dated past it by more than the skew, or undated; a PASS that did not cover ``symbol``. ``symbol``
    None judges the record alone, as the board does."""
    if not isinstance(fact, Mapping):
        return {"reason_code": ENTRY_CONTRACT_MISSING, "detail": "no venue contract was read"}
    if fact.get("error"):
        return {"reason_code": ENTRY_CONTRACT_UNREADABLE, "error": str(fact["error"])}
    if fact.get("recorded") is not True:
        return {"reason_code": ENTRY_CONTRACT_MISSING,
                "detail": "no verification is recorded; the pipeline fire asks while live trading is opted in"}
    verified_at = fact.get("verified_at")
    if fact.get("contract_version") != CONTRACT_VERSION:
        return {"reason_code": ENTRY_CONTRACT_VERSION, "contract_version": fact.get("contract_version"),
                "expected": CONTRACT_VERSION, "verified_at": verified_at}
    if fact.get("status") != STATUS_PASS:
        failed = fact.get("failed_checks")
        return {"reason_code": ENTRY_CONTRACT_NOT_PASS, "status": fact.get("status"),
                "failed_checks": list(failed) if isinstance(failed, (list, tuple)) else None,
                "verified_at": verified_at}
    age = _age_seconds(verified_at, at)
    if _stale(age):
        return {"reason_code": ENTRY_CONTRACT_STALE, "verified_at": verified_at, "age_seconds": age,
                "max_age_seconds": MAX_AGE_SECONDS}
    covered = fact.get("symbols")
    if symbol is not None and not covers(covered, symbol):
        return {"reason_code": ENTRY_CONTRACT_SYMBOL, "symbol": symbol,
                "symbols": list(covered) if isinstance(covered, (list, tuple)) else None,
                "verified_at": verified_at}
    return None


def covers(symbols: Any, symbol: Any) -> bool:
    """Whether a verification that named ``symbols`` covers ``symbol`` — the rule the doors, the board
    and the refresh cadence share. Case and surrounding space do not count; anything but a list or a
    tuple names nothing, and an empty symbol is never covered."""
    wanted = str(symbol).strip().upper() if symbol is not None else ""
    if not wanted or not isinstance(symbols, (list, tuple)):
        return False
    return wanted in {str(s).strip().upper() for s in symbols}


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


def _could_not_answer(answer: Mapping[str, Any]) -> bool:
    """Whether a validator's refusal says the venue could not be asked rather than what it thinks of
    the request: the breaker's own codes and statuses (`live_order`), a 418/429 or any 5xx."""
    from .live_order import API_ERROR_HTTP_STATUSES, API_ERROR_VENUE_CODES

    code, status = answer.get("code"), answer.get("http_status")
    if isinstance(status, int) and not isinstance(status, bool) and (
            status in API_ERROR_HTTP_STATUSES or 500 <= status <= 599):
        return True
    return isinstance(code, int) and not isinstance(code, bool) and code in API_ERROR_VENUE_CODES


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
    ``PERCENT_PRICE`` band, which the filter reader does not use, and names the symbols that fail, so
    a door can tell one symbol's break from the venue's.

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
    the runtime's routing assumes. An answer that means the venue could not be asked (a rate limit,
    clock, key or venue-failure code, a 418/429 or any 5xx — :func:`_could_not_answer`) is
    UNVERIFIED, as is a call that did not complete."""
    expected = f"refused with {VENUE_CONDITIONAL_MOVED} (conditional types live on the Algo API)"
    if failure is not None or not isinstance(answer, Mapping):
        return _check(CHECK_CONDITIONAL_REFUSED, STATUS_UNVERIFIED, expected=expected,
                      observed=dict(failure or {}), detail=detail or "the validator could not be asked")
    observed = {"accepted": answer.get("accepted"), "code": answer.get("code"),
                "msg": str(answer.get("msg"))[:200] if answer.get("msg") is not None else None,
                **({"http_status": answer["http_status"]} if isinstance(answer.get("http_status"), int) else {})}
    if answer.get("supported") is False or answer.get("dry_run"):
        # Never reached with the frozen probe on the live adapter; named so it can never pass.
        return _check(CHECK_CONDITIONAL_REFUSED, STATUS_UNVERIFIED, expected=expected, observed=observed,
                      detail="the validator did not judge the request")
    code = answer.get("code")
    if answer.get("accepted") is True:
        return _check(CHECK_CONDITIONAL_REFUSED, STATUS_FAIL, expected=expected, observed=observed,
                      detail="the order API ACCEPTS a conditional type again: the routing this runtime "
                             "is built on no longer matches the venue")
    if _could_not_answer(answer):
        return _check(CHECK_CONDITIONAL_REFUSED, STATUS_UNVERIFIED, expected=expected, observed=observed,
                      detail="the venue could not be asked")
    if code == VENUE_CONDITIONAL_MOVED:
        return _check(CHECK_CONDITIONAL_REFUSED, STATUS_PASS, expected=expected, observed=observed)
    return _check(CHECK_CONDITIONAL_REFUSED, STATUS_FAIL, expected=expected, observed=observed,
                  detail=f"refused with {code}, not {VENUE_CONDITIONAL_MOVED}: the venue's answer "
                         "about conditional orders changed")


def check_leverage(snapshot: Mapping[str, Any] | None, symbols: Sequence[str], *, now: str,
                   max_leverage: float, max_age_seconds: float = LEVERAGE_SNAPSHOT_MAX_AGE_SECONDS
                   ) -> dict[str, Any]:
    """Every traded symbol's configured leverage is at most the backtests' (decision 45). Read off the
    account snapshot this lane refreshes every 15 minutes, so it asks the venue nothing.

    Higher is FAIL: the liquidation price sits nearer than the evidence assumed. Lower is safer and
    passes. Judged only on a snapshot at most :data:`LEVERAGE_SNAPSHOT_MAX_AGE_SECONDS` old, in either
    direction — an older one says nothing about the account now (review of #902). A symbol the
    account does not report, or a snapshot missing, degraded or older, is UNVERIFIED."""
    expected = f"configured leverage <= {max_leverage:g}x on every traded symbol"
    if not isinstance(snapshot, Mapping):
        return _check(CHECK_LEVERAGE, STATUS_UNVERIFIED, expected=expected, detail="no account snapshot")
    age = _age_seconds(snapshot.get("as_of"), now)
    if age is None or age > max_age_seconds or snapshot.get("degraded"):
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
    observed = {"leverage": seen, "as_of": snapshot.get("as_of"), "unreported": unknown, "above": above}
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


def check_entry_left_no_order(found: Any, *, sent: bool, failure: Mapping[str, Any] | None = None,
                              symbol: str | None = None) -> dict[str, Any]:
    """The entry test's own id, asked of the order API afterwards, names no order.

    The entry test is the runtime's executable MARKET BUY in all but its path; an order found under
    its id means the validator created one — a path that no longer points at ``/order/test`` — and a
    filled order leaves nothing resting for :func:`check_nothing_resting` to see. Not sent (no price,
    no filters, no budget, a refusal by the venue or the builder) is a PASS with nothing to find: no
    order can carry the id. A query that could not be answered, or answered with a code the reader does
    not take as "not found", is UNVERIFIED. ``symbol`` names whose id was asked for (PR4b-2)."""
    expected = "no order under the entry test's own id"
    asked = {"symbol": symbol} if symbol is not None else {}
    if not sent:
        return _check(CHECK_ENTRY_LEFT_NO_ORDER, STATUS_PASS, expected=expected, observed={"sent": False},
                      detail="no entry test was accepted or left unanswered, so none can have left an order")
    if failure is not None:
        return _check(CHECK_ENTRY_LEFT_NO_ORDER, STATUS_UNVERIFIED, expected=expected,
                      observed={"sent": True, **asked, **failure}, detail="the order could not be asked for")
    if found is None:
        return _check(CHECK_ENTRY_LEFT_NO_ORDER, STATUS_PASS, expected=expected,
                      observed={"sent": True, **asked, "answer": "not_found"})
    status = found.get("status") if isinstance(found, Mapping) else None
    return _check(CHECK_ENTRY_LEFT_NO_ORDER, STATUS_FAIL, expected=expected,
                  observed={"sent": True, **asked, "answer": "found", "status": status},
                  detail="the validator's entry test left an ORDER at the venue: its path is not the "
                         "order API's validator")


def _client_id(row: Mapping[str, Any]) -> str:
    return str(row.get("clientAlgoId") or row.get("clientOrderId") or "")


def check_nothing_resting(plain: Sequence[Mapping[str, Any]] | None, algo: Sequence[Mapping[str, Any]] | None, *,
                          failures: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """No order resting at the venue carries the sentinel's prefix — nothing the sentinel sent is
    resting. (What rests is all this can see; an order that filled is
    :func:`check_entry_left_no_order`'s.) Either list unreadable is UNVERIFIED."""
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


def _entry_id_symbol(symbols: Sequence[str], entries: Mapping[str, Any]) -> str | None:
    """Whose entry-test id could name an order (PR4b-2 review): the first symbol whose request the
    validator accepted — on a path that drifted to the order endpoint, that one filled; else the first
    whose outcome is unknown — the call was made and no answer of the venue's came back, or a 5xx did,
    which an order endpoint also answers when it does not know. A request the venue refused, or one
    that never left the process, created nothing, and its id is not worth the call."""
    def entry(symbol: str) -> Mapping[str, Any]:
        value = entries.get(symbol)
        return value if isinstance(value, Mapping) else {}

    for symbol in symbols:
        if entry(symbol).get("sent") is True and entry(symbol).get("accepted") is True:
            return symbol
    for symbol in symbols:
        answer = entry(symbol)
        status = answer.get("http_status")
        unanswered = "accepted" not in answer and "venue_code" not in answer
        server_side = isinstance(status, int) and not isinstance(status, bool) and 500 <= status <= 599
        if answer.get("sent") is True and (unanswered or server_side):
            return symbol
    return None


def _validator_answer(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, Mapping):
        return {"answer": "none"}
    return {"accepted": answer.get("accepted"), "code": answer.get("code"),
            "msg": str(answer.get("msg"))[:200] if answer.get("msg") is not None else None,
            **({"http_status": answer["http_status"]} if isinstance(answer.get("http_status"), int) else {})}


# --- one run -----------------------------------------------------------------------------------

class _Budget:
    """The run's deadline. A call starts only if its full :data:`CALL_TIMEOUT_SECONDS` fits in what
    is left beyond what the calls after it keep back — never on a truncated timeout, which would turn
    a slow-but-answering venue into a failure of the sentinel's own making."""

    def __init__(self, clock: Callable[[], float], seconds: float):
        self._clock = clock
        self._deadline = clock() + seconds

    def timeout(self, *, reserve: float = 0.0) -> int | None:
        left = self._deadline - self._clock() - reserve
        return CALL_TIMEOUT_SECONDS if left >= CALL_TIMEOUT_SECONDS else None


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
    """Every check, in the order that keeps a decision reachable: the judged ones that ask the venue
    early, then the observed ones, then the three judged reads that must see what every validator call
    before them left — each started only with its full timeout inside the budget beyond what the last
    three keep back.

    The first answer that says the venue could not be asked stops the run (a fire whose market data
    was already rate limited stops before the first call): every call after it is RUN_STOPPED. The
    adapter is asked only through its validator and its reads; never raises for a venue answer."""
    from . import account_store, paper
    from .live_execution import build_order_request
    from .live_filters import parse_symbol_filters
    from .live_order import api_error_counts
    from .live_sizing import round_price_to_tick
    from .market_data import read_reference_quote

    budget = _Budget(clock, RUN_BUDGET_SECONDS)
    checks: list[dict[str, Any]] = []
    first = symbols[0]
    stopped: dict[str, Any] = {}

    def ask(call: Callable[[int], Any], *, reserve: float = FINAL_RESERVE_SECONDS,
            validator: bool = False) -> tuple[Any, dict[str, Any] | None]:
        if not stopped and getattr(collector, "rate_limited", None) is not None:
            stopped["error"] = "MARKET_DATA_RATE_LIMITED"
        if stopped:
            return None, {"error": RUN_STOPPED, "after": dict(stopped)}
        timeout = budget.timeout(reserve=reserve)
        if timeout is None:
            return None, {"error": RUN_BUDGET_SPENT}
        try:
            answer = call(timeout)
        except Exception as exc:  # noqa: BLE001 — a call that cannot complete is UNVERIFIED, never a crash
            failure = _failure(exc) if isinstance(exc, MvpRuntimeError) else {"error": type(exc).__name__}
            if api_error_counts(exc):
                stopped.update(failure)
            return None, failure
        if validator and isinstance(answer, Mapping) and _could_not_answer(answer):
            stopped.update(error="VENUE_COULD_NOT_ANSWER", venue_code=answer.get("code"),
                           **({"http_status": answer["http_status"]}
                              if isinstance(answer.get("http_status"), int) else {}))
        return answer, None

    # 1. exchangeInfo (public).
    reader = getattr(collector, "exchange_info", None)
    if callable(reader):
        payload, failure = ask(lambda t: reader(timeout_seconds=t))
    else:
        payload, failure = None, {"error": "NO_EXCHANGE_INFO_READER"}
    checks.append(check_exchange_info(payload, symbols, failure=failure))
    filters = {s: parse_symbol_filters(payload, s)[0] for s in symbols} if failure is None else {}

    prices: dict[str, float] = {}

    def price_of(symbol: str) -> float | None:
        if symbol not in prices:
            quote, _ = ask(lambda t: read_reference_quote(symbol, collector=collector, now=now, timeout_seconds=t))
            value = quote.get("price") if isinstance(quote, Mapping) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                prices[symbol] = float(value)
        return prices.get(symbol)

    # 2. The -4120 (signed validator).
    price, tick = price_of(first), getattr(filters.get(first), "tick_size", 0.0)
    stop = round_price_to_tick(price * 0.9, tick, mode="down") if price and tick else 0.0
    if stop > 0:
        probe = legacy_conditional_probe(first, stop_price=stop, client_id=_sentinel_id("C", now, first))
        answer, failure = ask(lambda t: adapter.validate_order(probe, timeout_seconds=t), validator=True)
        checks.append(check_conditional_refused(answer, failure=failure))
    else:
        checks.append(check_conditional_refused(None, detail=f"no price or tick for {first} to shape the probe"))

    # 3. Position mode (signed read).
    hedge, failure = ask(lambda t: adapter.position_mode(timeout_seconds=t))
    checks.append(check_position_mode(hedge, failure=failure))

    # 4. Leverage (the account snapshot this lane keeps; no call).
    if snapshot is None:
        snapshot = account_store.read_snapshot(root)
    checks.append(check_leverage(snapshot, symbols, now=now, max_leverage=float(paper.ASSUMED_LEVERAGE)))

    # 5-7. The hypotheses. Each request is the runtime's own, built by the money path's builder inside
    # the call, so a builder refusal is this check's answer rather than the run's end.
    entries: dict[str, Any] = {}
    for symbol in symbols:
        if stopped or budget.timeout(reserve=FINAL_RESERVE_SECONDS) is None:
            # Said as what it was, not as a missing price: "the venue gave no price" and "there was no
            # time, or no leave, to ask" are different facts about a run.
            entries[symbol] = ({"error": RUN_STOPPED, "after": dict(stopped)} if stopped
                               else {"error": RUN_BUDGET_SPENT})
            continue
        symbol_price = price_of(symbol)
        quantity = _test_quantity(filters.get(symbol), symbol_price or 0.0)
        if quantity is None:
            entries[symbol] = {"skipped": "no filters or price to size a request"}
            continue
        entry_intent = {"symbol": symbol, "side": "BUY", "order_type_exchange": "MARKET", "quantity": quantity,
                        "reduce_only": False, "client_order_id": _sentinel_id("E", now, symbol)}
        # Built before the call: a builder refusal is this symbol's answer, not the run's end — and
        # nothing left the process, so nothing was sent (PR4b-2 review).
        try:
            entry_request = build_order_request(entry_intent)
        except Exception as exc:  # noqa: BLE001 — see the comment above
            entries[symbol] = {"sent": False, **(_failure(exc) if isinstance(exc, MvpRuntimeError)
                                                 else {"error": type(exc).__name__})}
            continue
        answer, failure = ask(lambda t: adapter.validate_order(entry_request, timeout_seconds=t), validator=True)
        # "Sent" whenever the call was made at all: a request that timed out may still have reached the
        # venue, and that is exactly the one whose id must be asked for afterwards.
        attempted = failure is None or failure.get("error") not in (RUN_STOPPED, RUN_BUDGET_SPENT)
        entries[symbol] = ({"sent": attempted, **failure} if failure is not None
                           else {"sent": True, "quantity": quantity, **_validator_answer(answer)})
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
                validator=True)
            targets[leg] = {"side": side, "price_over_reference": round(factor, 6), "band": band,
                            **(failure if failure is not None else _validator_answer(answer))}
    else:
        targets["skipped"] = f"no price or PERCENT_PRICE band for {first}"
    checks.append(_observed(
        CHECK_TARGET_TEST, "the runtime's take-profit LIMIT twice the price band away is accepted",
        {"symbol": first, "legs": targets},
        detail="reduceOnly while flat: a refusal may be the account's state (-2022), not the band"))

    unknown = _sentinel_id("QA", now, first)
    found, failure = ask(lambda t: adapter.fetch_order(first, unknown, timeout_seconds=t, algo=True))
    checks.append(_observed(CHECK_ALGO_QUERY, "an algo id no order carries reads as not found (None)",
                            failure if failure is not None else {"answer": "not_found" if found is None else "found"}))

    # 8-9. The last three judged reads, on the budget kept for them: the entry test's own id (a filled
    # order rests nowhere), then everything resting. The id is one that could name an order
    # (:func:`_entry_id_symbol`, PR4b-2): a first symbol skipped, refused by the venue or by the builder
    # left nothing under its id, while the next one's request may have.
    asked_for = _entry_id_symbol(symbols, entries)
    if asked_for is not None:
        entry_id = _sentinel_id("E", now, asked_for)
        found, failure = ask(lambda t: adapter.fetch_order(asked_for, entry_id, timeout_seconds=t), reserve=0.0)
    else:
        found, failure = None, None
    checks.append(check_entry_left_no_order(found, sent=asked_for is not None, failure=failure, symbol=asked_for))
    plain, plain_failure = ask(lambda t: adapter.open_orders(None, timeout_seconds=t), reserve=0.0)
    algo_rows, algo_failure = ask(lambda t: adapter.algo_open_orders(None, timeout_seconds=t), reserve=0.0)
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


def _decided(root: Path | None) -> dict[str, Any]:
    """The decided record as it stands, for the mark: its status, and the version and symbols it was
    verified under — what :func:`is_due` asks sooner on. Status None when there is none or it cannot
    prove itself; never raises, for :func:`refresh_verification`'s reason."""
    try:
        record = read_verification(root)
    except Exception:  # noqa: BLE001 — see the docstring
        record = None
    if not isinstance(record, Mapping):
        return {"decided_status": None}
    return {"decided_status": record.get("status"), "decided_version": record.get("contract_version"),
            "decided_symbols": list(record.get("symbols") or [])}


def refresh_verification(*, collector: Any, now: str, root: Path | None = None, adapter: Any = None,
                         clock: Callable[[], float] = time.monotonic) -> str:
    """Verify once and store what was decided. Returns a one-line status for the fire.

    **Never raises**: this runs inside the pipeline fire, a RISK kind, and an exception here would
    take down the loop that manages real positions in order to fail at bookkeeping. Every failure is
    a status line and a moved mark.

    Asks nothing unless live trading is opted in (the live adapter exists only then) and a valid budget
    names the symbols, and nothing in a fire whose market data the venue already rate limited; asks
    through the raw adapter, never the one the API breaker records."""
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
        elif getattr(collector, "rate_limited", None) is not None:
            mark["outcome"] = OUTCOME_RATE_LIMITED
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
    mark.update(_decided(root))
    try:
        _write_json(refresh_mark_path(root), mark, code="VENUE_CONTRACT_MARK_LOCKED",
                    label="venue contract refresh mark")
    except Exception:  # noqa: BLE001 — the line below still says what happened
        pass
    return status_line(mark)


def status_line(mark: Mapping[str, Any]) -> str:
    outcome = mark.get("outcome")
    if outcome in (OUTCOME_DECIDED, OUTCOME_INCOMPLETE):
        checks = mark.get("checks") if isinstance(mark.get("checks"), list) else []
        failed = [str(c.get("check")) for c in checks
                  if isinstance(c, Mapping) and c.get("check") in JUDGED_CHECKS and c.get("result") != STATUS_PASS]
        return f"venue contract: {mark.get('status')}" + (f" ({', '.join(failed)})" if failed else "")
    if outcome == OUTCOME_ERROR:
        return f"venue contract: not verified ({mark.get('error')})"
    return f"venue contract: not verified ({outcome})"



# --- the operator notice (PR4b-2) --------------------------------------------------------------
#
# The doors refuse on the record quietly: a FAIL, a stale or a damaged record shows on the board and
# in each refused decision, and nowhere an operator is told. The pipeline fire says so once, on the
# edge — when what the doors would answer about the record changes, or a FAIL names other checks — and
# nothing while it holds. The told reading moves only once the channel has taken the message; one it
# did not take is kept on the mark as undelivered and said with the next message, even when the
# reading has returned by then (the breaker watch's missed transitions).

NOTICE_FILENAME = "venue_contract_notice.json"
READING_USABLE = "USABLE"
# Undelivered changes kept on the mark, newest last: enough for a channel that is down for hours on a
# venue that flaps, bounded so a mark cannot grow without end.
UNDELIVERED_KEPT = 10

_READING_TEXT = {
    READING_USABLE: "usable - mainnet entries are backed (the stage and every other door still apply)",
    ENTRY_CONTRACT_MISSING: "none recorded - every mainnet entry is refused",
    ENTRY_CONTRACT_UNREADABLE: "UNREADABLE - every mainnet entry is refused; find out who changed the record",
    ENTRY_CONTRACT_VERSION: ("verified under another contract version - every mainnet entry is refused until "
                             "the next fire verifies under this one"),
    ENTRY_CONTRACT_NOT_PASS: ("FAIL - the venue contradicted what the runtime assumes; every mainnet entry is "
                              "refused, and the next fire asks again"),
    ENTRY_CONTRACT_STALE: ("STALE - no usable PASS within six hours of this fire, or one dated ahead of its "
                           "clock; every mainnet entry is refused"),
}


def notice_mark_path(root: Path | None = None) -> Path:
    return venue_state_dir(root, venue=VENUE_MAINNET) / NOTICE_FILENAME


def read_notice_mark(root: Path | None = None) -> dict[str, Any] | None:
    """The reading the operator was last told, or None for absent AND for damaged: a mark nobody can
    read is a reading nobody can say was told, so the next notice is a first report. Never raises."""
    path = notice_mark_path(root)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def entry_reading(root: Path | None = None, *, now: str) -> dict[str, Any]:
    """What the doors would answer about the decided record at ``now``, for a symbol it covers:
    :data:`READING_USABLE` or the refusal's code, with the facts a notice names. Never raises."""
    fact = entry_fact(root)
    refusal = entry_refusal(fact, symbol=None, at=now)
    failed = fact.get("failed_checks")
    return {
        "reading": refusal["reason_code"] if refusal is not None else READING_USABLE,
        "status": fact.get("status"),
        "verified_at": fact.get("verified_at"),
        "contract_version": fact.get("contract_version"),
        "failed_checks": [str(c) for c in failed] if isinstance(failed, list) else [],
        "error": fact.get("error"),
    }


def _names(value: Any) -> list[str]:
    return sorted(str(v) for v in value) if isinstance(value, list) else []


def _moved(told: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """Whether the doors' reading moved since ``told``: another reading, or a FAIL that now names other
    checks — the operator who fixed what the last FAIL named is told the next one's (the breaker watch
    re-announces when the problems it names change)."""
    if told.get("reading") != current.get("reading"):
        return True
    return current.get("reading") == ENTRY_CONTRACT_NOT_PASS and \
        _names(told.get("failed_checks")) != _names(current.get("failed_checks"))


def _undelivered(mark: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    rows = mark.get("undelivered") if isinstance(mark, Mapping) else None
    return [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def render_notice(current: Mapping[str, Any], previous: Mapping[str, Any] | None,
                  *, missed: Sequence[Mapping[str, Any]] = ()) -> str:
    """The operator-facing message. ASCII only, like every other channel render. ``previous`` is the
    reading the operator was last told; ``missed`` the changes the channel did not take since."""
    reading = current.get("reading")
    if previous is None:
        headline = "CRYPTO VENUE CONTRACT - first report"
    elif not _moved(previous, current):
        headline = "CRYPTO VENUE CONTRACT - a change was not delivered when it happened"
    elif reading == READING_USABLE:
        headline = "CRYPTO VENUE CONTRACT USABLE - mainnet entries are backed again"
    elif previous.get("reading") == READING_USABLE:
        headline = "CRYPTO VENUE CONTRACT NOT USABLE - mainnet entries refused"
    else:
        headline = "CRYPTO VENUE CONTRACT - still not usable, for another reason"
    lines = [headline, f"  now      : {_READING_TEXT.get(str(reading), reading)}"]
    if previous is not None:
        lines.append(f"  was      : {previous.get('reading')} (told at {previous.get('announced_at')})")
    for row in missed:
        failed = _names(row.get("failed_checks"))
        lines.append(f"  missed   : {row.get('reading')} at {row.get('at')}"
                     + (f" (failed: {', '.join(failed)})" if failed else "") + " - not delivered then")
    if current.get("error"):
        lines.append(f"  record   : unreadable ({current['error']})")
    elif current.get("status") is not None:
        failed = current.get("failed_checks") or []
        lines.append(f"  record   : {current['status']} at {current.get('verified_at')} "
                     f"({current.get('contract_version')})" + (f", failed: {', '.join(failed)}" if failed else ""))
    lines.append("  scope    : mainnet autonomous entries and the probe; closing, protecting and settling "
                 "never read it")
    lines.append("  see      : docker exec thomas-scheduler python -m scripts.venue_contract --show")
    return "\n".join(lines)


def notice(root: Path | None = None, *, now: str) -> dict[str, Any]:
    """Whether there is something to tell (``changed``): the doors' reading moved since the operator
    was last told, or a change the channel did not take is waiting. The message (``text``) and the mark
    to write once it is delivered (``state``), which clears what was waiting. Never raises."""
    current = entry_reading(root, now=now)
    mark = read_notice_mark(root)
    told = mark if mark is not None and isinstance(mark.get("reading"), str) else None
    missed = _undelivered(mark)
    if missed and not _moved(missed[-1], current):
        missed = missed[:-1]      # the last change not delivered is what "now" says: a retry reads as the original
    changed = told is None or _moved(told, current) or bool(missed)
    return {"changed": changed, "text": render_notice(current, told, missed=missed) if changed else "",
            "state": {**current, "announced_at": now}}


def write_notice_mark(state: Mapping[str, Any], *, root: Path | None = None) -> None:
    """The reading the operator has now been told; what was waiting is cleared with it."""
    _write_json(notice_mark_path(root), state, code="VENUE_CONTRACT_NOTICE_LOCKED",
                label="venue contract notice mark")


def note_undelivered(result: Mapping[str, Any], *, root: Path | None = None) -> None:
    """A notice the channel did not take: its reading kept on the mark as undelivered — the told
    reading unchanged — so the next message says it even when the reading has returned by then.
    Raises on a mark it cannot write, like :func:`write_notice_mark`."""
    state = result.get("state") if isinstance(result, Mapping) else None
    if not isinstance(state, Mapping) or not state.get("reading"):
        return
    mark = read_notice_mark(root) or {}
    row = {"reading": state.get("reading"), "failed_checks": _names(state.get("failed_checks")),
           "at": state.get("announced_at")}
    rows = _undelivered(mark)
    if not rows or (rows[-1].get("reading"), _names(rows[-1].get("failed_checks"))) != \
            (row["reading"], row["failed_checks"]):
        rows.append(row)
    _write_json(notice_mark_path(root), {**mark, "undelivered": rows[-UNDELIVERED_KEPT:]},
                code="VENUE_CONTRACT_NOTICE_LOCKED", label="venue contract notice mark")


__all__ = [
    "CHECK_IDS", "CONTRACT_VERSION", "ENTRY_CONTRACT_CODES", "ENTRY_CONTRACT_MISSING", "ENTRY_CONTRACT_NOT_PASS",
    "ENTRY_CONTRACT_STALE", "ENTRY_CONTRACT_SYMBOL", "ENTRY_CONTRACT_UNREADABLE", "ENTRY_CONTRACT_VERSION",
    "ENTRY_FACT_FIELDS", "JUDGED_CHECKS", "MAX_AGE_SECONDS", "NOT_VERIFIED", "NOTICE_FILENAME", "OBSERVED_CHECKS",
    "READING_USABLE", "REFRESH_AFTER_SECONDS", "RETRY_AFTER_FAIL_SECONDS", "SENTINEL_ID_PREFIX", "UNDELIVERED_KEPT",
    "STATUS_FAIL", "STATUS_PASS", "STATUS_UNVERIFIED", "VENUE_CONTRACT_INVALID", "VENUE_CONTRACT_TAMPERED",
    "VENUE_CONTRACT_UNREADABLE", "build_record", "check_conditional_refused", "check_entry_left_no_order",
    "check_exchange_info", "check_leverage", "check_nothing_resting", "check_position_mode", "covers",
    "entry_fact", "entry_reading", "entry_refusal", "is_due", "judge", "legacy_conditional_probe", "notice",
    "note_undelivered", "notice_mark_path", "read_notice_mark", "read_refresh_mark", "read_verification", "refresh_due",
    "refresh_verification", "render_notice", "run_checks", "status_line", "verification_status",
    "write_notice_mark",
]
