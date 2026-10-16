"""C7 PR-A: the ledger checkpoint — recorded, moved, frozen on a mismatch, and never enforced (synthetic only).

``docs/proposals/HOLDINGS_LEDGER_CHECKPOINT_V0.1.md`` §3 and the PR-A readiness note §3.2–§3.6. The restore
cases themselves (C7-1 … C7-11) are in ``test_mvp_runtime_holdings_c7_partial_restore.py``; this file pins
the mechanism: where the checkpoint lives, when it moves, what the two detectors see, the read order of the
readiness, and that every state-only writer keeps the key.
"""

from __future__ import annotations

import json
import shutil

from runtime.mvp_runtime.holdings import cash_flows
from tests._h6d_support import CURRENT_OK, Feed, iso, ledger, ms, pay, resolve, set_cutover

T1, T2 = "2026-10-09T04:00:00Z", "2026-10-09T05:00:00Z"
P2 = pay("p2", at="2026-10-09T04:30:00Z")


class VenueFeed(Feed):
    def flow_history(self, source, *, start_ms, end_ms, timeout_seconds=5):
        rows = super().flow_history(source, start_ms=start_ms, end_ms=end_ms)
        return [r for r in rows if start_ms <= int(r.get("transactionTime") or r.get("insertTime") or 0) <= end_ms]


def fire(d, now, *pays):
    return cash_flows.collect_binance(VenueFeed(pay=list(pays)), d, now=now, now_ms=ms(now))


def later(at, hours=0):
    return iso(ms(at) + hours * 3_600_000)


def state(d):
    return cash_flows.load_state(d)


def tip(d):
    rows = ledger(d)
    return {"lines": len(rows), "sha256": rows[-1]["sha256"]}


def checkpoint(d):
    found = state(d).get(cash_flows.CHECKPOINT_KEY)
    return None if found is None else {"lines": found["lines"], "sha256": found["sha256"]}


def consistency(d, now):
    return cash_flows.readiness(d, now=now, current=CURRENT_OK)["checks"]["ledger_state_consistency"]


def write_state(d, body):
    cash_flows.state_path(d).write_text(json.dumps(body), encoding="utf-8")


def tell_exceptions(d, now):
    ready = cash_flows.update_readiness(d, now=now, current=CURRENT_OK)
    cash_flows.mark_exceptions_told(d, token=ready["exceptions_token"], now=now)


def test_every_write_moves_the_checkpoint_to_the_ledgers_new_end(tmp_path):
    set_cutover(tmp_path)
    assert checkpoint(tmp_path) == tip(tmp_path)                                  # the first write records it
    fire(tmp_path, T1)
    fire(tmp_path, T2, P2)
    assert checkpoint(tmp_path) == tip(tmp_path) and tip(tmp_path)["lines"] > 1
    assert state(tmp_path)[cash_flows.CHECKPOINT_KEY]["at"] == T2
    assert cash_flows.DETECTION_MARKER_KEY not in state(tmp_path)
    assert consistency(tmp_path, T2) == "PASS"


def test_a_mismatch_freezes_the_checkpoint_and_the_alert_key_while_the_writes_go_on(tmp_path):
    set_cutover(tmp_path)
    fire(tmp_path, T1)
    backup = tmp_path.parent / (tmp_path.name + "-backup")
    shutil.copytree(tmp_path, backup)
    fire(tmp_path, T2, P2)
    frozen = checkpoint(tmp_path)
    shutil.copyfile(cash_flows.ledger_path(backup), cash_flows.ledger_path(tmp_path))
    fire(tmp_path, later(T2, 1), P2)
    marker = state(tmp_path)[cash_flows.DETECTION_MARKER_KEY]
    key = cash_flows.blocked_state(tmp_path)["key"]
    assert marker["checkpoint"] == frozen and marker["since"] == later(T2, 1)
    for hours in (2, 3):
        fire(tmp_path, later(T2, hours), P2, pay(f"n{hours}", at=later(T2, hours - 1)))
    assert checkpoint(tmp_path) == frozen                                          # not moved
    assert state(tmp_path)[cash_flows.DETECTION_MARKER_KEY] == marker              # first detection kept
    assert cash_flows.blocked_state(tmp_path)["key"] == key                        # one failure, one alert key
    assert {"pay:n2", "pay:n3"} <= {r.get("source_event_key") for r in ledger(tmp_path)}   # writes went on


