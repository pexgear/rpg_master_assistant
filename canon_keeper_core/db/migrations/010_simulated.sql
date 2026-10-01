-- A player character autopilot plays.
--
-- The empty chair. Somebody could not make it, or a party of two wants enough
-- bodies for a fight built for four, and their character should still take its
-- turns rather than standing in the middle of the room being walked around.
--
-- It is a flag on the combatant rather than on the entity, because it is about
-- *this fight*: nobody wants a character permanently marked "played by a
-- machine" in the campaign, and a fight is exactly the length of time the
-- arrangement lasts.
--
-- The projection sends it on, so the table can see which of them is being run
-- by a machine. That is the same rule the roster follows for the agent itself:
-- a table deserves to know.

ALTER TABLE combatant ADD COLUMN simulated INTEGER NOT NULL DEFAULT 0;
