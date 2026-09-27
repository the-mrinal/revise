<div align="center">

# Revise

**Never forget what you learn.**

Track everything you study — coding problems, math exercises, design tutorials, language lessons, and more. FSRS — the modern spaced-repetition algorithm behind Anki — tells you exactly when to revise, so knowledge sticks for good.

[![Live Demo](https://img.shields.io/badge/Live-revise.mrinal.dev-6366f1?style=for-the-badge&logo=vercel&logoColor=white)](https://revise.mrinal.dev)
[![CI](https://img.shields.io/github/actions/workflow/status/the-mrinal/revise/ci.yml?branch=main&style=for-the-badge&label=CI)](https://github.com/the-mrinal/revise/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-FastAPI-3b82f6?style=for-the-badge&logo=python&logoColor=white)](https://fastapi.tiangolo.com)
[![Postgres](https://img.shields.io/badge/Postgres-17-336791?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org)

<br />

![Landing Page](docs/images/landing-hero.png)

</div>

## What It Does

You learn something. You forget it in a week. This fixes that.

Revise is a browser extension + web dashboard that:
- **Auto-detects** the platform you're using (LeetCode, Codeforces, HackerRank, Khan Academy, etc.)
- **Lets you add custom platforms** — track any website you learn from
- **Times your study** with a built-in timer — no manual entry
- **Schedules revisions** using FSRS, the modern spaced repetition algorithm behind Anki
- **Shows a dashboard** with stats, charts, activity feed, and a filterable table of everything you've tracked

No new password to remember: sign in with GitHub. Your data is yours.

## Screenshots

### Landing Page

![Supported Platforms](docs/images/landing-platforms.png)

> 10+ platforms supported out of the box, plus add your own.

### Dashboard

![Dashboard](docs/images/dashboard.png)

> Full analytics: items tracked, difficulty breakdown, platform distribution, revision schedule, and daily activity — all in one view.

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

> Start a timer when you begin studying. When you're done, rate your recall (1-5 stars), say how you solved it (yourself, with a hint, or from the solution), add notes, and save. FSRS handles the rest.

## How It Works

```
Browser Extension (Chrome / Safari)
        |
        |  REST API
        v
   FastAPI Server  -->  Postgres (sessions, data)  <--  GitHub sign-in
        |
        v
   Web Dashboard (revise.mrinal.dev/dashboard)
```

1. **Study something** on any supported platform (or add your own)
2. **Click the extension** — it auto-detects the URL and title
3. **Start the timer**, study, stop when done
4. **Rate your recall** (1-5 stars) and save
5. **FSRS schedules your next review** — things you found hard come back sooner, easy ones later. Peeking at the solution brings an item back quickly, no matter the rating
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

- **FSRS Spaced Repetition** — the same algorithm behind modern Anki. Rate your recall 1-5 stars, and it models your memory to schedule the next review at the optimal time, tunable via a per-user target retention (70–99%).
- **Honest Check-ins** — record how you solved each item: by yourself, with a hint, or from the solution. Assisted recalls earn shorter intervals so the schedule reflects what you actually know.
- **Built-in Timer** — start when you begin studying, pause/resume, stop when done. Time is recorded automatically.
- **Custom Platforms** — add any website from the dashboard settings. Define a name and URL pattern, and it auto-detects just like the built-in platforms.
- **Analytics Dashboard** — items tracked, difficulty breakdown, platform distribution, revision schedule, daily activity feed.
- **Sign in with GitHub** — one click, no new password. The extension picks up your session from the dashboard automatically.
- **10+ Platforms** — auto-detects LeetCode, Codeforces, HackerRank, CodeChef, GeeksForGeeks, InterviewBit, AtCoder, NeetCode, AlgoMonster, DesignGurus.
- **Browser Extension** — Chrome and Safari. Captures the current URL with one click.
- **Due for Revision** — the extension and dashboard both show which items are due today, so you always know what to revise.
- **CSV Export** — download your entire history as a CSV.
- **Per-user Data Isolation** — every API request is checked against the signed-in account; each user only sees their own data.

## Getting Started

### Use the hosted version (easiest)

1. Go to [revise.mrinal.dev](https://revise.mrinal.dev)
2. Click **Get Started Free**
3. Click **Sign in with GitHub**
4. Approve Revise on GitHub — you're signed in
5. Install the browser extension (see below)
6. Start learning!

### Install the Chrome Extension

1. Download [`extension.zip`](https://github.com/the-mrinal/revise/releases/latest/download/extension.zip) from the latest release
2. Unzip the downloaded file
3. Open `chrome://extensions` in Chrome
4. Enable **Developer mode** (top right toggle)
5. Click **Load unpacked** and select the unzipped folder
6. Pin the extension from the puzzle icon in the toolbar

**Safari:** Available on request. It requires a macOS/iOS native app wrapper built with Xcode. Reach out at dmrinal626@gmail.com and I'll send you the build.

### Self-host (for developers)

#### 1. Create a GitHub OAuth app

At [github.com/settings/applications/new](https://github.com/settings/applications/new):
- **Homepage URL**: `https://your-domain.com`
- **Authorization callback URL**: `https://your-domain.com/api/auth/github/callback`

Then generate a client secret.

#### 2. Environment variables

Create a `.env` file next to `docker-compose.yml`:

```env
POSTGRES_PASSWORD=...        # openssl rand -hex 24
REVISE_JWT_SECRET=...        # openssl rand -hex 32; signs sessions, keep it stable
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
DB_TARGET=local
SUPABASE_AUTH=off
```

`SERVER_URL` is set in `docker-compose.yml`; change it to your domain.

#### 3. Run

```bash
docker compose up -d
```

This starts Postgres 17 and the server at `http://localhost:8765` (landing page at `/`, dashboard at `/dashboard`). Database migrations in `server/migrations/pg/` apply automatically on startup.

#### 4. Back up

`scripts/backup.sh` dumps the database and avatars (set `BACKUP_REMOTE` to an rclone remote to copy them off the machine); `scripts/restore-test.sh` proves a dump restores. Run the backup from cron.

#### 5. Point the extension at your server

Update the `SERVER_URL` in the extension's config to point to your self-hosted instance.

#### Deploying updates

The hosted instance runs on a homelab that GitHub can't reach, so it pulls: run the **Deploy** workflow (Actions → Run workflow, pick a branch) and `scripts/homelab-deploy.sh`, polling from cron every two minutes, deploys that exact commit, checks the site is up, and logs to `~/revise-deploy.log`.

## API Endpoints

All endpoints except auth require an `Authorization: Bearer <token>` header.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/auth/config` | Which sign-in options are available |
| `GET` | `/api/auth/github/login` | Start signing in with GitHub |
| `GET` | `/api/auth/github/callback` | GitHub's redirect back; stores the session |
| `POST` | `/api/auth/github/link` | Connect GitHub to the signed-in account |
| `POST` | `/api/auth/refresh` | New access token for a refresh token |
| `POST` | `/api/auth/logout` | End a session |
| `POST` | `/api/questions` | Save a new item |
| `GET` | `/api/questions` | List all items |
| `PUT` | `/api/questions/{id}` | Edit an item |
| `DELETE` | `/api/questions/{id}` | Delete an item |
| `POST` | `/api/questions/{id}/review` | Submit a review rating + solution source (triggers FSRS) |
| `GET` | `/api/revisions/today` | Get items due for revision today |
| `GET` | `/api/activity/today` | Today's new + revised items |
| `GET` | `/api/stats` | Summary statistics |
| `GET` | `/api/platforms` | List built-in + custom platforms |
| `POST` | `/api/platforms` | Add a custom platform |
| `DELETE` | `/api/platforms/{id}` | Delete a custom platform |

## Scheduling Algorithm (FSRS)

The revision schedule uses [FSRS](https://github.com/open-spaced-repetition/fsrs4anki/wiki/ABC-of-FSRS) (Free Spaced Repetition Scheduler, the algorithm modern Anki adopted), via [py-fsrs](https://github.com/open-spaced-repetition/py-fsrs). It models each item's **memory stability** and **difficulty** from your review history and schedules the next review just before you'd forget.

Your 1-5 star rating combines with **how you solved it**:

| Rating | Solved myself | Used a hint | Saw the solution |
|--------|--------------|-------------|------------------|
| 1-2 | Back soon (lapse) | Back soon (lapse) | Back soon (lapse) |
| 3 | Short interval | Short interval | Back soon (lapse) |
| 4 | Normal interval | Capped — short | Back soon (lapse) |
| 5 | Longest interval | Capped — short | Back soon (lapse) |

Reading the answer always brings the item back quickly — solving it yourself is the only way to earn long intervals. A per-user **target retention** setting (default 90%) controls the trade-off between review frequency and forgetting.

Migrating an existing deployment from SM-2: apply `server/migrations/009_fsrs.sql`, deploy, then run `python migrate_to_fsrs.py` once (idempotent) to replay each question's review history through FSRS. Legacy rows also seed themselves lazily on their first post-migration review, so the backfill is a nicety, not a requirement.

## DSA Pattern Study Guides

The repo includes [15 in-depth study guides](thoughts/shared/research/) covering the classic DSA patterns — arrays/matrices, two pointers, sliding window, stacks, linked lists, trees, graphs, backtracking, dynamic programming, greedy, binary search, heaps, and more — each with worked problem analyses and step-by-step SVG diagrams. They're also served on the hosted instance at [revise.mrinal.dev/research](https://revise.mrinal.dev/research).

## Tech Stack

- **Backend**: Python, FastAPI
- **Database**: Postgres 17 (self-hosted, psycopg)
- **Auth**: Sign in with GitHub; Revise-issued sessions (JWT access tokens + hashed refresh tokens)
- **Frontend**: Vanilla HTML/CSS/JS (no frameworks)
- **Extension**: Manifest V3 (Chrome & Safari)
- **Deployment**: Docker Compose

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, testing, and PR guidelines. Good entry points: [platform support requests](https://github.com/the-mrinal/revise/issues?q=is%3Aissue+label%3Aplatform-support) and issues labeled [`good first issue`](https://github.com/the-mrinal/revise/issues?q=is%3Aissue+label%3A%22good+first+issue%22).

## License

MIT
