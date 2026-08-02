"""Domain console — asking the crypto stack what it is doing.

Both stacks ran on a schedule and spoke only when *they* had something to say; their state
was reachable only from a terminal on the host. These verbs close that, and the whole design
question is what they must NOT become on the way.

What must hold:

- Every subcommand is a **read**, rendered by the module that already owns that answer — so
  the chat and the host CLI cannot give two accounts of the same money.
- This console **cannot reach the order path**, structurally, not by convention.
- It opens **no socket**: the one network flag in the underlying board is never passed.
- Reads answer in any runtime mode (`kill_allows: read_only_status`) and write no ledger
  event (a read that appends to the log it reads races its own tail).
- A store that cannot be read is **reported**, never softened into an empty board — "no open
  positions" because a file would not parse is a false statement about money.
- A mistyped subcommand is refused, never silently answered with the default.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import domain_console, operator
from runtime.mvp_runtime.domain_console import apply_domain_command, parse_domain_command
from runtime.mvp_runtime.errors import MvpRuntimeError, OperatorBlocked
from runtime.mvp_runtime.events import stamped_event
from runtime.mvp_runtime.operator import (
    InboundMessage,
    OperatorIdentity,
    handle_operator_message,
)

NOW = "2026-07-28T09:00:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")


def _msg(text):
    return InboundMessage(text=text, sender_id="tg-12345", chat_id="chat-777")


# --- parsing -----------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("/crypto", ("CRYPTO", None)),
    ("/crypto status", ("CRYPTO", "status")),
    ("/crypto  READINESS ", ("CRYPTO", "readiness")),
    ("crypto paper", ("CRYPTO", "paper")),          # slash-optional, like every read verb
])
def test_parses_the_domain_verbs(text, expected):
    assert parse_domain_command(text) == expected


@pytest.mark.parametrize("text", ["", "   ", None, "분석해줘", "/tasks", "cryptocurrency 분석"])
def test_leaves_everything_else_alone(text):
    """A prose message mentioning crypto is a request, not a command — the tokenizer takes
    the whole leading token, so `cryptocurrency` is not `crypto`."""
    assert parse_domain_command(text) is None


# --- the boundary this console exists inside ---------------------------------

def test_the_console_is_covered_by_the_order_path_tripwire():
    """The crypto stack can place a real order, and a chat verb is exactly where "just one
    more argument" would be most tempting.

    The rule already has an owner — `test_no_autonomous_entry_point_reaches_the_live_order_path`
    — so this console was added to ITS list rather than given a second, weaker check here. What
    this pins is the *coverage*: drop the console from that list and the protection disappears
    silently, because a passing tripwire over four files looks exactly like a passing tripwire
    over five."""
    from tests.test_mvp_runtime_crypto_live_execution import AUTONOMOUS_ENTRY_POINTS

    assert "runtime/mvp_runtime/domain_console.py" in AUTONOMOUS_ENTRY_POINTS


def test_the_board_is_never_asked_for_the_account():
    """`--account` is the one flag that turns the dashboard into a network call. A chat verb
    must not be the thing that opens a socket to a venue."""
    source = open(domain_console.__file__, encoding="utf-8").read()
    assert "account=True" not in source
    assert "read_account" not in source


def test_every_advertised_subcommand_exists_and_every_existing_one_is_advertised():
    """`usage()` is built from the table rather than typed out, so the help line cannot
    promise a subcommand that is not there (or hide one that is)."""
    for verb, table in domain_console._SUBCOMMANDS.items():
        line = domain_console.usage(verb)
        for name in table:
            assert name in line, (verb, name)
        assert domain_console.DEFAULT_SUBCOMMAND[verb] in table


def test_both_verbs_have_a_default_and_it_is_real():
    assert set(domain_console.DEFAULT_SUBCOMMAND) == set(domain_console.COMMANDS)
    for verb, name in domain_console.DEFAULT_SUBCOMMAND.items():
        assert name in domain_console._SUBCOMMANDS[verb]


# --- dispatch ----------------------------------------------------------------

def _stub(monkeypatch, verb="crypto", name="status", fn=None):
    table = dict(domain_console._SUBCOMMANDS[verb])
    table[name] = fn if fn is not None else (lambda *, now, root: f"{verb} {name} 렌더")
    monkeypatch.setitem(domain_console._SUBCOMMANDS, verb, table)


def test_a_bare_verb_answers_its_default(monkeypatch):
    _stub(monkeypatch)
    outcome = apply_domain_command(("CRYPTO", None), operator_id="tg-12345", now=NOW)
    assert outcome["action"] == "CRYPTO_STATUS"
    assert outcome["reply"] == "crypto status 렌더"


def test_an_unknown_subcommand_is_refused_not_defaulted():
    """A typo that fell through to the default would answer a question he did not ask, with
    a board that looks like an answer."""
    with pytest.raises(OperatorBlocked) as exc:
        apply_domain_command(("CRYPTO", "statuss"), operator_id="tg-12345", now=NOW)
    assert exc.value.reason_code == "UNKNOWN_DOMAIN_SUBCOMMAND"
    assert "status" in exc.value.reason          # the usage line names what does exist


def test_an_unreadable_store_is_reported_never_an_empty_board(monkeypatch):
    """The registry-console rule, and it matters more here: a crypto board rendering as "no
    open positions" because a file would not parse is a false statement about money."""
    def _boom(*, now, root):
        raise MvpRuntimeError("PAPER_STORE_CORRUPT", "outcome row 3 is not JSON")

    _stub(monkeypatch, name="status", fn=_boom)
    with pytest.raises(OperatorBlocked) as exc:
        apply_domain_command(("CRYPTO", "status"), operator_id="tg-12345", now=NOW)
    assert exc.value.reason_code == "PAPER_STORE_CORRUPT"
    assert "확인할 수 없습니다" in exc.value.reason


