-- Canon Keeper initial schema.
--
-- Four ideas: entities are things, links are how they relate, facts are what is
-- true and when it stopped being true, and the DM's own words are the only
-- source of truth. A proposal is never a source for a fact.

CREATE TABLE campaign (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  created_at  REAL NOT NULL
);

-- `kind` is deliberately unconstrained TEXT rather than a CHECK: a third-party
-- panel must be able to introduce its own entity kind without a migration.
-- First-party kinds are npc | pc | location | faction | item.
CREATE TABLE entity (
  id           INTEGER PRIMARY KEY,
  campaign_id  INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL,
  name         TEXT NOT NULL,
  slug         TEXT NOT NULL,
  aliases_json TEXT NOT NULL DEFAULT '[]',
  summary      TEXT NOT NULL DEFAULT '',
  data_json    TEXT NOT NULL DEFAULT '{}',
  parent_id    INTEGER REFERENCES entity(id) ON DELETE SET NULL,
  created_at   REAL NOT NULL,
  updated_at   REAL NOT NULL
);

CREATE INDEX idx_entity_campaign_kind ON entity(campaign_id, kind);
CREATE INDEX idx_entity_parent        ON entity(parent_id);
CREATE UNIQUE INDEX idx_entity_slug   ON entity(campaign_id, kind, slug);

CREATE TABLE link (
  id          INTEGER PRIMARY KEY,
  from_entity INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  to_entity   INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  rel         TEXT NOT NULL,
  note        TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_link_from ON link(from_entity);
CREATE INDEX idx_link_to   ON link(to_entity);

CREATE TABLE session (
  id          INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  title       TEXT NOT NULL DEFAULT '',
  started_at  REAL NOT NULL,
  ended_at    REAL
);

-- Everything the DM actually said, verbatim.
CREATE TABLE utterance (
  id         INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
  t          REAL NOT NULL,
  text       TEXT NOT NULL,
  audio_path TEXT
);

CREATE INDEX idx_utterance_session ON utterance(session_id, t);

-- The truth. Rows are never deleted; they are superseded.
CREATE TABLE fact (
  id              INTEGER PRIMARY KEY,
  campaign_id     INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  subject_entity  INTEGER REFERENCES entity(id) ON DELETE CASCADE,
  predicate       TEXT NOT NULL,
  object          TEXT NOT NULL,
  source_utterance INTEGER REFERENCES utterance(id) ON DELETE SET NULL,
  confirmed       INTEGER NOT NULL DEFAULT 0,
  asserted_at     REAL NOT NULL,
  superseded_by   INTEGER REFERENCES fact(id) ON DELETE SET NULL
);

CREATE INDEX idx_fact_current ON fact(campaign_id, subject_entity)
  WHERE superseded_by IS NULL;

-- What the model offered. Never a source of truth.
CREATE TABLE proposal (
  id          INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  created_at  REAL NOT NULL,
  label       TEXT NOT NULL DEFAULT '',
  body        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'open'
              CHECK(status IN ('open', 'taken', 'discarded'))
);

CREATE TABLE conversation (
  id          INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
  entity_id   INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  session_id  INTEGER REFERENCES session(id) ON DELETE SET NULL,
  started_at  REAL NOT NULL
);

CREATE TABLE message (
  id                  INTEGER PRIMARY KEY,
  conversation_id     INTEGER NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
  role                TEXT NOT NULL CHECK(role IN ('dm', 'npc')),
  text                TEXT NOT NULL,
  grounded_facts_json TEXT NOT NULL DEFAULT '[]',
  created_at          REAL NOT NULL
);

CREATE INDEX idx_message_conversation ON message(conversation_id, created_at);

-- Dock arrangement, as produced by QMainWindow.saveState()/saveGeometry().
CREATE TABLE app_layout (
  name        TEXT PRIMARY KEY,
  geometry_b64 TEXT NOT NULL,
  state_b64   TEXT NOT NULL,
  is_default  INTEGER NOT NULL DEFAULT 0,
  updated_at  REAL NOT NULL
);

CREATE TABLE setting (
  key        TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);
