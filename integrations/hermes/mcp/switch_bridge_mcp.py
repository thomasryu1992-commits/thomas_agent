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
        if payload.get("command") == "disable":
            armed_note = (
                " This stop ALSO disarmed live entries, and it dropped the scheduler's due "
                "cycles: while stopped, the crypto cycle does not run, so open positions are "
                "NOT being settled or protected by the runtime — only whatever protective "
                "order already rests at the venue. Do not say positions will close on their "
                "own. The disarm is sticky: resume_runtime_only brings the runtime back with "
                "trading still off, and re-arming needs a separate start_trading approval."
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
