-- Two things that turn an initiative list into a battlefield.
--
-- **Bodies stay.** A creature at zero used to be taken off the map, which meant
-- the most interesting square on the board -- the one with your friend lying on
-- it -- was the one square that showed nothing. Now it keeps its place and is
-- drawn as a ghost. `down` rather than reading hit points, because occupancy is
-- decided here in SQL and hit points live on the entity, two tables away. The
-- host recomputes the flag whenever it publishes the fight, so a DM editing
-- somebody's hit points by hand cannot leave a ghost standing.
--
-- **Teams.** A fight has sides, and until now the app guessed: player
-- characters against everything else. That is right almost always and wrong
-- exactly when it matters -- the captured guard fighting beside the party, the
-- rival adventurers who are not monsters. Two teams are made with every fight
-- so nobody has to set them up, and a DM can add more.
--
-- `team.encounter_id` rather than a campaign-wide roster of factions: who is on
-- whose side is a fact about *this fight*. The bandits are enemies tonight and
-- allies in three sessions' time, and that should not be a migration.

ALTER TABLE combatant ADD COLUMN down INTEGER NOT NULL DEFAULT 0;

CREATE TABLE team (
  id            INTEGER PRIMARY KEY,
  encounter_id  INTEGER NOT NULL REFERENCES encounter(id) ON DELETE CASCADE,
  name          TEXT    NOT NULL,
  -- The party's team. One per fight, made with it, and the reason a new player
  -- character knows where to go without being asked.
  is_party      INTEGER NOT NULL DEFAULT 0,
  created_at    REAL    NOT NULL
);

CREATE INDEX team_by_encounter ON team(encounter_id);

-- NULL means "not sorted yet", which is what every combatant in every existing
-- campaign is. Nothing is backfilled: the host puts a combatant on a side when
-- it first needs to know, from the same rule the app used to guess with.
ALTER TABLE combatant ADD COLUMN team_id INTEGER REFERENCES team(id);
