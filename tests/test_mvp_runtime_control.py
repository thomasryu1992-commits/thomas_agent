"""R4 Operator emergency console tests — control state, transitions, and loop enforcement.

None of these need a local Core: control commands and the PAUSED/KILLED refusal short-circuit
before any task runs, so they exercise the safety control in isolation.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import control
from runtime.mvp_runtime.control import ACTIVE, KILLED, PAUSED, ControlState, ControlStore
from runtime.mvp_runtime.errors import ControlBlocked
from runtime.mvp_runtime.operator import InboundMessage, OperatorIdentity, handle_operator_message
from runtime.mvp_runtime.store import LedgerStore

NOW = "2026-07-16T09:00:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")


class FakeLedger:
    def __init__(self, tip="sha256:" + "ab" * 32):
        self.control: list[dict] = []
        self._tip = tip

    def append_control(self, entry):
        self.control.append(entry)

    def last_audit_hash(self):
        if isinstance(self._tip, Exception):
            raise self._tip
        return self._tip


def _store(tmp_path):
    return ControlStore(tmp_path)


def _task_msg(text="이 사업 아이디어를 분석해줘: 구독형 반려동물 사료"):
    return InboundMessage(text=text, sender_id="tg-12345", chat_id="chat-777", chat_type="private",
                          is_forwarded=False, channel="telegram_private")


# --- state + store ----------------------------------------------------------

def test_missing_file_is_active(tmp_path):
    state = _store(tmp_path).load()
    assert state.mode == ACTIVE
    assert state.execution_allowed is True


def test_pause_then_load_blocks_execution(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_PAUSE, actor="tg-12345", now=NOW)
    state = store.load()
    assert state.mode == PAUSED
    assert state.execution_allowed is False
    assert state.updated_by == "tg-12345"


def test_kill_then_resume_round_trip(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW, reason="halt")
    assert store.load().mode == KILLED
    control.apply_command(store, control.CMD_RESUME, actor="op", now=NOW)
    assert store.load().mode == ACTIVE
    assert store.load().execution_allowed is True


def test_corrupt_file_fails_closed_to_killed(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    state = store.load()
    assert state.mode == KILLED
    assert "fail-closed" in state.reason
    assert state.execution_allowed is False


def test_unknown_mode_fails_closed_to_killed(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"mode": "BOGUS"}), encoding="utf-8")
    assert store.load().mode == KILLED


def test_save_write_failure_fails_closed(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a file where a directory parent is expected
    store = ControlStore(blocker / "nested")
    with pytest.raises(ControlBlocked) as exc:
        store.save(ControlState(mode=PAUSED))
    assert exc.value.reason_code == "CONTROL_WRITE_FAILED"


# --- commands ---------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("/status", (control.CMD_STATUS, None)),
    ("status", (control.CMD_STATUS, None)),
    ("/PAUSE", (control.CMD_PAUSE, None)),
    ("/stop task-9", (control.CMD_STOP, "task-9")),
    ("stop_task task-9", (control.CMD_STOP, "task-9")),
    ("/resume  ", (control.CMD_RESUME, None)),
    # Telegram appends the bot username to menu-picked commands — the suffix is addressing.
    ("/kill@thomas_agent_bot", (control.CMD_KILL, None)),
    ("/stop@bot task-9", (control.CMD_STOP, "task-9")),
])
def test_parse_command_recognizes(text, expected):
    assert control.parse_command(text) == expected


@pytest.mark.parametrize("text", [
    "이 사업 아이디어를 분석해줘", "/bogus", "", "   ", None,
    # @-suffix stripping applies only to the slash form — bare text stays non-command.
    "kill@thomas_agent_bot",
])
def test_parse_command_ignores_non_commands(text):
    assert control.parse_command(text) is None


def test_status_is_read_only_no_event(tmp_path):
    store = _store(tmp_path)
    ledger = FakeLedger()
    out = control.apply_command(store, control.CMD_STATUS, actor="op", now=NOW, ledger=ledger)
    assert out["changed"] is False
    assert "mode: ACTIVE" in out["reply"]
    assert ledger.control == []


def test_status_carries_the_audit_tip_as_an_external_anchor(tmp_path):
    """The chain's documented blind spot: a PREFIX of a valid chain verifies clean, so a
    truncated tail is invisible to /audit alone. Every /status answered over Telegram now
    leaves the tip hash in an off-machine chat history — a later tip that does not descend
    from an anchored one is the truncation signal."""
    store = _store(tmp_path)
    tip = "sha256:" + "cd" * 32
    out = control.apply_command(store, control.CMD_STATUS, actor="op", now=NOW,
                                ledger=FakeLedger(tip=tip))
    assert f"audit_tip: {tip}" in out["reply"]

    # An empty ledger says so; no ledger wired keeps the plain status (no tip line).
    out_empty = control.apply_command(store, control.CMD_STATUS, actor="op", now=NOW,
                                      ledger=FakeLedger(tip=None))
    assert "audit_tip: none (empty ledger)" in out_empty["reply"]
    assert "audit_tip" not in control.status_lines(store.load())


def test_status_still_answers_when_the_ledger_tip_is_unreadable(tmp_path):
    """kill_allows: read_only_status — reading the anchor must never take status down."""
    from runtime.mvp_runtime.errors import PersistenceError

    store = _store(tmp_path)
    broken = FakeLedger(tip=PersistenceError("LEDGER_UNREADABLE", "torn line"))
    out = control.apply_command(store, control.CMD_STATUS, actor="op", now=NOW, ledger=broken)
    assert "mode: ACTIVE" in out["reply"]
    assert "audit_tip: unavailable (LEDGER_UNREADABLE)" in out["reply"]


def test_stop_requires_task_id(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        control.apply_command(_store(tmp_path), control.CMD_STOP, actor="op", now=NOW, arg=None)
    assert exc.value.reason_code == "MISSING_TASK_ID"


def test_stop_takes_the_first_token_as_the_id_and_the_rest_as_the_reason(tmp_path):
    """`/stop treg_a1 급하게 멈춰줘` recorded a stop request for the task id
    "treg_a1 급하게 멈춰줘" — an id nothing can ever match — and named it back as if it were
    one. Same id/reason split `/approve <id> <reason>` and `/result <id>` already use."""
    store, ledger = _store(tmp_path), FakeLedger()
    out = control.apply_command(store, control.CMD_STOP, actor="op", now=NOW,
                                arg="treg_a1b2c3 급하게 멈춰줘", ledger=ledger)
    state = store.load()
    assert state.stop_requested_task_ids == ("treg_a1b2c3",)
    assert state.reason == "급하게 멈춰줘"
    assert ledger.control[-1]["task_id"] == "treg_a1b2c3"
    assert "급하게 멈춰줘" in out["reply"]


def test_stop_says_it_stopped_nothing_and_names_the_verbs_that_do(tmp_path):
    """Nothing on any execution path reads `stop_requested_task_ids` (grep it: only this
    module writes and displays it), and the old reply's "will apply once R6 introduces
    long-running tasks" never came true. Meanwhile /cancel really cancels a queued task. A
    confirmation that let the operator believe this verb stopped his task was the defect."""
    out = control.apply_command(_store(tmp_path), control.CMD_STOP, actor="op", now=NOW,
                                arg="treg_a1b2c3")
    assert "실행을 멈추지 않습니다" in out["reply"]
    assert "/cancel" in out["reply"] and "/kill" in out["reply"]


def test_stop_records_request_and_event(tmp_path):
    store = _store(tmp_path)
    ledger = FakeLedger()
    out = control.apply_command(store, control.CMD_STOP, actor="op", now=NOW, arg="task-9", ledger=ledger)
    assert out["changed"] is True
    assert "task-9" in store.load().stop_requested_task_ids
    assert ledger.control[0]["action"] == "stop"
    assert ledger.control[0]["task_id"] == "task-9"


def test_unknown_command_raises(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        control.apply_command(_store(tmp_path), "detonate", actor="op", now=NOW)
    assert exc.value.reason_code == "UNKNOWN_COMMAND"


def test_control_event_is_tamper_evident(tmp_path):
    store = _store(tmp_path)
    ledger = FakeLedger()
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW, ledger=ledger)
    event = ledger.control[0]
    assert event["action"] == "kill"
    assert event["resulting_mode"] == KILLED
    assert event["integrity"]["event_sha256"].startswith("sha256:")


def test_control_event_persisted_to_real_ledger(tmp_path):
    store = _store(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    control.apply_command(store, control.CMD_PAUSE, actor="op", now=NOW, ledger=ledger)
    rows = [json.loads(ln) for ln in (ledger.root / "control_events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows[0]["action"] == "pause"
    assert rows[0]["resulting_mode"] == PAUSED


# --- loop enforcement (handle_operator_message) -----------------------------

def test_killed_runtime_refuses_task_request(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW)
    reply = handle_operator_message(_task_msg(), registration=REG, control_store=store, now=NOW)
    assert reply.accepted is False
    assert reply.status == "REFUSED"
    assert reply.reason_code == "RUNTIME_KILLED"


def test_paused_runtime_refuses_task_request(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_PAUSE, actor="op", now=NOW)
    reply = handle_operator_message(_task_msg(), registration=REG, control_store=store, now=NOW)
    assert reply.status == "REFUSED"
    assert reply.reason_code == "RUNTIME_PAUSED"


def test_status_command_answered_even_when_killed(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW)
    reply = handle_operator_message(_task_msg(text="/status"), registration=REG, control_store=store, now=NOW)
    assert reply.status == "CONTROL"
    assert "mode: KILLED" in reply.text


def test_resume_via_channel_clears_kill(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW)
    reply = handle_operator_message(_task_msg(text="/resume"), registration=REG, control_store=store, now=NOW)
    assert reply.status == "CONTROL"
    assert store.load().mode == ACTIVE


def test_pause_command_then_task_refused(tmp_path):
    store = _store(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    reply = handle_operator_message(_task_msg(text="/pause"), registration=REG, control_store=store, store=ledger, now=NOW)
    assert reply.status == "CONTROL"
    refused = handle_operator_message(_task_msg(), registration=REG, control_store=store, now=NOW)
    assert refused.reason_code == "RUNTIME_PAUSED"
    # The /pause was durably recorded.
    rows = (ledger.root / "control_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(rows[0])["action"] == "pause"


def test_unverified_sender_never_reaches_control(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW)
    impostor = InboundMessage(text="/resume", sender_id="tg-99999", chat_id="chat-777", chat_type="private")
    reply = handle_operator_message(impostor, registration=REG, control_store=store, now=NOW)
    assert reply.status == "REFUSED"
    assert reply.reason_code == "UNREGISTERED_USER"
    # The kill still stands — an impostor cannot resume.
    assert store.load().mode == KILLED


# --- deleting the state file must not clear a stop ----------------------------

def test_missing_state_file_still_means_active_on_a_fresh_deployment(tmp_path):
    """The reason missing=ACTIVE exists: a fresh deployment must not be bricked by the
    mere absence of a control file. With no ledger history, nothing contradicts ACTIVE."""
    assert ControlStore(tmp_path).load().mode == ACTIVE


def test_deleting_the_state_file_does_not_resume_a_killed_runtime(tmp_path):
    """Corrupting the file failed closed to KILLED while *removing* it silently cleared
    the kill — an unauthenticated, unaudited resume that a volume remount or a cleanup
    script performs by accident. The durable control-event ledger is consulted instead."""
    store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path / ".runtime_governance_state" / "runtime_ledger")
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW, ledger=ledger)
    assert store.load().mode == KILLED

    store.path.unlink()                              # the deletion
    recovered = store.load()
    assert recovered.mode == KILLED and recovered.fail_closed is True
    assert "control-event ledger" in recovered.reason


def test_deleting_the_state_file_after_a_resume_is_active(tmp_path):
    """The ledger's LAST word decides: a kill that was properly resumed leaves the
    runtime ACTIVE, so a later deletion must not resurrect the stop."""
    store = ControlStore(tmp_path)
    ledger = LedgerStore(tmp_path / ".runtime_governance_state" / "runtime_ledger")
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW, ledger=ledger)
    control.apply_command(store, control.CMD_RESUME, actor="op", now=NOW, ledger=ledger)
    store.path.unlink()
    assert store.load().mode == ACTIVE


def test_unreadable_control_ledger_with_no_state_file_fails_closed(tmp_path):
    """Uncertainty about a safety state is not permission."""
    store = ControlStore(tmp_path)
    ledger_dir = tmp_path / ".runtime_governance_state" / "runtime_ledger"
    ledger_dir.mkdir(parents=True)
    (ledger_dir / "control_events.jsonl").write_text("{not json\n", encoding="utf-8")
    assert store.load().mode == KILLED


# --- the operator's own words -------------------------------------------------
#
# Telegram has no `--reason` option, so the text after the verb IS the reason. It used to be
# parsed and then dropped: `/kill 시장 급변으로 중단` recorded "killed by operator" and the one
# field that answers *why* the runtime is stopped was lost on both the state and the ledger.

def test_kill_records_the_text_after_the_verb_as_the_reason(tmp_path):
    store, ledger = _store(tmp_path), FakeLedger()
    outcome = control.apply_command(
        store, control.CMD_KILL, actor="tg-12345", now=NOW, arg="시장 급변으로 중단", ledger=ledger,
    )
    assert store.load().reason == "시장 급변으로 중단"
    assert ledger.control[-1]["reason"] == "시장 급변으로 중단"
    # ...and it is echoed, so the operator can see that it landed.
    assert "시장 급변으로 중단" in outcome["reply"]


def test_pause_and_resume_record_their_reason_too(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_PAUSE, actor="op", now=NOW, arg="점검 중")
    assert store.load().reason == "점검 중"
    control.apply_command(store, control.CMD_RESUME, actor="op", now=NOW, arg="점검 완료")
    assert store.load().reason == "점검 완료"


def test_an_explicit_reason_wins_over_the_verb_tail(tmp_path):
    """`--reason` is the caller being deliberate; the tail is a channel artifact."""
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW,
                          reason="explicit", arg="tail")
    assert store.load().reason == "explicit"


def test_a_recorded_reason_is_normalized_and_bounded(tmp_path):
    """It lands in a state file and on every control event, so it is bounded — and collapsed
    to one line, because a multi-line reason makes the /status report unreadable."""
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW,
                          arg="첫 줄\n\n둘째 줄   " + "가" * 400)
    reason = store.load().reason
    assert "\n" not in reason
    assert reason.startswith("첫 줄 둘째 줄 ")
    assert len(reason) == control.MAX_REASON_CHARS


def test_no_reason_still_gets_the_default_and_no_echo(tmp_path):
    store = _store(tmp_path)
    outcome = control.apply_command(store, control.CMD_KILL, actor="op", now=NOW)
    assert store.load().reason == "killed by operator"
    assert "이유 기록" not in outcome["reply"]


def test_a_reason_on_a_no_op_pause_is_reported_as_not_recorded(tmp_path):
    """`/pause` over a KILLED runtime changes nothing, so there is no state to carry the
    reason. Saying nothing would let the operator assume their words were logged."""
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW)
    outcome = control.apply_command(store, control.CMD_PAUSE, actor="op", now=NOW, arg="일단 멈춤")
    assert outcome["changed"] is False
    assert "기록되지 않았습니다" in outcome["reply"]
    assert store.load().reason == "killed by operator"


def test_kill_with_a_reason_arrives_through_the_telegram_parser(tmp_path):
    """End to end: the channel's own parser must hand the tail through as the argument."""
    assert control.parse_command("/kill 시장 급변") == (control.CMD_KILL, "시장 급변")
    store = _store(tmp_path)
    reply = handle_operator_message(
        InboundMessage(text="/kill 시장 급변", sender_id="tg-12345", chat_id="chat-777",
                       chat_type="private", is_forwarded=False, channel="telegram_private"),
        registration=REG, control_store=store, now=NOW,
    )
    assert reply.status == "CONTROL" and store.load().reason == "시장 급변"


