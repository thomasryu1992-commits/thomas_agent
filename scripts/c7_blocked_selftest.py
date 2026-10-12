#!/usr/bin/env python3
"""C7 PR-D self-test: the BLOCKED judgement and its told record, on a synthetic lane only.

A formal, fixed check of PR-D's state transitions for an image that cannot be asserted with an inline
import (`HOLDINGS_C7_PR_D_DEPLOY_VALIDATION_DESIGN_V0.1.md` §2, criteria D-1–D-10). It is not a way to
read holdings data, and it is shaped so that it cannot:

- **No input surface (D-1).** It takes no arguments, reads no environment variable and never reads stdin.
  Any argument is refused before anything runs.
- **Its own synthetic root only (D-2, D-3).** Every call gets an explicit directory inside a fresh
  ``TemporaryDirectory`` made under ``/tmp`` (``SYNTHETIC_PARENT``), the tmpfs of the isolated candidate run.
  The parent is fixed: no environment variable (``TMPDIR``) and no argument can move it, and nothing falls
  back to another temporary directory. It imports only ``cash_flows``, whose functions take that directory;
  it never imports ``store`` (whose ``state_dir()`` defaults to the real state) and never resolves the
  default state path. A parent that is unusable or under a ``.runtime_governance_state`` directory is a FAIL
  before any lane starts; so is a root that cannot be made or does not land directly in the parent.
- **No network, no delivery (D-4, D-5).** Nothing here imports an operator channel, a provider or an HTTP
  client. "Delivery" is this process deciding whether to record the alert.
- **Closed output (D-6).** ``PASS <check>`` / ``FAIL <check>``, ``SOURCE <sha256> <path>`` for the files it
  covers, and ``RESULT PASS|FAIL``. An exception becomes ``FAIL <check>`` with no message or traceback.
- **Source integrity is part of the result.** Each covered file must resolve, through no symlink and no
  ``..``, to a regular file inside the source root and hash cleanly. One that is missing, unreadable or
  outside makes ``FAIL source_integrity`` and ``RESULT FAIL``, however the checks went.
- **Runtime untouched (D-9).** Nothing in ``runtime/`` imports this module; it changes no runtime file.

Run it only where it was approved, isolated on a candidate image (D-10): ``python -m scripts.c7_blocked_selftest``.
Exit 0 when every check passes, 1 when any fails, 2 when input was given.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.holdings import cash_flows  # noqa: E402 — after the path fix, like the other scripts

# The source this check stands for: printed as sha256 so a candidate image can be tied to an approved Git SHA.
COVERED_SOURCES = (
    "runtime/mvp_runtime/holdings/cash_flows.py",
    "runtime/mvp_runtime/holdings/store.py",
    "runtime/mvp_runtime/scheduler.py",
    "scripts/holdings_board.py",
    "scripts/c7_blocked_selftest.py",
)
STATE_ROOT_NAME = ".runtime_governance_state"
# Where every synthetic root is made: the candidate run's /tmp tmpfs (design §2.5). A constant, so only the
# repository tests can point it elsewhere (Windows CI has no /tmp); nothing at run time can move it.
SYNTHETIC_PARENT = Path("/tmp")
_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _clock() -> Callable[[int], str]:
    """Instants after the synthetic ledger's first line, which ``set_cutover`` stamps with the wall clock."""
    base = datetime.now(timezone.utc) + timedelta(minutes=1)
    return lambda minutes: (base + timedelta(minutes=minutes)).strftime(_FORMAT)


def _parent() -> Path:
    """The resolved synthetic parent, or an error: it must exist as a directory outside any state root."""
    parent = SYNTHETIC_PARENT.resolve(strict=True)
    if not parent.is_dir() or any(part == STATE_ROOT_NAME for part in parent.parts):
        raise RuntimeError("synthetic parent refused")
    return parent


@contextmanager
def _lane() -> Iterator[Path]:
    """A synthetic lane: its own temporary root, a cutover line in a fresh ledger, and a state. Never a
    default path: the directory handed to every call is built here and checked before use."""
    parent = _parent()
    with tempfile.TemporaryDirectory(prefix="c7-selftest-", dir=parent) as temp:
        root = Path(temp).resolve()
        if root.parent != parent or any(part == STATE_ROOT_NAME for part in root.parts):
            raise RuntimeError("synthetic root outside its parent")
        state_dir = root / "holdings"
        state_dir.mkdir()
        cash_flows.set_cutover(state_dir, at="2026-01-01T00:00:00Z", requested_by="c7-selftest")
        yield state_dir


