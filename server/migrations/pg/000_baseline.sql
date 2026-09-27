-- Baseline schema for Revise's own Postgres.
--
-- Recreates the tables that lived in Supabase's public schema (as of
-- migration 009), with the same column order, defaults, checks and indexes,
-- so data copies across unchanged. Differences from Supabase:
--   * a public.users table replaces auth.users; ids are the same UUIDs
--   * foreign keys point at users(id) instead of auth.users(id)
--   * no row-level-security policies: they relied on auth.uid(), and the
--     server is the only client (it always filters by user_id itself)
--
-- Everything is IF NOT EXISTS, so this also runs harmlessly against the
-- Supabase database while it is still the one serving requests: there it
-- only adds the users table (existing tables keep their auth.users keys).

CREATE TABLE IF NOT EXISTS users (
  id               uuid PRIMARY KEY,               -- same value as the old auth.users.id
  email            text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  last_sign_in_at  timestamptz,
  source           text NOT NULL DEFAULT 'supabase'
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users (lower(email));
-- On Supabase, public tables are reachable through its REST API; keep this
-- one closed to everything but the server's own connection.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS questions (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  url               text NOT NULL,
  title             text,
  platform          text,
  difficulty        text,
  self_rating       integer CHECK (self_rating >= 1 AND self_rating <= 5),
  time_taken        integer,
  notes             text,
  solved_at         timestamptz DEFAULT now(),
  easiness_factor   double precision DEFAULT 2.5,
  "interval"        integer DEFAULT 1,
  repetitions       integer DEFAULT 0,
  next_review       date,
  last_reviewed     timestamptz,
  attempts          integer DEFAULT 1,
  pattern           text,
  question_type     text DEFAULT 'dsa',
  approach          text,
  mistakes          text,
  time_complexity   text,
  space_complexity  text,
  stability         double precision,
  fsrs_difficulty   double precision,
  fsrs_state        smallint,
  solution_source   text CHECK (solution_source IN ('self', 'hint', 'solution'))
);
CREATE INDEX IF NOT EXISTS idx_questions_user_url ON questions (user_id, url);
CREATE INDEX IF NOT EXISTS idx_questions_next_review ON questions (user_id, next_review);

CREATE TABLE IF NOT EXISTS question_events (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id      bigint NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  event_type       text NOT NULL,
  self_rating      integer,
  time_taken       integer,
  "interval"       integer,
  repetitions      integer,
  easiness_factor  double precision,
  next_review      date,
  reconstructed    boolean DEFAULT false,
  created_at       timestamptz DEFAULT now(),
  solution_source  text CHECK (solution_source IN ('self', 'hint', 'solution')),
  stability        double precision,
  fsrs_difficulty  double precision,
  fsrs_state       smallint
);
CREATE INDEX IF NOT EXISTS idx_qevents_question ON question_events (user_id, question_id, created_at);
CREATE INDEX IF NOT EXISTS idx_question_events_question_id ON question_events (question_id);

CREATE TABLE IF NOT EXISTS user_settings (
  user_id              uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  revision_queue_size  integer NOT NULL DEFAULT 20,
  updated_at           timestamptz DEFAULT now(),
  desired_retention    double precision NOT NULL DEFAULT 0.9
    CHECK (desired_retention >= 0.70 AND desired_retention <= 0.99),
  fsrs_params          jsonb
);

CREATE TABLE IF NOT EXISTS user_platforms (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name         text NOT NULL,
  url_pattern  text NOT NULL,
  created_at   timestamptz DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_platforms_unique ON user_platforms (user_id, name);

CREATE TABLE IF NOT EXISTS user_profiles (
  user_id         uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  email           text,
  is_admin        boolean NOT NULL DEFAULT false,
  created_at      timestamptz DEFAULT now(),
  updated_at      timestamptz DEFAULT now(),
  display_name    text,
  avatar_url      text,
  platform_links  jsonb NOT NULL DEFAULT '{}'::jsonb,
  timezone        text NOT NULL DEFAULT 'Asia/Kolkata'
);

CREATE TABLE IF NOT EXISTS feature_access (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  feature     text NOT NULL,
  created_at  timestamptz DEFAULT now(),
  UNIQUE (user_id, feature)
);
CREATE INDEX IF NOT EXISTS idx_feature_access_user ON feature_access (user_id);

CREATE TABLE IF NOT EXISTS access_audit (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor_id      uuid REFERENCES users(id) ON DELETE SET NULL,
  actor_email   text,
  target_id     uuid REFERENCES users(id) ON DELETE SET NULL,
  target_email  text,
  action        text NOT NULL,
  feature       text,
  created_at    timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_access_audit_created ON access_audit (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_access_audit_actor_id ON access_audit (actor_id);
CREATE INDEX IF NOT EXISTS idx_access_audit_target_id ON access_audit (target_id);
