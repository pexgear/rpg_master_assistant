-- Accounts and sharing.
--
-- A session server is bound to one campaign, and the campaign owns the list of
-- people allowed into it. Players log in; what they are then allowed to see is
-- decided here, on the host, and never on their machine.

CREATE TABLE account (
  id            INTEGER PRIMARY KEY,
  campaign_id   INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  username      TEXT NOT NULL,
  display_name  TEXT NOT NULL DEFAULT '',
  role          TEXT NOT NULL DEFAULT 'player' CHECK(role IN ('dm', 'player')),

  -- Password material. `verifier` is scrypt(password, salt); the password
  -- itself is never stored, and never crosses the wire either -- login is a
  -- challenge/response against this value. See canon_keeper/net/auth.py.
  salt          BLOB NOT NULL,
  verifier      BLOB NOT NULL,

  -- The PC this login plays. Chat shows this character's name rather than the
  -- username, and it is the one entity the player is allowed to edit.
  character_entity_id INTEGER REFERENCES entity(id) ON DELETE SET NULL,

  disabled      INTEGER NOT NULL DEFAULT 0,
  created_at    REAL NOT NULL,
  last_seen_at  REAL
);

CREATE UNIQUE INDEX idx_account_username
  ON account(campaign_id, username COLLATE NOCASE);

-- What a player is allowed to know about.
--
-- One row per (entity, audience). A NULL account_id means the whole party, so
-- "everyone has met the innkeeper" is one row rather than one per player, while
-- "only the rogue knows about the contact" is a row naming that account.
CREATE TABLE share (
  id          INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  entity_id   INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  account_id  INTEGER REFERENCES account(id) ON DELETE CASCADE,
  created_at  REAL NOT NULL
);

-- IFNULL because SQLite treats NULLs as distinct in a unique index, which would
-- otherwise allow the same entity to be shared with the party twice.
CREATE UNIQUE INDEX idx_share_audience
  ON share(entity_id, IFNULL(account_id, 0));

CREATE INDEX idx_share_account ON share(account_id);
