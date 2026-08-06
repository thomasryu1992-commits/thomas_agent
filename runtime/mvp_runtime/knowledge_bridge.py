"""The knowledge door — the assistant files a document, and asks the corpus what it holds.

The fourth door, and the first that *accumulates*. ``switch_bridge`` stops and starts the
runtime, ``read_bridge`` renders what it is doing, ``dispatch_bridge`` starts bounded P3 work
that ends when it ends. This one takes something in and keeps it, which is a different
question about authority and gets a different answer per verb.

**What authority an ingest carries.** Under the canonical policy, ``memory_learning``'s
``working_memory_and_candidate_creation`` is ALLOW, and a stored document is *less* than a
candidate: a candidate is a claim the runtime proposes about how to work, on a ladder with a
promotion door Thomas controls. A document asserts nothing. It records that this text was
supplied, from this source, at this time — the same effect class as the operator pasting it
into a file on the host, which is the authentication this whole socket rests on. It invokes
no model, reaches no network, creates no permission, and cannot reach the money path: there
is no verb here that starts work, and nothing downstream reads this store to decide anything.
So an ingest needs no approval — and, being a write, it *does* stop at the kill switch.

**Which is the split this door's verb table encodes.** ``query`` and ``stats`` are reads and
keep answering while the runtime is PAUSED or KILLED, for the reason ``read_bridge`` gives:
``kill_allows: [read_only_status, audit_read]``, and an incident is when the record is most
worth reading. ``add_document`` is a write and refuses, because a halt that leaves a side
door open for state changes is not a halt. A test asserts each verb stays on its own side.

**Why the frame ceiling is larger here.** Every other door carries console verbs — tens of
bytes. This one carries a document, so it states its own ceiling (:data:`MAX_FRAME_BYTES`)
rather than widening the shared default for the doors that stop the runtime. Same reasoning
for the read deadline: ``pdftotext`` on a large report takes longer than a console verb ever
should, and the two limits should not be forced to agree just because they live in one
transport.

**Idempotency without a ledger claim.** The other write-ish door (``dispatch``) needs
``bridge_idempotency`` because starting work twice does the work twice. An ingest does not:
the document id is derived from the content hash and the source, so a retry after a client
timeout finds the row already there, stores nothing, and returns the same id with
``duplicate: true``. At-most-once falls out of the record's identity instead of being bolted
on beside it — and the caller is told, rather than left to infer the corpus grew.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import socket_door
from .control import ControlStore
from .errors import ControlBlocked, KnowledgeBlocked
from .knowledge import service
from .knowledge.store import KnowledgeStore
from .socket_door import ASSISTANT_ACTOR

SOCKET_REL = ".runtime_governance_state/bridge/knowledge.sock"
SOCKET_ENV = "MVP_KNOWLEDGE_BRIDGE_SOCKET"

ADD_DOCUMENT = "add_document"
QUERY = "query"
STATS = "stats"

# THE permission surface. Read it as the answer to "what can the assistant do to the corpus":
# two reads and one append. There is no delete, no edit, and no verb that reads a path off the
# runtime's filesystem — a document arrives as content in the frame or it does not arrive.
# Supersession happens as a side effect of re-filing the same source, never as a verb, so
# there is no request this door can build that removes something on purpose.
_COMMANDS: frozenset[str] = frozenset({ADD_DOCUMENT, QUERY, STATS})

# The verbs that survive a halt. Reads only — see the module docstring.
_READ_COMMANDS: frozenset[str] = frozenset({QUERY, STATS})

# Per-verb request surface. Anything not named is refused rather than ignored: a caller that
# passed a key believed it would be used, and silently dropping it is the quiet mismatch a
# later reader misreads as a wrong answer.
_ALLOWED_KEYS: dict[str, frozenset[str]] = {
    ADD_DOCUMENT: frozenset({
        "command", "title", "source", "text", "pdf_base64", "source_type",
        "document_date", "data_sensitivity", "tags",
    }),
    QUERY: frozenset({"command", "question", "limit"}),
    STATS: frozenset({"command"}),
}

# A document, not a console verb. 12 MB of frame holds a ~9 MB PDF once base64 has added its
# third — comfortably past any daily analysis, and still a bound.
MAX_FRAME_BYTES = 12 * 1024 * 1024

# Long enough for a large PDF to arrive and be decoded by poppler (whose own ceiling is
# 120s), short enough that a stalled peer still releases its slot.
REQUEST_TIMEOUT_SECONDS = 180.0

# The narrowest ceiling of the four doors. An ingest is the most expensive request the
# deployment serves — a subprocess, a full corpus re-index — and unlike a read there is no
# version of this traffic that is legitimately concurrent: documents are filed one at a time
# by one orchestrator.
MAX_CONCURRENT_REQUESTS = 2


def socket_path(root: Path | None = None) -> Path:
    """The socket path, overridable per-deployment via ``MVP_KNOWLEDGE_BRIDGE_SOCKET``."""
    return socket_door.resolve_socket_path(SOCKET_ENV, SOCKET_REL, root)


def apply_knowledge(
    request: Any,
    *,
    store: KnowledgeStore,
    control_store: ControlStore | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate one request and serve it, or raise a typed error.

    Pure with respect to the transport — an already-decoded object in, a reply out — which is
    what makes the permission surface testable without a listener.
    """
    if not isinstance(request, dict):
        raise ControlBlocked("MALFORMED_REQUEST", "request must be a JSON object")

    command = request.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ControlBlocked("MALFORMED_REQUEST", "request needs a non-empty 'command'")
    command = command.strip().lower()

    if command not in _COMMANDS:
        raise ControlBlocked(
            "VERB_NOT_PERMITTED",
            f"{command!r} is not a verb this door serves; it carries {sorted(_COMMANDS)}",
        )

    unexpected = set(request) - _ALLOWED_KEYS[command]
    if unexpected:
        raise ControlBlocked(
            "ARGUMENT_NOT_ACCEPTED",
            f"{command!r} accepts only {sorted(_ALLOWED_KEYS[command])}; "
            f"it will not act on {sorted(unexpected)}",
        )

    # Kill-switch binding, on the writes only. `store.add` does not read control state — this
    # is its door, and this is where the halt is enforced.
    if command not in _READ_COMMANDS and control_store is not None:
        state = control_store.load()
        if not state.execution_allowed:
            raise ControlBlocked(
                state.refusal_reason_code(),
                f"runtime is {state.mode}; the knowledge base accepts no new documents until "
                f"an authenticated resume. Reads ({sorted(_READ_COMMANDS)}) still answer",
            )

    if command == STATS:
        return {"ok": True, "command": command, "stats": store.stats()}

    if command == QUERY:
        result = service.query(
            store=store,
            question=request.get("question"),
            limit=request.get("limit"),
        )
        return {"ok": True, "command": command, **result}

    result = service.add_document(
        store=store,
        title=request.get("title"),
        source=request.get("source"),
        text=request.get("text"),
        pdf_base64=request.get("pdf_base64"),
        source_type=request.get("source_type"),
        # Honestly the assistant, never Thomas — the same attribution rule the other doors
        # follow. A document filed through this socket is attributable to the thing that
        # filed it, and no request field can override that.
        ingested_by=ASSISTANT_ACTOR,
        now=now,
        document_date=request.get("document_date"),
        data_sensitivity=request.get("data_sensitivity") or "INTERNAL",
        tags=request.get("tags"),
    )
    return {"ok": True, "command": command, **result}


def open_door(
    path: Path,
    *,
    store: KnowledgeStore,
    control_store: ControlStore | None = None,
) -> socket_door.SocketDoor:
    """Listen on ``path`` and serve the knowledge verbs from it.

    Framing, deadline, size cap, peer check, concurrency ceiling and error envelope come from
    ``socket_door``, shared with the other doors so a malformed frame cannot be answered two
    different ways. What this door supplies is its verb table and its three limits.
    """
    return socket_door.SocketDoor(
        path,
        lambda request: apply_knowledge(request, store=store, control_store=control_store),
        max_concurrent_requests=MAX_CONCURRENT_REQUESTS,
        max_frame_bytes=MAX_FRAME_BYTES,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    )


__all__ = [
    "ADD_DOCUMENT",
    "KnowledgeBlocked",
    "MAX_CONCURRENT_REQUESTS",
    "MAX_FRAME_BYTES",
    "QUERY",
    "STATS",
    "apply_knowledge",
    "open_door",
    "socket_path",
]
