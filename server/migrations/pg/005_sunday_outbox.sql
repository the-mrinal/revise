-- Saves waiting to be sent to Sunday, for accounts in sunday_links.
-- A row is added after a save and deleted once Sunday has it; while Sunday
-- can't be reached it stays, and next_try_at moves further out each time.
-- ref is "<question id>:<event id>"; sending the same ref again replaces
-- that line in Sunday, so a newer row for a ref replaces an unsent older one.
CREATE TABLE IF NOT EXISTS sunday_outbox (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  ref          text NOT NULL,
  body         jsonb NOT NULL,
  tries        integer NOT NULL DEFAULT 0,
  next_try_at  timestamptz NOT NULL DEFAULT now(),
  last_error   text,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sunday_outbox_next_try ON sunday_outbox (next_try_at);
ALTER TABLE sunday_outbox ENABLE ROW LEVEL SECURITY;
