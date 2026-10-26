"""The blog department's lane (BLOG_DEPARTMENT v0.2, Thomas 2026-10-26): the steps of blog production that can run
inside the system. Drafting, publishing and stats collection stay outsourced (v0.2 §3).

P2 brings verification in. ``preflight``, ``variety`` and ``taxonomy`` are generated from the vault's own checkers by
``scripts/blog_vendor.py`` (no network, no writes, paths from the caller). ``verify`` runs them on a reported draft
and compares the verdict with the one the drafting cron reported. The core reaches this package only through
function-local imports at the scheduler's fire site.
"""
