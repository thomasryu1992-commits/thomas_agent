"""The one reader for a number that came from outside this process.

Eight crypto modules each carried a four-line ``_f`` in three different spellings. They
were equivalent, but nothing said so, and the *rule* they encode — an unparseable value is
the caller's stated default, meaning **unknown** — was stated eight times and could have
been changed in seven of them.

The first test is the one that mattered when they were merged: every input on which the
three old spellings could conceivably have disagreed, checked against the survivor. The
rest pin the rule itself, because callers depend on the *default*, not on an exception:
``SymbolFilters.valid`` reads a 0.0 increment as unusable, and the live-order guard reads
an unconfigured cap as a block. A coercer that raised, or that invented a usable number,
would break both.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.coerce import as_float


def _short_circuit_on_empty(value, default=0.0):
    """The digest/feedback spelling: skip float() for None and ""."""
    try:
        return float(value) if value not in {None, ""} else default
    except (TypeError, ValueError):
        return default


def _short_circuit_on_none(value, default=0.0):
    """The robustness spelling: skip float() for None only."""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


# Every case the three spellings treat differently on the way to their answer: the
# unhashable ones (``in`` on a set raises TypeError), the falsy-but-numeric ones, and the
# strings that only float() can judge.
_INPUTS = [
    None, "", "abc", "1.5", 1.5, 0, 0.0, False, True, [], {}, (1,),
    "1e3", " 2 ", "nan", "inf", "-0", b"3", object(),
]


@pytest.mark.parametrize("value", _INPUTS)
@pytest.mark.parametrize("default", [0.0, -1.0])
def test_the_merged_reader_answers_exactly_as_the_spellings_it_replaced(value, default):
    assert repr(as_float(value, default)) == repr(_short_circuit_on_empty(value, default))
    assert repr(as_float(value, default)) == repr(_short_circuit_on_none(value, default))


def test_a_venue_string_is_a_number():
    """Venue payloads send every figure as a string; that is the normal case, not an error."""
    assert as_float("0.001") == 0.001
    assert as_float("60000") == 60000.0


@pytest.mark.parametrize("value", [None, "", "abc", [], object()])
def test_a_value_that_did_not_arrive_takes_the_default_and_never_raises(value):
    """A malformed field in one record must not take down the read of every other one."""
    assert as_float(value) == 0.0
    assert as_float(value, -1.0) == -1.0


def test_zero_is_the_unknown_marker_callers_rely_on():
    """The default is 0.0 because that is what every caller's own validity check refuses:
    ``SymbolFilters.valid`` treats a 0.0 increment as unusable rather than unrestricted, and
    the live guard treats a 0.0 cap as unconfigured and blocks. Coercing to something
    'reasonable' here would defeat both."""
    from runtime.mvp_runtime.crypto.live_sizing import SymbolFilters

    unusable = SymbolFilters(
        step_size=as_float("not a number"), min_qty=0.001, min_notional=5.0, tick_size=0.1
    )
    assert unusable.step_size == 0.0
    assert unusable.valid() is False
