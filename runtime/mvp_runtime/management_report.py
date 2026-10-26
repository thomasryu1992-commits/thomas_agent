"""The weekly management report: four sections, one message, the control bot. Reads only.

WEEKLY_MANAGEMENT_REPORT_V0.1.md (Thomas 2026-10-25, WMR-1: Q1=A, Q2 holdings excluded, Q3 Monday
09:00 KST, Q4 counts only). The assistant that relayed the lanes' weekly evidence was retired that
day, and with it the only periodic report Thomas received. This puts the same evidence in front of
him once a week, on the bot he decides on:

* **lanes** — the non-crypto lanes from :func:`lane_digest.lane_digest` (one line per role), and
  one line for the crypto lane from the dashboard's status document;
* **approvals** — the asks still awaiting an answer and not expired: id, kind (the target prefix)
  and expiry, the fields the read door's ``approval_status`` already shows. Never the snapshot;
* **schedules** — schedules whose last fire failed, and failed / abandoned fires per schedule over
  the window, archives included. Only the failure CODE leaves: a status can carry an exception's
  text (the read door's projection v1 drops it for the same reason). Scheduled analyses are counted
  too, because their results reach Thomas only through ``/result``;
* **feedback** — ``/feedback`` verdicts over the window, per lane by the run's ``trace_id``, against
  the runs delivered in the same window. Comments are never quoted;
* **blog** — the blog department's week from the vault's post front matter (``blog_department``,
  BLOG_DEPARTMENT_V0.1.md stages 1-2): status now, new drafts, published, and the slots planned for the
  week ahead, per platform. Counts only.

Each section is collected on its own: one that cannot be read renders as ``읽지 못함: <code>`` and the
others still go. Holdings never appear (Q2): numbers from that lane leave the process through H3's
one table only, and a Telegram message is outside. Nothing here calls a model or the network; the
send is the scheduler's (``scheduler._execute``).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any, Callable, Mapping

from . import approval, lane_digest, timeutil
from .errors import MvpRuntimeError
from .store import FEEDBACK_FILE, SCHEDULER_FILE

WINDOW_DAYS = 7
# A run delivered in the window may have been asked for before it; a feedback's lane is looked up
# over this much more history so a late verdict still finds its run.
FEEDBACK_LOOKBACK_DAYS = 14
_KST = timedelta(hours=9)
_FAILED_CODE = re.compile(r"^failed:([A-Z][A-Z0-9_]{2,63})")
_TASK_KIND = "analysis_task"         # scheduler.KIND_TASK, named here to keep this module import-light
UNREADABLE = "읽지 못함"


def _section(read: Callable[[], Any]) -> dict[str, Any]:
    """Run one section's read. A failure is the section's, never the report's: its code is kept
    (an exception's TYPE, never its message, when it is not one of the runtime's typed errors)."""
    try:
        return {"ok": True, "data": read()}
    except MvpRuntimeError as exc:
        return {"ok": False, "reason_code": exc.reason_code}
    except Exception as exc:  # noqa: BLE001 — one unreadable section must not cost the other three
        return {"ok": False, "reason_code": type(exc).__name__}


# --- the four reads ------------------------------------------------------------------------------

def read_lanes(ledger: Any, *, since: str, until: str) -> dict[str, Any]:
    digest = lane_digest.lane_digest(ledger, since=since, until=until)
    return {lane: {k: d[k] for k in ("runs", "delivered", "unverified", "revise", "block", "stopped")}
            for lane, d in digest.items()}


def read_crypto(status: Mapping[str, Any]) -> dict[str, Any]:
    last = status.get("last_cycle") or {}
    return {
        "cycles_seen": status.get("cycles_seen"),
        "last_cycle_at": last.get("at"),
        "last_verdict": last.get("verdict"),
        "open_positions": len(status.get("open_positions") or []),
        "warnings": len(status.get("warnings") or []),
    }


def read_approvals(approval_store: Any, *, now: str) -> list[dict[str, Any]]:
    rows = []
    for ask in approval_store.pending():
        if approval.is_expired(ask, now=now):
            continue
        target = str((ask.get("approved_action_snapshot") or {}).get("target_ref") or "")
        rows.append({
            "approval_id": str(ask.get("approval_id")),
            "kind": target.split(":", 1)[0] or "?",
            "expires_at": (ask.get("validity") or {}).get("expires_at"),
        })
    return rows


def failure_code(status: Any) -> str | None:
    """The code of a ``failed:<CODE>…`` status, or None. Nothing after the code ever leaves."""
    match = _FAILED_CODE.match(str(status or ""))
    return match.group(1) if match else None


def read_schedules(schedules: Any, ledger: Any, *, since: str) -> dict[str, Any]:
    failing = sorted(
        ({"schedule_id": s.schedule_id, "kind": s.kind, "code": failure_code(s.last_status) or "UNKNOWN"}
         for s in schedules.list() if s.enabled and str(s.last_status or "").startswith("failed:")),
        key=lambda row: (row["kind"], row["schedule_id"]),
    )
    fires: dict[tuple[str, str], Counter] = defaultdict(Counter)
    analyses = 0
    # The oldest row READ, in or before the window: a row older than `since` means the history
    # covers the whole window; only a history that starts inside it is reported as short.
    oldest: str | None = None
    for event in ledger.iter_events_with_archive(SCHEDULER_FILE, appended_since=since):
        created = str(event.get("created_at") or "")
        if not created:
            continue
        oldest = created if oldest is None or created < oldest else oldest
        if created < since:
            continue
        action = event.get("action")
        key = (str(event.get("kind") or "?"), str(event.get("schedule_id") or "?"))
        if action in ("failed", "abandoned"):
            fires[key][action] += 1
        elif action == "fired" and event.get("kind") == _TASK_KIND:
            analyses += 1
    failed_fires = sorted(
        ({"kind": k, "schedule_id": sid, "failed": c["failed"], "abandoned": c["abandoned"]}
         for (k, sid), c in fires.items()),
        key=lambda row: (-(row["failed"] + row["abandoned"]), row["kind"], row["schedule_id"]),
    )
    return {"failing": failing, "failed_fires": failed_fires, "scheduled_analyses": analyses,
            "history_from": oldest}


def read_feedback(ledger: Any, *, since: str, until: str, lanes: Mapping[str, Mapping[str, Any]] | None) -> dict[str, Any]:
    verdicts: Counter = Counter()
    by_trace: dict[str, str] = {}
    for event in ledger.iter_events_with_archive(FEEDBACK_FILE, appended_since=since):
        created = str(event.get("created_at") or "")
        if not created or created < since or created >= until:
            continue
        verdict = str(event.get("verdict") or "NOTE")
        verdicts[verdict] += 1
        if isinstance(event.get("trace_id"), str):
            by_trace[event["trace_id"]] = verdict
    by_lane: dict[str, Counter] = defaultdict(Counter)
    if by_trace:
        lookback = timeutil.plus_minutes(since, -FEEDBACK_LOOKBACK_DAYS * 24 * 60)
        roles = {
            row["trace_id"]: str((row.get("record") or {}).get("role_id") or "unassigned")
            for row in ledger.iter_records_with_archive(appended_since=lookback, kinds=["role_assignment"])
            if row.get("kind") == "role_assignment" and row.get("trace_id") in by_trace
        }
        for trace, verdict in by_trace.items():
            by_lane[roles.get(trace, "unknown")][verdict] += 1
    delivered = (sum(int(d.get("delivered", 0)) + int(d.get("unverified", 0)) for d in lanes.values())
                 if lanes is not None else None)
    return {"verdicts": dict(verdicts), "by_lane": {k: dict(v) for k, v in sorted(by_lane.items())},
            "delivered": delivered}


# --- collect and render -----------------------------------------------------------------------------

def collect(
    *, now: str, ledger: Any, schedules: Any, approval_store: Any, control_state: Any = None,
    crypto_status: Callable[[], Mapping[str, Any]] | None = None,
    blog: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Every section, each read on its own. ``crypto_status`` is called, not passed, so a dashboard
    that cannot be built is the crypto line's failure and nothing else's."""
    since = timeutil.plus_minutes(now, -WINDOW_DAYS * 24 * 60)
    lanes = _section(lambda: read_lanes(ledger, since=since, until=now))
    return {
        "since": since,
        "until": now,
        "control": None if control_state is None else {
            "mode": getattr(control_state, "mode", None), "halt_level": getattr(control_state, "halt_level", None)},
        "lanes": lanes,
        "crypto": _section(lambda: read_crypto(crypto_status())) if crypto_status is not None else None,
        "approvals": _section(lambda: read_approvals(approval_store, now=now)),
        "schedules": _section(lambda: read_schedules(schedules, ledger, since=since)),
        "feedback": _section(lambda: read_feedback(ledger, since=since, until=now,
                                                   lanes=lanes["data"] if lanes["ok"] else None)),
        "blog": _section(blog) if blog is not None else None,
    }


