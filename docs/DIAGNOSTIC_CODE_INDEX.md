# Diagnostic code index — GENERATED, do not edit by hand

Regenerate with `python scripts/build_diagnostic_code_index.py`. `tests/test_diagnostic_code_index.py` fails when this file and the code disagree, so a new reason code lands here or the suite goes red.

Answers the question `REMAINING_WORK.md` §G3 says an operator actually asks: **a code came out of the runtime — where is it raised, and what test is it behind?** The `condition` column is the guarding `if`, unparsed from the source, so it cannot drift from what the code does the way a written description would.

- **470** distinct codes across **878** raise sites
- **21** exception classes carry them
- **64** codes are raised from more than one module (see below)
- **122** raise sites build their code at runtime rather than from a literal and are not indexable; they are counted rather than guessed at
- **29** raise sites carry a human-readable **message** where a code would go, so there is nothing to look up — a different gap from the line above, and counted apart from it

## Codes raised from more than one module

Not automatically a defect — `APPROVAL_EXPIRED` meaning one thing in seven modules is shared vocabulary working correctly. It is here because the opposite case looks identical from the outside: one code, two meanings, and an operator reading it back gets the wrong module. The test pins this set, so the next one is a decision rather than an accident.

| code | modules |
|---|---|
| `ALREADY_CONSUMED` | `approval.py`, `consumption.py`, `switch_bridge.py`, `trial.py` |
| `APPROVAL_CONTENT_MISMATCH` | `probe.py`, `promotion.py`, `retirement.py`, `registration.py` |
| `APPROVAL_EXPIRED` | `approval.py`, `consumption.py`, `probe.py`, `promotion.py`, `retirement.py`, `registration.py`, `switch_bridge.py`, `trial.py` |
| `APPROVAL_MISSING` | `probe.py`, `promotion.py`, `retirement.py`, `registration.py` |
| `APPROVAL_NOT_APPROVED` | `probe.py`, `promotion.py`, `retirement.py`, `registration.py` |
| `APPROVAL_WRONG_ACTION` | `probe.py`, `promotion.py`, `retirement.py`, `registration.py` |
| `ARGUMENT_NOT_ACCEPTED` | `dispatch_bridge.py`, `knowledge_bridge.py`, `pipeline_worker.py`, `read_bridge.py`, `switch_bridge.py` |
| `AUTHORITY_RECORD_INVALID` | `policy.py`, `preflight.py` |
| `CANDIDATE_EXPIRED` | `consumption.py`, `memory_console.py` |
| `CANDIDATE_GONE` | `consumption.py`, `memory_console.py` |
| `CANDIDATE_INPUT_INVALID` | `programization.py`, `programization_cli.py` |
| `CANDIDATE_NOT_FOUND` | `program_request.py`, `programization.py`, `registration.py` |
| `CONSUMPTION_DISABLED` | `consumption.py`, `trial.py` |
| `CONTENT_CHANGED` | `consumption.py`, `trial.py` |
| `ENTRY_NOT_FOUND` | `registry_console.py`, `task_registry.py` |
| `FINGERPRINT_MISMATCH` | `consumption.py`, `switch_bridge.py`, `trial.py` |
| `FINGERPRINT_UNCOMPUTABLE` | `consumption.py`, `switch_bridge.py`, `trial.py` |
| `INVALID_CANDIDATE` | `memory.py`, `permission.py` |
| `INVALID_ROLE` | `assignment.py`, `permission.py` |
| `INVALID_TIMESTAMP` | `intake.py`, `permission.py` |
| `KILL_STATE_UNAVAILABLE` | `memory_console.py`, `registry_console.py` |
| `KIND_NOT_PERMITTED` | `dispatch_bridge.py`, `pipeline_worker.py` |
| `LEDGER_UNREADABLE` | `control.py`, `store.py` |
| `LEDGER_WRITE_FAILED` | `bridge_idempotency.py`, `store.py` |
| `LIFECYCLE_DECISION_INVALID` | `lifecycle.py`, `pool.py` |
| `LIFECYCLE_TERMINAL_IMMUTABLE` | `lifecycle.py`, `pool.py`, `retirement.py` |
| `LIFECYCLE_UNKNOWN_STRATEGY` | `pool.py`, `retirement.py` |
| `MALFORMED_DIRECTION` | `live_order.py`, `live_position.py` |
| `MALFORMED_REQUEST` | `bridge_idempotency.py`, `dispatch_bridge.py`, `knowledge_bridge.py`, `pipeline_worker.py`, `read_bridge.py`, `socket_door.py`, `switch_bridge.py` |
| `MALFORMED_RESULT` | `account.py`, `market_data.py`, `naver_research.py`, `tools.py` |
| `MISSING_OPERATOR` | `memory.py`, `program_request.py`, `programization.py` |
| `MISSING_REASON` | `memory.py`, `memory_console.py`, `program_request.py`, `programization.py` |
| `MISSING_SYMBOL` | `live_order.py`, `live_position.py` |
| `NOT_APPROVED` | `approval.py`, `consumption.py`, `switch_bridge.py`, `trial.py` |
| `NOT_A_CANDIDATE` | `memory.py`, `planner.py` |
| `NOT_BOUND` | `assignment.py`, `permission.py`, `validator.py`, `worker.py` |
| `NO_API_KEY` | `account.py`, `market_data.py`, `naver_research.py`, `providers.py`, `tools.py` |
| `NO_MODEL_BUDGET` | `validator.py`, `worker.py` |
| `PATTERN_NOT_FOUND` | `programization.py`, `programization_cli.py` |
| `PERMISSION_DECISION_MISSING` | `approval.py`, `consumption.py`, `switch_bridge.py`, `trial.py` |
| `PLANNED_TASK_INVALID` | `prime.py`, `trial.py` |
| `POLICY_UNAVAILABLE` | `approval.py`, `permission.py` |
| `PROVIDER_ERROR` | `validator.py`, `worker.py` |
| `REASON_REQUIRED` | `dispatch_bridge.py`, `pipeline_worker.py`, `switch_bridge.py` |
| `REGISTRY_UNAVAILABLE` | `planner.py`, `registry_console.py` |
| `REGISTRY_UNRESOLVABLE` | `program_request.py`, `registration.py` |
| `REQUEST_REQUIRED` | `dispatch_bridge.py`, `pipeline_worker.py` |
| `RESPONSE_TRUNCATED` | `validator.py`, `worker.py` |
| `ROLE_DEFINITION_INVALID` | `assignment.py`, `planner.py` |
| `ROUTE_NOT_SUPPORTED` | `router.py`, `worker_port.py` |
| `SCOPE_NOT_CONSUMABLE` | `consumption.py`, `trial.py` |
| `SECRET_IN_CANDIDATE` | `memory.py`, `programization.py` |
| `TOKEN_BUDGET_EXCEEDED` | `validator.py`, `worker.py` |
| `TOOL_ERROR` | `market_data.py`, `naver_research.py`, `tools.py` |
| `TOOL_TRANSPORT` | `account.py`, `market_data.py`, `naver_research.py`, `tools.py` |
| `UNKNOWN_APPROVAL` | `approval.py`, `consumption.py`, `switch_bridge.py`, `trial.py` |
| `UNKNOWN_CANDIDATE` | `approval_cli.py`, `pool.py` |
| `UNKNOWN_COMMAND` | `approval.py`, `control.py`, `registry_console.py` |
| `UNKNOWN_FLAG` | `safety_gate.py`, `task_registry.py` |
| `UNKNOWN_REQUEST_KIND` | `planner.py`, `task_registry.py` |
| `USAGE` | `memory_console.py`, `registry_console.py` |
| `VALIDATION_RESULT_INVALID` | `validation.py`, `validator.py` |
| `VERB_NOT_PERMITTED` | `knowledge_bridge.py`, `read_bridge.py`, `switch_bridge.py` |
| `WORKER_UNAVAILABLE` | `dispatch_bridge.py`, `scheduler.py` |

## Every code

