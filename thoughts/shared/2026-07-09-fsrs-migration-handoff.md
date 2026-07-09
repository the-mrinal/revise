# FSRS migration + solution-source — session handoff (2026-07-09)

**Status: implementation COMPLETE and committed. Deployment NOT started.**
Branch: `fsrs-solution-source`, commit `f3bb263` (local only — not pushed).
Base: `main` @ `51b7ff0` (in sync with origin at time of work).
Approved plan (full detail): `~/.claude/plans/i-want-you-to-shimmying-eagle.md`
Stakeholder-facing proposal artifact: https://claude.ai/code/artifact/cb118609-52be-43eb-9cb5-9639bb40767f

## What this change is

Two user-approved improvements, shipped together:

1. **`solution_source` capture** — every save/review records how the item was
   solved: `self | hint | solution`, via a 3-button segmented control. It
   AFFECTS scheduling (not just metadata).
2. **SM-2 → FSRS** — `server/sm2.py` is no longer called (kept one release for
   rollback; its tests still run green). New engine: `server/scheduler.py`
   wrapping py-fsrs 6.3.1.

### The rating mapping (implemented in `scheduler.map_rating`)

| stars | self | hint | solution |
|---|---|---|---|
| 1–2 | Again | Again | Again |
| 3 | Hard | Hard | Again |
| 4 | Good | Hard (cap) | Again |
| 5 | Easy | Hard (cap) | Again |

Verified behavior: mature 20d-stability card + "solution"+5★ → due in ≤2 days;
"hint"+5★ == self+3★ (Hard) interval; retention dial works (0.80→192d vs
0.95→23d on the same card).

## What was built (plan phase → files)

| Phase | Status | Files |
|---|---|---|
| 1. Dependency + migration | DONE | `server/requirements.txt` (fsrs==6.3.1), `server/migrations/009_fsrs.sql` |
| 2. FSRS engine | DONE | `server/scheduler.py` (map_rating, apply_review, initial_schedule, preview, lazy seed for stability-NULL rows) |
| 3. Data layer | DONE | `server/database.py` — COLUMNS/EVENT_COLUMNS/_EVENT_FIELDS widened; `update_question_sm2`→`update_question_schedule`; dupe-merge sort key repetitions→attempts; settings return `desired_retention`+`fsrs_params` (graceful pre-009 fallback) |
| 4. API | DONE | `server/main.py` — `solution_source` on QuestionIn/ReviewIn/QuestionUpdate + whitelist (line ~561); SettingsUpdate both-fields-optional (None-filtered so partial PUTs don't reset the other setting); create/review use scheduler; `/api/revisions/today` attaches `schedule_preview` (15 combos) per due item |
| 5. Backfill | DONE | `server/migrate_to_fsrs.py` — idempotent (skips stability NOT NULL), replays events oldest-first, seeds no-history rows from SM-2 interval keeping existing next_review |
| 6. Extension | DONE | `popup.html/js/css` + `overlay.js` — source toggle in both finish forms, sent in review POST + PUT payloads, resets to "self". Popup's Due-list only links out (no review POST there — checked) |
| 7. Dashboard | DONE | `templates/dashboard.html` — per-card toggle + preview tooltips (`setCardSource`/`ratingTip`), client-side `predictNextReview`/`SM2_QUALITY` deleted, `reviewQ(id,rating)` now sends source, EF chip → stability chip, Target Retention setting in modal, edit-modal source select, CSV column. `static/history.js` — hint/solution badges on events |
| 8. Tests | DONE | `tests/test_scheduler.py` (13 tests), `tests/test_api_routes.py` (+6). **60/60 pass**, incl. untouched `test_sm2.py` |
| 9. Copy | DONE | `templates/landing.html` (4 SM-2 spots reworded + new "Honest Check-ins" feature card w/ `icon-check` CSS) and `README.md` (schema blocks incl. previously-undocumented columns, FSRS algorithm section w/ mapping table + migration instructions). `grep -ri "sm-2\|sm2" server/templates/` → zero hits |

Verification done: full pytest green; `node --check` on all touched JS; server
boots with dummy env; landing + dashboard markup confirmed served.

## REMAINING WORK (in order)

1. **Apply migration 009 to Supabase** — MUST precede any deploy of this code
   (`insert_event` swallows errors silently; widened SELECTs would 400).
   The Supabase MCP is configured (`mcp.supabase.com`, project
   `omegmxlvokqbleftqjzr` — confirm this is the intended env; DDL is
   immediate) but its tools were NOT loaded in the implementing session (added
   mid-session). In a fresh session: ToolSearch "select:mcp__supabase__apply_migration,
   mcp__supabase__list_migrations,mcp__supabase__execute_sql,mcp__supabase__get_advisors",
   then `apply_migration` with name `fsrs` + contents of
   `server/migrations/009_fsrs.sql`; verify columns via `execute_sql`
   (`information_schema.columns where table_name='questions'`); run
   `get_advisors` after. Fallback: paste the SQL into the Supabase SQL Editor.
2. **Push branch + open PR** — user must say go. `git push -u origin
   fsrs-solution-source`, then `gh pr create`. main is PR-only; user
   self-merges with `--admin` (see memory: deploy-github-actions).
3. **Deploy** — via the repo's Deploy GitHub Action after merge. Cloudflare
   403s runner IPs — verify origin over SSH afterward.
4. **Run backfill once post-deploy** — `docker compose exec server python
   migrate_to_fsrs.py` (idempotent; lazy seeding covers rows until then).
5. **Extension release** — users get the new toggle from the next
   `extension.zip` release; old clients keep working (server defaults
   solution_source='self'). Chrome Web Store publishing setup is separately in
   progress (see memory).

## Follow-ups agreed in plan (not this PR)

- Delete `server/sm2.py` + `tests/test_sm2.py` one release after FSRS proves out.
- Optional offline per-user optimizer (torch, off-server) writing
  `user_settings.fsrs_params`; scheduler already reads it.
- `history.js` `outcome()` line ~96 still has a stale repetitions==1 special
  case — harmless (degrades to generic text), could drop later.

## Gotchas rediscovered while implementing

- `main.py` edit whitelist (~line 561) silently drops PUT fields not listed.
- `SettingsUpdate`: both fields Optional + None-filtered in `write_settings` —
  do NOT give them concrete defaults or a queue-size save resets retention.
- py-fsrs 6.x: `Scheduler(learning_steps=(), relearning_steps=(),
  enable_fuzzing=False)` — empty steps keep day granularity; `Rating` is
  Again/Hard/Good/Easy (1-4); 21 default parameters.
- `next_review` (date) stays the queue key; scheduler floors due at tomorrow
  so a reviewed card can't reappear same-day.
- Tests: venv at `server/.venv` (python3.13), run `.venv/bin/python -m pytest
  tests/ -q` from `server/`.
