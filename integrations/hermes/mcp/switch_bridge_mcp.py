"""stdio MCP shim — exposes the Thomas runtime's trading-switch door to Hermes (door API v2).

Hermes spawns this as a subprocess and speaks MCP over stdio. It holds no authority of its
own: every call is forwarded to a unix socket served by the Thomas runtime
(`runtime/mvp_runtime/switch_bridge.py`), which is where the verb allowlist, the domain
allowlist, the approval requirement and the audit trail actually live.

That split is the point. This file runs inside the assistant's container, which is the less
trusted side — it can be edited, it reads whatever the model was told, and it is exactly the
place a prompt injection would land. So it is deliberately dumb. It cannot start trading by
asking harder: `start_trading` without an approval id returns an APPROVAL_REQUIRED answer and
changes nothing, and the only thing that turns that into a resume is an approval Thomas grants
on his own authenticated Telegram control channel. Nothing typed here, and nothing said to the
model, can produce that approval.

Stopping is free and immediate, because an emergency control you must first get signed is not
an emergency control.

v2 (2026-09-04): frames carry `proto: 2` and `client_id` (attribution, never authority); an
`enable` carries a `request_id`, so a retry of the very same call after a timeout is answered
with the first application (`replayed`) instead of spending a second approval. Since PR11 the
approval ask is ALSO mirrored into the assistant's window — a copy, labelled as one — and the
decision still happens only on the control bot.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import thomas_door_client as door

mcp = FastMCP("thomas-switch")

_DOOR = "switch"

# The two bots, by id, as constants rather than prose scattered through messages. The control
# bot is the ONLY reader of `/approve`; the assistant's bot is the window this conversation is
# in. Alerts and (since PR11) a copy of every approval ask arrive in the assistant's window,
# which is exactly why the two get confused.
CONTROL_BOT_ID = "8732952898"
ASSISTANT_BOT_ID = "8950942278"


# What a stop leaves behind. `kill` and `pause` stop the runtime, and with it the crypto cycle.
_STOP_NOTE = (
    " This stop ALSO disarmed live entries, and it dropped the scheduler's due "
    "cycles: while stopped, the crypto cycle does not run, so open positions are "
    "NOT being settled or protected by the runtime — only whatever protective "
    "order already rests at the venue. Do not say positions will close on their "
    "own. The disarm is sticky: resume_runtime_only brings the runtime back with "
    "trading still off, and re-arming needs a separate start_trading approval."
)
# The modes `halt_trading` sends. A halt stops entries only and never stops the runtime.
_HALT_MODES = frozenset({"soft", "hard"})
# What a stopped runtime does not do, said wherever a disable leaves one in effect.
_STOPPED = ("While stopped the crypto cycle does not run, so open positions are NOT being settled or "
            "protected by the runtime (only whatever protective order already rests at the venue).")


def _halt_note(src: dict, *, level: str) -> str:
    """What a halt that changed the state left behind (shim 2.13). The stop note above is false for it:
    on an ACTIVE runtime a halt leaves it ACTIVE, so positions keep being managed; on a stopped runtime
    this door cannot release the stop, and the halt is recorded under it (PR6d). ``level`` is the one
    sent, which is the one applied when the state changed."""
    if src.get("mode") == "ACTIVE":
        return (f" New live entries are refused under the {level} halt and the runtime stays ACTIVE, so open "
                "positions keep being settled, protected, time-exited and reconciled — say that; do not say "
                "trading or position management stopped."
                + (" At HARD the order adapter also refuses every order that could add exposure; closes and "
                   "protective orders still go out." if level == "HARD" else "")
                + " The halt is sticky: resume_runtime_only keeps it, and lifting it needs Thomas's "
                "start_trading approval.")
    return (f" The runtime is still {src.get('mode')} — this tool cannot release a stop. {_STOPPED} The "
            f"{level} halt is recorded under the stop, so resume_runtime_only comes back to that halt, not "
            "to live entries.")


def _disable_text(src: dict, *, payload: dict, reply: str) -> str:
    """A stop's or a halt's answer (review of #915). A disable spends no grant, so the enable path's
    grant-scope line and trailer are not about it; and one that changed nothing must not open with
    "applied". The runtime's own reply comes last, after what the model must say."""
    mode = src.get("mode")
    if src.get("changed"):
        head = (f"DONE: {src.get('action')} applied to {src.get('domain')}. Runtime mode is now {mode} "
                f"(changed=True, actor={src.get('actor')}).")
        note = (_halt_note(src, level=str(payload.get("mode")).upper()) if payload.get("mode") in _HALT_MODES
                else _STOP_NOTE)
    else:
        head = (f"NOT CHANGED: {src.get('action')} left {src.get('domain')} as it was. Runtime mode is "
                f"{mode} (changed=False, actor={src.get('actor')}).")
        note = (" Nothing changed. The runtime's reply below says why and what is in effect; call "
                "trading_switch_status before saying whether live entries are halted."
                + (f" The runtime is {mode}: a stop is in effect. {_STOPPED}" if mode != "ACTIVE" else ""))
    return f"{head}{note}\nRuntime reply: {reply}"


def _emergency_close_text(src: dict, *, replayed: bool) -> str:
    """The emergency-close ask (shim 2.14, crypto PR6e; Thomas decision 49: the assistant only asks).
    Two steps follow and neither is the model's: Thomas approves on the control bot, and the operator
    spends the approval in the scheduler container. Nothing has closed when this is read."""
    positions = src.get("positions") or []
    listing = ", ".join(f"{p.get('symbol')} {p.get('direction')} {p.get('quantity')}"
                        for p in positions if isinstance(p, dict)) or "(not in this reply)"
    head = ("NOT DONE (REPLAYED) — this request_id already minted the emergency-close ask; no new ask "
            "was minted, nothing has been closed and no order was sent." if replayed else
            "NOT DONE — nothing has been closed and no order was sent. This minted Thomas's approval "
            "ask for the emergency close.")
    return (
        f"{head}\n"
        f"  approval id : {src.get('approval_id')}\n"
        f"  expires at  : {src.get('expires_at')}\n"
        f"  would close : {listing} — every booked live position, at market, reduceOnly\n"
        + (f"  bound to    : {src.get('halt')}\n" if src.get("halt") else "")
        + "Two steps follow, and neither is yours:\n"
        f"  1) Thomas approves on the CONTROL bot ({CONTROL_BOT_ID}): {src.get('approve_with')}\n"
        "  2) then the OPERATOR runs, in the scheduler container:\n"
        f"     {src.get('confirm_with')}\n"
        "You cannot approve it and you cannot confirm it. Never say positions are closing or closed. "
        "Nothing moves until both steps are done, and the confirm refuses, spending nothing, if the HARD "
        "halt it was asked under is no longer the one in effect: any control change voids the ask, and "
        "then you ask again. Convert `expires at` into the minutes remaining and say it; the ask dies 15 "
        "minutes after it is minted. approval_status(<id>) shows whether Thomas has answered."
        + (" If it reads EXPIRED, call request_emergency_close again with a NEW request_id."
           if replayed else "")
    )


def _render(answer: door.Answer, *, payload: dict, retry_tool: str, request_id: str | None) -> str:
    if answer.failure:
        return f"{answer.failure_text()} Nothing was changed."
    if answer.ok:
        if payload.get("command") == "status":
            return door.stamp(
                f"STATUS ({answer.get('domain')}): runtime mode is {answer.get('mode')}. "
                f"Nothing was changed.\n{answer.reply}"
            )
        # A replayed enable answers from the RECORD: the first application's outcome rides in
        # `data` (and `outcome`), not in the top-level keys a fresh reply has. Measured on the
        # 2026-09-04 drill: reading the top level rendered "DONE: None applied to None".
        src = answer.data if answer.replayed else (answer.frame or {})
        if answer.replayed and payload.get("command") == "emergency_close":
            return _emergency_close_text(src, replayed=True)
        if answer.replayed and ("approve_with" in src or "expires_at" in src):
            # The ask itself was the effect the first time; nothing new is minted on a repeat.
            return (
                "NOT DONE (REPLAYED) — this request_id already minted an approval ask; no new ask "
                "was minted and nothing has been changed.\n"
                f"  approval id : {src.get('approval_id')}\n"
                f"  expires at  : {src.get('expires_at')}\n"
                f"  domain      : {src.get('domain')}\n"
                f"  scope       : {src.get('scope') or 'trading'}\n"
                f"Thomas decides on the CONTROL bot ({CONTROL_BOT_ID}): {src.get('approve_with')}\n"
                "Check approval_status(<id>) first: if it reads EXPIRED, the id is dead — call "
                f"{retry_tool} again with a NEW request_id to mint a fresh ask."
            )
        if payload.get("command") == "disable" and not answer.replayed:
            return _disable_text(src, payload=payload, reply=answer.reply)
        armed = src.get("trading_armed")
        spent_scope = src.get("scope")
        armed_note = ""
        if armed is True and spent_scope == "runtime":
            armed_note = (
                " Live trade entries read ARMED — but THIS CALL DID NOT ARM THEM. The grant "
                "Thomas signed was runtime-only, which never arms; it preserves whatever the "
                "arm already was, and it was already armed. Do NOT say this call started, "
                "restarted, re-enabled or resumed live trading."
            )
        elif armed is True:
            armed_note = (
                " Live trade entries are ARMED after this call: the runtime can open real "
                "positions. This is the state AFTER the command — report it as the state, "
                "not as something you made happen."
            )
        elif armed is False:
            armed_note = (
                " Live trade entries remain DISARMED — this did NOT restart trading. Open "
                "positions still close on their own. Say this plainly; do not report that "
                "trading is running."
            )
        replay_note = (
            " (REPLAYED: this request_id was already applied earlier — the door did not apply "
            "it again; what follows is the state from that first application.)"
            if answer.replayed else ""
        )
        changed = "n/a (replay)" if answer.replayed else src.get("changed")
        actor = "assistant_bridge (first application)" if answer.replayed else src.get("actor")
        return (
            f"DONE: {src.get('action')} applied to {src.get('domain')}. Runtime mode is now "
            f"{src.get('mode')} (changed={changed}, "
            f"actor={actor}, grant scope={spent_scope or 'unknown'})."
            f"{replay_note}{armed_note} {answer.reply}\n"
            "Report the SCOPE, not the tool name — the effect comes from the grant Thomas "
            "signed, not from which tool you called. scope=runtime means live trading did "
            "not restart. Also note `changed=True` is emitted for every resume, including one "
            "that left mode and arm exactly as they were; it does not prove anything moved."
        )

    if answer.reason_code == "APPROVAL_REQUIRED" and payload.get("command") == "emergency_close":
        return _emergency_close_text(answer.frame or {}, replayed=False)
    if answer.reason_code == "APPROVAL_REQUIRED":
        scope = answer.get("scope")
        what = ("resume the runtime WITHOUT re-arming live trading" if scope == "runtime"
                else "restart trading")
        return (
            f"NOT DONE — Thomas's approval is required to {what}, and nothing has been changed.\n"
            f"  approval id : {answer.get('approval_id')}\n"
            f"  expires at  : {answer.get('expires_at')}\n"
            f"  domain      : {answer.get('domain')}\n"
            f"  scope       : {scope or 'trading'}\n"
            f"  runtime now : {answer.get('mode')}\n"
            f"  would clear : {answer.get('clears')}\n"
            "Read those last two before you describe the current state. NOTHING CHANGED is "
            "not the same as STOPPED: an ask can be minted while the runtime is already "
            "ACTIVE, and then `would clear` says so outright. Never tell Thomas the runtime "
            "is stopped unless `runtime now` reads KILLED or PAUSED — inventing a stop puts "
            "him under a 15-minute clock to sign something he did not need.\n"
            "Tell Thomas, in his own words, to send this on his Telegram CONTROL channel:\n"
            f"  {answer.get('approve_with')}\n"
            f"State WHICH window, every time: the control channel is Thomas's operator bot "
            f"(id {CONTROL_BOT_ID}). This conversation is bot {ASSISTANT_BOT_ID}. A copy of "
            "this ask arrives HERE too, labelled [알림 사본] — the notice comes to this window, "
            f"the decision does not: an /approve typed here reaches nobody. Use "
            "approval_status(<id>) to see whether he has answered. Convert `expires at` into "
            "the minutes remaining and say it: the ask dies 15 minutes after it is minted.\n"
            f"Then call {retry_tool} again WITH that approval id and request_id EMPTY — a fresh "
            "one is minted for the spend."
            + (f" This ask's request_id was \"{request_id}\", and it belongs to the ask alone: "
               "sent alongside an approval id it is a DIFFERENT request to the door (the "
               "fingerprint covers the whole frame), which answers REQUEST_ID_REUSED and spends "
               "nothing. Reuse it only to retry THIS ask unchanged; reuse the spend's own id only "
               "to retry the spend unchanged — that is what keeps one approval from being spent "
               "twice." if request_id else "")
            + "\nYou cannot approve this yourself and must never imply that you did, or that "
            "anything has started."
        )
    if answer.reason_code == door.REQUEST_IN_FLIGHT:
        return (
            "NOT DONE — the same request_id is still being applied by an earlier call. "
            "Nothing new was changed. Call trading_switch_status, then retry with the same "
            "request_id if the state has not moved."
        )
    return answer.refused_text(suffix=" Nothing was changed.")


def _ask(payload: dict, *, retry_tool: str = "start_trading", request_id: str | None = None) -> str:
    answer = door.ask(_DOOR, payload, request_id=request_id)
    return _render(answer, payload=payload, retry_tool=retry_tool, request_id=request_id)


@mcp.tool()
def trading_switch_status(domain: str = "crypto") -> str:
    """Whether the runtime is allowed to execute (trading on/off). Read-only, writes nothing.
    Call it before saying anything about whether trading is running."""
    return _ask({"command": "status", "domain": domain})


@mcp.tool()
def stop_trading(reason: str, domain: str = "crypto") -> str:
    """Immediately stop the runtime from starting new work, including new trade entries. No
    approval; applying it twice is harmless. Only when Thomas asks — never on your own
    judgement. A stop also DISARMS live entries (sticky: a later resume_runtime_only leaves
    trading off) and while stopped the crypto cycle does not run at all, so open positions
    are NOT settled or protected by the runtime — say so; never say they close on their own."""
    return _ask({"command": "disable", "mode": "kill", "reason": reason, "domain": domain})


@mcp.tool()
def pause_trading(reason: str, domain: str = "crypto") -> str:
    """Pause the runtime — same effect as stop_trading on what runs (the crypto cycle stops
    entirely and live entries are disarmed, sticky); it only reads softer in the ledger.
    Only when Thomas asks."""
    return _ask({"command": "disable", "mode": "pause", "reason": reason, "domain": domain})


@mcp.tool()
def halt_trading(reason: str, hard: bool = False, domain: str = "crypto") -> str:
    """Halt NEW live entries and keep managing open positions. On an ACTIVE runtime it stays ACTIVE, so
    open positions keep being settled, protected, time-exited and reconciled; prefer it to stop_trading
    when the point is to stop opening positions. No approval, applied at once: grade B in SOUL, like
    the stops (on evidence that a loss is in progress, or when Thomas asks), and say at once why you
    pressed it. Soft is the default. hard=True also has the order adapter refuse every order that could
    add exposure, the signed testnet rehearsal included; closes and protective orders still go out.
    From here it only tightens: a soft halt never loosens a hard one, and on a stopped runtime
    (KILLED/PAUSED) it cannot release the stop. At most it records the halt under the stop (not under a
    stop derived by failing closed, nor under one that already has the same or a tighter halt). Lifting
    a halt needs Thomas's start_trading approval; resume_runtime_only keeps it. Like any control change,
    it voids a pending start_trading/resume ask (STOP_CHANGED)."""
    return _ask({"command": "disable", "mode": "hard" if hard else "soft", "reason": reason, "domain": domain})


@mcp.tool()
def request_emergency_close(reason: str, domain: str = "crypto", request_id: str = "") -> str:
    """ASK Thomas for the emergency close: every booked live position closed at market, reduceOnly.
    You can only ask. This sends no order and closes nothing: it mints one approval Thomas answers on
    the CONTROL bot, and then the operator runs the close in the scheduler container. It needs the
    HARD halt with the runtime ACTIVE (halt_trading with hard=True first) and refuses otherwise. Only
    when Thomas asks for it. It refuses by name until the governance policy grants it. `request_id`:
    leave it EMPTY; pass one back only to retry this same call after a timeout or an unclear reply,
    which is what stops a second ask being minted."""
    rid = request_id.strip() or door.new_request_id()
    return _ask({"command": "emergency_close", "reason": reason, "domain": domain},
                retry_tool="request_emergency_close", request_id=rid)


@mcp.tool()
def start_trading(reason: str, approval_id: str = "", domain: str = "crypto", request_id: str = "") -> str:
    """Ask to restart TRADING (re-arm live entries), or complete a restart Thomas approved.
    Without approval_id: changes nothing, mints an ask, returns the id and the exact
    /approve command for the CONTROL bot. With approval_id: spends that approval. You cannot
    approve it yourself; APPROVAL_REQUIRED means trading is still stopped. Only when Thomas
    asks for TRADING specifically. `request_id`: leave it EMPTY, including for the call that
    spends an approval — the ask's id is not the spend's. Pass one back only to retry a call
    whose arguments are identical (a timeout, an unclear reply), which is what stops a second
    approval being spent."""
    payload: dict = {"command": "enable", "reason": reason, "domain": domain}
    if approval_id.strip():
        payload["approval_id"] = approval_id.strip()
    rid = request_id.strip() or door.new_request_id()
    return _ask(payload, retry_tool="start_trading", request_id=rid)


@mcp.tool()
def resume_runtime_only(reason: str, approval_id: str = "", domain: str = "crypto", request_id: str = "") -> str:
    """Ask to restart the runtime WITHOUT re-arming live trading, or complete such a restart.
    Prefer this over start_trading whenever Thomas has not asked for TRADING to restart: it
    brings back analysis, research, memory, scheduled work and your own tasks, and leaves
    live entries DISARMED (report that line as-is). Same two-step shape and the same wall as
    start_trading; the approval Thomas signs decides the effect. `request_id` as there."""
    payload: dict = {"command": "enable", "reason": reason, "domain": domain}
    if approval_id.strip():
        # `scope` is deliberately NOT sent beside an approval id: what an approval authorizes
        # is fixed when it is minted, and the runtime refuses the two together.
        payload["approval_id"] = approval_id.strip()
    else:
        payload["scope"] = "runtime"
    rid = request_id.strip() or door.new_request_id()
    return _ask(payload, retry_tool="resume_runtime_only", request_id=rid)


if __name__ == "__main__":
    mcp.run()
