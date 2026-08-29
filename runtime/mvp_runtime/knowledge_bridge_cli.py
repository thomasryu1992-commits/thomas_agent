"""Run the knowledge door (``knowledge_bridge``) as a long-lived listener.

    python -m runtime.mvp_runtime.knowledge_bridge_cli
    python -m runtime.mvp_runtime.knowledge_bridge_cli --socket /path/to/knowledge.sock

Separate service from the other doors for the reason they are separate from each other:
different cost and different failure. An ingest runs a subprocess and re-indexes the corpus,
so a wedged ingest must not be able to wedge the stop path — which is the property the
switch door was split into its own service to hold.

Holds no schedule, runs no pipeline, and makes no outbound call. It waits on a socket,
stores what it is given, and searches what it has stored.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import knowledge_bridge, socket_door
from .cli_common import force_utf8_io, serve_door_forever
from .control import ControlStore
from .knowledge import pdf_text
from .knowledge.store import KnowledgeStore


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="knowledge_bridge_cli",
        description=(
            "Knowledge-base door on a unix socket. Serves add_document (append, refused "
            "while halted), query and stats (reads, answered while halted)."
        ),
    )
    parser.add_argument(
        "--socket", default=None,
        help="socket path (default: MVP_KNOWLEDGE_BRIDGE_SOCKET, else the per-machine state dir)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    path = Path(args.socket) if args.socket else knowledge_bridge.socket_path()
    store = KnowledgeStore.default()
    return serve_door_forever(
        label="KNOWLEDGE_BRIDGE", path=path,
        open_server=lambda: knowledge_bridge.open_door(
            path, store=store, control_store=ControlStore.default(),
        ),
        # The PDF backends are reported at startup, not discovered at the first ingest. A
        # host with an empty chain answers every ingest with PDF_NO_BACKEND, and finding
        # that out from a refusal hours later — rather than from the line the service
        # printed when it came up — is the difference between a provisioning gap and a
        # mystery.
        banner=lambda server: (
            f"verbs={sorted(knowledge_bridge._COMMANDS)}, "
            f"pdf_backends={list(pdf_text.available_backends()) or 'NONE — PDF ingest will fail closed'}, "
            f"{socket_door.describe_admission(server)}"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
