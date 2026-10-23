# The capability row reads environment names, not values; revocation stated as it works

- **Why:** an outside review (Thomas 2026-10-23, §3.3–§3.4) found two problems in the `docs/ACTIVE_ARCHITECTURE.md`
  row answering "is a capability live on this machine".
  - It told the reader to run `docker inspect <svc> --format '{{json .Config.Env}}'`, which prints every variable's
    value, API keys and tokens included. That contradicts "secrets are metadata-only".
  - It said the capability is "re-verified at every egress by the env re-read". That reads as if editing `.env`
    revokes a running service. It does not: a container's environment is fixed at start, as `safety_gate.py`'s own
    comment says ("NOT a mid-flight revocation").
- **What:** the row now lists names only (`{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1`, the form
  `DEPLOYMENT.md` already used for hermes) and prefers the readiness commands, which print no values. It also states
  that the egress re-read fails closed for an authorization built earlier but is not a mid-flight revocation, and
  that revoking means unsetting the variable and recreating the container, as `CLAUDE.md` says.
- **Checked:**
  - No other committed doc prints environment values. The `printenv MVP_BRIDGE_CLIENT_UID` lines print one
    non-secret uid.
  - The pipe leaves names only, including values that contain `=`.
  - The release gate (check-only) and the architecture and doc tests pass.
