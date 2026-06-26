-- Migration 002: per-user settings.
-- One row per user holding dashboard/queue preferences. Currently just the
-- revision queue size (how many revisions are surfaced as "due" at once).
-- Run this in the Supabase SQL Editor.

create table public.user_settings (
  user_id              uuid primary key references auth.users(id) on delete cascade,
  revision_queue_size  integer not null default 20,  -- 0 = unlimited
  updated_at           timestamptz default now()
);

alter table public.user_settings enable row level security;

create policy "Users see own settings" on public.user_settings for select using (auth.uid() = user_id);
create policy "Users insert own settings" on public.user_settings for insert with check (auth.uid() = user_id);
create policy "Users update own settings" on public.user_settings for update using (auth.uid() = user_id);
create policy "Users delete own settings" on public.user_settings for delete using (auth.uid() = user_id);
