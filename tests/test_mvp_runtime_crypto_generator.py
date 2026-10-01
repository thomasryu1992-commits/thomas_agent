"""The seeded generator is its own module, and the factory still offers it under the old names (crypto
refactor plan PR-10; ``docs/proposals/CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md`` §L-4).

The generator moved out of ``factory`` whole: its knobs, the probe ceiling, ``mutate_params``, the
elite centres and ``generate_batch``. What it draws is pinned where it always was, in
``test_mvp_runtime_crypto_factory.py`` and ``test_mvp_runtime_crypto_elite_search_centre.py``. This
file pins what the move itself has to keep true: every name of the generator's that ``factory`` still
offers is the same object there. Which names it still offers is pinned by
``test_mvp_runtime_crypto_reexport_roster.py`` (PR-16).

That the generator imports nothing from ``factory`` is pinned by the layer test's cycle rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

from runtime.mvp_runtime.crypto import factory, generator
from tests.test_mvp_runtime_crypto_backtest import _defined


def test_every_name_of_the_generators_that_factory_offers_is_the_same_object():
    names = _defined(ast.parse(Path(generator.__file__).read_text(encoding="utf-8")))
    assert {"generate_batch", "mutate_params", "elite_centres", "build_spec_dict", "DEFAULT_BATCH_SIZE",
            "cohort_probe_stop_ceiling", "_rotation_offset"} <= set(names), (
        "the scan lost the generator's own names: it broke, not the module")
    offered = [n for n in names if n in vars(factory)]
    assert {"generate_batch", "mutate_params"} <= set(offered)
    different = sorted(n for n in offered if getattr(factory, n) is not getattr(generator, n))
    assert different == [], f"factory holds a different object for: {different}"
