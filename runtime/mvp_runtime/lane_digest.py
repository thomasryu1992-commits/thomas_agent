"""A per-lane digest of what the governed runs actually did over a window. Reads only.

System review B11 (2026-09-25): nothing counted how the non-crypto lanes are used or how they fare.
CLAUDE.md's rule that "a lane earns its size with evidence or is removed whole" had no evidence to
read, and the analysis lane's outcomes lived in the ledger unread. This folds the ledger's run
records into one row per lane (the role the run was assigned):

* **runs** — traces with a ``task`` record received in the window;
* **delivered / unverified / revise / block** — the run's final outcome: the stricter of its
  ``validation_result`` and, when the independent reviewer ran, its ``independent_validation_result``
  (after a revision, both are the re-verified ones — the pipeline records one of each per run). A run
  with a ``delivery`` row was delivered UNVERIFIED instead of withheld (review D2) and counts there,
  not as delivered or revise. A run with neither result stopped before validation — **stopped**;
* **revised** — runs that took the one governed regeneration (a ``revision`` record);
* **non-PASS checks** — which automatic checks withheld the analyses, by check id;
* **models** — which model answered the specialist call, by ``model_id``;
* **failovers** — the chain members the specialist call failed over past, by member and kind
  (``providers.failover_kind``, review D1). A ``configuration`` failover is a key or model slug that
  is wrong on that member, and it is flagged: wider failover was adopted on the condition that a
  broken member cannot hide behind the one that answered;
* **latency p50 / p95** — the specialist invocation's own ``latency_ms``. The run's wall clock is
  not recorded anywhere (``budgets.py``), so this is the model call, not what Thomas waited.

Nothing here decides anything; it is the number the lane-evidence rule needs.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

# The record kinds a run's outcome is read from. Everything else in records.jsonl — the crypto
# lane writes most of it — is skipped by the prescreen before it is parsed.
DIGEST_KINDS = ("task", "role_assignment", "validation_result", "independent_validation_result",
                "invocation", "revision", "delivery")
_SEVERITY = {"PASS": 0, "REVISE": 1, "BLOCK": 2}


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def fold_runs(rows: Iterable[Mapping[str, Any]], *, since: str, until: str | None = None) -> dict[str, dict[str, Any]]:
    """``{lane: digest}`` over ledger rows (``{"kind", "record", "trace_id"}``), runs received in
    ``[since, until)``. Rows without a trace id, and traces without a ``task`` record, are not runs."""
    by_trace: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        trace = row.get("trace_id")
        kind = row.get("kind")
        record = row.get("record")
        if isinstance(trace, str) and kind in DIGEST_KINDS and isinstance(record, Mapping):
            by_trace[trace][kind] = record

    lanes: dict[str, dict[str, Any]] = {}
    latencies: dict[str, list[int]] = defaultdict(list)
    for kinds in by_trace.values():
        task = kinds.get("task")
        if task is None:
            continue
        received = str((task.get("request") or {}).get("received_at") or "")
        if not received or received < since or (until is not None and received >= until):
            continue
        lane = str((kinds.get("role_assignment") or {}).get("role_id") or "unassigned")
        digest = lanes.setdefault(lane, {
            "runs": 0, "delivered": 0, "unverified": 0, "revise": 0, "block": 0, "stopped": 0, "revised": 0,
            "non_pass_checks": Counter(), "models": Counter(), "failovers": Counter(),
        })
        digest["runs"] += 1
        results = [
            str((kinds[k].get("validation") or {}).get("result") or "")
            for k in ("validation_result", "independent_validation_result") if k in kinds
        ]
        results = [r for r in results if r in _SEVERITY]
        if not results:
            digest["stopped"] += 1
        else:
            final = max(results, key=_SEVERITY.__getitem__)
            if final != "BLOCK" and "delivery" in kinds:
                digest["unverified"] += 1
            else:
                digest[{"PASS": "delivered", "REVISE": "revise", "BLOCK": "block"}[final]] += 1
            for check in (kinds.get("validation_result") or {}).get("validation", {}).get("checks") or []:
                if isinstance(check, Mapping) and check.get("result") not in (None, "PASS"):
                    digest["non_pass_checks"][str(check.get("check_id"))] += 1
        if "revision" in kinds:
            digest["revised"] += 1
        invocation = kinds.get("invocation") or {}
        if invocation.get("model_id"):
            digest["models"][str(invocation["model_id"])] += 1
        for failover in invocation.get("failovers") or []:
            if isinstance(failover, Mapping):
                digest["failovers"][f"{failover.get('member', '?')} {failover.get('kind', '?')}"] += 1
        if isinstance(invocation.get("latency_ms"), int):
            latencies[lane].append(invocation["latency_ms"])

    for lane, digest in lanes.items():
        digest["non_pass_checks"] = dict(digest["non_pass_checks"].most_common())
        digest["models"] = dict(digest["models"].most_common())
        digest["failovers"] = dict(digest["failovers"].most_common())
        digest["latency_ms_p50"] = _percentile(latencies[lane], 0.5)
        digest["latency_ms_p95"] = _percentile(latencies[lane], 0.95)
    return dict(sorted(lanes.items()))


def lane_digest(ledger: Any, *, since: str, until: str | None = None) -> dict[str, dict[str, Any]]:
    """:func:`fold_runs` over a ``LedgerStore``, archives included — a week's runs outlive rotation."""
    return fold_runs(ledger.iter_records_with_archive(appended_since=since, kinds=list(DIGEST_KINDS)),
                     since=since, until=until)


def render(digest: Mapping[str, Mapping[str, Any]], *, since: str, until: str | None = None) -> str:
    """The digest as the operator reads it: one block per lane, most-used lane first."""
    window = f"{since} → {until or 'now'}"
    if not digest:
        return f"레인 요약 ({window}): 기간 안에 실행된 요청이 없습니다."
    lines = [f"레인 요약 ({window})", ""]
    for lane, d in sorted(digest.items(), key=lambda item: -item[1]["runs"]):
        delivered_pct = round(100 * d["delivered"] / d["runs"]) if d["runs"] else 0
        lines.append(f"[{lane}] 실행 {d['runs']} · 전달 {d['delivered']} ({delivered_pct}%) · "
                     f"미검증 전달 {d.get('unverified', 0)} · "
                     f"보류 REVISE {d['revise']} · BLOCK {d['block']} · 검증 전 중단 {d['stopped']} · "
                     f"재생성 {d['revised']}")
        if d["non_pass_checks"]:
            lines.append("  보류 사유: " + ", ".join(f"{k} {v}" for k, v in d["non_pass_checks"].items()))
        if d["models"]:
            lines.append("  응답 모델: " + ", ".join(f"{k} {v}" for k, v in d["models"].items()))
        if d.get("failovers"):
            flag = (" — 설정 오류 의심: 해당 멤버의 키·모델명 확인"
                    if any(k.endswith(" configuration") for k in d["failovers"]) else "")
            lines.append("  페일오버: " + ", ".join(f"{k} {v}" for k, v in d["failovers"].items()) + flag)
        if d["latency_ms_p50"] is not None:
            lines.append(f"  모델 지연 p50 {d['latency_ms_p50']} ms · p95 {d['latency_ms_p95']} ms")
        lines.append("")
    return "\n".join(lines).rstrip()
