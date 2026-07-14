from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from gate_matrix import GATE_DEFINITIONS, GATE_SCOPE_ORDER
from lib.gate_runner import run_matrix


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "generated/release_gate"


def run_scope(scope: str, *, check_only: bool) -> None:
    if scope not in GATE_DEFINITIONS:
        raise ValueError(f"unknown gate scope: {scope}")

    definition = GATE_DEFINITIONS[scope]
    evidence_path = None
    if not check_only:
        evidence_path = EVIDENCE_ROOT / str(definition["evidence_filename"])

    run_matrix(
        root=ROOT,
        checks=definition["checks"],
        gate_id=str(definition["gate_id"]),
        evidence_path=evidence_path,
    )

    print(f"\nPASS: {definition['display_name']}")
    print(definition["no_authority_message"])


def run_scopes(scopes: Iterable[str], *, check_only: bool) -> None:
    for scope in scopes:
        run_scope(scope, check_only=check_only)


def _check_only_parser(*, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--check-only", action="store_true")
    return parser


def main_for_scope(scope: str) -> int:
    definition = GATE_DEFINITIONS[scope]
    parser = _check_only_parser(description=str(definition["description"]))
    args = parser.parse_args()
    run_scope(scope, check_only=args.check_only)
    return 0


def main_for_all() -> int:
    parser = _check_only_parser(
        description="Run Active, Deferred, and Legacy Compatibility Gates as separate scopes."
    )
    args = parser.parse_args()
    run_scopes(GATE_SCOPE_ORDER, check_only=args.check_only)
    print("\nPASS: Thomas Agent All Architecture Gate Scopes")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a canonical Thomas Agent validation scope without merging its authority boundary "
            "with other scopes."
        )
    )
    parser.add_argument(
        "--scope",
        choices=(*GATE_SCOPE_ORDER, "all"),
        default="active",
        help="Validation scope. Defaults to active.",
    )
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    if args.scope == "all":
        run_scopes(GATE_SCOPE_ORDER, check_only=args.check_only)
        print("\nPASS: Thomas Agent All Architecture Gate Scopes")
    else:
        run_scope(args.scope, check_only=args.check_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
