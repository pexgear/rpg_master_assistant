"""Turning a template into a campaign, and back to its starting point.

The whole value is that this is repeatable. Run it twice and you get two
campaigns that are the same in every way that matters: same characters, same
facts, same logins, same passwords, same storyline with nothing ticked off.

That is why nothing here generates anything. No random passwords, no timestamps
baked into content, no ids assumed. Entities are created in file order and
referred to by the template's own keys, so a share or a fact can point at "the
innkeeper" without knowing what row number the innkeeper turned out to be.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from canon_keeper import campaigns
from canon_keeper_core.db import connect, migrate
from canon_keeper_core.repo import Repos
from canon_keeper_core.repo.entities import Entity
from canon_keeper.templates import (
    PROGRESS_SETTING,
    SOURCE_SETTING,
    STORYLINE_SETTING,
    Template,
    TemplateError,
)

log = logging.getLogger("canonkeeper.templates.build")


def start(template: Template, name: str = "") -> campaigns.LocalCampaign:
    """Create a campaign from a template and return it."""
    campaign = campaigns.create_local(name or template.name)
    conn = connect(campaign.path)
    try:
        repos = Repos(conn)
        row = repos.campaigns.list()[0]
        populate(repos, row.id, template)
    finally:
        conn.close()
    log.info("started %r from template %r", campaign.name, template.id)
    return campaign


def restart(repos: Repos, campaign_id: int) -> None:
    """Put an open campaign back to the beginning of its template."""
    template = _source_template(str(repos.settings.get(SOURCE_SETTING, "") or ""))
    _empty(repos.conn, campaign_id)
    populate(repos, campaign_id, template)
    log.info("restarted campaign %s from %r", campaign_id, template.id)


def source(repos: Repos) -> str:
    """Which template an open campaign came from, or empty if it is its own."""
    return str(repos.settings.get(SOURCE_SETTING, "") or "")


def release(repos: Repos) -> None:
    """Stop treating an open campaign as a one-shot.

    Only the note goes. Nothing about the content changes, because there was
    never anything special about it -- which is the whole design.
    """
    repos.settings.set(SOURCE_SETTING, "")


def start_again(path: Path) -> None:
    """Put a campaign back to the beginning of its template.

    Everything in it goes. That is the point -- a one-shot run twice should be
    the same evening twice, not the same evening plus whatever the last table
    left lying about.
    """
    conn = connect(path)
    try:
        repos = Repos(conn)
        rows = repos.campaigns.list()
        if not rows:
            raise TemplateError("that campaign file is empty")
        restart(repos, rows[0].id)
    finally:
        conn.close()


def source_of(path: Path) -> str:
    """Which template a campaign came from, or empty if it is its own."""
    conn = connect(path)
    try:
        return source(Repos(conn))
    except Exception:  # noqa: BLE001 - a campaign we cannot read is not a template
        return ""
    finally:
        conn.close()


def keep(path: Path) -> None:
    """Stop treating this as a one-shot: make it a campaign of its own.

    Only the note is removed. Nothing about the content changes, because there
    was never anything special about it -- which is the whole design.
    """
    conn = connect(path)
    try:
        release(Repos(conn))
    finally:
        conn.close()


# ---------------------------------------------------------------------- filling


def populate(repos: Repos, campaign_id: int, template: Template) -> None:
    """Write a template's contents into an empty campaign."""
    ids = _create_entities(repos, campaign_id, template)
    _create_facts(repos, campaign_id, template, ids)
    accounts = _create_accounts(repos, campaign_id, template, ids)
    _create_shares(repos, campaign_id, template, ids, accounts)
    _create_encounter(repos, campaign_id, template, ids)

    repos.settings.set(SOURCE_SETTING, template.id)
    repos.settings.set(
        STORYLINE_SETTING, [beat.to_dict() for beat in template.storyline]
    )
    repos.settings.set(PROGRESS_SETTING, [])


def _create_entities(repos: Repos, campaign_id: int, template: Template) -> dict[str, int]:
    """In file order, so two runs number them identically."""
    ids: dict[str, int] = {}
    # Two passes: a room can name its building as a parent before the file has
    # got to it, and requiring authors to order the file correctly is a trap.
    for raw in template.entities:
        entity = repos.entities.create(
            Entity(
                id=None,
                campaign_id=campaign_id,
                kind=str(raw.get("kind", "npc")),
                name=str(raw.get("name", "")),
                summary=str(raw.get("summary", "")),
                aliases=list(raw.get("aliases") or []),
                data=dict(raw.get("data") or {}),
            )
        )
        ids[str(raw.get("key") or raw.get("name", ""))] = entity.id

    for raw in template.entities:
        parent = raw.get("parent")
        if not parent:
            continue
        key = str(raw.get("key") or raw.get("name", ""))
        entity = repos.entities.get(ids[key])
        entity.parent_id = ids.get(str(parent))
        repos.entities.update(entity)
    return ids


def _create_facts(
    repos: Repos, campaign_id: int, template: Template, ids: dict[str, int]
) -> None:
    for raw in template.facts:
        subject = raw.get("subject")
        repos.facts.assert_fact(
            campaign_id,
            ids.get(str(subject)) if subject else None,
            str(raw.get("predicate", "")),
            str(raw.get("object", "")),
            confirmed=True,
        )


