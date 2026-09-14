"""stdio MCP shim — the Thomas runtime's knowledge base, exposed to Hermes (door API v2).

The fourth Thomas door, after ``switch_bridge_mcp.py`` (turns execution on and off),
``read_bridge_mcp.py`` (looks at it) and ``dispatch_bridge_mcp.py`` (starts bounded work).
This one *accumulates*: it files a document and later answers questions from what was filed.

Like its siblings it holds no authority. Everything is forwarded to a unix socket served by
``runtime/mvp_runtime/knowledge_bridge.py``, which caps what matters and cannot be widened
from here: three verbs and no more, no delete and no edit (they are not filtered out — they
do not exist), no argument that names a path on the runtime's filesystem, and the kill switch
checked before every write.

**What ``search_knowledge`` gives back, and why it is not a summary.** Ranked passages,
verbatim, each with its source and the terms it matched. The far side deliberately does not
compose an answer, because you are the model that should. Quote the passages, or say the
corpus does not cover it.

**Trust the ``reason`` field.** ``KNOWLEDGE_BASE_EMPTY`` means nothing has been filed at all;
``NO_RELEVANT_PASSAGE`` means the corpus has material but none on this. Do not collapse both
into "I couldn't find it", and never fill the gap from your own memory of a document you
filed earlier in the session.

v2 (2026-09-04): frames go through `thomas_door_client` (`proto: 2`, `client_id`); the
rendering below is unchanged.
"""

from __future__ import annotations

import base64

from mcp.server.fastmcp import FastMCP

import thomas_door_client as door

mcp = FastMCP("thomas-knowledge")

_DOOR = "knowledge"

# The door accepts a 12 MB frame. Refuse past that here rather than sending bytes the far side
# will drop mid-frame — a dropped frame reads as "the door is down", which is a worse diagnosis
# than "your document is too big".
_MAX_PAYLOAD_BYTES = 12 * 1024 * 1024


def _ask(payload: dict[str, object]) -> dict[str, object] | str:
    """The door's frame as a dict on success, else the sentence to show the model."""
    answer = door.ask(_DOOR, payload, max_frame_bytes=_MAX_PAYLOAD_BYTES)
    if answer.failure:
        return answer.failure_text()
    if not answer.ok:
        return answer.refused_text()
    return answer.frame or {}


@mcp.tool()
def file_document(
    title: str,
    source: str,
    text: str,
    document_date: str = "",
    tags: str = "",
) -> str:
    """File a text document into Thomas's knowledge base so it can be searched later.

    Use for a daily analysis, a research note, a briefing — anything Thomas should be able to
    ask about days later. ``source`` identifies what is being filed (a path, a report id, an
    analyst) and is the key supersession works on: filing the same source again with changed
    text replaces it and keeps the old version in the record, while filing identical text
    stores nothing and says so. ``document_date`` is what the document is ABOUT (YYYY-MM-DD),
    which is not the same as when it was filed. ``tags`` is comma-separated.

    For a PDF use ``file_pdf`` instead — do not paste extracted text you produced yourself.
    """
    payload: dict[str, object] = {
        "command": "add_document", "title": title, "source": source, "text": text,
    }
    if document_date.strip():
        payload["document_date"] = document_date.strip()
    if tags.strip():
        payload["tags"] = [t.strip() for t in tags.split(",") if t.strip()]

    answer = _ask(payload)
    if isinstance(answer, str):
        return answer
    return _render_filed(answer)


