"""The operator's own answer to Gate 0 (``crypto_live_candidate_ack.v0.1``).

``docs/runtime-contracts/CRYPTO_LIVE_EXECUTION_V0.1.md`` states Gate 0 as an item on the
**operator** go-live checklist — *"Paper trading by this runtime shows positive expectancy over
a sustained window. Check with `python -m runtime.mvp_runtime.crypto.dashboard`"* — under a
heading that reads *"Every step is Thomas's."* #400 found that `feedback.live_candidate_eligible`
computed that judgement every cycle and that nothing read it, and #409 wired it into
`live_entry` as a refusal. That closed a real gap and, in closing it, moved a checklist item
from a person to a pool-wide aggregate.

The aggregate cannot answer the question it is asked. Measured 2026-08-01: **all 86 rows holding
Gate 0 shut came from lineages the `lifecycle` ladder had already SUSPENDED, and zero from one
that could still route** — the ladder had done its job, and the losses of strategies that no
longer exist were still gating a real-money door. #411 scoped the population to routable
lineages, which fixes that specific defect and leaves the shape: one number, over a whole pool,
refusing every strategy's entries for any strategy's record.

**This module does not remove the gate.** It gives the operator a governed way to answer it, in
the same idiom `risk_limits` and `live_budget` already use for other checklist items:

1. **Nothing registered = today's behaviour exactly.** The computed answer stands, unchanged and
   uncontested. The override is opt-in and absent by default.
2. **The measurement is never overwritten.** `feedback.build_performance_report` still computes
   `live_candidate_eligible` and the cycle records both — what the evidence said, and what the
   operator signed. A record that hid the disagreement would be worse than no record.
3. **An acknowledgement is bound to the routable set it was signed for.** It applies only while
   that set is EXACTLY what it names, so promoting a strategy or letting the ladder demote one
   voids it. A signature for one pool cannot silently authorise the next one — the same
   auto-invalidation rule #405 states for the drawdown baseline, and for the same reason.
4. **Acknowledgements expire.** A checklist tick is a judgement about a moment. Past
   ``valid_until`` the gate returns to its computed answer rather than staying open.
5. **A record that cannot prove itself does not apply.** Unreadable, tampered, not schema-valid,
   outside its window, or naming a different pool — every one of those means "no operator
   answer is available", and the computed answer is what stands. Note the direction: unlike
   `risk_limits`, a bad record here fails toward the *stricter* of the two answers, because the
   thing it would otherwise authorise is a live entry.

Registering one **grants no permission by itself.** The `live_trading` grant, the confirmation
phrase, the canary evidence, the registered budget, the loss breakers over live outcomes and
both kill switches all still stand exactly where they stood.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from ..errors import ToolError
from ..paths import repo_root as _repo_root
from ..schema_cache import validate_against_schema
from .live_pnl import state_dir

ACK_SCHEMA_VERSION = "crypto_live_candidate_ack.v0.1"
ACK_SCHEMA_FILE = "crypto_live_candidate_ack.v0.1.schema.json"
ACK_FILENAME = "crypto_live_candidate_ack.json"

ACK_INVALID = "CRYPTO_LIVE_CANDIDATE_ACK_INVALID"
ACK_UNREADABLE = "CRYPTO_LIVE_CANDIDATE_ACK_UNREADABLE"
ACK_TAMPERED = "CRYPTO_LIVE_CANDIDATE_ACK_TAMPERED"

# Why a stored acknowledgement did not apply this cycle. Reported, never raised: the computed
# answer is always available, so an unusable acknowledgement degrades to "no override" rather
# than blocking a cycle — and it must say which of these it was, because "the operator has not
# signed" and "the operator signed for a pool that no longer exists" are different facts.
NOT_REGISTERED = "not_registered"
EXPIRED = "outside_validity_window"
POOL_CHANGED = "routable_pool_changed_since_signature"
UNUSABLE = "record_unusable"


def _schema_path(repo_root: Path | None = None) -> Path:
    return (repo_root if repo_root is not None else _repo_root()) / "schemas" / ACK_SCHEMA_FILE


def ack_path(root: Path | None = None) -> Path:
    """The per-machine acknowledgement file (gitignored, beside the registered risk limits)."""
    return state_dir(root) / ACK_FILENAME


def build_ack_record(
    *,
    acknowledged_strategy_ids: Sequence[str],
    reason: str,
    valid_from: str,
    valid_until: str,
    registered_by: str,
    registered_at: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build a self-hashed, schema-valid acknowledgement. Fail-closed.

    Raises ``ToolError(ACK_INVALID)`` on an empty or duplicated strategy set, a missing reason,
    a missing operator identity, or a window that does not open before it closes.
    """
    ids = [str(s).strip() for s in (acknowledged_strategy_ids or []) if str(s).strip()]
    if not ids:
        raise ToolError(ACK_INVALID, "acknowledged_strategy_ids must name at least one strategy")
    deduped = sorted(dict.fromkeys(ids))
    if len(deduped) != len(ids):
        raise ToolError(ACK_INVALID, "acknowledged_strategy_ids contains duplicates")
    if not (isinstance(reason, str) and reason.strip()):
        raise ToolError(
            ACK_INVALID,
            "reason is required — this record is a person overriding a measurement that says "
            "otherwise, and it has to say on what basis",
        )
    if not (isinstance(valid_from, str) and isinstance(valid_until, str) and valid_from < valid_until):
        raise ToolError(
            ACK_INVALID, f"validity window must open before it closes: {valid_from} .. {valid_until}"
        )
    if not (isinstance(registered_by, str) and registered_by.strip()):
        raise ToolError(ACK_INVALID, "registered_by (operator identity) is required")

    body: dict[str, Any] = {
        "schema_version": ACK_SCHEMA_VERSION,
        # Seeded from the POOL and the window, so re-signing the same pool for the same window
        # is idempotent and signing a different pool is a different record.
        "acknowledgement_id": integrity.short_id(
            "livecandack",
            {"ids": deduped, "valid_from": valid_from, "valid_until": valid_until,
             "registered_by": registered_by.strip()},
        ),
        "acknowledged_strategy_ids": deduped,
        "reason": reason.strip(),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "registered_by": registered_by.strip(),
        "registered_at": registered_at,
    }
    body["record_sha256"] = integrity.sha256_record(body)
    _validate(body, repo_root=repo_root)
    return body