def test_the_renderers_text_is_passed_through_untouched(monkeypatch):
    """No reflowing, no summarising: what the chat shows is what the host CLI prints, because
    two renderings of the same board are two accounts that can disagree."""
    board = "=== crypto dashboard === 2026-07-28\n\n지금   포지션 없음\n판단: 검토 가능"
    _stub(monkeypatch, fn=lambda *, now, root: board)
    assert apply_domain_command(("CRYPTO", "status"), operator_id="tg-12345", now=NOW)["reply"] == board


# --- on the channel ----------------------------------------------------------

def test_the_channel_answers_the_verb(monkeypatch):
    _stub(monkeypatch, verb="crypto", name="status", fn=lambda *, now, root: "크립토 보드")
    reply = handle_operator_message(_msg("/crypto"), registration=REG, now=NOW)
    assert reply.accepted and reply.status == "DOMAIN"
    assert reply.reason_code == "CRYPTO_STATUS"
    assert reply.text == "크립토 보드"


def test_the_removed_prediction_verb_is_not_a_command():
    """`/pred` answered here until 2026-08-02, when the lane was removed. It must now be
    refused like any unknown verb rather than parsed into a subcommand nothing serves —
    the console's own rule is that a leading slash matching no verb is refused, never run."""
    assert parse_domain_command("/pred") is None
    reply = handle_operator_message(_msg("/pred report"), registration=REG, now=NOW)
    assert not reply.accepted and reply.reason_code == "UNKNOWN_COMMAND"


def test_the_verbs_answer_while_the_runtime_is_halted(tmp_path, monkeypatch):
    """`kill_allows` covers read-only status, and an operator facing a KILLED runtime most
    needs to see what it was doing before it stopped."""
    from runtime.mvp_runtime import control
    from runtime.mvp_runtime.control import ControlStore

    store = ControlStore(tmp_path / "control")
    store.save(control.ControlState(mode=control.KILLED, updated_by="tg-12345",
                                    updated_at=NOW, reason="test"))
    _stub(monkeypatch, fn=lambda *, now, root: "보드")

    reply = handle_operator_message(_msg("/crypto status"), registration=REG,
                                    control_store=store, now=NOW)
    assert reply.accepted and reply.text == "보드"


def test_the_verbs_never_reach_the_pipeline(monkeypatch):
    """Like every `/` command: a domain verb must not spend a model call."""
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(operator_mod, "run_task",
                        lambda *a, **k: pytest.fail("a console verb must not run the pipeline"))
    _stub(monkeypatch, fn=lambda *, now, root: "보드")

    handle_operator_message(_msg("/crypto"), registration=REG, now=NOW)


def test_an_unverified_sender_gets_nothing():
    """The identity gate runs before any of this — the domain state is not public."""
    reply = handle_operator_message(
        InboundMessage(text="/crypto status", sender_id="tg-99999", chat_id="chat-777"),
        registration=REG, now=NOW,
    )
    assert not reply.accepted and reply.status == "REFUSED"


def test_the_unknown_command_help_lists_the_new_verbs():
    """A verb nobody can discover is a verb nobody uses — and one that no longer exists must
    not be advertised, or the help text sends the operator at a door that is gone."""
    reply = handle_operator_message(_msg("/nosuchverb"), registration=REG, now=NOW)
    assert reply.reason_code == "UNKNOWN_COMMAND"
    assert "/crypto" in reply.text and "/pred" not in reply.text


def test_the_verbs_write_no_ledger_event(monkeypatch, tmp_path):
    """The `/status` precedent: a read that appends to the log it reads races its own tail.

    Asserted by *counting*, not by looking for an empty directory — an assertion that passes
    when the ledger was never touched at all would also pass if the verb had never run."""
    from runtime.mvp_runtime.store import LedgerStore

    ledger = LedgerStore(tmp_path / "ledger")
    ledger.append_block(stamped_event("probe.v0", action="baseline", created_at=NOW))
    before = (len(ledger.read_blocks()), len(ledger.read_audit_events()))

    _stub(monkeypatch, fn=lambda *, now, root: "보드")
    reply = handle_operator_message(_msg("/crypto"), registration=REG, store=ledger, now=NOW)

    assert reply.accepted and reply.text == "보드"      # the verb really did answer
    assert (len(ledger.read_blocks()), len(ledger.read_audit_events())) == before


# --- the authority inventory -------------------------------------------------

def test_both_verbs_carry_a_named_authority():
    """The completeness gate in test_mvp_runtime_control asserts the inventory both ways;
    this pins what the domain verbs actually claim, since a read of live money state is
    where a vague authority would matter most."""
    for verb in domain_console.COMMANDS:
        clause = operator.CHANNEL_VERB_AUTHORITY[verb]
        assert "INTERNAL_READ" in clause
        assert "read_only_status" in clause