def _break(state_dir: Path) -> bytes:
    """Edit the ledger's first line and keep its hash: every verify refuses it. Returns the good bytes."""
    path = cash_flows.ledger_path(state_dir)
    good = path.read_bytes()
    lines = good.decode("utf-8").splitlines()
    row = json.loads(lines[0])
    row["selftest_edited"] = True
    lines[0] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return good


def _deliver(state_dir: Path, now: str) -> tuple[str, str] | None:
    """One judgement whose alert counts as delivered: recorded at once."""
    alert = cash_flows.blocked_alert(state_dir, now=now)["alert"]
    if alert is not None and cash_flows.mark_blocked_told(state_dir, token=alert[0], now=now) is not True:
        raise RuntimeError("a delivered alert was not recorded")
    return alert


# --- the checks (HOLDINGS_C7_PR_D_DEPLOY_VALIDATION_DESIGN_V0.1.md §2.3) ------------------------------------

def check_synthetic_root_isolated() -> bool:
    with _lane() as state_dir:
        return (state_dir.is_relative_to(_parent())
                and not any(part == STATE_ROOT_NAME for part in state_dir.parts)
                and cash_flows.ledger_path(state_dir).parent == state_dir
                and cash_flows.state_path(state_dir).parent == state_dir)


def check_healthy_is_quiet() -> bool:
    at = _clock()
    with _lane() as state_dir:
        quiet = cash_flows.blocked_alert(state_dir, now=at(0)) == {"alert": None, "note": None}
        return quiet and not cash_flows.blocked_told_path(state_dir).exists()


def check_broken_is_told_once_and_stays_quiet() -> bool:
    at = _clock()
    with _lane() as state_dir:
        _break(state_dir)
        first = _deliver(state_dir, at(0))
        again = cash_flows.blocked_alert(state_dir, now=at(1))["alert"]
        return first is not None and first[0].startswith("blocked|") and again is None


def check_failed_delivery_is_offered_again() -> bool:
    at = _clock()
    with _lane() as state_dir:
        _break(state_dir)
        first = cash_flows.blocked_alert(state_dir, now=at(0))["alert"]     # the send failed: nothing recorded
        again = cash_flows.blocked_alert(state_dir, now=at(1))["alert"]
        return first is not None and again is not None and first[0] == again[0]


def check_failed_record_is_offered_again() -> bool:
    at = _clock()
    with _lane() as state_dir:
        _break(state_dir)
        token = cash_flows.blocked_alert(state_dir, now=at(0))["alert"][0]
        cash_flows.blocked_told_path(state_dir).mkdir()                        # the told write cannot replace it
        try:
            cash_flows.mark_blocked_told(state_dir, token=token, now=at(0))
        except OSError:
            pass
        else:
            return False
        cash_flows.blocked_told_path(state_dir).rmdir()
        return cash_flows.blocked_alert(state_dir, now=at(1))["alert"] is not None


def check_repair_is_cleared_once_and_recurrence_told() -> bool:
    at = _clock()
    with _lane() as state_dir:
        good = _break(state_dir)
        _deliver(state_dir, at(0))
        cash_flows.ledger_path(state_dir).write_bytes(good)
        cleared = _deliver(state_dir, at(1))
        after = cash_flows.blocked_alert(state_dir, now=at(2))["alert"]
        _break(state_dir)
        recurrence = cash_flows.blocked_alert(state_dir, now=at(3))["alert"]
        return (cleared is not None and cleared[0].startswith("resolved|") and after is None
                and recurrence is not None and recurrence[0].startswith("blocked|"))


def check_vanished_is_not_cleared() -> bool:
    at = _clock()
    with _lane() as state_dir:
        _break(state_dir)
        _deliver(state_dir, at(0))
        cash_flows.ledger_path(state_dir).unlink()
        cash_flows.state_path(state_dir).unlink()
        result = cash_flows.blocked_alert(state_dir, now=at(1))
        told, _note = cash_flows.read_blocked_told(state_dir)
        return result["alert"] is None and bool(result["note"]) and told.get("resolved_told_at") is None


