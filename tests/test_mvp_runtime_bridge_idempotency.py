"""At-most-once on the assistant doors, and the one place a domain-scoped grant met a global effect.

Two changes share this file because they answer the same question — *what does the second
arrival of one intent do?* — from opposite ends. ``bridge_idempotency`` answers it for a frame
the client re-sent after a timeout. The ``switch_bridge`` guard answers it for the effect side:
a grant that names one domain must not re-arm the others, and until control state has a domain
the only honest answer is to refuse rather than to widen quietly.

None of these need a local Core: the ask-construction path is stubbed and every refusal under
test precedes it.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import approval as approval_mod
from runtime.mvp_runtime import bridge_idempotency, control, dispatch_bridge, switch_bridge
from runtime.mvp_runtime.control import ACTIVE, KILLED, ControlStore
from runtime.mvp_runtime.errors import ControlBlocked
from runtime.mvp_runtime.permission import TRADING_SWITCH_PERMISSION_SCOPE
from runtime.mvp_runtime.store import BRIDGE_REQUESTS_FILE, LedgerStore

NOW = "2026-08-05T09:00:00Z"
LATER = "2026-08-05T09:05:00Z"
MUCH_LATER = "2026-08-06T09:00:00Z"

DOOR = "test.door"


@pytest.fixture
def ledger(tmp_path):
    return LedgerStore(tmp_path)


@pytest.fixture
def captured_execute():
    """The door's forward to the pipeline worker, captured. How many times it lands here IS
    the claim in most of what follows, so the counter is the assertion and not scaffolding."""
    calls: list[dict] = []

    def _execute(text, kind, reason, naver_keywords):
        calls.append({"request": text, "kind": kind, "reason": reason})
        return {"ok": True, "kind": kind, "task_id": f"task-{len(calls)}",
                "final_response": "ok", "actor": "assistant_bridge"}

    _execute.calls = calls
    return _execute


def _halt(store, *, now=NOW):
    control.apply_command(store, control.CMD_KILL, actor="test", now=now, reason="halt")


def _claim(ledger, *, request_id="req-1", fingerprint="sha256:aa", now=NOW, door=DOOR):
    return bridge_idempotency.claim(
        ledger, door=door, request_id=request_id, request_fingerprint=fingerprint, now=now,
    )


def _complete(ledger, *, request_id="req-1", fingerprint="sha256:aa", outcome=None, now=NOW,
              door=DOOR):
    bridge_idempotency.complete(
        ledger, door=door, request_id=request_id, request_fingerprint=fingerprint,
        outcome=outcome or {"task_id": "task-1"}, now=now,
    )


# --- reading the id off the frame ------------------------------------------------

def test_a_frame_without_an_id_is_unchanged():
    """Optional on purpose: making it mandatory would refuse every existing caller in the
    failing-closed direction for a property most of them do not need."""
    assert bridge_idempotency.request_id_of({"command": "status"}) is None


@pytest.mark.parametrize("value", [42, "", "   ", [], {}, True])
def test_a_malformed_id_is_refused_rather_than_treated_as_absent(value):
    """A caller that sent `request_id: 42` believed it was getting at-most-once. Reading that
    as "no id" hands it the opposite of what it asked for, at the moment it matters."""
    with pytest.raises(ControlBlocked) as exc:
        bridge_idempotency.request_id_of({"request_id": value})
    assert exc.value.reason_code == "MALFORMED_REQUEST"


def test_an_id_is_a_name_not_a_payload():
    long_id = "x" * (bridge_idempotency.MAX_REQUEST_ID_LENGTH + 1)
    with pytest.raises(ControlBlocked) as exc:
        bridge_idempotency.request_id_of({"request_id": long_id})
    assert exc.value.reason_code == "MALFORMED_REQUEST"


def test_the_fingerprint_ignores_the_id_and_the_envelope_and_nothing_else():
    """Same request under two ids fingerprints the same; different requests do not — which is
    what makes a reused id detectable instead of dangerous. The envelope (`proto`,
    `client_id`) is excluded with the id: a retry from another session of the same assistant,
    or after it upgraded its client, is the same request, not a reused id."""
    a = bridge_idempotency.fingerprint({"request_id": "one", "kind": "analysis"})
    b = bridge_idempotency.fingerprint({"request_id": "two", "kind": "analysis"})
    c = bridge_idempotency.fingerprint({"request_id": "one", "kind": "research"})
    assert a == b
    assert a != c
    d = bridge_idempotency.fingerprint({"request_id": "one", "kind": "analysis", "proto": 2, "client_id": "hermes:dm"})
    e = bridge_idempotency.fingerprint({"request_id": "one", "kind": "analysis", "proto": 2, "client_id": "hermes:cron:x"})
    assert d == a == e


# --- claim / complete / release ---------------------------------------------------

def test_a_first_claim_is_granted(ledger):
    assert _claim(ledger) is None


def test_a_second_claim_while_in_flight_is_refused(ledger):
    """Two frames carrying one id race for the same lock. The loser must be refused rather
    than executing alongside the winner — that is the difference between this and a check."""
    _claim(ledger)
    with pytest.raises(ControlBlocked) as exc:
        _claim(ledger)
    assert exc.value.reason_code == "REQUEST_IN_FLIGHT"


def test_a_completed_id_replays_its_outcome(ledger):
    _claim(ledger)
    _complete(ledger, outcome={"task_id": "task-7", "kind": "analysis"})
    prior = _claim(ledger, now=LATER)
    assert prior is not None

    reply = bridge_idempotency.replay_reply(prior)
    # `ok` is true and that is the point: a false here reads as a failure and invites the
    # retry-with-a-fresh-id that produces the second effect this exists to prevent.
    assert reply["ok"] is True
    assert reply["replayed"] is True
    assert reply["outcome"] == {"task_id": "task-7", "kind": "analysis"}
    assert reply["applied_at"] == NOW


def test_the_same_id_with_different_parameters_is_blocked(ledger):
    """Answering this with the first frame's outcome would have the door report success for
    something it never did."""
    _claim(ledger)
    _complete(ledger)
    with pytest.raises(ControlBlocked) as exc:
        _claim(ledger, fingerprint="sha256:bb", now=LATER)
    assert exc.value.reason_code == "REQUEST_ID_REUSED"


def test_a_reused_id_is_blocked_even_while_the_first_is_in_flight(ledger):
    _claim(ledger)
    with pytest.raises(ControlBlocked) as exc:
        _claim(ledger, fingerprint="sha256:bb")
    assert exc.value.reason_code == "REQUEST_ID_REUSED"


def test_a_released_id_is_immediately_claimable_again(ledger):
    """A refused effect changed nothing, so holding its id until the claim lapses would punish
    a caller for a request the door itself rejected.

    This is also the regression for the fold: `release` writes a row that is expired the moment
    it lands, so a reader that filtered expired rows *while scanning* would fall back to the
    `claimed` row behind it and report a released id as in flight for the rest of the TTL.
    """
    _claim(ledger)
    bridge_idempotency.release(
        ledger, door=DOOR, request_id="req-1", request_fingerprint="sha256:aa", now=NOW,
    )
    assert _claim(ledger, now=NOW) is None


def test_an_abandoned_claim_lapses(ledger):
    """The crash window, bounded. A process that died mid-effect must not hold an id all day."""
    _claim(ledger)
    assert _claim(ledger, now=MUCH_LATER) is None


def test_a_completed_id_lapses_too(ledger):
    _claim(ledger)
    _complete(ledger)
    assert _claim(ledger, now=MUCH_LATER) is None


def test_ids_are_scoped_per_door(ledger):
    """The same id at two doors is two requests, because it is."""
    _claim(ledger, door="switch.enable")
    assert _claim(ledger, door="dispatch") is None


def test_the_record_carries_no_payload(ledger):
    """A replayed answer should be small and stable. The report is already in the ledger and
    the read door's `result <task_id>` is how it is fetched."""
    _claim(ledger)
    _complete(ledger, outcome={"task_id": "task-1"})
    rows = (ledger.root / BRIDGE_REQUESTS_FILE).read_text(encoding="utf-8")
    assert "task-1" in rows
    assert "final_response" not in rows


