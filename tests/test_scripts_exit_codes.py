"""One exit-code vocabulary across the runtime CLIs and the operator scripts.

`REMAINING_WORK.md` §G4c: the code-2 slot meant three different things. `cli_common` — the
authority, and the one CI pins — says 2 is `EXIT_BLOCKED` and 3 is `EXIT_USAGE`. Six scripts said
the **inverse**, a seventh called 2 `EXIT_REFUSED`, and an eighth skipped 2 entirely. Both sides
were internally consistent, which is why nothing caught it: an operator reading 2 back from a
script got "usage error" where the same number from `python -m runtime.mvp_runtime.cli` means
"fail-closed block".

`cli_common` is the fixed point rather than the thing that moved: `.github/workflows/docker-image.yml`
asserts `-eq 2` for a fail-closed runtime CLI, and `tests/test_place_canary_order_audit_report.py`
imports `EXIT_BLOCKED` by name.

These tests are structural on purpose. Asserting the *values* would restate `cli_common`; what can
rot is a script quietly minting its own constant again, which is exactly how the divergence began.
"""

from __future__ import annotations

import ast
import pathlib

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

#: Owned by `cli_common`; a script that needs one imports it.
SHARED_NAMES = {"EXIT_OK", "EXIT_BLOCKED", "EXIT_USAGE"}
SHARED_VALUES = {EXIT_OK, EXIT_BLOCKED, EXIT_USAGE}


def _module_exit_constants(path: pathlib.Path) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, object] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.startswith("EXIT_")
                and isinstance(node.value, ast.Constant)):
            found[node.targets[0].id] = node.value.value
    return found


def test_no_script_redefines_a_shared_exit_code():
    """The three shared names have one definition, in `cli_common`. A local copy is how the
    inversion §G4c found got in: both sides stay self-consistent while their meanings diverge."""
    offenders = {
        p.relative_to(ROOT).as_posix(): sorted(SHARED_NAMES & set(_module_exit_constants(p)))
        for p in sorted(SCRIPTS.rglob("*.py"))
        if SHARED_NAMES & set(_module_exit_constants(p))
    }
    assert not offenders, f"these redefine a cli_common exit code instead of importing it: {offenders}"


def test_a_script_specific_exit_code_cannot_collide_with_a_shared_one():
    """A script may add its own code — `EXIT_UNOWNED`, `EXIT_REJECTED` — but not on a number that
    already means something. Those two are *findings*: the command ran and reports what it saw,
    which is a different claim from "refused to act", and reusing the refusal's number would make
    the two indistinguishable from outside."""
    offenders = {}
    for p in sorted(SCRIPTS.rglob("*.py")):
        clashes = {n: v for n, v in _module_exit_constants(p).items() if v in SHARED_VALUES}
        if clashes:
            offenders[p.relative_to(ROOT).as_posix()] = clashes
    assert not offenders, f"these reuse a shared exit-code number for a different meaning: {offenders}"


def test_the_authority_still_says_what_ci_and_the_docs_assume():
    """Pinned because everything above is relative to it, and because two things outside this file
    already depend on these exact numbers: the docker-image workflow asserts exit 2 on a
    fail-closed runtime CLI, and CLAUDE.md documents `EXIT_USAGE` for an unknown intake flag."""
    assert (EXIT_OK, EXIT_BLOCKED, EXIT_USAGE) == (0, 2, 3)
