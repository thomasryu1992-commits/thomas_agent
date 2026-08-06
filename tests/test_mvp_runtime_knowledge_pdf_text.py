"""The PDF extractor — what it refuses, and why a refusal beats the string it could return.

These are the tests for the two failures the module was built around: returning binary as
though it were text, and dying because a backend was not installed. Everything else here is
in service of those two.

None of these need a local Core — decoding a PDF binds no task.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import KnowledgeBlocked
from runtime.mvp_runtime.knowledge import pdf_text

KOREAN = (
    "비트코인은 8월 5일 기준 62,400달러 부근에서 거래되고 있다. "
    "펀딩비는 중립 구간으로 회귀했고, 미결제약정은 전주 대비 12% 감소했다."
)


def build_pdf(lines: list[str]) -> bytes:
    """A minimal, structurally valid, text-bearing PDF.

    Hand-built rather than checked in as a binary fixture: a test that can *say* what is in
    the file it is asserting about is worth more than one that points at bytes, and the whole
    subject here is the difference between a file's bytes and its text. ASCII only — Korean
    would need an embedded CID font, and the CJK question this module actually owns is the
    text gate's, which :func:`test_the_gate_admits_korean` asks directly.
    """
    content = "BT /F1 12 Tf 72 720 Td 14 TL\n" + "".join(f"({line}) Tj T*\n" for line in lines) + "ET\n"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}endstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = "%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n"
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    return out.encode("latin-1")


SAMPLE = build_pdf([
    "Bitcoin daily analysis 2026-08-05.",
    "Funding rate returned to neutral territory.",
    "Open interest fell twelve percent week over week.",
])


# --- the binary-passthrough failure -------------------------------------------

def test_the_gate_rejects_the_pdf_bytes_themselves():
    """THE test. A PDF decodes cleanly under errors="replace", so every layer downstream
    accepts it; this is the only place that can still tell it apart from a document."""
    decoded = SAMPLE.decode("utf-8", "replace")
    verdict = pdf_text.text_gate(decoded)
    assert verdict is not None
    assert "PDF_BINARY_PASSTHROUGH" in verdict


def test_the_gate_rejects_high_replacement_character_content():
    """The general case of the same failure: bytes that were never text in this encoding."""
    damaged = "펀딩비 " + "�" * 400
    verdict = pdf_text.text_gate(damaged)
    assert verdict is not None
    assert "PDF_DECODE_DAMAGED" in verdict


def test_the_gate_admits_korean():
    """A gate that failed CJK would refuse precisely the documents this system is for —
    which is a worse outcome than having no gate, because it looks like a broken PDF."""
    assert pdf_text.text_gate(KOREAN) is None


@pytest.mark.parametrize("text", [
    "日本語のテキストがここにあります。これは十分な長さの文章です。ビットコインの分析です。",
    "Ordinary English prose, long enough to clear the minimum character floor comfortably.",
    "Mixed 한국어 and English, with numbers 62,400 and symbols — all of it legitimate text.",
])
def test_the_gate_admits_real_prose(text):
    assert pdf_text.text_gate(text) is None


def test_the_gate_names_an_empty_extraction_as_a_missing_text_layer():
    """Distinct from a failed extraction: the operator's next step differs (OCR vs. retry),
    so the two must not collapse into one reason code."""
    verdict = pdf_text.text_gate("   \n\n  ")
    assert verdict is not None
    assert "PDF_NO_TEXT_LAYER" in verdict


# --- the missing-backend failure ----------------------------------------------

def test_an_empty_backend_chain_refuses_and_names_the_environment(monkeypatch):
    """"Command not found" is an environment state, not a document problem, and the reason
    code has to say so or the operator debugs the wrong thing."""
    monkeypatch.setattr(pdf_text, "available_backends", lambda: ())
    with pytest.raises(KnowledgeBlocked) as caught:
        pdf_text.extract_pdf_text(SAMPLE)
    assert caught.value.reason_code == "PDF_NO_BACKEND"
    assert "poppler-utils" in str(caught.value)


def test_a_missing_binary_falls_through_to_the_next_backend(monkeypatch):
    """The chain's whole point. pdftotext absent must not be fatal while pypdf is present."""
    monkeypatch.setattr(pdf_text, "_pdftotext_binary", lambda: None)
    result = pdf_text.extract_pdf_text(SAMPLE)
    assert result.backend == pdf_text.PYPDF
    assert "Bitcoin daily analysis" in result.text


def test_a_backend_that_raises_is_skipped_rather_than_fatal(monkeypatch):
    """A backend may raise anything — a parser bug, a C-level error surfaced as ValueError.
    None of it is a reason to stop before the next backend has been asked."""
    monkeypatch.setattr(pdf_text, "available_backends", lambda: (pdf_text.PDFTOTEXT, pdf_text.PYPDF))

    def explode(_data):
        raise ValueError("poppler had an opinion")

    monkeypatch.setattr(pdf_text, "_extract_with_pdftotext", explode)
    result = pdf_text.extract_pdf_text(SAMPLE)
    assert result.backend == pdf_text.PYPDF
    assert result.attempted == (pdf_text.PDFTOTEXT, pdf_text.PYPDF)


