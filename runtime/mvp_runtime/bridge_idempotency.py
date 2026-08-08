"""At-most-once for the assistant doors: one ``request_id``, one effect, however often it arrives.

The doors answer one request per connection and close, and the client is a model with a
timeout. A request that times out on the client's side has not necessarily failed on this
side — a dispatch may be halfway through a pipeline run, a switch spend may be inside its
approval lock — so the honest thing for a client to do after a timeout is retry, and the honest
thing for it to retry with is the same request. Without an identity on the frame, "the same
request" and "a second request" are indistinguishable here, and the door runs the effect twice:
two analyses billed to the same shared quota, or a resume applied against a grant that was
supposed to be single-use.

``request_id`` is optional. A frame without one behaves exactly as it did before this module
existed — no bookkeeping, no file, no lock. That is deliberate: making it mandatory would break
every existing caller in the failing-closed direction for a property most callers do not need,
and a door that refuses a well-formed frame for want of a field it never used to want is a
worse outage than the duplicate it prevents.

**Three states, append-only, latest row wins.** ``claimed`` before the effect, then
``completed`` with the outcome, or ``released`` if the effect refused. The claim is what makes
this correct under concurrency rather than merely usually right: two frames carrying one id
race for the same lock, the loser sees a live claim and is refused rather than executing
alongside the winner.

**The window this does not close, stated plainly.** The effect runs between the claim and the
completion, so a process that dies mid-effect leaves a claim with no outcome. A retry inside
``CLAIM_TTL_SECONDS`` is refused (``REQUEST_IN_FLIGHT``); after it the id is claimable again and
the effect can run a second time. Closing that window entirely means the effect and the record
committing together, which is a transaction across two stores that this deployment does not
have. What is bought here is the ordinary case — a timeout, a retry, a client that reconnects —
and what is left is a crash window that is bounded, visible in the file, and never silent.

**Why the read door is not wired to this.** ``read_bridge`` has no effect to duplicate: its verb
table is reads, it writes no control state, and answering the same read twice is answering the
same read twice. Idempotency there would be bookkeeping with nothing to protect.
"""

from __future__ import annotations

from typing import Any, Mapping

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.integrity import IntegrityError

from . import jsonl, timeutil
from .errors import ControlBlocked
from .store import BRIDGE_REQUESTS_FILE, LedgerStore

# The frame key this module owns. Each door names it in its own allowed-key set, so a door that
# has not been wired to this still refuses it rather than ignoring it.
REQUEST_ID_KEY = "request_id"

# Long enough for a uuid or a ULID with a prefix; short enough that the id cannot be used as a
# payload. The cap is on the id, not the frame — `socket_door.MAX_FRAME_BYTES` bounds that.
MAX_REQUEST_ID_LENGTH = 128

STATE_CLAIMED = "claimed"
STATE_COMPLETED = "completed"
STATE_RELEASED = "released"

# How long a claim holds the id when nothing completes it. This is the crash window: it must be
# longer than the slowest effect a door can run (a dispatch is a full pipeline run) or a slow
# request would be refused as in-flight by its own retry, and short enough that a dead process
# does not hold an id all day.
CLAIM_TTL_SECONDS = 15 * 60

# How long a completed id keeps answering with its recorded outcome. A client that retries at
# all retries within minutes; a day is the generous end of that and still far inside the
# rotation horizon `retention` gives this file (see the note beside ROTATABLE_FILES — the TTL
# being shorter than the rotation horizon is what makes rotating this file safe).
COMPLETED_TTL_SECONDS = 24 * 60 * 60

_LABEL = "the bridge request ledger"


def request_id_of(request: Mapping[str, Any]) -> str | None:
    """The frame's ``request_id``, validated, or ``None`` when it carries none.

    Malformed rather than absent is a refusal: a caller that sent ``request_id: 42`` believed
    it was getting at-most-once, and silently treating that as "no id" would give it the
    opposite of what it asked for at exactly the moment it mattered.
    """
    raw = request.get(REQUEST_ID_KEY)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ControlBlocked(
            "MALFORMED_REQUEST", f"{REQUEST_ID_KEY!r} must be a non-empty string when given",
        )
    value = raw.strip()
    if len(value) > MAX_REQUEST_ID_LENGTH:
        raise ControlBlocked(
            "MALFORMED_REQUEST",
            f"{REQUEST_ID_KEY!r} is capped at {MAX_REQUEST_ID_LENGTH} characters; "
            f"an id is a name, not a payload",
        )
    return value


def fingerprint(request: Mapping[str, Any]) -> str:
    """Hash everything in the frame **except** the id.

    This is what makes a reused id detectable instead of dangerous. Without it, a second frame
    carrying a spent id and different parameters would be answered with the first frame's
    outcome — the door reporting success for something it never did.
    """
    payload = {key: value for key, value in request.items() if key != REQUEST_ID_KEY}
    try:
        return integrity.sha256_record(payload)
    except IntegrityError as exc:
        raise ControlBlocked("MALFORMED_REQUEST", f"this frame cannot be fingerprinted: {exc}") from exc


