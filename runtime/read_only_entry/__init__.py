"""Read-only Runtime entry design components.

I0.5.5 integration candidates intentionally remain module-local to avoid
introducing a package-root dependency from I0.5.3/4 components back into
protected governance state. Import them from
``runtime.read_only_entry.integration_candidate`` only.
"""

from .planner import build_entry_plan, validate_entry_plan_semantics
from .disabled_adapter import build_disabled_entry_evidence, validate_disabled_entry_evidence_semantics
from .authorization import build_entry_authorization, validate_entry_authorization_semantics
from .atomic_transition import build_atomic_transition_preview, validate_atomic_transition_preview_semantics

__all__ = [
    "build_entry_plan",
    "validate_entry_plan_semantics",
    "build_disabled_entry_evidence",
    "validate_disabled_entry_evidence_semantics",
    "build_entry_authorization",
    "validate_entry_authorization_semantics",
    "build_atomic_transition_preview",
    "validate_atomic_transition_preview_semantics",
]