# --- the dispatch door ------------------------------------------------------------

def test_a_dispatch_without_an_id_still_runs(tmp_path, captured_execute):
    store = ControlStore(tmp_path)
    out = dispatch_bridge.apply_dispatch(
        {"request": "analyze this", "kind": "analysis", "reason": "asked"},
        control_store=store, ledger=LedgerStore(tmp_path), execute=captured_execute, now=NOW,
    )
    assert out["ok"] is True
    assert len(captured_execute.calls) == 1


def test_a_repeated_dispatch_runs_once(tmp_path, captured_execute):
    """The failure this closes: a client that timed out re-sends, and a second analysis is
    billed to a quota shared with the operator's own assistant."""
    store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path)
    frame = {"request": "analyze this", "kind": "analysis", "reason": "asked",
             "request_id": "req-abc"}

    first = dispatch_bridge.apply_dispatch(frame, control_store=store, ledger=ledger,
                                           execute=captured_execute, now=NOW)
    second = dispatch_bridge.apply_dispatch(frame, control_store=store, ledger=ledger,
                                            execute=captured_execute, now=LATER)

    assert len(captured_execute.calls) == 1, "the worker ran twice for one request"
    assert first["ok"] is True
    assert first["request_id"] == "req-abc"
    assert "replayed" not in first
    assert second["replayed"] is True
    assert second["request_id"] == "req-abc"
    assert second["outcome"]["task_id"] == first["task_id"]


