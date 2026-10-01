-- Changes a player has proposed, waiting for the DM.
--
-- Only the *build* half of a sheet comes through here: level, class, ability
-- scores -- the things that describe what a character is. Hit points and
-- conditions apply immediately, because stopping to ask mid-combat would make
-- the app unusable.

CREATE TABLE pending_change (
  id            INTEGER PRIMARY KEY,
  campaign_id   INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  entity_id     INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  -- Who is asking. Null would mean the DM, who does not need to ask.
  account_id    INTEGER REFERENCES account(id) ON DELETE CASCADE,

  -- The proposed values, as a JSON object of sheet fields. Only the fields
  -- being changed, so approving one does not silently re-apply the rest.
  changes_json  TEXT NOT NULL,

  -- The version the proposal was made against. If the sheet has moved on since,
  -- approving it blindly would overwrite whatever happened in between.
  base_version  INTEGER NOT NULL,

  status        TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open', 'approved', 'rejected', 'stale')),
  note          TEXT NOT NULL DEFAULT '',
  created_at    REAL NOT NULL,
  decided_at    REAL
);

CREATE INDEX idx_pending_open ON pending_change(campaign_id, status);
CREATE INDEX idx_pending_entity ON pending_change(entity_id);
