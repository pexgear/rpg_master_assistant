-- Death saving throws, for player characters only.
--
-- A monster at zero hit points is finished. A player character at zero is
-- dying, which is a different thing that takes three rolls to resolve one way
-- or the other, and turning the most dramatic moment in the game into "token
-- removed, next please" was throwing it away.
--
-- On the combatant rather than the entity, for the same reason `simulated` is:
-- it is about *this fight*. Two failures against a character are not something
-- they should still be carrying next week, and a fight is exactly how long the
-- count is meant to last. Healing above zero clears them within the fight too.
--
-- Counted rather than listed, because nothing needs to know the order the
-- successes and failures arrived in -- only how many there are of each.

ALTER TABLE combatant ADD COLUMN death_successes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE combatant ADD COLUMN death_failures INTEGER NOT NULL DEFAULT 0;
