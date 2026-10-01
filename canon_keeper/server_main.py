"""Headless session host: ``canonkeeper-server``.

The same :class:`~canon_keeper.net.server.SessionServer` the desktop app runs,
with no GUI around it. Use this when the session should outlive the DM's laptop
-- on a spare machine on the LAN, or on a box everyone can reach.

A server hosts exactly one campaign, so it needs that campaign's file. Accounts
live in the same file, which is why creating them is a job for this command too:
a dedicated server has no other way to be told who is allowed in.

    canonkeeper-server --db our-campaign.sqlite3 --invite Marla
    canonkeeper-server --db our-campaign.sqlite3
"""

from __future__ import annotations

import argparse
import getpass
import logging
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer

from canon_keeper import __version__, config
from canon_keeper_core.db import connect, migrate
from canon_keeper_protocol import enrol
from canon_keeper_protocol.auth import AuthError
from canon_keeper.net.server import DEFAULT_PORT, SessionServer
from canon_keeper_core.repo import Repos
from canon_keeper_core.repo.entities import KIND_PC


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="canonkeeper-server", description="Host a Canon Keeper session."
    )
    parser.add_argument("--version", action="version", version=f"canon-keeper {__version__}")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="campaign database to host (default: the per-user data directory)",
    )
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("-n", "--name", default=None, help="session name (default: the campaign)")
    parser.add_argument(
        "--no-announce",
        action="store_true",
        help="do not broadcast on the LAN; players must be given the address",
    )

    accounts = parser.add_argument_group("accounts")
    accounts.add_argument("--list-players", action="store_true", help="show who may log in")
    accounts.add_argument(
        "--invite",
        metavar="CHARACTER",
        help="make a one-use invite code for a character, and print it",
    )
    accounts.add_argument(
        "--characters", action="store_true", help="show who can be invited"
    )
    accounts.add_argument(
        "--address",
        metavar="URL",
        help="the address players connect to, folded into --invite output",
    )
    accounts.add_argument("--add-dm", metavar="USERNAME", help="create a DM login")
    accounts.add_argument(
        "--add-agent",
        metavar="USERNAME",
        help="create an autopilot login for an agent to answer with",
    )
    accounts.add_argument("--set-password", metavar="USERNAME", help="change a password")
    accounts.add_argument("--remove-player", metavar="USERNAME", help="delete a login")
    accounts.add_argument(
        "--password",
        default=None,
        help="password to use, instead of being prompted (visible in shell history)",
    )

    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def _ask_password(args) -> str:
    if args.password:
        return args.password
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat: "):
        raise SystemExit("The passwords did not match.")
    return first


def _manage_accounts(args, repos: Repos, campaign_id: int) -> bool:
    """Handle any account flags. Returns True if the command was account-only."""
    handled = False

    if args.list_players:
        handled = True
        people = repos.accounts.list(campaign_id)
        if not people:
            print("No logins yet. Invite somebody with --invite CHARACTER.")
        for account in people:
            character = ""
            if account.character_entity_id is not None:
                entity = repos.entities.get(account.character_entity_id)
                character = f" playing {entity.name}" if entity else ""
            state = " (disabled)" if account.disabled else ""
            print(f"  {account.username:20} {account.role:6}{character}{state}")

    if args.characters:
        handled = True
        for entity in repos.entities.list(campaign_id, kinds=(KIND_PC,)):
            seat = next(
                (
                    a
                    for a in repos.accounts.list(campaign_id)
                    if a.character_entity_id == entity.id
                ),
                None,
            )
            waiting = repos.invites.waiting_for(entity.id)
            if seat is not None:
                state = f"played by {seat.username}"
            elif waiting is not None:
                state = f"invited ({waiting.code})"
            else:
                state = "nobody yet"
            print(f"  {entity.name:24} {state}")

    if args.invite:
        handled = True
        wanted = args.invite.strip().lower()
        found = [
            e
            for e in repos.entities.list(campaign_id, kinds=(KIND_PC,))
            if e.name.strip().lower() == wanted
        ]
        if not found:
            raise SystemExit(
                f"No character called {args.invite!r}. Try --characters."
            )
        invite = repos.invites.create(campaign_id, found[0].id)
        # The whole thing, address included, so what gets pasted into a chat
        # window is one line and the player has nothing to assemble.
        whole = enrol.wrap(args.address or "", invite.code)
        print(f"Invite for {found[0].name}: {whole}")
        if not args.address:
            print(
                "No --address given, so that is the code alone. Add "
                "--address wss://your-host to send one line."
            )
        print("Good for 24 hours, and once. Making another one cancels it.")
        print("Send it the way you would send a password.")

    for flag, role in (
        (args.add_dm, "dm"),
        (args.add_agent, "agent"),
    ):
        if not flag:
            continue
        handled = True
        try:
            account = repos.accounts.create(campaign_id, flag, _ask_password(args), role=role)
        except (ValueError, AuthError) as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Created {role} login {account.username!r}.")

    if args.set_password:
        handled = True
        account = repos.accounts.by_username(campaign_id, args.set_password)
        if account is None:
            raise SystemExit(f"No login called {args.set_password!r}.")
        try:
            repos.accounts.set_password(account.id, _ask_password(args))
        except AuthError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Password changed for {account.username!r}.")

    if args.remove_player:
        handled = True
        account = repos.accounts.by_username(campaign_id, args.remove_player)
        if account is None:
            raise SystemExit(f"No login called {args.remove_player!r}.")
        repos.accounts.delete(account.id)
        print(f"Removed {account.username!r}.")

    return handled


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = config.setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    db_path = args.db or config.default_db_path()
    conn = connect(db_path)
    migrate(conn)
    repos = Repos(conn)
    campaign = repos.campaigns.ensure_default()

    if _manage_accounts(args, repos, campaign.id):
        return 0

    if not repos.accounts.list(campaign.id) and not repos.invites.live(campaign.id):
        print("This campaign has nobody in it and nobody invited to it.")
        print(f"  canonkeeper-server --db {db_path} --characters")
        print(f"  canonkeeper-server --db {db_path} --invite CHARACTER")
        return 1

    app = QCoreApplication(sys.argv[:1])
    server = SessionServer(repos, campaign.id, args.name or campaign.name)

    server.roster_changed.connect(
        lambda members: log.info(
            "%d connected: %s", len(members), ", ".join(m.label for m in members)
        )
    )
    server.failed.connect(lambda message: log.error("%s", message))

    if not server.start(args.port, announce=not args.no_announce):
        return 1

    print(f"Canon Keeper hosting {campaign.name!r}")
    print(f"  campaign:  {db_path}")
    print(f"  port:      {server.port}")
    print(f"  logins:    {', '.join(a.username for a in repos.accounts.list(campaign.id))}")
    print("  Ctrl-C to stop.")

    def shutdown(*_args) -> None:
        log.info("shutting down")
        server.stop()
        app.quit()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)
    # Qt's event loop blocks Python signal delivery unless it wakes periodically.
    heartbeat = QTimer()
    heartbeat.start(400)
    heartbeat.timeout.connect(lambda: None)

    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