@mcp.tool()
def file_pdf(
    title: str,
    source: str,
    pdf_path: str,
    document_date: str = "",
    tags: str = "",
) -> str:
    """File a PDF into Thomas's knowledge base; the runtime extracts the text itself.

    ``pdf_path`` is a path **in this container** — the file is read here and sent as content.
    The runtime never receives a path and cannot open one; that is deliberate, so this door
    grants no ability to read Thomas's filesystem.

    Extraction is pdftotext then pypdf, and it refuses rather than storing anything doubtful:
    a scanned/image-only PDF comes back as PDF_NO_TEXT_LAYER (OCR is out of scope), an
    encrypted one as PDF_ENCRYPTED. Report the refusal — do not work around it by pasting text
    you extracted some other way, because then the corpus holds a document nobody can trace.
    """
    try:
        with open(pdf_path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return f"UNAVAILABLE: could not read {pdf_path} ({exc})."
    if not data:
        return f"REFUSED: {pdf_path} is empty."

    payload: dict[str, object] = {
        "command": "add_document", "title": title, "source": source,
        "pdf_base64": base64.b64encode(data).decode("ascii"),
    }
    if document_date.strip():
        payload["document_date"] = document_date.strip()
    if tags.strip():
        payload["tags"] = [t.strip() for t in tags.split(",") if t.strip()]

    answer = _ask(payload)
    if isinstance(answer, str):
        return answer
    return _render_filed(answer)


@mcp.tool()
def search_knowledge(question: str, limit: str = "") -> str:
    """Search Thomas's knowledge base and return the passages that answer the question.

    Use whenever Thomas asks something that a filed document could answer — "what did the
    08-05 analysis say about funding", "have we written anything on ETF flows". Returns
    verbatim passages with their sources, not a summary: read them and answer from them,
    quoting what supports the claim.

    ``limit`` is an optional count (default 5, max 20). Retrieval is lexical, so it matches
    the *words* of the corpus — a Korean corpus does not answer an English question, and a
    synonym is not a match. If nothing comes back, rephrase using the terms the documents
    would themselves use before concluding the corpus is silent.
    """
    payload: dict[str, object] = {"command": "query", "question": question}
    if limit.strip():
        try:
            payload["limit"] = int(limit.strip())
        except ValueError:
            return f"REFUSED: limit must be a whole number, got {limit!r}."

    answer = _ask(payload)
    if isinstance(answer, str):
        return answer

    reason = answer.get("reason")
    if reason == "KNOWLEDGE_BASE_EMPTY":
        return (
            "EMPTY: nothing has been filed into the knowledge base at all. This is not "
            "'no material on this topic' — say the base is empty."
        )
    if reason == "NO_RELEVANT_PASSAGE":
        stats = answer.get("stats", {})
        return (
            f"NO MATCH: the knowledge base holds {stats.get('documents')} document(s) but "
            f"none matched {question!r}. Do not answer from memory."
        )

    lines = [f"ANSWER (extractive, verbatim from the corpus):\n{answer.get('answer')}\n",
             "PASSAGES:"]
    for hit in answer.get("hits", []):
        lines.append(
            f"\n[{hit.get('source')}] {hit.get('title')}"
            f" · date={hit.get('document_date') or 'n/a'}"
            f" · filed={hit.get('ingested_at_utc')}"
            f" · score={hit.get('score')} coverage={hit.get('coverage')}\n"
            f"{hit.get('text')}"
        )
    stats = answer.get("stats", {})
    lines.append(f"\n(searched {stats.get('documents')} documents, {stats.get('chars')} chars)")
    return "\n".join(lines)


@mcp.tool()
def knowledge_stats() -> str:
    """How much is in Thomas's knowledge base: document count, total characters, date range.

    Use before claiming the base does or does not hold something, and after filing, to confirm
    the count actually moved rather than assuming it did.
    """
    answer = _ask({"command": "stats"})
    if isinstance(answer, str):
        return answer
    stats = answer.get("stats", {})
    return (
        f"documents={stats.get('documents')} sources={stats.get('sources')} "
        f"chars={stats.get('chars')} "
        f"oldest={stats.get('oldest_ingested_at_utc')} newest={stats.get('newest_ingested_at_utc')}"
    )


def _render_filed(answer: dict[str, object]) -> str:
    if answer.get("duplicate"):
        return (
            f"ALREADY FILED (nothing stored): {answer.get('document_id')} — identical content "
            f"from the same source is the same document. The corpus did not grow: "
            f"{answer.get('stats')}"
        )
    extraction = answer.get("extraction")
    detail = f" via {extraction['backend']}" if isinstance(extraction, dict) else ""
    pages = answer.get("page_count")
    return (
        f"FILED {answer.get('document_id')}{detail}: {answer.get('title')} "
        f"[{answer.get('source')}] {answer.get('chars')} chars"
        + (f", {pages} page(s)" if pages else "")
        + f", tags={answer.get('tags')} at {answer.get('ingested_at_utc')}. "
        f"Corpus now: {answer.get('stats')}"
    )


if __name__ == "__main__":
    mcp.run()