| code | class | module | line | function | condition |
|---|---|---|---|---|---|
| `ABSOLUTE_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 173 | `resolve_target` | `candidate.is_absolute() or candidate.drive or relative_path.startswith(('/', '\\'))` |
| `ACCEPT_REQUIRES_SHADOW_PASS` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 587 | `transition_candidate` | `action == 'accept' and shadow.get('status') != 'PASS'` |
| `ACTIVATION_CHANGED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 406 | `assert_authorization` | `current != authorization.activation_sha256` |
| `ACTIVATION_EXPIRED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 290 | `authorize` | `now >= str(expires_at)` |
| `ACTIVATION_EXPIRED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 367 | `assert_authorization` | `now >= authorization.expires_at` |
| `ACTIVATION_MALFORMED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 168 | `_load_record` | `—` |
| `ACTIVATION_MALFORMED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 170 | `_load_record` | `not isinstance(record, dict)` |
| `ACTIVATION_MALFORMED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 178 | `_verify_integrity` | `field not in record` |
| `ACTIVATION_MALFORMED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 180 | `_verify_integrity` | `record['activation_marker'] != ACTIVATION_MARKER` |
| `ACTIVATION_MALFORMED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 182 | `_verify_integrity` | `not isinstance(record['flags'], list) or not all((isinstance(f, str) for f in record['flags']))` |
| `ACTIVATION_MALFORMED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 184 | `_verify_integrity` | `rank_of(record['authority_level']) is None` |
| `ACTIVATION_MALFORMED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 191 | `_verify_integrity` | `—` |
| `ACTIVATION_MALFORMED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 231 | `build_activation_record` | `rank_of(authority_level) is None` |
| `ACTIVATION_MALFORMED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 238 | `build_activation_record` | `not _TIMESTAMP_PATTERN.match(str(value) or '')` |
| `ACTIVATION_MALFORMED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 283 | `authorize` | `not _TIMESTAMP_PATTERN.match(str(value) or '')` |
| `ACTIVATION_MISSING` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 159 | `_load_record` | `not path.is_file()` |
| `ACTIVATION_NOT_YET_ACTIVE` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 288 | `authorize` | `now < str(activated_at)` |
| `ACTIVATION_REVOKED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 393 | `assert_authorization` | `not record_path.is_file()` |
| `ACTIVATION_TAMPERED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 193 | `_verify_integrity` | `not isinstance(claimed, str) or claimed != recomputed` |
| `ACTOR_PROFILE_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 236 | `apply_work` | `isinstance(raw_profile, str) and raw_profile.strip() in _ACTOR_PROFILES` |
| `ALREADY_CLASSIFIED` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 198 | `classify_task` | `classification.get('classification_status') != 'UNCLASSIFIED'` |
| `ALREADY_CONSUMED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 320 | `build_consumed_record` | `status == STATUS_CONSUMED` |
| `ALREADY_CONSUMED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 167 | `consume_approval` | `status == approval_mod.STATUS_CONSUMED` |
| `ALREADY_CONSUMED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 247 | `consume_approval` | `latest is None or latest.get('status') != approval_mod.STATUS_APPROVED` |
| `ALREADY_CONSUMED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 402 | `_spend` | `status == approval_mod.STATUS_CONSUMED` |
| `ALREADY_CONSUMED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 493 | `_spend` | `fresh is None or fresh.get('status') != approval_mod.STATUS_APPROVED` |
| `ALREADY_CONSUMED` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 404 | `run_trial` | `status == approval_mod.STATUS_CONSUMED` |
| `ALREADY_CONSUMED` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 486 | `run_trial` | `latest is None or latest.get('status') != approval_mod.STATUS_APPROVED` |
| `ALREADY_REGISTERED` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 169 | `request_registration` | `_registry_has(registry, program_id, version)` |
| `ALREADY_REGISTERED` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 261 | `apply_registration` | `_registry_has(registry, program_id, version)` |
| `AMBIGUOUS_ENTRY_ID` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 400 | `find` | `len(matches) > 1` |
| `AMBIGUOUS_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 333 | `select_role` | `len(candidates) > 1` |
| `APPROVAL_CONTENT_MISMATCH` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/probe.py` | 677 | `verify_probe_approval` | `snapshot.get('content_sha256') != probe_content_sha256(params)` |
| `APPROVAL_CONTENT_MISMATCH` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 349 | `verify_promotion_approval` | `snapshot.get('content_sha256') != expected` |
| `APPROVAL_CONTENT_MISMATCH` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 167 | `verify_retirement_approval` | `snapshot.get('content_sha256') != retirement_content_sha256(resolve_pool_entries(strategy_ids, …` |
| `APPROVAL_CONTENT_MISMATCH` | `ApprovalBlocked` | `runtime/mvp_runtime/registration.py` | 229 | `verify_registration_approval` | `snapshot.get('content_sha256') != expected` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 244 | `record_decision` | `is_expired(approval, now=now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 564 | `apply_command` | `approval.get('status') == STATUS_PENDING and is_expired(approval, now=now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 173 | `consume_approval` | `approval_mod.is_expired(approval_rec, now=now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/probe.py` | 669 | `verify_probe_approval` | `not isinstance(expires_at, str) or timeutil.parse_iso(expires_at) <= timeutil.parse_iso(now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 328 | `verify_promotion_approval` | `not isinstance(expires_at, str) or timeutil.parse_iso(expires_at) <= timeutil.parse_iso(now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 159 | `verify_retirement_approval` | `not isinstance(expires_at, str) or timeutil.parse_iso(expires_at) <= timeutil.parse_iso(now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/registration.py` | 222 | `verify_registration_approval` | `not isinstance(expires_at, str) or timeutil.parse_iso(expires_at) <= timeutil.parse_iso(now)` |
| `APPROVAL_EXPIRED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 410 | `_spend` | `approval_mod.is_expired(record, now=now)` |
| `APPROVAL_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 410 | `run_trial` | `approval_mod.is_expired(approval_rec, now=now)` |
| `APPROVAL_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/probe.py` | 663 | `verify_probe_approval` | `approval is None` |
| `APPROVAL_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 322 | `verify_promotion_approval` | `approval is None` |
| `APPROVAL_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 153 | `verify_retirement_approval` | `approval is None` |
| `APPROVAL_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/registration.py` | 216 | `verify_registration_approval` | `approval is None` |
| `APPROVAL_NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/probe.py` | 666 | `verify_probe_approval` | `status != 'APPROVED'` |
| `APPROVAL_NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 325 | `verify_promotion_approval` | `status != 'APPROVED'` |
| `APPROVAL_NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 156 | `verify_retirement_approval` | `status != 'APPROVED'` |
| `APPROVAL_NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/registration.py` | 219 | `verify_registration_approval` | `status != 'APPROVED'` |
| `APPROVAL_NOT_CONSUMED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 801 | `build_approval_consumption_audit` | `approval.get('status') != 'CONSUMED'` |
| `APPROVAL_NOT_CONSUMED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 856 | `build_trial_consumption_audit` | `approval.get('status') != 'CONSUMED'` |
| `APPROVAL_READ_FAILED` | `PersistenceError` | `runtime/mvp_runtime/approval_store.py` | 71 | `read_all` | `—` |
| `APPROVAL_READ_FAILED` | `PersistenceError` | `runtime/mvp_runtime/approval_store.py` | 115 | `get_permission_decision` | `—` |
| `APPROVAL_SCHEMA_INVALID` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 112 | `_validate` | `—` |
| `APPROVAL_SEMANTICS_INVALID` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 117 | `_validate` | `issues` |
| `APPROVAL_UNVERIFIED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 753 | `build_approval_decision_audit` | `approver.get('verification_status') != 'VERIFIED'` |
| `APPROVAL_UNVERIFIED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 804 | `build_approval_consumption_audit` | `approver.get('verification_status') != 'VERIFIED'` |
| `APPROVAL_UNVERIFIED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 859 | `build_trial_consumption_audit` | `approver.get('verification_status') != 'VERIFIED'` |
| `APPROVAL_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/approval_store.py` | 65 | `append` | `—` |
| `APPROVAL_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/approval_store.py` | 107 | `append_permission_decision` | `—` |
| `APPROVAL_WRONG_ACTION` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/probe.py` | 672 | `verify_probe_approval` | `snapshot.get('action_type') != PROBE_ACTION_TYPE` |
| `APPROVAL_WRONG_ACTION` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 332 | `verify_promotion_approval` | `snapshot.get('action_type') != PROMOTION_ACTION_TYPE` |
| `APPROVAL_WRONG_ACTION` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 163 | `verify_retirement_approval` | `snapshot.get('action_type') != RETIREMENT_ACTION_TYPE` |
| `APPROVAL_WRONG_ACTION` | `ApprovalBlocked` | `runtime/mvp_runtime/registration.py` | 225 | `verify_registration_approval` | `snapshot.get('action_type') != REGISTRATION_ACTION_TYPE` |
| `ARCHIVE_ALL_BOOKS_DEGRADED` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 1503 | `_execute` | `summary['books'] and summary['degraded'] == summary['books']` |
| `ARCHIVE_NAME_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/candle_archive.py` | 134 | `archive_path` | `not all((part and _SAFE_NAME.fullmatch(part) for part in parts))` |
| `ARCHIVE_NOT_ENABLED` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 1011 | `collect` | `—` |
| `ARCHIVE_NOT_ENABLED` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 1017 | `live_symbols` | `—` |
| `ARCHIVE_RATE_LIMITED` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 1515 | `_execute` | `summary.get('rate_limited')` |
| `ARCHIVE_SYMBOL_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/candle_archive.py` | 291 | `append_candles` | `not str(symbol).strip()` |
| `ARCHIVE_TIMEFRAME_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/candle_archive.py` | 143 | `_require_timeframe` | `timeframe not in TIMEFRAMES` |
| `ARCHIVE_UNIVERSE_UNREADABLE` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 1496 | `_execute` | `summary['blocked']` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 157 | `apply_dispatch` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/knowledge_bridge.py` | 128 | `apply_knowledge` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 191 | `apply_work` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 357 | `_apply_job` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 382 | `_apply_job` | `'proposal_inputs' in request` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 404 | `_apply_job` | `'inventory' in request` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/read_bridge.py` | 114 | `apply_read` | `argument is not None and command not in _TAKES_ARGUMENT` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 577 | `apply_switch` | `unexpected` |
| `ARGUMENT_NOT_ACCEPTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 649 | `apply_switch` | `approval_id is not None and 'scope' in request` |
| `ASSIGNMENT_LINEAGE_MISMATCH` | `KernelBlocked` | `runtime/read_only_kernel/router.py` | 18 | `select_route` | `assignment.get('assignment_id') != routing.get('role_assignment_ids', [None])[0]` |
| `ASSIGNMENT_SCHEMA_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 199 | `build_role_assignment` | `—` |
| `AUDIT_EVENT_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 162 | `_make_event` | `—` |
| `AUTHORITY_INSUFFICIENT` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 598 | `build_permission_decision` | `not authority_sufficient and disposition != 'BLOCK'` |
| `AUTHORITY_INVARIANT` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 90 | `build_role_assignment` | `not invariant_holds` |
| `AUTHORITY_RECORD_INVALID` | `KernelBlocked` | `runtime/read_only_kernel/policy.py` | 23 | `adapt_policy` | `authority.get('effective_permission_level') is None` |
| `AUTHORITY_RECORD_INVALID` | `KernelBlocked` | `runtime/read_only_kernel/preflight.py` | 369 | `run_preflight` | `—` |
| `BINDING_FAILED` | `PlannerBlocked` | `runtime/mvp_runtime/binding.py` | 55 | `bind_task_to_core` | `—` |
| `BRIDGE_ALREADY_RUNNING` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 311 | `__init__` | `door_is_live(path)` |
| `BRIDGE_CLIENT_GID_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 431 | `resolve_client_gid` | `—` |
| `BRIDGE_CLIENT_GID_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 436 | `resolve_client_gid` | `gid < 0` |
| `BRIDGE_CLIENT_GID_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 566 | `grant_client_access` | `—` |
| `BRIDGE_CLIENT_UID_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 462 | `resolve_client_uids` | `—` |
| `BRIDGE_CLIENT_UID_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 468 | `resolve_client_uids` | `uid < 0` |
| `BRIDGE_CLIENT_UID_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 473 | `resolve_client_uids` | `not uids` |
| `BRIDGE_CONCURRENCY_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 279 | `__init__` | `max_concurrent_requests < 1` |
| `BRIDGE_LIMITS_INVALID` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 285 | `__init__` | `max_frame_bytes < 1 or request_timeout_seconds <= 0` |
| `CANARY_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/live_promotion.py` | 195 | `read_canary_orders` | `order_id in seen` |
| `CANARY_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_promotion.py` | 191 | `read_canary_orders` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `CANARY_HISTORY_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_promotion.py` | 180 | `read_canary_orders` | `—` |
| `CANDIDATES_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1751 | `read_candidates` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `CANDIDATES_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1739 | `read_candidates` | `—` |
| `CANDIDATE_AMBIGUOUS` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1376 | `resolve_candidates` | `ambiguous` |
| `CANDIDATE_BEHAVIOUR_CLUSTER_OCCUPIED` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1080 | `assert_no_cluster_siblings` | `—` |
| `CANDIDATE_BELOW_OBSERVATION_ENTRY_BAR` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1181 | `assert_observation_entry_bar` | `—` |
| `CANDIDATE_COST_BASIS_STALE` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 341 | `assert_promotable_cost_basis` | `stale` |
| `CANDIDATE_DERIVATION_NOT_PROMOTABLE` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1337 | `assert_promotable_derivation` | `refused` |
| `CANDIDATE_EMPTY` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 226 | `consume_approval` | `not (isinstance(content, str) and content.strip())` |
| `CANDIDATE_EVIDENCE_DEPTH_UNRECORDED` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 567 | `assert_promotable_evidence_depth` | `unknown` |
| `CANDIDATE_EXISTS` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 513 | `create_program_candidate` | `any((c.get('pattern_id') == pattern_id for c in store.read_candidates()))` |
| `CANDIDATE_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 219 | `consume_approval` | `memory_is_expired(candidate, now=now)` |
| `CANDIDATE_EXPIRED` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 205 | `apply_memory_command` | `memory.is_expired(match, stamp)` |
| `CANDIDATE_FAMILY_CAP_EXCEEDED` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1224 | `assert_family_cap` | `—` |
| `CANDIDATE_GONE` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 211 | `consume_approval` | `candidate is None` |
| `CANDIDATE_GONE` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 199 | `apply_memory_command` | `match is None` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 484 | `create_program_candidate` | `not isinstance(review_input, Mapping)` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 491 | `create_program_candidate` | `not items` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 496 | `create_program_candidate` | `not (isinstance(rollback_ref, str) and rollback_ref.strip())` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 105 | `_load_review_input` | `not path_str` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 110 | `_load_review_input` | `—` |
| `CANDIDATE_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 112 | `_load_review_input` | `not isinstance(loaded, dict)` |
| `CANDIDATE_LINEAGE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1282 | `validate_candidate_lineage` | `not has_type` |
| `CANDIDATE_LINEAGE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1284 | `validate_candidate_lineage` | `derivation not in DERIVATION_TYPES` |
| `CANDIDATE_LINEAGE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1287 | `validate_candidate_lineage` | `not isinstance(parents, list) or not all((isinstance(p, str) and p for p in parents))` |
| `CANDIDATE_LINEAGE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1289 | `validate_candidate_lineage` | `len(set(parents)) != len(parents)` |
| `CANDIDATE_LINEAGE_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1292 | `validate_candidate_lineage` | `len(parents) < lo or (hi is not None and len(parents) > hi)` |
| `CANDIDATE_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 146 | `create_program_request` | `candidate is None` |
| `CANDIDATE_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 547 | `_require_candidate` | `latest is None` |
| `CANDIDATE_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 64 | `_lineage` | `candidate is None` |
| `CANDIDATE_NOT_RETIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 293 | `consume_approval` | `—` |
| `CANDIDATE_REQUIRES_REVIEW` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 508 | `create_program_candidate` | `latest.get('review_status') != 'UNDER_REVIEW'` |
| `CANDIDATE_SEMANTIC_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 724 | `assert_no_semantic_duplicates` | `—` |
| `CANDIDATE_UNCONFIRMED_FOR_LIVE` | `ToolError` | `runtime/mvp_runtime/crypto/forward_confirmation.py` | 204 | `assert_live_tier_confirmed` | `—` |
| `CANDIDATE_UNHASHED` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 102 | `_resolve_identity` | `not (isinstance(c.get('strategy_rule_hash'), str) and c['strategy_rule_hash'])` |
| `CANDIDATE_VERSION_MISMATCH` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 288 | `select_candidate_role` | `version is not None and role.get('version') != version` |
| `CAPABILITY_EXCEEDS_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 75 | `build_role_assignment` | `not set(required_capabilities).issubset(capabilities)` |
| `CHANNEL_PARTIAL_DELIVERY` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 1026 | `send` | `—` |
| `CHANNEL_TRANSPORT` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 890 | `_call` | `—` |
| `CHANNEL_TRANSPORT` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 892 | `_call` | `not isinstance(payload, dict) or not payload.get('ok')` |
| `CHAT_NOT_REGISTERED` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 241 | `verify_control_channel` | `not isinstance(message.chat_id, str) or message.chat_id != registration.chat_id` |
| `CONSUMED_NOT_PROMOTED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 280 | `consume_approval` | `—` |
| `CONSUMED_UNAUDITED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 301 | `consume_approval` | `—` |
| `CONSUMPTION_DISABLED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 76 | `consume` | `—` |
| `CONSUMPTION_DISABLED` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 101 | `authorize_spend` | `—` |
| `CONSUMPTION_SUBJECT_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 808 | `build_approval_consumption_audit` | `not (isinstance(validated_id, str) and validated_id)` |
| `CONSUMPTION_SUBJECT_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 862 | `build_trial_consumption_audit` | `not (isinstance(trial_task_id, str) and trial_task_id)` |
| `CONTENT_CHANGED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 229 | `consume_approval` | `integrity.sha256_record({'content': content}) != snapshot.get('content_sha256')` |
| `CONTENT_CHANGED` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 451 | `run_trial` | `trial_content_sha256(role, trial_request) != snapshot.get('content_sha256')` |
| `CONTENT_TOO_LARGE` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 216 | `_require_content` | `size > MAX_CONTENT_BYTES` |
| `CONTROL_CHARS` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 76 | `_reject_control_chars` | `code < 32 and ch not in _ALLOWED_CONTROL_CHARS or code == 127` |
| `CONTROL_WRITE_FAILED` | `ControlBlocked` | `runtime/mvp_runtime/control.py` | 495 | `save` | `—` |
| `CORE_CANDIDATE_ALREADY_DECIDED` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 483 | `decide_core_candidate` | `candidate.get('status') != CORE_CANDIDATE_STATUS` |
| `CORE_NOT_ACTIVATED` | `PlannerBlocked` | `runtime/mvp_runtime/binding.py` | 46 | `bind_task_to_core` | `not pointer.is_file()` |
| `COST_MODEL_UNMEASURED` | `ToolError` | `runtime/mvp_runtime/crypto/cost.py` | 700 | `cost_model_for` | `missing` |
| `COST_MODEL_VENUE_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/cost.py` | 694 | `cost_model_for` | `declaration is None` |
| `COUNTERFACTUAL_BOOK_UNVERIFIABLE` | `ToolError` | `runtime/mvp_runtime/crypto/counterfactual.py` | 111 | `load_open_counterfactuals` | `—` |
| `COUNTERFACTUAL_BOOK_UNVERIFIABLE` | `ToolError` | `runtime/mvp_runtime/crypto/counterfactual.py` | 117 | `load_open_counterfactuals` | `rows is None and (not isinstance(book, dict))` |
| `COUNTERFACTUAL_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/counterfactual.py` | 412 | `read_counterfactual_outcomes` | `settlement_id in seen_settlements` |
| `COUNTERFACTUAL_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/counterfactual.py` | 405 | `read_counterfactual_outcomes` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `COUNTERFACTUAL_HISTORY_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/counterfactual.py` | 393 | `read_counterfactual_outcomes` | `—` |
| `CRYPTO_RISK_LIMITS_EXPIRED` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 267 | `resolve_risk_limits` | `not record['valid_from'] <= now <= record['valid_until']` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 101 | `build_risk_limits_record` | `missing` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 108 | `build_risk_limits_record` | `—` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 112 | `build_risk_limits_record` | `numeric[key] != int(numeric[key])` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 123 | `build_risk_limits_record` | `problems` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 129 | `build_risk_limits_record` | `not (isinstance(valid_from, str) and isinstance(valid_until, str) and (valid_from < valid_until…` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 131 | `build_risk_limits_record` | `not (isinstance(registered_by, str) and registered_by.strip())` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 165 | `build_risk_limits_record` | `not isinstance(ids, (list, tuple)) or not ids or (not all((isinstance(i, str) and i.strip() for…` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 170 | `build_risk_limits_record` | `not (isinstance(reason, str) and reason.strip())` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 177 | `build_risk_limits_record` | `len(deduped) != len(ids)` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 195 | `_validate` | `—` |
| `CRYPTO_RISK_LIMITS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 246 | `limits_from_record` | `problems` |
| `CRYPTO_RISK_LIMITS_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 216 | `read_registered_limits` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `CRYPTO_RISK_LIMITS_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 319 | `write_registered_limits` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `CRYPTO_RISK_LIMITS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 210 | `read_registered_limits` | `—` |
| `CRYPTO_RISK_LIMITS_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/risk_limits.py` | 212 | `read_registered_limits` | `not isinstance(data, dict)` |
| `DECISION_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 173 | `build_approval_request` | `expires <= issued` |
| `DEFINITION_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 91 | `build_program_definition` | `not isinstance(definition_input, Mapping)` |
| `DEFINITION_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 94 | `build_program_definition` | `not (isinstance(purpose, str) and purpose.strip())` |
| `DEFINITION_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 100 | `build_program_definition` | `not items` |
| `DEFINITION_PATH_EXISTS` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 264 | `apply_registration` | `definition_path.exists()` |
| `DELIVERY_POINTER_PERSIST_FAILED` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 103 | `record_delivery` | `—` |
| `DOMAIN_EFFECT_MISMATCH` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 478 | `_spend` | `len(_ALLOWED_DOMAINS) > 1` |
| `DOMAIN_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 282 | `_require_domain` | `domain not in _ALLOWED_DOMAINS` |
| `DOMAIN_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 453 | `_spend` | `domain not in _ALLOWED_DOMAINS` |
| `DOOR_REPLY_MALFORMED` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 653 | `call_door` | `size > max_reply_bytes` |
| `DOOR_REPLY_MALFORMED` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 675 | `call_door` | `—` |
| `DOOR_UNREACHABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 661 | `call_door` | `—` |
| `DOOR_UNREACHABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 669 | `call_door` | `not raw` |
| `DUPLICATE_CORE_RULES` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 192 | `build_task` | `len(set(rule_ids)) != len(rule_ids)` |
| `DUPLICATE_PROVIDER` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 601 | `select_env_gated_chain` | `len(set(names)) != len(names)` |
| `DUPLICATE_PROVIDER` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 688 | `select_gated_chain` | `len(set(names)) != len(names)` |
| `DUPLICATE_SELECTOR` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1383 | `resolve_candidates` | `record['candidate_id'] in seen` |
| `EMPTY_CONTENT` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 213 | `_require_content` | `not content` |
| `EMPTY_FEEDBACK` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 232 | `apply_feedback` | `not payload` |
| `EMPTY_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 162 | `resolve_target` | `not isinstance(relative_path, str) or not relative_path.strip()` |
| `EMPTY_QUERY` | `ToolBlocked` | `runtime/mvp_runtime/tools.py` | 96 | `_require_query` | `not isinstance(query, str) or not query.strip()` |
| `EMPTY_REQUEST` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 240 | `build_entry` | `not text` |
| `EMPTY_SEED` | `ToolBlocked` | `runtime/mvp_runtime/naver_research.py` | 250 | `_require_seed` | `not isinstance(seed, str) or not seed.strip()` |
| `EMPTY_SYMBOL` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 777 | `_require_symbol` | `not isinstance(symbol, str) or not symbol.strip()` |
| `ENTRY_NOT_FOUND` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 372 | `_require_entry` | `entry is None` |
| `ENTRY_NOT_FOUND` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 453 | `_current_locked` | `latest is None` |
| `ENV_OPT_IN_WITHDRAWN` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 385 | `assert_authorization` | `os.environ.get(env_var, '').strip().lower() != expected.strip().lower()` |
| `EVENT_FINGERPRINT_FAILED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 115 | `_make_event` | `—` |
| `EVENT_FINGERPRINT_FAILED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 918 | `rechain_events` | `—` |
| `EVENT_STRUCTURE_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 912 | `rechain_events` | `not (isinstance(integrity_block, MutableMapping) and isinstance(payload, MutableMapping) and is…` |
| `EVIDENCE_INVALID` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 312 | `authorize` | `ref_parts.is_absolute() or ref_parts.drive or ref.startswith(('/', '\\')) or ('..' in ref_parts…` |
| `EVIDENCE_INVALID` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 318 | `authorize` | `evidence != root_real and root_real not in evidence.parents` |
| `EVIDENCE_MISSING` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 322 | `authorize` | `not evidence.is_file()` |
| `FEEDBACK_TARGET_UNREADABLE` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 120 | `load_last_delivered` | `—` |
| `FEEDBACK_TARGET_UNREADABLE` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 125 | `load_last_delivered` | `not (isinstance(trace_id, str) and trace_id and isinstance(delivered_at, str) and delivered_at)` |
| `FEEDBACK_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 228 | `apply_feedback` | `store is None` |
| `FEED_ABSENT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1962 | `liquidation_history` | `—` |
| `FEED_ABSENT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1966 | `open_interest_history` | `—` |
| `FINGERPRINT_FAILED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 625 | `build_permission_decision` | `—` |
| `FINGERPRINT_MISMATCH` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 192 | `consume_approval` | `recomputed_fp != approval_rec.get('action_fingerprint')` |
| `FINGERPRINT_MISMATCH` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 428 | `_spend` | `recomputed != record.get('action_fingerprint')` |
| `FINGERPRINT_MISMATCH` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 428 | `run_trial` | `recomputed_fp != approval_rec.get('action_fingerprint')` |
| `FINGERPRINT_UNCOMPUTABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 190 | `consume_approval` | `—` |
| `FINGERPRINT_UNCOMPUTABLE` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 426 | `_spend` | `—` |
| `FINGERPRINT_UNCOMPUTABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 426 | `run_trial` | `—` |
| `FLAG_NOT_ENABLED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 304 | `authorize` | `missing` |
| `FLAG_NOT_ENABLED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 365 | `assert_authorization` | `missing` |
| `FORWARDED_MESSAGE` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 237 | `verify_control_channel` | `message.is_forwarded` |
| `FRONTDESK_ROLE_HASH_MISMATCH` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 188 | `_require_active_role` | `actual != expected` |
| `FRONTDESK_ROLE_INACTIVE` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 165 | `_require_active_role` | `entry.get('status') != 'active'` |
| `FRONTDESK_ROLE_MISCONFIGURED` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 174 | `_require_active_role` | `entry.get('routable') is not False` |
| `FRONTDESK_ROLE_UNRESOLVED` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 155 | `_require_active_role` | `—` |
| `FRONTDESK_ROLE_UNRESOLVED` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 159 | `_require_active_role` | `len(entries) != 1` |
| `FRONTDESK_ROLE_UNRESOLVED` | `OperatorBlocked` | `runtime/mvp_runtime/frontdesk.py` | 184 | `_require_active_role` | `—` |
| `GUARD_NOT_APPROVED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 899 | `submit_and_reconcile` | `not (isinstance(guard_verdict, Mapping) and guard_verdict.get('approved') is True)` |
| `HOST_NOT_ALLOWED` | `ToolBlocked` | `runtime/mvp_runtime/crypto/account.py` | 224 | `__init__` | `host not in ALLOWED_ACCOUNT_HOSTS` |
| `IDEMPOTENCY_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 205 | `apply_dispatch` | `request_id is not None and ledger is None` |
| `INVALID_ASSIGNMENT_MODE` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 110 | `build_role_assignment` | `assignment_mode not in ('normal', 'candidate_trial')` |
| `INVALID_AUTHENTICATED` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 179 | `build_task` | `not isinstance(authenticated, bool)` |
| `INVALID_AUTHORITY` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 88 | `build_role_assignment` | `—` |
| `INVALID_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 593 | `promote_candidate` | `not (isinstance(candidate_id, str) and candidate_id)` |
| `INVALID_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 595 | `promote_candidate` | `not (isinstance(content, str) and content.strip())` |
| `INVALID_CANDIDATE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 890 | `build_memory_promotion_permission_decision` | `not (isinstance(candidate_id, str) and candidate_id)` |
| `INVALID_CANDIDATE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 892 | `build_memory_promotion_permission_decision` | `not (isinstance(content, str) and content.strip())` |
| `INVALID_CANDIDATE_TRANSITION` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 573 | `transition_candidate` | `allowed is None` |
| `INVALID_CANDIDATE_TRANSITION` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 579 | `transition_candidate` | `to_status is None` |
| `INVALID_CHAIN` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 845 | `__init__` | `len(providers) < 2` |
| `INVALID_CHANNEL` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 171 | `build_task` | `channel not in _ALLOWED_CHANNELS` |
| `INVALID_CONTENT` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 211 | `_require_content` | `not isinstance(content, str)` |
| `INVALID_CORE_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 489 | `decide_core_candidate` | `not (isinstance(candidate_id, str) and candidate_id)` |
| `INVALID_DERIVATIVE_KIND` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 686 | `derivative_price_klines` | `kind not in DERIVATIVE_KLINE_PATHS` |
| `INVALID_DERIVATIVE_KIND` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1299 | `derivative_price_klines` | `kind not in DERIVATIVE_KLINE_PATHS` |
| `INVALID_DOMAIN` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 960 | `build_trading_switch_permission_decision` | `not (isinstance(domain, str) and domain.strip())` |
| `INVALID_DOMAIN` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1056 | `build_nonfinancial_resume_permission_decision` | `not (isinstance(domain, str) and domain.strip())` |
| `INVALID_ENCODING` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 87 | `_require_text` | `—` |
| `INVALID_ENCODING` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 125 | `_clean_str_list` | `—` |
| `INVALID_INITIAL_STATUS` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 246 | `build_entry` | `status not in (QUEUED, RUNNING)` |
| `INVALID_INTERVAL` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 425 | `build_schedule` | `not (isinstance(interval_seconds, int) and interval_seconds >= MIN_INTERVAL_SECONDS)` |
| `INVALID_LEVEL` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 522 | `build_permission_decision` | `rank_of(required_permission_level) is None or rank_of(role_permission_ceiling) is None` |
| `INVALID_LIST` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 108 | `_clean_str_list` | `value is not None and isinstance(value, (str, bytes))` |
| `INVALID_LIST` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 116 | `_clean_str_list` | `—` |
| `INVALID_LIST_ITEM` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 121 | `_clean_str_list` | `not isinstance(item, str) or not item.strip()` |
| `INVALID_ORDER_BOOK_LIMIT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 740 | `order_book` | `int(limit) not in ORDER_BOOK_VALID_LIMITS` |
| `INVALID_ORDER_BOOK_LIMIT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1501 | `order_book` | `int(limit) not in ORDER_BOOK_VALID_LIMITS` |
| `INVALID_ORIGIN` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 100 | `_normalize_origin` | `missing` |
| `INVALID_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 166 | `resolve_target` | `any((ord(ch) < 32 for ch in relative_path))` |
| `INVALID_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 181 | `resolve_target` | `':' in relative_path` |
| `INVALID_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 186 | `resolve_target` | `any((part != part.rstrip('. ') for part in candidate.parts))` |
| `INVALID_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 194 | `resolve_target` | `any((part.split('.', 1)[0].lower() in _RESERVED_BASENAMES for part in candidate.parts))` |
| `INVALID_PATH` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 205 | `resolve_target` | `target == base_real` |
| `INVALID_POSITIONING_PERIOD` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 713 | `positioning_history` | `period not in POSITIONING_PERIOD_SECONDS` |
| `INVALID_POSITIONING_PERIOD` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1405 | `positioning_history` | `period not in POSITIONING_PERIOD_SECONDS` |
| `INVALID_POSITIONING_SERIES` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 711 | `positioning_history` | `series not in POSITIONING_PATHS` |
| `INVALID_POSITIONING_SERIES` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1403 | `positioning_history` | `series not in POSITIONING_PATHS` |
| `INVALID_PRIORITY` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 177 | `build_task` | `priority not in _ALLOWED_PRIORITIES` |
| `INVALID_PROBE_BATCH` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1143 | `build_slippage_probe_permission_decision` | `not (isinstance(batch_id, str) and batch_id)` |
| `INVALID_PROBE_BATCH` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1145 | `build_slippage_probe_permission_decision` | `not (isinstance(content_sha256, str) and content_sha256.startswith('sha256:'))` |
| `INVALID_PROBE_BATCH` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1148 | `build_slippage_probe_permission_decision` | `not symbols` |
| `INVALID_PROMOTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1390 | `build_strategy_promotion_permission_decision` | `not candidate_ids or not all((isinstance(c, str) and c for c in candidate_ids))` |
| `INVALID_PROMOTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1392 | `build_strategy_promotion_permission_decision` | `len(strategy_ids) != len(candidate_ids) or not all((isinstance(s, str) and s for s in strategy_…` |
| `INVALID_PROMOTION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1394 | `build_strategy_promotion_permission_decision` | `len(rule_hashes) != len(candidate_ids) or not all((isinstance(h, str) and h for h in rule_hashe…` |
| `INVALID_PROVIDER_ID` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 138 | `activation_path` | `not (isinstance(provider_id, str) and _PROVIDER_ID_PATTERN.match(provider_id))` |
| `INVALID_PROVIDER_ID` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 143 | `activation_path` | `provider_id.split('.', 1)[0] in _RESERVED_BASENAMES` |
| `INVALID_PROVIDER_ID` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 150 | `activation_path` | `path.parent != base` |
| `INVALID_PROVIDER_ID` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 229 | `build_activation_record` | `not _PROVIDER_ID_PATTERN.match(provider_id or '')` |
| `INVALID_REGISTRATION` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1325 | `build_program_registration_permission_decision` | `not (isinstance(definition_sha256, str) and definition_sha256.startswith('sha256:'))` |
| `INVALID_REQUESTER_TYPE` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 173 | `build_task` | `requester_type not in _ALLOWED_REQUESTER_TYPES` |
| `INVALID_REQUIRED_LEVEL` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 310 | `select_role` | `required_rank is None` |
| `INVALID_RETIREMENT` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1461 | `build_strategy_retirement_permission_decision` | `not strategy_ids or not all((isinstance(s, str) and s for s in strategy_ids))` |
| `INVALID_RETIREMENT` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1463 | `build_strategy_retirement_permission_decision` | `len(candidate_ids) != len(strategy_ids)` |
| `INVALID_RETIREMENT` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1465 | `build_strategy_retirement_permission_decision` | `len(rule_hashes) != len(strategy_ids) or not all((isinstance(h, str) and h for h in rule_hashes…` |
| `INVALID_RETIREMENT` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1467 | `build_strategy_retirement_permission_decision` | `not (isinstance(reason, str) and reason.strip())` |
| `INVALID_REVIEW_TRANSITION` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 414 | `transition_review` | `to_status not in _REVIEW_TRANSITIONS.get(from_status, set())` |
| `INVALID_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 69 | `build_role_assignment` | `not isinstance(role, Mapping) or not role.get('role_id') or (not role.get('version')) or (not r…` |
| `INVALID_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1256 | `build_trial_permission_decision` | `not (isinstance(role_id, str) and role_id and isinstance(role_version, str) and role_version)` |
| `INVALID_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1258 | `build_trial_permission_decision` | `not (isinstance(definition_sha256, str) and definition_sha256)` |
| `INVALID_SENSITIVITY` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 175 | `build_task` | `data_sensitivity not in _ALLOWED_SENSITIVITY` |
| `INVALID_STOP_REF` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 967 | `build_trading_switch_permission_decision` | `not (isinstance(stop_ref, str) and stop_ref.strip())` |
| `INVALID_STOP_REF` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1061 | `build_nonfinancial_resume_permission_decision` | `not (isinstance(stop_ref, str) and stop_ref.strip())` |
| `INVALID_STOP_SUMMARY` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 971 | `build_trading_switch_permission_decision` | `not (isinstance(stop_summary, str) and stop_summary.strip())` |
| `INVALID_STOP_SUMMARY` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1065 | `build_nonfinancial_resume_permission_decision` | `not (isinstance(stop_summary, str) and stop_summary.strip())` |
| `INVALID_SYMBOL` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 779 | `_require_symbol` | `not (pattern or _SYMBOL_PATTERN).fullmatch(symbol)` |
| `INVALID_TASK` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 192 | `classify_task` | `not isinstance(task, Mapping)` |
| `INVALID_TIMEFRAME` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 785 | `_require_timeframe` | `timeframe not in TIMEFRAMES` |
| `INVALID_TIMESTAMP` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 98 | `_validate_timestamp` | `not isinstance(value, str)` |
| `INVALID_TIMESTAMP` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 102 | `_validate_timestamp` | `—` |
| `INVALID_TIMESTAMP` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 486 | `_parse_ts` | `—` |
| `INVALID_TRIAL_REQUEST` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 1260 | `build_trial_permission_decision` | `not (isinstance(trial_request, str) and trial_request.strip())` |
| `INVALID_TTL` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 161 | `build_approval_request` | `requested < 1` |
| `INVALID_VALIDATED_MEMORY` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 409 | `build_core_candidate` | `not (isinstance(validated_id, str) and validated_id)` |
| `INVALID_VALIDATED_MEMORY` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 411 | `build_core_candidate` | `not (isinstance(content, str) and content.strip())` |
| `INVENTORY_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 376 | `_apply_job` | `not isinstance(inventory, dict) or not inventory` |
| `JOB_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 347 | `_apply_job` | `not isinstance(job, str) or job.strip() not in _ALLOWED_JOBS` |
| `KILL_STATE_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 181 | `apply_memory_command` | `control_store is None` |
| `KILL_STATE_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 309 | `apply_registry_command` | `control_store is None` |
| `KIND_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 174 | `apply_dispatch` | `kind not in _ALLOWED_KINDS` |
| `KIND_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 215 | `apply_work` | `kind not in _ALLOWED_KINDS` |
| `KNOWLEDGE_CONTENT_AMBIGUOUS` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 87 | `add_document` | `(text is None) == (pdf_base64 is None)` |
| `KNOWLEDGE_DOCUMENT_DATE_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 307 | `_optional_timestamp` | `not isinstance(value, str) or not value.strip()` |
| `KNOWLEDGE_DOCUMENT_DATE_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 316 | `_optional_timestamp` | `—` |
| `KNOWLEDGE_LIMIT_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 196 | `_resolve_limit` | `not isinstance(limit, int) or isinstance(limit, bool) or limit < 1` |
| `KNOWLEDGE_LIMIT_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 198 | `_resolve_limit` | `limit > MAX_LIMIT` |
| `KNOWLEDGE_QUESTION_REQUIRED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 147 | `query` | `not isinstance(question, str) or not question.strip()` |
| `KNOWLEDGE_QUESTION_TOO_LONG` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 150 | `query` | `len(question) > MAX_QUESTION_CHARS` |
| `KNOWLEDGE_RECORD_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 266 | `validate_document` | `—` |
| `KNOWLEDGE_SENSITIVITY_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 224 | `build_document` | `data_sensitivity not in SENSITIVITIES` |
| `KNOWLEDGE_SOURCE_TYPE_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 219 | `build_document` | `source_type not in SOURCE_TYPES` |
| `KNOWLEDGE_TAGS_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 282 | `_clean_tags` | `not isinstance(tags, (list, tuple))` |
| `KNOWLEDGE_TAGS_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 286 | `_clean_tags` | `not isinstance(tag, str) or not tag.strip()` |
| `KNOWLEDGE_TAGS_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 291 | `_clean_tags` | `len(cleaned) > MAX_TAGS` |
| `KNOWLEDGE_TEXT_REQUIRED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 208 | `build_document` | `not isinstance(text, str) or not text.strip()` |
| `KNOWLEDGE_TEXT_TOO_LARGE` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/store.py` | 213 | `build_document` | `len(text) > MAX_TEXT_CHARS` |
| `KNOWLEDGE_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/knowledge/store.py` | 162 | `_read_unlocked` | `—` |
| `KNOWLEDGE_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/knowledge/store.py` | 111 | `add` | `—` |
| `LEDGER_INVALID_KEEP` | `PersistenceError` | `runtime/mvp_runtime/retention.py` | 141 | `rotate_file` | `not (isinstance(keep_rows, int) and keep_rows > 0)` |
| `LEDGER_PROTECTED_FROM_ROTATION` | `PersistenceError` | `runtime/mvp_runtime/retention.py` | 131 | `rotate_file` | `filename in PROTECTED_FILES` |
| `LEDGER_READ_FAILED` | `PersistenceError` | `runtime/mvp_runtime/bridge_idempotency.py` | 275 | `_live_record` | `—` |
| `LEDGER_ROTATION_FAILED` | `PersistenceError` | `runtime/mvp_runtime/retention.py` | 185 | `rotate_file` | `—` |
| `LEDGER_UNAVAILABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 475 | `run_trial` | `—` |
| `LEDGER_UNKNOWN_FILE` | `PersistenceError` | `runtime/mvp_runtime/retention.py` | 136 | `rotate_file` | `filename not in ROTATABLE_FILES` |
| `LEDGER_UNKNOWN_RECORD_KIND` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 149 | `append_records` | `unknown` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/control.py` | 466 | `_mode_from_ledger` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 236 | `_tip` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 245 | `_tip` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 253 | `read_blocks` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 287 | `iter_records` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 327 | `iter_records_with_archive` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 330 | `iter_records_with_archive` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 354 | `read_scheduler_events` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 365 | `read_audit_events` | `—` |
| `LEDGER_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 389 | `health` | `not entry['present']` |
| `LEDGER_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/bridge_idempotency.py` | 254 | `_append` | `—` |
| `LEDGER_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 141 | `append_audit_events` | `—` |
| `LEDGER_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/store.py` | 195 | `_append_locked` | `—` |
| `LIFECYCLE_DECISION_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/lifecycle.py` | 355 | `operator_retirement_decision` | `not (isinstance(strategy_id, str) and strategy_id)` |
| `LIFECYCLE_DECISION_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1675 | `update_statuses` | `not (isinstance(strategy_id, str) and strategy_id and isinstance(new_status, str))` |
| `LIFECYCLE_TERMINAL_IMMUTABLE` | `ToolError` | `runtime/mvp_runtime/crypto/lifecycle.py` | 360 | `operator_retirement_decision` | `current in TERMINAL_STATUSES` |
| `LIFECYCLE_TERMINAL_IMMUTABLE` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1680 | `update_statuses` | `str(entry.get('status')) in TERMINAL_STATUSES` |
| `LIFECYCLE_TERMINAL_IMMUTABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 80 | `resolve_pool_entries` | `terminal` |
| `LIFECYCLE_UNKNOWN_STRATEGY` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1678 | `update_statuses` | `entry is None` |
| `LIFECYCLE_UNKNOWN_STRATEGY` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 77 | `resolve_pool_entries` | `unknown` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 78 | `_price` | `isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 112 | `limit_entry_fill` | `direction not in (LONG, SHORT)` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 114 | `limit_entry_fill` | `isinstance(limit_price, bool) or not isinstance(limit_price, (int, float)) or (not limit_price …` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 116 | `limit_entry_fill` | `isinstance(tick_size, bool) or not isinstance(tick_size, (int, float)) or (not tick_size > 0)` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 122 | `limit_entry_fill` | `isinstance(timeout_bars, bool) or not isinstance(timeout_bars, int) or timeout_bars < 1` |
| `LIMIT_FILL_UNPRICEABLE` | `ToolError` | `runtime/mvp_runtime/crypto/limit_entry.py` | 136 | `limit_entry_fill` | `not isinstance(bar, Mapping)` |
| `LIVE_BRACKET_BREAKER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 790 | `read_bracket_failures` | `—` |
| `LIVE_BRACKET_BREAKER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 792 | `read_bracket_failures` | `not isinstance(data, dict)` |
| `LIVE_BRACKET_BREAKER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 799 | `read_bracket_failures` | `—` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 101 | `build_live_trading_budget_record` | `venue != SUPPORTED_VENUE` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 105 | `build_live_trading_budget_record` | `missing` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 112 | `build_live_trading_budget_record` | `—` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 117 | `build_live_trading_budget_record` | `numeric[key] <= 0` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 121 | `build_live_trading_budget_record` | `float(caps[count_key]) != int(caps[count_key])` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 124 | `build_live_trading_budget_record` | `numeric['absolute_max_notional_usdt'] > HARD_CEILING_USDT` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 130 | `build_live_trading_budget_record` | `numeric['max_order_notional_usdt'] > numeric['absolute_max_notional_usdt']` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 139 | `build_live_trading_budget_record` | `not symbols` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 142 | `build_live_trading_budget_record` | `not (isinstance(valid_from, str) and isinstance(valid_until, str) and (valid_from < valid_until…` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 144 | `build_live_trading_budget_record` | `not (isinstance(registered_by, str) and registered_by.strip())` |
| `LIVE_BUDGET_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 180 | `_validate` | `—` |
| `LIVE_BUDGET_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 201 | `read_registered_budget` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `LIVE_BUDGET_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 262 | `write_registered_budget` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `LIVE_BUDGET_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 195 | `read_registered_budget` | `—` |
| `LIVE_BUDGET_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_budget.py` | 197 | `read_registered_budget` | `not isinstance(data, dict)` |
| `LIVE_COUNTER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 661 | `count_today` | `—` |
| `LIVE_COUNTER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 663 | `count_today` | `not isinstance(data, dict)` |
| `LIVE_COUNTER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 667 | `count_today` | `—` |
| `LIVE_COUNTER_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 706 | `record_submission` | `path.is_file()` |
| `LIVE_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/live_pnl.py` | 334 | `read_live_outcomes` | `outcome_id in seen_outcome_ids` |
| `LIVE_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/live_pnl.py` | 339 | `read_live_outcomes` | `settlement_id in seen_settlement_ids` |
| `LIVE_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_pnl.py` | 330 | `read_live_outcomes` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `LIVE_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/live_pnl.py` | 441 | `daily_realized_pnl` | `—` |
| `LIVE_HISTORY_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_pnl.py` | 319 | `read_live_outcomes` | `—` |
| `LIVE_ORDER_PERMDEC_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 1188 | `build_live_order_audit` | `not (isinstance(permdec_id, str) and permdec_id)` |
| `LIVE_POSITION_STAGE_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 298 | `_read_position_file` | `data.get('stage') != LIVE_STAGE` |
| `LIVE_POSITION_STATE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 290 | `_read_position_file` | `—` |
| `LIVE_POSITION_SYMBOL_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 269 | `live_position_path` | `not symbol or not symbol.replace('_', '').replace('-', '').isalnum()` |
| `LIVE_POSITION_SYMBOL_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 275 | `live_position_path` | `path.parent != resolved_base` |
| `LIVE_POSITION_UNATTRIBUTABLE` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 249 | `position_symbol` | `not symbol` |
| `MALFORMED_ANALYSIS` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 509 | `_require_analysis` | `not isinstance(analysis, Mapping)` |
| `MALFORMED_ANALYSIS` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 512 | `_require_analysis` | `missing` |
| `MALFORMED_ANALYSIS` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 515 | `_require_analysis` | `not isinstance(summary, str) or not summary.strip()` |
| `MALFORMED_ANALYSIS` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 518 | `_require_analysis` | `not isinstance(facts, list)` |
| `MALFORMED_BRACKET_LEG` | `ToolError` | `runtime/mvp_runtime/crypto/live_leg.py` | 287 | `build_bracket_intent` | `leg not in ('SL', 'TP')` |
| `MALFORMED_DIRECTION` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 223 | `build_live_order_intent` | `direction not in {'LONG', 'SHORT'}` |
| `MALFORMED_DIRECTION` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 176 | `build_live_position` | `direction not in {'LONG', 'SHORT'}` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 262 | `build_order_request` | `not (isinstance(symbol, str) and symbol)` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 264 | `build_order_request` | `side not in ('BUY', 'SELL')` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 266 | `build_order_request` | `not (isinstance(client_order_id, str) and CLIENT_ORDER_ID_PATTERN.match(client_order_id))` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 272 | `build_order_request` | `order_type not in SUPPORTED_ORDER_TYPES` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 291 | `build_order_request` | `not (isinstance(stop_price, (int, float)) and stop_price > 0)` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 302 | `build_order_request` | `working_type not in WORKING_TYPES` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 312 | `build_order_request` | `close_position` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 320 | `build_order_request` | `not (isinstance(price, (int, float)) and price > 0)` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 327 | `build_order_request` | `time_in_force not in TIMES_IN_FORCE` |
| `MALFORMED_LIVE_ORDER_INTENT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 339 | `build_order_request` | `not (isinstance(quantity, (int, float)) and quantity > 0)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/bridge_idempotency.py` | 86 | `request_id_of` | `not isinstance(raw, str) or not raw.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/bridge_idempotency.py` | 91 | `request_id_of` | `len(value) > MAX_REQUEST_ID_LENGTH` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/bridge_idempotency.py` | 110 | `fingerprint` | `—` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 153 | `apply_dispatch` | `not isinstance(request, dict)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 172 | `apply_dispatch` | `isinstance(raw_kind, str) and raw_kind.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 188 | `apply_dispatch` | `not isinstance(raw_seeds, str) or not raw_seeds.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 192 | `apply_dispatch` | `len(raw_seeds) > MAX_SEED_CHARS` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/knowledge_bridge.py` | 113 | `apply_knowledge` | `not isinstance(request, dict)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/knowledge_bridge.py` | 117 | `apply_knowledge` | `not isinstance(command, str) or not command.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 187 | `apply_work` | `not isinstance(request, dict)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 210 | `apply_work` | `not isinstance(kind, str) or not kind.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 249 | `apply_work` | `not isinstance(raw_seeds, str) or not raw_seeds.strip() or len(raw_seeds) > MAX_SEED_CHARS` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/read_bridge.py` | 97 | `apply_read` | `not isinstance(request, dict)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/read_bridge.py` | 101 | `apply_read` | `not isinstance(command, str) or not command.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/read_bridge.py` | 112 | `apply_read` | `argument is not None and (not isinstance(argument, str))` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 155 | `decode_request` | `—` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 279 | `_require_domain` | `not isinstance(raw, str) or not raw.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 297 | `_require_scope` | `not isinstance(raw, str) or not raw.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 573 | `apply_switch` | `not isinstance(request, dict)` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 585 | `apply_switch` | `not isinstance(command, str) or not command.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 617 | `apply_switch` | `isinstance(raw_mode, str) and raw_mode.strip()` |
| `MALFORMED_REQUEST` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 640 | `apply_switch` | `approval_id is not None and (not isinstance(approval_id, str) or not approval_id.strip())` |
| `MALFORMED_RESPONSE` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 499 | `_parse_hosted_response` | `—` |
| `MALFORMED_RESPONSE` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 501 | `_parse_hosted_response` | `not isinstance(analysis, dict) or any((k not in analysis for k in _REQUIRED_ANALYSIS_KEYS))` |
| `MALFORMED_RESPONSE` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 509 | `_parse_hosted_response` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/account.py` | 288 | `fill_history` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/account.py` | 325 | `_signed_get` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/account.py` | 336 | `_build` | `not isinstance(account, dict)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1178 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1180 | `_parse` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1190 | `_parse` | `not isinstance(row, list) or len(row) < 7` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1211 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1263 | `funding_history` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1362 | `_parse_derivative_page` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1364 | `_parse_derivative_page` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1369 | `_parse_derivative_page` | `not isinstance(row, list) or len(row) < 7` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1373 | `_parse_derivative_page` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1462 | `_parse_positioning_page` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1464 | `_parse_positioning_page` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1469 | `_parse_positioning_page` | `not isinstance(row, dict)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1475 | `_parse_positioning_page` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1522 | `order_book` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1524 | `order_book` | `not isinstance(payload, dict)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1543 | `_parse_book_side` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1547 | `_parse_book_side` | `not isinstance(row, (list, tuple)) or len(row) < 2` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1551 | `_parse_book_side` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1557 | `_parse_book_side` | `not (price > 0.0 and quantity > 0.0)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1590 | `exchange_info` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1592 | `exchange_info` | `not isinstance(payload, dict)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1698 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1700 | `_parse` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1709 | `_parse` | `not isinstance(row, dict)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1729 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1765 | `live_symbols` | `not isinstance(listings, list) or not isinstance(metas, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1767 | `live_symbols` | `len(listings) != len(metas)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1798 | `_info` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2022 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2026 | `_parse` | `not isinstance(payload, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2042 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2107 | `_parse_open_interest` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2111 | `_parse_open_interest` | `not isinstance(payload, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2134 | `_parse_open_interest` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 650 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 652 | `_parse` | `not isinstance(rows, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 792 | `trend` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 828 | `competition` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 830 | `competition` | `not isinstance(items, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 294 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 296 | `_parse` | `not isinstance(results, list)` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 384 | `_parse` | `—` |
| `MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 386 | `_parse` | `not isinstance(results, list)` |
| `MEMORY_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 144 | `apply_memory_command` | `working_memory is None` |
| `MEMORY_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 175 | `apply_memory_command` | `ledger is None` |
| `MISSING_BRACKET_QUANTITY` | `ToolError` | `runtime/mvp_runtime/crypto/live_leg.py` | 308 | `build_bracket_intent` | `not (isinstance(quantity, (int, float)) and quantity > 0)` |
| `MISSING_CORE_RULES` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 190 | `build_task` | `not rule_ids` |
| `MISSING_CREATOR` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 430 | `build_schedule` | `not (isinstance(created_by, str) and created_by.strip())` |
| `MISSING_ENTRY_PRICE` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 184 | `build_live_position` | `entry_price <= 0` |
| `MISSING_OPERATOR` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 418 | `build_core_candidate` | `not (isinstance(proposed_by, str) and proposed_by.strip())` |
| `MISSING_OPERATOR` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 496 | `decide_core_candidate` | `not (isinstance(decided_by, str) and decided_by.strip())` |
| `MISSING_OPERATOR` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 597 | `promote_candidate` | `not (isinstance(promoted_by, str) and promoted_by.strip())` |
| `MISSING_OPERATOR` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 139 | `create_program_request` | `not (isinstance(requested_by, str) and requested_by.strip())` |
| `MISSING_OPERATOR` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 384 | `_require_operator` | `not (isinstance(actor, str) and actor.strip())` |
| `MISSING_ORDER_NOTIONAL` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 229 | `build_live_order_intent` | `notional_usdt <= 0` |
| `MISSING_ORDER_QUANTITY` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 227 | `build_live_order_intent` | `quantity <= 0` |
| `MISSING_POSITION_QUANTITY` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 182 | `build_live_position` | `quantity <= 0` |
| `MISSING_RATIONALE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 420 | `build_core_candidate` | `not (isinstance(rationale, str) and rationale.strip())` |
| `MISSING_REASON` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 498 | `decide_core_candidate` | `not (isinstance(reason, str) and reason.strip())` |
| `MISSING_REASON` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 599 | `promote_candidate` | `not (isinstance(reason, str) and reason.strip())` |
| `MISSING_REASON` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 169 | `apply_memory_command` | `not reason` |
| `MISSING_REASON` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 141 | `create_program_request` | `not (isinstance(reason, str) and reason.strip())` |
| `MISSING_REASON` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 386 | `_require_operator` | `not (isinstance(reason, str) and reason.strip())` |
| `MISSING_REQUEST` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 428 | `build_schedule` | `kind == KIND_TASK and (not request)` |
| `MISSING_REQUESTER` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 244 | `build_entry` | `not (isinstance(requester_id, str) and requester_id.strip())` |
| `MISSING_SYMBOL` | `ToolError` | `runtime/mvp_runtime/crypto/live_order.py` | 225 | `build_live_order_intent` | `not symbol` |
| `MISSING_SYMBOL` | `ToolError` | `runtime/mvp_runtime/crypto/live_position.py` | 178 | `build_live_position` | `not symbol` |
| `MISSING_TASK_ID` | `ControlBlocked` | `runtime/mvp_runtime/control.py` | 602 | `apply_command` | `not (isinstance(arg, str) and arg.strip())` |
| `MODE_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 619 | `apply_switch` | `mode not in _DISABLE_MODES` |
| `NOT_ALLOWED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 540 | `build_permission_decision` | `disposition not in _BUILDABLE_DISPOSITIONS` |
| `NOT_ALLOWED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 547 | `build_permission_decision` | `disposition == EXECUTE_AND_REPORT and permission_scope not in _EXECUTE_AND_REPORT_SCOPES` |
| `NOT_ALLOWED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 554 | `build_permission_decision` | `disposition == APPROVAL_REQUIRED and permission_scope not in _APPROVAL_REQUIRED_SCOPES` |
| `NOT_ALLOWED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 561 | `build_permission_decision` | `disposition == 'BLOCK' and permission_scope not in _BLOCK_EVIDENCE_SCOPES` |
| `NOT_ALLOWED` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 746 | `build_resource_refusal_permission_decision` | `permission_scope not in _BLOCK_EVIDENCE_SCOPES` |
| `NOT_APPROVAL_REQUIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 149 | `build_approval_request` | `decision != 'APPROVAL_REQUIRED'` |
| `NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 322 | `build_consumed_record` | `status != STATUS_APPROVED` |
| `NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 169 | `consume_approval` | `status != approval_mod.STATUS_APPROVED` |
| `NOT_APPROVED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 406 | `_spend` | `status != approval_mod.STATUS_APPROVED` |
| `NOT_APPROVED` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 406 | `run_trial` | `status != approval_mod.STATUS_APPROVED` |
| `NOT_AUTHORIZED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 357 | `assert_authorization` | `not isinstance(authorization, Authorization)` |
| `NOT_A_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 587 | `promote_candidate` | `not isinstance(candidate, Mapping)` |
| `NOT_A_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 589 | `promote_candidate` | `candidate.get('status') != CANDIDATE_STATUS or candidate.get('scope') != CANDIDATE_SCOPE` |
| `NOT_A_CANDIDATE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 282 | `select_candidate_role` | `status != 'candidate' or role.get('routable') is not False` |
| `NOT_A_CORE_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 481 | `decide_core_candidate` | `not isinstance(candidate, Mapping) or candidate.get('scope') != CORE_CANDIDATE_SCOPE` |
| `NOT_BOUND` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 66 | `build_role_assignment` | `not (isinstance(ccb, str) and ccb.startswith('ccb-'))` |
| `NOT_BOUND` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 519 | `build_permission_decision` | `not (isinstance(ccb, str) and ccb.startswith('ccb-'))` |
| `NOT_BOUND` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 208 | `run_validation_worker` | `not (isinstance(ccb, str) and ccb.startswith('ccb-'))` |
| `NOT_BOUND` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 649 | `run_analysis_worker` | `not (isinstance(ccb, str) and ccb.startswith('ccb-'))` |
| `NOT_EXPIRED` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 289 | `expire` | `not is_expired(approval, now=now)` |
| `NOT_INDEPENDENT` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 210 | `run_validation_worker` | `validator_assignment.get('role_id') == agent_output.get('role_id')` |
| `NOT_PENDING` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 92 | `_require_pending` | `status != STATUS_PENDING` |
| `NOT_PRIVATE_CHANNEL` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 235 | `verify_control_channel` | `message.channel != PRIMARY_CHANNEL or message.chat_type != 'private'` |
| `NOT_RECEIVED` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 196 | `classify_task` | `lifecycle.get('status') != 'RECEIVED'` |
| `NOT_VALIDATED_MEMORY` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 399 | `build_core_candidate` | `not isinstance(validated_entry, Mapping)` |
| `NOT_VALIDATED_MEMORY` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 402 | `build_core_candidate` | `validated_entry.get('status') != VALIDATED_STATUS or validated_entry.get('scope') != VALIDATED_…` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/crypto/account.py` | 298 | `_signed_get` | `not api_key or not api_secret` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1996 | `liquidation_history` | `not api_key` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2079 | `open_interest_history` | `not api_key` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 586 | `_headers` | `missing` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 717 | `_headers` | `missing` |
| `NO_API_KEY` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 585 | `generate` | `not api_key` |
| `NO_API_KEY` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 714 | `generate` | `not api_key` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/tools.py` | 266 | `search` | `not api_key` |
| `NO_API_KEY` | `ToolError` | `runtime/mvp_runtime/tools.py` | 357 | `search` | `not api_key` |
| `NO_APPROVAL_ID` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 155 | `build_approval_request` | `not (isinstance(approval_id, str) and approval_id.startswith('approval_'))` |
| `NO_APPROVAL_ID` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 542 | `apply_command` | `not approval_id` |
| `NO_BOT_TOKEN` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 871 | `_assert` | `not token` |
| `NO_CONSUMPTION_REF` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 326 | `build_consumed_record` | `not (isinstance(consumption_ref, str) and consumption_ref.strip())` |
| `NO_DECISION_REASON` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 261 | `record_decision` | `not (isinstance(reason, str) and reason.strip())` |
| `NO_DELIVERABLE` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 288 | `apply_registry_command` | `entry.status != task_registry.DELIVERED` |
| `NO_FEEDBACK_TARGET` | `OperatorBlocked` | `runtime/mvp_runtime/operator_feedback.py` | 237 | `apply_feedback` | `target is None` |
| `NO_MODEL_BUDGET` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 217 | `run_validation_worker` | `not isinstance(max_model_calls, int) or max_model_calls < 1` |
| `NO_MODEL_BUDGET` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 656 | `run_analysis_worker` | `not isinstance(max_model_calls, int) or max_model_calls < 1` |
| `NO_ORDER_API_KEY` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 610 | `_signed_request` | `not api_key or not api_secret` |
| `NO_ROLE_OUTPUT_CONTRACT` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 129 | `role_output_spec` | `not isinstance(contract, Mapping) or not contract` |
| `NO_ROUTABLE_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 327 | `select_role` | `not candidates` |
| `NO_TRIAL_AUTHORIZATION` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 114 | `build_role_assignment` | `trial and (not (isinstance(trial_authorization_ref, str) and trial_authorization_ref.strip()))` |
| `NO_VERIFICATION_REF` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 259 | `record_decision` | `not (isinstance(verification.verification_ref, str) and verification.verification_ref.strip())` |
| `OBSERVATION_INCOMPLETE` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 260 | `observe_completed_run` | `not (task_id and trace_id and ccb.startswith('ccb-') and isinstance(task_revision, int) and (ta…` |
| `OFFSET_PERSIST_FAILED` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 931 | `_save_offset` | `—` |
| `OFFSET_STATE_MALFORMED` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 904 | `_load_offset` | `—` |
| `OI_INTERVAL_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 2069 | `open_interest_history` | `interval not in OI_INTERVALS` |
| `OI_SYMBOL_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/oi_store.py` | 204 | `append_rows` | `not name` |
| `ORDERBOOK_CROSSED` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 183 | `summarize_book` | `not best_bid < best_ask` |
| `ORDERBOOK_DEPTH_EMPTY` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 189 | `summarize_book` | `total <= 0.0` |
| `ORDERBOOK_SIDE_EMPTY` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 180 | `summarize_book` | `not bids or not asks` |
| `ORDERBOOK_SYMBOL_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 301 | `append_snapshot` | `not name` |
| `ORDERBOOK_TIMESTAMP_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 140 | `period_start` | `—` |
| `ORDERBOOK_TIMESTAMP_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/orderbook_store.py` | 304 | `append_snapshot` | `not stamp` |
| `ORDER_HOST_NOT_ALLOWED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 584 | `__init__` | `host not in ALLOWED_ORDER_HOSTS` |
| `ORDER_MALFORMED_RESULT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 649 | `_signed_request` | `—` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 669 | `submit` | `code == VENUE_DUPLICATE_CLIENT_ORDER_ID` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 675 | `submit` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 749 | `fetch_order` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 784 | `open_orders` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 808 | `algo_open_orders` | `code is not None` |
| `ORDER_REJECTED` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 849 | `cancel_order` | `code is not None` |
| `ORDER_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 639 | `_signed_request` | `code is None` |
| `ORDER_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/live_execution.py` | 645 | `_signed_request` | `—` |
| `OUTCOME_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1197 | `read_outcomes` | `outcome_id in seen_outcome_ids` |
| `OUTCOME_HISTORY_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1202 | `read_outcomes` | `settlement_id in seen_settlement_ids` |
| `OUTCOME_HISTORY_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1191 | `read_outcomes` | `not isinstance(stored, str) or integrity.sha256_record(body) != stored` |
| `OUTCOME_HISTORY_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1179 | `read_outcomes` | `—` |
| `OUTPUT_SCHEMA_INVALID` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 745 | `run_analysis_worker` | `—` |
| `OUT_OF_MVP_SCOPE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 204 | `classify_task` | `_READ_ONLY_CONSTRAINT not in constraints` |
| `PATH_ESCAPE` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 175 | `resolve_target` | `'..' in candidate.parts` |
| `PATH_ESCAPE` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 203 | `resolve_target` | `target != base_real and base_real not in target.parents` |
| `PATH_TOO_LONG` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 164 | `resolve_target` | `len(relative_path) > MAX_PATH_CHARS` |
| `PATTERN_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 411 | `transition_review` | `latest is None` |
| `PATTERN_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 506 | `create_program_candidate` | `latest is None` |
| `PATTERN_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 160 | `main` | `not args.target` |
| `PATTERN_NOT_FOUND` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 188 | `main` | `not args.target` |
| `PDF_BACKEND_UNKNOWN` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 258 | `_run_backend` | `—` |
| `PDF_BASE64_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 215 | `_decode_pdf` | `—` |
| `PDF_BASE64_REQUIRED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/service.py` | 209 | `_decode_pdf` | `not isinstance(payload, str) or not payload.strip()` |
| `PDF_CORRUPT` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 299 | `_extract_with_pdftotext` | `completed.returncode == 1` |
| `PDF_ENCRYPTED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 305 | `_extract_with_pdftotext` | `completed.returncode == 3` |
| `PDF_ENCRYPTED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 341 | `_extract_with_pypdf` | `getattr(reader, 'is_encrypted', False)` |
| `PDF_EXTRACTION_FAILED` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 162 | `extract_pdf_text` | `—` |
| `PDF_EXTRACTION_TIMEOUT` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 290 | `_extract_with_pdftotext` | `—` |
| `PDF_INPUT_EMPTY` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 237 | `_check_pdf_bytes` | `not data` |
| `PDF_INPUT_INVALID` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 235 | `_check_pdf_bytes` | `not isinstance(data, (bytes, bytearray))` |
| `PDF_NOT_A_PDF` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 247 | `_check_pdf_bytes` | `PDF_MAGIC not in bytes(data[:1024])` |
| `PDF_NO_BACKEND` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 121 | `extract_pdf_text` | `not backends` |
| `PDF_NO_TEXT_LAYER` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 156 | `extract_pdf_text` | `failures and all(('PDF_NO_TEXT_LAYER' in failure for failure in failures))` |
| `PDF_TEXT_TOO_LARGE` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 353 | `_extract_with_pypdf` | `total > MAX_TEXT_CHARS` |
| `PDF_TEXT_TOO_LARGE` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 363 | `_check_text_size` | `len(text) > MAX_TEXT_CHARS` |
| `PDF_TOO_LARGE` | `KnowledgeBlocked` | `runtime/mvp_runtime/knowledge/pdf_text.py` | 239 | `_check_pdf_bytes` | `len(data) > MAX_PDF_BYTES` |
| `PEER_CREDENTIALS_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 350 | `authorize_peer` | `creds is None` |
| `PEER_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 358 | `authorize_peer` | `creds[1] not in self.allowed_client_uids` |
| `PERMISSION_DECISION_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 551 | `apply_command` | `permission_decision is None` |
| `PERMISSION_DECISION_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 180 | `consume_approval` | `permission_decision is None` |
| `PERMISSION_DECISION_MISSING` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 417 | `_spend` | `decision is None` |
| `PERMISSION_DECISION_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 416 | `run_trial` | `permission_decision is None` |
| `PERMISSION_NOT_ALLOW` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 95 | `build_role_assignment` | `permission_decision.get('decision', {}).get('permission_decision') != 'ALLOW'` |
| `PERMISSION_NOT_EXECUTABLE_READ_ONLY` | `KernelBlocked` | `runtime/read_only_kernel/policy.py` | 18 | `adapt_policy` | `permission.get('evaluation_status') != 'DECIDED'` |
| `PERMISSION_SCHEMA_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 716 | `build_permission_decision` | `—` |
| `PERMISSION_SEMANTICS_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 719 | `build_permission_decision` | `issues` |
| `PLANNED_TASK_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/prime.py` | 285 | `plan_task` | `—` |
| `PLANNED_TASK_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/trial.py` | 310 | `_plan_trial_run` | `—` |
| `POLICY_UNAVAILABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 101 | `_policy` | `—` |
| `POLICY_UNAVAILABLE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 528 | `build_permission_decision` | `—` |
| `POOL_CONTEXT_CAP_EXCEEDED` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 980 | `assert_pool_within_size_cap` | `over` |
| `POOL_SILENT_REACTIVATION` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 829 | `assert_no_silent_reactivation` | `—` |
| `POOL_SIZE_CAP_EXCEEDED` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 963 | `assert_pool_within_size_cap` | `len(occupying) > MAX_ROUTABLE_STRATEGIES` |
| `POSITIONING_SERIES_UNKNOWN` | `ToolError` | `runtime/mvp_runtime/crypto/positioning_store.py` | 258 | `append_rows` | `series not in POSITIONING_SERIES` |
| `POSITIONING_SYMBOL_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/positioning_store.py` | 256 | `append_rows` | `not name` |
| `POSITION_CONTEXT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1021 | `__post_init__` | `not (isinstance(value, str) and _CONTEXT_PART_PATTERN.match(value))` |
| `POSITION_CONTEXT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1026 | `__post_init__` | `value.split('.', 1)[0].upper() in RESERVED_BASENAMES` |
| `POSITION_CONTEXT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1072 | `position_path` | `path.parent != resolved_base` |
| `POSITION_CONTEXT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1107 | `load_open_position` | `PositionContext.from_position(stored) != context` |
| `POSITION_CONTEXT_MISMATCH` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1142 | `list_open_positions` | `blocker is not None` |
| `POSITION_STATE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1086 | `_read_position_file` | `—` |
| `PROBE_BATCH_EXHAUSTED` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 469 | `select_cell` | `not empty` |
| `PROBE_BUDGET_EXCEEDED` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 269 | `assert_batch_budget` | `worst > cap` |
| `PROBE_CELL_OPEN` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 462 | `select_cell` | `opened is not None` |
| `PROBE_CELL_OPEN` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 739 | `abandon_plan` | `open_index is not None` |
| `PROBE_FILTERS_UNAVAILABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 574 | `probe_quantity` | `filters is None or not filters.valid()` |
| `PROBE_FILTERS_UNAVAILABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 597 | `probe_stop_price` | `trigger <= 0` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 200 | `build_batch_params` | `not names` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 203 | `build_batch_params` | `duplicates` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 217 | `build_batch_params` | `not name.endswith('USDT') or name == 'USDT' or (not name.isalnum())` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 223 | `build_batch_params` | `int(repeats) < 1 or int(timeout_minutes) < 1` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 225 | `build_batch_params` | `not float(stop_bps) > 0` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 229 | `build_batch_params` | `n != grid` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 235 | `build_batch_params` | `not (float(per_probe_notional_cap_usdt) > 0 and float(budget_cap_usdt) > 0)` |
| `PROBE_PARAMS_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 727 | `abandon_plan` | `not isinstance(reason, str) or not reason.strip()` |
| `PROBE_PLAN_EXISTS` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 703 | `confirm_probe_batch` | `existing is not None and existing['status'] == PLAN_ACTIVE` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 369 | `validate_plan` | `not isinstance(plan, dict)` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 373 | `validate_plan` | `unknown or missing` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 379 | `validate_plan` | `plan['plan_version'] != PLAN_VERSION` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 381 | `validate_plan` | `plan['status'] not in PLAN_STATUSES` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 384 | `validate_plan` | `not isinstance(params, dict) or set(params) != _PARAM_KEYS` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 386 | `validate_plan` | `plan['batch_id'] != batch_id_of(params)` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 389 | `validate_plan` | `not isinstance(cells, list) or len(cells) != int(params['n'])` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 392 | `validate_plan` | `not isinstance(cell, dict) or set(cell) != _CELL_KEYS` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 394 | `validate_plan` | `cell['status'] not in CELL_STATUSES` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 396 | `validate_plan` | `cell['symbol'] not in params['symbols'] or cell['regime'] not in REGIMES` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 490 | `mark_cell` | `not 0 <= index < len(cells)` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 494 | `mark_cell` | `status not in legal.get(current, set())` |
| `PROBE_PLAN_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 497 | `mark_cell` | `unknown` |
| `PROBE_PLAN_MISSING` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 730 | `abandon_plan` | `plan is None` |
| `PROBE_PLAN_NOT_ACTIVE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 458 | `select_cell` | `plan['status'] != PLAN_ACTIVE` |
| `PROBE_PLAN_NOT_ACTIVE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 732 | `abandon_plan` | `plan['status'] != PLAN_ACTIVE` |
| `PROBE_PLAN_TAMPERED` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 417 | `read_plan` | `not isinstance(stored, str) or _plan_sha256(raw) != stored` |
| `PROBE_PLAN_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 412 | `read_plan` | `—` |
| `PROBE_PLAN_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 414 | `read_plan` | `not isinstance(raw, dict)` |
| `PROBE_PRICE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 576 | `probe_quantity` | `not (isinstance(price, (int, float)) and (not isinstance(price, bool)) and (price > 0))` |
| `PROBE_PRICE_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 594 | `probe_stop_price` | `not (isinstance(fill_price, (int, float)) and (not isinstance(fill_price, bool)) and (fill_pric…` |
| `PROBE_REGIME_EXHAUSTED` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 475 | `select_cell` | `—` |
| `PROBE_REGIME_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/probe.py` | 303 | `regime_of` | `isinstance(atr_percentile, bool) or not isinstance(atr_percentile, (int, float))` |
| `PROGRAMIZATION_RECORD_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 228 | `_validate` | `—` |
| `PROGRAMIZATION_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 115 | `read_observations` | `—` |
| `PROGRAMIZATION_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 120 | `read_patterns` | `—` |
| `PROGRAMIZATION_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 142 | `read_candidates` | `—` |
| `PROGRAMIZATION_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 160 | `read_requests` | `—` |
| `PROGRAMIZATION_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 133 | `append_observation` | `—` |
| `PROGRAMIZATION_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 137 | `append_pattern` | `—` |
| `PROGRAMIZATION_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 155 | `append_candidate` | `—` |
| `PROGRAMIZATION_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/programization.py` | 164 | `append_request` | `—` |
| `PROMOTED_NOT_RETIRED` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 232 | `apply_memory_command` | `—` |
| `PROMOTED_UNAUDITED` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 240 | `apply_memory_command` | `—` |
| `PROMOTION_ACTOR_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 632 | `build_promotion_audit` | `not (isinstance(promoted_by, str) and promoted_by.strip())` |
| `PROMOTION_ORIGIN_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 629 | `build_promotion_audit` | `missing` |
| `PROMOTION_ORIGIN_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 622 | `build_promotion_audit` | `not isinstance(origin, Mapping)` |
| `PROMOTION_REASON_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 634 | `build_promotion_audit` | `not (isinstance(reason, str) and reason.strip())` |
| `PROMOTION_SUBJECT_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 646 | `build_promotion_audit` | `not (isinstance(candidate_id, str) and candidate_id and isinstance(validated_id, str) and valid…` |
| `PROMOTION_TIER_INVALID` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/promotion.py` | 75 | `promotion_content_sha256` | `live_tier not in pool_store.LIVE_TIERS` |
| `PROPOSAL_INPUTS_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 399 | `_apply_job` | `not isinstance(inputs, dict)` |
| `PROPOSAL_INPUTS_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 409 | `_apply_job` | `not isinstance(families, list) or not all((isinstance(f, str) for f in families)) or len(famili…` |
| `PROPOSAL_INPUTS_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 415 | `_apply_job` | `focus is not None and (not isinstance(focus, str))` |
| `PROVIDER_ERROR` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 229 | `run_validation_worker` | `—` |
| `PROVIDER_ERROR` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 672 | `run_analysis_worker` | `—` |
| `PROVIDER_NOT_AUTHORIZED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 297 | `authorize` | `record['provider_id'] != provider_id` |
| `PROVIDER_NOT_AUTHORIZED` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 362 | `assert_authorization` | `authorization.provider_id != provider_id` |
| `PROVIDER_TRANSPORT` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 333 | `_post_json_with_retry` | `—` |
| `PROVIDER_TRANSPORT` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 338 | `_post_json_with_retry` | `—` |
| `PROVIDER_UNAVAILABLE` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 330 | `_post_json_with_retry` | `exc.code in _RETRYABLE_HTTP` |
| `PROVIDER_UNAVAILABLE` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 881 | `generate` | `—` |
| `QUERY_TOO_LONG` | `ToolBlocked` | `runtime/mvp_runtime/tools.py` | 98 | `_require_query` | `len(query) > MAX_QUERY_CHARS` |
| `QUEUE_FULL` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 517 | `enqueue` | `depth >= QUEUE_DEPTH_LIMIT` |
| `REASON_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 181 | `apply_dispatch` | `not isinstance(reason, str) or not reason.strip()` |
| `REASON_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 222 | `apply_work` | `not isinstance(reason, str) or not reason.strip()` |
| `REASON_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 268 | `_require_reason` | `not isinstance(reason, str) or not reason.strip()` |
| `REGISTRATION_MALFORMED` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 221 | `load_operator_registration` | `—` |
| `REGISTRATION_MALFORMED` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 225 | `load_operator_registration` | `not (isinstance(operator_id, str) and operator_id and isinstance(chat_id, str) and chat_id)` |
| `REGISTRATION_MISSING` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 214 | `load_operator_registration` | `not path.is_file()` |
| `REGISTRATION_REQUIRES_ACCEPTED` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 66 | `_lineage` | `candidate.get('status') != 'ACCEPTED'` |
| `REGISTRATION_REQUIRES_REQUEST` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 73 | `_lineage` | `not isinstance(request, dict)` |
| `REGISTRATION_SELF_CHECK_FAILED` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 293 | `apply_registration` | `—` |
| `REGISTRY_RECORD_INVALID` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 184 | `from_record` | `—` |
| `REGISTRY_RECORD_INVALID` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 189 | `from_record` | `status not in _ALLOWED_TRANSITIONS` |
| `REGISTRY_RECORD_INVALID` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 220 | `_validate` | `—` |
| `REGISTRY_UNAVAILABLE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 254 | `load_resolved_roles` | `—` |
| `REGISTRY_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 245 | `apply_registry_command` | `registry is None` |
| `REGISTRY_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/task_registry.py` | 319 | `_read_rows` | `—` |
| `REGISTRY_UNRESOLVABLE` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 72 | `_registry_snapshot` | `—` |
| `REGISTRY_UNRESOLVABLE` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 167 | `request_registration` | `—` |
| `REGISTRY_UNRESOLVABLE` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 259 | `apply_registration` | `—` |
| `REGISTRY_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/task_registry.py` | 327 | `_append` | `—` |
| `REQUEST_EXISTS` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 153 | `create_program_request` | `any((row.get('candidate_id') == candidate_id for row in store.read_requests()))` |
| `REQUEST_ID_REUSED` | `ControlBlocked` | `runtime/mvp_runtime/bridge_idempotency.py` | 135 | `claim` | `prior.get('request_sha256') != request_fingerprint` |
| `REQUEST_INPUT_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization_cli.py` | 206 | `main` | `not (args.program_id and args.program_version)` |
| `REQUEST_IN_FLIGHT` | `ControlBlocked` | `runtime/mvp_runtime/bridge_idempotency.py` | 143 | `claim` | `prior.get('state') == STATE_CLAIMED` |
| `REQUEST_LINEAGE_MISSING` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 105 | `_lineage_task` | `anchor is None` |
| `REQUEST_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 164 | `apply_dispatch` | `not isinstance(text, str) or not text.strip()` |
| `REQUEST_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 204 | `apply_work` | `not isinstance(text, str) or not text.strip()` |
| `REQUEST_REQUIRES_ACCEPTED` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 148 | `create_program_request` | `candidate.get('status') != 'ACCEPTED'` |
| `RESPONSE_TRUNCATED` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 234 | `run_validation_worker` | `response_was_truncated(result.finish_reason)` |
| `RESPONSE_TRUNCATED` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 678 | `run_analysis_worker` | `response_was_truncated(result.finish_reason)` |
| `RESULT_UNAVAILABLE` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 297 | `apply_registry_command` | `rendered is None` |
| `RETIREMENT_DUPLICATE_SELECTOR` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 70 | `resolve_pool_entries` | `duplicates` |
| `RETIREMENT_EMPTY` | `ApprovalBlocked` | `runtime/mvp_runtime/crypto/retirement.py` | 67 | `resolve_pool_entries` | `not strategy_ids` |
| `RETIREMENT_REASON_REQUIRED` | `ToolError` | `runtime/mvp_runtime/crypto/lifecycle.py` | 357 | `operator_retirement_decision` | `not (isinstance(reason, str) and reason.strip())` |
| `RISK_BELOW_DISPOSITION_FLOOR` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 576 | `build_permission_decision` | `declared_rank is None or declared_rank < RISK_ORDER[risk_floor]` |
| `ROLE_ALREADY_ACTIVE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 278 | `select_candidate_role` | `status == 'active'` |
| `ROLE_BINDING_UNSUPPORTED` | `ProviderError` | `runtime/mvp_runtime/providers.py` | 861 | `bind_role_output_keys` | `binder is None` |
| `ROLE_DEFINITION_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 104 | `build_role_assignment` | `—` |
| `ROLE_DEFINITION_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 120 | `load_role_definition` | `—` |
| `ROLE_DEFINITION_INVALID` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 256 | `load_resolved_roles` | `—` |
| `ROLE_OUTPUT_CONTRACT_UNSUPPORTED_BY_PROVIDER` | `WorkerBlocked` | `runtime/mvp_runtime/pipeline.py` | 167 | `_provider_for_role` | `getattr(provider, 'network_egress', False)` |
| `ROLE_REGISTRY_MISMATCH` | `KernelBlocked` | `runtime/read_only_kernel/preflight.py` | 404 | `run_preflight` | `—` |
| `ROUTE_NOT_SUPPORTED` | `KernelBlocked` | `runtime/read_only_kernel/router.py` | 13 | `select_route` | `routing.get('selected_route') != 'ROLE'` |
| `ROUTE_NOT_SUPPORTED` | `KernelBlocked` | `runtime/read_only_kernel/worker_port.py` | 18 | `invoke_worker` | `route.selected_route != 'ROLE'` |
| `SCHEDULER_EVENT_INVALID` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 707 | `mutation_event` | `action not in MUTATION_ACTIONS` |
| `SCHEDULES_UNREADABLE` | `PersistenceError` | `runtime/mvp_runtime/scheduler.py` | 462 | `list` | `—` |
| `SCHEDULES_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/scheduler.py` | 470 | `_save` | `—` |
| `SCHEDULE_RECORD_INVALID` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 356 | `from_record` | `—` |
| `SCHEDULE_RECORD_INVALID` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 361 | `from_record` | `not (isinstance(next_run_at, str) and _TIMESTAMP_PATTERN.match(next_run_at))` |
| `SCHEMA_INVALID` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 332 | `build_task` | `—` |
| `SCHEMA_UNAVAILABLE` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 328 | `build_task` | `not schema_path.is_file()` |
| `SCOPE_NOT_CONSUMABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 199 | `consume_approval` | `snapshot.get('permission_scope') != MEMORY_PROMOTION_PERMISSION_SCOPE` |
| `SCOPE_NOT_CONSUMABLE` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 433 | `run_trial` | `snapshot.get('permission_scope') != TRIAL_PERMISSION_SCOPE` |
| `SCOPE_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 300 | `_require_scope` | `scope not in _ALLOWED_SCOPES` |
| `SCOPE_NOT_SPENDABLE` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 433 | `_spend` | `snapshot.get('permission_scope') != TRADING_SWITCH_PERMISSION_SCOPE` |
| `SECRET_BEARING_KEY` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 321 | `build_task` | `—` |
| `SECRET_IN_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 177 | `build_memory_candidates` | `—` |
| `SECRET_IN_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 230 | `build_correction_candidate` | `—` |
| `SECRET_IN_CANDIDATE` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 535 | `create_program_candidate` | `—` |
| `SECRET_IN_CANDIDATE` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 644 | `record_shadow_result` | `—` |
| `SECRET_IN_CORE_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 455 | `build_core_candidate` | `—` |
| `SECRET_IN_CORE_CANDIDATE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 515 | `decide_core_candidate` | `—` |
| `SECRET_IN_DEFINITION` | `ProgramizationBlocked` | `runtime/mvp_runtime/registration.py` | 122 | `build_program_definition` | `—` |
| `SECRET_IN_REQUEST` | `ProgramizationBlocked` | `runtime/mvp_runtime/program_request.py` | 339 | `create_program_request` | `—` |
| `SECRET_IN_VALIDATED` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 633 | `promote_candidate` | `—` |
| `SEED_TOO_LONG` | `ToolBlocked` | `runtime/mvp_runtime/naver_research.py` | 252 | `_require_seed` | `len(seed) > MAX_SEED_CHARS` |
| `SETTLEMENT_RACE_LOST` | `ToolError` | `runtime/mvp_runtime/crypto/paper.py` | 1377 | `settle_position` | `current is None or current.get('position_id') != expected_id` |
| `SHADOW_EVIDENCE_MISSING` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 624 | `record_shadow_result` | `not (isinstance(comparison_ref, str) and comparison_ref.strip())` |
| `SHADOW_EVIDENCE_MISSING` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 626 | `record_shadow_result` | `not (isinstance(result, str) and result.strip())` |
| `SHADOW_NOT_RUNNING` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 630 | `record_shadow_result` | `latest.get('status') != 'VALIDATING' or latest.get('shadow_validation', {}).get('status') != 'R…` |
| `SHADOW_OUTCOME_INVALID` | `ProgramizationBlocked` | `runtime/mvp_runtime/programization.py` | 622 | `record_shadow_result` | `outcome not in ('PASS', 'FAIL')` |
| `STATE_FOREIGN_ROOT_RUN` | `PersistenceError` | `runtime/mvp_runtime/state_guard.py` | 192 | `assert_not_foreign_root_run` | `owner is not None` |
| `STATE_NOT_WRITABLE` | `PersistenceError` | `runtime/mvp_runtime/state_guard.py` | 209 | `assert_state_writable` | `offenders` |
| `STOP_CHANGED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 520 | `_spend` | `stop_ref(current) != approved_stop` |
| `STOP_NOT_NAMED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 513 | `_spend` | `not isinstance(approved_stop, str) or not approved_stop` |
| `STRATEGY_POOL_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1410 | `assert_pool_identity_unique` | `strategy_id in seen_strategy` |
| `STRATEGY_POOL_DUPLICATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1415 | `assert_pool_identity_unique` | `candidate_id in seen_candidate` |
| `STRATEGY_POOL_INVALID` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1431 | `load_active_pool` | `—` |
| `STRATEGY_POOL_UNREADABLE` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1427 | `load_active_pool` | `—` |
| `SUBJECT_FINGERPRINT_FAILED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 49 | `_fingerprint` | `—` |
| `TARGET_EXISTS` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 138 | `write` | `—` |
| `TARGET_EXISTS` | `ToolBlocked` | `runtime/mvp_runtime/workspace.py` | 249 | `run_write` | `target.exists()` |
| `TARGET_NOT_CANDIDATE` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 206 | `consume_approval` | `not target_ref.startswith(_CANDIDATE_TARGET_PREFIX)` |
| `TARGET_NOT_CANDIDATE_ROLE` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 218 | `_parse_target` | `not target_ref.startswith(_TARGET_PREFIX) or '@' not in target_ref` |
| `TARGET_NOT_CANDIDATE_ROLE` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 224 | `_parse_target` | `not role_id or not version` |
| `TARGET_NOT_SWITCH` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 445 | `_spend` | `prefix is None` |
| `TASK_NOT_FINISHED` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 281 | `apply_registry_command` | `not entry.is_terminal` |
| `TOKEN_BUDGET_EXCEEDED` | `WorkerBlocked` | `runtime/mvp_runtime/validator.py` | 241 | `run_validation_worker` | `token_budget and tokens_used > int(token_budget)` |
| `TOKEN_BUDGET_EXCEEDED` | `WorkerBlocked` | `runtime/mvp_runtime/worker.py` | 686 | `run_analysis_worker` | `token_budget and tokens_used > int(token_budget)` |
| `TOOL_ERROR` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 828 | `collect_market_data` | `—` |
| `TOOL_ERROR` | `ToolBlocked` | `runtime/mvp_runtime/naver_research.py` | 324 | `run_keyword_research` | `—` |
| `TOOL_ERROR` | `ToolBlocked` | `runtime/mvp_runtime/tools.py` | 121 | `run_search` | `—` |
| `TOOL_RATE_LIMITED` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 174 | `classify_transport_error` | `isinstance(status, int) and status in _RATE_LIMIT_STATUSES` |
| `TOOL_REQUEST_REJECTED` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 151 | `_rejected` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/account.py` | 321 | `_signed_get` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 178 | `classify_transport_error` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1337 | `derivative_price_klines` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/crypto/market_data.py` | 1433 | `positioning_history` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 641 | `keywords` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 744 | `_fetch` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 284 | `search` | `—` |
| `TOOL_TRANSPORT` | `ToolError` | `runtime/mvp_runtime/tools.py` | 374 | `search` | `—` |
| `TOO_LONG` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 89 | `_require_text` | `len(value) > max_len` |
| `TOO_LONG` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 127 | `_clean_str_list` | `len(item) > MAX_FIELD_CHARS` |
| `TOO_MANY_ITEMS` | `TaskIntakeBlocked` | `runtime/mvp_runtime/intake.py` | 118 | `_clean_str_list` | `len(items) > MAX_LIST_ITEMS` |
| `TRANSITION_INVALID` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 286 | `_assert_transition` | `target not in _ALLOWED_TRANSITIONS.get(current, frozenset())` |
| `TRIAL_REQUEST_MISSING` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 440 | `run_trial` | `not (isinstance(trial_request, str) and trial_request.strip())` |
| `TTL_EXCEEDS_POLICY` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 163 | `build_approval_request` | `requested > policy_max` |
| `UNEXPECTED_TRIAL_AUTHORIZATION` | `PlannerBlocked` | `runtime/mvp_runtime/assignment.py` | 119 | `build_role_assignment` | `not trial and trial_authorization_ref is not None` |
| `UNIX_SOCKETS_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 274 | `__init__` | `not UNIX_SOCKETS_AVAILABLE` |
| `UNIX_SOCKETS_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/socket_door.py` | 634 | `call_door` | `not UNIX_SOCKETS_AVAILABLE or not hasattr(socket, 'AF_UNIX')` |
| `UNKNOWN_APPROVAL` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 547 | `apply_command` | `approval is None` |
| `UNKNOWN_APPROVAL` | `ApprovalBlocked` | `runtime/mvp_runtime/consumption.py` | 164 | `consume_approval` | `approval_rec is None` |
| `UNKNOWN_APPROVAL` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 399 | `_spend` | `record is None` |
| `UNKNOWN_APPROVAL` | `ApprovalBlocked` | `runtime/mvp_runtime/trial.py` | 401 | `run_trial` | `approval_rec is None` |
| `UNKNOWN_CANDIDATE` | `MvpRuntimeError` | `runtime/mvp_runtime/approval_cli.py` | 70 | `_find_candidate` | `entry is None` |
| `UNKNOWN_CANDIDATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1374 | `resolve_candidates` | `missing` |
| `UNKNOWN_COMMAND` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 539 | `apply_command` | `verb not in COMMANDS` |
| `UNKNOWN_COMMAND` | `ControlBlocked` | `runtime/mvp_runtime/control.py` | 572 | `apply_command` | `command not in COMMANDS` |
| `UNKNOWN_COMMAND` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 359 | `apply_registry_command` | `—` |
| `UNKNOWN_CORE_CANDIDATE_DECISION` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 491 | `decide_core_candidate` | `decision not in CORE_CANDIDATE_DECISIONS` |
| `UNKNOWN_CORE_CANDIDATE_TYPE` | `MemoryBlocked` | `runtime/mvp_runtime/memory.py` | 413 | `build_core_candidate` | `candidate_type not in CORE_CANDIDATE_TYPES` |
| `UNKNOWN_DOMAIN_COMMAND` | `OperatorBlocked` | `runtime/mvp_runtime/domain_console.py` | 181 | `apply_domain_command` | `verb not in _SUBCOMMANDS` |
| `UNKNOWN_DOMAIN_SUBCOMMAND` | `OperatorBlocked` | `runtime/mvp_runtime/domain_console.py` | 188 | `apply_domain_command` | `handler is None` |
| `UNKNOWN_FLAG` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 226 | `build_activation_record` | `bad` |
| `UNKNOWN_FLAG` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 253 | `build_entry` | `unknown` |
| `UNKNOWN_KIND` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 423 | `build_schedule` | `kind not in KINDS` |
| `UNKNOWN_KIND` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 1533 | `_execute` | `schedule.kind != KIND_TASK` |
| `UNKNOWN_ORIGIN` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 242 | `build_entry` | `origin not in ORIGINS` |
| `UNKNOWN_PARENT_CANDIDATE` | `ToolError` | `runtime/mvp_runtime/crypto/pool.py` | 1298 | `validate_candidate_lineage` | `unknown` |
| `UNKNOWN_PROVIDER` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 595 | `select_env_gated_chain` | `unknown` |
| `UNKNOWN_PROVIDER` | `SafetyGateBlocked` | `runtime/mvp_runtime/safety_gate.py` | 682 | `select_gated_chain` | `unknown` |
| `UNKNOWN_REQUEST_KIND` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 147 | `capabilities_for_request_kind` | `capabilities is None` |
| `UNKNOWN_REQUEST_KIND` | `TaskRegistryBlocked` | `runtime/mvp_runtime/task_registry.py` | 262 | `build_entry` | `kind is not None and kind not in REQUEST_KIND_CAPABILITIES` |
| `UNKNOWN_ROLE` | `PlannerBlocked` | `runtime/mvp_runtime/planner.py` | 293 | `select_candidate_role` | `—` |
| `UNKNOWN_SCOPE` | `PlannerBlocked` | `runtime/mvp_runtime/permission.py` | 531 | `build_permission_decision` | `disposition is None` |
| `UNKNOWN_VENUE` | `ToolBlocked` | `runtime/mvp_runtime/crypto/market_data.py` | 123 | `venue_feeds` | `—` |
| `UNREGISTERED_USER` | `OperatorBlocked` | `runtime/mvp_runtime/operator.py` | 239 | `verify_control_channel` | `not isinstance(message.sender_id, str) or message.sender_id != registration.operator_id` |
| `UNREPORTED_ORDER_EVIDENCE_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 1276 | `build_unreported_live_order_audit` | `not (isinstance(canary_order_id, str) and canary_order_id)` |
| `UNREPORTED_ORDER_EVIDENCE_UNHASHED` | `AuditError` | `runtime/mvp_runtime/audit.py` | 1284 | `build_unreported_live_order_audit` | `not (isinstance(record_sha, str) and record_sha)` |
| `UNREPORTED_ORDER_REASON_MISSING` | `AuditError` | `runtime/mvp_runtime/audit.py` | 1289 | `build_unreported_live_order_audit` | `not reason.strip()` |
| `UNREPORTED_ORDER_TASK_INVALID` | `AuditError` | `runtime/mvp_runtime/audit.py` | 1270 | `build_unreported_live_order_audit` | `not isinstance(recording_task.get(field_name), Mapping)` |
| `UNVERIFIED_SOURCE` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 254 | `record_decision` | `verification.method != TELEGRAM_VERIFICATION_METHOD` |
| `USAGE` | `OperatorBlocked` | `runtime/mvp_runtime/memory_console.py` | 163 | `apply_memory_command` | `not candidate_id` |
| `USAGE` | `OperatorBlocked` | `runtime/mvp_runtime/registry_console.py` | 365 | `_require_entry` | `not argument` |
| `VALIDATION_RESULT_INVALID` | `ValidationError` | `runtime/mvp_runtime/validation.py` | 319 | `validate_agent_output` | `—` |
| `VALIDATION_RESULT_INVALID` | `ValidationError` | `runtime/mvp_runtime/validator.py` | 355 | `run_validation_worker` | `—` |
| `VERB_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/knowledge_bridge.py` | 121 | `apply_knowledge` | `command not in _COMMANDS` |
| `VERB_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/read_bridge.py` | 105 | `apply_read` | `command not in _READS` |
| `VERB_NOT_PERMITTED` | `ControlBlocked` | `runtime/mvp_runtime/switch_bridge.py` | 588 | `apply_switch` | `command not in _ALLOWED_COMMANDS` |
| `WINDOW_TOO_EARLY` | `ToolError` | `runtime/mvp_runtime/naver_research.py` | 775 | `trend` | `start_date < self.EARLIEST_PERIOD` |
| `WORKER_SOCKET_IN_ASSISTANT_DIR` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 488 | `open_door` | `'bridge' in {part.lower() for part in path.parts}` |
| `WORKER_UID_ALLOWLIST_REQUIRED` | `ControlBlocked` | `runtime/mvp_runtime/pipeline_worker.py` | 477 | `open_door` | `not socket_door.resolve_client_uids()` |
| `WORKER_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 224 | `apply_dispatch` | `execute is None` |
| `WORKER_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 245 | `apply_dispatch` | `not isinstance(reply, dict)` |
| `WORKER_UNAVAILABLE` | `ControlBlocked` | `runtime/mvp_runtime/dispatch_bridge.py` | 331 | `_forward` | `exc.reason_code in {'DOOR_UNREACHABLE', 'DOOR_REPLY_MALFORMED'}` |
| `WORKER_UNAVAILABLE` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 816 | `delegate_analysis_task` | `not isinstance(reply, dict)` |
| `WORKER_UNAVAILABLE` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 886 | `delegate_data_review` | `not isinstance(record, dict) or not record.get('review_id')` |
| `WORKER_UNAVAILABLE` | `SchedulerBlocked` | `runtime/mvp_runtime/scheduler.py` | 928 | `delegate_proposal_generation` | `not isinstance(generation, dict) or 'raw' not in generation` |
| `WORKING_MEMORY_WRITE_FAILED` | `PersistenceError` | `runtime/mvp_runtime/working_memory.py` | 99 | `prune_expired` | `removed` |
| `WRITE_FAILED` | `ToolError` | `runtime/mvp_runtime/workspace.py` | 140 | `write` | `—` |
| `WRITE_FAILED` | `ToolError` | `runtime/mvp_runtime/workspace.py` | 145 | `write` | `—` |
| `WRONG_APPROVER` | `ApprovalBlocked` | `runtime/mvp_runtime/approval.py` | 249 | `record_decision` | `verification.approved_by != REQUIRED_APPROVER` |
| `direction_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 3811 | `fuse_specs` | `first.direction != second.direction` |
| `holdout_unjudgeable` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 3833 | `fuse_specs` | `len(conditions) > MAX_FUSION_ENTRY_CONDITIONS` |
| `non_and_parent` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 3819 | `fuse_specs` | `'OR' in (first.entry_rules.operator, second.entry_rules.operator)` |
| `schema_version_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 3800 | `fuse_specs` | `first.schema_version != second.schema_version` |
| `stop_model_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 3817 | `fuse_specs` | `first.exit_rules.stop_model != second.exit_rules.stop_model` |
| `symbol_scope_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 3815 | `fuse_specs` | `sorted(first.symbol_scope) != sorted(second.symbol_scope)` |
| `timeframe_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 3813 | `fuse_specs` | `first.timeframe != second.timeframe` |
| `too_many_conditions` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 3827 | `fuse_specs` | `len(conditions) > MAX_ENTRY_CONDITIONS` |
| `venue_mismatch` | `FusionRefused` | `runtime/mvp_runtime/crypto/factory.py` | 3809 | `fuse_specs` | `first.venue != second.venue` |