def claim(
    ledger: LedgerStore,
    *,
    door: str,
    request_id: str,
    request_fingerprint: str,
    now: str,
) -> dict[str, Any] | None:
    """Take the id for this effect, or say what already happened to it.

    ``None`` means the caller owns the id and must go on to ``complete`` or ``release`` it.
    A returned record means the effect already ran; the caller answers with ``replay_reply``
    and applies nothing.

    Call this **after** the frame has been validated and **immediately before** the effect. A
    claim taken before validation would burn an id on a request that was going to be refused
    anyway, and the caller could never legitimately retry it.
    """
    with ledger.file_lock(BRIDGE_REQUESTS_FILE, label=_LABEL):
        prior = _live_record(ledger, door=door, request_id=request_id, now=now)
        if prior is not None:
            if prior.get("request_sha256") != request_fingerprint:
                raise ControlBlocked(
                    "REQUEST_ID_REUSED",
                    f"request_id {request_id!r} was already used at this door for a different "
                    f"request; nothing was applied. Use a fresh id",
                )
            if prior.get("state") == STATE_COMPLETED:
                return prior
            if prior.get("state") == STATE_CLAIMED:
                raise ControlBlocked(
                    "REQUEST_IN_FLIGHT",
                    f"request_id {request_id!r} is already being applied at this door; "
                    f"nothing was applied again",
                )
        _append(
            ledger,
            _row(
                door=door, request_id=request_id, request_fingerprint=request_fingerprint,
                state=STATE_CLAIMED, now=now, ttl_seconds=CLAIM_TTL_SECONDS,
            ),
        )
    return None


def complete(
    ledger: LedgerStore,
    *,
    door: str,
    request_id: str,
    request_fingerprint: str,
    outcome: Mapping[str, Any],
    now: str,
) -> None:
    """Record that the effect ran, and what it produced.

    ``outcome`` is the *identity* of what happened (a task id, a control action, an approval
    id) and never the payload. A replayed answer should be small and stable; the payload is
    already in the ledger and the read door already serves it.
    """
    with ledger.file_lock(BRIDGE_REQUESTS_FILE, label=_LABEL):
        _append(
            ledger,
            _row(
                door=door, request_id=request_id, request_fingerprint=request_fingerprint,
                state=STATE_COMPLETED, now=now, ttl_seconds=COMPLETED_TTL_SECONDS,
                outcome=dict(outcome),
            ),
        )


def release(
    ledger: LedgerStore,
    *,
    door: str,
    request_id: str,
    request_fingerprint: str,
    now: str,
) -> None:
    """Give the id back after a refusal, so a corrected retry is not blocked by the failure.

    A refused effect changed nothing, so holding its id until the claim expires would punish a
    caller for a request the door itself rejected.
    """
    with ledger.file_lock(BRIDGE_REQUESTS_FILE, label=_LABEL):
        _append(
            ledger,
            _row(
                door=door, request_id=request_id, request_fingerprint=request_fingerprint,
                state=STATE_RELEASED, now=now, ttl_seconds=0,
            ),
        )


def replay_reply(prior: Mapping[str, Any]) -> dict[str, Any]:
    """The answer to a repeat of an id that already ran.

    ``ok`` is **true**, and that is the whole point rather than a detail: the caller's intent
    is applied, and an ``ok: false`` here would read as a failure and invite the retry-with-a-
    fresh-id that produces the second effect this module exists to prevent. ``replayed``
    carries the distinction for a caller that cares.
    """
    return {
        "ok": True,
        "replayed": True,
        "request_id": prior.get("request_id"),
        "applied_at": prior.get("recorded_at"),
        "reason": (
            f"request_id {prior.get('request_id')!r} was already applied at "
            f"{prior.get('recorded_at')}; nothing was applied again"
        ),
        "outcome": prior.get("outcome") or {},
    }


def _row(
    *,
    door: str,
    request_id: str,
    request_fingerprint: str,
    state: str,
    now: str,
    ttl_seconds: int,
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "record_type": "bridge_request.v0",
        "door": door,
        "request_id": request_id,
        "request_sha256": request_fingerprint,
        "state": state,
        "recorded_at": now,
        "expires_at": timeutil.plus_seconds(now, ttl_seconds),
    }
    if outcome is not None:
        row["outcome"] = outcome
    return row


def _append(ledger: LedgerStore, row: Mapping[str, Any]) -> None:
    """Caller must hold this file's lock — the check and the record are one critical section."""
    jsonl.append_lines(
        ledger.root / BRIDGE_REQUESTS_FILE, [dict(row)],
        write_code="LEDGER_WRITE_FAILED", label=_LABEL,
    )


def _live_record(
    ledger: LedgerStore, *, door: str, request_id: str, now: str,
) -> dict[str, Any] | None:
    """The current row for this ``(door, request_id)``, or ``None`` if there is none or it lapsed.

    The **last** matching row is the state, and expiry is applied to that row alone — never as
    a filter while scanning. Filtering first looks equivalent and is not: ``release`` writes a
    row that is expired the moment it lands, so a scan that skipped expired rows would fall
    back to the ``claimed`` row behind it and report a released id as still in flight for the
    rest of the claim's TTL, which is precisely the state ``release`` exists to end.

    Expiry is compared as text, which the fixed UTC format makes correct: ``timeutil``'s stamps
    are zero-padded and second-resolution, so lexical order is chronological order — the same
    comparison the scheduler makes against ``next_run_at``.
    """
    rows = jsonl.read_objects(
        ledger.root / BRIDGE_REQUESTS_FILE, read_code="LEDGER_READ_FAILED", label=_LABEL,
    )
    latest: dict[str, Any] | None = None
    for row in rows:
        if row.get("door") != door or row.get("request_id") != request_id:
            continue
        latest = row
    if latest is None or str(latest.get("expires_at", "")) <= now:
        return None
    return latest
