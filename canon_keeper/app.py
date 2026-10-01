"""Application bootstrap: choose a campaign, then open the workspace.

The campaign comes first. Everything else -- panels, sessions, sharing -- is
about one campaign, so no window is built until the app knows which one.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from canon_keeper import __version__, assets, campaigns, config, credentials
from canon_keeper.bus import Bus
from canon_keeper_core.db import connect, migrate
from canon_keeper.naming import PanelNames
from canon_keeper.net.state import SharedState
from canon_keeper.plugin import AppContext, PendingJoin
from canon_keeper_core.repo import Repos
from canon_keeper.shell.loader import discover_panels
from canon_keeper.shell.main_window import MainWindow
from canon_keeper.shell.startup import Launch, choose_campaign
from canon_keeper.shell.theme import ThemeController


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="canonkeeper", description="Canon Keeper")
    parser.add_argument("--version", action="version", version=f"canon-keeper {__version__}")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="open this campaign file directly, skipping the chooser",
    )
    parser.add_argument(
        "--player", action="store_true", help="open the chooser on the join tab"
    )
    parser.add_argument(
        "--choose",
        action="store_true",
        help="always show the chooser, ignoring any automatic campaign",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def build_context(
    db_path: Path,
    log: logging.Logger,
    role: str = "dm",
    shared: SharedState | None = None,
) -> tuple[AppContext, object]:
    """Open the database, migrate it, and assemble the panel context."""
    conn = connect(db_path)
    version = migrate(conn)
    log.info("database %s at schema version %s", db_path, version)

    repos = Repos(conn)
    campaign = repos.campaigns.ensure_default()
    ctx = AppContext(
        repos=repos,
        bus=Bus(),
        log=log,
        campaign_id=campaign.id,
        role=role,
        shared=shared if shared is not None else SharedState(),
        names=PanelNames(repos.settings, is_dm=role == "dm"),
    )
    ctx.names.changed.connect(ctx.bus.panel_names_changed)
    return ctx, conn


def _from_autostart(log: logging.Logger) -> Launch | None:
    """Rebuild a launch from the saved automatic choice, if it is still usable."""
    entry = campaigns.get_autostart()
    if entry is None:
        return None

    if entry.kind == "local":
        return Launch(kind="local", path=Path(entry.path), name=entry.name)

    password = credentials.load(entry.url, entry.username)
    if not password:
        # Saved without a password, or the credential store said no. Ask rather
        # than failing a login the user never got to see.
        log.info("no saved password for %s; showing the chooser", entry.url)
        return None
    return Launch(
        kind="remote",
        url=entry.url,
        username=entry.username,
        password=password,
        name=entry.name,
    )


def _resolve_launch(
    args, log: logging.Logger, force_chooser: bool = False
) -> Launch | None:
    """Decide which campaign to open, asking unless we already know."""
    if args.db is not None:
        return Launch(
            kind="local", path=args.db, name=campaigns.campaign_name_of(args.db)
        )
    if not force_chooser and not args.choose:
        automatic = _from_autostart(log)
        if automatic is not None:
            log.info("opening %r automatically", automatic.name or automatic.url)
            return automatic
    return choose_campaign(start_online=args.player)


#: Longest the chooser will sit there before giving up on a join. Comfortably
#: past the client's own connect timeout and one scrypt, so this only fires when
#: something has genuinely stopped answering rather than merely being slow.
JOIN_TIMEOUT_MS = 30_000


def _wait_for_the_join(ctx, log: logging.Logger) -> str:
    """Block until the launch join resolves. Empty on success, else the reason.

    The window is already built, which is what makes this cheap: the Table
    panel is connecting with the one connection it will keep, so waiting costs
    nothing on the wire and the table does not watch somebody join, leave and
    join again while their app makes up its mind.
    """
    outcome: dict = {}
    loop = QEventLoop()

    def settled(ok: bool, reason: str) -> None:
        outcome["ok"] = ok
        outcome["reason"] = reason
        loop.quit()

    ctx.bus.session_ready.connect(settled)
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(JOIN_TIMEOUT_MS)
    try:
        loop.exec()
    finally:
        guard.stop()
        try:
            ctx.bus.session_ready.disconnect(settled)
        except (RuntimeError, TypeError):  # pragma: no cover - already gone
            pass

    if not outcome:
        log.warning("the host did not answer within %d ms", JOIN_TIMEOUT_MS)
        return "The host did not answer."
    return "" if outcome["ok"] else (outcome["reason"] or "That did not work.")


def _remember_choices(launch: Launch) -> None:
    """Apply the "remember me" and "open automatically" boxes."""
    if launch.is_remote:
        campaigns.remember_remote(launch.url, launch.name, launch.username)
        # Saved without being asked. This only runs once the host has accepted
        # the login, so what goes into the credential store is known to work --
        # and being asked "shall I remember this?" every time you join your own
        # weekly game is a question with one answer.
        credentials.save(launch.url, launch.username, launch.password)

    if launch.autostart:
        campaigns.set_autostart(
            campaigns.Autostart(
                kind=launch.kind,
                path=str(launch.path or ""),
                url=launch.url,
                username=launch.username,
                name=launch.name,
            )
        )
    else:
        # Unticking the box on the campaign that was set must actually clear it.
        current = campaigns.get_autostart()
        if current is not None and (
            (launch.kind == "local" and current.path == str(launch.path or ""))
            or (launch.is_remote and current.url == launch.url)
        ):
            campaigns.clear_autostart()


#: What Windows files this application under. Without it a Python app is
#: grouped in the taskbar as *python.exe* and shown with Python's icon, however
#: carefully the window icon was set.
APP_ID = "pexgear.CanonKeeper"


def _own_the_taskbar_entry(log: logging.Logger) -> None:
    """Tell Windows this is its own application. A no-op everywhere else.

    Says so in the log when it will not work, because it fails by *succeeding*:
    a process running under the Microsoft Store Python has package identity,
    Windows takes the taskbar button's icon from that package's manifest, and
    the call below returns S_OK while changing nothing. The symptom is the
    Python logo in the taskbar beside a correct icon in the title bar, and
    nothing anywhere to explain it. A venv inherits this from the interpreter
    it was built with, so the cure is a python.org interpreter rather than a
    fresh venv from the same base.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        length = ctypes.c_uint32(0)
        # Anything but APPMODEL_ERROR_NO_PACKAGE means we are inside one.
        if ctypes.windll.kernel32.GetCurrentPackageFullName(
            ctypes.byref(length), None
        ) != 15700:
            log.info(
                "running under a packaged interpreter (%s), so Windows will "
                "show its icon in the taskbar rather than ours",
                sys.executable,
            )
    except Exception:  # noqa: BLE001 - a cosmetic call is never worth a crash
        log.debug("could not claim a taskbar identity", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = config.setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    log.info("Canon Keeper %s starting", __version__)
    # Before anything Qt: Windows decides which taskbar button a process owns
    # the first time it puts one there, and it does not revisit the question.
    _own_the_taskbar_entry(log)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Canon Keeper")
    app.setApplicationVersion(__version__)
    app.setOrganizationName(config.APP_AUTHOR)
    # Fusion keeps the three platforms looking like the same application.
    app.setStyle("Fusion")
    # Set on the application rather than per window, so every window, dialog
    # and undocked panel carries it without anybody remembering to.
    icon = QIcon(str(assets.icon_path()))
    if not icon.isNull():
        app.setWindowIcon(icon)
    else:
        log.warning("the application icon did not load from %s", assets.icon_path())

    # The chooser appears before any campaign is open, so its appearance comes
    # from the profile -- otherwise a dark-mode user gets a flash of white.
    profile_ctx, _profile_conn = build_context(config.profile_db_path(), log)
    ThemeController(app, profile_ctx.repos.settings).apply()

    # Looping lets "Open a Different Campaign" work without restarting: the
    # window closes, the chooser comes back, and a new context is built.
    force_chooser = False
    while True:
        launch = _resolve_launch(args, log, force_chooser)
        if launch is None:
            log.info("no campaign chosen; exiting")
            return 0

        # A player's settings and dock layout live in their own profile: the
        # campaign they joined is not theirs to write to, and may not be a
        # local file at all.
        db_path = launch.path if launch.kind == "local" else config.profile_db_path()

        try:
            ctx, conn = build_context(db_path, log, role=launch.role)
        except Exception as exc:  # noqa: BLE001 - show something actionable
            log.exception("could not open the campaign")
            QMessageBox.critical(
                None, "Canon Keeper", f"Could not open:\n\n{db_path}\n\n{exc}"
            )
            return 1

        if launch.is_remote:
            # Picked up by the Table panel, which connects once it is built.
            ctx.pending_join = PendingJoin(
                launch.url, launch.username, launch.password, launch.invite
            )

        theme = ThemeController(app, ctx.repos.settings)
        theme.changed.connect(ctx.bus.theme_changed)
        theme.apply()

        panels, errors = discover_panels(log, role=launch.role)
        if not panels:
            log.warning("no panels were discovered; is the package installed?")

        window = MainWindow(ctx, panels, errors, log, theme)
        if launch.is_remote:
            window.setWindowTitle(
                f"Canon Keeper - {launch.name or launch.url} (player)"
            )
            # Built but not shown. A wrong password should leave you in the
            # chooser being asked again, not inside an empty app with the
            # reason buried in a chat log you have no session for.
            problem = _wait_for_the_join(ctx, log)
            if problem:
                log.info("could not join %s: %s", launch.url, problem)
                window.close()
                window.deleteLater()
                conn.close()
                QMessageBox.warning(
                    None, "Could not join", f"{problem}\n\nTry again."
                )
                force_chooser = True
                continue

        # Only now is the login known to be good, so only now is it worth
        # remembering -- and worth opening automatically next time.
        _remember_choices(launch)
        window.show()
        app.exec()

        if not window.switch_requested:
            return 0
        conn.close()
        force_chooser = True