def _kst(iso: Any) -> str:
    try:
        return (timeutil.parse_iso(str(iso)) + _KST).strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return "?"


def _unreadable(section: Mapping[str, Any]) -> str:
    return f"  {UNREADABLE}: {section.get('reason_code')}"


def render(report: Mapping[str, Any]) -> str:
    lines = [f"주간 경영 보고 ({_kst(report['since'])} → {_kst(report['until'])} KST)"]
    control = report.get("control")
    if control:
        halt = f" · 정지 {control['halt_level']}" if control.get("halt_level") else ""
        lines.append(f"런타임: {control.get('mode')}{halt}")

    lines += ["", "■ 레인 요약"]
    lanes = report["lanes"]
    if not lanes["ok"]:
        lines.append(_unreadable(lanes))
    elif not lanes["data"]:
        lines.append("  비크립토 레인: 기간 안에 실행된 요청이 없습니다.")
    else:
        for lane, d in sorted(lanes["data"].items(), key=lambda item: -item[1]["runs"]):
            lines.append(f"  [{lane}] 실행 {d['runs']} · 전달 {d['delivered']} · 미검증 전달 {d['unverified']}"
                         f" · REVISE {d['revise']} · BLOCK {d['block']} · 중단 {d['stopped']}")
    crypto = report.get("crypto")
    if crypto is not None:
        if not crypto["ok"]:
            lines.append(f"  [crypto] {UNREADABLE}: {crypto['reason_code']}")
        else:
            c = crypto["data"]
            lines.append(f"  [crypto] 최근 사이클 {c['cycles_seen']} · 마지막 {_kst(c['last_cycle_at'])} "
                         f"{c['last_verdict'] or '-'} · 열린 포지션 {c['open_positions']} · 경고 {c['warnings']}")

    lines += ["", "■ 대기 승인"]
    approvals = report["approvals"]
    if not approvals["ok"]:
        lines.append(_unreadable(approvals))
    elif not approvals["data"]:
        lines.append("  없음")
    else:
        for a in approvals["data"]:
            lines.append(f"  {a['approval_id']} · {a['kind']} · 만료 {_kst(a['expires_at'])}")
        lines.append("  결정: /approve <id> 또는 /reject <id>")

    lines += ["", "■ 실패 스케줄"]
    schedules = report["schedules"]
    if not schedules["ok"]:
        lines.append(_unreadable(schedules))
    else:
        s = schedules["data"]
        if s["failing"]:
            lines.append("  지금 실패 중:")
            lines += [f"    {r['kind']} ({r['schedule_id']}) — {r['code']}" for r in s["failing"]]
        else:
            lines.append("  지금 실패 중: 없음")
        if s["failed_fires"]:
            lines.append("  기간 중 실패한 fire:")
            lines += [f"    {r['kind']} ({r['schedule_id']}) 실패 {r['failed']} · 중단 {r['abandoned']}"
                      for r in s["failed_fires"]]
        else:
            lines.append("  기간 중 실패한 fire: 없음")
        if s["history_from"] is None:
            lines.append("  (기록된 스케줄러 이벤트가 없습니다)")
        elif s["history_from"] > report["since"]:
            lines.append(f"  (이벤트 기록이 {_kst(s['history_from'])}부터만 있어 그 이후만 셌습니다)")
        lines.append(f"  스케줄 분석 실행 {s['scheduled_analyses']}건 — 결과는 보내지지 않고 /result로만 볼 수 있습니다")

    lines += ["", "■ 피드백"]
    feedback = report["feedback"]
    if not feedback["ok"]:
        lines.append(_unreadable(feedback))
    else:
        f = feedback["data"]
        v = f["verdicts"]
        total = sum(v.values())
        delivered = f["delivered"]
        rate = (f" (전달 {delivered}건 대비)" if delivered is not None else "")
        lines.append(f"  /feedback {total}건{rate}: GOOD {v.get('GOOD', 0)} · BAD {v.get('BAD', 0)} · NOTE {v.get('NOTE', 0)}")
        for lane, counts in f["by_lane"].items():
            lines.append(f"    [{lane}] " + " · ".join(f"{k} {counts[k]}" for k in sorted(counts)))
        lines.append("  피드백은 기록만 되고 아직 다시 읽히지 않습니다(E2·E3 미구현).")

    blog = report.get("blog")
    if blog is not None:
        lines += ["", "■ 블로그"]
        if not blog["ok"]:
            lines.append(_unreadable(blog))
        else:
            for platform, label in (("naver", "네이버"), ("tistory", "티스토리")):
                p = blog["data"]["platforms"][platform]
                st = p["status"]
                lines.append(f"  [{label}] 새 원고 {p['new_drafts']} · 발행 {p['published']} · 다음 7일 예정 {p['upcoming']}")
                lines.append(f"    지금: draft {st['draft']} · 보류 {st['hold']} · 예약 {st['scheduled']} · 발행됨 {st['published']}"
                             + (f" · 읽지 못한 파일 {p['unparsed']}" if p["unparsed"] else ""))
            runs = blog["data"].get("runs")
            if runs is None:
                lines.append("  [원고 실행 보고] 보고 폴더가 마운트되지 않았다")
            else:
                new = sum(blog["data"]["platforms"][p]["new_drafts"] for p in ("naver", "tistory"))
                lines.append(f"  [원고 실행 보고] {runs['reports']}일 보고 · 보고 없는 날 {runs['days_without_report']}"
                             f" · 원고 단계 실패 {runs['draft_step_failed']}일"
                             + (f" · 읽지 못한 보고 {runs['unreadable']}" if runs["unreadable"] else ""))
                lines.append(f"    원고 {runs['drafts']}편(front matter 새 원고 {new}) · 검수 FAIL preflight {runs['preflight_fail']}"
                             f" · variety {runs['variety_fail']} · 검수 미실행 {runs['checks_unrun']}"
                             f" · 키워드 {runs['distinct_keywords']}개 · 시스템 지시 {runs['instructed']}일")
            check = blog["data"].get("verification")
            if check is not None:
                codes = " · ".join(f"{c} {n}" for c, n in check["codes"])
                lines.append(f"  [검수 대조] {check['runs']}일 · 비교 {check['compared']}편 중 일치 {check['agreed']}"
                             f" · 다름 {check['differed']}" + (f"({codes})" if codes else "")
                             + f" · 비교 못 함 {check['not_compared']}")
    return "\n".join(lines)
