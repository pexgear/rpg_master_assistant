-- What the action was spent on, and how many swings came out of it.
--
-- `action_used` is a flag, and a flag can only say "the action is gone". Two
-- rules need more than that.
--
-- **Extra Attack** gives a level-five fighter two swings from one Attack action.
-- With a flag the second swing is indistinguishable from a second action, so it
-- was refused -- which is why a fighter at level five still got one swing. The
-- count is kept here rather than derived from the flag because "how many attacks
-- have happened" is not a thing the flag knows.
--
-- **Dash** spends the action to move your speed again. Movement is checked
-- against the allowance, so the allowance has to know the action went on Dash
-- rather than on a swing -- again, not something a flag distinguishes.
--
-- Beside the rest of the turn budget on `encounter`, for the reason migration
-- 009 gives: the budget belongs to the *turn*, not to the creature, and whoever
-- is up is the only one spending anything. Sixteen stale counters on sixteen
-- combatants would leave one to be read by mistake.
--
-- `action_used` stays, and stays meaning exactly what it meant: the action
-- itself is spent. These two say what became of it.

ALTER TABLE encounter ADD COLUMN attacks_made INTEGER NOT NULL DEFAULT 0;
ALTER TABLE encounter ADD COLUMN dashed INTEGER NOT NULL DEFAULT 0;
