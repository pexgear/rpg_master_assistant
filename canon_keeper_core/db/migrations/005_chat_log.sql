-- The table's conversation, kept.
--
-- Grouped by play session, so each evening is its own log rather than one
-- endless scroll. Everything is kept; only the recent tail is ever loaded,
-- because nobody rejoining a game wants to scroll through last month.

CREATE TABLE chat_message (
  id           INTEGER PRIMARY KEY,
  campaign_id  INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  -- Which evening this belongs to. Null if it was said outside a session.
  session_id   INTEGER REFERENCES session(id) ON DELETE SET NULL,

  -- said | rolled | system
  kind         TEXT NOT NULL,
  -- Who said it, as they were known at the time. Deliberately a copy rather
  -- than a reference: a log should still read correctly after someone renames
  -- their character or their login is deleted.
  speaker      TEXT NOT NULL DEFAULT '',
  role         TEXT NOT NULL DEFAULT '',

  text         TEXT NOT NULL,
  -- Dice details, so an old roll can still be shown in full.
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at   REAL NOT NULL
);

CREATE INDEX idx_chat_recent ON chat_message(campaign_id, created_at);
CREATE INDEX idx_chat_session ON chat_message(session_id, created_at);
