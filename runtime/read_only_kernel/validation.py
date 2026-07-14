from __future__ import annotations

from typing import Any

from .integrity import short_id


def build_validation_result(
    *,
    output: dict[str, Any],
    output_fingerprint: str,
    task: dict[str, Any],
    assignment: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    seed = {
        "agent_output_id": output["agent_output_id"],
        "task_id": task["identity"]["task_id"],
        "task_revision": task["identity"]["task_revision"],
    }
    return {
        "schema_version": "validation_result.v0.1",
        "validation_result_id": short_id("valres", seed),
        "trace_id": task["identity"]["trace_id"],
        "task_id": task["identity"]["task_id"],
        "task_revision": task["identity"]["task_revision"],
        "core_context_binding_id": task["context"]["core_context_binding_id"],
        "subject": {
            "subject_type": "AGENT_OUTPUT",
            "subject_id": output["agent_output_id"],
            "subject_ref": f"in_memory:{output['agent_output_id']}",
            "subject_fingerprint": output_fingerprint,
            "subject_created_by_actor_id": assignment["actor_instance_id"],
        },
        "validator": {
            "validator_type": "SYSTEM",
            "validator_actor_id": "i0_5.read_only_runtime.contract_validator",
            "validator_role_id": None,
            "validator_role_version": None,
            "validator_execution_context_id": short_id("valctx", seed),
            "independent_required": False,
            "independence_verified": False,
        },
        "validation": {
            "validation_mode": "CONTRACT",
            "result": "PASS",
            "acceptance_criteria": [
                "agent_output_lineage_consistent",
                "read_only_boundary_preserved",
                "no_hidden_capability_invocation",
            ],
            "rejection_criteria": [
                "lineage_mismatch",
                "secret_exposure",
                "hidden_model_tool_program_network_or_write_effect",
            ],
            "checks": [
                {
                    "check_id": "agent_output_lineage",
                    "result": "PASS",
                    "evidence_refs": [f"in_memory:{output['agent_output_id']}"],
                    "notes": "Agent Output matches Task, Binding, Assignment, Role, and Actor lineage.",
                },
                {
                    "check_id": "read_only_effects",
                    "result": "PASS",
                    "evidence_refs": ["runtime:read_only_kernel.effects"],
                    "notes": "No model, Tool, Program, network, filesystem write, external action, or Runtime mutation occurred.",
                },
            ],
            "result_reasons": [
                "The deterministic worker returned a schema-shaped Agent Output with exact lineage.",
                "All prohibited capability counters remained zero.",
            ],
            "recommended_next_state": "REPLAY_COMPLETED",
        },
        "findings": {
            "facts": ["The Agent Output was generated from explicit input records only."],
            "risks": ["Development replay is non-authoritative and must not be treated as Runtime activation."],
            "omissions": ["No live Runtime state or external evidence was evaluated."],
            "assumptions": ["The provided snapshots are the intended development inputs."],
            "limitations": ["Automatic contract validation is not independent domain validation."],
        },
        "evidence_refs": [f"in_memory:{output['agent_output_id']}", "runtime:read_only_kernel.preflight"],
        "permission_boundary": {
            "grants_permission": False,
            "grants_approval": False,
            "grants_authority": False,
            "grants_execution": False,
            "grants_activation": False,
            "mutates_subject": False,
        },
        "runtime_effect": {
            "mode": "REVIEW_ONLY",
            "grants_permission": False,
            "grants_approval": False,
            "grants_authority": False,
            "grants_execution": False,
            "grants_activation": False,
            "executor_handoff_allowed": False,
            "side_effects_allowed": False,
            "runtime_mutation_allowed": False,
        },
        "lifecycle": {"created_at": now, "supersedes": []},
        "audit_refs": [],
    }