def check_stale_token_is_refused() -> bool:
    at = _clock()
    with _lane() as state_dir:
        _break(state_dir)
        token = _deliver(state_dir, at(5))[0]
        before = cash_flows.blocked_told_path(state_dir).read_bytes()
        refused = cash_flows.mark_blocked_told(state_dir, token=token, now=at(0)) is False
        return refused and cash_flows.blocked_told_path(state_dir).read_bytes() == before


def check_lock_is_not_blocked() -> bool:
    with _lane() as state_dir:
        with cash_flows._state_lock(state_dir):                                # a writer holds the state
            return cash_flows.blocked_state(state_dir)["blocked"] is False


def check_alert_text_carries_no_key() -> bool:
    at = _clock()
    with _lane() as state_dir:
        _break(state_dir)
        token, text = cash_flows.blocked_alert(state_dir, now=at(0))["alert"]
        key = token.split("|", 1)[1]
        digest = key.rsplit(":", 1)[1]
        return key not in text and digest not in text and "BLOCKED" in text


CHECKS: tuple[tuple[str, Callable[[], bool]], ...] = (
    ("synthetic_root_isolated", check_synthetic_root_isolated),
    ("healthy_is_quiet", check_healthy_is_quiet),
    ("broken_is_told_once_and_stays_quiet", check_broken_is_told_once_and_stays_quiet),
    ("failed_delivery_is_offered_again", check_failed_delivery_is_offered_again),
    ("failed_record_is_offered_again", check_failed_record_is_offered_again),
    ("repair_is_cleared_once_and_recurrence_told", check_repair_is_cleared_once_and_recurrence_told),
    ("vanished_is_not_cleared", check_vanished_is_not_cleared),
    ("stale_token_is_refused", check_stale_token_is_refused),
    ("lock_is_not_blocked", check_lock_is_not_blocked),
    ("alert_text_carries_no_key", check_alert_text_carries_no_key),
)


def _source_digest(rel: str) -> str:
    """sha256 of one covered file, or an error. The path is fixed here, relative and without ``..``; it must
    resolve, through no symlink, to a regular file inside the source root."""
    if Path(rel).is_absolute() or ".." in Path(rel).parts or STATE_ROOT_NAME in Path(rel).parts:
        raise ValueError("source path refused")
    root = ROOT.resolve(strict=True)
    path = (root / rel).resolve(strict=True)
    if path != root / rel or not path.is_file():
        raise ValueError("source path refused")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> tuple[list[tuple[str, str]], bool]:
    """``(sha256, path)`` for each covered file that hashed, and whether every one did. A file that is
    missing, unreadable or outside the source root is left out and makes the second value False."""
    out, complete = [], True
    for rel in COVERED_SOURCES:
        try:
            out.append((_source_digest(rel), rel))
        except Exception:  # noqa: BLE001 — a source that cannot be hashed is a FAIL, and says nothing more (D-6)
            complete = False
    return out, complete


def run_selftest_checks() -> list[tuple[str, bool]]:
    results = []
    for name, check in CHECKS:
        try:
            ok = check() is True
        except Exception:  # noqa: BLE001 — a check that raises is a FAIL, and says nothing more (D-6)
            ok = False
        results.append((name, ok))
    return results


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print("FAIL input_refused")
        print("RESULT FAIL")
        return 2
    try:
        _parent()
    except Exception:  # noqa: BLE001 — no usable synthetic parent: no lane starts anywhere (D-2)
        print("FAIL synthetic_root_isolated")
        print("RESULT FAIL")
        return 1
    try:
        results = run_selftest_checks()
        for name, ok in results:
            print(f"{'PASS' if ok else 'FAIL'} {name}")
        hashes, complete = source_hashes()
        print(f"{'PASS' if complete else 'FAIL'} source_integrity")
        for digest, rel in hashes:
            print(f"SOURCE {digest} {rel}")
        passed = complete and all(ok for _name, ok in results)
    except Exception:  # noqa: BLE001 — anything unforeseen is a FAIL with no message or traceback (D-6)
        passed = False
    print(f"RESULT {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