def _create_accounts(
    repos: Repos, campaign_id: int, template: Template, ids: dict[str, int]
) -> dict[str, int]:
    """No logins at all. A template brings characters; people bring themselves.

    A template file lives in a public repository, so a password written in one
    is a *published* password. These used to be handed out as a convenience --
    "logins are marco, elsa and dana" -- which meant anybody who found a hosted
    one-shot could read the credentials out of the source and log in as
    somebody's character, or as the DM.

    The DM's own login is no exception, and that is the part worth saying out
    loud: a published DM password opens the whole campaign rather than one seat.
    The DM's own app does not need an account -- it holds the campaign file and
    logs in with a local token -- and a headless host is told who may run it
    with ``canonkeeper-server --add-dm``.

    So the field is read and ignored rather than removed. Old campaign templates
    still load, a login written into a new one is refused rather than honoured,
    and the rule does not depend on whoever writes the next template
    remembering it. Everybody arrives the same way now: an invite for a
    character, and a password only they ever know.
    """
    for raw in template.accounts:
        username = str(raw.get("username", ""))
        if username:
            log.warning(
                "ignoring the login %r in %r: templates bring characters and "
                "invitations, not passwords",
                username,
                template.id,
            )
    return {}


def _create_shares(
    repos: Repos,
    campaign_id: int,
    template: Template,
    ids: dict[str, int],
    accounts: dict[str, int],
) -> None:
    """What the party already knows when the evening starts.

    A one-shot that opens with nothing shared opens with four blank screens.
    """
    for raw in template.shares:
        entity_id = ids.get(str(raw.get("entity", "")))
        if entity_id is None:
            continue
        with_whom = raw.get("with")
        repos.shares.share(
            campaign_id,
            entity_id,
            accounts.get(str(with_whom)) if with_whom else None,
        )


def _create_encounter(
    repos: Repos, campaign_id: int, template: Template, ids: dict[str, int]
) -> None:
    """The fight the evening opens on, if it opens on one.

    Nothing is rolled here. The template states every initiative and every
    square, so a one-shot laid out for a second table is laid out identically --
    which is the whole property templates exist to have. A combatant naming an
    entity the template does not contain is skipped rather than fatal, in the
    same spirit as a share that points at nothing.
    """
    raw = template.encounter
    if not raw:
        return

    encounter = repos.encounters.create(
        campaign_id,
        name=str(raw.get("name", "")),
        width=int(raw.get("width") or 20),
        height=int(raw.get("height") or 15),
        running=bool(raw.get("running", True)),
    )
    for combatant in raw.get("combatants") or []:
        entity_id = ids.get(str(combatant.get("entity", "")))
        if entity_id is None:
            log.warning(
                "%s: a combatant names %r, which the template does not contain",
                template.id,
                combatant.get("entity"),
            )
            continue
        repos.encounters.add(
            encounter.id,
            entity_id=entity_id,
            initiative=_optional_int(combatant.get("initiative")),
            tiebreak=int(combatant.get("tiebreak") or 0),
            x=_optional_int(combatant.get("x")),
            y=_optional_int(combatant.get("y")),
        )

    # The room itself, before anyone is asked to stand in it.
    for square in raw.get("obstacles") or []:
        if isinstance(square, (list, tuple)) and len(square) == 2:
            repos.encounters.toggle_obstacle(
                encounter.id, int(square[0]), int(square[1])
            )

    # Starting it here rather than making the DM press it: a template that says
    # "this begins on initiative" should begin on initiative.
    if raw.get("begun", True):
        repos.encounters.begin(encounter.id)


def _optional_int(value) -> int | None:
    return int(value) if isinstance(value, int) else None


# --------------------------------------------------------------------- emptying


#: Everything a campaign accumulates. Ordered so a table is emptied before
#: whatever points at it.
_TABLES = (
    "message",
    "conversation",
    "chat_message",
    "pending_change",
    "proposal",
    # The fight before the grid it stands on, and both before the entities the
    # tokens point at.
    "combatant",
    "obstacle",
    "encounter",
    "fact",
    "utterance",
    "session",
    "share",
    "link",
    "account",
    "entity",
)


def _empty(conn, campaign_id: int) -> None:
    """Clear a campaign's contents, keeping the campaign row itself.

    Not `DELETE FROM campaign` and start over: the file is already open, is
    already named, and is already listed. Only what is in it goes.
    """
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    with conn:
        for table in _TABLES:
            if table not in existing:
                continue
            columns = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "campaign_id" in columns:
                conn.execute(f"DELETE FROM {table} WHERE campaign_id = ?", (campaign_id,))
            else:
                # Reached only through a parent that has already gone.
                conn.execute(f"DELETE FROM {table}")
        conn.execute(
            "UPDATE campaign SET created_at = ? WHERE id = ?",
            (time.time(), campaign_id),
        )


def _source_template(source: str) -> Template:
    from canon_keeper.templates import get

    if not source:
        raise TemplateError("that campaign did not come from a template")
    template = get(source)
    if template is None:
        raise TemplateError(
            f"the template it came from ({source}) is no longer installed"
        )
    return template
