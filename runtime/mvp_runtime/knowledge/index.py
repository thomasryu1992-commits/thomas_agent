"""Retrieval with no dependency, no model, and no network.

The obvious build here is ChromaDB or FAISS over a sentence-transformer. It was rejected for
a reason worth writing down, because the next person will reach for it again: this runtime's
production dependency list is ``PyYAML`` and ``jsonschema``, and every network capability is
OFF and enforced in code. A hosted embedding API needs a ``network_access`` grant Thomas has
not signed; a local embedding model needs PyTorch baked into the image, which is roughly a
2 GB answer to a question the corpus size does not yet ask. Neither is *wrong* later. Both
are a governance escalation and an architecture change to ship a feature that has not yet
demonstrated it needs them.

So retrieval is **BM25 over a hybrid term space**, which is a real ranking function rather
than a placeholder, and which for this corpus is very likely within noise of a dense model
anyway. What makes it work on Korean is the hybrid part:

- **Word terms.** Runs of letters and digits, casefolded. On English these are words. On
  Korean they are 어절 — "비트코인의", "비트코인은" and "비트코인" are three different terms,
  which on its own would make the index nearly useless for the language it most needs to
  serve.
- **Character bigrams**, emitted for every token. "비트코인의" also contributes 비트/트코/코인/
  인의, so a question asking about 비트코인 matches a document discussing 비트코인의 without
  either side needing a morphological analyzer, a dictionary, or a model. This is the
  CJK-bigram trick Lucene has shipped for twenty years; it is old because it works.

Bigrams are noisier than words, so they carry :data:`BIGRAM_WEIGHT` of a word's weight —
they are there to rescue recall, not to outvote an exact term match.

**The seam.** :class:`Retriever` is the interface :mod:`service` depends on, and
:class:`LexicalRetriever` is the only implementation today. A dense backend arrives later as
a second implementation of the same three methods, and nothing above this module changes:
not the door's verbs, not the stored records, not the caller. That is the whole reason the
seam is drawn here rather than left implicit.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Protocol, Sequence

# BM25's standard parameters. k1 damps the effect of a term repeating within one chunk; b is
# how strongly a long chunk is penalized for having more chances to match. These are the
# textbook values and are not tuned — with a corpus this size, tuning them would be fitting
# noise, and a tuned constant nobody can re-derive is worse than a defensible default.
K1 = 1.5
B = 0.75

# A bigram match is worth this much of a word match. See the module docstring: bigrams buy
# recall on an agglutinative language and cost precision, and this is the exchange rate.
BIGRAM_WEIGHT = 0.4

# Chunking. A knowledge base answers with *passages*, not documents: handing back a 40-page
# report because page 31 matched is technically a retrieval and practically a shrug.
CHUNK_TARGET_CHARS = 900
CHUNK_OVERLAP_CHARS = 150
MIN_CHUNK_CHARS = 40

# A term appearing in more than this fraction of chunks carries no signal (BM25's IDF already
# drives it toward zero; this stops it costing scoring time as well). Only applied once the
# corpus is big enough for the ratio to mean anything.
STOP_TERM_DOCUMENT_RATIO = 0.9
_STOP_TERM_MIN_CHUNKS = 20

# Unicode-aware token pattern: any run of letters, marks or digits. `\w` would also admit the
# underscore and depend on the locale; being explicit keeps the term space reproducible.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Scripts whose "words" are not whitespace-delimited, so a token is not a word and the bigram
# path is doing the real work. Used only to decide whether a *single-character* token is worth
# keeping — "물" is a word, "a" almost never is.
_CJK_RANGES = (
    (0xAC00, 0xD7A3),   # Hangul syllables
    (0x1100, 0x11FF),   # Hangul jamo
    (0x3040, 0x30FF),   # Hiragana + Katakana
    (0x4E00, 0x9FFF),   # CJK unified ideographs
    (0x3400, 0x4DBF),   # CJK extension A
)


def is_cjk(ch: str) -> bool:
    point = ord(ch)
    return any(low <= point <= high for low, high in _CJK_RANGES)


def terms_of(text: str) -> Counter[str]:
    """The weighted term counts for one piece of text.

    Word terms are stored bare; bigrams are prefixed ``2:`` so the two spaces cannot collide
    — without the prefix, the Korean bigram "비트" and a hypothetical word "비트" would share
    a posting list and silently double-count.
    """
    counts: Counter[str] = Counter()
    for token in _TOKEN_RE.findall(unicodedata.normalize("NFC", text).casefold()):
        if len(token) == 1 and not is_cjk(token):
            continue        # a lone Latin letter or digit is noise, not a term
        counts[token] += 1
        for i in range(len(token) - 1):
            counts[f"2:{token[i:i + 2]}"] += 1
    return counts


def term_weight(term: str) -> float:
    return BIGRAM_WEIGHT if term.startswith("2:") else 1.0


# Question scaffolding: the words that make a string a question rather than the words that say
# what it is about. They are dropped from the *query* side only — a document containing
# "어떻게" keeps it, because on the document side it is just a word.
#
# This is not a general stopword list and must not become one. Every entry is here because it
# inflates `Hit.coverage`'s denominator while being unable to match anything meaningful, and
# the failure that motivated it is concrete: "지지선은 어디인가" scored 0.18 coverage against a
# document that literally contains 지지선, because 어디인가 and its three bigrams were 50% of
# the question's weight and matched nothing. Content words absent from the corpus stay in the
# denominator on purpose — that is coverage correctly reporting an unanswerable question.
_QUERY_STOPWORDS = frozenset({
    # Korean interrogatives and request forms
    "무엇", "무엇인가", "무엇인지", "뭐", "뭔가", "어디", "어디인가", "어디인지", "어떻게",
    "어떤", "어떠한", "어느", "왜", "언제", "누구", "누가", "얼마", "얼마나", "몇",
    "알려줘", "알려주세요", "설명해줘", "설명해주세요", "요약해줘", "요약해주세요",
    "말해줘", "보여줘", "정리해줘", "무슨",
    # Korean function words that survive tokenization as their own eojeol
    "그리고", "그러나", "하지만", "또는", "및", "대해", "대한", "관련", "위한", "위해",
    "있는", "있나", "있나요", "인가", "인가요", "일까", "일까요", "한가", "한가요",
    # English question words and auxiliaries
    "what", "where", "when", "why", "how", "who", "which", "whose", "whom",
    "is", "are", "was", "were", "do", "does", "did", "the", "a", "an", "of", "in",
    "on", "for", "to", "and", "or", "tell", "me", "about", "explain", "summarize",
})


def query_terms_of(text: str) -> Counter[str]:
    """Term counts for a *question*, with scaffolding removed.

    A stopword token is dropped whole — its bigrams go with it. Dropping the word and keeping
    2:어디/2:디인/2:인가 would leave three quarters of the weight it contributed and defeat the
    point of dropping it.
    """
    counts: Counter[str] = Counter()
    for token in _TOKEN_RE.findall(unicodedata.normalize("NFC", text).casefold()):
        if token in _QUERY_STOPWORDS:
            continue
        if len(token) == 1 and not is_cjk(token):
            continue
        counts[token] += 1
        for i in range(len(token) - 1):
            counts[f"2:{token[i:i + 2]}"] += 1
    return counts


def chunk_text(text: str) -> list[str]:
    """Split ``text`` into overlapping passages, preferring paragraph boundaries.

    Overlap exists because the sentence that answers a question is frequently the one
    straddling a boundary, and a chunker with no overlap is a chunker that reliably cuts
    exactly there. Paragraphs are preferred over a fixed stride so a passage usually begins
    where the author began one.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= CHUNK_TARGET_CHARS:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > CHUNK_TARGET_CHARS:
            # A paragraph bigger than a chunk (a table, a wall of text with no blank lines)
            # falls back to a hard stride with the same overlap.
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_stride(paragraph))
            continue
        if not current:
            current = paragraph
        elif len(current) + len(paragraph) + 2 <= CHUNK_TARGET_CHARS:
            current = f"{current}\n\n{paragraph}"
        else:
            chunks.append(current)
            tail = current[-CHUNK_OVERLAP_CHARS:] if CHUNK_OVERLAP_CHARS else ""
            current = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
    if current:
        chunks.append(current)
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS] or [text[:CHUNK_TARGET_CHARS]]


