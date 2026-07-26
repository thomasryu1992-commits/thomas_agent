"""The operator-visible authorization notice, and the property that keeps it complete.

`cli_common.gate_banners` exists so that no gated capability can be selected without the
operator being told. It had no test at all — the module written to prevent a silent missing
notice had nothing asserting a notice appears — and it was in fact incomplete: it took one
named parameter per role, so the property held for the four roles already thought of.
`scripts/place_canary_order.py`, the only path in this repository that can move real money,
printed nothing; `operator_cli` hand-wrote two more notices beside the call, one of which
never said SAFETY_GATE at all.

What is pinned here is the *general* property rather than four specific strings: a capable
implementation passed under **any** kwarg name announces itself, and an inert one stays
silent. That is what makes the fifth capability safe by construction instead of by memory.
"""

from __future__ import annotations

from runtime.mvp_runtime.cli_common import gate_banners


class _Impl:
    """A stand-in declaring exactly the capability attributes the real ones declare."""

    def __init__(self, *, network_egress=False, filesystem_write=False, model_id=None):
        self.network_egress = network_egress
        self.filesystem_write = filesystem_write
        if model_id is not None:
            self.model_id = model_id


def _banners(capsys, **kwargs) -> list[str]:
    gate_banners(**kwargs)
    return [line for line in capsys.readouterr().err.splitlines() if line]


def test_an_inert_implementation_is_silent(capsys):
    """Every mock declares both capabilities False; a default run must stay quiet, or the
    notice becomes noise the operator learns to scroll past."""
    assert _banners(capsys, provider=_Impl(model_id="mock-model")) == []


def test_a_capable_implementation_announces_itself_under_any_name(capsys):
    """The property the role-keyed signature could not provide.

    `live_order_adapter` is not a name `gate_banners` knows, and that is the point — the
    adapter that can send a real order was the capability nobody remembered to add a
    parameter for.
    """
    lines = _banners(capsys, live_order_adapter=_Impl(network_egress=True))
    assert lines == ["SAFETY_GATE: live order adapter authorized (network_access)"]


def test_a_model_invoking_capability_names_its_model_and_both_flags(capsys):
    lines = _banners(capsys, analysis_provider=_Impl(network_egress=True, model_id="gemini-x"))
    assert lines == [
        "SAFETY_GATE: analysis provider authorized "
        "(gemini-x; model_invocation, network_access)"
    ]


def test_model_invocation_is_not_claimed_for_an_inert_model_holder(capsys):
    """`model_id` alone must not produce a notice: a MockProvider has one and reaches nothing.
    The flag is only meaningful once something capable has already been established."""
    assert _banners(capsys, analysis_provider=_Impl(model_id="mock-model")) == []


def test_a_disk_writing_capability_is_announced(capsys):
    lines = _banners(capsys, workspace_writer=_Impl(filesystem_write=True))
    assert lines == ["SAFETY_GATE: workspace writer authorized (filesystem_write)"]


def test_none_is_skipped_without_a_guard_at_the_call_site(capsys):
    """An optional capability that was not selected passes None; requiring the caller to
    guard is how a notice gets dropped during a refactor."""
    assert _banners(capsys, frontdesk_provider=None) == []


def test_every_selected_capability_gets_its_own_line(capsys):
    lines = _banners(
        capsys,
        operator_channel=_Impl(network_egress=True),
        analysis_provider=_Impl(network_egress=True, model_id="gemini-x"),
        search_tool=_Impl(network_egress=True),
        workspace_writer=_Impl(filesystem_write=True),
        mock_thing=_Impl(),
    )
    assert len(lines) == 4
    assert all(line.startswith("SAFETY_GATE: ") for line in lines)


# --- every entry point reconfigures stdio ------------------------------------------------

import re                                                             # noqa: E402
from pathlib import Path                                              # noqa: E402

import pytest                                                         # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = REPO_ROOT / "runtime"

# `runtime/read_only_kernel/` is the frozen replay kernel — CLAUDE.md forbids modifying it,
# so its CLI is named here rather than silently skipped. It renders ASCII only.
EXEMPT = {"runtime/read_only_kernel/cli.py"}

ENTRY_POINTS = sorted(
    p for p in RUNTIME.rglob("*.py")
    if re.search(r"^def main\(", p.read_text(encoding="utf-8"), re.M)
    and p.relative_to(REPO_ROOT).as_posix() not in EXEMPT
)


def test_there_are_entry_points_to_check():
    assert ENTRY_POINTS, "no module with a main() under runtime/"


@pytest.mark.parametrize("path", ENTRY_POINTS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_an_entry_point_reconfigures_stdio_to_utf8(path):
    """A CLI that writes non-ASCII dies on a Windows cp949 console without this call.

    Six entry points were missing it, and it was not theoretical: the output of
    `python -m runtime.mvp_runtime.crypto.dashboard` is genuinely not cp949-encodable (the
    em dash alone fails), so that board raised `UnicodeEncodeError` instead of printing.
    `console_cli` — the emergency pause/kill console — was among the six.

    Asserted for every entry point rather than the ones that happen to print non-ASCII
    today: which strings are ASCII is not a property anyone maintains, and a single Korean
    message added later would reintroduce the bug silently. `force_utf8_io` is a no-op where
    stdio is already UTF-8, so there is no cost to calling it everywhere.
    """
    assert "force_utf8_io" in path.read_text(encoding="utf-8"), (
        f"{path.relative_to(REPO_ROOT)} defines main() but never calls force_utf8_io(); "
        f"non-ASCII output will raise UnicodeEncodeError on a cp949 console"
    )
