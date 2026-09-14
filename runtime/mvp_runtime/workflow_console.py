"""Text renderings of the workflow store's views — what a door's ``reply`` shows beside ``data``.

Sequence 2, P05. Pure functions over the dicts ``workflow_store`` returns; no store access, no
I/O. The Korean labels follow the registry console's vocabulary so a workflow read in the chat
reads like a task read.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import workflow as wf

_WORKFLOW_MARK = {
    wf.W_RECEIVED: "접수", wf.W_VALIDATED: "검증됨", wf.W_RUNNING: "실행 중",
    wf.W_WAITING_APPROVAL: "승인 대기", wf.W_WAITING_REPLAN: "재계획 대기", wf.W_CANCELLING: "취소 처리 중",
    wf.W_COMPLETED: "완료", wf.W_FAILED: "실패", wf.W_BLOCKED: "차단", wf.W_CANCELLED: "취소됨",
}
_STEP_MARK = {
    wf.S_PENDING: "대기", wf.S_READY: "준비", wf.S_RUNNING: "실행 중", wf.S_SUCCEEDED: "성공",
    wf.S_RETRY_WAIT: "재시도 대기", wf.S_WAITING_APPROVAL: "승인 대기", wf.S_NEEDS_RECONCILIATION: "대사 필요",
    wf.S_FAILED: "실패", wf.S_BLOCKED: "차단", wf.S_CANCEL_REQUESTED: "취소 요청됨", wf.S_CANCELLED: "취소됨",
}


def _short(ident: str | None) -> str:
    return ident[:12] if isinstance(ident, str) else "-"


def render_view(view: Mapping[str, Any]) -> str:
    """One workflow: its status, every step with its attempt count and result, the budget."""
    budget = view.get("budget") or {}
    lines = [
        f"workflow {view['workflow_id']}  [{_WORKFLOW_MARK.get(view['status'], view['status'])} · v{view.get('row_version')}]",
        f"  목표: {view.get('goal', '')}",
        f"  as of {view.get('as_of')}  (계획 v{view.get('plan_version')}, {view.get('created_at')} 접수)",
    ]
    if view.get("last_reason_code"):
        lines.append(f"  사유: {view['last_reason_code']}")
    if view.get("cancel_reason"):
        lines.append(f"  취소 사유: {view['cancel_reason']}")
    for step in view.get("steps") or ():
        deps = f" ← {', '.join(step['depends_on'])}" if step.get("depends_on") else ""
        result = f"  → {step['result_ref']}" if step.get("result_ref") else ""
        reason = f"  [{step['last_reason_code']}]" if step.get("last_reason_code") else ""
        lines.append(
            f"  • {step['key']} ({step['capability']}) {_STEP_MARK.get(step['status'], step['status'])}"
            f"  시도 {step['attempts_opened']}/{step['max_attempts']}{deps}{result}{reason}"
        )
    lines.append(
        f"  예산: 모델 호출 {budget.get('reserved_model_calls', 0)}/{budget.get('max_model_calls', '?')} 예약"
        f" (확인 {budget.get('confirmed_model_calls', 0)}, 미확인 {budget.get('unconfirmed_model_calls', 0)})"
    )
    reported = budget.get("reported")
    if reported:
        lines.append(
            f"  보고된 사용량(Hermes, 강제 아님): 입력 {reported['input_tokens']:,}·출력 {reported['output_tokens']:,} 토큰,"
            f" ≈${reported['estimated_cost_usd']:.4f} [{reported['cost_status']}] {reported['source']} as of {reported['as_of']}"
        )
    return "\n".join(lines)


_PUSH_MARK = {
    wf.W_COMPLETED: "✅ 완료", wf.W_FAILED: "⛔ 실패", wf.W_BLOCKED: "⛔ 차단", wf.W_CANCELLED: "⏹ 취소됨",
    wf.W_WAITING_REPLAN: "⏸ 결정 대기",
}


def render_push(event: Mapping[str, Any], view: Mapping[str, Any] | None) -> str:
    """One control-channel message for a workflow's arrival at a pushed state (P08). Short:
    what happened, to which goal, and — when a decision is waited on — which steps and why.
    Decisions on a workflow are Hermes-window actions (retry, a new plan version, cancel), so
    the line says where to go; nothing here is a command the control bot reads."""
    status = str(event.get("to_status"))
    mark = _PUSH_MARK.get(status, status)
    wid = str(event.get("workflow_id"))
    goal = str((view or {}).get("goal") or "")[:80]
    head = f"[workflow] {mark}  {wid}"
    lines = [head + (f"  — {goal}" if goal else "")]
    if event.get("reason_code"):
        lines.append(f"  사유: {event['reason_code']}")
    steps = list((view or {}).get("steps") or ())
    if status == wf.W_COMPLETED:
        done = [s for s in steps if s.get("status") == wf.S_SUCCEEDED]
        lines.append(f"  단계 {len(done)}/{len(steps)} 성공; 결과는 Hermes 창에서 workflow_status → task_result")
    elif status == wf.W_WAITING_REPLAN:
        waiting = [s for s in steps if s.get("status") in (wf.S_FAILED, wf.S_BLOCKED, wf.S_NEEDS_RECONCILIATION)]
        for s in waiting[:5]:
            lines.append(f"  • {s['key']} {_STEP_MARK.get(s['status'], s['status'])}"
                         + (f" [{s['last_reason_code']}]" if s.get("last_reason_code") else ""))
        lines.append("  결정은 Hermes 창에서: retry_workflow_step / propose_workflow_update / cancel_workflow")
    elif status in (wf.W_FAILED, wf.W_BLOCKED, wf.W_CANCELLED):
        settled = [s for s in steps if s.get("status") in (wf.S_FAILED, wf.S_BLOCKED, wf.S_CANCELLED)]
        for s in settled[:5]:
            lines.append(f"  • {s['key']} {_STEP_MARK.get(s['status'], s['status'])}"
                         + (f" [{s['last_reason_code']}]" if s.get("last_reason_code") else ""))
        if (view or {}).get("cancel_reason"):
            lines.append(f"  취소 사유: {view['cancel_reason']}")
    lines.append(f"  {event.get('created_at')}  event #{event.get('cursor')}")
    return "\n".join(lines)


def render_list(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return "workflow 없음"
    lines = [
        f"• {r['workflow_id']}  [{_WORKFLOW_MARK.get(r['status'], r['status'])}]  {r['created_at']}  {str(r.get('goal', ''))[:60]}"
        for r in rows
    ]
    lines.append(f"({len(rows)}건)")
    return "\n".join(lines)


def render_events(events: Sequence[Mapping[str, Any]], next_cursor: int) -> str:
    if not events:
        return f"새 이벤트 없음 (next_cursor={next_cursor})"
    lines = []
    for e in events:
        where = _short(e.get("step_id")) if e.get("step_id") else _short(e.get("workflow_id"))
        reason = f" [{e['reason_code']}]" if e.get("reason_code") else ""
        lines.append(f"{e['cursor']:>6}  {e['created_at']}  {e['entity']:<8} {where}  "
                     f"{e.get('from_status') or '-'} → {e['to_status']}{reason}")
    lines.append(f"(next_cursor={next_cursor})")
    return "\n".join(lines)
