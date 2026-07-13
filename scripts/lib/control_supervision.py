from __future__ import annotations
import hashlib, json
from typing import Any

class ControlSupervisionError(ValueError):
    pass

FORBIDDEN_SECRET_KEYS={
    "api_key","api_secret","secret","secret_value","private_key","password","passphrase",
    "access_token","refresh_token","bearer_token","bot_token","webhook_secret","credential_value"
}
SAFE_METADATA_KEYS={
    "bot_token_value_included","webhook_secret_value_included","credential_values_included",
    "secret_access_allowed","secret_access_performed","secret_denial_test"
}

def canonical_sha256(payload: Any) -> str:
    encoded=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    return "sha256:"+hashlib.sha256(encoded).hexdigest()

def ensure_no_secret_values(value: Any, path: str="<root>") -> None:
    if isinstance(value,dict):
        for key,item in value.items():
            normalized=str(key).lower()
            if normalized in FORBIDDEN_SECRET_KEYS and normalized not in SAFE_METADATA_KEYS:
                raise ControlSupervisionError(f"forbidden secret-bearing key at {path}.{key}")
            ensure_no_secret_values(item,f"{path}.{key}")
    elif isinstance(value,list):
        for i,item in enumerate(value): ensure_no_secret_values(item,f"{path}[{i}]")

def count_status(items: list[dict[str,Any]], field: str="status") -> dict[str,int]:
    out: dict[str,int]={}
    for item in items:
        status=str(item.get(field)); out[status]=out.get(status,0)+1
    return out

def validate_threshold_rule(rule: dict[str,Any]) -> None:
    warning=float(rule["warning_threshold"]); critical=float(rule["critical_threshold"])
    direction=rule["direction"]
    if direction=="HIGHER_IS_WORSE" and not warning < critical:
        raise ControlSupervisionError("HIGHER_IS_WORSE requires warning_threshold < critical_threshold")
    if direction=="LOWER_IS_WORSE" and not warning > critical:
        raise ControlSupervisionError("LOWER_IS_WORSE requires warning_threshold > critical_threshold")

def evaluate_metric(rule: dict[str,Any], metric: dict[str,Any]) -> tuple[str,list[str]]:
    validate_threshold_rule(rule)
    if metric["data_status"] != "AVAILABLE" or metric["observed_value"] is None:
        return "NOT_AVAILABLE", ["metric_data_not_available"]
    if int(metric["age_seconds"]) > int(rule["stale_after_seconds"]):
        return "STALE", ["metric_evidence_stale"]
    value=float(metric["observed_value"]); warning=float(rule["warning_threshold"]); critical=float(rule["critical_threshold"])
    if rule["direction"]=="HIGHER_IS_WORSE":
        if value>=critical: return "CRITICAL",["observed_value_at_or_above_critical_threshold"]
        if value>=warning: return "WARN",["observed_value_at_or_above_warning_threshold"]
    else:
        if value<=critical: return "CRITICAL",["observed_value_at_or_below_critical_threshold"]
        if value<=warning: return "WARN",["observed_value_at_or_below_warning_threshold"]
    return "PASS",["observed_value_within_review_threshold"]
