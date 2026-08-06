"""K1 Knowledge base — durable documents in, evidence-bearing answers out.

Three modules, one direction of travel:

- :mod:`pdf_text` turns a PDF into text **or a typed refusal** — never bytes wearing a
  string's clothes.
- :mod:`index` scores a question against stored text with no dependency, no model and no
  network, behind a seam a real embedding backend can take over later.
- :mod:`store` keeps the documents, and :mod:`service` is the pair of verbs the assistant
  actually calls (``add_document``, ``query``).

The door that exposes those verbs is ``runtime/mvp_runtime/knowledge_bridge.py``, a sibling
of the switch/read/dispatch doors rather than anything new: same transport, same framing,
same error envelope.
"""

from __future__ import annotations