def test_a_dispatch_id_reused_for_a_different_request_is_blocked(tmp_path, captured_execute):
    store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path)
    dispatch_bridge.apply_dispatch(
        {"request": "analyze this", "kind": "analysis", "reason": "asked", "request_id": "r1"},
        control_store=store, ledger=ledger, execute=captured_execute, now=NOW,
    )
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(
            {"request": "something else", "kind": "analysis", "reason": "asked",
             "request_id": "r1"},
            control_store=store, ledger=ledger, execute=captured_execute, now=LATER,
        )
    assert exc.value.reason_code == "REQUEST_ID_REUSED"
    assert len(captured_execute.calls) == 1


def test_a_refused_dispatch_never_burns_its_id(tmp_path, captured_execute):
    """The claim is taken after validation, so a frame that was going to be refused anyway
    leaves its id usable — otherwise a typo would cost the caller an id it never spent."""
    store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path)
    with pytest.raises(ControlBlocked):
        dispatch_bridge.apply_dispatch(
            {"request": "  ", "kind": "analysis", "reason": "asked", "request_id": "r2"},
            control_store=store, ledger=ledger, execute=captured_execute, now=NOW,
        )
    out = dispatch_bridge.apply_dispatch(
        {"request": "analyze this", "kind": "analysis", "reason": "asked", "request_id": "r2"},
        control_store=store, ledger=ledger, execute=captured_execute, now=NOW,
    )
    assert out["ok"] is True


def test_a_worker_refusal_never_burns_its_id_either(tmp_path, captured_execute):
    """New with the split: a refusal envelope from the worker (its kill-switch re-check, a
    BRIDGE_BUSY) means nothing ran, so the claim is released and the same id retries cleanly
    once the cause clears — the same contract an in-process refusal always had."""
    store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path)
    frame = {"request": "analyze this", "kind": "analysis", "reason": "asked",
             "request_id": "r-worker"}

    def _busy(text, kind, reason, naver_keywords):
        return {"ok": False, "reason_code": "BRIDGE_BUSY", "reason": "retry shortly"}

    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(frame, control_store=store, ledger=ledger,
                                       execute=_busy, now=NOW)
    assert exc.value.reason_code == "BRIDGE_BUSY"

    out = dispatch_bridge.apply_dispatch(frame, control_store=store, ledger=ledger,
                                         execute=captured_execute, now=LATER)
    assert out["ok"] is True
    assert len(captured_execute.calls) == 1


def test_a_killed_runtime_refuses_before_the_id_is_claimed(tmp_path, captured_execute):
    """The kill switch is not a spent id: a dispatch refused by a halt must be retriable with
    the same id once the runtime is resumed."""
    store = ControlStore(tmp_path)
    _halt(store)
    frame = {"request": "analyze this", "kind": "analysis", "reason": "asked",
             "request_id": "r3"}
    with pytest.raises(ControlBlocked):
        dispatch_bridge.apply_dispatch(frame, control_store=store, ledger=LedgerStore(tmp_path),
                                       execute=captured_execute, now=NOW)

    control.apply_command(store, control.CMD_RESUME, actor="test", now=LATER, reason="go")
    out = dispatch_bridge.apply_dispatch(frame, control_store=store,
                                         ledger=LedgerStore(tmp_path),
                                         execute=captured_execute, now=LATER)
    assert out["ok"] is True
    assert len(captured_execute.calls) == 1


