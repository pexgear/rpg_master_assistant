-- Invites: how a player gets an account without the DM inventing one for them.
--
-- A campaign starts with characters and nobody to play them. The DM makes an
-- invite *for a character* and sends the code; the person who has it makes
-- their own username and password, and the account arrives already attached to
-- that character. The DM never types somebody else's password and never learns
-- it, which is the only arrangement where "do not reuse your password" is
-- advice they are allowed to give.
--
-- **The code is stored as written.** The host has to have it to open what the
-- joining player sealed with it -- see canon_keeper_protocol/enrol.py -- so it
-- cannot be kept as a hash the way a password is. That means somebody holding
-- the campaign file holds any invite still outstanding. They also hold every
-- password verifier in that file, so this adds nothing they did not have.
--
-- **One live invite per character.** `used_at` and `revoked_at` are timestamps
-- rather than a status column, because the question is never only "is this
-- live" -- it is "what happened to the one I sent on Tuesday". Making a new
-- invite for a character stamps `revoked_at` on the others, which is what makes
-- a code that was never taken up stop working the moment it is replaced.

CREATE TABLE invite (
  id          INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  -- The character this invite is for. Deleting the character kills the invite:
  -- an invite with nothing on the other end of it is not an account waiting to
  -- happen, it is a way into the campaign with no reason attached.
  entity_id   INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  code        TEXT    NOT NULL,
  created_at  REAL    NOT NULL,
  expires_at  REAL    NOT NULL,
  used_at     REAL,
  revoked_at  REAL,
  -- Who it made, once somebody has taken it up. Kept so the DM can see that
  -- Tuesday's invite is the reason this account exists.
  account_id  INTEGER REFERENCES account(id) ON DELETE SET NULL
);

CREATE INDEX invite_by_entity ON invite(entity_id);
CREATE INDEX invite_live ON invite(campaign_id, used_at, revoked_at);
