-- What the turn in progress has spent.
--
-- A turn is a move up to your speed and one action. The app knew the rule but
-- kept no record of how much of it was left, so a turn was all-or-nothing: one
-- proposal carrying a whole move and a whole attack. Splitting the move around
-- the action -- three squares, swing, three more -- had nowhere to be written
-- down, and a player had no way to see what they still had.
--
-- It lives on the encounter rather than the combatant because it belongs to
-- the *turn*, not to the creature: whoever is up is the only one spending
-- anything, and the moment the turn passes it is all reset. Putting it on the
-- combatant would leave sixteen stale counters lying about, one of which would
-- eventually be read.

ALTER TABLE encounter ADD COLUMN moved_squares INTEGER NOT NULL DEFAULT 0;
ALTER TABLE encounter ADD COLUMN action_used INTEGER NOT NULL DEFAULT 0;
