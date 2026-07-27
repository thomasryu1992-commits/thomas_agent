"""The canary tool's report half — the step that runs AFTER real money has moved.

This file exists because of a live failure on 2026-07-26. A canary order FILLED at the venue
(BTCUSDT 0.001 @ 64,491) and the operator saw a `TypeError` traceback instead of the fill:
step 8 called ``LedgerStore(root)`` with ``--root``'s default of ``None``, so ``Path(None)``
raised, the narrow ``except (MvpRuntimeError, AuditError)`` did not catch it, and the process
died between "the order is at the venue" and "here is what the venue said". The audit event was
built and never appended — a real order with nothing on the durable chain, which is precisely
what ``p5_policy_gate``'s ``post_action_report_and_audit`` exists to prevent.

The bare constructor was wrong twice over, and the second way was silent: ``LedgerStore``
takes the ledger DIRECTORY, so with a real ``--root`` it would have written the chain to the
root itself rather than under ``.runtime_governance_state/runtime_ledger/``. Every other caller
in ``scripts/`` appends ``LEDGER_REL``; ``LedgerStore.default()`` is that resolution, and it
accepts ``None``.

Nothing here touches a venue or a real ledger: every collaborator across the point of no return
is a stub, and the assertions are about where the store is rooted and what the tool reports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK
from runtime.mvp_runtime.errors import MvpRuntimeError
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore
from scripts import place_canary_order as pco

ARGV = ["--symbol", "BTCUSDT", "--direction", "LONG", "--quantity", "0.001", "--notional", "65"]


class _Limits:
    daily_loss_limit_usdt = 20.0
    max_open_notional_usdt = 120.0


class _Control:
    execution_allowed = True

    def __init__(self, root=None):
        self.root = root

    def load(self):
        return self

    @classmethod
    def default(cls, root=None):
        return cls(root)


class _Adapter:
    network_egress = True


class _AccountSnapshot:
    """Only the fields this tool reads off the venue snapshot."""

    realized_windows = {"1d": {"net": 0.0, "pnl": 0.0, "fee": 0.0, "funding": 0.0}}


_RESULT = {
    "reconcile_status": "RECONCILED",
    "symbol": "BTCUSDT",
    "exchange_order_id": "4242",
    "client_order_id": "canary-1",
    "mismatches": [],
    "fill": {"avg_price": 64491.0, "executed_qty": 0.001, "cum_quote": 64.491},
}
_RECORD = {"canary_order_id": "canary_abc", "reconcile_status": "RECONCILED", "clean": True}


@pytest.fixture
def approved(monkeypatch):
    """Every collaborator up to and past the venue call, stubbed to the happy path.

    The order "lands" without a socket, so the tests below are about step 8 alone.
    """
    monkeypatch.setattr(pco, "resolve_live_order_limits", lambda root, now: (_Limits(), {"valid": True}))
    monkeypatch.setattr(pco, "ControlStore", _Control)
    # The market-data precondition: the canary checks `--notional` against the venue's own
    # price, and the gate's default collector is the mock, whose synthetic price is refused
    # outright. These tests are about what happens at and past the venue call, so they have to
    # say what they mean — assume the read-only feed is configured — rather than ride a default
    # that now blocks at step 3. ARGV declares 65 USDT for 0.001, so 65,000 is the truthful
    # price; that the check *does* block on a synthetic one is its own test, in the live-guard
    # and canary-door suites.
    monkeypatch.setattr(pco, "read_reference_price", lambda *a, **kw: (65_000.0, {}))
    monkeypatch.setattr(pco, "live_risk_snapshot",
                        lambda **kw: {"daily_loss_limit_breached": False})
    monkeypatch.setattr(pco.live_promotion, "clean_canary_order_count", lambda root: (0, None))
    monkeypatch.setattr(pco, "count_today", lambda root: 0)
    # A bare object() was enough until the breaker started reading the venue's realized figure
    # off this snapshot. The stub now carries the field the real AccountSnapshot does, so the
    # test exercises the same shape the tool actually receives.
    monkeypatch.setattr(pco, "read_account", lambda **kw: (_AccountSnapshot(), {}))
    monkeypatch.setattr(pco, "compute_open_notional_usdt", lambda snapshot, at_cap: 0.0)
    monkeypatch.setattr(pco.live_execution, "select_order_adapter", lambda **kw: _Adapter())
    monkeypatch.setattr(pco, "build_live_order_intent", lambda *a, **kw: {"symbol": "BTCUSDT"})
    monkeypatch.setattr(pco, "enrich_order_identity", lambda intent: intent)
    monkeypatch.setattr(pco, "evaluate_live_order_guard", lambda *a, **kw: {"approved": True})
    monkeypatch.setattr(pco, "render_guard_text", lambda verdict: "live order guard: READY")
    monkeypatch.setattr(pco.live_governance, "prepare_live_order_governance",
                        lambda *a, **kw: {"permission_decision": {"permission_decision_id": "pd_1"}})
    monkeypatch.setattr(pco.live_execution, "submit_and_reconcile", lambda *a, **kw: dict(_RESULT))
    monkeypatch.setattr(pco.live_promotion, "build_canary_order_record", lambda **kw: dict(_RECORD))
    monkeypatch.setattr(pco.live_promotion, "select_canary_registry",
                        lambda **kw: type("_R", (), {"append_canary_order": lambda self, r: None})())
    monkeypatch.setattr(pco.live_governance, "report_live_order",
                        lambda *a, **kw: ({"event_type": "live_order"}, "sha256:test"))
    monkeypatch.setattr(pco, "select_live_order_counter", lambda **kw: _Counter())


class _Counter:
    """Stand-in for the durable daily counter. Class-level so a test can read the tally."""

    submissions = 0
    raises: Exception | None = None

    def record_submission(self, *, day=None):
        if _Counter.raises is not None:
            raise _Counter.raises
        _Counter.submissions += 1
        return _Counter.submissions


@pytest.fixture(autouse=True)
def _reset_counter():
    _Counter.submissions = 0
    _Counter.raises = None
    yield


@pytest.fixture
def appended(monkeypatch):
    """Records the ledger root every append lands on, without writing a chain."""
    seen: list[Path] = []
    monkeypatch.setattr(LedgerStore, "append_audit_events",
                        lambda self, events: seen.append(self.root))
    return seen


def test_the_audit_event_lands_under_the_ledger_dir_not_the_state_root(approved, appended, tmp_path):
    """The silent half of the bug: a bare ``LedgerStore(root)`` roots the chain at ``root``.

    Asserting the resolved path rather than "it did not crash" is what separates the two
    defects — this one would have passed a no-exception test while writing the ledger to the
    wrong directory.
    """
    assert pco.main([*ARGV, "--root", str(tmp_path)]) == EXIT_OK
    assert appended == [tmp_path / LEDGER_REL]


def test_the_default_root_does_not_crash_after_the_order_is_placed(approved, appended, monkeypatch):
    """``--root`` defaults to None, and ``Path(None)`` is a TypeError.

    The regression under test is the *whole* invocation form the docstring documents:
    no ``--root`` at all. `LedgerStore.default(None)` resolves the repo ledger, so the store is
    rooted rather than exploding; the append itself is stubbed, so no real chain is touched.
    """
    assert pco.main(ARGV) == EXIT_OK
    assert len(appended) == 1
    assert appended[0].name == Path(LEDGER_REL).name
    assert appended[0].is_absolute()


def test_an_unexpected_failure_past_the_venue_is_reported_not_raised(approved, monkeypatch, capsys):
    """A traceback here reads as "the order never happened". It must never be the output.

    ``TypeError`` is deliberately the exception raised: it is the exact class that escaped the
    narrow ``(MvpRuntimeError, AuditError)`` tuple and turned a filled canary into a crash.
    """
    def boom(self, events):
        raise TypeError("argument should be a str or an os.PathLike object")

    monkeypatch.setattr(LedgerStore, "append_audit_events", boom)

    # BLOCKED, not OK: the order landed but the chain has no record of it, so the P5
    # post-action audit obligation is unmet and a 0 exit would call that a clean canary.
    assert pco.main(ARGV) == EXIT_BLOCKED
    out = capsys.readouterr().out
    assert "UNEXPECTED_TypeError" in out
    assert "the order IS placed" in out
    assert _RESULT["exchange_order_id"] in out


def test_a_governed_audit_failure_still_blocks(approved, monkeypatch, capsys):
    """The pre-existing narrow path keeps its behavior, and now also fails the exit code."""
    from runtime.mvp_runtime.audit import AuditError

    def boom(self, events):
        raise AuditError("LEDGER_WRITE_FAILED")

    monkeypatch.setattr(LedgerStore, "append_audit_events", boom)
    assert pco.main(ARGV) == EXIT_BLOCKED
    assert "the order IS placed" in capsys.readouterr().out


def test_the_operator_is_told_the_order_adapter_was_authorized(approved, appended, tmp_path, capsys):
    """The one path that can move real money printed no SAFETY_GATE notice.

    Every other gated capability announces itself — the network provider, the search tool, the
    workspace writer, the operator channel. The live order adapter did not, because
    `gate_banners` took one named parameter per role and nobody added a fifth. The stubbed
    `_Adapter` here declares `network_egress = True`, i.e. the grant is open and this run can
    reach the venue, which is exactly when the operator must be told.
    """
    assert pco.main([*ARGV, "--root", str(tmp_path)]) == EXIT_OK
    assert "SAFETY_GATE: live order adapter authorized (network_access)" in capsys.readouterr().err

# --- the daily cap: an order that is placed must be counted ---------------------------

def test_a_placed_canary_consumes_daily_budget(approved, appended):
    """The gap this closes: the cap read a number nothing ever wrote.

    `count_today` reads `live_order_counter.json`; the only code that incremented it lived in
    `live_leg.execute_live_entry`, the autonomous leg no entry point may import. So the one
    door that can actually place an order never counted its own orders — `count_today` returned
    0 for a missing file, forever, and a registered `max_daily_order_count` refused nothing.
    Two real canaries went out on 2026-07-26 and the counter file did not exist afterwards.
    """
    assert pco.main(ARGV) == EXIT_OK
    assert _Counter.submissions == 1


def test_an_ambiguous_submit_still_consumes_daily_budget(approved, monkeypatch, capsys):
    """A submit that RAISED may still have reached the venue, so it must spend the cap.

    This is `LiveOrderCounter`'s own rule — without it a flapping connection spends one day's
    budget many times over. Over-counting a submit that never left is the safe direction for a
    risk limit; under-counting one that did is not.
    """
    monkeypatch.setattr(pco.live_execution, "submit_and_reconcile",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            MvpRuntimeError("ORDER_TRANSPORT", "connection reset")))

    assert pco.main(ARGV) == EXIT_BLOCKED
    assert _Counter.submissions == 1, "an ambiguous submit must still consume daily budget"


def test_a_counter_failure_is_reported_and_fails_the_exit_code(approved, appended, capsys):
    """An uncounted order leaves the cap reading low for every later run — a widened limit."""
    _Counter.raises = MvpRuntimeError("LIVE_COUNTER_UNREADABLE", "counter is unreadable")

    assert pco.main(ARGV) == EXIT_BLOCKED
    out = capsys.readouterr().out
    assert "DAILY CAP" in out and "LIVE_COUNTER_UNREADABLE" in out
    assert "the order IS placed" in out


def test_a_counter_failure_never_becomes_a_traceback(approved, appended, capsys):
    """Same lesson as the audit append: past the venue, nothing may crash the report."""
    _Counter.raises = RuntimeError("something unforeseen")

    assert pco.main(ARGV) == EXIT_BLOCKED
    assert "RuntimeError" in capsys.readouterr().out


def test_every_caller_of_the_venue_also_counts_the_order():
    """Structural gate on the shape of the bug, not just this instance of it.

    The defect was not a typo — it was a *door* that could reach the venue without touching
    the daily counter, and nothing said that was wrong. Two call sites reach
    ``submit_and_reconcile`` today; a third that forgets to count would silently widen the
    daily cap exactly as this one did, and would pass every behavioural test above because
    those only exercise the canary path.
    """
    from pathlib import Path as _Path

    from runtime.mvp_runtime.paths import repo_root

    root = _Path(repo_root())
    doors = [
        root / "scripts" / "place_canary_order.py",
        root / "runtime" / "mvp_runtime" / "crypto" / "live_leg.py",
    ]
    # Excluded by path PARTS, not by substring: `"/tests/" in str(path)` is false on Windows,
    # where the separator is a backslash, so the first version of this gate reported the suite's
    # own fixtures as new callers on one runner and passed on the other.
    skipped_dirs = {".venv", ".git", "tests", "historical", "deferred", "generated"}
    found = []
    for path in root.rglob("*.py"):
        if skipped_dirs & set(path.parts):
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "submit_and_reconcile(" in source and "def submit_and_reconcile" not in source:
            found.append(path)
    assert sorted(found) == sorted(doors), (
        f"a new caller of submit_and_reconcile appeared: {sorted(set(found) - set(doors))}. "
        "Add it here AND make it record a submission on the daily counter, or the registered "
        "max_daily_order_count silently stops bounding anything."
    )
    for path in doors:
        assert "record_submission()" in path.read_text(encoding="utf-8"), (
            f"{path.name} can place a live order but never records it on the daily counter"
        )
