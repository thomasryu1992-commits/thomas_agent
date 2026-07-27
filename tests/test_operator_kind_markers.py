"""§8.5 on the chat door — `!번역` / `!조사` routing markers, and the kind surviving the queue.

The CLI had `--kind`; Telegram did not, so the one door Thomas actually uses could not reach
the Roles activated on 2026-07-27. The load-bearing part is not the marker — it is that the
kind survives a **durable, unattended** queue: the drain runs minutes after submission, so a
kind that lived only in the operator's message would silently become an analysis.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import operator, task_registry
from runtime.mvp_runtime.errors import TaskRegistryBlocked
from runtime.mvp_runtime.operator import InboundMessage, OperatorIdentity
from runtime.mvp_runtime.planner import REQUEST_KIND_CAPABILITIES

NOW = "2026-07-27T09:00:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")


def _msg(text):
    return InboundMessage(text=text, sender_id="tg-12345", chat_id="chat-777",
                          chat_type="private", is_forwarded=False, channel="telegram_private")


# --- the marker table is honest about the runtime it routes into -------------

def test_every_operator_kind_marker_is_a_real_kind():
    """The marker names a KIND, and `planner` owns which kinds exist. A marker for a kind the
    planner does not know would be a refusal the operator only discovers by typing it."""
    for marker, kind in operator.KIND_MARKERS.items():
        assert kind in REQUEST_KIND_CAPABILITIES, f"{marker} -> {kind}"


def test_a_marker_never_names_a_role():
    """Same layering as the CLI: the Registry decides which Role covers a capability set."""
    assert not set(operator.KIND_MARKERS.values()) & {"general.specialist", "research.general",
                                                      "translation.general"}


def test_the_importance_marker_is_untouched():
    assert operator.IMPORTANT_MARKERS == ("!중요", "!important")


# --- the entry carries it ----------------------------------------------------

def _entry(**kwargs):
    return task_registry.build_entry(
        request_text="번역해줘", origin="TELEGRAM", requester_id="thomas", now=NOW, **kwargs)


def test_no_kind_is_the_behavior_the_registry_already_had():
    entry = _entry()
    assert entry.request_kind is None
    assert entry.as_record()["request_kind"] is None
    assert entry.as_record()["schema_version"] == "task_registry_entry.v0.2"


def test_a_kind_round_trips_through_the_record():
    entry = _entry(request_kind="translation")
    rebuilt = task_registry.RegistryEntry.from_record(entry.as_record())
    assert rebuilt.request_kind == "translation"


def test_a_v0_1_row_still_reads_as_the_routing_it_was_queued_with():
    """Rows already on disk carry no `request_kind`. Reading a missing one as None is not a
    default chosen for convenience — None IS the analysis routing those rows ran under."""
    record = _entry(request_kind="translation").as_record()
    del record["request_kind"]
    record["schema_version"] = "task_registry_entry.v0.1"

    rebuilt = task_registry.RegistryEntry.from_record(record)

    assert rebuilt.request_kind is None
    assert rebuilt.registry_entry_id == record["registry_entry_id"]


def test_an_unroutable_kind_is_refused_at_submission_not_at_drain():
    """A bad kind accepted into a durable queue surfaces minutes later as a blocked run with
    nobody watching, when the operator who typed it is long gone from the channel."""
    with pytest.raises(TaskRegistryBlocked) as exc:
        _entry(request_kind="summarise")
    assert exc.value.reason_code == "UNKNOWN_REQUEST_KIND"


def test_a_blank_kind_is_not_a_kind():
    assert _entry(request_kind="   ").request_kind is None
    assert _entry(request_kind=None).request_kind is None


# --- the parser --------------------------------------------------------------

def _parse(text):
    """Drive only the marker loop by calling the handler with nothing wired, and read back
    what it decided. Kept to the parse: the rest of `handle_operator_message` needs a channel,
    a registry and a provider, none of which this behavior depends on."""
    captured = {}

    def fake_enqueue(registry, *, request_text, origin, requester_id, now, flags, request_kind=None):
        captured.update(text=request_text, kind=request_kind, important=flags["important"])
        raise TaskRegistryBlocked("STOP", "captured")

    return captured, fake_enqueue


@pytest.mark.parametrize("message,expected_kind,expected_text", [
    ("!번역 이 문장을 영어로", "translation", "이 문장을 영어로"),
    ("!translate this sentence", "translation", "this sentence"),
    ("!조사 시장 규모를 알아봐", "research", "시장 규모를 알아봐"),
    ("!분석 이 아이디어", "analysis", "이 아이디어"),
    ("아무 표시 없는 요청", None, "아무 표시 없는 요청"),
])
def test_the_marker_is_stripped_and_the_kind_recorded(message, expected_kind, expected_text,
                                                      monkeypatch):
    captured, fake_enqueue = _parse(message)
    monkeypatch.setattr(task_registry, "enqueue", fake_enqueue)
    monkeypatch.setattr(operator.task_registry, "enqueue", fake_enqueue)

    reply = operator.handle_operator_message(
        _msg(message), registration=REG, registry=object(), now=NOW,
    )

    assert captured["kind"] == expected_kind
    assert captured["text"] == expected_text
    assert reply.accepted is False        # the fake enqueue refuses; the parse is what we read


def test_both_markers_compose_in_either_order(monkeypatch):
    """One parser for both, so "!중요 !번역 x" and "!번역 !중요 x" mean the same thing. Two
    parsers would inevitably guard one marker and not the other."""
    for message in ("!중요 !번역 이 문장", "!번역 !중요 이 문장"):
        captured, fake_enqueue = _parse(message)
        monkeypatch.setattr(operator.task_registry, "enqueue", fake_enqueue)
        operator.handle_operator_message(
            _msg(message), registration=REG, registry=object(), now=NOW,
        )
        assert (captured["kind"], captured["important"], captured["text"]) == \
            ("translation", True, "이 문장")


def test_a_marker_with_nothing_after_it_is_refused():
    reply = operator.handle_operator_message(
        _msg("!번역"), registration=REG, registry=object(), now=NOW,
    )
    assert (reply.accepted, reply.reason_code) == (False, "EMPTY_REQUEST")


def test_a_command_hidden_behind_a_kind_marker_is_still_refused():
    """The guard that exists because `!중요 /killl` once became a pipeline task and spent a
    model call on a typo'd emergency verb. It now lives inside the loop, so it covers every
    marker rather than only the first."""
    reply = operator.handle_operator_message(
        _msg("!번역 /killl"), registration=REG, registry=object(), now=NOW,
    )
    assert (reply.accepted, reply.reason_code) == (False, "MARKED_COMMAND")


def test_marker_like_prose_is_not_a_marker(monkeypatch):
    """"!번역기를 만들자" is a request about translators, not a routing flag."""
    captured, fake_enqueue = _parse("!번역기를 만들자")
    monkeypatch.setattr(operator.task_registry, "enqueue", fake_enqueue)
    operator.handle_operator_message(
        _msg("!번역기를 만들자"), registration=REG, registry=object(), now=NOW,
    )
    assert captured["kind"] is None
    assert captured["text"] == "!번역기를 만들자"