# --- read-only verbs answer, but never silently substitute ---------------------

def test_a_mistyped_audit_count_answers_and_says_it_defaulted(tmp_path):
    """`/audit` is the command an operator runs when things are already broken and must
    answer while KILLED — so a typo never denies the read. It also never passes silently:
    the operator was looking at a different window than the one they asked for."""
    outcome = control.apply_command(_store(tmp_path), control.CMD_AUDIT, actor="op", now=NOW,
                                    arg="abc", ledger=LedgerStore(tmp_path / "ledger"))
    assert outcome["action"] == control.CMD_AUDIT
    assert "abc" in outcome["reply"] and str(control.AUDIT_TAIL_DEFAULT) in outcome["reply"]


def test_an_out_of_range_audit_count_states_the_clamp(tmp_path):
    outcome = control.apply_command(_store(tmp_path), control.CMD_AUDIT, actor="op", now=NOW,
                                    arg="500", ledger=LedgerStore(tmp_path / "ledger"))
    assert "500" in outcome["reply"] and str(control.AUDIT_TAIL_MAX) in outcome["reply"]


def test_a_usable_audit_count_gets_no_note(tmp_path):
    limit, note = control.parse_count_arg("5", default=10, maximum=100, usage="…")
    assert (limit, note) == (5, "")
    assert control.parse_count_arg(None, default=10, maximum=100, usage="…") == (10, "")


