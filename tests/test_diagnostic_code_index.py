"""The diagnostic index stays true, and a new cross-module code is a decision.

`REMAINING_WORK.md` §G3: a large `reason_code` vocabulary is right for a fail-closed system; what
was missing is that reading a code back to its cause had no index, and **nothing checked for
duplicate codes across modules**.

Two tests, and they fail for different reasons on purpose. The first says the committed index
does not match the source — regenerate it. The second says a code just started being raised from
a second module, which is either shared vocabulary (declare it) or two meanings wearing one name
(rename one).
"""

from __future__ import annotations

import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_diagnostic_code_index import OUTPUT_REL, build, collect_sites  # noqa: E402

# Codes raised from more than one module as of 2026-08-06, when this check was introduced.
#
# **A snapshot, not an endorsement.** Most are shared vocabulary working correctly —
# `APPROVAL_EXPIRED` means the same thing in all seven modules that raise it, and an operator
# reading it back is not misled. Some may be two meanings that happen to share a name; nobody has
# audited all 59. The list exists so the *sixtieth* is a decision someone makes, which is the gap
# §G3 names, rather than a review of the fifty-nine that is not this test's job.
SHARED_ACROSS_MODULES = frozenset({
    "ALREADY_CONSUMED", "APPROVAL_CONTENT_MISMATCH", "APPROVAL_EXPIRED",
    "APPROVAL_MISSING", "APPROVAL_NOT_APPROVED", "APPROVAL_WRONG_ACTION",
    "ARGUMENT_NOT_ACCEPTED", "AUTHORITY_RECORD_INVALID", "CANDIDATE_EXPIRED",
    "CANDIDATE_GONE", "CANDIDATE_INPUT_INVALID", "CANDIDATE_NOT_FOUND",
    "CONSUMPTION_DISABLED", "CONTENT_CHANGED", "ENTRY_NOT_FOUND",
    "FINGERPRINT_MISMATCH", "FINGERPRINT_UNCOMPUTABLE", "INVALID_CANDIDATE",
    "INVALID_ROLE", "INVALID_TIMESTAMP", "KILL_STATE_UNAVAILABLE",
    # Added 2026-08-08, and they are not new collisions — they are newly *visible* ones. Both are
    # handed to `jsonl`, which raises them on the caller's behalf, so the literal sits at the call
    # and the extractor (walking only error-class calls) never saw either. `LEDGER_UNREADABLE`:
    # store.py's readers and control.py's `_mode_from_ledger`, both "a ledger file would not
    # read". `LEDGER_WRITE_FAILED`: store.py's audit append and bridge_idempotency.py's, which
    # writes under the same LedgerStore root — both "a ledger append failed". Same meaning each.
    "LEDGER_UNREADABLE", "LEDGER_WRITE_FAILED",
    "LIFECYCLE_DECISION_INVALID", "LIFECYCLE_TERMINAL_IMMUTABLE", "LIFECYCLE_UNKNOWN_STRATEGY",
    "MALFORMED_DIRECTION", "MALFORMED_REQUEST", "MALFORMED_RESULT",
    "MISSING_OPERATOR", "MISSING_REASON", "MISSING_SYMBOL",
    "NOT_APPROVED", "NOT_A_CANDIDATE", "NOT_BOUND",
    "NO_API_KEY", "NO_MODEL_BUDGET", "PATTERN_NOT_FOUND",
    "PERMISSION_DECISION_MISSING", "PLANNED_TASK_INVALID", "POLICY_UNAVAILABLE",
    "PROVIDER_ERROR", "REASON_REQUIRED", "REGISTRY_UNAVAILABLE",
    "REGISTRY_UNRESOLVABLE", "RESPONSE_TRUNCATED", "ROLE_DEFINITION_INVALID",
    "ROUTE_NOT_SUPPORTED", "SCOPE_NOT_CONSUMABLE", "SECRET_IN_CANDIDATE",
    "TOKEN_BUDGET_EXCEEDED", "TOOL_ERROR", "TOOL_TRANSPORT",
    "UNKNOWN_APPROVAL", "UNKNOWN_CANDIDATE", "UNKNOWN_COMMAND",
    "UNKNOWN_FLAG", "UNKNOWN_REQUEST_KIND", "USAGE",
    "VALIDATION_RESULT_INVALID", "VERB_NOT_PERMITTED",
})


def _modules_per_code() -> dict[str, set[str]]:
    sites, _ = collect_sites()
    per_code: dict[str, set[str]] = defaultdict(set)
    for site in sites:
        per_code[site.code].add(site.module)
    return per_code


def test_the_committed_index_matches_the_source():
    """An index that can go stale is the artifact §G3 is complaining about, one layer up."""
    committed = (ROOT / OUTPUT_REL).read_text(encoding="utf-8")
    assert committed == build(), (
        f"{OUTPUT_REL} is stale — run `python scripts/build_diagnostic_code_index.py`"
    )