def _validate(record: Mapping[str, Any], *, repo_root: Path | None = None) -> None:
    try:
        validate_against_schema(dict(record), _schema_path(repo_root), "crypto live-candidate ack")
    except RuntimeSchemaError as exc:
        raise ToolError(ACK_INVALID, f"acknowledgement is not schema-valid: {exc}") from exc


def read_ack(root: Path | None = None) -> dict[str, Any] | None:
    """The registered acknowledgement, VERIFIED — or None when none is registered.

    Missing file = honestly None. Anything unparseable, failing its self-hash, or not
    schema-valid raises, because a record that cannot prove itself must not be allowed to
    authorise anything."""
    path = ack_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(ACK_UNREADABLE, f"live-candidate acknowledgement is unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise ToolError(ACK_UNREADABLE, "live-candidate acknowledgement is not a JSON object")
    stored = data.get("record_sha256")
    body = {k: v for k, v in data.items() if k != "record_sha256"}
    if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
        raise ToolError(ACK_TAMPERED, "live-candidate acknowledgement fails its self-hash")
    _validate(data)
    return data


def resolve_ack(
    root: Path | None = None,
    *,
    now: str,
    routable_strategy_ids: set[str] | None,
) -> dict[str, Any]:
    """``{"applies": bool, "reason": str, "record": dict | None}``. Never raises.

    Reports rather than raising, and that is the whole posture: the computed Gate 0 answer is
    always available, so an acknowledgement that cannot be used degrades to "no override" — the
    stricter of the two answers — instead of blocking a cycle. Every non-applying case names
    itself, because "nobody signed" and "somebody signed for a pool that no longer exists" want
    different responses from an operator.

    ``routable_strategy_ids=None`` means the pool could not be read, so the set the signature was
    for cannot be confirmed and the acknowledgement does not apply.
    """
    try:
        record = read_ack(root)
    except ToolError as exc:
        return {"applies": False, "reason": UNUSABLE, "error": exc.reason_code, "record": None}
    if record is None:
        return {"applies": False, "reason": NOT_REGISTERED, "error": None, "record": None}
    if not (record["valid_from"] <= now <= record["valid_until"]):
        return {"applies": False, "reason": EXPIRED, "error": None, "record": record}
    if routable_strategy_ids is None:
        return {"applies": False, "reason": POOL_CHANGED, "error": None, "record": record}
    # EXACT set equality, not a subset test. A subset would let a signature for two strategies
    # authorise a pool that later gained a third nobody looked at, which is the failure this
    # binding exists to prevent — and it would do it silently, since the pool file changes
    # without anything re-reading this record.
    if set(record["acknowledged_strategy_ids"]) != set(routable_strategy_ids):
        return {"applies": False, "reason": POOL_CHANGED, "error": None, "record": record}
    return {"applies": True, "reason": "acknowledged", "error": None, "record": record}


def write_ack(record: Mapping[str, Any], *, root: Path | None = None) -> Path:
    """Persist a built acknowledgement to the per-machine state dir."""
    path = ack_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(record), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


__all__ = [
    "ACK_INVALID",
    "ACK_SCHEMA_VERSION",
    "ACK_TAMPERED",
    "ACK_UNREADABLE",
    "EXPIRED",
    "NOT_REGISTERED",
    "POOL_CHANGED",
    "UNUSABLE",
    "ack_path",
    "build_ack_record",
    "read_ack",
    "resolve_ack",
    "write_ack",
]
