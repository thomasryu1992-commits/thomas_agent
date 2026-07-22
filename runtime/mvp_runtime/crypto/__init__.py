"""Crypto Pipeline (C-phases) — governed port of the standalone crypto_AI_System.

Design contract: ``docs/runtime-contracts/CRYPTO_PIPELINE_V0.1.md``. The five source
"agents" become governed pipeline stages inside one Task; every behavior enters at its
effect tier behind an existing chokepoint. C2 delivers market-data collection
(``market_data``) — INTERNAL_READ / ALLOW behind the Safety-Flag Gate, degrading (never
blocking) on backend failure, the R3 search-tool precedent applied to exchange data.
"""

from __future__ import annotations
