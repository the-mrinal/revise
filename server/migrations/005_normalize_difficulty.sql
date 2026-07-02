-- Migration 005: normalize the questions.difficulty column.
-- The extension always wrote lowercase ('easy'), but imported LeetCode data
-- carried capitalized values ('Easy', 'Medium'), which split the dashboard's
-- By Difficulty chart into duplicate buckets. The API now normalizes on write
-- (see QuestionIn/QuestionUpdate validators in main.py); this backfills the
-- rows written before that.
--
-- Run this in the Supabase SQL Editor.

-- Lowercase + trim, and collapse empty strings to NULL.
update public.questions
set difficulty = nullif(lower(trim(difficulty)), '')
where difficulty is distinct from nullif(lower(trim(difficulty)), '');

-- Anything outside the vocabulary the API accepts becomes NULL (= "unknown").
update public.questions
set difficulty = null
where difficulty is not null
  and difficulty not in ('easy', 'medium', 'hard');