def test_a_dispatch_id_without_a_ledger_fails_closed(tmp_path, captured_execute):
    """Honouring the request while dropping the guarantee gives the caller the duplicate it
    was trying to avoid, silently."""
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(
            {"request": "analyze this", "kind": "analysis", "reason": "asked",
             "request_id": "r4"},
            control_store=store, ledger=None, execute=captured_execute, now=NOW,
        )
    assert exc.value.reason_code == "IDEMPOTENCY_UNAVAILABLE"
    assert captured_execute.calls == []


# --- the switch door's enable -----------------------------------------------------

class _FakeApprovalStore:
    def __init__(self, tmp_path):
        self.path = tmp_path / "approvals.jsonl"
        self.records: dict[str, dict] = {}
        self.decisions: dict[str, dict] = {}
        self.appended: list[dict] = []

    def get(self, approval_id):
        return self.records.get(approval_id)

    def get_permission_decision(self, decision_id):
        return self.decisions.get(decision_id)

    def append(self, records):
        for record in records:
            self.appended.append(dict(record))
            self.records[record["approval_id"]] = dict(record)

    def append_permission_decision(self, decision):
        self.decisions[decision["permission_decision_id"]] = dict(decision)


@pytest.fixture
def asks(tmp_path, monkeypatch):
    """An approval store plus a stubbed ask builder — the ask's construction is covered in
    `test_mvp_runtime_switch_bridge`; what matters here is how many of them get minted."""
    store = _FakeApprovalStore(tmp_path)
    minted: list[str] = []

    def _fake_open_ask(domain, reason, *, scope, approval_store, control_store, now, repo_root):
        approval_id = f"approval_{len(minted)}"
        minted.append(approval_id)
        approval_store.append([{"approval_id": approval_id, "status": approval_mod.STATUS_PENDING}])
        return {
            "ok": False, "reason_code": "APPROVAL_REQUIRED", "reason": "needs Thomas",
            "approval_id": approval_id, "expires_at": "2026-08-05T09:30:00Z",
            "approve_with": f"/approve {approval_id}", "domain": domain, "scope": scope,
        }

    monkeypatch.setattr(switch_bridge, "_open_ask", _fake_open_ask)
    store.minted = minted
    return store


def test_a_repeated_enable_does_not_mint_a_second_ask(tmp_path, asks):
    """The failure this closes: two pending approvals in front of Thomas for one intent, where
    he answers one and the other sits there spendable."""
    control_store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path)
    frame = {"command": "enable", "reason": "assistant: resume", "request_id": "req-enable"}

    first = switch_bridge.apply_switch(frame, control_store=control_store, ledger=ledger,
                                       approval_store=asks, now=NOW)
    second = switch_bridge.apply_switch(frame, control_store=control_store, ledger=ledger,
                                        approval_store=asks, now=LATER)

    assert asks.minted == ["approval_0"], "a retry minted a second approval request"
    assert first["approval_id"] == "approval_0"
    # Both replies name the id; `replayed` is the only thing that separates them.
    assert first["request_id"] == "req-enable"
    assert "replayed" not in first
    # The replay carries what a retry actually needs back: the id, when it lapses, and how.
    assert second["replayed"] is True
    assert second["outcome"]["approval_id"] == "approval_0"
    assert second["outcome"]["approve_with"] == "/approve approval_0"


def test_an_enable_without_an_id_behaves_exactly_as_before(tmp_path, asks):
    control_store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path)
    frame = {"command": "enable", "reason": "assistant: resume"}
    switch_bridge.apply_switch(frame, control_store=control_store, ledger=ledger,
                               approval_store=asks, now=NOW)
    switch_bridge.apply_switch(frame, control_store=control_store, ledger=ledger,
                               approval_store=asks, now=LATER)
    assert asks.minted == ["approval_0", "approval_1"]


