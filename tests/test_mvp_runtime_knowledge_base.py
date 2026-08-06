"""The knowledge base — retrieval that works on Korean, and a store that keeps its history.

The claims worth testing here are not "search returns something". They are:

- a question phrased with different particles than the document still finds it (the reason
  the term space is hybrid at all);
- a question the corpus has no material on returns *nothing*, on a corpus of any size (the
  reason the relevance floor is coverage rather than score);
- re-filing a document does not duplicate it, and does not erase what it replaced.

None of these need a local Core — the store binds no task and invokes no model.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import KnowledgeBlocked, PersistenceError
from runtime.mvp_runtime.knowledge import index, service
from runtime.mvp_runtime.knowledge.store import KnowledgeStore

NOW = "2026-08-06T09:00:00Z"

DAILY_0805 = (
    "비트코인은 8월 5일 기준 62,400달러 부근에서 거래되고 있다. 온체인 지표상 장기 보유자의 "
    "매도 압력이 완화되었다.\n\n"
    "펀딩비는 중립 구간으로 회귀했고, 미결제약정은 전주 대비 12% 감소했다. 단기적으로는 "
    "61,000달러 지지선이 핵심이다.\n\n"
    "이더리움 대비 상대강도는 여전히 비트코인 우위다. 알트코인 시즌 지표는 28로 낮다."
)
DAILY_0804 = (
    "8월 4일 비트코인은 60,900달러까지 하락했다가 반등했다. 거래량은 30일 평균을 밑돌았다.\n\n"
    "현물 ETF 순유입은 3거래일 연속 플러스를 기록했다. 기관 수요가 유지되고 있다는 신호다.\n\n"
    "채굴자 매도 물량은 해시레이트 상승에도 안정적이다."
)
MACRO_0803 = (
    "연준은 9월 회의에서 금리 인하 가능성을 열어뒀다. 시장은 25bp 인하를 70% 확률로 반영 중이다.\n\n"
    "달러 인덱스는 103 부근에서 횡보하고 있다. 위험자산 선호가 소폭 개선되었다.\n\n"
    "10년물 국채 금리는 4.1%로 하락했다."
)


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(tmp_path / "knowledge")


@pytest.fixture()
def corpus(store):
    for title, source, text in (
        ("BTC 일일 분석 2026-08-05", "daily/btc-2026-08-05.md", DAILY_0805),
        ("BTC 일일 분석 2026-08-04", "daily/btc-2026-08-04.md", DAILY_0804),
        ("거시경제 브리핑 2026-08-03", "macro/2026-08-03.md", MACRO_0803),
    ):
        service.add_document(
            store=store, title=title, source=source, text=text,
            ingested_by="assistant_bridge", now=NOW, tags=["btc"],
        )
    return store


# --- the term space -----------------------------------------------------------

def test_korean_particles_do_not_hide_a_match():
    """THE reason bigrams exist. "비트코인" and "비트코인의" are different word terms, so a
    word-only index answers nothing to half of every Korean question asked of it."""
    document = index.terms_of("비트코인의 지지선")
    question = index.query_terms_of("비트코인 지지선")
    assert "비트코인" not in document                # the premise: no shared word term
    assert set(document) & set(question)            # but the bigrams bridge it
    assert "2:비트" in set(document) & set(question)


def test_bigrams_are_weighted_below_word_terms():
    """They buy recall and cost precision; the exchange rate is explicit, not accidental."""
    assert index.term_weight("2:비트") < index.term_weight("비트코인")
    assert index.term_weight("2:비트") == index.BIGRAM_WEIGHT


def test_question_scaffolding_is_dropped_from_the_query_side_only():
    """"어디인가" cannot match anything meaningful but was half the weight of the question,
    which is how "지지선은 어디인가" scored 0.18 coverage against a document containing 지지선."""
    assert "어디인가" not in index.query_terms_of("지지선은 어디인가")
    assert "2:어디" not in index.query_terms_of("지지선은 어디인가")
    # A document that happens to contain the word keeps it — this is a query-side rule.
    assert "어디인가" in index.terms_of("어디인가 하는 질문이 있었다")


def test_a_lone_latin_character_is_not_a_term_but_a_lone_hangul_syllable_is():
    assert "a" not in index.terms_of("a bitcoin analysis")
    assert "물" in index.terms_of("물 온스")


# --- relevance ----------------------------------------------------------------

@pytest.mark.parametrize("question,expected_source", [
    ("지지선은 어디인가", "daily/btc-2026-08-05.md"),
    ("펀딩비 상황은?", "daily/btc-2026-08-05.md"),
    ("금리 인하 전망은 어떻게 되나요?", "macro/2026-08-03.md"),
    ("ETF 자금 유입은?", "daily/btc-2026-08-04.md"),
    ("채굴자 매도 물량", "daily/btc-2026-08-04.md"),
])
def test_a_question_finds_the_document_that_answers_it(corpus, question, expected_source):
    result = service.query(store=corpus, question=question)
    assert result["hits"], f"no hit for {question!r}"
    assert result["hits"][0]["source"] == expected_source


@pytest.mark.parametrize("question", [
    "김치프리미엄은 얼마인가",
    "반감기 이후 해시프라이스 추이",
    "도지코인 커뮤니티 동향",
])
def test_a_question_the_corpus_has_no_material_on_returns_nothing(corpus, question):
    """The failure that matters most: an answer to a question the corpus cannot answer is
    indistinguishable from a real one at the point of reading."""
    result = service.query(store=corpus, question=question)
    assert result["hits"] == []
    assert result["reason"] == "NO_RELEVANT_PASSAGE"
    assert result["answer"] is None


def test_relevance_does_not_depend_on_corpus_size(store):
    """The regression that produced the coverage floor. BM25's scale is set by corpus IDF, so
    an absolute score threshold rejects a correct match on a one-document corpus — the corpus
    every deployment has on its first day."""
    service.add_document(
        store=store, title="단일 문서", source="daily/only.md", text=DAILY_0805,
        ingested_by="assistant_bridge", now=NOW,
    )
    result = service.query(store=store, question="펀딩비 상황은?")
    assert result["hits"], "a one-document corpus must still answer about its one document"
    assert "펀딩비" in result["answer"]


def test_an_empty_corpus_says_so_rather_than_reporting_no_match(store):
    """"Nothing relevant" invites the caller to conclude the base has nothing on the topic.
    "Nothing at all" is a different fact and the caller acts on it differently."""
    result = service.query(store=store, question="펀딩비 상황은?")
    assert result["reason"] == "KNOWLEDGE_BASE_EMPTY"
    assert result["stats"]["documents"] == 0


def test_a_hit_shows_its_working(corpus):
    """An answer that cannot be checked is a guess with a citation stapled on."""
    hit = service.query(store=corpus, question="금리 인하 전망")["hits"][0]
    assert hit["matched_terms"]
    assert 0.0 < hit["coverage"] <= 1.0
    assert hit["score"] > 0
    assert hit["source"] and hit["document_id"].startswith("kdoc_")


def test_the_answer_is_extractive(corpus):
    """Every sentence returned appears verbatim in a stored document — which is what lets the
    caller treat the answer as evidence instead of as a summary it must go and verify."""
    result = service.query(store=corpus, question="금리 인하 전망")
    stored = " ".join(row["text"] for row in corpus.live_documents())
    for sentence in result["answer"].split(". "):
        assert sentence.strip(". ") in stored


def test_search_is_deterministic(corpus):
    first = service.query(store=corpus, question="비트코인 지지선", limit=5)
    second = service.query(store=corpus, question="비트코인 지지선", limit=5)
    assert first["hits"] == second["hits"]
    assert first["answer"] == second["answer"]


def test_the_limit_is_honoured_and_capped(corpus):
    assert len(service.query(store=corpus, question="비트코인", limit=1)["hits"]) <= 1
    with pytest.raises(KnowledgeBlocked) as caught:
        service.query(store=corpus, question="비트코인", limit=999)
    assert caught.value.reason_code == "KNOWLEDGE_LIMIT_INVALID"


# --- chunking -----------------------------------------------------------------

def test_a_long_document_is_split_into_overlapping_passages():
    """A knowledge base answers with passages. Returning a 40-page report because page 31
    matched is technically a retrieval and practically a shrug."""
    long_text = "\n\n".join(f"{i}번째 문단이다. " * 20 for i in range(20))
    chunks = index.chunk_text(long_text)
    assert len(chunks) > 1
    assert all(len(c) <= index.CHUNK_TARGET_CHARS * 1.5 for c in chunks)


def test_a_paragraph_larger_than_a_chunk_still_gets_split():
    wall = "가나다라마바사아자차카타파하" * 400
    chunks = index.chunk_text(wall)
    assert len(chunks) > 1
    assert "".join(c[:1] for c in chunks)


def test_a_short_document_is_one_chunk():
    assert index.chunk_text("짧은 문서다.") == ["짧은 문서다."]


# --- the store ----------------------------------------------------------------

def test_re_filing_identical_content_stores_nothing_and_says_so(store):
    """At-most-once falls out of the record's identity: a retry after a client timeout finds
    the row already there rather than appending a second copy of the same document."""
    first = service.add_document(
        store=store, title="일일 분석", source="daily/btc.md", text=DAILY_0805,
        ingested_by="assistant_bridge", now=NOW,
    )
    second = service.add_document(
        store=store, title="일일 분석", source="daily/btc.md", text=DAILY_0805,
        ingested_by="assistant_bridge", now=NOW,
    )
    assert second["duplicate"] is True
    assert second["document_id"] == first["document_id"]
    assert store.stats()["documents"] == 1


def test_re_filing_a_source_with_new_content_supersedes_without_erasing(store):
    """The sequence of rows for one source IS its revision history — "what did the 08-05
    analysis say before it was corrected" is not answerable by a store that rewrites."""
    first = service.add_document(
        store=store, title="일일 분석", source="daily/btc.md", text=DAILY_0805,
        ingested_by="assistant_bridge", now=NOW,
    )
    second = service.add_document(
        store=store, title="일일 분석 (정정)", source="daily/btc.md", text=DAILY_0804,
        ingested_by="assistant_bridge", now=NOW,
    )
    assert second["document_id"] != first["document_id"]
    live = store.live_documents()
    assert len(live) == 1 and live[0]["document_id"] == second["document_id"]
    # The superseded row is out of the answer, not out of the record.
    assert any(row["document_id"] == first["document_id"] for row in store.read_all())
    assert first["document_id"] not in {h["document_id"] for h in
                                        service.query(store=store, question="펀딩비")["hits"]}


