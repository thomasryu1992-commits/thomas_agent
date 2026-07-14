from __future__ import annotations

from pathlib import Path

from gate_matrix import (
    ACTIVE_CHECKS,
    ACTIVE_VALIDATOR_FILENAMES,
    DEFERRED_CHECKS,
    DEFERRED_VALIDATOR_FILENAMES,
    LEGACY_COMPATIBILITY_CHECKS,
)


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    overlap = ACTIVE_VALIDATOR_FILENAMES.intersection(
        DEFERRED_VALIDATOR_FILENAMES
    )
    if overlap:
        raise SystemExit(
            "FAIL: Active and Deferred validators overlap: "
            + ", ".join(sorted(overlap))
        )

    all_checks = [
        *ACTIVE_CHECKS,
        *DEFERRED_CHECKS,
        *LEGACY_COMPATIBILITY_CHECKS,
    ]

    missing = []
    for _, command in all_checks:
        path = ROOT / command[0]
        if not path.exists():
            missing.append(command[0])

    if missing:
        raise SystemExit(
            "FAIL: Gate matrix references missing validators: "
            + ", ".join(sorted(set(missing)))
        )

    active_names = {label for label, _ in ACTIVE_CHECKS}
    prohibited_active_fragments = (
        "Executor Foundation",
        "Operations Evidence",
        "Control, Supervision",
        "Runtime Promotion Readiness",
        "Runtime-Authoritative Read-only Entry",
        "At-Most-Once",
        "Protected Local Governance State",
        "Disabled Single Read-only Entry",
    )
    for label in active_names:
        if any(fragment in label for fragment in prohibited_active_fragments):
            raise SystemExit(
                f"FAIL: Deferred scope leaked into Active Gate: {label}"
            )

    print("THOMAS_AGENT_GATE_SEPARATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
