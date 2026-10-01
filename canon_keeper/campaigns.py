"""Finding campaigns: files on this machine, and servers you have joined.

A campaign is one SQLite file, so "the campaigns on this computer" is just the
files in the campaigns directory. There is no index to fall out of step with
what is actually on disk -- copy a file in and it appears, delete it and it is
gone.

Remembered servers live in a small JSON file instead, because a campaign you
join is not a file you own: all you keep is where it was and who you were.
"""

from __future__ import annotations

import json
import logging
import secrets
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from canon_keeper import config
from canon_keeper_core.db import connect, migrate

log = logging.getLogger("canonkeeper.campaigns")

SUFFIX = ".sqlite3"
RECENT_SERVERS_FILE = "servers.json"
AUTOSTART_FILE = "autostart.json"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9 _-]+")


@dataclass(slots=True)
class LocalCampaign:
    path: Path
    name: str
    opened_at: float = 0.0

    @property
    def label(self) -> str:
        return self.name or self.path.stem


@dataclass(slots=True)
class RemoteCampaign:
    url: str
    name: str = ""
    username: str = ""
    last_joined: float = 0.0

    @property
    def label(self) -> str:
        return self.name or self.url


@dataclass(slots=True)
class Autostart:
    """The campaign to open without asking, if the user chose one."""

    kind: str  # "local" or "remote"
    path: str = ""
    url: str = ""
    username: str = ""
    name: str = ""


# --------------------------------------------------------------------- local


def campaign_name_of(path: Path) -> str:
    """Read a campaign's name without keeping the database open."""
    try:
        conn = connect(path)
    except Exception:  # noqa: BLE001 - an unreadable file is simply skipped
        return path.stem
    try:
        row = conn.execute(
            "SELECT name FROM campaign ORDER BY created_at LIMIT 1"
        ).fetchone()
        return row["name"] if row else path.stem
    except Exception:  # noqa: BLE001 - not a campaign file, or too old to read
        return path.stem
    finally:
        conn.close()


def list_local() -> list[LocalCampaign]:
    """Every campaign file in the campaigns directory, newest first."""
    found: list[LocalCampaign] = []
    for path in sorted(config.campaigns_dir().glob(f"*{SUFFIX}")):
        try:
            opened_at = path.stat().st_mtime
        except OSError:
            opened_at = 0.0
        found.append(LocalCampaign(path, campaign_name_of(path), opened_at))
    return sorted(found, key=lambda c: c.opened_at, reverse=True)


def path_for(name: str) -> Path:
    """A filename derived from the campaign name, without collisions."""
    stem = _SAFE_NAME.sub("", name).strip().replace(" ", "-").lower() or "campaign"
    candidate = config.campaigns_dir() / f"{stem}{SUFFIX}"
    counter = 2
    while candidate.exists():
        candidate = config.campaigns_dir() / f"{stem}-{counter}{SUFFIX}"
        counter += 1
    return candidate


def create_local(name: str) -> LocalCampaign:
    """Make a new campaign file, migrated and named."""
    path = path_for(name)
    conn = connect(path)
    try:
        migrate(conn)
        with conn:
            conn.execute(
                "INSERT INTO campaign (name, created_at) VALUES (?, ?)",
                (name.strip() or "New Campaign", time.time()),
            )
    finally:
        conn.close()
    log.info("created campaign %r at %s", name, path)
    return LocalCampaign(path, name.strip() or "New Campaign", time.time())


def delete_local(path: Path) -> None:
    """Remove a campaign file, and the WAL sidecars SQLite leaves behind."""
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


# -------------------------------------------------------------------- remote


def _servers_path() -> Path:
    return config.data_dir() / RECENT_SERVERS_FILE


def list_remote() -> list[RemoteCampaign]:
    path = _servers_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("could not read %s; starting with an empty list", path)
        return []
    if not isinstance(raw, list):
        return []

    servers: list[RemoteCampaign] = []
    for entry in raw:
        if isinstance(entry, dict) and entry.get("url"):
            servers.append(
                RemoteCampaign(
                    url=str(entry["url"]),
                    name=str(entry.get("name", "")),
                    username=str(entry.get("username", "")),
                    last_joined=float(entry.get("last_joined", 0) or 0),
                )
            )
    return sorted(servers, key=lambda s: s.last_joined, reverse=True)


def remember_remote(url: str, name: str = "", username: str = "") -> None:
    """Record a server we joined, so it is one click next time.

    The password is deliberately not kept: it is the one thing that would turn a
    stolen laptop into a stolen campaign.
    """
    servers = [s for s in list_remote() if s.url != url]
    servers.insert(
        0, RemoteCampaign(url=url, name=name, username=username, last_joined=time.time())
    )
    _write_remote(servers[:20])


def forget_remote(url: str) -> None:
    _write_remote([s for s in list_remote() if s.url != url])


# ----------------------------------------------------------------- autostart


def _autostart_path() -> Path:
    return config.data_dir() / AUTOSTART_FILE


def get_autostart() -> Autostart | None:
    """The campaign to open on launch, or None to show the chooser."""
    path = _autostart_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("could not read %s; ignoring it", path)
        return None
    if not isinstance(raw, dict) or raw.get("kind") not in ("local", "remote"):
        return None

    entry = Autostart(
        kind=str(raw["kind"]),
        path=str(raw.get("path", "")),
        url=str(raw.get("url", "")),
        username=str(raw.get("username", "")),
        name=str(raw.get("name", "")),
    )
    # A campaign file that has since been deleted or moved must not lock the
    # user out of their own app.
    if entry.kind == "local" and not Path(entry.path).exists():
        log.info("autostart campaign %s is gone; showing the chooser", entry.path)
        clear_autostart()
        return None
    return entry


def set_autostart(entry: Autostart | None) -> None:
    if entry is None:
        clear_autostart()
        return
    try:
        _autostart_path().write_text(
            json.dumps(asdict(entry), indent=2), encoding="utf-8"
        )
    except OSError:
        log.warning("could not save the autostart choice")


def clear_autostart() -> None:
    path = _autostart_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            log.warning("could not clear the autostart choice")


def _write_remote(servers: list[RemoteCampaign]) -> None:
    try:
        _servers_path().write_text(
            json.dumps([asdict(s) for s in servers], indent=2, default=str),
            encoding="utf-8",
        )
    except OSError:
        log.warning("could not save the server list")


# ------------------------------------------------------------ campaign identity


#: Setting holding a campaign's own random id.
CAMPAIGN_KEY = "campaign_key"


def campaign_key(repos) -> str:
    """A stable, unique id for this campaign, minted on first use.

    ``campaign.id`` is not one. Every campaign is its own SQLite file, so almost
    every campaign in existence has ``id = 1`` -- fine inside a file, useless
    the moment two of them are told apart from outside: a cache, a credential
    store, a reconnecting client holding versions from somewhere else.
    """
    key = repos.settings.get(CAMPAIGN_KEY, "")
    if not isinstance(key, str) or not key:
        key = secrets.token_hex(16)
        repos.settings.set(CAMPAIGN_KEY, key)
    return key