def test_a_stored_document_satisfies_its_closed_schema(store):
    """Validated on the way in, so a record that could not be read back never gets written."""
    service.add_document(
        store=store, title="일일 분석", source="daily/btc.md", text=DAILY_0805,
        ingested_by="assistant_bridge", now=NOW, document_date="2026-08-05", tags=["btc", "daily"],
    )
    row = store.read_all()[0]
    assert row["schema_version"] == "knowledge_document.v0.1"
    assert row["document_date"] == "2026-08-05T00:00:00Z"
    assert row["tags"] == ["btc", "daily"]
    assert row["ingested_by"] == "assistant_bridge"
    assert len(row["content_sha256"]) == 64


def test_a_corrupt_store_fails_closed_rather_than_returning_partial_knowledge(store):
    service.add_document(
        store=store, title="일일 분석", source="daily/btc.md", text=DAILY_0805,
        ingested_by="assistant_bridge", now=NOW,
    )
    store.path.write_text(store.path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    with pytest.raises(PersistenceError) as caught:
        store.read_all()
    assert caught.value.reason_code == "KNOWLEDGE_UNREADABLE"


def test_the_index_is_rebuilt_when_the_store_changes(store):
    service.add_document(
        store=store, title="A", source="a.md", text=DAILY_0805,
        ingested_by="assistant_bridge", now=NOW,
    )
    before = store.retriever().size
    service.add_document(
        store=store, title="B", source="b.md", text=MACRO_0803,
        ingested_by="assistant_bridge", now=NOW,
    )
    assert store.retriever().size > before


# --- input guards -------------------------------------------------------------

def test_content_must_be_exactly_one_of_text_or_pdf(store):
    """A call carrying both states a precedence this door does not have; whichever it picked
    would be wrong for half of the callers who sent both."""
    for kwargs in ({}, {"text": "x" * 50, "pdf_base64": "eA=="}):
        with pytest.raises(KnowledgeBlocked) as caught:
            service.add_document(
                store=store, title="t", source="s", ingested_by="assistant_bridge", **kwargs,
            )
        assert caught.value.reason_code == "KNOWLEDGE_CONTENT_AMBIGUOUS"


@pytest.mark.parametrize("kwargs,code", [
    ({"title": "  "}, "KNOWLEDGE_TITLE_REQUIRED"),
    ({"source": ""}, "KNOWLEDGE_SOURCE_REQUIRED"),
    ({"text": "   "}, "KNOWLEDGE_TEXT_REQUIRED"),
    ({"data_sensitivity": "TOP_SECRET"}, "KNOWLEDGE_SENSITIVITY_INVALID"),
    ({"source_type": "docx"}, "KNOWLEDGE_SOURCE_TYPE_INVALID"),
    ({"document_date": "not-a-date"}, "KNOWLEDGE_DOCUMENT_DATE_INVALID"),
    ({"tags": ["ok", ""]}, "KNOWLEDGE_TAGS_INVALID"),
])
def test_malformed_ingests_are_refused_with_a_named_reason(store, kwargs, code):
    call = {
        "title": "일일 분석", "source": "daily/btc.md", "text": DAILY_0805,
        "ingested_by": "assistant_bridge", "now": NOW,
    }
    call.update(kwargs)
    with pytest.raises(KnowledgeBlocked) as caught:
        service.add_document(store=store, **call)
    assert caught.value.reason_code == code


@pytest.mark.parametrize("question", ["", "   ", None])
def test_an_empty_question_is_refused(corpus, question):
    with pytest.raises(KnowledgeBlocked) as caught:
        service.query(store=corpus, question=question)
    assert caught.value.reason_code == "KNOWLEDGE_QUESTION_REQUIRED"


def test_a_bare_date_is_accepted_as_midnight_utc(store):
    """An operator filing a daily analysis types a date, not an instant."""
    service.add_document(
        store=store, title="t", source="s", text=DAILY_0805,
        ingested_by="assistant_bridge", now=NOW, document_date="2026-08-05",
    )
    assert store.read_all()[0]["document_date"] == "2026-08-05T00:00:00Z"
