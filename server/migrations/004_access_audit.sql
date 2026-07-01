-- Migration 004: audit log for access-control changes.
-- Immutable record of who granted/revoked a feature or admin role, to whom,
-- and when. Written only by the server (service-role key); RLS on with no
-- policies so clients can't read/write it directly. Shown in the /admin panel.
--
-- Run this in the Supabase SQL Editor.

create table public.access_audit (
  id            bigint generated always as identity primary key,
  actor_id      uuid references auth.users(id) on delete set null,
  actor_email   text,
  target_id     uuid references auth.users(id) on delete set null,
  target_email  text,
  action        text not null,   -- 'grant' | 'revoke' | 'make_admin' | 'remove_admin'
  feature       text,            -- set for grant/revoke; null for admin-role changes
  created_at    timestamptz default now()
);

create index idx_access_audit_created on public.access_audit (created_at desc);

alter table public.access_audit enable row level security;
-- No policies on purpose: only the service-role key (server) may touch this.
