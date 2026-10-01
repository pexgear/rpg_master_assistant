-- Who a line in the log was for.
--
-- The log was written as though everything in it were public, and then things
-- that are not public were written into it: "Marco asks to change level to 6",
-- "Autopilot could not answer: Error code 401 ...". Every one of those went to
-- the DM alone when it happened -- and then straight into the history any
-- player is handed the next time they log in.
--
-- So a line now says who it was for. Empty means the table; 'dm' means whoever
-- is running it, which is the DM and the agent standing in for them. The
-- default is empty, so every line already in the log stays public, which is
-- what it was.
--
-- The alternative -- not recording private lines at all -- loses the record of
-- what a table was told and why, which is the half of the log worth keeping.

ALTER TABLE chat_message ADD COLUMN audience TEXT NOT NULL DEFAULT '';
