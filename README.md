<div align="center">

# Revise

**Never forget what you learn.**

Track everything you study — coding problems, math exercises, design tutorials, language lessons, and more. The SM-2 algorithm tells you exactly when to revise, so knowledge sticks for good.

[![Live Demo](https://img.shields.io/badge/Live-revise.mrinal.dev-6366f1?style=for-the-badge&logo=vercel&logoColor=white)](https://revise.mrinal.dev)
[![License](https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-FastAPI-3b82f6?style=for-the-badge&logo=python&logoColor=white)](https://fastapi.tiangolo.com)
[![Supabase](https://img.shields.io/badge/Supabase-Postgres-3ecf8e?style=for-the-badge&logo=supabase&logoColor=white)](https://supabase.com)

<br />

![Landing Page](docs/images/landing-hero.png)

</div>

## What It Does

You learn something. You forget it in a week. This fixes that.

Revise is a browser extension + web dashboard that:
- **Auto-detects** the platform you're using (LeetCode, Codeforces, HackerRank, Khan Academy, etc.)
- **Lets you add custom platforms** — track any website you learn from
- **Times your study** with a built-in timer — no manual entry
- **Schedules revisions** using the SM-2 spaced repetition algorithm (same algorithm behind Anki and SuperMemo)
- **Shows a dashboard** with stats, charts, activity feed, and a filterable table of everything you've tracked

No passwords. Sign in with a magic link. Your data is yours.

## Screenshots

### Landing Page

![Supported Platforms](docs/images/landing-platforms.png)

> 10+ platforms supported out of the box, plus add your own.

### Dashboard

![Dashboard](docs/images/dashboard.png)

> Full analytics: items tracked, difficulty breakdown, platform distribution, revision schedule, and daily activity — all in one view.

### Magic Link Login

![Login](docs/images/login.png)

> No passwords to remember. Enter your email, click the link, you're in.

### Browser Extension

<p>
  <img src="docs/images/extension-auto-detect.png" width="280" alt="Extension - Auto Detect" />
  <img src="docs/images/extension-capture.png" width="280" alt="Extension - Capture Problem" />
</p>

> The extension auto-detects the URL and title. Navigate to any supported platform and it picks it up instantly.

<p>
  <img src="docs/images/extension-timer.png" width="280" alt="Extension - Timer Running" />
  <img src="docs/images/extension-save.png" width="280" alt="Extension - Save Question" />
</p>

> Start a timer when you begin studying. When you're done, rate your recall (1-5 stars), add notes, and save. The SM-2 algorithm handles the rest.

## How It Works

```
Browser Extension (Chrome / Safari)
        |
        |  REST API
        v
   FastAPI Server  -->  Supabase (Postgres + Auth)
        |
        v
   Web Dashboard (revise.mrinal.dev/dashboard)
```

1. **Study something** on any supported platform (or add your own)
2. **Click the extension** — it auto-detects the URL and title
3. **Start the timer**, study, stop when done
4. **Rate your recall** (1-5 stars) and save
5. **SM-2 schedules your next review** — things you found hard come back sooner, easy ones later
6. **Check the dashboard** for what's due today, your stats, and your full history

## Supported Platforms

| Platform | Auto-detected |
|----------|:---:|
| LeetCode | Yes |
| Codeforces | Yes |
| HackerRank | Yes |
| CodeChef | Yes |
| GeeksForGeeks | Yes |
| InterviewBit | Yes |
| AtCoder | Yes |
| NeetCode | Yes |
| AlgoMonster | Yes |
| DesignGurus.io | Yes |
| **Custom Platforms** | **User-defined** |

Any other URL works too — it's tagged as "other". You can add custom platforms from the dashboard settings to auto-detect any website.

## Features

- **SM-2 Spaced Repetition** — the same algorithm behind Anki and SuperMemo. Rate your recall 1-5 stars, and the system schedules your next review at the optimal time.
- **Built-in Timer** — start when you begin studying, pause/resume, stop when done. Time is recorded automatically.
- **Custom Platforms** — add any website from the dashboard settings. Define a name and URL pattern, and it auto-detects just like the built-in platforms.
- **Analytics Dashboard** — items tracked, difficulty breakdown, platform distribution, revision schedule, daily activity feed.
- **Magic Link Auth** — no passwords. Enter your email, click the link in your inbox, done. Powered by Supabase Auth.
- **10+ Platforms** — auto-detects LeetCode, Codeforces, HackerRank, CodeChef, GeeksForGeeks, InterviewBit, AtCoder, NeetCode, AlgoMonster, DesignGurus.
- **Browser Extension** — Chrome and Safari. Captures the current URL with one click.
- **Due for Revision** — the extension and dashboard both show which items are due today, so you always know what to revise.
- **CSV Export** — download your entire history as a CSV.
- **Per-user Data Isolation** — Row Level Security on Supabase. Each user only sees their own data.

## Getting Started

### Use the hosted version (easiest)

1. Go to [revise.mrinal.dev](https://revise.mrinal.dev)
2. Click **Get Started Free**
3. Enter your email and click **Send Magic Link**
4. Check your inbox, click the link — you're logged in
5. Install the browser extension (see below)
6. Start learning!

### Install the Chrome Extension

1. Download [`extension.zip`](https://github.com/the-mrinal/code-revision-tracker/raw/main/extension.zip)
2. Unzip the downloaded file
3. Open `chrome://extensions` in Chrome
4. Enable **Developer mode** (top right toggle)
5. Click **Load unpacked** and select the unzipped folder
6. Pin the extension from the puzzle icon in the toolbar

**Safari:** Available on request. It requires a macOS/iOS native app wrapper built with Xcode. Reach out at dmrinal626@gmail.com and I'll send you the build.

### Self-host (for developers)

#### 1. Supabase Setup

Create a [Supabase](https://supabase.com) project and run this in the SQL Editor:

```sql
create table public.questions (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  url text not null,
  title text,
  platform text,
  difficulty text,
  self_rating integer check (self_rating between 1 and 5),
  time_taken integer,
  notes text,
  solved_at timestamptz default now(),
  easiness_factor double precision default 2.5,
  interval integer default 1,
  repetitions integer default 0,
  next_review date,
  last_reviewed timestamptz,
  attempts integer default 1
);

alter table public.questions enable row level security;

create policy "Users see own questions" on public.questions for select using (auth.uid() = user_id);
create policy "Users insert own questions" on public.questions for insert with check (auth.uid() = user_id);
create policy "Users update own questions" on public.questions for update using (auth.uid() = user_id);
create policy "Users delete own questions" on public.questions for delete using (auth.uid() = user_id);

create index idx_questions_user_url on public.questions(user_id, url);
create index idx_questions_next_review on public.questions(user_id, next_review);

-- Custom platforms (for user-defined URL patterns)
create table public.user_platforms (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  url_pattern text not null,
  created_at timestamptz default now()
);

alter table public.user_platforms enable row level security;

create policy "Users see own platforms" on public.user_platforms for select using (auth.uid() = user_id);
create policy "Users insert own platforms" on public.user_platforms for insert with check (auth.uid() = user_id);
create policy "Users update own platforms" on public.user_platforms for update using (auth.uid() = user_id);
create policy "Users delete own platforms" on public.user_platforms for delete using (auth.uid() = user_id);

create unique index idx_user_platforms_unique on public.user_platforms(user_id, name);

-- Per-question audit/event log (history of every solve / review / re-attempt).
-- Also available as server/migrations/001_question_events.sql
create table public.question_events (
  id              bigint generated always as identity primary key,
  user_id         uuid not null references auth.users(id) on delete cascade,
  question_id     bigint not null references public.questions(id) on delete cascade,
  event_type      text not null,            -- 'created' | 'reviewed' | 'attempted'
  self_rating     integer,
  time_taken      integer,
  interval        integer,                  -- SM-2 snapshot AFTER this event
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

create index idx_qevents_question on public.question_events(user_id, question_id, created_at);
```

Configure Auth redirect URLs in Supabase Dashboard:
- **Site URL**: `https://your-domain.com/dashboard`
- **Redirect URL**: `https://your-domain.com/api/auth/callback`

#### 2. Environment Variables

Create a `.env` file:

```env
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
SUPABASE_JWT_SECRET=your-jwt-secret
SERVER_URL=https://your-domain.com
```

#### 3. Run

```bash
docker compose up -d
```

The server starts at `http://localhost:8765`. The landing page is at `/` and the dashboard at `/dashboard`.

#### 4. Point the extension at your server

Update the `SERVER_URL` in the extension's config to point to your self-hosted instance.

## API Endpoints

All endpoints except auth require an `Authorization: Bearer <token>` header.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/auth/magic-link` | Send magic link email |
| `GET` | `/api/auth/callback` | Handle auth callback (PKCE + implicit flow) |
| `POST` | `/api/auth/refresh` | Refresh access token |
| `POST` | `/api/questions` | Save a new item |
| `GET` | `/api/questions` | List all items |
| `PUT` | `/api/questions/{id}` | Edit an item |
| `DELETE` | `/api/questions/{id}` | Delete an item |
| `POST` | `/api/questions/{id}/review` | Submit a review rating (triggers SM-2) |
| `GET` | `/api/revisions/today` | Get items due for revision today |
| `GET` | `/api/activity/today` | Today's new + revised items |
| `GET` | `/api/stats` | Summary statistics |
| `GET` | `/api/platforms` | List built-in + custom platforms |
| `POST` | `/api/platforms` | Add a custom platform |
| `DELETE` | `/api/platforms/{id}` | Delete a custom platform |

## SM-2 Algorithm

The revision schedule uses the [SM-2 algorithm](https://en.wikipedia.org/wiki/SuperMemo#Description_of_SM-2_algorithm):

| Rating | Meaning | What happens |
|--------|---------|--------------|
| 1-2 | Forgot / struggled | Interval resets — review again soon |
| 3 | Hard recall | Short interval |
| 4 | Good recall | Moderate interval |
| 5 | Easy recall | Long interval |

The easiness factor adjusts over time. Things you consistently nail appear less frequently. Things you struggle with keep coming back until they stick.

## Tech Stack

- **Backend**: Python, FastAPI
- **Database**: Supabase (Postgres + Row Level Security)
- **Auth**: Supabase Auth (magic link / passwordless)
- **Frontend**: Vanilla HTML/CSS/JS (no frameworks)
- **Extension**: Manifest V3 (Chrome & Safari)
- **Deployment**: Docker Compose

## Contributing

Contributions welcome! Open an issue or submit a PR.

## License

MIT
