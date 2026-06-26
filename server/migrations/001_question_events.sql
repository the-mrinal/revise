-- Migration 001: per-question audit/event log.
-- Append-only history of every solve / review / re-attempt so we can show users
-- exactly what happened (and compute NEW vs REVISION reliably).
-- Run this in the Supabase SQL Editor.

create table public.question_events (
  id              bigint generated always as identity primary key,
  user_id         uuid not null references auth.users(id) on delete cascade,
  question_id     bigint not null references public.questions(id) on delete cascade,
  event_type      text not null,            -- 'created' | 'reviewed' | 'attempted'
  self_rating     integer,                  -- rating at the time (1-5), nullable
  time_taken      integer,                  -- minutes, nullable
  -- SM-2 snapshot AFTER applying this event (nullable for 'attempted'):
  interval        integer,
  repetitions     integer,
  easiness_factor double precision,
  next_review     date,
  reconstructed   boolean default false,    -- true for backfilled rows (approximate)
  created_at      timestamptz default now()
);

alter table public.question_events enable row level security;

create policy "Users see own events" on public.question_events for select using (auth.uid() = user_id);
create policy "Users insert own events" on public.question_events for insert with check (auth.uid() = user_id);
create policy "Users update own events" on public.question_events for update using (auth.uid() = user_id);
create policy "Users delete own events" on public.question_events for delete using (auth.uid() = user_id);

create index idx_qevents_question on public.question_events (user_id, question_id, created_at);
