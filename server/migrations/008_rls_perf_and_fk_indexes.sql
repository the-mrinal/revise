-- Migration 008: RLS performance fixes + foreign-key indexes.
-- Addresses two Supabase advisor findings:
--   1. auth_rls_initplan: policies that call auth.uid() directly re-evaluate
--      it for every row. Wrapping it as (select auth.uid()) makes Postgres
--      evaluate it once per query instead. Semantics are unchanged.
--   2. unindexed_foreign_keys: FK columns on access_audit and question_events
--      had no covering index.
-- Run this in the Supabase SQL Editor.

-- questions
alter policy "Users see own questions"    on public.questions using ((select auth.uid()) = user_id);
alter policy "Users insert own questions" on public.questions with check ((select auth.uid()) = user_id);
alter policy "Users update own questions" on public.questions using ((select auth.uid()) = user_id);
alter policy "Users delete own questions" on public.questions using ((select auth.uid()) = user_id);

-- question_events
alter policy "Users see own events"    on public.question_events using ((select auth.uid()) = user_id);
alter policy "Users insert own events" on public.question_events with check ((select auth.uid()) = user_id);
alter policy "Users update own events" on public.question_events using ((select auth.uid()) = user_id);
alter policy "Users delete own events" on public.question_events using ((select auth.uid()) = user_id);

-- user_settings
alter policy "Users see own settings"    on public.user_settings using ((select auth.uid()) = user_id);
alter policy "Users insert own settings" on public.user_settings with check ((select auth.uid()) = user_id);
alter policy "Users update own settings" on public.user_settings using ((select auth.uid()) = user_id);
alter policy "Users delete own settings" on public.user_settings using ((select auth.uid()) = user_id);

-- user_platforms
alter policy "Users see own platforms"    on public.user_platforms using ((select auth.uid()) = user_id);
alter policy "Users insert own platforms" on public.user_platforms with check ((select auth.uid()) = user_id);
alter policy "Users update own platforms" on public.user_platforms using ((select auth.uid()) = user_id);
alter policy "Users delete own platforms" on public.user_platforms using ((select auth.uid()) = user_id);

-- FK covering indexes
create index if not exists idx_access_audit_actor_id      on public.access_audit (actor_id);
create index if not exists idx_access_audit_target_id     on public.access_audit (target_id);
create index if not exists idx_question_events_question_id on public.question_events (question_id);
