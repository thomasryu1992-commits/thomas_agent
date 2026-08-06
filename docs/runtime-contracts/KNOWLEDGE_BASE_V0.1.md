# Knowledge Base v0.1 (K1)

A local, per-machine corpus of documents the operator's orchestrator files, and a retrieval
verb that answers questions from it with cited passages.

Owner modules: `runtime/mvp_runtime/knowledge/` (extraction, index, store, service) and
`runtime/mvp_runtime/knowledge_bridge.py` (the door). Record schema:
`schemas/knowledge_document.v0.1.schema.json`.

## Why a third knowledge store exists

The reuse-first rule says check for an existing owner before minting one. Two were candidates
and neither fits, which is the justification this contract exists to record:

| Owner | Holds | Why a filed document is not that |
|---|---|---|
| `working_memory` (R5) | Candidates the **agent proposed** about how to work, on a CANDIDATE→VALIDATED→CORE ladder with a promotion door Thomas controls and a 7-day expiry | A filed document is on no ladder. It is not a claim the runtime made, promoting it would mean nothing, and expiring last month's analysis after a week deletes the corpus this exists to accumulate |
| `operational_knowledge` (v0.1) | **Validated operating truth**, with a review window, confidence, and an environment signature | A stored document asserts none of that. Conflating a source document with a validated conclusion is the exact confusion the review window exists to prevent |

A `knowledge_document.v0.1` record makes no truth claim at all. It says: *this text was
supplied, from this source, at this time, and here is how its text was recovered.*

## Authority

An ingest is ALLOW-tier. Under `governance/GOVERNANCE_POLICY.yaml`,
`memory_learning.working_memory_and_candidate_creation` is ALLOW, and a filed document is
*less* than a candidate — the same effect class as the operator pasting text into a file on
the host, which is the authentication the bridge sockets already rest on. It invokes no
model, reaches no network, creates no permission, and cannot reach the money path.

Being a write, it stops at the kill switch. Being reads, `query` and `stats` do not:

| Verb | Effect | While PAUSED / KILLED |
|---|---|---|
| `add_document` | Append one document | **Refused** — a halt that leaves a side door open for state changes is not a halt |
| `query` | Search, return passages | Answers — `kill_switch.kill_allows: [read_only_status, audit_read]` |
| `stats` | Corpus counts | Answers — same |

There is no delete and no edit verb, and none is planned: supersession happens as a side
effect of re-filing a source, never as a verb a caller can aim. No verb accepts a filesystem
path — a document arrives as content in the frame or it does not arrive, so this door hands
the assistant no read primitive it did not already hold.

## The record

Closed schema, `additionalProperties: false`, validated before every append. Notable fields:

- `document_id` — `kdoc_<20 hex>`, derived from `sha256(text) + source`. Identity, not a
  serial number: re-filing the same text from the same source is the *same document*, which
  is what makes a retry after a client timeout store nothing and return `duplicate: true`.
- `ingested_at_utc` vs `document_date` — when this runtime received it, vs. what it is
  *about*. A daily analysis filed three days late is dated by its subject, not its filing.
- `extraction` — which backend recovered the text and which were tried. Kept because the
  answer to "why does this document read badly" is almost always in here.
- `superseded_by` — set on the old row when a source is re-filed with new content. The store
  is append-only: the sequence of rows for one source *is* its revision history, and
  "what did the 08-05 analysis say before it was corrected" is not answerable by a store that
  rewrites in place.

State lives under `.runtime_governance_state/knowledge/documents.jsonl` — per-machine, never
committed, never baked into the image.

## Extraction

`pdftotext` (poppler-utils, installed in the Dockerfile) then `pypdf` (pure Python), first
success wins. A missing binary is an **environment state**, not an error: it advances to the
next backend and is recorded. Only an empty chain refuses, and it names the environment.

Extraction is not finished when a backend returns a string. It is finished when that string
passes the **text gate**, which refuses output beginning with `%PDF-`, output that is
mostly replacement characters, and output that is mostly non-printable. The gate admits every
script — Hangul, Han and Kana are letters and pass exactly as Latin does. An empty extraction
is reported as `PDF_NO_TEXT_LAYER` (a scan) rather than stored as a document with no content:
"we have that report" and "we have that report's zero characters" are answers that differ
only when it is too late. OCR is out of scope.

## Retrieval

BM25 over a hybrid term space: word terms plus character bigrams, bigrams weighted at 0.4 of
a word. The bigrams are what make it work on Korean — "비트코인" and "비트코인의" are different
word terms, so a word-only index answers nothing to half of every question asked of it.
Question scaffolding (의문사, auxiliaries) is dropped from the query side only.

Relevance is floored on **coverage** — the weighted fraction of the question's terms a passage
matched — not on score. BM25's scale is set by corpus IDF, so an absolute score threshold
rejects everything on a small corpus and nothing on a large one, which is backwards: the new
corpus is the one whose first users conclude the thing does not work.

`query` returns ranked passages, each with its score, coverage, matched terms, source and
ingest time, plus an **extractive** answer whose every sentence appears verbatim in a stored
document. It does not compose prose. The front desk's rule — *deterministic data beats model
narration* — applies with extra force here: the caller on the other side of this door is
itself a capable model, and handing it a paraphrase would mean two models in series with the
first one's compression invisible to the second. Model-composed prose over this evidence is
`dispatch_bridge`'s authority, not this door's.

**Known limits.** No cross-lingual matching: a Korean corpus does not answer an English
question, because lexical retrieval has no shared term space across scripts. No synonymy and
no paraphrase matching. Both are what a dense embedding backend would buy, and
`knowledge/index.py::Retriever` is the seam it would arrive behind — three methods, with
nothing above it changing: not the door's verbs, not the stored records, not the caller.
Adopting one is a governance decision (a `network_access` grant for a hosted embedding API,
or a ~2 GB model layer in the image), which is why it is not in v0.1.

## Deployment

`knowledge-bridge` in `docker-compose.yml`, one more service on the same image, sharing the
state volume and the `bridge/` socket directory. Its own service because an ingest runs a
subprocess and re-indexes the corpus — by far the most expensive request the deployment
serves — and a wedged ingest must not be able to wedge the stop path.

It states its own frame ceiling (12 MB) and read deadline (180 s) rather than widening the
shared console defaults, so a limit raised to carry a document cannot loosen the door that
stops the runtime.
