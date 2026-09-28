# Actions pinned to commit SHAs, the base image pinned by digest

- **What changed:** every `uses:` in `.github/workflows/` names a full commit SHA with the version
  in a comment:
  - `actions/checkout@d23441a4… # v6.1.0`;
  - `actions/setup-python@ece7cb06… # v6.3.0`.
  The `Dockerfile` builds `FROM python:3.12-slim@sha256:57cd7c3a…`. `tests/test_supply_chain_pins.py`
  refuses a bare tag, and the i0_5_1 validator now requires the SHA form instead of the literal `@v6`.
- **Why:** a tag can be moved under us. The two Actions' `v6` is moved by their owners, and
  `python:3.12-slim` by every upstream rebuild. The images had already drifted: the host builds on
  its cached base from 2026-07-14 (Python 3.12.13), while CI's docker job pulled the 2026-09-19 base
  (3.12.14). CI's image smoke had been testing a base production does not run.
- **Which digest:** the one the deployed image was built on, so pinning changes no production byte.
  The pins are the same commits `v6` resolved to on 2026-09-27. Moving to 3.12.14 is a separate PR
  with its own deploy check.
- **Deliberately not done:** no Dependabot or Renovate. Pins move by hand, in a PR of their own.
