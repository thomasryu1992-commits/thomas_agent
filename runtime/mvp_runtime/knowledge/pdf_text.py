"""PDF -> text, or a typed refusal. Never bytes wearing a string's clothes.

This module exists because of two specific failures, and every decision in it is aimed at
one of them.

**"It returned binary."** A PDF's bytes decode fine under ``latin-1``, and under
``utf-8, errors="replace"`` they decode to something a type checker, a JSON encoder and a
JSONL store all accept. So the failure is silent at every layer that could have caught it,
and surfaces later as a knowledge base full of ``%PDF-1.7 %âãÏÓ 1 0 obj``, cited back at
the operator as though it were a document. The repair is not "decode more carefully" —
it is that extraction is not finished when a backend returns a string. It is finished when
that string passes :func:`text_gate`, which asks whether this looks like prose a human
wrote. Anything else is a refusal, and a refusal stores nothing.

**"Command not found."** ``pdftotext`` lives in ``poppler-utils``, which is an image build
decision, not a Python one — so it is present in the deployed container and absent in a
bare checkout, a test runner, or the next host someone provisions. A missing binary is
therefore an *expected environment state*, not an error: it takes this module to the next
backend and is recorded in ``attempted``. Only an empty chain refuses, with a reason code
that names the environment rather than the document.

**The backend chain**, in order, first success wins:

1. ``pdftotext`` (poppler). Best CJK/CID-font coverage, and the reason poppler-utils is in
   the Dockerfile. A subprocess — the only one in this runtime, which had none. It is
   pinned as tightly as a subprocess can be: an absolute path resolved by ``shutil.which``,
   a fixed argv, ``shell=False``, a minimal environment, a wall-clock timeout, and an output
   cap, so it can neither be redirected by ``PATH``, nor hang the door, nor return more than
   the caller agreed to hold.
2. ``pypdf``. Pure Python, so it works wherever the interpreter does — including the test
   suite and any host where the apt layer is missing. Weaker on unusual CID fonts, which is
   why it is second and not first.

Both are optional at import time. Nothing here is enabled by an env var, nothing reaches
the network, and no model is invoked: this is a decoder, so it needs no safety flag. A
*scanned* PDF is not decodable by either backend — it is images — and is refused with its
own reason code rather than stored as an empty document, because "we have that report" and
"we have that report's zero characters" are answers that differ only when it is too late.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass

from ..errors import KnowledgeBlocked

PDF_MAGIC = b"%PDF-"

# Backend names, as they appear in `Extraction.backend` and `Extraction.attempted`. Stable
# strings: they are stored on every document record and read back by later diagnosis.
PDFTOTEXT = "pdftotext"
PYPDF = "pypdf"
BACKEND_ORDER: tuple[str, ...] = (PDFTOTEXT, PYPDF)

# Ceilings. A door serves these, so every one of them is a denial-of-service ceiling as much
# as a correctness one.
MAX_PDF_BYTES = 64 * 1024 * 1024        # a 64 MB PDF is already an unusual research report
MAX_TEXT_CHARS = 4 * 1024 * 1024        # ~4M chars of extracted text
PDFTOTEXT_TIMEOUT_SECONDS = 120

# The text gate's thresholds. See `text_gate` for what each one is actually catching.
MIN_TEXT_CHARS = 32
MAX_REPLACEMENT_RATIO = 0.02
MIN_ACCEPTABLE_RATIO = 0.85
_BINARY_SNIFF_CHARS = 4096

# `pdftotext` separates pages with a form feed; it is how the page count is recovered without
# paying for a second `pdfinfo` process.
_PAGE_BREAK = "\f"


@dataclass(frozen=True)
class Extraction:
    """One successful extraction, and the provenance of how it was obtained.

    ``attempted`` is the whole chain up to and including the backend that won — the record
    of what was tried is worth as much as the text when someone later asks why a document
    reads badly."""

    text: str
    backend: str
    page_count: int
    attempted: tuple[str, ...]

    @property
    def chars(self) -> int:
        return len(self.text)


def available_backends() -> tuple[str, ...]:
    """The backends this host can actually run, in chain order.

    Deliberately callable without a document: an operator asking "can this machine read a
    PDF at all" should not have to feed it one, and a readiness board that can answer that
    before the first ingest is the difference between a diagnosed environment and a
    mysterious refusal."""
    found: list[str] = []
    if _pdftotext_binary() is not None:
        found.append(PDFTOTEXT)
    if _pypdf_module() is not None:
        found.append(PYPDF)
    return tuple(found)


def extract_pdf_text(data: bytes, *, source_name: str = "document.pdf") -> Extraction:
    """Extract text from PDF ``data``, or raise :class:`KnowledgeBlocked`.

    Bytes rather than a path on purpose: the door receives a document over a socket, and a
    path-taking API would have invited it to name a file on the runtime's filesystem — which
    is a read primitive the assistant does not otherwise hold, handed over as a side effect
    of wanting to read PDFs.
    """
    _check_pdf_bytes(data, source_name)

    backends = available_backends()
    if not backends:
        raise KnowledgeBlocked(
            "PDF_NO_BACKEND",
            "no PDF text backend on this host: install poppler-utils (pdftotext) or the "
            "pypdf package. This is an environment state, not a problem with the document",
        )

    attempted: list[str] = []
    failures: list[str] = []
    for name in backends:
        attempted.append(name)
        try:
            raw, pages = _run_backend(name, data)
        except KnowledgeBlocked:
            # A typed refusal is a judgement about the *document* (encrypted, corrupt), not
            # about this backend's luck. Trying the next one would at best get the same
            # answer and at worst get a worse one, so it propagates.
            raise
        except Exception as exc:  # noqa: BLE001 - a backend may raise anything; none is fatal
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            continue

        text = normalize_text(raw)
        verdict = text_gate(text)
        if verdict is None:
            if pages <= 0:
                pages = text.count(_PAGE_BREAK) + 1
            return Extraction(
                text=text, backend=name, page_count=pages, attempted=tuple(attempted),
            )
        failures.append(f"{name}: {verdict}")

    # Every backend ran and none produced usable text. The most common cause by far is a
    # scanned PDF, so say so — the operator's next step (OCR, or ask for the source file)
    # depends on knowing which of the two happened.
    if failures and all("PDF_NO_TEXT_LAYER" in failure for failure in failures):
        raise KnowledgeBlocked(
            "PDF_NO_TEXT_LAYER",
            f"{source_name}: every backend {list(attempted)} read the file but found no text "
            "layer — this is almost certainly a scanned/image-only PDF. OCR is out of scope; "
            "supply a text-bearing source",
        )
    raise KnowledgeBlocked(
        "PDF_EXTRACTION_FAILED",
        f"{source_name}: no backend produced usable text ({'; '.join(failures)})",
    )


def text_gate(text: str) -> str | None:
    """``None`` if ``text`` looks like extracted prose; else a short reason it does not.

    THE check this module is built around. Four questions, each one a failure that has
    actually been shipped by somebody's PDF pipeline:

    - **Is it the PDF itself?** A leading ``%PDF-`` is the binary-passthrough bug caught at
      the only layer that can still tell.
    - **Is there anything at all?** Empty output is a scanned PDF, and gets its own code so
      the caller can say so instead of storing a document with no content.
    - **Did the decode fail?** U+FFFD is what ``errors="replace"`` leaves behind. A handful
      can occur in a legitimately messy document; a drift of them means the bytes were never
      text in this encoding.
    - **Is it printable?** The general case of the first question, and the one that catches
      a mangled font encoding. ``unicodedata.category`` rather than ``str.isprintable`` so
      the rule is explicit about what it admits: Hangul, Han and Kana are letters (``L*``)
      and pass, exactly as Latin does — a gate that quietly failed CJK would be worse than
      no gate, because it would refuse precisely the documents this system is for.
    """
    stripped = text.strip()
    if not stripped:
        return "PDF_NO_TEXT_LAYER (no characters recovered)"
    if stripped.encode("utf-8", "ignore").startswith(PDF_MAGIC):
        return "PDF_BINARY_PASSTHROUGH (output begins with the PDF header)"
    if len(stripped) < MIN_TEXT_CHARS:
        return (
            f"PDF_NO_TEXT_LAYER (only {len(stripped)} characters recovered, "
            f"under the {MIN_TEXT_CHARS} minimum)"
        )

    sample = text[:_BINARY_SNIFF_CHARS]
    replacements = sample.count("�")
    if replacements / len(sample) > MAX_REPLACEMENT_RATIO:
        return (
            f"PDF_DECODE_DAMAGED ({replacements}/{len(sample)} replacement characters "
            "in the leading sample)"
        )

    acceptable = sum(1 for ch in sample if _is_acceptable_char(ch))
    ratio = acceptable / len(sample)
    if ratio < MIN_ACCEPTABLE_RATIO:
        return (
            f"PDF_NOT_TEXT (only {ratio:.0%} of the leading sample is printable text, "
            f"under the {MIN_ACCEPTABLE_RATIO:.0%} floor)"
        )
    return None


def normalize_text(raw: str) -> str:
    """Canonicalize extracted text so two extractions of one document compare equal.

    NFC because the same Hangul syllable arrives decomposed from one backend and composed
    from another, and a store that holds both cannot match either against a question typed a
    third way. Line endings and trailing whitespace are flattened for the same reason: they
    are the backend's formatting, not the document's content, and they would otherwise change
    the content hash that decides whether a re-ingest is a duplicate.
    """
    text = unicodedata.normalize("NFC", raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or not _is_control(ch))
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _check_pdf_bytes(data: bytes, source_name: str) -> None:
    """Refuse anything that is not a PDF *before* a backend gets to guess at it."""
    if not isinstance(data, (bytes, bytearray)):
        raise KnowledgeBlocked("PDF_INPUT_INVALID", "PDF content must be bytes")
    if not data:
        raise KnowledgeBlocked("PDF_INPUT_EMPTY", f"{source_name}: empty document")
    if len(data) > MAX_PDF_BYTES:
        raise KnowledgeBlocked(
            "PDF_TOO_LARGE",
            f"{source_name}: {len(data)} bytes exceeds the {MAX_PDF_BYTES}-byte ceiling",
        )
    # The header may legitimately sit a few bytes in (some producers emit a BOM or junk
    # prefix), but not arbitrarily far — that is a different file type with a PDF somewhere
    # inside it, which is not the same thing as a PDF.
    if PDF_MAGIC not in bytes(data[:1024]):
        raise KnowledgeBlocked(
            "PDF_NOT_A_PDF",
            f"{source_name}: no %PDF- header in the first 1024 bytes; this is not a PDF",
        )


def _run_backend(name: str, data: bytes) -> tuple[str, int]:
    if name == PDFTOTEXT:
        return _extract_with_pdftotext(data)
    if name == PYPDF:
        return _extract_with_pypdf(data)
    raise KnowledgeBlocked("PDF_BACKEND_UNKNOWN", f"no such PDF backend: {name!r}")


def _pdftotext_binary() -> str | None:
    """The resolved absolute path to ``pdftotext``, or ``None``.

    Resolved every call rather than cached at import: a door is long-lived, and an image
    that gains the binary on a redeploy should not need a restart to notice. The cost is one
    ``PATH`` walk per ingest, against a subprocess that costs milliseconds more.
    """
    return shutil.which(PDFTOTEXT)


def _extract_with_pdftotext(data: bytes) -> tuple[str, int]:
    binary = _pdftotext_binary()
    if binary is None:
        raise FileNotFoundError("pdftotext is not on PATH")

    # Reading stdin and writing stdout ("- -") keeps the document off the filesystem
    # entirely: no temp file to leak, to collide, or to leave behind on a timeout.
    argv = [binary, "-q", "-enc", "UTF-8", "-eol", "unix", "-", "-"]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, absolute path, shell=False
            argv,
            input=bytes(data),
            capture_output=True,
            timeout=PDFTOTEXT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise KnowledgeBlocked(
            "PDF_EXTRACTION_TIMEOUT",
            f"pdftotext exceeded {PDFTOTEXT_TIMEOUT_SECONDS}s; the document is refused rather "
            "than retried, because a door that retries a hang is a slower hang",
        ) from exc

    if completed.returncode == 1:
        # Poppler's own code for "cannot open / not a PDF"; the magic-byte check above
        # already covers the common case, so reaching here means a structurally broken file.
        raise KnowledgeBlocked(
            "PDF_CORRUPT",
            "pdftotext could not open the document: "
            f"{completed.stderr.decode('utf-8', 'replace').strip() or 'no detail'}",
        )
    if completed.returncode == 3:
        raise KnowledgeBlocked(
            "PDF_ENCRYPTED",
            "the document is encrypted; supply a decrypted copy — this runtime holds no "
            "passwords and will not ask for one",
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"pdftotext exited {completed.returncode}: "
            f"{completed.stderr.decode('utf-8', 'replace').strip() or 'no detail'}"
        )

    text = completed.stdout.decode("utf-8", "replace")
    _check_text_size(text)
    return text, text.count(_PAGE_BREAK)


def _pypdf_module():
    try:
        import pypdf  # noqa: PLC0415 - optional backend, probed at call time by design
    except ImportError:
        return None
    return pypdf


def _extract_with_pypdf(data: bytes) -> tuple[str, int]:
    import io  # noqa: PLC0415 - kept next to its only user

    pypdf = _pypdf_module()
    if pypdf is None:
        raise ModuleNotFoundError("pypdf is not installed")

    reader = pypdf.PdfReader(io.BytesIO(bytes(data)))
    if getattr(reader, "is_encrypted", False):
        # pypdf will happily "decrypt" with an empty password and then return blank pages,
        # which would reach the text gate as PDF_NO_TEXT_LAYER and be reported as a scan.
        # Naming it here keeps the diagnosis true.
        raise KnowledgeBlocked(
            "PDF_ENCRYPTED",
            "the document is encrypted; supply a decrypted copy — this runtime holds no "
            "passwords and will not ask for one",
        )

    pages: list[str] = []
    total = 0
    for page in reader.pages:
        extracted = page.extract_text() or ""
        total += len(extracted)
        if total > MAX_TEXT_CHARS:
            raise KnowledgeBlocked(
                "PDF_TEXT_TOO_LARGE",
                f"extracted text exceeds the {MAX_TEXT_CHARS}-character ceiling",
            )
        pages.append(extracted)
    return _PAGE_BREAK.join(pages), len(pages)


def _check_text_size(text: str) -> None:
    if len(text) > MAX_TEXT_CHARS:
        raise KnowledgeBlocked(
            "PDF_TEXT_TOO_LARGE",
            f"extracted text is {len(text)} characters, over the {MAX_TEXT_CHARS} ceiling",
        )


def _is_control(ch: str) -> bool:
    return unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cn")


def _is_acceptable_char(ch: str) -> bool:
    """Whether one character counts as text for :func:`text_gate`'s printable ratio.

    Letters, marks, numbers, punctuation, symbols and separators pass — which is every
    script, not merely Latin. Only the ``C*`` categories (control, format, surrogate,
    private-use, unassigned) fail, plus whitespace which passes explicitly because ``\\n``
    and ``\\t`` are ``Cc``.
    """
    if ch in "\n\t ":
        return True
    return unicodedata.category(ch)[0] in ("L", "M", "N", "P", "S", "Z")