def test_a_typed_document_refusal_stops_the_chain(monkeypatch):
    """An encrypted document is encrypted for every backend. Trying the next one would at
    best repeat the answer and at worst replace a true diagnosis with a vaguer one."""
    monkeypatch.setattr(pdf_text, "available_backends", lambda: (pdf_text.PDFTOTEXT, pdf_text.PYPDF))

    def encrypted(_data):
        raise KnowledgeBlocked("PDF_ENCRYPTED", "encrypted")

    monkeypatch.setattr(pdf_text, "_extract_with_pdftotext", encrypted)
    with pytest.raises(KnowledgeBlocked) as caught:
        pdf_text.extract_pdf_text(SAMPLE)
    assert caught.value.reason_code == "PDF_ENCRYPTED"


def test_every_backend_producing_no_text_is_reported_as_a_scan(monkeypatch):
    monkeypatch.setattr(pdf_text, "available_backends", lambda: (pdf_text.PYPDF,))
    monkeypatch.setattr(pdf_text, "_extract_with_pypdf", lambda _data: ("", 3))
    with pytest.raises(KnowledgeBlocked) as caught:
        pdf_text.extract_pdf_text(SAMPLE)
    assert caught.value.reason_code == "PDF_NO_TEXT_LAYER"
    assert "scanned" in str(caught.value)


def test_a_backend_returning_the_raw_pdf_never_reaches_the_caller(monkeypatch):
    """The two halves of this module meeting: the gate is not advisory, it is the exit
    condition. A backend that hands back the file itself produces a refusal, not a string."""
    monkeypatch.setattr(pdf_text, "available_backends", lambda: (pdf_text.PYPDF,))
    monkeypatch.setattr(
        pdf_text, "_extract_with_pypdf", lambda data: (data.decode("utf-8", "replace"), 1),
    )
    with pytest.raises(KnowledgeBlocked) as caught:
        pdf_text.extract_pdf_text(SAMPLE)
    assert caught.value.reason_code == "PDF_EXTRACTION_FAILED"
    assert "PDF_BINARY_PASSTHROUGH" in str(caught.value)


# --- input guards -------------------------------------------------------------

@pytest.mark.parametrize("data,code", [
    (b"", "PDF_INPUT_EMPTY"),
    (b"not a pdf at all, just some bytes", "PDF_NOT_A_PDF"),
    (b"\x00" * 2000 + b"%PDF-1.4", "PDF_NOT_A_PDF"),
])
def test_non_pdf_input_is_refused_before_a_backend_guesses(data, code):
    with pytest.raises(KnowledgeBlocked) as caught:
        pdf_text.extract_pdf_text(data)
    assert caught.value.reason_code == code


def test_an_oversized_document_is_refused():
    oversized = b"%PDF-1.4\n" + b"x" * (pdf_text.MAX_PDF_BYTES + 1)
    with pytest.raises(KnowledgeBlocked) as caught:
        pdf_text.extract_pdf_text(oversized)
    assert caught.value.reason_code == "PDF_TOO_LARGE"


# --- the happy path and its provenance ----------------------------------------

def test_a_real_pdf_extracts_with_recorded_provenance():
    result = pdf_text.extract_pdf_text(SAMPLE, source_name="daily.pdf")
    assert "Funding rate returned to neutral" in result.text
    assert result.backend in pdf_text.BACKEND_ORDER
    assert result.attempted[-1] == result.backend
    assert result.page_count >= 1
    assert result.chars == len(result.text)


def test_normalization_makes_two_spellings_of_one_document_compare_equal():
    """NFC, because one backend emits decomposed Hangul and another composed. Without this
    the content hash differs and a re-ingest stores a second copy of the same document.

    Both inputs are built by ``unicodedata`` rather than typed as literals. A decomposed
    Hangul literal is invisible in a diff and is silently recomposed by some editors on
    save — which is how a test like this goes on passing while asserting nothing."""
    import unicodedata

    composed = unicodedata.normalize("NFC", "한국어 비트코인 분석")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed            # the premise, asserted rather than assumed
    assert len(decomposed) > len(composed)
    assert pdf_text.normalize_text(decomposed) == pdf_text.normalize_text(composed) == composed


def test_normalization_strips_control_characters_but_keeps_layout():
    normalized = pdf_text.normalize_text("line one\x00\x07\nline\ttwo   \r\nline three")
    assert normalized == "line one\nline\ttwo\nline three"


def test_available_backends_is_answerable_without_a_document():
    """An operator asking "can this host read a PDF at all" should not have to feed it one."""
    backends = pdf_text.available_backends()
    assert set(backends) <= set(pdf_text.BACKEND_ORDER)
    assert list(backends) == [b for b in pdf_text.BACKEND_ORDER if b in backends]
