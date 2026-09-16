"""The signed testnet evidence registry (PR1d-1; Thomas decisions 2 and 11, 2026-09-16).

One row per completed testnet cycle, and a cycle is the whole thing: an entry that FILLED and
reconciled, protective legs confirmed RESTING at the algo endpoint, those legs cancelled or the
position closed, and the venue's own position view reconciled afterwards. Thomas chose that
definition over "N reconciled entries" for a reason the history makes plain — 2026-08-02 (both
protective legs refused, cause -4120: conditional orders had moved to the Algo API eight months
earlier), 2026-08-03 (a stop accepted but unfindable, so never cancelled) and 2026-08-05 (an algo
fill whose renamed fields the settle path could not read, 42 cycles stuck) were all failures
*after* the entry. An entry-only rehearsal reproduces none of them; one full cycle reproduces all
three.

The shape of this registry is the retired canary registry's, deliberately (`live_promotion`, PR1r):

- **the venue's own answers are stored, and the verdict is derived** — never a stored ``clean``
  boolean a writer could assert. A row is complete because its parts say so, on every read;
- **a verified read that raises** — a row that fails its self-hash, or a duplicate cycle id, is a
  refusal, not a quietly shorter list. Evidence that cannot prove itself must not be able to
  argue that a machine may trade real money;
- **a missing file is honestly empty**, which is what a machine that has not run a cycle is;
- **the writer is one door and the reader is separate**, so the reader outlives the door.

Where it binds: the stage ladder's climb out of SIGNED_TESTNET names a cycle id, and that id (with
the row's hash) rides in the stage record's evidence — so Thomas's approval signs one specific
cycle, and a record carrying any other reads READ_ONLY. The binding itself is PR1d-2; this module
is what it will read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from ..errors import ToolError
from ..jsonl import iter_numbered
from .state import VENUE_TESTNET, venue_state_dir

EVIDENCE_FILENAME = "signed_testnet_cycles.jsonl"
EVIDENCE_SCHEMA_VERSION = "signed_testnet_cycle.v0.1"
EVIDENCE_PROVENANCE = "mvp_testnet_kernel"

EVIDENCE_UNREADABLE = "TESTNET_EVIDENCE_UNREADABLE"
EVIDENCE_TAMPERED = "TESTNET_EVIDENCE_TAMPERED"
EVIDENCE_DUPLICATE = "TESTNET_EVIDENCE_DUPLICATE"
EVIDENCE_INCOMPLETE = "TESTNET_EVIDENCE_INCOMPLETE"

# What one complete cycle has to show. Each is a fact the venue answered, not a claim the door
# made, and each maps to a live failure an entry-only rehearsal would have missed.
RECONCILED = "RECONCILED"
RESTING_STATUSES = frozenset({"NEW", "PARTIALLY_FILLED"})
# Both halves of a protected position: the conditional stop (the leg the Algo migration moved and
# -4120 refused on 2026-08-02) and the target LIMIT, each at its own endpoint.
REQUIRED_LEGS = ("SL", "TP")
ALGO_LEGS = frozenset({"SL"})
TESTNET_HOSTS = frozenset({"testnet.binancefuture.com"})


def evidence_path(root: Path | None = None) -> Path:
    return venue_state_dir(root, venue=VENUE_TESTNET) / EVIDENCE_FILENAME


def build_cycle_record(
    *,
    cycle_id: str,
    symbol: str,
    entry: Mapping[str, Any],
    protective_legs: Sequence[Mapping[str, Any]],
    exit_result: Mapping[str, Any],
    position_reconciliation: Mapping[str, Any],
    adapter_tool_id: str,
    base_url_host: str,
    started_at: str,
    completed_at: str,
    operator: str | None = None,
    reason: str | None = None,
    failure: str | None = None,
) -> dict[str, Any]:
    """One cycle's row, self-hashed. Stores the venue's answers; judges nothing here."""
    body = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "cycle_id": cycle_id,
        "venue": VENUE_TESTNET,
        "venue_host": base_url_host,
        "provenance": EVIDENCE_PROVENANCE,
        "symbol": symbol,
        "adapter_tool_id": adapter_tool_id,
        # The entry as `submit_and_reconcile` reported it: reconcile status, mismatches, the
        # venue's order id and the fill facts.
        "entry": dict(entry),
        # Each protective leg as the venue answered it, including the status that says RESTING
        # and whether the cancel afterwards found it.
        "protective_legs": [dict(leg) for leg in protective_legs],
        "exit": dict(exit_result),
        "position_reconciliation": dict(position_reconciliation),
        # Who ran it and why, because a row outlives the argv that made it; and where it stopped
        # when it stopped, because an interrupted cycle is evidence of what happened.
        "operator": operator,
        "reason": reason,
        "failure": failure,
        "started_at": started_at,
        "completed_at": completed_at,
    }
    body["record_sha256"] = integrity.sha256_record(body)
    return body


def cycle_findings(record: Mapping[str, Any]) -> list[str]:
    """Why this row is NOT a complete cycle, derived from what the venue answered. Empty means
    complete. Derived on every read: a stored verdict is a claim, and a claim is what the retired
    canary registry's ">= 3 clean" gate turned out to be counting."""
    findings: list[str] = []
    entry = record.get("entry") if isinstance(record.get("entry"), Mapping) else {}
    if entry.get("reconcile_status") != RECONCILED:
        findings.append(f"entry did not reconcile ({entry.get('reconcile_status')})")
    if entry.get("mismatches"):
        findings.append(f"entry reconciled with mismatches ({entry.get('mismatches')})")
    if record.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        findings.append(f"row is not {EVIDENCE_SCHEMA_VERSION}")
    if record.get("venue") != VENUE_TESTNET or record.get("venue_host") not in TESTNET_HOSTS:
        findings.append(f"row is not from the testnet venue ({record.get('venue')} / {record.get('venue_host')})")
    if record.get("failure"):
        findings.append(f"the cycle stopped at {record.get('failure')}")
    legs = record.get("protective_legs")
    legs = [leg for leg in legs if isinstance(leg, Mapping)] if isinstance(legs, list) else []
    by_name = {str(leg.get("leg")): leg for leg in legs}
    # Both legs by name, not "at least one": a cycle that placed a stop and no target proves half
    # of what a protected position needs, and `len(legs) >= 1` read that as the whole thing
    # (review of #877).
    for required in REQUIRED_LEGS:
        if required not in by_name:
            findings.append(f"no {required} leg was placed (the 2026-08-02 failure is exactly here)")
    for name, leg in sorted(by_name.items()):
        if leg.get("observed_status") not in RESTING_STATUSES:
            findings.append(f"protective leg {name} was not confirmed resting ({leg.get('observed_status')})")
        # The conditional leg is the one the Algo migration moved and the one -4120 refused; the
        # target is a plain LIMIT and must NOT claim the algo endpoint.
        if bool(leg.get("algo")) is not (name in ALGO_LEGS):
            findings.append(
                f"protective leg {name} reports algo={leg.get('algo')}, which is not where a "
                f"{leg.get('order_type')} belongs"
            )
        if leg.get("withdrawn") is not True:
            findings.append(f"protective leg {name} was not withdrawn afterwards")
    exit_result = record.get("exit") if isinstance(record.get("exit"), Mapping) else {}
    if exit_result.get("reconcile_status") != RECONCILED:
        findings.append(f"exit did not reconcile ({exit_result.get('reconcile_status')})")
    if not exit_result.get("reduce_only"):
        findings.append("exit was not reduceOnly")
    reconciliation = record.get("position_reconciliation")
    reconciliation = reconciliation if isinstance(reconciliation, Mapping) else {}
    if reconciliation.get("status") != RECONCILED:
        findings.append(f"the position view did not reconcile ({reconciliation.get('status')})")
    return findings


