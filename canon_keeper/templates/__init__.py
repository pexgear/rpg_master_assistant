"""Campaigns that start the same way every time.

Two uses, and they want the same thing from different directions. A **one-shot**
is an evening that begins somewhere specific — the party is already in the inn,
the bodies are already in the cellar — and a DM running it for a second table
wants exactly the evening they ran before. **Testing** wants a table with four
players, logins that already exist, and a world with something in it, in one
click rather than twenty minutes of typing.

So a template is a *description* of a starting point, and starting one builds a
real campaign from it. Three rules follow:

**Deterministic.** Starting the same template twice produces the same campaign:
same characters, same facts, same logins, same passwords. A template that
generated its own passwords would be useless for handing out and useless for a
test that expects to log in.

**It becomes an ordinary campaign.** What you get is a normal `.sqlite3` file
with nothing special about it. The only trace is a note of which template it
came from, which is what makes **Start again** possible — and clearing that note
is how a one-shot becomes a campaign you keep.

**Data, not code.** Templates are JSON. Adding one is writing a file, and a
broken one is reported rather than fatal, exactly like a plugin.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from canon_keeper_core.repo.entities import KIND_PC

log = logging.getLogger("canonkeeper.templates")

BUNDLED = Path(__file__).parent / "data"

#: Settings key naming the template a campaign was built from. Its presence is
#: what makes "start again" offerable, and removing it is what makes a one-shot
#: into a campaign of your own.
SOURCE_SETTING = "template.source"

#: Settings key holding the storyline: the beats, and what ends it.
STORYLINE_SETTING = "template.storyline"

#: Settings key holding which beats are done.
PROGRESS_SETTING = "template.progress"


class TemplateError(ValueError):
    """A template could not be read, and the message says which and why."""


@dataclass(frozen=True)
class Beat:
    """One step of the storyline."""

    id: str
    title: str
    #: What the DM might read out or paraphrase. Never sent to players.
    read_aloud: str = ""
    #: How the DM knows this beat is done.
    done_when: str = ""
    #: True for the beat that finishes the adventure. A one-shot that never
    #: says where it stops is a campaign.
    ends_it: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "read_aloud": self.read_aloud,
            "done_when": self.done_when,
            "ends_it": self.ends_it,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Beat":
        return cls(
            id=str(raw.get("id", "")),
            title=str(raw.get("title", "")),
            read_aloud=str(raw.get("read_aloud", "")),
            done_when=str(raw.get("done_when", "")),
            ends_it=bool(raw.get("ends_it")),
        )


@dataclass(frozen=True)
class Template:
    """A starting point, read from one JSON file."""

    id: str
    name: str
    #: One line for the chooser.
    summary: str = ""
    #: Longer text: what the evening is, how long it runs, who it suits.
    about: str = ""
    entities: list[dict] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    accounts: list[dict] = field(default_factory=list)
    shares: list[dict] = field(default_factory=list)
    storyline: list[Beat] = field(default_factory=list)
    #: An optional fight to open on: grid size, and who is standing where.
    #: Initiatives are stated rather than rolled, because a one-shot that laid
    #: its combatants out in a different order each time would not be the same
    #: evening twice.
    encounter: dict = field(default_factory=dict)
    #: A template meant for exercising the app rather than playing. Kept out of
    #: the chooser's way, since "Four Players And A Goblin" is not an evening.
    for_testing: bool = False

    @property
    def player_count(self) -> int:
        """How many seats there are, counted in characters rather than logins.

        A one-shot ships nobody's login, so counting accounts would say nought
        players for an adventure written for three. The characters are the
        thing that exists before anybody has been invited.
        """
        return sum(1 for entity in self.entities if entity.get("kind") == KIND_PC)

    @property
    def ending(self) -> Beat | None:
        for beat in self.storyline:
            if beat.ends_it:
                return beat
        return None


def load(path: Path) -> Template:
    """Read one template file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateError(f"{path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise TemplateError(f"{path.name}: expected an object")

    identifier = str(raw.get("id") or path.stem)
    name = str(raw.get("name") or identifier)
    if not raw.get("entities") and not raw.get("accounts"):
        raise TemplateError(f"{path.name}: a template with nothing in it")

    return Template(
        id=identifier,
        name=name,
        summary=str(raw.get("summary", "")),
        about=str(raw.get("about", "")),
        entities=list(raw.get("entities") or []),
        facts=list(raw.get("facts") or []),
        accounts=list(raw.get("accounts") or []),
        shares=list(raw.get("shares") or []),
        storyline=[
            Beat.from_dict(beat)
            for beat in (raw.get("storyline") or [])
            if isinstance(beat, dict)
        ],
        encounter=dict(raw.get("encounter") or {}),
        for_testing=bool(raw.get("for_testing")),
    )


def available(directory: Path | None = None) -> list[Template]:
    """Every template that reads cleanly, in a stable order.

    A broken one is logged and skipped rather than fatal: a bad file in the
    folder must not stop the others being offered, for the same reason a bad
    plugin must not stop the app opening.
    """
    folder = directory or BUNDLED
    if not folder.is_dir():
        return []

    found: list[Template] = []
    for path in sorted(folder.glob("*.json")):
        try:
            found.append(load(path))
        except TemplateError:
            log.exception("skipping template %s", path.name)
    return found


def get(template_id: str, directory: Path | None = None) -> Template | None:
    for template in available(directory):
        if template.id == template_id:
            return template
    return None
