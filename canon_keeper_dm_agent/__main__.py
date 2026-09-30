"""``canonkeeper-agent`` -- connect to a session and answer when let.

    canonkeeper-agent --url ws://192.168.1.20:8766 --user autopilot

The password is read from ``CANONKEEPER_AGENT_PASSWORD`` or prompted for. It is
never written anywhere: it exists to derive a verifier at login and is then just
a string in memory, the same as in the app.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import sys

from canon_keeper_dm_agent import __version__
from canon_keeper_dm_agent.brain import Brain, is_available, unavailable_hint
from canon_keeper_client import AgentSession, LoginFailed
from canon_keeper_dm_agent.responder import QUIET_FOR, Responder
from canon_keeper_dm_agent.tools import Tools

log = logging.getLogger("canonkeeper.agent")

#: How long the model's thread waits for a tool call to come back. Generous:
#: setting a fight up is several frames and two round trips to the host, and
#: giving up early would leave a half-built map on everyone's screen.
TOOL_TIMEOUT = 30.0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canonkeeper-agent",
        description="Run a Canon Keeper table while the DM has autopilot on.",
    )
    parser.add_argument("--url", required=True, help="ws:// or wss:// session address")
    parser.add_argument("--user", required=True, help="the agent login")
    parser.add_argument("--model", default="", help="which model to answer with")
    parser.add_argument(
        "--workspace",
        default="",
        help=(
            "your Anthropic workspace id. Only needed for keys that are "
            "refused without one -- the API says so if yours is."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what it would say instead of saying it",
    )
    parser.add_argument(
        "--talk-only",
        action="store_true",
        help=(
            "answer, but never touch the map. Without it the agent can start a "
            "fight, place people and move them -- always through the host, and "
            "only while autopilot is on."
        ),
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=QUIET_FOR,
        metavar="SECONDS",
        help=(
            "how long the table must be quiet before it answers "
            f"(default {QUIET_FOR}). Raise it if it keeps cutting people off."
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="log every decision")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


async def _run(args) -> int:
    password = os.environ.get("CANONKEEPER_AGENT_PASSWORD") or getpass.getpass(
        f"Password for {args.user}: "
    )
    brain = Brain(model=args.model, workspace=args.workspace)
    loop = asyncio.get_running_loop()

    session: AgentSession | None = None

    def run_tool(name: str, arguments: dict) -> str:
        """Bridge from the model's thread back to the socket's event loop.

        The model call is blocking and runs in a worker thread; the session is
        asyncio and belongs to this loop. Handing the coroutine across rather
        than touching the socket from the thread is what keeps that true.
        """
        if session is None:
            return "Not connected to a table, so nothing was changed."
        future = asyncio.run_coroutine_threadsafe(
            Tools(session).run(name, arguments), loop
        )
        try:
            return future.result(timeout=TOOL_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - a stuck tool must not end the turn
            log.exception("tool %s did not finish", name)
            return f"That did not finish: {exc}"

    def answer(table, spoken: list[tuple[str, str]]) -> str:
        """Runs off the event loop; a model call takes seconds.

        Nothing is caught here: the responder turns a failure into a line the
        DM sees. Swallowing it would put the agent back to going quiet for
        reasons nobody can discover.
        """
        acting = None if (args.talk_only or args.dry_run) else run_tool
        return brain.answer(table, spoken, run_tool=acting)

    async def say(reply: str) -> None:
        if args.dry_run:
            print(f"\n[would say] {reply}\n")
            return
        if session is not None and not await session.say(reply):
            log.info("not sent -- autopilot went off while we were thinking")

    async def on_busy(on: bool) -> None:
        if session is None:
            return
        await session.set_busy(on)
        if not on:
            # Reported after each turn rather than at the end: a bill you only
            # see when you close the app is not a bill you can act on.
            await session.report_spend(
                tokens_in=brain.total.input_tokens,
                tokens_out=brain.total.output_tokens,
                cached=brain.total.cached_tokens,
                dollars=round(brain.total.dollars, 4),
                turns=brain.turns,
                model=args.model or "",
            )

    async def on_trouble(message: str) -> None:
        if args.dry_run:
            print(f"\n[could not answer] {message}\n")
            return
        if session is not None:
            await session.report_trouble(message)

    responder = Responder(
        answer, say, quiet_for=args.pause, on_busy=on_busy, on_trouble=on_trouble
    )

    async def on_said(current: AgentSession, member, text: str) -> None:
        # Returns immediately: the answer happens on its own task, so the
        # socket keeps being read while the model is busy.
        await responder.heard(current, member, text)

    async def on_encounter(current: AgentSession) -> None:
        # Nobody says "it is the goblin's turn" out loud, so the map arriving
        # is the only way to find out that one is ours to take.
        await responder.turn_came_round(current)

    async def on_translate(current: AgentSession, request: dict) -> None:
        # The DM asked for this one by name. No pause and no lull: the click
        # already was the cue.
        await responder.asked_to_translate(current, request)

    session = AgentSession(
        args.url,
        args.user,
        password,
        on_said,
        on_encounter=on_encounter,
        on_translate=on_translate,
    )
    try:
        await session.run()
    except LoginFailed as exc:
        print(f"Could not log in: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Could not reach {args.url}: {exc}", file=sys.stderr)
        return 1
    finally:
        await responder.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.dry_run and not is_available():
        print(f"No model to answer with: {unavailable_hint()}", file=sys.stderr)
        return 1

    print(
        f"Connecting to {args.url} as {args.user}.\n"
        "It answers only while the DM has autopilot switched on"
        + (
            ", and will not touch the map.\n"
            if args.talk_only
            else ", and can run a fight on the shared map.\n"
        )
        + "Ctrl-C to stop."
    )
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
