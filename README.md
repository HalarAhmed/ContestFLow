# CP Assistant – Competitive Programming Assistant

Autonomous agent for contest monitoring (Codeforces + LeetCode), email reminders, auto-registration, practice tracking, and analytics. Uses MongoDB and email-only notifications.

## Setup

1. **Python 3.11+** and **MongoDB** running locally or remote.

2. **Install dependencies** (use D/E when C: is full)
   ```powershell
   cd e:\codeforces
   .\scripts\setup_use_d_and_e.ps1
   ```
   This uses **D:\\cp-assistant-cache** (or E: if D: is missing) for pip cache, temp, and Playwright browsers so C: is not filled. It installs core deps and optionally Playwright.
   **Or** install manually with D/E for caches:
   ```powershell
   $env:PIP_CACHE_DIR = "D:\cp-assistant-cache\pip"
   $env:PLAYWRIGHT_BROWSERS_PATH = "D:\cp-assistant-cache\playwright-browsers"
   pip install -r requirements-core.txt
   pip install playwright
   playwright install chromium
   ```
   In `.env` you can set `CACHE_DRIVE=E` to use E: instead of D: for Playwright path when the app runs.

3. **Environment**
   - Copy `.env.example` to `.env`
   - Set `MONGODB_URI` (e.g. `mongodb://localhost:27017`), `MONGODB_DB=cp_assistant`
   - Set `CODEFORCES_HANDLE`, `LEETCODE_USERNAME`
   - Set SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `NOTIFICATION_EMAIL`
   - For auto-registration: `CODEFORCES_PASSWORD`, `LEETCODE_PASSWORD`
   - Optional: `OPENAI_API_KEY` for the LangChain agent

## Run

- **Dashboard (API + UI)**  
  ```powershell
  python run_api.py
  ```
  Or, to force temp/Playwright on D/E: `.\scripts\run_with_de.ps1 api`  
  Open http://localhost:8000

- **Scheduler (contest monitor, practice sync, post-contest)**  
  ```powershell
  python run_scheduler.py
  ```
  Or: `.\scripts\run_with_de.ps1 scheduler`  
  Runs contest check every 30 min, practice sync every 6 h, post-contest every 1 h.

## Features

- **Contest monitoring**: Fetches upcoming contests from Codeforces and LeetCode, stores in MongoDB, sends email on new contest and at 24h / 1h / 15m before start.
- **Auto-registration**: Use dashboard “Register” or API `POST /api/register?platform=codeforces&contest_id=2200` (Playwright).
- **Practice tracking**: Syncs solved problems from both platforms into MongoDB.
- **Post-contest**: After contest end, fetches standings/rating and emails a short report.
- **Analytics**: Weak/strong tags and training plan from practice data.
- **Dashboard**: Overview, practice summary, analytics, training plan, “Update Data” to trigger sync.

## API

- `GET /api/user-config` – user config
- `GET /api/contests/upcoming` – upcoming contests
- `GET /api/practice/summary?days=30` – practice summary
- `GET /api/analytics/weak-strong-tags` – weak/strong tags
- `GET /api/analytics/training-plan` – recommended plan
- `POST /api/update-data` – run practice sync + contest monitor
- `POST /api/register?platform=codeforces&contest_id=2200` – register for contest
