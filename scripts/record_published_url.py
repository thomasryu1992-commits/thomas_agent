#!/usr/bin/env python3
"""Record that a blog content package was published — the writer `published_url` never had.

`blog_content_package.v0.1` has carried `publish_state` / `published_url` / `published_at_utc`
since the schema shipped, with the note that the URL "is written back by the operator after
publishing" — and nothing could write it. REMAINING_WORK §J names the consequence as Phase 4's
missing prerequisite: *"nothing can tell the runtime a post was published."* This is that
writer, and only that:

    1) See what could be marked (read-only):
        python -m scripts.record_published_url --list
    2) Record the publish (appends one row; edits nothing):
        python -m scripts.record_published_url --package-id bcp_... \\
            --url https://blog.naver.com/...

The ledger is append-only, so "marking" a package published APPENDS a new package row under
the same `package_id` — the full record, copied from the newest row for that id, with
`publish_state` / `published_url` / `published_at_utc` set — and a reader takes the newest row
per id, which is the same convention every other ledger consumer already lives by. A second
run against an already-published package refuses (``ALREADY_PUBLISHED``) unless ``--replace``
is passed, which appends a corrected row the same way; the wrong URL stays visible in the
ledger's history, exactly like every other superseded row.

**This grants nothing and publishes nothing.** The operator already published, by hand, on a
platform this runtime has no credentials for; the runtime is being told after the fact — the
only direction the schema allows (`publish_state`'s own description: the runtime "has no way
to observe a publish it did not perform"). The appended row revalidates against the closed
schema, whose `if publish_state == published then published_url is required` rule is the
contract this script exists to satisfy.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import blog_content  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.store import LedgerStore  # noqa: E402
from runtime.read_only_kernel.schema_validation import (  # noqa: E402
    RuntimeSchemaError,
    validate_against_schema,
)

_ISO = "%Y-%m-%dT%H:%M:%SZ"
# Mirrors the schema's own pattern so the refusal names the rule instead of a validator trace.
_URL_PATTERN = re.compile(r"^https://blog\.naver\.com/.+")
_SCHEMA_PATH = ROOT / "schemas" / "blog_content_package.v0.1.schema.json"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Append the published-URL row for one blog content package.")
    p.add_argument("--list", action="store_true",
                   help="read-only: list every package's newest row (id, state, keyword, url)")
    p.add_argument("--package-id", help="the package to mark published")
    p.add_argument("--url", help="the published post's URL (https://blog.naver.com/...)")
    p.add_argument("--replace", action="store_true",
                   help="append a corrected row for a package that is ALREADY published "
                        "(without this, a second publish run refuses)")
    p.add_argument("--root", type=Path, default=Path("."),
                   help="repo root holding .runtime_governance_state (default: cwd)")
    return p.parse_args(argv)


def latest_packages(store: LedgerStore) -> dict[str, dict]:
    """Newest package row per package_id, archives included.

    Archives matter here more than anywhere: a package is published days after it was
    produced, which is exactly the window rotation moves rows out of the active file.
    """
    latest: dict[str, dict] = {}
    for row in store.iter_records_with_archive():
        if row.get("kind") != blog_content.PACKAGE_RECORD_KIND:
            continue
        record = row.get("record")
        if isinstance(record, dict) and isinstance(record.get("package_id"), str):
            latest[record["package_id"]] = record
    return latest


def build_published_row(package: dict, *, url: str, now: str) -> dict:
    """The published revision of one package — copy, stamp, revalidate. Pure."""
    updated = dict(package)
    updated["publish_state"] = "published"
    updated["published_url"] = url
    updated["published_at_utc"] = now
    try:
        validate_against_schema(updated, _SCHEMA_PATH, "blog_content_package")
    except RuntimeSchemaError as exc:
        raise ToolError(blog_content.BLOG_PACKAGE_SCHEMA_INVALID, str(exc)) from exc
    return updated


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.root.resolve()
    store = LedgerStore.default(root)

    if args.list:
        packages = latest_packages(store)
        if not packages:
            print("no blog content packages in the ledger")
            return 0
        for record in sorted(packages.values(), key=lambda r: str(r.get("created_at_utc"))):
            url = record.get("published_url", "-")
            print(f"{record['package_id']}  {record.get('publish_state'):9}  "
                  f"{record.get('created_at_utc')}  {record.get('target_keyword')}  {url}")
        return 0

    if not args.package_id or not args.url:
        print("ERROR: --package-id and --url are both required (or use --list)", file=sys.stderr)
        return 2
    if not _URL_PATTERN.match(args.url):
        print("ERROR URL_INVALID: the URL must start with https://blog.naver.com/ — "
              "that pattern is the schema's own rule, not this script's taste", file=sys.stderr)
        return 2

    # Same reasoning as the budget registrar: a host-side root run leaves ledger files the
    # uid-10001 services can no longer append to. Exit 3 matches the guard's other adopters.
    try:
        assert_not_foreign_root_run(root)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 3

    packages = latest_packages(store)
    package = packages.get(args.package_id)
    if package is None:
        print(f"ERROR PACKAGE_NOT_FOUND: no ledger row carries package_id {args.package_id} "
              f"(--list shows what exists)", file=sys.stderr)
        return 2
    if package.get("publish_state") == "published" and not args.replace:
        print(f"ERROR ALREADY_PUBLISHED: {args.package_id} already records "
              f"{package.get('published_url')} — pass --replace to append a corrected row",
              file=sys.stderr)
        return 2
    if args.replace and package.get("publish_state") != "published":
        print("ERROR REPLACE_WITHOUT_PUBLISH: --replace corrects an existing published row, "
              "and this package has none — run without --replace", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc).strftime(_ISO)
    try:
        updated = build_published_row(package, url=args.url, now=now)
        store.append_records(args.package_id, {blog_content.PACKAGE_RECORD_KIND: updated})
    except ToolError as exc:
        print(f"ERROR {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 2

    print(f"recorded publish for {args.package_id}")
    print(f"  keyword:   {updated.get('target_keyword')}")
    print(f"  url:       {updated['published_url']}")
    print(f"  published: {updated['published_at_utc']}")
    print("Read it back with --list rather than assuming the row landed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
