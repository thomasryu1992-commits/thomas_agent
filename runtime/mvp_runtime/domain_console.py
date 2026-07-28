"""Operator domain console — ask the crypto and prediction stacks what they are doing.

The fifth verb family on the control channel, beside the emergency console (``control``),
approval (``approval``), memory (``memory_console``), task coordination
(``registry_console``) and feedback (``operator_feedback``). Same tokenizer as all of them
(``control.command_verb``) and the same precondition: the R4 identity gate in
``handle_operator_message`` has already run, so only the registered Thomas is here.

The gap this closes: both domains ran on a schedule and reported into the channel when
*they* had something to say (the digest, an alert), and there was no way to ask. Their state
was reachable only by opening a terminal on the host — so the answer to "어제 크립토 어떻게
됐어?" was either a model narrating a store it cannot read, or nothing.

**Every verb here is a read, and the renderers are not new.** Each subcommand is a thin
adapter over the module that already owns that answer — the same text the host CLI prints:

- ``/crypto status`` → ``crypto.dashboard`` (the board)
- ``/crypto readiness`` → ``crypto.live_readiness`` (what stands between here and a live order)
- ``/crypto paper`` → ``crypto.feedback`` (the paper performance report)
- ``/pred report`` → ``predmarket.report`` (the PM1 observation report)

Writing a second rendering of any of them is what this module exists to avoid: two accounts
of the same money would eventually disagree, and the one on the phone is the one read in a
hurry. Deterministic data beats narration — the front desk's own precedent.

**No network, by construction and then by choice.** Every builder above is a pure local read
of `.runtime_governance_state/`, which the operator service already mounts. The one exception
in the underlying modules is opt-in and not taken: the dashboard's `--account` flag is never
passed here, and `live_readiness` opens its single socket only where an account feed is
configured — which on the real deployment the operator service is not, since the crypto feed
env is scheduler-only (`docker-compose.yml`). So these verbs read what the scheduler wrote;
they never ask a venue anything.

**This console cannot trade.** It imports no part of the order path, and the repository's
existing tripwire — the one that keeps autonomous entry points away from the modules that can
send an order — lists this file. The crypto stack can place a real order, and a chat verb is
exactly the surface where "just one more argument" would be most tempting.

(That tripwire is a plain substring scan over this file, so it trips on the module names
themselves appearing anywhere here — prose included. That crudeness is the point: it cannot
be satisfied by an import written a cleverer way, which is why this paragraph names none of
them.)

**Kill switch.** These answer in any runtime mode: ``kill_allows`` covers read-only status,
and an operator staring at a PAUSED runtime most needs to see what it was doing. Nothing here
mutates, so there is no second door to gate. No ledger event either — the ``/status``
precedent, and a correctness point rather than a saving: a read that appends to the log it
reads races its own tail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import timeutil
from .control import command_verb
from .errors import MvpRuntimeError, OperatorBlocked

CRYPTO_COMMAND = "crypto"
PRED_COMMAND = "pred"
COMMANDS = frozenset({CRYPTO_COMMAND, PRED_COMMAND})

# What each verb answers when asked with no argument: the one a person opening the chat
# most likely wants. Never a guess between two — each verb has exactly one obvious default.
DEFAULT_SUBCOMMAND = {CRYPTO_COMMAND: "status", PRED_COMMAND: "report"}

# A Telegram message caps at 4096 units and the channel already chunks past that, but a
# board rendered for an 80-column terminal becomes unreadable long before it becomes
# undeliverable. These renderers are all built for a screen, so nothing is reflowed here —
# what is sent is what the host CLI prints, and drift between the two is the thing this
# module is designed not to have.


def parse_domain_command(text: Any) -> tuple[str, str | None] | None:
    """Classify a domain-console command, or return None if ``text`` is not one.

    Returns ``(verb, subcommand)`` with the verb upper-cased (``CRYPTO``/``PRED``) and the
    subcommand lower-cased, or None for the subcommand when none was given — resolved to the
    verb's default by :func:`apply_domain_command`, which is also the only place that knows
    which subcommands exist.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    head, _, rest = stripped.partition(" ")
    verb = command_verb(head, slash_seen=stripped.startswith("/"))
    if verb not in COMMANDS:
        return None
    argument = rest.strip().lower() or None
    return (verb.upper(), argument)