def _stride(paragraph: str) -> list[str]:
    step = max(1, CHUNK_TARGET_CHARS - CHUNK_OVERLAP_CHARS)
    return [
        paragraph[start:start + CHUNK_TARGET_CHARS]
        for start in range(0, len(paragraph), step)
        if paragraph[start:start + CHUNK_TARGET_CHARS].strip()
    ]


@dataclass(frozen=True)
class Passage:
    """One indexable chunk and the document it came from."""

    document_id: str
    chunk_index: int
    text: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Hit:
    """One scored passage. ``matched_terms`` is why it scored, in descending contribution.

    An answer that cannot show its working is a guess with a citation stapled on, and this
    field is what lets the caller — and the operator reading over its shoulder — see whether
    a hit matched on the thing they meant or on a coincidence of bigrams.

    ``coverage`` is the weighted fraction of the *question's* terms this passage matched, and
    it exists because ``score`` cannot be thresholded. BM25 is a ranking function, not a
    similarity: its scale is set by the corpus's IDF, so with three documents every score is
    small and with three thousand the same match scores several times higher. An absolute
    floor on it therefore means "relevant" on a mature corpus and "reject everything" on a
    new one — which is exactly backwards, since the new corpus is the one whose first users
    conclude the thing does not work. Coverage is corpus-independent: it asks how much of
    what was *asked* actually appears here.
    """

    passage: Passage
    score: float
    coverage: float
    matched_terms: tuple[str, ...]


