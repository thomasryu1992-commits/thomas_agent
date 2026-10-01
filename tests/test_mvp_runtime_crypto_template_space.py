"""The template space is its own module, and the factory still offers it under the old names (crypto
refactor plan PR-09; ``docs/proposals/CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md`` §L-4).

The space moved out of ``factory`` whole: the validator's bounds, the feature vocabulary, the template
library and ``validate_strategy``. What they decide is pinned where it always was, in
``test_mvp_runtime_crypto_factory.py`` and the per-family files, which still reach them through
``factory``. This file pins what the move itself has to keep true: every name of the space's that
``factory`` still offers is the same object there. Which names it still offers is pinned by
``test_mvp_runtime_crypto_reexport_roster.py`` (PR-16).

That the space imports nothing from ``factory`` is pinned by the layer test's cycle rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import factory, template_space
from tests.test_mvp_runtime_crypto_backtest import _defined


def test_every_name_of_the_template_spaces_that_factory_offers_is_the_same_object():
    names = _defined(ast.parse(Path(template_space.__file__).read_text(encoding="utf-8")))
    assert {"TEMPLATES", "templates_for_timeframe", "validate_strategy", "known_features", "ParamSpec",
            "StrategyTemplate", "NUMERIC_FEATURES", "STOP_ATR_RANGE", "RETIRED_FAMILIES",
            "_FEATURE_FEED"} <= set(names), "the scan lost the space's own names: it broke, not the module"
    offered = [n for n in names if n in vars(factory)]
    assert {"TEMPLATES", "validate_strategy"} <= set(offered)
    different = sorted(n for n in offered if getattr(factory, n) is not getattr(template_space, n))
    assert different == [], f"factory holds a different object for: {different}"
