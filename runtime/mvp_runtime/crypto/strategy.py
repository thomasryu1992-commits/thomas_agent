"""C3 declarative strategy specs — structural parse, identity hash, entry evaluation.

Port of the source system's ``strategy_spec.py`` / ``strategy_hash.py`` /
``strategy_evaluator.py`` (already pure Python there). A :class:`StrategySpec` is data,
not code: entry/exit rules as typed conditions over named features. ``from_dict`` does
*structural* validation only and fails closed (:class:`SpecParseError`) on malformed
input; whether a strategy is safe or good is a later phase's judgment.

``strategy_rule_hash`` must stay **bit-identical to the source implementation** (plain
hex sha256 over the source's canonical JSON — no ``sha256:`` prefix, unlike the kernel's
``integrity`` helpers): the C7 import keeps the original hashes of migrated strategies,
and a re-parse must verify them, not re-mint them. Parity is asserted against a
source-computed fixture.

Hard safety invariant, kept verbatim: a spec can never carry execution authority —
``can_submit_orders`` / ``can_modify_runtime`` are forced false and a dict setting
either true is rejected.

The evaluator is pure and fail-closed: a condition over a missing/None feature is
*indeterminate* (``None``), never a silent match; AND does not match on any
indeterminate or false condition, OR matches only on a genuine true. A strategy never
enters on data it could not evaluate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

SCHEMA_VERSION = "strategy_spec.v1"
STRATEGY_RULE_HASH_VERSION = "strategy_rule_hash.v1"

ALLOWED_TIMEFRAMES = frozenset({"15m", "1h", "4h", "1d"})
ALLOWED_STOP_MODELS = frozenset({"atr"})
_ENTRY_OPERATORS = frozenset({"AND", "OR"})
_EQUALITY_OPS = frozenset({"==", "!="})


class SpecParseError(ValueError):
    """Raised when a dict cannot be parsed into a structurally valid spec."""


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    LONG_SHORT = "long_short"


class StrategyStatus(str, Enum):
    GENERATED = "GENERATED"
    VALIDATED = "VALIDATED"
    BACKTESTED = "BACKTESTED"
    BATCH_CHAMPION = "BATCH_CHAMPION"
    PAPER_ACTIVE = "PAPER_ACTIVE"
    SIGNED_TESTNET_CANDIDATE = "SIGNED_TESTNET_CANDIDATE"
    SIGNED_TESTNET_ACTIVE = "SIGNED_TESTNET_ACTIVE"
    LIVE_CANARY_CANDIDATE = "LIVE_CANARY_CANDIDATE"
    LIVE_ACTIVE = "LIVE_ACTIVE"
    WARNING = "WARNING"
    PROBATION = "PROBATION"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True)
class RuleCondition:
    """One entry condition: ``feature comparison (value | value_from)`` — exactly one
    of ``value`` (constant) or ``value_from`` (another feature name) is set."""

    feature: str
    comparison: str
    value: float | str | None = None
    value_from: str | None = None

    @staticmethod
    def from_dict(raw: Any, *, where: str) -> "RuleCondition":
        if not isinstance(raw, dict):
            raise SpecParseError(f"{where}: condition must be an object, got {type(raw).__name__}")
        feature = raw.get("feature")
        comparison = raw.get("comparison")
        if not isinstance(feature, str) or not feature:
            raise SpecParseError(f"{where}: condition 'feature' must be a non-empty string")
        if not isinstance(comparison, str) or not comparison:
            raise SpecParseError(f"{where}: condition 'comparison' must be a non-empty string")

        has_value = "value" in raw and raw.get("value") is not None
        value_from = raw.get("value_from")
        has_value_from = isinstance(value_from, str) and value_from != ""
        if has_value and has_value_from:
            raise SpecParseError(f"{where}: condition sets both 'value' and 'value_from'")
        if not has_value and not has_value_from:
            raise SpecParseError(f"{where}: condition sets neither 'value' nor 'value_from'")

        value: float | str | None = None
        if has_value:
            raw_value = raw["value"]
            if isinstance(raw_value, bool):
                raise SpecParseError(f"{where}: condition 'value' must be a number or label, not a boolean")
            if isinstance(raw_value, (int, float)):
                value = float(raw_value)
            elif isinstance(raw_value, str) and raw_value != "":
                value = raw_value
            else:
                raise SpecParseError(
                    f"{where}: condition 'value' must be a number or non-empty string, got {raw_value!r}"
                )

        return RuleCondition(
            feature=feature,
            comparison=comparison,
            value=value,
            value_from=value_from if has_value_from else None,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"feature": self.feature, "comparison": self.comparison}
        if self.value_from is not None:
            out["value_from"] = self.value_from
        else:
            out["value"] = self.value
        return out


@dataclass(frozen=True)
class EntryRules:
    operator: str
    conditions: tuple[RuleCondition, ...]

    @staticmethod
    def from_dict(raw: Any) -> "EntryRules":
        if not isinstance(raw, dict):
            raise SpecParseError("entry_rules must be an object")
        operator = raw.get("operator", "AND")
        if operator not in _ENTRY_OPERATORS:
            raise SpecParseError(f"entry_rules.operator must be one of {sorted(_ENTRY_OPERATORS)}, got {operator!r}")
        raw_conditions = raw.get("conditions")
        if not isinstance(raw_conditions, list) or not raw_conditions:
            raise SpecParseError("entry_rules.conditions must be a non-empty list")
        conditions = tuple(
            RuleCondition.from_dict(c, where=f"entry_rules.conditions[{i}]")
            for i, c in enumerate(raw_conditions)
        )
        return EntryRules(operator=operator, conditions=conditions)

    def to_dict(self) -> dict[str, Any]:
        return {"operator": self.operator, "conditions": [c.to_dict() for c in self.conditions]}


@dataclass(frozen=True)
class ExitRules:
    stop_model: str
    stop_atr: float
    target_atr: float
    max_holding_bars: int

    @staticmethod
    def from_dict(raw: Any) -> "ExitRules":
        if not isinstance(raw, dict):
            raise SpecParseError("exit_rules must be an object")
        stop_model = raw.get("stop_model")
        if stop_model not in ALLOWED_STOP_MODELS:
            raise SpecParseError(f"exit_rules.stop_model must be one of {sorted(ALLOWED_STOP_MODELS)}, got {stop_model!r}")

        def _pos_number(key: str) -> float:
            val = raw.get(key)
            try:
                num = float(val)
            except (TypeError, ValueError):
                raise SpecParseError(f"exit_rules.{key} must be numeric, got {val!r}") from None
            if num <= 0:
                raise SpecParseError(f"exit_rules.{key} must be > 0, got {num}")
            return num

        stop_atr = _pos_number("stop_atr")
        target_atr = _pos_number("target_atr")

        max_holding_raw = raw.get("max_holding_bars")
        try:
            max_holding_bars = int(max_holding_raw)
        except (TypeError, ValueError):
            raise SpecParseError(f"exit_rules.max_holding_bars must be an integer, got {max_holding_raw!r}") from None
        if max_holding_bars <= 0:
            raise SpecParseError(f"exit_rules.max_holding_bars must be > 0, got {max_holding_bars}")

        return ExitRules(
            stop_model=stop_model, stop_atr=stop_atr, target_atr=target_atr, max_holding_bars=max_holding_bars
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stop_model": self.stop_model,
            "stop_atr": self.stop_atr,
            "target_atr": self.target_atr,
            "max_holding_bars": self.max_holding_bars,
        }


@dataclass(frozen=True)
class RiskConstraints:
    max_risk_per_trade_R: float = 1.0

    @staticmethod
    def from_dict(raw: Any) -> "RiskConstraints":
        if raw is None:
            return RiskConstraints()
        if not isinstance(raw, dict):
            raise SpecParseError("risk_constraints must be an object")
        val = raw.get("max_risk_per_trade_R", 1.0)
        try:
            num = float(val)
        except (TypeError, ValueError):
            raise SpecParseError(f"risk_constraints.max_risk_per_trade_R must be numeric, got {val!r}") from None
        if num <= 0:
            raise SpecParseError(f"risk_constraints.max_risk_per_trade_R must be > 0, got {num}")
        return RiskConstraints(max_risk_per_trade_R=num)

    def to_dict(self) -> dict[str, Any]:
        return {"max_risk_per_trade_R": self.max_risk_per_trade_R}


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    strategy_version: str
    strategy_family: str
    direction: Direction
    timeframe: str
    symbol_scope: tuple[str, ...]
    entry_rules: EntryRules
    exit_rules: ExitRules
    risk_constraints: RiskConstraints
    status: StrategyStatus = StrategyStatus.GENERATED
    generation_id: str | None = None
    created_by: str = "StrategyGenerationAgent"
    schema_version: str = SCHEMA_VERSION
    strategy_rule_hash: str = ""

    @staticmethod
    def from_dict(raw: Any) -> "StrategySpec":
        if not isinstance(raw, dict):
            raise SpecParseError(f"spec must be an object, got {type(raw).__name__}")

        # Reject any attempt to grant execution authority (fail-closed, verbatim).
        for forbidden in ("can_submit_orders", "can_modify_runtime"):
            if raw.get(forbidden) is True:
                raise SpecParseError(f"{forbidden} must not be true — a strategy spec has no execution authority")

        strategy_id = _require_str(raw, "strategy_id")
        strategy_version = _require_str(raw, "strategy_version")
        strategy_family = _require_str(raw, "strategy_family")

        direction = _parse_enum(raw.get("direction"), Direction, "direction")
        status = _parse_enum(raw.get("status", StrategyStatus.GENERATED.value), StrategyStatus, "status")

        timeframe = raw.get("timeframe")
        if timeframe not in ALLOWED_TIMEFRAMES:
            raise SpecParseError(f"timeframe must be one of {sorted(ALLOWED_TIMEFRAMES)}, got {timeframe!r}")

        symbol_scope_raw = raw.get("symbol_scope")
        if not isinstance(symbol_scope_raw, list) or not symbol_scope_raw:
            raise SpecParseError("symbol_scope must be a non-empty list")
        if not all(isinstance(s, str) and s for s in symbol_scope_raw):
            raise SpecParseError("symbol_scope entries must be non-empty strings")

        generation_id = raw.get("generation_id")
        if generation_id is not None and not isinstance(generation_id, str):
            raise SpecParseError("generation_id must be a string or null")
        created_by = raw.get("created_by", "StrategyGenerationAgent")
        if not isinstance(created_by, str) or not created_by:
            raise SpecParseError("created_by must be a non-empty string")

        spec = StrategySpec(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_family=strategy_family,
            direction=direction,
            timeframe=timeframe,
            symbol_scope=tuple(symbol_scope_raw),
            entry_rules=EntryRules.from_dict(raw.get("entry_rules")),
            exit_rules=ExitRules.from_dict(raw.get("exit_rules")),
            risk_constraints=RiskConstraints.from_dict(raw.get("risk_constraints")),
            status=status,
            generation_id=generation_id,
            created_by=created_by,
            schema_version=str(raw.get("schema_version", SCHEMA_VERSION)),
        )

        computed = compute_strategy_rule_hash(spec)
        provided = raw.get("strategy_rule_hash")
        if provided is not None and provided != "" and provided != computed:
            raise SpecParseError("strategy_rule_hash does not match the spec's rules (tampered or stale)")
        object.__setattr__(spec, "strategy_rule_hash", computed)  # frozen field, set once at parse
        return spec

    def referenced_features(self) -> set[str]:
        names: set[str] = set()
        for cond in self.entry_rules.conditions:
            names.add(cond.feature)
            if cond.value_from:
                names.add(cond.value_from)
        return names

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "generation_id": self.generation_id,
            "strategy_family": self.strategy_family,
            "status": self.status.value,
            "symbol_scope": list(self.symbol_scope),
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "entry_rules": self.entry_rules.to_dict(),
            "exit_rules": self.exit_rules.to_dict(),
            "risk_constraints": self.risk_constraints.to_dict(),
            "created_by": self.created_by,
            "can_submit_orders": False,
            "can_modify_runtime": False,
            "strategy_rule_hash": self.strategy_rule_hash,
        }


def strategy_rule_fingerprint(spec: StrategySpec) -> dict[str, Any]:
    """The canonical subset of a spec that defines its trading behaviour."""
    return {
        "hash_version": STRATEGY_RULE_HASH_VERSION,
        "strategy_family": spec.strategy_family,
        "timeframe": spec.timeframe,
        "direction": spec.direction.value,
        "symbol_scope": sorted(spec.symbol_scope),
        "entry_rules": {
            "operator": spec.entry_rules.operator,
            "conditions": [
                {
                    "feature": c.feature,
                    "comparison": c.comparison,
                    "value": c.value,
                    "value_from": c.value_from,
                }
                for c in spec.entry_rules.conditions
            ],
        },
        "exit_rules": {
            "stop_model": spec.exit_rules.stop_model,
            "stop_atr": spec.exit_rules.stop_atr,
            "target_atr": spec.exit_rules.target_atr,
            "max_holding_bars": spec.exit_rules.max_holding_bars,
        },
        "risk_constraints": {
            "max_risk_per_trade_R": spec.risk_constraints.max_risk_per_trade_R,
        },
    }


def compute_strategy_rule_hash(spec: StrategySpec) -> str:
    """Source-exact identity hash: plain hex sha256 over the source's canonical JSON
    (``sort_keys, separators=(',', ':'), ensure_ascii=False, default=str`` — NOT the
    kernel's ``sha256:``-prefixed helpers). Imported strategies keep their original
    hashes; re-minting under a different canonicalization would break their identity."""
    text = json.dumps(
        strategy_rule_fingerprint(spec),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Entry evaluation (port of strategy_evaluator.py, pure + fail-closed) ----

@dataclass(frozen=True)
class MatchResult:
    matched: bool
    direction: str | None  # "LONG" | "SHORT" when matched, else None
    condition_results: tuple[bool | None, ...]


def _cell(row: Mapping[str, Any], name: str) -> Any:
    value = row.get(name)
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


def evaluate_condition(cond: RuleCondition, row: Mapping[str, Any]) -> bool | None:
    """Evaluate one condition. Returns None when it cannot be evaluated."""
    left = _cell(row, cond.feature)
    if left is None:
        return None

    if cond.value_from is not None:
        right: Any = _cell(row, cond.value_from)
    else:
        right = cond.value
    if right is None:
        return None

    op = cond.comparison
    if op in _EQUALITY_OPS:
        equal = left == right
        return equal if op == "==" else not equal

    # Ordering comparisons require two numbers; a string operand is indeterminate.
    if isinstance(left, str) or isinstance(right, str):
        return None
    try:
        left_f, right_f = float(left), float(right)
    except (TypeError, ValueError):
        return None
    if op == ">":
        return left_f > right_f
    if op == ">=":
        return left_f >= right_f
    if op == "<":
        return left_f < right_f
    if op == "<=":
        return left_f <= right_f
    return None


def evaluate_spec(spec: StrategySpec, row: Mapping[str, Any]) -> MatchResult:
    """Evaluate ``spec``'s entry rules against one feature row."""
    results = tuple(evaluate_condition(c, row) for c in spec.entry_rules.conditions)
    if spec.entry_rules.operator == "OR":
        matched = any(r is True for r in results)
    else:  # AND
        matched = all(r is True for r in results)
    if matched:
        # LONG, and LONG_SHORT until directional rule sets exist, both enter long.
        direction = "SHORT" if spec.direction is Direction.SHORT else "LONG"
    else:
        direction = None
    return MatchResult(matched=matched, direction=direction, condition_results=results)


def load_strategy_pool(raw: Any) -> list[StrategySpec]:
    """Parse an ``active_strategy_pool``-shaped dict into validated specs.

    Fail-closed: a malformed pool or any malformed member raises ``SpecParseError``
    (one bad spec poisons the load — a partially-loaded pool would silently change
    which strategies trade)."""
    if not isinstance(raw, dict):
        raise SpecParseError("strategy pool must be an object")
    entries = raw.get("active_strategies")
    if not isinstance(entries, list):
        raise SpecParseError("strategy pool must have an 'active_strategies' list")
    specs: list[StrategySpec] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("strategy_spec"), dict):
            raise SpecParseError(f"active_strategies[{i}] must be an object with a 'strategy_spec'")
        specs.append(StrategySpec.from_dict(entry["strategy_spec"]))
    return specs


def _require_str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise SpecParseError(f"{key} must be a non-empty string")
    return value


def _parse_enum(value: Any, enum_cls: type[Enum], key: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError:
        allowed = [e.value for e in enum_cls]
        raise SpecParseError(f"{key} must be one of {allowed}, got {value!r}") from None
