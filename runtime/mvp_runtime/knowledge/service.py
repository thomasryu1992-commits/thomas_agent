"""The two verbs: ``add_document`` and ``query``.

Pure with respect to transport — decoded arguments in, a reply dict out — so the permission
surface is testable without a listener, exactly as ``read_bridge.apply_read`` is.

**``query`` returns evidence, not prose.** The obvious reading of "answer the question" is
"call a model with the retrieved passages". This does not, and the reason is the front desk's
own rule, already quoted in ``read_bridge``: *deterministic data beats model narration*. The
caller on the other side of this door is itself a capable model. Handing it ranked, cited,
verbatim passages lets it compose an answer it can support; handing it a paraphrase produced
here would mean two models in series, the first one's compression invisible to the second,
and a citation pointing at text nobody in the chain has actually read. It would also drag
``model_invocation``, a provider chain, a budget and a failover policy into a door whose
entire job is lookup.

What it does supply is the deterministic half of an answer: the passages that matched, why
each matched, which document each came from, and — in ``answer`` — an extractive summary
built by selecting sentences already present in the corpus. No sentence in that summary is
one this runtime wrote. If model-composed prose is wanted later, ``dispatch_bridge`` already
runs the governed pipeline and is the door that carries that authority.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Mapping

from ..errors import KnowledgeBlocked
from . import pdf_text
from .index import Hit
from .store import KnowledgeStore, build_document

# Retrieval ceilings. `limit` is what the caller asks for; the cap is what a door will serve.
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
MAX_QUESTION_CHARS = 2000

# The extractive answer's size. Small on purpose: it is a lede for the passages beneath it,
# not a replacement for reading them.
ANSWER_SENTENCES = 4
ANSWER_MAX_CHARS = 1200

# The relevance floor, and the one number here worth arguing about. BM25 will happily return
# a chunk sharing a single common bigram with the question, and returning it is how a
# knowledge base starts answering questions it holds no material on — the failure that
# matters most, because it is indistinguishable from a real answer at the point of reading.
#
# It is a *coverage* floor, not a score floor. See `index.Hit.coverage`: BM25's scale is set
# by corpus IDF, so any absolute score threshold rejects everything on a small corpus and
# nothing on a large one. This was not theoretical — at one document, a correct match on
# "펀딩비" scored 0.29 against a 0.35 floor and the query answered "nothing relevant" about a
# document that discussed it in as many words.
MIN_COVERAGE = 0.2
# Having cleared the floor, drop the tail: a hit worth less than this fraction of the best
# one is padding, and padding in an evidence list is read as corroboration.
RELATIVE_SCORE_FLOOR = 0.15
# Retrieve wider than the caller asked, then filter, then truncate — otherwise a low-coverage
# chunk that outranks a good one on raw score consumes a slot before the filter can see it.
_OVERSCAN = 4

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。？！])\s+|\n+")


def add_document(
    *,
    store: KnowledgeStore,
    title: str,
    source: str,
    text: str | None = None,
    pdf_base64: str | None = None,
    source_type: str | None = None,
    ingested_by: str,
    now: str | None = None,
    document_date: str | None = None,
    data_sensitivity: str = "INTERNAL",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Ingest one document — either supplied text or a base64 PDF — and store it.

    Exactly one of ``text`` / ``pdf_base64``. Not "prefer one": a call carrying both is a
    caller who believes something about precedence, and whichever this module picked would be
    wrong for half of them.
    """
    if (text is None) == (pdf_base64 is None):
        raise KnowledgeBlocked(
            "KNOWLEDGE_CONTENT_AMBIGUOUS",
            "supply exactly one of 'text' or 'pdf_base64' — a call with both states a "
            "precedence this door does not have, and a call with neither states nothing",
        )

    page_count: int | None = None
    extraction: dict[str, Any] | None = None

    if pdf_base64 is not None:
        data = _decode_pdf(pdf_base64)
        result = pdf_text.extract_pdf_text(data, source_name=source)
        text = result.text
        page_count = result.page_count
        extraction = {"backend": result.backend, "attempted": list(result.attempted)}
        resolved_type = "pdf"
    else:
        resolved_type = source_type or "text"

    record = build_document(
        title=title,
        source=source,
        text=text or "",
        source_type=resolved_type,
        ingested_by=ingested_by,
        now=now,
        document_date=document_date,
        data_sensitivity=data_sensitivity,
        tags=tags,
        page_count=page_count,
        extraction=extraction,
    )
    stored = store.add(record)

    duplicate_of = stored.pop("_duplicate_of", None)
    return {
        "document_id": stored["document_id"],
        "title": stored["title"],
        "source": stored["source"],
        "source_type": stored["source_type"],
        "chars": stored["chars"],
        "page_count": stored["page_count"],
        "ingested_at_utc": stored["ingested_at_utc"],
        "extraction": stored["extraction"],
        "tags": stored["tags"],
        # Reported rather than hidden. A caller re-filing yesterday's analysis needs to know
        # its call stored nothing new, or it will believe the corpus grew when it did not.
        "duplicate": duplicate_of is not None,
        "stats": store.stats(),
    }


