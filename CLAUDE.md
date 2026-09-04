# SkyTab Inside Sales — Claude Code Context

## Who I'm working with
**Bryce Mack** — Inside Sales Rep at Shift4, selling SkyTab POS to restaurants.
- Email: bryce.mack@shift4.com
- Tools: Salesforce CRM (`crmcredorax.lightning.force.com`), CX1 dialer, Microsoft Teams (demos/meetings)
- Product: SkyTab POS — key selling points: Advantage Program (dual pricing, 99% adoption), Lighthouse software ($20/mo), hardware $29.99/device/mo (waived year 1 with Advantage)
- Lead pipeline: ~50-75 fresh leads/month + ~1,500+ recycled leads

## Project Overview
A Flask web app deployed on **Railway** (`https://web-production-980e0.up.railway.app`) that gives Bryce a prioritized daily call list, KPI tracking, team leaderboard, and recycled lead management — all sourced from Salesforce via SF CLI.

**This is a Railway deployment, not localhost.** Pushing to GitHub (`main` branch) triggers a Railway redeploy.

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

## Railway Deployment
- **URL:** `https://web-production-980e0.up.railway.app`
- **Database:** PostgreSQL via SQLAlchemy (Railway managed)
- **Deploy trigger:** Push to `main` branch on GitHub
- **Ingest API:** `POST /api/ingest` with `X-API-Key` header — extraction scripts use this to push data
- **API Key:** `d219d2be8540f1d079dd896937fbd8fe41c9754ab955629cf74d43068e99d36d`
- **DB migrations:** App auto-runs `ALTER TABLE ADD COLUMN IF NOT EXISTS` on startup (no Alembic)

## Microsoft 365 ONLY — Google Workspace is fully decommissioned
**Shift4 is now an all-Microsoft shop.** The org migrated off Google Workspace on 2026-08-30 and Bryce confirmed on **2026-09-04** that Google is gone entirely: **email, calendar, meetings, and files are all Microsoft now.**

| Need | Use (Microsoft MCP `mcp__75618fea-...`) | NEVER use |
|---|---|---|
| Email read | `outlook_email_search` + `read_resource` on the `uri` | Gmail MCP (`search_threads`, `get_thread`) |
| Email draft | `outlook_create_draft`, `outlook_create_reply_draft` | Gmail `create_draft` / `list_drafts` |
| Calendar | `outlook_calendar_search` (+ `read_resource` on `calendar:///events/...`) | Google Calendar MCP (`mcp__0c1f58f7-...`) |
| Create/update events | `outlook_create_event`, `outlook_update_event` | Google Calendar MCP |
| Meetings | **Microsoft Teams** | Google Meet, Zoom |
| Files/docs | SharePoint / OneDrive (`sharepoint_search`, `read_resource` on `file:///...`) | Google Drive MCP |
| Chat | Teams (`teams_list_chats`) + Slack (still in use) | — |

**The Google Calendar MCP is dead.** Calling it returns a stale calendar and will make you miss real demos. Verified 2026-09-04: `outlook_calendar_search` returned a Hangar Pub demo on 9/9 that the Google calendar did not have.

`outlook_calendar_search` requires a `query` — pass `*` for everything. Its `start`/`end` come back as `{dateTime, timeZone}` wall-clock pairs; present them as-is, do NOT re-interpret `dateTime` as UTC.

- **Read mail** with the Outlook MCP (`mcp__75618fea-...`): `outlook_email_search` (+ `read_resource` on the returned `uri` for full bodies).
- **Create drafts** with `outlook_create_draft`, or `outlook_create_reply_draft` when following up on an existing thread so it threads correctly.
- **Never call the Gmail MCP** (`search_threads` / `get_thread` / `create_draft` / `list_drafts`). That mailbox is dead and returns stale, near-empty results — using it silently drops real customer replies and lead assignments.
- **No signature block in the body.** Shift4 runs a server side signature service that stamps on send. End with "Thanks," / "Bryce".

## Email Draft Generation Workflow
When Bryce asks to generate email drafts (queued on the dashboard or ad-hoc), use the **`gmail-draft-generator`** skill (`.claude/skills/gmail-draft-generator/`) — it covers the Railway draft queue, draft creation, queue clearing, and Bryce's style rules. **The skill's name is historical only: create drafts in Outlook via `outlook_create_draft`, never Gmail.**

## Commissions Refresh Workflow
When Bryce says "refresh commissions" (or similar), use the **`commissions-refresh`** skill (`.claude/skills/commissions-refresh/`) — it covers the Drive sheets, parsers, and Railway ingest.

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

## Daily / Weekly Activity Recap
When Bryce asks "what did I do today/this week", use the **`sf-activity-recap`** skill (`.claude/skills/sf-activity-recap/`) — it has the required SOQL sources, layout, and stage-exclusion rules. Always pull live from Salesforce, never from local JSON.

## Platform Notes
- **Mac (personal):** `python3` command, cron for scheduling, iMessage briefing via `run_extraction.sh`
- **Windows (work PC):** `python` command, Task Scheduler for scheduling, use `run_extraction.bat`, iMessage not available (briefing prints to console only)
- On Windows, SF CLI auth is in `%USERPROFILE%\.sf\` — no shell sourcing needed; it's available in any terminal session after `sf org login web --alias shift4`

## Sales Documents (in project root)
- `Farzad Method - Sales Script (Claude).docx` — Primary daily-use sales script
- `Templates - Copy & Paste (Claude).docx` — Email/SMS templates
- `Inside Sales Cadence & Templates (Claude).docx` — Full 10-day cadence
- `Objection Handler (Claude).docx`
- `Demo Script - Google Meet (Claude).docx` — filename is historical; demos now run on **Microsoft Teams**
- `SkyTab Proposal Template (Claude).docx`