def test_a_code_handed_to_a_shared_primitive_is_still_indexed():
    """A primitive that raises on its caller's behalf takes the code as a *parameter*.

    So the raise carries a variable and the literal sits one frame up, at the call. Walking only
    error-class calls therefore misses the code entirely — which it did: every code handed to
    ``jsonl`` was absent from this index, `LEDGER_UNREADABLE` and `LEDGER_WRITE_FAILED` among
    them, the audit ledger's own failures. Pinned because the index is the operator's lookup path
    and this is the shape that silently empties it.
    """
    per_code = _modules_per_code()
    for code, module in (
        ("LEDGER_UNREADABLE", "runtime/mvp_runtime/store.py"),
        ("LEDGER_WRITE_FAILED", "runtime/mvp_runtime/store.py"),
        ("APPROVAL_READ_FAILED", "runtime/mvp_runtime/approval_store.py"),
        ("COUNTERFACTUAL_HISTORY_UNREADABLE", "runtime/mvp_runtime/crypto/counterfactual.py"),
    ):
        assert module in per_code.get(code, set()), f"{code} is raised from {module} but not indexed"


def test_a_delegated_code_is_indexed_under_the_class_that_actually_carries_it():
    """``exc_type=`` decides which except-clause sees the failure, so the row must say so.

    ``counterfactual`` keeps ``ToolError`` because the tool chokepoints above it catch that and
    would not catch ``jsonl``'s default. An index that reported the primitive's default here would
    send an operator to the wrong handler."""
    sites, _ = collect_sites()
    cf = [s for s in sites if s.code == "COUNTERFACTUAL_HISTORY_UNREADABLE"]
    assert cf and all(s.error_class == "ToolError" for s in cf), cf
    ledger = [s for s in sites if s.code == "LEDGER_UNREADABLE"]
    assert ledger and all(s.error_class == "PersistenceError" for s in ledger), ledger


def test_a_code_raised_from_a_new_module_is_declared_or_renamed():
    """One code, two meanings, is indistinguishable from shared vocabulary at the call site.

    An operator reads a code back to find the module that produced it. When two modules raise the
    same code for different reasons, that lookup silently returns the wrong one — and nothing in
    the runtime notices, because both raises are individually correct.
    """
    per_code = _modules_per_code()
    now_shared = {code for code, modules in per_code.items() if len(modules) > 1}
    fresh = now_shared - SHARED_ACROSS_MODULES
    assert not fresh, (
        "these codes are now raised from more than one module: "
        + ", ".join(f"{c} ({', '.join(sorted(pathlib.Path(m).name for m in per_code[c]))})"
                    for c in sorted(fresh))
        + " — if they mean the same thing, add them to SHARED_ACROSS_MODULES; if they do not, "
          "rename one, because an operator cannot tell the two cases apart from the code alone"
    )


def test_the_declared_set_does_not_outlive_its_codes():
    """A name that stopped being shared, or stopped existing, should leave the list.

    Kept as its own test rather than folded into the one above: a stale entry is a documentation
    bug and a new collision is a diagnostic one, and a single assertion covering both would report
    the harmless case in the language of the harmful one.
    """
    per_code = _modules_per_code()
    now_shared = {code for code, modules in per_code.items() if len(modules) > 1}
    stale = SHARED_ACROSS_MODULES - now_shared
    assert not stale, (
        "declared as shared but no longer raised from more than one module: "
        + ", ".join(sorted(stale)) + " — drop them from SHARED_ACROSS_MODULES"
    )


def test_module_paths_are_platform_independent():
    """The index is generated on one platform and checked on both.

    `str(path.relative_to(ROOT))` emits `runtime\\mvp_runtime\\...` on Windows, so every row in a
    Linux-generated index differed and the freshness test above failed for a reason that had
    nothing to do with the code changing. CI caught it after the merge; this pins the fix, since
    the failure is invisible to anyone developing on one platform.
    """
    sites, _ = collect_sites()
    offenders = sorted({site.module for site in sites if "\\" in site.module})
    assert not offenders, f"module paths must be POSIX-style: {offenders[:5]}"


def test_every_indexed_site_names_a_real_file_and_line():
    """The index is a lookup an operator follows. A row pointing nowhere is worse than no row."""
    sites, _ = collect_sites()
    assert sites, "no raise sites found at all — the extractor is broken, not the runtime clean"
    for site in sites:
        path = ROOT / site.module
        assert path.is_file(), f"{site.code}: {site.module} does not exist"
        assert 0 < site.line <= len(path.read_text(encoding="utf-8").splitlines()), (
            f"{site.code}: {site.module}:{site.line} is past the end of the file"
        )
