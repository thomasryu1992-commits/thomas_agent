"""P2 of BLOG_DEPARTMENT v0.2: the system re-checks each draft the drafting cron reported, and compares verdicts.

The drafting cron (outsourced) runs ``blog-preflight`` and ``blog_variety`` on its drafts and reports, per draft, the
codes each one raised and the sha256 of the bytes it checked (``ops/blog-runs/<KST date>.json``). Here the same rules
— generated from the vault's files by ``scripts/blog_vendor.py`` — run on the same draft, and the two verdicts are
compared code by code. That comparison is P2's switch condition (v0.2 §4): two weeks of agreement on every draft
the system could compare.

What is compared, and what is not:

* **Only a draft whose bytes are unchanged.** A draft edited after the report (a slot fix, a hand edit) is
  ``changed``, not compared; a report without a hash (before P2) is ``no_hash``.
* **Not ``X2`` (link liveness).** It needs the network, which stays outsourced (v0.2 §7 N1). The generated
  preflight answers offline, so ``X2`` is dropped from both sides before comparing.
* **Both levels.** ``FAIL`` and ``WARN`` code counts must be equal. A ``WARN`` the vault caps at five per code is
  capped the same way on both sides, because both sides run the same code.

The paths come from a report, which is data written outside the system: a path that does not resolve under the
vault's ``content/`` is refused for that draft (``outside``), never opened.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import re
from pathlib import Path
from typing import Any, Mapping

PREFLIGHT_CODE = re.compile(r"(🔴 FAIL|🟡 WARN) \[(\w+)\]")
VARIETY_CODE = re.compile(r"\[(FAIL|WARN)\] (\w+)")
NETWORK_CODES = frozenset({"X2"})
CHECKERS = ("preflight", "variety")

Codes = dict[str, dict[str, int]]


def codes(text: str, rx: re.Pattern[str]) -> Codes:
    """``{"FAIL": {code: n}, "WARN": {code: n}}`` from a checker's printed lines — the host report's own parse."""
    out: Codes = {"FAIL": {}, "WARN": {}}
    for level, code in rx.findall(text):
        level = "FAIL" if "FAIL" in level else "WARN"
        out[level][code] = out[level].get(code, 0) + 1
    return out


def comparable(found: Mapping[str, Mapping[str, int]] | None) -> Codes | None:
    """The codes with the network-only ones removed, or None when the side has no codes to compare."""
    if not isinstance(found, Mapping):
        return None
    out: Codes = {}
    for level in ("FAIL", "WARN"):
        raw = found.get(level)
        if not isinstance(raw, Mapping):
            return None
        out[level] = {str(c): int(n) for c, n in raw.items() if c not in NETWORK_CODES and isinstance(n, int)}
    return out


def resolve(vault_root: Path, rel: Any) -> Path | None:
    """The draft's path under ``<vault>/content``, or None for anything that leaves it."""
    if not isinstance(rel, str) or not rel.startswith("content/") or not rel.endswith(".md"):
        return None
    content = (vault_root / "content").resolve()
    path = (vault_root / rel).resolve()
    return path if content in path.parents else None


def check_draft(vault_root: Path, path: Path) -> dict[str, Codes]:
    """Run both generated checkers on one draft and parse their verdicts."""
    from . import preflight, taxonomy, variety

    taxonomy.configure(vault_root)
    variety.configure(vault_root)
    out: dict[str, Codes] = {}
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        preflight.check_draft(str(path))
    out["preflight"] = codes(buffer.getvalue(), PREFLIGHT_CODE)
    buffer = io.StringIO()
    variety.PLATFORM = variety.platform_of(str(path))
    with contextlib.redirect_stdout(buffer):
        variety.check(str(path), variety.corpus(exclude={os.path.abspath(path)}))
    out["variety"] = codes(buffer.getvalue(), VARIETY_CODE)
    return out


def _differ(system: Codes, reported: Codes) -> list[str]:
    return sorted({code for level in ("FAIL", "WARN")
                   for code in set(system[level]) | set(reported[level])
                   if system[level].get(code) != reported[level].get(code)})


