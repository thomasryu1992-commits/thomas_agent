"""The knowledge door — what the assistant can do to the corpus, and what a halt still allows.

Three claims, in descending order of what they are worth:

1. The verb table has no way to remove or edit a document. Not filtered out — never named.
2. The write stops at the kill switch and the reads do not. A halt that leaves a side door
   open for state changes is not a halt; a halt that blinds the operator to the record is not
   a safety property either.
3. A document-sized frame gets through this door and *only* this door, so raising a ceiling
   for a corpus cannot widen the one on the door that stops the runtime.

None of these need a local Core — this door binds no task and runs no pipeline.
"""

from __future__ import annotations

import base64
import json
import socket
import threading

import pytest

from runtime.mvp_runtime import control, knowledge_bridge, socket_door
from runtime.mvp_runtime.control import ACTIVE, KILLED, PAUSED, ControlStore
from runtime.mvp_runtime.errors import ControlBlocked, KnowledgeBlocked
from runtime.mvp_runtime.knowledge.store import KnowledgeStore

NOW = "2026-08-06T09:00:00Z"

DOCUMENT = (
    "비트코인은 8월 5일 기준 62,400달러 부근에서 거래되고 있다. 온체인 지표상 장기 보유자의 "
    "매도 압력이 완화되었다.\n\n"
    "펀딩비는 중립 구간으로 회귀했고, 미결제약정은 전주 대비 12% 감소했다. 단기적으로는 "
    "61,000달러 지지선이 핵심이다."
)

unix_only = pytest.mark.skipif(
    not socket_door.UNIX_SOCKETS_AVAILABLE, reason="the knowledge door listens on AF_UNIX"
)


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(tmp_path / "knowledge")


@pytest.fixture()
def control_store(tmp_path):
    return ControlStore(tmp_path)


def _apply(request, store, control_store=None):
    return knowledge_bridge.apply_knowledge(
        request, store=store, control_store=control_store, now=NOW,
    )


def _file(store, control_store=None, **overrides):
    request = {
        "command": "add_document",
        "title": "BTC 일일 분석 2026-08-05",
        "source": "daily/btc-2026-08-05.md",
        "text": DOCUMENT,
    }
    request.update(overrides)
    return _apply(request, store, control_store)


# --- what cannot be reached ---------------------------------------------------

def test_the_verb_table_carries_no_way_to_remove_or_edit(store):
    """Structural, and the reason it is structural: filtering a delete verb out later is a
    weaker guarantee than never having one. Supersession happens as a side effect of
    re-filing a source, never as a verb a caller can aim."""
    assert knowledge_bridge._COMMANDS == {"add_document", "query", "stats"}
    for verb in ("delete", "remove", "edit", "update", "forget", "purge", "clear"):
        with pytest.raises(ControlBlocked) as caught:
            _apply({"command": verb}, store)
        assert caught.value.reason_code == "VERB_NOT_PERMITTED"


def test_no_verb_reads_a_path_off_the_runtime_filesystem(store):
    """A document arrives as content in the frame or it does not arrive. A path argument
    would hand the assistant a filesystem read primitive it does not otherwise hold, as a
    side effect of wanting to read PDFs."""
    for key in ("path", "file", "filename", "file_path", "url"):
        for verb, allowed in knowledge_bridge._ALLOWED_KEYS.items():
            assert key not in allowed, f"{verb} must not accept {key}"


@pytest.mark.parametrize("request_obj", [
    None, [], "add_document", {}, {"command": ""}, {"command": 5},
])
def test_malformed_requests_are_refused(store, request_obj):
    with pytest.raises(ControlBlocked) as caught:
        _apply(request_obj, store)
    assert caught.value.reason_code == "MALFORMED_REQUEST"


def test_an_unnamed_key_is_refused_rather_than_ignored(store):
    """A caller that passed a key believed it would be used; dropping it silently is the
    quiet mismatch a later reader misreads as a wrong answer."""
    with pytest.raises(ControlBlocked) as caught:
        _file(store, ingested_by="thomas")
    assert caught.value.reason_code == "ARGUMENT_NOT_ACCEPTED"
    assert "ingested_by" in str(caught.value)


def test_attribution_cannot_be_overridden_by_the_request(store):
    """Honestly the assistant, never Thomas — the rule every door follows."""
    _file(store)
    assert store.read_all()[0]["ingested_by"] == socket_door.ASSISTANT_ACTOR


@pytest.mark.parametrize("verb,key", [("query", "title"), ("stats", "question")])
def test_a_key_belonging_to_another_verb_is_refused(store, verb, key):
    with pytest.raises(ControlBlocked) as caught:
        _apply({"command": verb, key: "x"}, store)
    assert caught.value.reason_code == "ARGUMENT_NOT_ACCEPTED"


# --- the halt split -----------------------------------------------------------