def _recorded_ids(path: Path) -> set[str]:
    """Every cycle id already on disk, read raw. Never raises: this answers "have I written this
    one already", not "is this history trustworthy"."""
    ids: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("cycle_id"), str):
                ids.add(row["cycle_id"])
    except OSError:
        return ids
    return ids


def read_cycles(root: Path | None = None) -> list[dict[str, Any]]:
    """Every recorded cycle, oldest first — a VERIFIED read.

    Missing file = honestly empty (no cycle has run here). Anything unreadable, failing its
    self-hash, or duplicated raises: evidence that cannot prove itself must not be allowed to
    argue that this machine may climb to LIVE_AUTONOMOUS."""
    path = evidence_path(root)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lineno, record in iter_numbered(
        path, read_code=EVIDENCE_UNREADABLE, label="signed testnet evidence", exc_type=ToolError,
    ):
        if not isinstance(record, dict):
            continue
        stored = record.get("record_sha256")
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        try:
            recomputed = integrity.sha256_record(body)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ToolError(EVIDENCE_UNREADABLE, f"signed testnet evidence line {lineno} cannot be hashed: {exc}") from None
        if not isinstance(stored, str) or recomputed != stored:
            raise ToolError(EVIDENCE_TAMPERED, f"signed testnet evidence line {lineno} fails its self-hash")
        cycle_id = record.get("cycle_id")
        if isinstance(cycle_id, str) and cycle_id:
            if cycle_id in seen:
                raise ToolError(EVIDENCE_DUPLICATE, f"duplicate signed testnet cycle_id: {cycle_id}")
            seen.add(cycle_id)
        rows.append(record)
    return rows


