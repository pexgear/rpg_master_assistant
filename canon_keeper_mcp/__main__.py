"""``canonkeeper-mcp`` -- expose one seat at a table over MCP.

Configure it in your MCP client, e.g.::

    {
      "mcpServers": {
        "canon-keeper": {
          "command": "canonkeeper-mcp",
          "args": ["--url", "wss://your-host.tailXXXX.ts.net", "--user", "marco"],
          "env": {"CANONKEEPER_PASSWORD": "..."}
        }
      }
    }

The password comes from ``CANONKEEPER_PASSWORD``. There is no prompt: an MCP
server is started by another program, with nobody watching a terminal to type
into.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading

from canon_keeper_client import AgentSession, LoginFailed
from canon_keeper_protocol import enrol
from canon_keeper_mcp import __version__
from canon_keeper_mcp.server import build_server

log = logging.getLogger("canonkeeper.mcp")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canonkeeper-mcp",
        description="Expose one Canon Keeper login as MCP tools.",
    )
    parser.add_argument("--url", required=True, help="ws:// or wss:// session address")
    parser.add_argument("--user", required=True, help="your login")
    parser.add_argument(
        "--invite",
        default="",
        help=(
            "an invite code, the first time only: makes the login your DM "
            "invited, with the username and password given here. Afterwards "
            "leave it off -- the account exists and logging in is the ordinary "
            "way in."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # An MCP server talks JSON-RPC on stdout. Anything else printed there
    # corrupts the stream, so logging goes to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    password = os.environ.get("CANONKEEPER_PASSWORD")
    if not password:
        print(
            "Set CANONKEEPER_PASSWORD. An MCP server has no terminal to prompt at.",
            file=sys.stderr,
        )
        return 1

    async def on_said(_session, _member, _text) -> None:
        """Chat arrives and is remembered on the table; nothing to do here.

        The MCP client reads it when it calls whats_happening, rather than
        being pushed at.
        """

    # A whole invite -- "wss://host#ABCDE-FGHIJ" -- can be pasted as the url and
    # carries its own code, because that is the shape a DM sends. The code lives
    # in the fragment, which is the part of a URL never sent to a server, which
    # is exactly what it needs to be.
    url, invite = args.url, args.invite
    if "#" in url and not invite:
        try:
            url, invite = enrol.unwrap(url)
        except enrol.EnrolError as exc:
            print(f"That invite could not be read: {exc}", file=sys.stderr)
            return 1

    session = AgentSession(url, args.user, password, on_said, invite=invite)
    ready = threading.Event()
    failure: list[BaseException] = []

    def pump() -> None:
        """Hold the session open on its own loop.

        MCPServer.run owns the main thread's loop, so the socket lives on
        another one. The two only ever meet through the Table, which is written
        here and read there.
        """
        async def go() -> None:
            try:
                await session.run()
            except BaseException as exc:  # noqa: BLE001 - reported to the caller
                failure.append(exc)
            finally:
                ready.set()

        asyncio.run(go())

    thread = threading.Thread(target=pump, daemon=True, name="canonkeeper-session")
    thread.start()

    # Wait for either a login or a failure before advertising any tools.
    for _ in range(200):
        if session.table.me is not None or failure:
            break
        ready.wait(0.1)

    if failure:
        exc = failure[0]
        kind = "Could not log in" if isinstance(exc, LoginFailed) else "Could not connect"
        print(f"{kind}: {exc}", file=sys.stderr)
        return 1
    if session.table.me is None:
        print(f"Timed out connecting to {url}.", file=sys.stderr)
        return 1

    log.info("connected to %s as %s", url, session.table.me.label)
    build_server(session).run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
