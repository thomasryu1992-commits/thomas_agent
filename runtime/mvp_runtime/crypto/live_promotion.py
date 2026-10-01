"""The old name of :mod:`live_evidence` (crypto refactor plan L-2.3, 2026-10-01).

``live_evidence`` is the live evidence board; it was named ``live_promotion`` while a canary count
gated promotion, and that gate went on 2026-09-15 (PR1r). This file keeps the old name working:

- imported, it **is** ``live_evidence``. ``sys.modules`` maps the old name to the same module object,
  so a read or a patch through either name reaches the same functions, and nothing can drift;
- run as ``python -m runtime.mvp_runtime.crypto.live_promotion``, it runs the board.

New code imports ``live_evidence``.
"""

from __future__ import annotations

import sys

from . import live_evidence

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(live_evidence.main())

sys.modules[__name__] = live_evidence
