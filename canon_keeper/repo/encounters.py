"""Fights: who is in one, in what order, and where they are standing.

Two things a DM tracks in combat and nothing else in this app tracks for them:
an **initiative order** and a **grid**. They are one table each, and one module,
because they are one activity -- the order tells you whose turn it is, the grid
tells you whether they can reach anybody.

Three rules run through this file.

**Off the map is not out of the fight.** ``x``/``y`` of ``None`` means a
combatant is in the order, takes turns, and is simply not standing anywhere --
they fled down the corridor, or they have not burst through the door yet.
Taking them out of the fight is :meth:`EncounterRepo.remove`, a different
operation, because those are two different things a DM means.

**Whose turn it is, is an id.** Not an index into the order: an index shifts the
moment a dead goblin is removed, and the marker jumping a place mid-round is
indistinguishable from a bug.

**The order is a pure function of the rows.** Initiative down, then the
Dexterity tiebreak, then id. No stored ordinal to drift, and a template that
states its initiatives lays out identically every time it is started.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from canon_keeper.repo.entities import KIND_PC
from canon_keeper_protocol import grid

#: A grid smaller than this is not a battlefield, and one larger stops being
#: readable on a laptop before it stops being drawable.
MIN_SIZE = 5
MAX_SIZE = 60

DEFAULT_WIDTH = 20
DEFAULT_HEIGHT = 15


@dataclass(slots=True)
class Combatant:
    id: int | None
    encounter_id: int
    #: The creature. None for a token the DM named and never made an entity of.
    entity_id: int | None = None
    name: str = ""
    #: None until it is rolled, so "not rolled" and "rolled badly" look
    #: different on screen.
    initiative: int | None = None
    tiebreak: int = 0
    x: int | None = None
    y: int | None = None
    added_at: float = 0.0
    #: A player character autopilot is playing, for this fight. The empty
    #: chair -- somebody could not make it, and their character should still
    #: take its turns rather than being walked around the room.
    simulated: bool = False
    #: Death saving throws made and failed in this fight. Player characters
    #: only; see :mod:`canon_keeper.rules.death`. Cleared by healing above zero,
    #: and never carried out of the fight they were rolled in.
    death_successes: int = 0
    death_failures: int = 0
    #: The round this one last used its reaction in, or 0 for never. A round
    #: number rather than a flag, so nothing has to remember to clear it.
    reaction_round: int = 0
    #: At zero hit points, and still lying where they fell. Kept here rather
    #: than read off the entity because occupancy is decided in SQL: a body does
    #: not hold a square against the living. The host keeps it in step with the
    #: hit points every time it publishes the fight.
    down: bool = False
    #: Which side they are on, or None until somebody has asked.
    team_id: int | None = None
    #: **Not a column.** Whether something is connected on this character's
    #: seat right now, filled in by the host on the way out to a client. It is
    #: the difference between handed over and actually being played, which
    #: decides whether autopilot proposes a turn or takes it -- and a proposal
    #: nobody can answer stops the fight.
    stand_in: bool = False
    #: **Not a column either.** What this character's stand-in is called, so a
    #: table can say "BRASS moved" rather than "autopilot moved" when there are
    #: three of them at once.
    stand_in_name: str = ""

    @property
    def on_map(self) -> bool:
        return self.x is not None and self.y is not None

    @property
    def sort_key(self) -> tuple:
        """Initiative down, tiebreak down, id up. Total, and stable."""
        return (
            self.initiative is None,
            -(self.initiative if self.initiative is not None else 0),
            -self.tiebreak,
            self.id or 0,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Combatant":
        return cls(
            id=row["id"],
            encounter_id=row["encounter_id"],
            entity_id=row["entity_id"],
            name=row["name"],
            initiative=row["initiative"],
            tiebreak=row["tiebreak"],
            x=row["x"],
            y=row["y"],
            added_at=row["added_at"],
            simulated=bool(_column(row, "simulated", 0)),
            death_successes=_column(row, "death_successes", 0),
            death_failures=_column(row, "death_failures", 0),
            reaction_round=_column(row, "reaction_round", 0),
            down=bool(_column(row, "down", 0)),
            team_id=_column(row, "team_id", None),
        )


@dataclass(slots=True)
class Encounter:
    id: int | None
    campaign_id: int
    name: str = ""
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    #: 0 until the first turn is taken.
    round: int = 0
    turn_combatant_id: int | None = None
    running: bool = False
    #: What the turn in progress has spent. Reset whenever the turn passes,
    #: because it belongs to the turn and not to the creature taking it.
    moved_squares: int = 0
    action_used: bool = False
    #: What the action became. `action_used` says it is gone; these say what it
    #: went on, which is what Extra Attack and Dash each need to know.
    attacks_made: int = 0
    dashed: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    version: int = 1

    @property
    def has_begun(self) -> bool:
        return self.round > 0

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        """``(left, top, right, bottom)``, inclusive. 0,0 is the middle."""
        return grid.bounds(self.width, self.height)

    def holds(self, x: int, y: int) -> bool:
        return grid.holds(self.width, self.height, x, y)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Encounter":
        return cls(
            id=row["id"],
            campaign_id=row["campaign_id"],
            name=row["name"],
            width=row["width"],
            height=row["height"],
            round=row["round"],
            turn_combatant_id=row["turn_combatant_id"],
            running=bool(row["running"]),
            moved_squares=_column(row, "moved_squares", 0),
            action_used=bool(_column(row, "action_used", 0)),
            attacks_made=_column(row, "attacks_made", 0),
            dashed=bool(_column(row, "dashed", 0)),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )


#: The two sides every fight starts with. A DM who wants a third makes one; a
#: DM who wants neither never has to look at them, because the party's team is
#: where a player character goes without being asked and everything else lands
#: on the other.
PARTY = "The party"
HOSTILE = "Hostile"


@dataclass(slots=True)
class Team:
    """One side of a fight. Named, because "team 2" tells a table nothing."""

    id: int | None
    encounter_id: int
    name: str = ""
    #: The party's own. At most one per fight, and the default home for a
    #: player character.
    is_party: bool = False
    created_at: float = 0.0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Team":
        return cls(
            id=row["id"],
            encounter_id=row["encounter_id"],
            name=row["name"],
            is_party=bool(row["is_party"]),
            created_at=row["created_at"],
        )


def order_of(combatants: list[Combatant]) -> list[Combatant]:
    """The initiative order. A pure function, so it can be trusted anywhere."""
    return sorted(combatants, key=lambda c: c.sort_key)


class EncounterRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------- encounters

    def create(
        self,
        campaign_id: int,
        name: str = "",
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        running: bool = True,
    ) -> Encounter:
        """Start a new fight. By default it becomes the one being run."""
        now = time.time()
        width = _clamp(width, MIN_SIZE, MAX_SIZE)
        height = _clamp(height, MIN_SIZE, MAX_SIZE)
        with self._conn:
            if running:
                self._conn.execute(
                    "UPDATE encounter SET running = 0, version = version + 1"
                    " WHERE campaign_id = ? AND running = 1",
                    (campaign_id,),
                )
            cursor = self._conn.execute(
                "INSERT INTO encounter (campaign_id, name, width, height, round,"
                " running, created_at, updated_at, version)"
                " VALUES (?, ?, ?, ?, 0, ?, ?, ?, 1)",
                (campaign_id, name, width, height, int(running), now, now),
            )
        # Both sides exist before anybody is added, so a fight never has to be
        # set up before it can be run.
        self.ensure_teams(int(cursor.lastrowid))
        return Encounter(
            id=int(cursor.lastrowid),
            campaign_id=campaign_id,
            name=name,
            width=width,
            height=height,
            running=running,
            created_at=now,
            updated_at=now,
        )

    def get(self, encounter_id: int) -> Encounter | None:
        row = self._conn.execute(
            "SELECT * FROM encounter WHERE id = ?", (encounter_id,)
        ).fetchone()
        return Encounter.from_row(row) if row else None

    def list(self, campaign_id: int) -> list[Encounter]:
        """Newest first, so the fight you just made is the one at the top."""
        rows = self._conn.execute(
            "SELECT * FROM encounter WHERE campaign_id = ?"
            " ORDER BY created_at DESC, id DESC",
            (campaign_id,),
        ).fetchall()
        return [Encounter.from_row(r) for r in rows]

    def current(self, campaign_id: int) -> Encounter | None:
        """The fight being run, or the most recent one, or nothing.

        Falling back to the most recent rather than to nothing means ending a
        fight leaves it on screen to look at, instead of blanking the panel the
        moment the last goblin drops.
        """
        row = self._conn.execute(
            "SELECT * FROM encounter WHERE campaign_id = ?"
            " ORDER BY running DESC, created_at DESC, id DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return Encounter.from_row(row) if row else None

    def running(self, campaign_id: int) -> Encounter | None:
        """The fight actually being run. What players are told about."""
        row = self._conn.execute(
            "SELECT * FROM encounter WHERE campaign_id = ? AND running = 1"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return Encounter.from_row(row) if row else None

    def rename(self, encounter_id: int, name: str) -> None:
        self._write(encounter_id, "name = ?", (name,))

    def resize(self, encounter_id: int, width: int, height: int) -> None:
        """Change the grid. Anyone left outside it is taken off the map.

        Silently keeping a token at (30, 4) on a grid twenty wide would leave
        it invisible and still in the way of every reach and range question the
        DM asks afterwards.
        """
        width = _clamp(width, MIN_SIZE, MAX_SIZE)
        height = _clamp(height, MIN_SIZE, MAX_SIZE)
        left, top, right, bottom = grid.bounds(width, height)
        outside = " AND (x < ? OR x > ? OR y < ? OR y > ?)"
        edges = (left, right, top, bottom)
        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET x = NULL, y = NULL"
                " WHERE encounter_id = ?" + outside,
                (encounter_id, *edges),
            )
            # Terrain outside the room simply stops existing. There is nothing
            # to take it off the map *to*, the way there is for a creature.
            self._conn.execute(
                "DELETE FROM obstacle WHERE encounter_id = ?" + outside,
                (encounter_id, *edges),
            )
        self._write(encounter_id, "width = ?, height = ?", (width, height))

    def set_running(self, encounter_id: int, running: bool) -> None:
        """Make this the fight being run, or stop running it."""
        encounter = self.get(encounter_id)
        if encounter is None:
            return
        if running:
            with self._conn:
                self._conn.execute(
                    "UPDATE encounter SET running = 0, version = version + 1"
                    " WHERE campaign_id = ? AND running = 1 AND id != ?",
                    (encounter.campaign_id, encounter_id),
                )
        self._write(encounter_id, "running = ?", (int(bool(running)),))

    def delete(self, encounter_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM combatant WHERE encounter_id = ?", (encounter_id,))
            self._conn.execute("DELETE FROM obstacle WHERE encounter_id = ?", (encounter_id,))
            self._conn.execute("DELETE FROM encounter WHERE id = ?", (encounter_id,))

    # ------------------------------------------------------------- combatants

    def combatants(self, encounter_id: int) -> list[Combatant]:
        """Everyone in the fight, in initiative order."""
        rows = self._conn.execute(
            "SELECT * FROM combatant WHERE encounter_id = ?", (encounter_id,)
        ).fetchall()
        return order_of([Combatant.from_row(r) for r in rows])

    def combatant(self, combatant_id: int) -> Combatant | None:
        row = self._conn.execute(
            "SELECT * FROM combatant WHERE id = ?", (combatant_id,)
        ).fetchone()
        return Combatant.from_row(row) if row else None

    def add(
        self,
        encounter_id: int,
        entity_id: int | None = None,
        name: str = "",
        initiative: int | None = None,
        tiebreak: int = 0,
        x: int | None = None,
        y: int | None = None,
    ) -> Combatant | None:
        """Put a creature in the fight. Returns None if it is already in it.

        A square that cannot be stood on is not written down. They go into the
        fight without one instead, which is the app's ordinary "in the order,
        not yet on the map" state and a token the DM can drag -- rather than
        one standing outside the room, which nothing can then do anything with.
        """
        existing = self._by_entity(encounter_id, entity_id)
        if existing is not None:
            return None
        if x is not None and y is not None and not self.free_for(
            encounter_id, int(x), int(y)
        ):
            x = y = None

        now = time.time()
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO combatant (encounter_id, entity_id, name, initiative,"
                " tiebreak, x, y, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (encounter_id, entity_id, name, initiative, tiebreak, x, y, now),
            )
        self._touch(encounter_id)
        return Combatant(
            id=int(cursor.lastrowid),
            encounter_id=encounter_id,
            entity_id=entity_id,
            name=name,
            initiative=initiative,
            tiebreak=tiebreak,
            x=x,
            y=y,
            added_at=now,
        )

    def remove(self, combatant_id: int) -> None:
        """Out of the fight entirely.

        If it was their turn, the turn moves on first. Otherwise deleting the
        active row leaves the marker on nobody, and the next press of "next
        turn" restarts the order from the top.
        """
        combatant = self.combatant(combatant_id)
        if combatant is None:
            return
        encounter = self.get(combatant.encounter_id)
        if encounter is not None and encounter.turn_combatant_id == combatant_id:
            self.advance(encounter.id, skipping=combatant_id)

        with self._conn:
            self._conn.execute("DELETE FROM combatant WHERE id = ?", (combatant_id,))
        self._touch(combatant.encounter_id)

    def free_for(
        self, encounter_id: int, x: int, y: int, ignoring: int | None = None
    ) -> bool:
        """Whether a creature could stand on that square at all.

        Three facts about the map, not three rules: a square off the edge does
        not exist, and one holding a body or a rock cannot be shared. Nothing
        waives any of them.

        Written once here because there is more than one way onto the map --
        walking, dragging, and being put into the fight already standing
        somewhere -- and the last of those went in without asking. A goblin
        enlisted onto a square the map does not have made the whole fight
        unplayable: every legal move it tried came back "off the map", because
        the square it was measuring from was the one outside the room.
        """
        encounter = self.get(encounter_id)
        if encounter is None or not encounter.holds(x, y):
            return False
        if self._occupant(encounter_id, x, y, ignoring=ignoring) is not None:
            return False
        return (x, y) not in self.obstacles(encounter_id)

    def place(self, combatant_id: int, x: int | None, y: int | None) -> bool:
        """Move a token, or take it off the map with ``None, None``.

        Returns False for a square outside the grid or already occupied. The
        occupancy check is here rather than in the panel because the agent will
        eventually send these too, and a rule enforced in one of two callers is
        not a rule.
        """
        combatant = self.combatant(combatant_id)
        if combatant is None:
            return False
        encounter = self.get(combatant.encounter_id)
        if encounter is None:
            return False

        if x is None or y is None:
            x = y = None
        else:
            x, y = int(x), int(y)
            if not self.free_for(encounter.id, x, y, ignoring=combatant_id):
                return False

        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET x = ?, y = ? WHERE id = ?", (x, y, combatant_id)
            )
        self._touch(combatant.encounter_id)
        return True

    def set_simulated(self, combatant_id: int, on: bool) -> None:
        """Hand a character to autopilot for this fight, or take it back."""
        combatant = self.combatant(combatant_id)
        if combatant is None:
            return
        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET simulated = ? WHERE id = ?",
                (int(bool(on)), combatant_id),
            )
        self._touch(combatant.encounter_id)

    def record_death_save(
        self, combatant_id: int, successes: int = 0, failures: int = 0
    ) -> None:
        """Add to the count. Adds rather than sets, so two callers cannot race
        each other into overwriting a failure with a success."""
        combatant = self.combatant(combatant_id)
        if combatant is None:
            return
        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET death_successes = death_successes + ?, "
                "death_failures = death_failures + ? WHERE id = ?",
                (int(successes), int(failures), combatant_id),
            )
        self._touch(combatant.encounter_id)

    def use_reaction(self, combatant_id: int, round_number: int) -> None:
        """Spend this one's reaction for the round it is currently in."""
        combatant = self.combatant(combatant_id)
        if combatant is None:
            return
        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET reaction_round = ? WHERE id = ?",
                (int(round_number), combatant_id),
            )
        self._touch(combatant.encounter_id)

    def clear_death_saves(self, combatant_id: int) -> None:
        """Back above zero hit points: the count starts again if they drop again.

        Carrying two failures through a healing word and into the next time
        they go down would be a rule the game does not have, and a nasty one.
        """
        combatant = self.combatant(combatant_id)
        if combatant is None:
            return
        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET death_successes = 0, death_failures = 0 "
                "WHERE id = ?",
                (combatant_id,),
            )
        self._touch(combatant.encounter_id)

    def set_initiative(self, combatant_id: int, initiative: int | None) -> None:
        combatant = self.combatant(combatant_id)
        if combatant is None:
            return
        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET initiative = ? WHERE id = ?",
                (initiative, combatant_id),
            )
        self._touch(combatant.encounter_id)

    def at(self, encounter_id: int, x: int, y: int) -> Combatant | None:
        return self._occupant(encounter_id, x, y)

    # ------------------------------------------------------------- what is in the way

    def obstacles(self, encounter_id: int) -> set[tuple[int, int]]:
        """Squares nobody can stand in. A rock, a pillar, an overturned cart."""
        rows = self._conn.execute(
            "SELECT x, y FROM obstacle WHERE encounter_id = ?", (encounter_id,)
        ).fetchall()
        return {(row["x"], row["y"]) for row in rows}

    def toggle_obstacle(self, encounter_id: int, x: int, y: int) -> bool:
        """Put something in the way, or take it out. Returns whether it is there now.

        Refused where somebody is standing: the square would then hold a
        creature and a rock at once, and every rule about the second one would
        have an exception for the first.
        """
        encounter = self.get(encounter_id)
        if encounter is None or not encounter.holds(x, y):
            return False

        if (x, y) in self.obstacles(encounter_id):
            with self._conn:
                self._conn.execute(
                    "DELETE FROM obstacle WHERE encounter_id = ? AND x = ? AND y = ?",
                    (encounter_id, x, y),
                )
            self._touch(encounter_id)
            return False

        if self._occupant(encounter_id, x, y) is not None:
            return False

        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO obstacle (encounter_id, x, y) VALUES (?, ?, ?)",
                (encounter_id, x, y),
            )
        self._touch(encounter_id)
        return True

    # ------------------------------------------------------------------ turns

    #: Cleared every time the turn passes. Written out once here rather than at
    #: each of the four places the turn can move, because a turn that inherits
    #: the last one's spent movement is a bug nobody would look for.
    #: Everything the turn budget holds, cleared together. One string, because a
    #: column added here and forgotten there is a counter that survives the turn
    #: it belonged to.
    _FRESH_TURN = (
        "moved_squares = 0, action_used = 0, attacks_made = 0, dashed = 0"
    )

    def begin(self, encounter_id: int) -> None:
        """Round one, and the highest initiative is up."""
        order = self.combatants(encounter_id)
        if not order:
            return
        self._write(
            encounter_id,
            f"round = 1, turn_combatant_id = ?, running = 1, {self._FRESH_TURN}",
            (order[0].id,),
        )

    def spend_movement(self, encounter_id: int, squares: int) -> None:
        """Count squares against this turn's allowance."""
        self._write(
            encounter_id,
            "moved_squares = moved_squares + ?",
            (max(0, int(squares)),),
        )

    def use_action(self, encounter_id: int) -> None:
        self._write(encounter_id, "action_used = 1", ())

    def count_attack(self, encounter_id: int) -> None:
        """One more swing out of this turn's action.

        Separate from :meth:`use_action` because the two answer different
        questions: whether the action is gone, and how many attacks it has
        produced. A level-five fighter gets two swings from one action, so the
        first answer stops being enough.
        """
        self._write(encounter_id, "attacks_made = attacks_made + 1", ())

    def dash(self, encounter_id: int) -> None:
        """The action went on moving again rather than on a swing."""
        self._write(encounter_id, "action_used = 1, dashed = 1", ())

    def advance(
        self,
        encounter_id: int,
        skipping: int | None = None,
        passing_over: frozenset[int] = frozenset(),
    ) -> None:
        """Next turn, and next round when the order wraps.

        ``skipping`` is the combatant about to be deleted. They cannot be handed
        the turn we are taking away from them -- but the walk still starts from
        *their* place in the order, because the question is who acts after them,
        not who acts first. Closing the gap before looking sends the turn back
        to the top of the round, and somebody acts twice.

        ``passing_over`` is everyone who is still in the fight and still in the
        order but takes no turn: the dead, and the unconscious. They stay listed
        -- a DM can bring them round, and a corpse is worth seeing -- but a fight
        that stopped on every one of them got slower the closer it came to being
        over, which is exactly backwards. Worked out by the caller, from
        :func:`canon_keeper.rules.death.resting`, because hit points are not
        this table's to know.
        """
        full = self.combatants(encounter_id)
        if not [c for c in full if c.id != skipping]:
            self._write(encounter_id, "turn_combatant_id = NULL")
            return

        encounter = self.get(encounter_id)
        if encounter is None:
            return
        if not encounter.has_begun:
            self.begin(encounter_id)
            return

        ids = [c.id for c in full]
        standing = [
            i for i in ids if i != skipping and i not in passing_over
        ]
        if not standing:
            # Everybody left is down. Nobody is up, and the DM ends the fight --
            # it is not this method's place to decide a battle is finished.
            self._write(encounter_id, "turn_combatant_id = NULL")
            return

        if encounter.turn_combatant_id not in ids:
            # Whoever was up left the fight without this being told about it.
            # The top of the order is the honest place to resume, and the round
            # does not advance on a correction.
            self._write(
                encounter_id,
                f"turn_combatant_id = ?, {self._FRESH_TURN}",
                (standing[0],),
            )
            return

        position = ids.index(encounter.turn_combatant_id) + 1
        wrapped = False
        for _ in range(len(ids) + 1):
            if position >= len(ids):
                position, wrapped = 0, True
            if ids[position] in standing:
                break
            position += 1

        if wrapped:
            self._write(
                encounter_id,
                f"round = round + 1, turn_combatant_id = ?, {self._FRESH_TURN}",
                (ids[position],),
            )
        else:
            self._write(
                encounter_id,
                f"turn_combatant_id = ?, {self._FRESH_TURN}",
                (ids[position],),
            )

    def end(self, encounter_id: int) -> None:
        """The fight is over. Everything stays; only the clock stops."""
        self._write(
            encounter_id,
            f"running = 0, round = 0, turn_combatant_id = NULL, {self._FRESH_TURN}",
            (),
        )

    def clear(self, encounter_id: int) -> None:
        """Empty the fight without deleting it -- the room, kept, and nobody in it.

        The obstacles stay. They are the room rather than the fight, and a DM
        who laid out a cave does not want it back as a flat field because the
        goblins in it changed.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE encounter SET turn_combatant_id = NULL WHERE id = ?",
                (encounter_id,),
            )
            self._conn.execute(
                "DELETE FROM combatant WHERE encounter_id = ?", (encounter_id,)
            )
        self._write(encounter_id, "round = 0", ())

    # ---------------------------------------------------------------- helpers

    def _by_entity(self, encounter_id: int, entity_id: int | None) -> Combatant | None:
        if entity_id is None:
            return None
        row = self._conn.execute(
            "SELECT * FROM combatant WHERE encounter_id = ? AND entity_id = ?",
            (encounter_id, entity_id),
        ).fetchone()
        return Combatant.from_row(row) if row else None

    def _occupant(
        self, encounter_id: int, x: int, y: int, ignoring: int | None = None
    ) -> Combatant | None:
        """Who is standing there. A body on the floor is not standing there.

        The dead keep their square so you can see where they fell, but they do
        not hold it: a corpse that blocked movement would turn the end of every
        fight into an obstacle course, and stepping over a body is a thing
        people do.
        """
        row = self._conn.execute(
            "SELECT * FROM combatant WHERE encounter_id = ? AND x = ? AND y = ?"
            " AND id != ? AND COALESCE(down, 0) = 0",
            (encounter_id, x, y, ignoring or -1),
        ).fetchone()
        return Combatant.from_row(row) if row else None

    # ------------------------------------------------------------------ teams

    def teams(self, encounter_id: int) -> list[Team]:
        """Every side in this fight, the party's first."""
        rows = self._conn.execute(
            "SELECT * FROM team WHERE encounter_id = ?"
            " ORDER BY is_party DESC, created_at, id",
            (encounter_id,),
        ).fetchall()
        return [Team.from_row(r) for r in rows]

    def add_team(
        self, encounter_id: int, name: str, is_party: bool = False
    ) -> Team:
        now = time.time()
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO team (encounter_id, name, is_party, created_at)"
                " VALUES (?, ?, ?, ?)",
                (encounter_id, name.strip() or "Another side", int(is_party), now),
            )
        self._touch(encounter_id)
        return Team(
            id=int(cursor.lastrowid),
            encounter_id=encounter_id,
            name=name,
            is_party=is_party,
            created_at=now,
        )

    def ensure_teams(self, encounter_id: int) -> list[Team]:
        """The two a fight starts with, made if they are not there already.

        Called rather than assumed, because a campaign that predates teams has
        fights with none, and a fight opened three months from now should look
        like one made today.
        """
        existing = self.teams(encounter_id)
        if existing:
            return existing
        self.add_team(encounter_id, PARTY, is_party=True)
        self.add_team(encounter_id, HOSTILE)
        return self.teams(encounter_id)

    def sort_into_teams(self, encounter_id: int) -> bool:
        """Put anybody with no side onto one. True if anything moved.

        The same rule the app used to guess with, run once and written down
        instead of re-derived everywhere: player characters with the party,
        everything else against them. Written down is the point -- a guess
        cannot be corrected, and a DM can drag any of these onto another side
        the moment it is wrong.
        """
        teams = self.ensure_teams(encounter_id)
        party = next((t for t in teams if t.is_party), None)
        against = next((t for t in teams if not t.is_party), None)
        if party is None or against is None:
            return False

        with self._conn:
            known = self._conn.execute(
                "UPDATE combatant SET team_id = ("
                "  SELECT CASE WHEN entity.kind = ? THEN ? ELSE ? END"
                "  FROM entity WHERE entity.id = combatant.entity_id)"
                " WHERE encounter_id = ? AND team_id IS NULL"
                " AND entity_id IS NOT NULL",
                (KIND_PC, party.id, against.id, encounter_id),
            ).rowcount
            # A name the DM typed for a fourth goblin has no entity and so no
            # kind. It is on the map to be fought, so that is the side it takes.
            nameless = self._conn.execute(
                "UPDATE combatant SET team_id = ? WHERE encounter_id = ?"
                " AND team_id IS NULL AND entity_id IS NULL",
                (against.id, encounter_id),
            ).rowcount

        if known or nameless:
            self._touch(encounter_id)
            return True
        return False

    def rename_team(self, team_id: int, name: str) -> None:
        row = self._conn.execute(
            "SELECT encounter_id FROM team WHERE id = ?", (team_id,)
        ).fetchone()
        if row is None:
            return
        with self._conn:
            self._conn.execute(
                "UPDATE team SET name = ? WHERE id = ?",
                (name.strip() or "Another side", team_id),
            )
        self._touch(row["encounter_id"])

    def remove_team(self, team_id: int) -> None:
        """Delete a side. Whoever was on it goes back to having none."""
        row = self._conn.execute(
            "SELECT encounter_id FROM team WHERE id = ?", (team_id,)
        ).fetchone()
        if row is None:
            return
        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET team_id = NULL WHERE team_id = ?", (team_id,)
            )
            self._conn.execute("DELETE FROM team WHERE id = ?", (team_id,))
        self._touch(row["encounter_id"])

    def set_team(self, combatant_id: int, team_id: int | None) -> None:
        combatant = self.combatant(combatant_id)
        if combatant is None:
            return
        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET team_id = ? WHERE id = ?",
                (team_id, combatant_id),
            )
        self._touch(combatant.encounter_id)

    def set_down(self, combatant_id: int, on: bool) -> bool:
        """Lying where they fell, or back on their feet. True if it changed."""
        combatant = self.combatant(combatant_id)
        if combatant is None or combatant.down == bool(on):
            return False
        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET down = ? WHERE id = ?",
                (int(bool(on)), combatant_id),
            )
        self._touch(combatant.encounter_id)
        return True

    def _write(self, encounter_id: int, assignment: str = "", params: tuple = ()) -> None:
        """One write, and every write bumps the version and the clock."""
        clauses = ([assignment] if assignment else []) + [
            "updated_at = ?",
            "version = version + 1",
        ]
        with self._conn:
            self._conn.execute(
                f"UPDATE encounter SET {', '.join(clauses)} WHERE id = ?",
                (*params, time.time(), encounter_id),
            )

    def _touch(self, encounter_id: int) -> None:
        """A combatant changed, so the encounter did. One version for both.

        Clients are sent the whole fight at once -- it is a few hundred bytes --
        so there is nothing to gain from versioning the halves separately, and a
        map whose tokens and order can disagree about how current they are is
        worse than one that cannot.
        """
        self._write(encounter_id)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _column(row: sqlite3.Row, name: str, fallback):
    """A column a migration may not have added yet, for a half-migrated read."""
    try:
        value = row[name]
    except (IndexError, KeyError):
        return fallback
    return fallback if value is None else value
