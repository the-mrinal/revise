-- Migration 009: FSRS scheduler + solution-source capture.
-- Two changes shipped together:
--   1. solution_source records HOW an attempt was solved (self / hint /
--      solution) so scheduling can discount assisted recalls.
--   2. FSRS (successor to SM-2) needs per-card memory state: stability,
--      difficulty, and learning state. The old SM-2 columns
--      (easiness_factor, interval, repetitions) are kept for rollback;
--      the server stops writing easiness_factor/repetitions but keeps
--      writing interval (derived) for display.
-- The column is named fsrs_difficulty because questions.difficulty already
-- holds the easy/medium/hard label.
-- Apply via Supabase MCP apply_migration (or the SQL Editor) BEFORE
-- deploying server code that references these columns.

alter table public.questions
  add column stability        double precision,
  add column fsrs_difficulty  double precision,
  add column fsrs_state       smallint,  -- fsrs.State: 1 learning, 2 review, 3 relearning
  add column solution_source  text check (solution_source in ('self', 'hint', 'solution'));

alter table public.question_events
  add column solution_source  text check (solution_source in ('self', 'hint', 'solution')),
  -- post-event FSRS snapshot, mirroring the existing SM-2 snapshot columns
  add column stability        double precision,
  add column fsrs_difficulty  double precision,
  add column fsrs_state       smallint;

alter table public.user_settings
  add column desired_retention double precision not null default 0.9
    check (desired_retention between 0.70 and 0.99),
  -- per-user optimized FSRS parameters (filled by a future offline
  -- optimizer run; null = library defaults)
  add column fsrs_params jsonb;
