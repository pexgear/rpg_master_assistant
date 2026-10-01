-- One reaction a round, spent on opportunity attacks.
--
-- Walking out of somebody's reach lets them swing at you as you go. Without it
-- a grid is a diagram: you can stroll past an ogre to reach the wizard behind
-- it and the ogre may only watch. Standing next to something is supposed to
-- cost something.
--
-- Stored as the round the reaction was spent in, rather than a flag that has to
-- be cleared. A flag needs somebody to remember to reset it at the top of every
-- round, and the round somebody last reacted in is a fact that answers the
-- question without anybody having to tidy up afterwards. Zero means never.

ALTER TABLE combatant ADD COLUMN reaction_round INTEGER NOT NULL DEFAULT 0;
