"""The scripts/ import bridge must reach its three symbols WITHOUT shadowing the world.

`runtime/` (the product) reaches four shared governance helpers that live under `scripts/`
(the tooling) through one `sys.path` mutation, so the fingerprint logic and the policy-
semantics validator have one authority rather than two. That reuse is deliberate; what is
not negotiable is the blast radius of the mutation.
"""

from __future__ import annotations

import sys


def _bridged():
    from runtime.mvp_runtime import _scripts_bridge  # noqa: F401  (side effect under test)

    return [index for index, entry in enumerate(sys.path) if entry.endswith("scripts")]


def test_the_bridge_reaches_every_symbol_the_runtime_imports():
    """If this breaks, `permission` / `approval` / `consumption` / `trial` cannot import."""
    _bridged()
    from lib.action_fingerprint import compute_action_fingerprint
    from validate_permission_approval_contracts import (
        POLICY_BINDING,
        scope_policy_map,
        validate_approval_record,
        validate_permission_record,
    )

    assert callable(compute_action_fingerprint)
    assert callable(validate_permission_record) and callable(validate_approval_record)
    assert callable(scope_policy_map) and POLICY_BINDING


def test_scripts_never_wins_the_import_race():
    """`scripts/` holds 68 top-level names, `lib` among them. Inserted at position 0 they all
    won the race for the whole process — ahead of the standard library and every installed
    package — so a dependency expecting its own `lib` would silently get this one and break
    somewhere unrelated. Appended, a real collision fails loudly here instead, on the one
    name that collided."""
    positions = _bridged()
    assert positions, "the bridge did not put scripts/ on sys.path at all"
    assert 0 not in positions, (
        "scripts/ is at sys.path[0] and now shadows every same-named module in the process; "
        "the bridge must append (see runtime/mvp_runtime/_scripts_bridge.py)"
    )


def test_the_bridge_is_idempotent():
    """Five runtime modules import it; the path must not grow once per importer."""
    first = _bridged()
    import importlib

    from runtime.mvp_runtime import _scripts_bridge

    importlib.reload(_scripts_bridge)
    assert _bridged() == first
