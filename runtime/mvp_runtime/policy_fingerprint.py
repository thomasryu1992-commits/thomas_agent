"""What policy is this process actually running under — and did it change?

The governance policy is COPIED INTO THE IMAGE (`Dockerfile`: `COPY governance/ ./governance/`),
never mounted. So the file cannot be edited under a running service: it changes when, and only
when, a different image is deployed. That is what this module watches, and it is a narrower and
more useful question than "did someone edit the file":

* **A deploy silently changed the governed policy.** The 1.4.0 -> 1.5.0 bump rode in on
  `candidate-839` and nothing in any log said so.
* **A rollback silently reverted it.** `docker tag rollback-pre-839 latest` puts 1.4.0 back
  under services that were approving against 1.5.0, and the two look identical from outside.
  This repository has already deployed an old checkout by accident (2026-08-29), which is the
  same failure one layer up.

Thomas's decision Q7-a (2026-09-03) names the control and its limit: *record the hash at
startup, alert on a change, and do NOT fail closed.* It is the compensating control for what
decision Q2 gave up — policy edits are a human act now, so nothing in code prevents the file
from differing from what was reviewed; this makes a difference **visible** instead.

Fail-closed would be wrong here and the reason is worth stating: a runtime that refuses to
start because its policy hash moved would turn every legitimate policy bump into an outage,
and the halt door — the one thing that must work when everything else is wrong — lives in
these same processes. A control that can stop the stop button is not a safety control.

State is per-machine and per-service (`.runtime_governance_state/policy_fingerprints/<service>.json`),
one file each for the reason the heartbeats are one file each: the services all start within a
second of each other on a deploy, and a single shared pointer would let whichever process won
the race record the new hash while the other three read it back as UNCHANGED — the change
would be announced by nobody.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import timeutil
from .paths import repo_root as _repo_root

POLICY_REL = "governance/GOVERNANCE_POLICY.yaml"
FINGERPRINTS_REL = ".runtime_governance_state/policy_fingerprints"

# The top-level key, at column 0 — the same literal `validate_permission_approval_contracts.py`
# pins with `require_doc_tokens("policy_version: 1.5.0")`. Scanned rather than YAML-parsed: this
# runs at every service startup and the answer is one line of a 25 KB file. A nested key of the
# same name cannot be mistaken for it, because a nested one is indented.
_VERSION_KEY = "policy_version:"

FIRST_SEEN = "FIRST_SEEN"
UNCHANGED = "UNCHANGED"
CHANGED = "CHANGED"
POLICY_UNREADABLE = "POLICY_UNREADABLE"


def fingerprints_dir(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / FINGERPRINTS_REL


def fingerprint_path(service: str, root: Path | None = None) -> Path:
    return fingerprints_dir(root) / f"{service}.json"


def policy_identity(root: Path | None = None) -> dict[str, Any] | None:
    """``{sha256, policy_version, bytes}`` for the policy this process would load, or None.

    None means "cannot be read", never "unchanged": the caller reports that as its own status
    rather than treating a missing file as agreement with the last recording.
    """
    path = (root if root is not None else _repo_root()) / POLICY_REL
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    version: str | None = None
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if line.startswith(_VERSION_KEY):
            version = line[len(_VERSION_KEY):].strip().strip("'\"") or None
            break
    return {"sha256": hashlib.sha256(raw).hexdigest(), "policy_version": version, "bytes": len(raw)}


def read_fingerprint(service: str, root: Path | None = None) -> dict[str, Any] | None:
    """What this service recorded last time it started, or None (never started here, or the
    record is unreadable — both mean the same thing to the caller: nothing to compare with)."""
    try:
        return json.loads(fingerprint_path(service, root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def check_and_record(service: str, *, now: str | None = None, root: Path | None = None) -> dict[str, Any]:
    """Compare the policy in this image against what ``service`` last recorded, and record it.

    Never raises: an unwritable state directory or an unreadable policy degrades to a status
    the caller can print. The recording happens even when the status is CHANGED — the point is
    to notice each change once, not to keep re-announcing one that has already been reported.
    """
    stamp = now or timeutil.utc_now_iso()
    identity = policy_identity(root)
    previous = read_fingerprint(service, root)
    if identity is None:
        return {"status": POLICY_UNREADABLE, "service": service, "checked_at": stamp,
                "policy_ref": POLICY_REL, "sha256": None, "policy_version": None,
                "previous_sha256": (previous or {}).get("sha256"),
                "previous_policy_version": (previous or {}).get("policy_version")}
    if previous is None:
        status = FIRST_SEEN
    elif previous.get("sha256") == identity["sha256"]:
        status = UNCHANGED
    else:
        status = CHANGED
    result = {
        "status": status,
        "service": service,
        "checked_at": stamp,
        "policy_ref": POLICY_REL,
        "sha256": identity["sha256"],
        "policy_version": identity["policy_version"],
        "bytes": identity["bytes"],
        "previous_sha256": (previous or {}).get("sha256"),
        "previous_policy_version": (previous or {}).get("policy_version"),
        "previous_seen_at": (previous or {}).get("seen_at"),
    }
    record = {
        "service": service,
        "sha256": identity["sha256"],
        "policy_version": identity["policy_version"],
        "policy_ref": POLICY_REL,
        "seen_at": stamp,
        "pid": os.getpid(),
    }
    try:
        path = fingerprint_path(service, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)          # whole-file replace: a reader never sees a half-written record
        result["recorded"] = True
    except OSError as exc:
        # Not fatal, and not silent: the comparison still happened and is still reported. A
        # service that cannot record will simply say CHANGED again on its next start.
        result["recorded"] = False
        result["record_error"] = exc.__class__.__name__
    return result


def _short(value: Any) -> str:
    return str(value)[:12] if value else "?"


def banner(result: dict[str, Any]) -> str:
    """The one stderr line a service prints at startup. Always printed, including UNCHANGED:
    "which policy am I running" is the question this answers, and an answer that appears only
    when something is wrong cannot be checked against a log from before it went wrong."""
    status = result.get("status")
    version, sha = result.get("policy_version"), _short(result.get("sha256"))
    if status == POLICY_UNREADABLE:
        return (f"POLICY: {result['policy_ref']} could not be read — this process cannot report "
                "which policy it runs under (it will still fail on its own when it needs one)\n")
    if status == CHANGED:
        return (f"POLICY CHANGED: {result.get('previous_policy_version')} "
                f"({_short(result.get('previous_sha256'))}) -> {version} ({sha}). This image's "
                f"{result['policy_ref']} differs from what this machine last ran. Expected after a "
                "policy bump or a rollback; unexpected otherwise — check the deployed tag.\n")
    if status == FIRST_SEEN:
        return f"POLICY: {version} ({sha}) — first recording on this machine\n"
    return f"POLICY: {version} ({sha}) unchanged\n"


def change_notice(result: dict[str, Any]) -> str:
    """The control-channel message for a CHANGED result — one short block, in Thomas's window.

    Sent by the operator only. It says what changed and what to check, and deliberately asks
    for nothing: this is a notice, not an approval, and no verb here can undo a deploy.
    """
    return (
        "정책 파일이 바뀐 채로 런타임이 올라왔습니다.\n"
        f"  이전 : {result.get('previous_policy_version')} ({_short(result.get('previous_sha256'))})\n"
        f"  지금 : {result.get('policy_version')} ({_short(result.get('sha256'))})\n"
        f"  파일 : {result['policy_ref']} (이미지에 포함됨 — 배포로만 바뀝니다)\n"
        "정책 범프나 롤백 직후라면 정상입니다. 그런 적이 없다면 배포된 태그를 확인하세요."
    )