# --- transition guards --------------------------------------------------------

def test_pause_does_not_downgrade_a_kill(tmp_path):
    """`pause` is not the verb for clearing a kill — only `resume` is, and only for the
    authenticated operator. Downgrading KILLED to PAUSED moved a safety state in the
    permissive direction on a command that never asked to."""
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW)
    outcome = control.apply_command(store, control.CMD_PAUSE, actor="op", now=NOW)
    assert outcome["changed"] is False and outcome["mode"] == KILLED
    assert store.load().mode == KILLED


def test_resume_keeps_pending_stop_requests(tmp_path):
    """Stop requests are operator intent about specific tasks, not part of the pause they
    were recorded during; the old default-empty tuple discarded them with no event."""
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_STOP, actor="op", now=NOW, arg="task-9")
    control.apply_command(store, control.CMD_PAUSE, actor="op", now=NOW)
    control.apply_command(store, control.CMD_RESUME, actor="op", now=NOW)
    state = store.load()
    assert state.mode == ACTIVE and state.stop_requested_task_ids == ("task-9",)


# --- diagnostics must survive the corruption they exist to report -------------

def test_recovery_uses_the_flag_not_the_reason_prose(tmp_path):
    """`recovery` detected the corrupt case by substring-matching the reason, so an
    operator kill with --reason "fail-closed test" printed the wrong guidance."""
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW,
                          reason="fail-closed test of the kill path")
    text = control.recovery_lines(store.load(), None)
    assert "an operator stop is in effect" in text
    assert "unreadable/corrupt" not in text

    store.path.write_text("{broken", encoding="utf-8")
    corrupt_text = control.recovery_lines(store.load(), None)
    assert "unreadable/corrupt" in corrupt_text


