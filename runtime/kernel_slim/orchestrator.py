from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .assembler import assemble_run
from .audit import build_audit_event
from .loader import load_context
from .policy import evaluate_policy
from .preflight import run_preflight
from .router import RoutingError, select_route
from .validation import validate_output
from .worker_port import Worker, invoke_worker


def run_read_only_kernel(
    *,
    repo_root: Path,
    input_bundle: Mapping[str, Any],
    governance_policy: Mapping[str, Any],
    created_at: str,
    worker: Worker,
) -> dict[str, Any]:
    context = load_context(
        repo_root=repo_root,
        input_bundle=input_bundle,
        created_at=created_at,
    )
    preflight = run_preflight(context)
    policy = evaluate_policy(
        preflight=preflight,
        governance_policy=governance_policy,
    )

    task = input_bundle.get("task", {})
    task_id = task.get("identity", {}).get("task_id", "unknown")
    audit_events: list[dict[str, Any]] = []
    blockers = list(preflight.blockers) + list(policy.blockers)

    if blockers:
        audit_events.append(
            build_audit_event(
                event_type="READ_ONLY_REPLAY_BLOCKED",
                actor_id="runtime.kernel",
                task_id=task_id,
                payload={"blockers": blockers},
                created_at=created_at,
            )
        )
        return assemble_run(
            task=task,
            policy=policy,
            route=None,
            output=None,
            validation=None,
            audit_events=audit_events,
            blockers=tuple(blockers),
        )

    try:
        route = select_route(preflight=preflight, policy=policy)
    except RoutingError as exc:
        blockers.append(f"ROUTING_ERROR:{exc}")
        return assemble_run(
            task=task,
            policy=policy,
            route=None,
            output=None,
            validation=None,
            audit_events=audit_events,
            blockers=tuple(blockers),
        )

    output = invoke_worker(
        context=context,
        route=route,
        worker=worker,
    )
    validation = validate_output(output=output, task=task)

    if validation["status"] != "passed":
        blockers.extend(validation["blockers"])

    audit_events.append(
        build_audit_event(
            event_type="READ_ONLY_REPLAY_VALIDATED",
            actor_id=route.actor_id,
            task_id=task_id,
            payload={
                "route_type": route.route_type,
                "validation_status": validation["status"],
            },
            created_at=created_at,
        )
    )

    return assemble_run(
        task=task,
        policy=policy,
        route=route,
        output=output,
        validation=validation,
        audit_events=audit_events,
        blockers=tuple(blockers),
    )
