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

Two readers, because callers need two different answers to "it did not arrive":

- :func:`as_float` takes the caller's default. Use it where the default *is* the meaning —
  0.0 as "unknown", which every downstream validity check refuses.
- :func:`as_optional_float` returns ``None``. Use it where absent and zero are different
  facts and conflating them would be a lie: a missing fill price is not a free trade, and
  a market with no bid is not a market bid at zero.

It lives here rather than under ``crypto/`` because nothing about it is crypto-specific —
``predmarket`` reads venue numbers with exactly the same rule.
"""

from __future__ import annotations

from typing import Any

__all__ = ["as_float", "as_optional_float"]


def as_float(value: Any, default: float = 0.0) -> float:
    """``value`` as a float, or ``default`` when it is missing or malformed.

    ``None``, ``""``, a non-numeric string and an unhashable object all take the default:
    they are all "this field did not arrive", and the caller's default is what that means
    in its context. A caller that must distinguish *absent* from *zero* wants
    :func:`as_optional_float` instead.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_optional_float(value: Any) -> float | None:
    """``value`` as a float, or ``None`` when it is missing or malformed. Never a default.

    For the fields where zero is a real, different fact. ``live_leg`` computes realized P&L
    from fill figures that must never read as 0.0 (that would be a free trade), and a
    prediction market with no resting bid is not a market bid at zero — it is a market
    nobody is quoting, which is a reason to skip it, not to record a price of 0.

    ``bool`` is rejected on purpose: ``float(True)`` is 1.0, and a boolean arriving where a
    price belongs is a malformed payload, not the number one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