def test_audit_renders_a_corrupt_event_instead_of_crashing(tmp_path):
    """A tampered line can be valid JSON with the wrong shapes. The naive
    event.get("event", {}).get(...) chain raised AttributeError on those — the diagnostic
    died exactly when needed and took the operator loop with it."""
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.root.mkdir(parents=True, exist_ok=True)
    (ledger.root / "audit_events.jsonl").write_text(
        json.dumps({"audit_event_id": "ae1", "event": 5, "integrity": "nope"}) + "\n"
        + json.dumps({"audit_event_id": "ae2", "event": {"outcome": "RECORDED",
                                                        "reason_codes": ["X"]},
                      "integrity": {}}) + "\n",
        encoding="utf-8",
    )
    text = control.audit_lines(ledger)
    assert "BROKEN" in text                      # reported...
    assert "MALFORMED EVENT BLOCK" in text       # ...and the bad line still renders


# --- policy drift gate --------------------------------------------------------

def test_console_verbs_stay_within_the_policy_grant():
    """Mechanical drift gate: every emergency verb the console exposes must be granted by
    the Governance Policy (control_channel.local_operator_console.emergency_controls_allowed).
    /resume silently exceeding the policy for several waves is exactly the drift class this
    catches — a new console verb now requires the matching policy edit in the same change."""
    import yaml

    from runtime.mvp_runtime.paths import repo_root

    policy = yaml.safe_load(
        (repo_root() / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8")
    )
    allowed = set(policy["control_channel"]["local_operator_console"]["emergency_controls_allowed"])
    policy_name = {control.CMD_STOP: "stop_task"}  # the policy names the stop verb stop_task
    for verb in control.COMMANDS:
        assert policy_name.get(verb, verb) in allowed, (
            f"console verb {verb!r} is not granted by the Governance Policy - "
            "either drop the verb or extend emergency_controls_allowed explicitly"
        )


def test_every_channel_verb_names_the_authority_that_permits_it():
    """The gate above covers only the EMERGENCY console. Four more verb families grew on the
    same verified door (approval, memory, registry, feedback) with no governance edit anywhere,
    and nothing would have noticed a fifth. `operator.CHANNEL_VERB_AUTHORITY` is the inventory
    of the whole door; this asserts it both ways, so a new family fails until its author writes
    down what permits it. (Extending `emergency_controls_allowed` instead would be wrong — that
    grant is for emergency controls, not for ordinary reads like /tasks.)

    The fifth family did arrive — the domain console (`/crypto`, `/pred`) — and this gate is
    what made it write down its authority before it could answer anything."""
    from runtime.mvp_runtime import (
        approval, domain_console, memory_console, operator, operator_feedback, registry_console,
    )

    reachable = (
        set(control.COMMANDS)
        | set(approval.COMMANDS)
        | {memory_console.LIST_COMMAND, memory_console.PROMOTE_COMMAND}
        | set(registry_console.COMMANDS)
        | {operator_feedback.COMMAND}
        | set(domain_console.COMMANDS)
    )
    inventory = set(operator.CHANNEL_VERB_AUTHORITY)
    assert reachable - inventory == set(), (
        "a control-channel verb has no recorded governance authority - add it to "
        "operator.CHANNEL_VERB_AUTHORITY with the policy clause that permits it"
    )
    assert inventory - reachable == set(), (
        "operator.CHANNEL_VERB_AUTHORITY lists a verb the channel no longer answers"
    )
    # No entry may be blank, and every emergency-grant claim must be true in the policy.
    import yaml

    from runtime.mvp_runtime.paths import repo_root

    policy = yaml.safe_load(
        (repo_root() / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8")
    )
    allowed = set(policy["control_channel"]["local_operator_console"]["emergency_controls_allowed"])
    policy_name = {control.CMD_STOP: "stop_task"}
    for verb, authority in operator.CHANNEL_VERB_AUTHORITY.items():
        assert authority.strip(), f"{verb!r} has an empty authority"
        if authority == operator._EMERGENCY_GRANT:
            assert policy_name.get(verb, verb) in allowed, (
                f"{verb!r} claims the emergency grant but the policy does not list it"
            )

    # And the state-changing subset stays a subset: a mutating verb missing from
    # `_MUTATING_VERBS` would skip the chat channel's slash requirement. (`_MUTATING_VERBS`
    # matches raw tokens, so it also carries the `stop_task` alias the inventory canonicalizes.)
    mutating = {control._ALIASES.get(v, v) for v in operator._MUTATING_VERBS}
    assert mutating <= inventory