# --- the subcommands ---------------------------------------------------------
#
# Each one does exactly two things: call the module that owns the answer, and hand back its
# text. A subcommand that started computing something would be the second account of that
# number, which is the failure this module is shaped to prevent.


def _crypto_status(*, now: str, root: Path | None) -> str:
    from .crypto import dashboard

    # `--account` is deliberately NOT passed: that flag is what turns this board into a
    # network call, and a chat verb must not be the thing that opens a socket to a venue.
    return dashboard.render_status_text(dashboard.build_status(root, now=now))


def _crypto_readiness(*, now: str, root: Path | None) -> str:
    from .crypto import live_readiness

    return live_readiness.render_readiness_text(live_readiness.build_readiness(root, now=now))


def _crypto_paper(*, now: str, root: Path | None) -> str:
    from .crypto import feedback

    _report, text = feedback.run_paper_performance_report(now=now, root=root)
    return text


def _pred_report(*, now: str, root: Path | None) -> str:
    from .predmarket import report

    return report.render_pm1_report_text(report.build_pm1_report(now=now, root=root))


_SUBCOMMANDS: dict[str, dict[str, Callable[..., str]]] = {
    CRYPTO_COMMAND: {
        "status": _crypto_status,
        "readiness": _crypto_readiness,
        "paper": _crypto_paper,
    },
    PRED_COMMAND: {
        "report": _pred_report,
    },
}


def usage(verb: str) -> str:
    """The subcommands a verb actually has. Built from the table, so a subcommand cannot be
    advertised that does not exist (or exist without being advertised)."""
    known = " | ".join(sorted(_SUBCOMMANDS[verb]))
    return f"/{verb} <{known}> (기본: {DEFAULT_SUBCOMMAND[verb]})"


def apply_domain_command(
    command: tuple[str, str | None],
    *,
    operator_id: str,
    now: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Execute a parsed domain-console command and return ``{"reply", "action"}``.

    Raises ``OperatorBlocked`` for every refusal so the caller renders one typed REFUSED
    reply. An unreadable store is **reported, never softened into an empty board**: the
    registry-console rule, and it matters more here — a crypto board that renders as "no
    open positions" because a file would not parse is a false statement about money.

    ``operator_id`` is unused and required on purpose: it keeps this applier's signature the
    same shape as every other console's, so the channel's dispatch reads uniformly and a
    future verb that *does* need the identity has it without a signature change.
    """
    verb, subcommand = command
    verb = verb.lower()
    stamp = now or timeutil.utc_now_iso()

    if verb not in _SUBCOMMANDS:      # pragma: no cover — parse_domain_command bounds this
        raise OperatorBlocked("UNKNOWN_DOMAIN_COMMAND", f"알 수 없는 명령입니다: /{verb}")

    name = subcommand or DEFAULT_SUBCOMMAND[verb]
    handler = _SUBCOMMANDS[verb].get(name)
    if handler is None:
        # Never guessed at. A mistyped subcommand that fell through to the default would
        # answer a question he did not ask, with a board that looks like an answer.
        raise OperatorBlocked(
            "UNKNOWN_DOMAIN_SUBCOMMAND",
            f"'{name}'은(는) 없는 항목입니다 — {usage(verb)}",
        )

    try:
        text = handler(now=stamp, root=repo_root)
    except MvpRuntimeError as exc:
        raise OperatorBlocked(
            exc.reason_code,
            f"{verb} {name} 조회에 실패했습니다 ({exc.reason_code}) — 상태를 확인할 수 없습니다.",
        ) from exc

    return {"reply": text, "action": f"{verb.upper()}_{name.upper()}"}
