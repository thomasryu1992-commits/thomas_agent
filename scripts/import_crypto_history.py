#!/usr/bin/env python3
"""Operator tool: one-time audited import of crypto_AI_System's accumulated evidence (C7).

Reads the source repo's registries READ-ONLY and imports, with provenance marking:

- ``storage/registries/outcome_feedback_registry.jsonl`` → the paper outcome store
  (``.runtime_governance_state/crypto/paper_outcomes.jsonl``) — the closed paper
  outcomes the C4 risk guard and C6 feedback read.
- ``storage/registries/counterfactual_outcome_registry.jsonl`` → a SEPARATE
  ``counterfactual_outcomes.jsonl`` — shadow trades must never be counted by the risk
  guard as real losses, so "same store, kind-tagged" is implemented as same state
  directory, distinct file, ``kind`` stamped on every record.
- ``storage/latest/active_strategy_pool.json`` → the strategy CANDIDATES store. The
  ACTIVE pool is deliberately NOT installed by default: re-establishing it is an
  explicit operator decision (``--activate-pool``), the pre-R10 promotion posture —
  never a silent carry-over.

Rules (contract §data-migration): idempotent (already-imported ids are skipped on
re-run), read-only toward the source, every imported record keeps its original id and
hashes and gains ``provenance: crypto_ai_system_import`` + the import batch id, and
the import itself is recorded as a control-ledger event (the safety-flag-activation
precedent for operator actions with no originating task) with counts + source file
hashes. Requires ``--confirm`` to write anything; without it, a dry-run report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.read_only_kernel import integrity  # noqa: E402
from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.crypto import pool as pool_store  # noqa: E402
from runtime.mvp_runtime.crypto.paper import OUTCOMES_FILENAME, read_outcomes, state_dir  # noqa: E402
from runtime.mvp_runtime.crypto.strategy import SpecParseError, load_strategy_pool  # noqa: E402
from runtime.mvp_runtime.events import stamped_event  # noqa: E402
from runtime.mvp_runtime.filelock import locked  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402

IMPORT_EVENT_TYPE = "crypto_import_event.v0"
PROVENANCE = "crypto_ai_system_import"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3

OUTCOME_REGISTRY_REL = "storage/registries/outcome_feedback_registry.jsonl"
COUNTERFACTUAL_REGISTRY_REL = "storage/registries/counterfactual_outcome_registry.jsonl"
POOL_REL = "storage/latest/active_strategy_pool.json"
COUNTERFACTUALS_FILENAME = "counterfactual_outcomes.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise SystemExit(f"BLOCKED: {path.name} line {i + 1} is not valid JSON: {exc}")
        if isinstance(record, dict):
            rows.append(record)
    return rows


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _mark(record: dict[str, Any], *, kind: str, batch_id: str) -> dict[str, Any]:
    marked = dict(record)
    marked["provenance"] = PROVENANCE
    marked["import_batch_id"] = batch_id
    marked["kind"] = kind
    return marked


def _append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="IMPORT_STORE_LOCKED", label=path.name):
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_import(
    *, source: Path, root: Path | None = None, confirm: bool = False,
    activate_pool: bool = False, now: str | None = None,
) -> dict[str, Any]:
    """Perform (or dry-run) the import. Returns the summary the event records."""
    now = now or timeutil.utc_now_iso()
    outcome_src = source / OUTCOME_REGISTRY_REL
    counter_src = source / COUNTERFACTUAL_REGISTRY_REL
    pool_src = source / POOL_REL
    for path in (outcome_src, counter_src, pool_src):
        if not path.is_file():
            raise SystemExit(f"BLOCKED: source file missing: {path}")

    batch_id = integrity.short_id(
        "crypto_import", {"outcomes": _file_sha256(outcome_src), "counterfactuals": _file_sha256(counter_src),
                          "pool": _file_sha256(pool_src), "at": now}
    )

    source_outcomes = _read_jsonl(outcome_src)
    source_counterfactuals = _read_jsonl(counter_src)
    raw_pool = json.loads(pool_src.read_text(encoding="utf-8"))
    try:
        pool_specs = load_strategy_pool(raw_pool)
    except SpecParseError as exc:
        raise SystemExit(f"BLOCKED: source strategy pool failed validation: {exc}")

    # Idempotency: skip records whose original ids are already in the destination.
    existing_outcome_ids = {r.get("outcome_feedback_registry_record_id") or r.get("outcome_id")
                            for r in read_outcomes(root)}
    counter_path = state_dir(root) / COUNTERFACTUALS_FILENAME
    existing_counter_ids = set()
    if counter_path.is_file():
        existing_counter_ids = {r.get("counterfactual_id") or r.get("outcome_id")
                                for r in _read_jsonl(counter_path)}
    existing_candidate_ids = {r.get("strategy_id") for r in pool_store.read_candidates(root)
                              if r.get("provenance") == PROVENANCE}

    new_outcomes = [
        _mark(r, kind="outcome", batch_id=batch_id) for r in source_outcomes
        if (r.get("outcome_feedback_registry_record_id") or r.get("outcome_id")) not in existing_outcome_ids
    ]
    new_counterfactuals = [
        _mark(r, kind="counterfactual", batch_id=batch_id) for r in source_counterfactuals
        if (r.get("counterfactual_id") or r.get("outcome_id")) not in existing_counter_ids
    ]
    new_candidates = [
        _mark(dict(entry), kind="strategy_candidate", batch_id=batch_id)
        for entry in (raw_pool.get("active_strategies") or [])
        if entry.get("strategy_id") not in existing_candidate_ids
    ]

    summary = {
        "import_batch_id": batch_id,
        "source": str(source),
        "source_hashes": {
            "outcome_registry": _file_sha256(outcome_src),
            "counterfactual_registry": _file_sha256(counter_src),
            "active_strategy_pool": _file_sha256(pool_src),
        },
        "outcomes_total": len(source_outcomes),
        "outcomes_imported": len(new_outcomes),
        "counterfactuals_total": len(source_counterfactuals),
        "counterfactuals_imported": len(new_counterfactuals),
        "pool_strategies_total": len(pool_specs),
        "candidates_imported": len(new_candidates),
        "pool_activated": False,
        "confirmed": confirm,
        "created_at": now,
    }
    if not confirm:
        return summary

    _append_jsonl(state_dir(root) / OUTCOMES_FILENAME, new_outcomes)
    _append_jsonl(counter_path, new_counterfactuals)
    if new_candidates:
        pool_store.append_candidates(new_candidates, root=root)
    if activate_pool:
        # The explicit operator decision that re-establishes the active pool
        # (pre-R10 promotion posture). Marked so the pool says where it came from.
        installed = dict(raw_pool)
        installed["provenance"] = PROVENANCE
        installed["import_batch_id"] = batch_id
        installed["activated_by"] = "operator_import"
        installed["activated_at"] = now
        summary["pool_activated"] = bool(pool_store.install_active_pool(installed, root=root))

    ledger = LedgerStore((root if root is not None else ROOT) / LEDGER_REL)
    ledger.append_control(stamped_event(IMPORT_EVENT_TYPE, **summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-time audited import of crypto_AI_System history.")
    parser.add_argument("--source", required=True, help="crypto_AI_System repo root (read-only)")
    parser.add_argument("--confirm", action="store_true", help="actually write; without it, dry-run report")
    parser.add_argument("--activate-pool", action="store_true",
                        help="ALSO install the imported pool as the ACTIVE pool (explicit operator decision)")
    args = parser.parse_args(argv)

    if args.activate_pool and not args.confirm:
        print("BLOCKED: --activate-pool requires --confirm")
        return EXIT_USAGE

    summary = run_import(source=Path(args.source), confirm=args.confirm, activate_pool=args.activate_pool)
    mode = "IMPORTED" if args.confirm else "DRY-RUN (nothing written; re-run with --confirm)"
    print(f"{mode}: batch {summary['import_batch_id']}")
    for key in ("outcomes_imported", "counterfactuals_imported", "candidates_imported", "pool_activated"):
        print(f"  {key:24}: {summary[key]}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
