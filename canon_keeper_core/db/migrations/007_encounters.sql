-- Combat: an initiative order and a grid to stand on.
--
-- Two tables rather than one blob in `setting`, because both halves are read
-- and written one row at a time. Moving a token is the most frequent write in
-- the app during a fight, and rewriting the whole fight to move one goblin one
-- square is how a map starts feeling slow.
--
-- `combatant.x`/`y` being NULL is not a missing value: it means *in the fight
-- but not on the map*. The one who fled up the corridor is still in the order
-- and still takes a turn. Removing them from the fight is deleting the row, so
-- the two ideas the DM has -- "off the map" and "out of the fight" -- are two
-- different operations rather than one flag with a meaning to remember.
--
-- Whose turn it is, is a combatant *id*, not an index into the order. An index
-- shifts under you the moment a dead goblin is removed, and a table watching
-- the marker jump a place has no way to tell that from a mistake.

CREATE TABLE encounter (
  id          INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  name        TEXT NOT NULL DEFAULT '',

  -- Squares, not pixels. Five feet each, like the books.
  width       INTEGER NOT NULL DEFAULT 20,
  height      INTEGER NOT NULL DEFAULT 15,

  -- 0 means it has not started. The first "next turn" makes it round 1.
  round       INTEGER NOT NULL DEFAULT 0,
  turn_combatant_id INTEGER REFERENCES combatant(id) ON DELETE SET NULL,

  -- The fight being run right now. At most one per campaign; the others are
  -- prepared, or finished and kept.
  running     INTEGER NOT NULL DEFAULT 0,

  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL,

  -- Bumped on every change to the encounter *or* its combatants, so a client
  -- can tell one fight's state from the next without diffing it.
  version     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE combatant (
  id           INTEGER PRIMARY KEY,
  encounter_id INTEGER NOT NULL REFERENCES encounter(id) ON DELETE CASCADE,

  -- The creature this is. NULL for a token the DM typed a name for and never
  -- made into an entity -- a fourth goblin that will not survive the scene.
  -- Those are never sent to players: with no entity there is no share, and
  -- there is nothing to check a name against.
  entity_id    INTEGER REFERENCES entity(id) ON DELETE CASCADE,
  name         TEXT NOT NULL DEFAULT '',

  -- NULL until it is rolled, so "not rolled yet" and "rolled a zero" are not
  -- the same thing on screen.
  initiative   INTEGER,
  -- Dexterity modifier, kept here so the order does not change when a sheet
  -- does mid-fight, and so two runs of a template order identically.
  tiebreak     INTEGER NOT NULL DEFAULT 0,

  x            INTEGER,
  y            INTEGER,

  added_at     REAL NOT NULL DEFAULT 0
);

-- What is in the way. A rock, a pillar, an overturned cart: a square nobody can
-- stand in and anybody can hide behind.
--
-- Its own table rather than a JSON blob on the encounter, for the same reason
-- the combatants are: it is load-bearing. Placement is refused against it on
-- every write, and a rule enforced by parsing a column would be a rule with a
-- parser in front of it.
--
-- What cover *does* is not stored, because it is not decided here. Half or
-- three-quarters, and whether that pillar counts from where the rogue is
-- standing, is a ruling the DM makes at the table; the app's job is to agree
-- with everyone about where the pillar is.
CREATE TABLE obstacle (
  id           INTEGER PRIMARY KEY,
  encounter_id INTEGER NOT NULL REFERENCES encounter(id) ON DELETE CASCADE,
  x            INTEGER NOT NULL,
  y            INTEGER NOT NULL
);

CREATE UNIQUE INDEX idx_obstacle_square ON obstacle(encounter_id, x, y);

CREATE INDEX idx_encounter_campaign ON encounter(campaign_id);
CREATE INDEX idx_combatant_encounter ON combatant(encounter_id);

-- One creature is in a fight once. Two tokens for one goblin is a mistake, not
-- a feature; four goblins are four entities. NULL entity_ids stay distinct
-- under this index, which is exactly what nameless tokens need.
CREATE UNIQUE INDEX idx_combatant_entity ON combatant(encounter_id, entity_id);