def complete_cycles(root: Path | None = None) -> list[dict[str, Any]]:
    """The recorded cycles that are complete, judged on every read (never on a stored flag)."""
    return [row for row in read_cycles(root) if not cycle_findings(row)]


def find_cycle(cycle_id: str, root: Path | None = None) -> dict[str, Any] | None:
    """One recorded cycle by id, verified — or None. Raises what `read_cycles` raises."""
    for row in read_cycles(root):
        if row.get("cycle_id") == cycle_id:
            return row
    return None


def assert_complete_cycle(cycle_id: str, root: Path | None = None) -> dict[str, Any]:
    """The row for ``cycle_id``, or a typed refusal naming what is missing. What the ladder's
    climb out of SIGNED_TESTNET will call (PR1d-2)."""
    row = find_cycle(cycle_id, root)
    if row is None:
        raise ToolError(EVIDENCE_INCOMPLETE, f"no signed testnet cycle {cycle_id} is recorded here")
    findings = cycle_findings(row)
    if findings:
        raise ToolError(EVIDENCE_INCOMPLETE,
                        f"signed testnet cycle {cycle_id} is not complete: {'; '.join(findings)}")
    return row


def append_cycle(record: Mapping[str, Any], root: Path | None = None) -> Path:
    """Append one cycle row. The door's write; idempotent on ``cycle_id``."""
    path = evidence_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Deliberately a raw scan, not `find_cycle`: the verified read raises on an earlier bad row,
    # and a door that has already reached the venue must still be able to write down what it did
    # (review of #877). The strictness stays where it belongs — on every READ of the history.
    if str(record.get("cycle_id")) in _recorded_ids(path):
        return path
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")
        handle.flush()
    return path


def evidence_rows(root: Path | None = None) -> list[dict[str, Any]]:
    """The board's view: one line per recorded cycle with what it proves and what it does not."""
    rows = []
    for row in read_cycles(root):
        findings = cycle_findings(row)
        rows.append({
            "cycle_id": row.get("cycle_id"),
            "symbol": row.get("symbol"),
            "completed_at": row.get("completed_at"),
            "complete": not findings,
            "findings": findings,
        })
    return rows
