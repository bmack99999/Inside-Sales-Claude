# SkyTab Inside Sales — Claude Code Context

## Who I'm working with
**Bryce Mack** — Inside Sales Rep at Shift4, selling SkyTab POS to restaurants.
- Email: bryce.mack@shift4.com
- Tools: Salesforce CRM (`crmcredorax.lightning.force.com`), CX1 dialer, Google Meet
- Product: SkyTab POS — key selling points: Advantage Program (dual pricing, 99% adoption), Lighthouse software ($20/mo), hardware $29.99/device/mo (waived year 1 with Advantage)
- Lead pipeline: ~50-75 fresh leads/month + ~1,500+ recycled leads

## Project Overview
A Flask web app deployed on **Railway** (`https://web-production-980e0.up.railway.app`) that gives Bryce a prioritized daily call list, KPI tracking, team leaderboard, and recycled lead management — all sourced from Salesforce via SF CLI.

**This is a Railway deployment, not localhost.** Pushing to GitHub (`main` branch) triggers a Railway redeploy.

## Key Files

### Extraction (runs on local Mac)
- `extract_salesforce.py` — Pulls open leads + opps from SF, scores them, POSTs to Railway
- `extract_recycled.py` — Pulls recycled leads (Unqualified/Recycled status), categorizes them, POSTs to Railway
- `scripts/extract_team_metrics.py` — Pulls MTD team leaderboard stats, POSTs to Railway
- `scripts/morning_briefing.py` — Reads team_metrics.json and sends iMessage briefing to Bryce
- `run_extraction.sh` — Runs all 4 scripts in sequence; called by cron and desktop shortcut

### Dashboard (Railway Flask app)
- `dashboard/app.py` — Flask routes, DB models migration on startup, priority scoring logic (`score_record()`)
- `dashboard/models.py` — SQLAlchemy models (Lead, Opportunity, RecycledLead, TeamMetrics, etc.)
- `dashboard/templates/dashboard.html` — "Daily Plan" page: prioritized lead + opp call list
- `dashboard/templates/my_leads.html` — "My Leads" page: full lead and opp tables with sorting
- `dashboard/templates/recycled.html` — "Recycled" page: 1,500+ recycled leads with search/filter
- `dashboard/templates/kpis.html` — "KPIs" page: MTD charts, team leaderboard, activity log
- `dashboard/static/style.css` — Navy/white theme
- `dashboard/data/` — Local JSON backup files (also used by morning_briefing.py)

### Configuration
- `requirements.txt` — Python dependencies for Railway
- `nixpacks.toml` — Railway build config
- `Procfile` — Railway start command

## Salesforce Setup
- **SF CLI alias:** `shift4` (authenticated as bryce.mack@shift4.com)
- **SF CLI command:** `sf data query --query "..." --target-org shift4 --json`
- **Instance:** `crmcredorax.lightning.force.com`
- **My Open Leads list:** `filterName=My_Open_Leads`
- **My Open Opps list:** `filterName=My_Open_Opportunities2` (NOT MyOpportunities — that returns 50+ closed records)

### Key SF Field Notes
- Phone on Opportunities: NOT on the Opp record — lives on the Contact via Contact Roles
- ContentNotes: queried via `ContentDocumentLink` → `ContentNote` (TextPreview field)
- Call activities: Task records with `Type='Call'`, `Status='Completed'`

## Extraction Workflow
Bryce runs `run_extraction.sh` via:
1. **Desktop shortcut** — `Refresh SF Data.command` on the Mac desktop
2. **Scheduled cron** — 9am and 5:55pm Mon-Fri (see crontab on local Mac)

The cron uses `bash -l` (login shell) to ensure SF CLI auth is available:
```
0 9 * * 1-5 /bin/bash -l -c 'cd /Users/bryce/Inside-Sales-Claude && bash run_extraction.sh' >> /Users/bryce/Inside-Sales-Claude/logs/extract.log 2>&1
```

Logs are at `logs/extract.log`.

## Priority Scoring (score_record in app.py)
Scores leads and opps 1-100 for daily call prioritization:
- **Activity recency** (max 35): 7-14 days since last contact = prime window
- **Open task due** (max 30): Overdue = +30, due tomorrow = +20
- **Status/Stage** (max 25): Working/Qualified leads, Proposal/Demo opps score highest
- **Call attempts** (max 10): 1-5 attempts is ideal
- **Positive signals** (max 5): Keywords in notes like "interested", "callback", "demo", "pricing"

## Railway Deployment
- **URL:** `https://web-production-980e0.up.railway.app`
- **Database:** PostgreSQL via SQLAlchemy (Railway managed)
- **Deploy trigger:** Push to `main` branch on GitHub
- **Ingest API:** `POST /api/ingest` with `X-API-Key` header — extraction scripts use this to push data
- **API Key:** `d219d2be8540f1d079dd896937fbd8fe41c9754ab955629cf74d43068e99d36d`
- **DB migrations:** App auto-runs `ALTER TABLE ADD COLUMN IF NOT EXISTS` on startup (no Alembic)

## Salesforce Safety Rules — NON-NEGOTIABLE
**NEVER perform any of the following, even if it seems helpful:**
1. Never delete a lead
2. Never set a lead status to "Unqualified"
3. Never delete an opportunity
4. Never change an opportunity Stage to any Closed variant (Closed Won, Closed Lost, etc.)
5. Never modify any Salesforce record unless Bryce explicitly asks for that specific change in the current session

These are production records tied to Bryce's real pipeline and performance metrics.

## Working Style & Preferences
- **Terse responses** — No trailing summaries, no preamble, just do it
- **Incremental builds** — Add one thing at a time, don't over-engineer
- **No extra features** — Don't add things that weren't asked for
- **Railway = production** — Always push to GitHub to deploy; local changes don't affect what Bryce sees
- **SF CLI alias** is `shift4` — always use `--target-org shift4`

## Platform Notes
- **Mac (personal):** `python3` command, cron for scheduling, iMessage briefing via `run_extraction.sh`
- **Windows (work PC):** `python` command, Task Scheduler for scheduling, use `run_extraction.bat`, iMessage not available (briefing prints to console only)
- On Windows, SF CLI auth is in `%USERPROFILE%\.sf\` — no shell sourcing needed; it's available in any terminal session after `sf org login web --alias shift4`

## Sales Documents (in project root)
- `Farzad Method - Sales Script (Claude).docx` — Primary daily-use sales script
- `Templates - Copy & Paste (Claude).docx` — Email/SMS templates
- `Inside Sales Cadence & Templates (Claude).docx` — Full 10-day cadence
- `Objection Handler (Claude).docx`
- `Demo Script - Google Meet (Claude).docx`
- `SkyTab Proposal Template (Claude).docx`
