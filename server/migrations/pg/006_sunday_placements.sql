-- Where Sunday put each question's newest save, from its answer to
-- POST /api/revise/saves: {placed: week|own|skipped, week, module}.
-- One row per question; each answer replaces the last. All items shows it
-- in the Sunday column. Removing the link (either side's Disconnect) removes
-- that person's rows, so reconnecting starts clean.
CREATE TABLE IF NOT EXISTS sunday_placements (
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id  bigint NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  placed       text NOT NULL CHECK (placed IN ('week', 'own', 'skipped')),
  week         integer,
  module       integer,
  placed_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, question_id)
);
ALTER TABLE sunday_placements ENABLE ROW LEVEL SECURITY;