def test_the_stop_path_takes_no_idempotency_lock(tmp_path, asks, monkeypatch):
    """`disable` is idempotent by construction and is the emergency stop. An emergency control
    you must first acquire a lock for is a worse trade than the duplicate it would prevent."""
    control_store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path)

    def _explode(*args, **kwargs):
        raise AssertionError("the stop path must not touch the request ledger")

    monkeypatch.setattr(switch_bridge.bridge_idempotency, "claim", _explode)
    out = switch_bridge.apply_switch(
        {"command": "disable", "reason": "assistant: stop", "request_id": "req-stop"},
        control_store=control_store, ledger=ledger, approval_store=asks, now=NOW,
    )
    assert out["ok"] is True
    assert control_store.load().mode == KILLED
    # Not deduped, but not silently swallowed either: this door refuses keys it does not act
    # on, so a key it understands and does not use has to say so.
    assert out["request_id"] == "req-stop"
    assert "replayed" not in out


# --- the domain-scoped grant and the global effect --------------------------------

def _grant(domain="crypto", *, stop_ref="stop_unset"):
    """A grant shaped like the fields the door reads.

    ``stop_ref`` defaults to a placeholder no real control state hashes to, so a test that
    means to spend must point it at the stop actually in effect (`_arm`). A test that forgets
    fails loudly rather than passing because the door happened not to check.
    """
    return {
        "approval_id": "approval_test",
        "status": approval_mod.STATUS_APPROVED,
        "permission_decision_id": "permdec_test",
        "validity": {"expires_at": "2026-08-05T09:30:00Z"},
        "action_fingerprint": "fp_bound",
        "approved_action_snapshot": {
            "permission_scope": TRADING_SWITCH_PERMISSION_SCOPE,
            "target_ref": f"trading_switch:{domain}",
            "normalized_parameters": {"stop_ref": stop_ref},
        },
    }


def _arm(store, control_store, approval_id="approval_test"):
    """Point a grant at the stop currently in effect — what minting the ask would have done."""
    snapshot = store.records[approval_id]["approved_action_snapshot"]
    snapshot["normalized_parameters"] = {"stop_ref": switch_bridge.stop_ref(control_store.load())}
    return store


@pytest.fixture
def approved(tmp_path, monkeypatch):
    store = _FakeApprovalStore(tmp_path)
    store.records["approval_test"] = _grant()
    store.decisions["permdec_test"] = {"permission_decision_id": "permdec_test"}
    monkeypatch.setattr(switch_bridge, "compute_action_fingerprint", lambda s: "fp_bound")
    monkeypatch.setattr(
        switch_bridge.approval_mod, "build_consumed_record",
        lambda rec, dec, **kw: {**rec, "status": approval_mod.STATUS_CONSUMED},
    )
    return store


def test_a_second_domain_cannot_be_re_armed_through_the_global_switch(tmp_path, approved,
                                                                     monkeypatch):
    """`control.CMD_RESUME` clears the one kill switch this runtime has — `ControlState` has no
    domain field. The grant, meanwhile, is minted per domain and `permission.py` writes the
    constraint "authorizes this domain's switch only". Those agree today for one reason: there
    is one domain. Widening the set without giving control state a domain would make a `crypto`
    grant re-arm `prediction` too, so the door refuses instead."""
    control_store = ControlStore(tmp_path)
    _halt(control_store)
    monkeypatch.setattr(switch_bridge, "_ALLOWED_DOMAINS", frozenset({"crypto", "prediction"}))

    with pytest.raises(ControlBlocked) as exc:
        switch_bridge.apply_switch(
            {"command": "enable", "reason": "assistant: resume",
             "approval_id": "approval_test"},
            control_store=control_store, ledger=LedgerStore(tmp_path), approval_store=approved,
            now=NOW,
        )
    assert exc.value.reason_code == "DOMAIN_EFFECT_MISMATCH"
    # Nothing applied, and the grant is still spendable once the mismatch is resolved.
    assert control_store.load().mode == KILLED
    assert approved.records["approval_test"]["status"] == approval_mod.STATUS_APPROVED


def test_one_domain_spends_normally(tmp_path, approved):
    """The guard is inert while the set has one member — it is not a new refusal for today."""
    control_store = ControlStore(tmp_path)
    _halt(control_store)
    _arm(approved, control_store)
    out = switch_bridge.apply_switch(
        {"command": "enable", "reason": "assistant: resume", "approval_id": "approval_test"},
        control_store=control_store, ledger=LedgerStore(tmp_path), approval_store=approved,
        now=NOW,
    )
    assert out["ok"] is True
    assert control_store.load().mode == ACTIVE