def query(
    *,
    store: KnowledgeStore,
    question: str,
    limit: int | None = None,
) -> dict[str, Any]:
    """Search the corpus and return ranked passages plus an extractive answer."""
    if not isinstance(question, str) or not question.strip():
        raise KnowledgeBlocked("KNOWLEDGE_QUESTION_REQUIRED", "a query needs a non-empty question")
    question = question.strip()
    if len(question) > MAX_QUESTION_CHARS:
        raise KnowledgeBlocked(
            "KNOWLEDGE_QUESTION_TOO_LONG",
            f"question is {len(question)} characters, over the {MAX_QUESTION_CHARS} ceiling",
        )

    resolved_limit = _resolve_limit(limit)
    stats = store.stats()
    if not stats["documents"]:
        # An empty corpus is not a failed search, and answering it as one ("no results")
        # invites the caller to conclude the knowledge base has nothing *on this topic*.
        return {
            "question": question,
            "answer": None,
            "reason": "KNOWLEDGE_BASE_EMPTY",
            "hits": [],
            "stats": stats,
        }

    candidates = store.retriever().search(question, limit=resolved_limit * _OVERSCAN)
    relevant = [hit for hit in candidates if hit.coverage >= MIN_COVERAGE]
    if relevant:
        cutoff = relevant[0].score * RELATIVE_SCORE_FLOOR
        relevant = [hit for hit in relevant if hit.score >= cutoff]
    hits = relevant[:resolved_limit]
    if not hits:
        return {
            "question": question,
            "answer": None,
            "reason": "NO_RELEVANT_PASSAGE",
            "hits": [],
            "stats": stats,
        }

    return {
        "question": question,
        "answer": _extractive_answer(question, hits),
        "reason": None,
        "hits": [_render_hit(hit) for hit in hits],
        "stats": stats,
    }


def _resolve_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise KnowledgeBlocked("KNOWLEDGE_LIMIT_INVALID", "limit must be a positive integer")
    if limit > MAX_LIMIT:
        raise KnowledgeBlocked(
            "KNOWLEDGE_LIMIT_INVALID", f"limit exceeds the {MAX_LIMIT} ceiling this door serves",
        )
    return limit


def _decode_pdf(payload: str) -> bytes:
    # Its own code, not pdf_text's PDF_INPUT_EMPTY. That one means "the document decoded to
    # zero bytes"; this means "the field arrived empty" — a caller error one layer earlier,
    # and an operator reading the code back cannot tell two causes apart from one name.
    if not isinstance(payload, str) or not payload.strip():
        raise KnowledgeBlocked("PDF_BASE64_REQUIRED", "pdf_base64 must be a non-empty string")
    try:
        # validate=True so a payload with stray characters is a refusal rather than silently
        # decoding to a shorter, corrupt document that then fails as "not a PDF".
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise KnowledgeBlocked(
            "PDF_BASE64_INVALID", f"pdf_base64 is not valid base64: {exc}",
        ) from exc


def _render_hit(hit: Hit) -> dict[str, Any]:
    metadata: Mapping[str, Any] = hit.passage.metadata
    return {
        "document_id": hit.passage.document_id,
        "chunk_index": hit.passage.chunk_index,
        "score": hit.score,
        "coverage": hit.coverage,
        "matched_terms": list(hit.matched_terms),
        "text": hit.passage.text,
        "source": metadata.get("source"),
        "title": metadata.get("title"),
        "document_date": metadata.get("document_date"),
        "ingested_at_utc": metadata.get("ingested_at_utc"),
        "tags": list(metadata.get("tags") or []),
    }


def _extractive_answer(question: str, hits: list[Hit]) -> str:
    """Select sentences from the top passages that share terms with the question.

    Extractive, never abstractive: every sentence returned appears verbatim in a stored
    document. That is what lets the caller treat the answer as evidence rather than as a
    second-hand summary it would have to go and check.
    """
    from .index import query_terms_of, term_weight, terms_of  # noqa: PLC0415 - one caller

    wanted = set(query_terms_of(question))
    scored: list[tuple[float, int, int, str]] = []
    for rank, hit in enumerate(hits[:3]):
        for position, sentence in enumerate(_SENTENCE_SPLIT.split(hit.passage.text)):
            sentence = sentence.strip()
            if len(sentence) < 10:
                continue
            # Weighted, not counted: an exact word match is worth more than the two or three
            # bigrams it happens to drag along, and counting them equally lets a sentence win
            # on the debris of a match rather than the match.
            overlap = sum(term_weight(t) for t in wanted & set(terms_of(sentence)))
            if overlap:
                # rank/position keep the ordering deterministic and readable: a sentence from
                # a better passage, earlier in it, wins a tie.
                scored.append((-overlap, rank, position, sentence))
    if not scored:
        return hits[0].passage.text[:ANSWER_MAX_CHARS]

    scored.sort()
    chosen: list[str] = []
    used = 0
    for _, _, _, sentence in scored[:ANSWER_SENTENCES]:
        if sentence in chosen:
            continue
        if used + len(sentence) > ANSWER_MAX_CHARS:
            break
        chosen.append(sentence)
        used += len(sentence)
    return " ".join(chosen) if chosen else hits[0].passage.text[:ANSWER_MAX_CHARS]
