-- A third kind of login: the agent.
--
-- Autopilot means the human DM hands the table to an agent for a while and
-- takes it back when they want to. The agent connects like anything else -- own
-- login, own password, over the wire -- so what it did is attributable in the
-- log and switching it off for good is deleting an account rather than editing
-- a config file.
--
-- It is a separate role rather than a flag on 'dm' because the two want
-- opposite things. An agent needs a DM's *view* (it answers from the canon), and
-- must not have a DM's *authority*: the host refuses chat from it whenever
-- autopilot is off. Making it a DM with an exception would put that rule one
-- forgotten `and not is_agent` away from failing open.
--
-- SQLite cannot widen a CHECK constraint in place, so the table is rebuilt.

PRAGMA foreign_keys = OFF;

CREATE TABLE account_new (
  id            INTEGER PRIMARY KEY,
  campaign_id   INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  username      TEXT NOT NULL,
  display_name  TEXT NOT NULL DEFAULT '',
  role          TEXT NOT NULL DEFAULT 'player'
                CHECK(role IN ('dm', 'player', 'agent')),

  salt          BLOB NOT NULL,
  verifier      BLOB NOT NULL,

  character_entity_id INTEGER REFERENCES entity(id) ON DELETE SET NULL,

  disabled      INTEGER NOT NULL DEFAULT 0,
  created_at    REAL NOT NULL,
  last_seen_at  REAL
);

INSERT INTO account_new (id, campaign_id, username, display_name, role, salt,
                         verifier, character_entity_id, disabled, created_at,
                         last_seen_at)
  SELECT id, campaign_id, username, display_name, role, salt, verifier,
         character_entity_id, disabled, created_at, last_seen_at
  FROM account;

DROP TABLE account;
ALTER TABLE account_new RENAME TO account;

-- Recreated with the table: dropping it took the index with it.
CREATE UNIQUE INDEX idx_account_username
  ON account(campaign_id, username COLLATE NOCASE);

PRAGMA foreign_keys = ON;