@pytest.mark.parametrize("halt", [control.CMD_KILL, control.CMD_PAUSE])
def test_an_ingest_is_refused_while_the_runtime_is_halted(store, control_store, halt):
    """A write is a write. A halt that leaves a side door open for state changes is not one."""
    control.apply_command(control_store, halt, actor="test", now=NOW)
    assert control_store.load().mode in (KILLED, PAUSED)

    with pytest.raises(ControlBlocked) as caught:
        _file(store, control_store)
    assert caught.value.reason_code == control_store.load().refusal_reason_code()
    assert store.stats()["documents"] == 0


@pytest.mark.parametrize("verb", sorted(knowledge_bridge._READ_COMMANDS))
def test_reads_keep_answering_while_the_runtime_is_halted(store, control_store, verb):
    """`kill_allows: [read_only_status, audit_read]` — an incident is when the record is most
    worth reading, so a halt must not blind the assistant to what it already filed."""
    _file(store, control_store)
    control.apply_command(control_store, control.CMD_KILL, actor="test", now=NOW)
    assert control_store.load().mode == KILLED

    request = {"command": verb}
    if verb == "query":
        request["question"] = "펀딩비 상황은?"
    out = _apply(request, store, control_store)
    assert out["ok"] is True


def test_every_verb_is_on_exactly_one_side_of_the_halt():
    """The split is a table, not a chain of ifs, so it can be asserted rather than reviewed."""
    assert knowledge_bridge._READ_COMMANDS < knowledge_bridge._COMMANDS
    writes = knowledge_bridge._COMMANDS - knowledge_bridge._READ_COMMANDS
    assert writes == {"add_document"}


# --- the verbs themselves -----------------------------------------------------

def test_an_ingest_stores_the_document_and_reports_the_corpus(store, control_store):
    out = _file(store, control_store, document_date="2026-08-05", tags=["btc", "daily"])
    assert out["ok"] is True
    assert out["document_id"].startswith("kdoc_")
    assert out["duplicate"] is False
    assert out["stats"]["documents"] == 1
    assert out["tags"] == ["btc", "daily"]


def test_a_retry_of_the_same_ingest_stores_nothing_and_says_so(store, control_store):
    """At-most-once without a ledger claim: the id is derived from content and source, so a
    retry after a client timeout finds the row rather than appending a second one."""
    first = _file(store, control_store)
    second = _file(store, control_store)
    assert second["duplicate"] is True
    assert second["document_id"] == first["document_id"]
    assert store.stats()["documents"] == 1


def test_a_query_answers_from_what_was_filed(store, control_store):
    _file(store, control_store)
    out = _apply({"command": "query", "question": "지지선은 어디인가"}, store, control_store)
    assert out["ok"] is True
    assert out["hits"]
    assert "지지선" in out["answer"]
    assert out["hits"][0]["source"] == "daily/btc-2026-08-05.md"


def test_stats_answers_on_an_empty_corpus(store, control_store):
    out = _apply({"command": "stats"}, store, control_store)
    assert out["ok"] is True
    assert out["stats"]["documents"] == 0


def test_a_pdf_arrives_as_content_and_is_extracted(store, control_store):
    from tests.test_mvp_runtime_knowledge_pdf_text import build_pdf

    pdf = build_pdf([
        "Bitcoin daily analysis 2026-08-05.",
        "Funding rate returned to neutral territory.",
        "Open interest fell twelve percent week over week.",
    ])
    out = _apply(
        {
            "command": "add_document",
            "title": "BTC daily 2026-08-05",
            "source": "daily/btc-2026-08-05.pdf",
            "pdf_base64": base64.b64encode(pdf).decode("ascii"),
        },
        store, control_store,
    )
    assert out["ok"] is True
    assert out["source_type"] == "pdf"
    assert out["extraction"]["backend"] in ("pdftotext", "pypdf")
    assert out["page_count"] >= 1
    assert "Funding rate" in store.read_all()[0]["text"]


def test_an_unreadable_pdf_stores_nothing(store, control_store):
    """Fail-closed in the direction of storing nothing: a document admitted as garbage is a
    wrong answer with a citation attached, arriving weeks later."""
    with pytest.raises(KnowledgeBlocked) as caught:
        _apply(
            {
                "command": "add_document", "title": "t", "source": "s.pdf",
                "pdf_base64": base64.b64encode(b"not a pdf").decode("ascii"),
            },
            store, control_store,
        )
    assert caught.value.reason_code == "PDF_NOT_A_PDF"
    assert store.stats()["documents"] == 0


def test_invalid_base64_is_named_rather_than_decoded_into_a_shorter_document(store):
    with pytest.raises(KnowledgeBlocked) as caught:
        _apply(
            {"command": "add_document", "title": "t", "source": "s.pdf", "pdf_base64": "!!!!"},
            store,
        )
    assert caught.value.reason_code == "PDF_BASE64_INVALID"


