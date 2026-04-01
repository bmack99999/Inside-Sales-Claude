---
name: Inside Sales Dashboard
description: Local Flask dashboard built for Bryce's daily lead prioritization and KPI tracking
type: project
---

A locally hosted Flask web app lives at `C:/Users/bmack/Desktop/Inside Sales/dashboard/`.

**To start:** Double-click `start_dashboard.bat` or run `py -m flask --app app.py run` from the dashboard/ folder. Access at `http://localhost:5000`.

**Key files:**
- `dashboard/app.py` — Flask routes + priority scoring logic
- `dashboard/templates/dashboard.html` — Daily hot list view
- `dashboard/templates/pipeline.html` — Pipeline board by stage
- `dashboard/templates/kpis.html` — KPI charts + activity log
- `dashboard/static/style.css` — Navy/white theme
- `dashboard/static/charts.js` — Chart.js KPI charts
- `dashboard/data/leads.json` — Lead data (refreshed by morning extraction)
- `dashboard/data/opportunities.json` — Opportunity data
- `dashboard/data/kpi_log.json` — Daily KPI log (appended, never overwritten)
- `extract_salesforce.py` — Morning SF extraction workflow script

**Priority scoring:** Records scored 0-100 based on recency, task due date, status, and momentum. Tiers: HOT (70+), WARM (40-69), COOL (10-39), COLD (<10).

**Morning workflow:**
1. Make sure you're logged into Salesforce in Chrome (Sidekick extension removed — no other pre-flight steps needed)
2. Tell Claude Code: "Run morning Salesforce extraction"
3. Claude navigates SF, extracts data, writes JSON files
4. Open http://localhost:5000 for daily plan

**Data state as of 3/30/2026:** 14 leads + 5 opps loaded with real SF data. 7 leads still have placeholder IDs (sf-lead-*) — next morning extraction will replace with real SF IDs.

**Why:** Built to replace manual SF browsing with a prioritized daily call list + KPI tracking system.
