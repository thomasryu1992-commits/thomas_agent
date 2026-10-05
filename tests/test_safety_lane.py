"""The safety invariants lane (PHASE_7_14_ALIGNMENT_AUDIT_V0.1 §D, Thomas 2026-10-05).

`tests/safety_lane.args` names the test files `.github/workflows/safety-invariants.yml` runs on
their own. A lane that silently lost a file would still go green, so this pins the property, not
the spelling: every listed file exists, and the files that hold the invariants below — found by
searching the suite, so a moved test moves with it — are all in the lane."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANE = ROOT / "tests" / "safety_lane.args"

# The invariants the lane exists to run alone, by the test that holds each one.
INVARIANTS = (
    "test_a_skip_is_refused_before_anything_is_asked",                       # no stage skip
    "test_live_waits_for_signed_testnet_evidence",                           # no LIVE without a testnet cycle
    "test_there_is_no_escape_hatch_around_the_approval",                     # no climb without approval
    "test_evidence_is_not_permission_a_climb_on_good_results_alone_reads_read_only",
    "test_a_testnet_authorization_opens_nothing_live",                       # venue separation
    "test_the_door_refuses_before_the_venue_when_the_stage_is_too_low",      # stage gates the testnet door
    "test_the_env_only_gate_has_exactly_the_capabilities_thomas_named",      # env gate inventory
    "test_a_paper_cycle_reaches_no_venue_even_with_every_trading_opt_in_set",
)


def _lane() -> list[str]:
    return [line.strip() for line in LANE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _defining_files(name: str) -> set[str]:
    found = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.FunctionDef) and node.name == name for node in tree.body):
            found.add(path.relative_to(ROOT).as_posix())
    return found


def test_every_lane_entry_is_an_existing_test_file_listed_once():
    lane = _lane()
    assert lane and len(lane) == len(set(lane))
    for entry in lane:
        path = ROOT / entry
        assert path.is_file() and path.name.startswith("test_") and path.suffix == ".py", entry


def test_the_lane_runs_every_named_invariant():
    lane = set(_lane())
    for name in INVARIANTS:
        files = _defining_files(name)
        assert len(files) == 1, f"{name} is defined in {sorted(files) or 'no test file'}"
        assert files <= lane, f"{name} lives in {files.pop()}, which the safety lane does not run"


def test_the_workflow_runs_the_lane_file():
    workflow = (ROOT / ".github" / "workflows" / "safety-invariants.yml").read_text(encoding="utf-8")
    assert "@tests/safety_lane.args" in workflow
    assert "scripts/check_test_skips.py" in workflow and "ci_activate_core_for_tests.py" in workflow