def verify_draft(vault_root: Path, draft: Mapping[str, Any]) -> dict[str, Any]:
    """One reported draft: its state (compared / changed / no_hash / missing / outside / error) and, when compared,
    whether the system's verdict agrees with the reported one."""
    rel = draft.get("path")
    row: dict[str, Any] = {"platform": draft.get("platform") if draft.get("platform") in ("naver", "tistory") else None,
                           "state": "", "agree": None, "differ": []}
    path = resolve(vault_root, rel)
    if path is None:
        row["state"] = "outside"
        return row
    if not path.is_file():
        row["state"] = "missing"
        return row
    reported_hash = draft.get("sha256")
    if not isinstance(reported_hash, str):
        row["state"] = "no_hash"
        return row
    if hashlib.sha256(path.read_bytes()).hexdigest() != reported_hash:
        row["state"] = "changed"
        return row
    try:
        found = check_draft(vault_root, path)
    except Exception as exc:  # noqa: BLE001 — a checker crash on one draft is recorded, never the whole day's verdict
        row["state"] = f"error:{type(exc).__name__}"
        return row
    differ: list[str] = []
    for checker in CHECKERS:
        reported = comparable((draft.get(checker) or {}).get("codes") if isinstance(draft.get(checker), Mapping) else None)
        if reported is None:
            row["state"] = "no_codes"
            return row
        differ += _differ(comparable(found[checker]) or {"FAIL": {}, "WARN": {}}, reported)
    row.update(state="compared", agree=not differ, differ=sorted(set(differ)))
    return row


def verify_report(vault_root: Path, report: Mapping[str, Any]) -> list[dict[str, Any]]:
    drafts = report.get("drafts") if isinstance(report.get("drafts"), list) else []
    return [verify_draft(vault_root, d) for d in drafts if isinstance(d, Mapping)]


# --- the daily fire (scheduler kind ``blog_verify``) -------------------------------------------------------------

RECORD_KIND = "blog_verification"
RECORD_SCHEMA = "schemas/blog_verification.v0.1.schema.json"
RECORD_SCHEMA_VERSION = "blog_verification.v0.1"
LOOKBACK_DAYS = 3


def build_record(run_date: str, rows: list[dict[str, Any]], *, now: str) -> dict[str, Any]:
    """The day's comparison as a ledger record, validated against its closed schema. Paths become short ids."""
    from runtime.read_only_kernel import integrity

    from .. import schema_cache
    from ..paths import repo_root

    drafts = [{"draft_id": row["draft_id"], "platform": row["platform"], "state": row["state"],
               "agree": row["agree"], "differ": row["differ"]} for row in rows]
    compared = [d for d in drafts if d["state"] == "compared"]
    summary = {"drafts": len(drafts), "compared": len(compared), "agreed": sum(1 for d in compared if d["agree"]),
               "differed": sum(1 for d in compared if not d["agree"]), "not_compared": len(drafts) - len(compared)}
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "verification_id": integrity.short_id("blogverify", {"run_date": run_date, "verified_at": now, "drafts": drafts}),
        "verified_at": now,
        "run_date": run_date,
        "drafts": drafts,
        "summary": summary,
    }
    schema_cache.validate_against_schema(record, repo_root() / RECORD_SCHEMA, RECORD_KIND)
    return record


def fire(ledger: Any, *, now: str, vault_root: Path | None = None) -> str:
    """Verify every run report of the last ``LOOKBACK_DAYS`` KST days that has no verification record yet.
    One record per report. An absent vault raises (``BLOG_VAULT_UNAVAILABLE``) so the fire is a failure."""
    import json
    from datetime import timedelta

    from runtime.read_only_kernel import integrity

    from .. import blog_department, timeutil

    root = blog_department.vault_root(vault_root)
    folder = root.joinpath(*blog_department.RUNS_DIR)
    today = (timeutil.parse_iso(now) + timedelta(hours=9)).date()
    since = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    done = {row["record"].get("run_date") for row in ledger.iter_records(kinds=[RECORD_KIND])
            if row.get("kind") == RECORD_KIND}
    recorded: list[str] = []
    totals = {"compared": 0, "agreed": 0}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        run_date = path.stem
        if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", run_date) and since <= run_date <= today.isoformat()) or run_date in done:
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not (isinstance(report, Mapping) and report.get("schema") == blog_department.RUN_REPORT_SCHEMA):
            continue
        rows = []
        for draft in report.get("drafts") if isinstance(report.get("drafts"), list) else []:
            if not isinstance(draft, Mapping):
                continue
            row = verify_draft(root, draft)
            row["draft_id"] = integrity.short_id("blogdraft", {"path": str(draft.get("path"))})
            rows.append(row)
        record = build_record(run_date, rows, now=now)
        ledger.append_records(record["verification_id"], {RECORD_KIND: record})
        recorded.append(run_date)
        totals["compared"] += record["summary"]["compared"]
        totals["agreed"] += record["summary"]["agreed"]
    if not recorded:
        return "blog_verify_nothing_new"
    return f"blog_verified={','.join(recorded)} compared={totals['compared']} agreed={totals['agreed']}"