# --- the transport ------------------------------------------------------------

def test_this_door_carries_a_document_sized_frame_and_the_others_do_not():
    """Per-door, so a ceiling raised for a corpus cannot widen the one on the door that stops
    the runtime."""
    from runtime.mvp_runtime import dispatch_bridge, read_bridge, switch_bridge

    assert knowledge_bridge.MAX_FRAME_BYTES > socket_door.MAX_FRAME_BYTES * 100
    for module in (read_bridge, switch_bridge, dispatch_bridge):
        assert not hasattr(module, "MAX_FRAME_BYTES"), (
            f"{module.__name__} states no frame ceiling and must keep the shared default"
        )


@unix_only
def test_the_four_doors_and_the_worker_do_not_share_a_socket_path(tmp_path):
    from runtime.mvp_runtime import dispatch_bridge, pipeline_worker, read_bridge, switch_bridge

    modules = (read_bridge, switch_bridge, dispatch_bridge, knowledge_bridge, pipeline_worker)
    assert len({m.SOCKET_REL for m in modules}) == 5
    assert len({m.socket_path(tmp_path) for m in modules}) == 5


@unix_only
def test_the_default_frame_ceiling_is_unchanged_for_the_console_doors(tmp_path):
    """The shared transport gained a per-door limit; the doors that did not ask for one must
    behave exactly as before."""
    from runtime.mvp_runtime import read_bridge

    class _Ledger:
        def append_control(self, entry): ...
        def append_block(self, entry): ...
        def last_audit_hash(self): return "sha256:" + "ab" * 32

    server = read_bridge.open_door(
        tmp_path / "r.sock", control_store=ControlStore(tmp_path), ledger=_Ledger(),
    )
    try:
        assert server.max_frame_bytes == socket_door.MAX_FRAME_BYTES
        assert server.request_timeout_seconds == socket_door.REQUEST_TIMEOUT_SECONDS
    finally:
        server.server_close()


@unix_only
def test_end_to_end_over_the_socket(tmp_path, store):
    sock = tmp_path / "k.sock"
    control_store = ControlStore(tmp_path)
    server = knowledge_bridge.open_door(sock, store=store, control_store=control_store)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        filed = _ask(sock, {
            "command": "add_document",
            "title": "BTC 일일 분석 2026-08-05",
            "source": "daily/btc-2026-08-05.md",
            "text": DOCUMENT,
        })
        assert filed["ok"] is True and filed["stats"]["documents"] == 1

        answered = _ask(sock, {"command": "query", "question": "펀딩비 상황은?"})
        assert answered["ok"] is True
        assert "펀딩비" in answered["answer"]

        refused = _ask(sock, {"command": "delete", "document_id": filed["document_id"]})
        assert refused["ok"] is False
        assert refused["reason_code"] == "VERB_NOT_PERMITTED"
        assert store.stats()["documents"] == 1
    finally:
        server.shutdown()
        server.server_close()


@unix_only
def test_a_document_larger_than_the_shared_ceiling_gets_through(tmp_path, store):
    """The reason the ceiling moved at all: a daily analysis is routinely past 8 KB, and a
    door that silently drops the frame is indistinguishable from a door that is down."""
    sock = tmp_path / "k.sock"
    server = knowledge_bridge.open_door(sock, store=store, control_store=ControlStore(tmp_path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        big = "\n\n".join(f"{i}번 문단. {DOCUMENT}" for i in range(200))
        assert len(big.encode("utf-8")) > socket_door.MAX_FRAME_BYTES * 4
        out = _ask(sock, {
            "command": "add_document", "title": "긴 분석", "source": "daily/long.md", "text": big,
        })
        assert out["ok"] is True
        assert out["chars"] == len(big)
    finally:
        server.shutdown()
        server.server_close()


def _ask(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(20)
        client.connect(str(path))
        client.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        chunks = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        return json.loads(b"".join(chunks).decode("utf-8").strip())


# --- the frame envelope (door API v2) ------------------------------------------

def test_stats_echoes_the_envelope_and_repeats_its_result_as_data(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge")
    out = _apply({"command": "stats", "proto": 2, "client_id": "hermes:dm"}, store)
    assert out["ok"] is True and out["proto"] == 2 and out["client_id"] == "hermes:dm"
    assert out["data"] == {"stats": out["stats"]}


def test_every_verb_accepts_the_envelope_and_still_refuses_a_stray_key(tmp_path):
    for verb, allowed in knowledge_bridge._ALLOWED_KEYS.items():
        assert socket_door.ENVELOPE_KEYS <= allowed, verb
    store = KnowledgeStore(tmp_path / "knowledge")
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "stats", "proto": 2, "protocol": 2}, store)
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "stats", "proto": 5}, store)
    assert exc.value.reason_code == "PROTO_UNSUPPORTED"