def test_a_marker_stands_even_when_the_ledger_matches_again(tmp_path):
    """Detection never clears itself: an operator adoption (PR-C) does."""
    set_cutover(tmp_path)
    fire(tmp_path, T1)
    backup = tmp_path.parent / (tmp_path.name + "-backup")
    shutil.copytree(tmp_path, backup)
    fire(tmp_path, T2, P2)
    good = cash_flows.ledger_path(tmp_path).read_bytes()
    shutil.copyfile(cash_flows.ledger_path(backup), cash_flows.ledger_path(tmp_path))
    fire(tmp_path, later(T2, 1), P2)
    cash_flows.ledger_path(tmp_path).write_bytes(good)                             # the right ledger is back
    fire(tmp_path, later(T2, 2), P2)
    assert cash_flows.DETECTION_MARKER_KEY in state(tmp_path)
    assert consistency(tmp_path, later(T2, 2)) == "FAIL"


def test_a_told_exception_missing_from_the_ledger_is_a_proven_loss(tmp_path):
    set_cutover(tmp_path)
    fire(tmp_path, T1)
    backup = tmp_path.parent / (tmp_path.name + "-backup")
    shutil.copytree(tmp_path, backup)
    fire(tmp_path, T2, P2)
    tell_exceptions(tmp_path, T2)
    assert state(tmp_path)["exceptions"]["told_keys"]
    shutil.copyfile(cash_flows.ledger_path(backup), cash_flows.ledger_path(tmp_path))
    body = state(tmp_path)
    body.pop(cash_flows.CHECKPOINT_KEY)                                            # only the second detector can see it
    write_state(tmp_path, body)
    assert consistency(tmp_path, later(T2, 1)) == "FAIL"                          # read-only, before any write
    fire(tmp_path, later(T2, 30))                                                 # past the overlap: P2 stays lost
    marker = state(tmp_path)[cash_flows.DETECTION_MARKER_KEY]
    assert marker["reason"] == cash_flows.TOLD_KEY_MISSING
    assert checkpoint(tmp_path) == marker["checkpoint"]                           # recorded once, frozen
    assert cash_flows.blocked_state(tmp_path)["category"] == "marker"


def test_a_told_exception_that_was_resolved_since_is_no_loss(tmp_path):
    set_cutover(tmp_path)
    fire(tmp_path, T1)
    fire(tmp_path, T2, P2)
    tell_exceptions(tmp_path, T2)
    resolve(tmp_path, "pay:p2", "deposit", now=later(T2, 1))
    fire(tmp_path, later(T2, 2), P2)
    assert cash_flows.readiness(tmp_path, now=later(T2, 2), current=CURRENT_OK)["exceptions"] == 0
    assert cash_flows.DETECTION_MARKER_KEY not in state(tmp_path)
    assert consistency(tmp_path, later(T2, 2)) == "PASS"


def test_a_malformed_checkpoint_is_detected_not_trusted(tmp_path):
    set_cutover(tmp_path)
    fire(tmp_path, T1)
    for bad in ({"lines": "3", "sha256": "x"}, {"lines": 0, "sha256": "x"}, {"lines": 2}, "tip", {"lines": True,
                                                                                                    "sha256": "x"}):
        body = state(tmp_path)
        body.pop(cash_flows.DETECTION_MARKER_KEY, None)
        body[cash_flows.CHECKPOINT_KEY] = bad
        write_state(tmp_path, body)
        assert consistency(tmp_path, T2) == "FAIL", bad
    fire(tmp_path, T2)
    assert state(tmp_path)[cash_flows.DETECTION_MARKER_KEY]["reason"] == cash_flows.CHECKPOINT_MALFORMED


