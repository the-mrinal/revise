-- Migration 003: per-user access control + feature flags.
-- Replaces the hard-coded ALLOWED_EMAILS array in research.html with a
-- DB-backed system: user_profiles carries an is_admin role, and feature_access
-- rows grant named features (e.g. 'research') to individual users. The admin
-- panel at /admin manages both.
--
-- These tables are written ONLY by the server via the Supabase service-role
-- key, which bypasses RLS. We enable RLS with NO policies so that anon/authed
-- clients can never read or write them directly.
--
-- Run this in the Supabase SQL Editor.

create table public.user_profiles (
  user_id     uuid primary key references auth.users(id) on delete cascade,
  email       text,                          -- cached from the JWT for admin listing
  is_admin    boolean not null default false,
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);

create table public.feature_access (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references auth.users(id) on delete cascade,
  feature     text not null,                 -- e.g. 'research'
  created_at  timestamptz default now(),
  unique (user_id, feature)                  -- presence of a row = feature granted
);

create index idx_feature_access_user on public.feature_access (user_id);

alter table public.user_profiles enable row level security;
alter table public.feature_access enable row level security;
-- No policies on purpose: only the service-role key (server) may touch these.

-- ── Seed: preserve the two emails that had research access in research.html ──
-- Make the owner an admin.
insert into public.user_profiles (user_id, email, is_admin)
select id, email, true
from auth.users
where lower(email) = 'dmrinal626@gmail.com'
on conflict (user_id) do update set is_admin = true;

-- Grant 'research' to both previously-allowed emails (idempotent).
insert into public.feature_access (user_id, feature)
select id, 'research'
from auth.users
where lower(email) in ('dmrinal626@gmail.com', 'bibhashchandra4850@gmail.com')
on conflict (user_id, feature) do nothing;
