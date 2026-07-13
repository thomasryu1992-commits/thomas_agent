from __future__ import annotations
import hashlib, json
from typing import Any

class OperationsEvidenceError(ValueError):
    pass

FORBIDDEN_SECRET_KEYS = {
    "api_key", "api_secret", "secret", "secret_value", "private_key", "password", "passphrase",
    "access_token", "refresh_token", "bearer_token", "credential_value"
}

SAFE_SECRET_METADATA_KEYS = {
    "secret_handling_mode", "secret_values_included", "no_secret_evidence_refs", "secret_access_performed"
}

def canonical_sha256(payload: Any) -> str:
    encoded=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    return "sha256:"+hashlib.sha256(encoded).hexdigest()

def ensure_no_secret_values(value: Any, path: str = "<root>") -> None:
    if isinstance(value, dict):
        for key,item in value.items():
            normalized=str(key).lower()
            if normalized in FORBIDDEN_SECRET_KEYS and normalized not in SAFE_SECRET_METADATA_KEYS:
                raise OperationsEvidenceError(f"forbidden secret-bearing key at {path}.{key}")
            ensure_no_secret_values(item,f"{path}.{key}")
    elif isinstance(value,list):
        for index,item in enumerate(value): ensure_no_secret_values(item,f"{path}[{index}]")

def count_status(items: list[dict[str, Any]], field: str = "status") -> dict[str,int]:
    out: dict[str,int]={}
    for item in items:
        status=str(item.get(field))
        out[status]=out.get(status,0)+1
    return out

def authority_rank(level: str) -> int:
    return int(level[1:])
