"""R2.2 Core Context Binding tests.

The happy path needs a locally-activated Core (``.runtime_governance_state/
CURRENT_CORE_RELEASE.yaml``), which is per-environment state absent from a clean
CI checkout — so it is skipped when no local activation exists. The full binding
path is also covered by the repository release gate's isolated self-test. The
fail-closed cases run everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL, bind_task_to_core
from runtime.mvp_runtime.errors import PlannerBlocked
from runtime.mvp_runtime.intake import build_task

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_POINTER = REPO_ROOT / DEFAULT_POINTER_REL
FIXED_NOW = "2026-07-15T09:00:00Z"

_has_local_core = LOCAL_POINTER.is_file()
requires_local_core = pytest.mark.skipif(
    not _has_local_core, reason="no local Core activation (.runtime_governance_state/CURRENT_CORE_RELEASE.yaml)"
)


def _received_task(**overrides):
    params = dict(raw_request="이 사업 아이디어를 분석해줘", now=FIXED_NOW)
    params.update(overrides)
    return build_task(**params)


# --- fail-closed (run everywhere) -------------------------------------------

def test_bind_blocks_when_pointer_missing(tmp_path):
    with pytest.raises(PlannerBlocked) as exc:
        bind_task_to_core(_received_task(), repo_root=tmp_path, pointer_path=tmp_path / "nope.yaml")
    assert exc.value.reason_code == "CORE_NOT_ACTIVATED"


@requires_local_core
def test_bind_blocks_unknown_rule():
    # A rule id that is valid in shape but not in the active Release must fail closed.
    task = _received_task(active_core_rule_ids=["MVP_RULE_999"])
    with pytest.raises(PlannerBlocked) as exc:
        bind_task_to_core(task)
    assert exc.value.reason_code == "BINDING_FAILED"


# --- happy path (local only) ------------------------------------------------

@requires_local_core
def test_bind_received_task_produces_ccb():
    task = _received_task()
    binding, bound_task = bind_task_to_core(task)
    ccb = binding["identity"]["core_context_binding_id"]
    assert ccb.startswith("ccb-")
    assert binding["schema_version"] == "core_context_binding.v0.3"
    assert binding["identity"]["task_id"] == task["identity"]["task_id"]
    assert binding["identity"]["trace_id"] == task["identity"]["trace_id"]
    # The bound task now carries the binding id; the original is unchanged.
    assert bound_task["context"]["core_context_binding_id"] == ccb
    assert task["context"]["core_context_binding_id"] is None


@requires_local_core
def test_bind_is_deterministic():
    a, _ = bind_task_to_core(_received_task(), now=FIXED_NOW)
    b, _ = bind_task_to_core(_received_task(), now=FIXED_NOW)
    assert a["identity"]["core_context_binding_id"] == b["identity"]["core_context_binding_id"]
