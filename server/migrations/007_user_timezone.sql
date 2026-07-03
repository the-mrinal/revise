-- Migration 007: per-user timezone on user_profiles.
-- Used to bucket activity into local days for the heatmap and streaks.
-- Defaults to Indian Standard Time; users change it in the Profile modal.
-- Values are IANA zone names (e.g. 'Asia/Kolkata', 'America/New_York'),
-- validated server-side against Python's zoneinfo.
--
-- Run this in the Supabase SQL Editor.

alter table public.user_profiles
  add column if not exists timezone text not null default 'Asia/Kolkata';