class Retriever(Protocol):
    """The seam a dense backend would implement instead. Three methods, no more."""

    def index(self, passages: Sequence[Passage]) -> None: ...

    def search(self, question: str, *, limit: int) -> list[Hit]: ...

    @property
    def size(self) -> int: ...


class LexicalRetriever:
    """BM25 over the hybrid word+bigram term space. Deterministic, stdlib-only.

    Deterministic in the sense that matters for this repo's definition: the same corpus and
    the same question produce the same ranking, byte for byte, on any machine and in any
    process — no seed, no float accumulation order that depends on dict iteration, and ties
    broken by ``(document_id, chunk_index)`` rather than by whatever order the store happened
    to yield.
    """

    def __init__(self) -> None:
        self._passages: list[Passage] = []
        self._term_counts: list[Counter[str]] = []
        self._lengths: list[float] = []
        self._document_frequency: Counter[str] = Counter()
        self._average_length = 0.0
        self._stop_terms: frozenset[str] = frozenset()

    def index(self, passages: Sequence[Passage]) -> None:
        self._passages = list(passages)
        self._term_counts = [terms_of(p.text) for p in self._passages]
        # Length in *weighted* terms, so a chunk is not judged long merely for being written
        # in a script that emits a bigram per character.
        self._lengths = [
            sum(count * term_weight(term) for term, count in counts.items())
            for counts in self._term_counts
        ]
        self._average_length = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0

        self._document_frequency = Counter()
        for counts in self._term_counts:
            self._document_frequency.update(counts.keys())

        total = len(self._passages)
        if total >= _STOP_TERM_MIN_CHUNKS:
            ceiling = STOP_TERM_DOCUMENT_RATIO * total
            self._stop_terms = frozenset(
                term for term, df in self._document_frequency.items() if df > ceiling
            )
        else:
            self._stop_terms = frozenset()

    def search(self, question: str, *, limit: int) -> list[Hit]:
        query_terms = query_terms_of(question)
        if not query_terms or not self._passages:
            return []

        total = len(self._passages)
        # The denominator of `coverage`: everything the question asked for, weighted. Stop
        # terms are excluded from both sides, so a question full of corpus-wide boilerplate
        # is not penalized for terms no passage could have distinguished itself by.
        askable = [t for t in sorted(query_terms) if t not in self._stop_terms]
        asked_weight = sum(term_weight(term) for term in askable)

        scored: list[Hit] = []
        for position, counts in enumerate(self._term_counts):
            contributions: list[tuple[float, str]] = []
            matched_weight = 0.0
            for term in askable:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                matched_weight += term_weight(term)
                df = self._document_frequency[term]
                idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
                length = self._lengths[position]
                denominator = frequency + K1 * (
                    1.0 - B + B * (length / self._average_length if self._average_length else 1.0)
                )
                contribution = term_weight(term) * idf * (frequency * (K1 + 1.0)) / denominator
                contributions.append((contribution, term))
            if not contributions:
                continue
            contributions.sort(key=lambda pair: (-pair[0], pair[1]))
            scored.append(
                Hit(
                    passage=self._passages[position],
                    score=round(sum(c for c, _ in contributions), 6),
                    coverage=round(matched_weight / asked_weight, 6) if asked_weight else 0.0,
                    matched_terms=tuple(term for _, term in contributions[:8]),
                )
            )

        scored.sort(
            key=lambda hit: (
                -hit.score, hit.passage.document_id, hit.passage.chunk_index,
            )
        )
        return scored[:limit]

    @property
    def size(self) -> int:
        return len(self._passages)


def build_passages(documents: Iterable[Mapping[str, object]]) -> list[Passage]:
    """Turn stored document records into the passages a retriever indexes.

    The chunking happens here, at index time, rather than being stored per chunk. That is a
    deliberate trade: re-chunking the corpus costs milliseconds and is paid once per process
    (the store caches the built index by file signature), while *storing* chunks would freeze
    today's chunk size into every record and make changing it a migration.
    """
    passages: list[Passage] = []
    for record in documents:
        document_id = str(record.get("document_id", ""))
        text = str(record.get("text", ""))
        metadata = {
            "source": record.get("source"),
            "title": record.get("title"),
            "ingested_at_utc": record.get("ingested_at_utc"),
            "document_date": record.get("document_date"),
            "tags": record.get("tags", []),
        }
        for position, chunk in enumerate(chunk_text(text)):
            passages.append(
                Passage(
                    document_id=document_id,
                    chunk_index=position,
                    text=chunk,
                    metadata=metadata,
                )
            )
    return passages
