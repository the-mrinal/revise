-- Migration 006: user-facing profile fields on user_profiles + avatar storage.
-- Adds display name, avatar, and per-platform public profile links
-- (e.g. {"leetcode": "https://leetcode.com/u/you"}) shown on the flex page.
--
-- Run this in the Supabase SQL Editor.

alter table public.user_profiles
  add column if not exists display_name text,
  add column if not exists avatar_url text,
  add column if not exists platform_links jsonb not null default '{}'::jsonb;

-- Public bucket for avatars. Uploads go through the server (service role) only;
-- public read is intentional — the avatar shows on the public flex page.
insert into storage.buckets (id, name, public)
values ('avatars', 'avatars', true)
on conflict (id) do nothing;
