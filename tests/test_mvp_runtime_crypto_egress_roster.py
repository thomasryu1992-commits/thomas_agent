"""Every call that can put a write on an exchange, and every door that makes one, by name (crypto
refactor plan PR-02; ``docs/proposals/CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md`` §M-2a, §M-2d).

The lane's other egress pins are per module. ``test_mvp_runtime_crypto_live_execution.py`` pins which
runtime module may import the executing leg. ``venue_contract``'s and ``list_resting_orders``' own
tests pin that each one places and cancels nothing. The counter test pins that each door it lists
counts its orders. None of them answers "which code, anywhere in ``runtime/`` or ``scripts/``, can
send, cancel or validate an order", and ``scripts/`` is outside the layer test. A new script that
takes an adapter from ``select_order_adapter()`` and calls ``submit`` would be caught by none of them.
This file answers that question, as data, and fails when the answer changes.

It reads calls, not imports and not file names. Importing an adapter module is harmless (the
readiness board does, for constants), and holding an adapter is sometimes the point:
``venue_contract`` validates and reads with the order key and never sends. What the file pins is who
**calls** what:

- **the egress primitives.** An adapter's ``submit``, ``cancel_order`` and ``validate_order`` are
  methods on an object the scanner cannot type, so they are matched by name and keyed by the receiver
  as spelled. The four non-exchange ``submit`` calls in the core (a workflow store, a task store and a
  thread pool) are therefore on the roster too, named for what they are.
- **the doors.** These are functions that reach a primitive: the send-and-reconcile loop, the adapter
  and gate selectors, the live leg's entry, exit, bracket and settlement, the route's leg and
  emergency close, and the venue contract's checks. They are matched by name and resolved through the
  file's imports. A call that resolves to a module outside the crypto lane is not one of them.
- **the signed write verbs.** ``_signed_request`` calls whose verb is not ``GET``. A new write method
  on an adapter shows up here even before anything calls it.
- **the signing modules and the key names.** The files that import ``hmac``, and the files that
  name an exchange key's environment variable, whether by its constant or by its string (§M-2d).

Each roster entry says what the call is for. An entry is keyed by (file, enclosing function, callee),
and its value counts the calls there, because a second send in the same function is a new send.
The rosters only shrink: a call not on the roster fails, so does a count that changed, and so does an
entry whose call is gone. Adding an entry is the decision. It belongs in the same PR as the call, where
a reviewer sees it.

Two decisions stand behind the current entries. Recording them here makes them visible, not new.
- The venue-contract refresh runs inside the trading fire and builds the mainnet order-capable
  adapter to validate and read (S-2 in the plan, Thomas D-5 2026-09-30: this roster covers it; the
  read-only adapter protocol waits for the execution-layer step).
- The operator's probe, signed-testnet and emergency-close scripts are deliberate doors outside
  ``live_route``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCANNED = ("runtime", "scripts")
LANE = "runtime.mvp_runtime.crypto"

# Adapter methods that write, or validate a write, at the venue. Matched by name.
METHOD_PRIMITIVES = frozenset({"submit", "cancel_order", "validate_order"})

# Functions that reach a primitive, by the lane module that defines them. A call is matched by name and
# resolved through the file's imports. It is skipped only when it resolves to a module outside the lane.
DOORS: dict[str, frozenset[str]] = {
    "live_execution": frozenset({"submit_and_reconcile", "select_order_adapter"}),
    "testnet_execution": frozenset({"select_testnet_order_adapter"}),
    "live_route": frozenset({"select_live_gate", "run_live_leg", "run_emergency_close"}),
    "live_leg": frozenset({"execute_live_entry", "execute_live_exit", "place_bracket_leg",
                           "cancel_bracket_legs", "settle_venue_closed_position", "_close_naked_position"}),
    "venue_contract": frozenset({"refresh_verification", "run_checks"}),
}
DOOR_NAMES = frozenset().union(*DOORS.values())

_LIVE = "runtime/mvp_runtime/crypto/live_leg.py"
_ROUTE = "runtime/mvp_runtime/crypto/live_route.py"
_EXEC = "runtime/mvp_runtime/crypto/live_execution.py"
_VENUE = "runtime/mvp_runtime/crypto/venue_contract.py"
_PROBE = "scripts/run_slippage_probe.py"
_TESTNET = "scripts/run_signed_testnet_cycle.py"

# (file, enclosing function, callee) -> (calls, what it is for). The callee is ``<receiver>.<method>``
# for a primitive and ``<module>.<function>`` for a door (``?`` when the scanner cannot resolve it).
ROSTER: dict[tuple[str, str, str], tuple[int, str]] = {
    # --- the send-and-reconcile loop and the live leg (the autonomous path, via live_route) --------------
    (_EXEC, "submit_and_reconcile", "adapter.submit"): (1, "the one send of an entry or a close"),
    (_LIVE, "place_bracket_leg", "adapter.submit"): (1, "a protective leg; refused unless reduceOnly/closePosition"),
    (_LIVE, "cancel_bracket_legs", "adapter.cancel_order"): (1, "the brackets, once a close is confirmed"),
    (_LIVE, "execute_live_entry", "live_execution.submit_and_reconcile"): (1, "the autonomous entry"),
    (_LIVE, "execute_live_entry", "live_leg.place_bracket_leg"): (1, "the entry's brackets"),
    (_LIVE, "execute_live_entry", "live_leg._close_naked_position"): (2, "an entry whose brackets failed"),
    (_LIVE, "_close_naked_position", "live_execution.submit_and_reconcile"): (1, "the reduce-only close"),
    (_LIVE, "_close_naked_position", "live_leg.cancel_bracket_legs"): (1, "legs left after that close"),
    (_LIVE, "execute_live_exit", "live_execution.submit_and_reconcile"): (1, "the reduce-only close"),
    (_LIVE, "execute_live_exit", "live_leg.cancel_bracket_legs"): (1, "the legs, after the close"),
    (_LIVE, "settle_venue_closed_position", "live_leg.cancel_bracket_legs"): (1, "the surviving leg"),
    # --- the chokepoint -----------------------------------------------------------------------------------
    (_ROUTE, "select_live_gate", "live_execution.select_order_adapter"): (1, "the one gate of the live plane"),
    (_ROUTE, "run_live_leg", "live_route.select_live_gate"): (1, "the autonomous leg"),
    (_ROUTE, "run_emergency_close", "live_route.select_live_gate"): (1, "the emergency close"),
    (_ROUTE, "_run_gated_live_leg", "live_leg.execute_live_entry"): (1, "the autonomous entry"),
    (_ROUTE, "_settle_or_protect", "live_leg.settle_venue_closed_position"): (1, "a venue-closed position"),
    (_ROUTE, "_settle_or_protect", "live_leg.execute_live_exit"): (1, "an unprotected position"),
    (_ROUTE, "_time_exit_or_hold", "live_leg.execute_live_exit"): (1, "the time exit"),
    (_ROUTE, "_close_emergency_positions", "live_leg.execute_live_exit"): (1, "an approved emergency close"),
    ("runtime/mvp_runtime/crypto/cycle.py", "run_crypto_cycle", "live_route.run_live_leg"): (
        1, "the only autonomous caller of the live plane"),
    # --- the venue contract: validates and reads with the order key, never sends (S-2, D-5) --------------
    (_VENUE, "run_checks", "adapter.validate_order"): (3, "/order/test only; creates nothing"),
    (_VENUE, "refresh_verification", "live_execution.select_order_adapter"): (1, "the order-key adapter, for validation"),
    (_VENUE, "refresh_verification", "venue_contract.run_checks"): (1, "the checks"),
    ("runtime/mvp_runtime/scheduler.py", "_execute._refresh_venue_contract", "venue_contract.refresh_verification"): (
        1, "the trading fire's contract refresh"),
    ("scripts/venue_contract.py", "main", "venue_contract.refresh_verification"): (1, "the operator's --run"),
    # --- operator doors outside live_route ----------------------------------------------------------------
    (_PROBE, "run_fire", "live_execution.select_order_adapter"): (1, "the probe --fire door"),
    (_PROBE, "run_fire", "live_execution.submit_and_reconcile"): (1, "the probe's entry"),
    (_PROBE, "run_fire", "live_leg.place_bracket_leg"): (1, "the probe's stop"),
    (_PROBE, "run_fire", "live_leg.execute_live_exit"): (3, "the probe's closes"),
    (_PROBE, "run_fire", "live_leg.settle_venue_closed_position"): (1, "the probe's venue-side close"),
    (_TESTNET, "plan_cycle", "testnet_execution.select_testnet_order_adapter"): (1, "the testnet adapter"),
    (_TESTNET, "run_cycle", "live_execution.submit_and_reconcile"): (2, "testnet entry and close"),
    (_TESTNET, "run_cycle", "adapter.cancel_order"): (1, "the testnet legs"),
    (_TESTNET, "run_cycle", "live_leg.place_bracket_leg"): (1, "the testnet brackets"),
    ("scripts/emergency_close.py", "run_confirm", "live_route.run_emergency_close"): (1, "the approved emergency close"),
    ("scripts/diagnose_bracket_leg.py", "main", "live_execution.select_order_adapter"): (1, "validation only"),
    ("scripts/diagnose_bracket_leg.py", "main", "adapter.validate_order"): (1, "/order/test only; creates nothing"),
    ("scripts/list_resting_orders.py", "main", "live_execution.select_order_adapter"): (1, "reads resting orders only"),
    # --- not exchange calls: the same method name on other objects ----------------------------------------
    ("runtime/mvp_runtime/dispatch_bridge.py", "apply_workflow_command", "workflow_store.submit"): (1, "not an exchange call"),
    ("runtime/mvp_runtime/scheduler.py", "_execute", "workflow_store.submit"): (1, "not an exchange call"),
    ("runtime/mvp_runtime/socket_door.py", "SocketDoor.process_request", "self._pool.submit"): (1, "a thread pool"),
    ("runtime/mvp_runtime/task_registry.py", "record_submission", "store.submit"): (1, "not an exchange call"),
}

# (file, enclosing function, verb) -> (calls, what it is for): signed requests that are not GETs.
SIGNED_WRITES: dict[tuple[str, str, str], tuple[int, str]] = {
    (_EXEC, "BinanceFuturesOrderAdapter.submit", "POST"): (1, "/fapi/v1/order or /fapi/v1/algoOrder"),
    (_EXEC, "BinanceFuturesOrderAdapter.validate_order", "POST"): (1, "/fapi/v1/order/test"),
    (_EXEC, "BinanceFuturesOrderAdapter.cancel_order", "DELETE"): (1, "/fapi/v1/order or /fapi/v1/algoOrder"),
    ("runtime/mvp_runtime/crypto/testnet_execution.py", "BinanceTestnetOrderAdapter.submit", "POST"): (1, "testnet host"),
    ("runtime/mvp_runtime/crypto/testnet_execution.py", "BinanceTestnetOrderAdapter.cancel_order", "DELETE"): (1, "testnet host"),
}

# Files in the lane or in scripts/ that sign requests (import hmac): the three exchange clients.
SIGNING_MODULES = frozenset({
    "runtime/mvp_runtime/crypto/account.py",            # the read-only account key
    _EXEC,                                              # the mainnet order key
    "runtime/mvp_runtime/crypto/testnet_execution.py",  # the testnet order key
})

# The exchange keys' environment variables, and the files that may name them (constant or string).
KEY_ENV_NAMES: dict[str, str] = {
    "ORDER_API_KEY_ENV": "MVP_LIVE_ORDER_API_KEY",
    "ORDER_API_SECRET_ENV": "MVP_LIVE_ORDER_API_SECRET",
    "TESTNET_API_KEY_ENV": "MVP_TESTNET_ORDER_API_KEY",
    "TESTNET_API_SECRET_ENV": "MVP_TESTNET_ORDER_API_SECRET",
    "ACCOUNT_API_KEY_ENV": "BINANCE_ACCOUNT_API_KEY",
    "ACCOUNT_API_SECRET_ENV": "BINANCE_ACCOUNT_API_SECRET",
}
KEY_ENV_READERS = frozenset({
    _EXEC,
    "runtime/mvp_runtime/crypto/testnet_execution.py",
    "runtime/mvp_runtime/crypto/account.py",
    "runtime/mvp_runtime/crypto/live_readiness.py",     # whether the read key is set, never its value
})


def _files(repo: Path) -> list[Path]:
    return sorted(p for base in SCANNED if (repo / base).is_dir() for p in (repo / base).rglob("*.py"))


def _module_name(path: Path, repo: Path) -> str:
    parts = path.relative_to(repo).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _bindings(tree: ast.AST, module: str, is_package: bool) -> dict[str, str]:
    """Each name the file binds by import, anywhere in it, to the dotted object it names."""
    package = module if is_package else module.rpartition(".")[0]
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                source = ".".join(base + ([node.module] if node.module else []))
            else:
                source = node.module or ""
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{source}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
    return bound


def _door_module(dotted: str) -> str | None:
    """The lane module a dotted object sits in, ``?`` if it is not a lane path, None if it is outside."""
    if not dotted.startswith(LANE + "."):
        return None if "." in dotted else "?"
    return dotted[len(LANE) + 1:].split(".")[0]


def scan(repo: Path) -> dict[str, dict]:
    """What the tree at ``repo`` calls: the egress roster, the signed writes, the signers, the key readers."""
    calls: dict[tuple[str, str, str], int] = {}
    writes: dict[tuple[str, str, str], int] = {}
    signers: set[str] = set()
    key_readers: set[str] = set()
    key_values = set(KEY_ENV_NAMES.values())
    for path in _files(repo):
        rel = path.relative_to(repo).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = _module_name(path, repo)
        bound = _bindings(tree, module, path.name == "__init__.py")
        own = module.startswith(LANE + ".") and module[len(LANE) + 1:].split(".")[0]
        defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if any(isinstance(n, ast.Import) and any(a.name == "hmac" for a in n.names)
               or isinstance(n, ast.ImportFrom) and n.module == "hmac" for n in ast.walk(tree)):
            if rel.startswith("scripts/") or module.startswith(LANE + "."):
                signers.add(rel)
        for n in ast.walk(tree):
            if (isinstance(n, ast.Name) and n.id in KEY_ENV_NAMES
                    or isinstance(n, ast.Attribute) and n.attr in KEY_ENV_NAMES
                    or isinstance(n, ast.alias) and (n.asname or n.name) in KEY_ENV_NAMES
                    or isinstance(n, ast.Constant) and n.value in key_values):
                key_readers.add(rel)

        stack: list[str] = []

        def door(func: ast.expr) -> str | None:
            if isinstance(func, ast.Name):
                if func.id in bound:
                    where = _door_module(bound[func.id])
                    name = bound[func.id].rsplit(".", 1)[-1]
                else:
                    where = (own or "?") if func.id in defined else "?"
                    name = func.id
                return None if where is None else f"{where}.{name}"
            head = func.value
            if isinstance(head, ast.Name) and head.id in bound:
                target = bound[head.id]
                where = _door_module(target + ".x")
                if where is None:
                    return None
                return f"{target.rsplit('.', 1)[-1] if where != '?' else '?'}.{func.attr}"
            return f"?.{func.attr}"

        def visit(node: ast.AST) -> None:
            named = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            if named:
                stack.append(node.name)
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
                if isinstance(func, ast.Name) and func.id in bound:
                    name = bound[func.id].rsplit(".", 1)[-1]    # `import … as send` is still the door
                where = ".".join(stack) or "<module>"
                callee = None
                if isinstance(func, ast.Attribute) and name in METHOD_PRIMITIVES:
                    callee = f"{ast.unparse(func.value)}.{name}"
                elif name in DOOR_NAMES:
                    callee = door(func)
                if callee is not None:
                    calls[(rel, where, callee)] = calls.get((rel, where, callee), 0) + 1
                if name == "_signed_request" and node.args:
                    verb = node.args[0].value if isinstance(node.args[0], ast.Constant) else "?"
                    if verb != "GET":
                        writes[(rel, where, str(verb))] = writes.get((rel, where, str(verb)), 0) + 1
            for child in ast.iter_child_nodes(node):
                visit(child)
            if named:
                stack.pop()

        visit(tree)
    return {"calls": calls, "writes": writes, "signers": signers, "key_readers": key_readers}


def _against(found: dict[tuple, int], roster: dict[tuple, tuple[int, str]], what: str) -> list[str]:
    problems = [f"{what} not on the roster: {key} x{count}" for key, count in sorted(found.items()) if key not in roster]
    problems += [f"{what} on the roster but gone: {key}" for key in sorted(roster) if key not in found]
    problems += [f"{what} count changed: {key} {roster[key][0]} -> {found[key]}"
                 for key in sorted(roster) if key in found and found[key] != roster[key][0]]
    return problems


def breaches(repo: Path, *, roster=None, signed_writes=None, signing=None, key_readers=None) -> list[str]:
    """What the tree at ``repo`` breaks, against the rosters (the module's own unless given)."""
    found = scan(repo)
    problems = _against(found["calls"], ROSTER if roster is None else roster, "egress call")
    problems += _against(found["writes"], SIGNED_WRITES if signed_writes is None else signed_writes, "signed write")
    signing = SIGNING_MODULES if signing is None else signing
    problems += [f"signs requests, not on the roster: {p}" for p in sorted(found["signers"] - signing)]
    problems += [f"signer on the roster but gone: {p}" for p in sorted(signing - found["signers"])]
    readers = KEY_ENV_READERS if key_readers is None else key_readers
    problems += [f"names an exchange key's env, not on the roster: {p}" for p in sorted(found["key_readers"] - readers)]
    problems += [f"key reader on the roster but gone: {p}" for p in sorted(readers - found["key_readers"])]
    return problems


def test_every_egress_call_signed_write_signer_and_key_reader_is_named():
    problems = breaches(REPO)
    assert problems == [], (
        "the answer to 'which code can send, cancel or validate an order' changed. Adding a caller is a "
        "decision: name it in this file's roster, with what it is for, in the same PR.\n  "
        + "\n  ".join(problems)
    )


def test_every_door_is_defined_where_the_roster_says():
    """The door names are resolved by module, so a door that moved would quietly stop being matched."""
    for module, names in DOORS.items():
        tree = ast.parse((REPO / "runtime" / "mvp_runtime" / "crypto" / f"{module}.py").read_text(encoding="utf-8"))
        defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert names <= defined, f"{module} no longer defines {sorted(names - defined)}"


def test_every_roster_entry_says_what_it_is_for():
    for key, (count, purpose) in {**ROSTER, **SIGNED_WRITES}.items():
        assert count >= 1 and purpose.strip(), key


def test_the_scanner_sees_every_breach(tmp_path):
    """The check above passes on a tree with nothing to find, which proves nothing about it. This
    synthetic tree has a script that takes the mainnet adapter and sends, a second send in a registered
    function, a new signed write, a new signer, a new key reader, a door called under an alias, a
    same-named function from outside the lane that must not count, and a roster entry that is gone."""
    lane = tmp_path / "runtime" / "mvp_runtime" / "crypto"
    lane.mkdir(parents=True)
    (tmp_path / "runtime" / "__init__.py").write_text("")
    (tmp_path / "runtime" / "mvp_runtime" / "__init__.py").write_text("")
    (lane / "__init__.py").write_text("")
    (lane / "live_execution.py").write_text(
        "import hmac\n"
        "class A:\n"
        "    def submit(self, r):\n"
        "        return self._signed_request('POST', '/o', r)\n"
        "    def amend(self, r):\n"
        "        return self._signed_request('PUT', '/o', r)\n"
        "def select_order_adapter():\n"
        "    return A()\n"
        "def submit_and_reconcile(intent, adapter):\n"
        "    adapter.submit(intent)\n"
        "    adapter.submit(intent)\n"
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "new_door.py").write_text(
        "from runtime.mvp_runtime.crypto import live_execution as lx\n"
        "from runtime.mvp_runtime.crypto.live_execution import submit_and_reconcile as send\n"
        "from elsewhere import run_checks\n"
        "KEY = 'MVP_LIVE_ORDER_API_KEY'\n"
        "def main():\n"
        "    adapter = lx.select_order_adapter()\n"
        "    adapter.submit({})\n"
        "    send({}, adapter)\n"
        "    run_checks()\n"
    )
    ex = "runtime/mvp_runtime/crypto/live_execution.py"
    roster = {
        (ex, "submit_and_reconcile", "adapter.submit"): (1, "the send"),
        (ex, "gone", "adapter.cancel_order"): (1, "removed since"),
    }
    writes = {(ex, "A.submit", "POST"): (1, "order")}
    problems = breaches(tmp_path, roster=roster, signed_writes=writes, signing=frozenset(), key_readers=frozenset())
    text = "\n".join(problems)
    assert "egress call count changed: ('runtime/mvp_runtime/crypto/live_execution.py', 'submit_and_reconcile', 'adapter.submit') 1 -> 2" in text
    assert "('scripts/new_door.py', 'main', 'live_execution.select_order_adapter')" in text
    assert "('scripts/new_door.py', 'main', 'adapter.submit')" in text
    assert "('scripts/new_door.py', 'main', 'live_execution.submit_and_reconcile')" in text
    assert "run_checks" not in text
    assert "gone: ('runtime/mvp_runtime/crypto/live_execution.py', 'gone', 'adapter.cancel_order')" in text
    assert "signed write not on the roster: ('runtime/mvp_runtime/crypto/live_execution.py', 'A.amend', 'PUT')" in text
    assert f"signs requests, not on the roster: {ex}" in text
    assert "names an exchange key's env, not on the roster: scripts/new_door.py" in text
