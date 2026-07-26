"""Reading a number that came from outside this process.

Venue payloads send numbers as strings, stored records survive a schema change, and an
imported history can carry a null where a float belongs. Eight modules each grew their own
four-line ``_f`` for this — three spellings of the same behaviour, which meant the *rule*
they encode was stated eight times and could be changed in seven places and forgotten in
the eighth.

The rule, once:

    A value that will not parse is the caller's stated ``default`` — which every caller
    passes (or leaves at ``0.0``) meaning **unknown**, never *unrestricted* and never *zero
    exposure*. ``SymbolFilters.valid`` reads a 0.0 increment as unusable; the live guard
    reads an unknown cap as unconfigured and blocks. Coercion here must therefore never
    invent a usable number, and it never raises: a malformed field in one record must not
    take down the read of every other one.
"""

from __future__ import annotations

from typing import Any

__all__ = ["as_float"]


def as_float(value: Any, default: float = 0.0) -> float:
    """``value`` as a float, or ``default`` when it is missing or malformed.

    ``None``, ``""``, a non-numeric string and an unhashable object all take the default:
    they are all "this field did not arrive", and the caller's default is what that means
    in its context. Deliberately not ``float | None`` — a caller that must distinguish
    *absent* from *zero* (``live_execution._fill_facts``: a missing fill price must never
    read as a free trade) needs its own reader and keeps one.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
