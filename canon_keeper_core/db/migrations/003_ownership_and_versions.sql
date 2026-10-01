-- Ownership and versions.
--
-- Two changes that go together, because both are about a character belonging to
-- someone and changing over time.

-- A player may own several characters. Previously a login pointed at one
-- character; now the character points back at its owner, which is the direction
-- that supports more than one.
ALTER TABLE entity ADD COLUMN owner_account_id INTEGER
  REFERENCES account(id) ON DELETE SET NULL;

CREATE INDEX idx_entity_owner ON entity(owner_account_id);

-- Bumped on every write. Three jobs:
--   * a reconnecting client asks for only what changed rather than everything;
--   * an edit made against a stale version is refused instead of silently
--     overwriting whatever happened in between;
--   * a proposed change can name the version it was made against, so approving
--     it later is a coherent act.
ALTER TABLE entity ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

-- Carry over the single character each login already had. account.character_entity_id
-- survives, but its meaning narrows to "the one they are playing right now",
-- which is what chat labels them as.
UPDATE entity SET owner_account_id = (
  SELECT account.id FROM account
  WHERE account.character_entity_id = entity.id
  LIMIT 1
)
WHERE owner_account_id IS NULL;