def test_without_a_checkpoint_the_first_write_records_the_observed_end_and_says_absent_before(tmp_path):
    """R-3 (가): a state saved before PR-A is not judged here; PR-B's history check is the one that will."""
    set_cutover(tmp_path)
    fire(tmp_path, T1)
    body = state(tmp_path)
    body.pop(cash_flows.CHECKPOINT_KEY)
    write_state(tmp_path, body)
    ready = cash_flows.readiness(tmp_path, now=T1, current=CURRENT_OK)
    assert ready["checkpoint"] == "absent" and ready["checks"]["ledger_state_consistency"] == "PASS"
    fire(tmp_path, T2, P2)
    assert checkpoint(tmp_path) == tip(tmp_path)
    assert cash_flows.readiness(tmp_path, now=T2, current=CURRENT_OK)["checkpoint"] == "present"
    assert not [r for r in ledger(tmp_path) if "checkpoint" in str(r.get("event"))]   # no ledger line of its own


def test_the_readiness_reads_the_state_before_the_ledger(tmp_path, monkeypatch):
    set_cutover(tmp_path)
    order = []
    real_state, real_verify = cash_flows.load_state, cash_flows.verify
    monkeypatch.setattr(cash_flows, "load_state", lambda d: order.append("state") or real_state(d))
    monkeypatch.setattr(cash_flows, "verify", lambda d: order.append("ledger") or real_verify(d))
    cash_flows.readiness(tmp_path, now=T1, current=CURRENT_OK)
    assert order[:2] == ["state", "ledger"]


def test_a_write_between_the_two_reads_is_no_false_failure(tmp_path, monkeypatch):
    """The race the read order exists for: the ledger read after the state may only be longer."""
    set_cutover(tmp_path)
    fire(tmp_path, T1)
    real_verify = cash_flows.verify
    raced = []

    def verify_after_a_write(d):
        if not raced:
            raced.append(True)
            fire(tmp_path, T2, P2)                                                 # lands between the two reads
        return real_verify(d)

    monkeypatch.setattr(cash_flows, "verify", verify_after_a_write)
    assert cash_flows.readiness(tmp_path, now=T2, current=CURRENT_OK)["checks"]["ledger_state_consistency"] == "PASS"


def test_every_state_only_writer_keeps_the_checkpoint(tmp_path):
    set_cutover(tmp_path)
    fire(tmp_path, T1)
    fire(tmp_path, T2, P2)
    kept = state(tmp_path)[cash_flows.CHECKPOINT_KEY]
    ready = cash_flows.update_readiness(tmp_path, now=T2, current=CURRENT_OK)
    assert state(tmp_path)[cash_flows.CHECKPOINT_KEY] == kept
    cash_flows.mark_exceptions_told(tmp_path, token=ready["exceptions_token"], now=T2)
    assert state(tmp_path)[cash_flows.CHECKPOINT_KEY] == kept
    cash_flows.mark_readiness_told(tmp_path, now=T2)
    assert state(tmp_path)[cash_flows.CHECKPOINT_KEY] == kept
    cash_flows.mark_fire_verified(tmp_path, now=T2)
    assert checkpoint(tmp_path) == tip(tmp_path)


def test_a_marker_survives_every_state_only_writer(tmp_path):
    set_cutover(tmp_path)
    fire(tmp_path, T1)
    body = state(tmp_path)
    body[cash_flows.CHECKPOINT_KEY] = {"lines": 99, "sha256": "f" * 64}
    write_state(tmp_path, body)
    fire(tmp_path, T2, P2)
    marker = state(tmp_path)[cash_flows.DETECTION_MARKER_KEY]
    ready = cash_flows.update_readiness(tmp_path, now=T2, current=CURRENT_OK)
    cash_flows.mark_exceptions_told(tmp_path, token=ready["exceptions_token"], now=T2)
    cash_flows.mark_readiness_told(tmp_path, now=T2)
    assert state(tmp_path)[cash_flows.DETECTION_MARKER_KEY] == marker
