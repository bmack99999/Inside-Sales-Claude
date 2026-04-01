"""
SkyTab Inside Sales — Morning Salesforce Extraction
====================================================
Run by telling Claude Code: "Run morning Salesforce extraction"
Claude Code uses the Chrome browser MCP tools to navigate Salesforce,
read leads and opportunities, and write JSON files for the dashboard.

BEFORE RUNNING:
  - Confirm you are logged into Salesforce in Chrome
  - Salesforce Sidekick extension has been REMOVED (no longer needed)

CONFIRMED WORKING LIST VIEW URLs:
  - Leads:  filterName=My_Open_Leads         (14 leads)
  - Opps:   filterName=My_Open_Opportunities2  (5 open opps)
  NOTE: Do NOT use My_Open_Opportunities (wrong) or MyOpportunities (includes closed)

NAVIGATION NOTE:
  - In the list view, rows require double-click to navigate to a record
  - Contact phone numbers are on the Contact record (via Contact Roles), NOT on the Opportunity
  - After navigating to an opp, click on the Contact Role link to get the phone number

WHAT THIS SCRIPT DOES:
  1. Navigates to "My Open Leads" list view
  2. Scrolls through all leads and collects record IDs + surface data
  3. For hot leads (recent activity or task due), navigates to each
     record detail page and reads the full activity timeline + notes
  4. Navigates to "My Open Opportunities" list view
  5. For all open opportunities, navigates to each record detail page,
     reads activity timeline + notes, and clicks Contact Role to get phone
  6. Writes leads.json, opportunities.json, last_refresh.json to dashboard/data/
"""

import json
import os
from datetime import date, datetime

# ─── Config ───────────────────────────────────────────────────────────────────
SF_BASE        = "https://crmcredorax.lightning.force.com"
LEADS_LIST_URL = f"{SF_BASE}/lightning/o/Lead/list?filterName=My_Open_Leads"
OPPS_LIST_URL  = f"{SF_BASE}/lightning/o/Opportunity/list?filterName=My_Open_Opportunities2"

DASHBOARD_DATA = os.path.join(os.path.dirname(__file__), "dashboard", "data")

# ─── File helpers ─────────────────────────────────────────────────────────────

def save_json(filename, data):
    path = os.path.join(DASHBOARD_DATA, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  Saved {len(data) if isinstance(data, list) else 1} records → {path}")


# ─── STEP 1: Navigate to Leads List ──────────────────────────────────────────
"""
CLAUDE INSTRUCTIONS — Step 1:
  navigate(LEADS_LIST_URL)
  wait 3 seconds for page to load
  Take a screenshot to confirm the list loaded and CSS error is gone
  Expected: "My Open Leads | Leads | Salesforce" as page title
"""

def step1_navigate_leads_list():
    print("\n[Step 1] Navigating to My Open Leads list view...")
    print(f"  URL: {LEADS_LIST_URL}")
    # Claude: navigate to LEADS_LIST_URL, wait 3 seconds, confirm page title


# ─── STEP 2: Extract All Lead Record IDs from List View ──────────────────────
"""
CLAUDE INSTRUCTIONS — Step 2:
  The leads list uses virtual scrolling — only ~10 rows are rendered at a time.

  Do the following loop until all records are collected:
    a) read_page() → find all row elements in the grid
    b) For each row, extract:
         - Name (text of the name link)
         - Record URL (href of the name link, format: /lightning/r/Lead/[ID]/view)
         - Email, Phone, Company, Lead Status, Lead Source, Created Date
         from the gridcells in that row
    c) Add new records to the collection (skip duplicates by ID)
    d) Scroll down in the list table by pressing Page Down or scrolling
         via computer.scroll(direction="down") on the table area
    e) Wait 1 second for new rows to render
    f) Repeat until no new records are found after a scroll

  After collecting all record IDs, print: "Collected [N] lead records"
"""

def step2_collect_lead_records():
    print("\n[Step 2] Collecting lead records from list view...")
    leads = []
    # Claude: scroll through the list, collecting all rows
    # Return list of dicts with: id, name, email, phone, company,
    #   status, lead_source, created_date, sf_url
    return leads


# ─── STEP 3: Deep-Dive Hot Leads (Activity + Notes) ──────────────────────────
"""
CLAUDE INSTRUCTIONS — Step 3:
  For each lead collected in Step 2:

    Determine if it needs a "detail pass" (read full activity):
      - Lead Status is "Connected" or "Nurturing"  → YES
      - Lead Status is "Working" and has a recent created date (< 7 days)  → YES
      - Any lead with a task showing as overdue in the list  → YES
      - All others → NO (skip detail pass, keep surface data only)

  For leads that DO need a detail pass:
    a) navigate to the lead's SF URL (sf_url from Step 2)
    b) wait 3 seconds for the page to fully load
    c) get_page_text() — capture the full text of the record page
    d) read_page() — look specifically for:
         - The "Activity Timeline" or "Activity" section
           (look for elements containing "Call", "Email", "Task", "Note", "Log a call")
         - Recent activity entries: date, type (Call/Email/Task), subject, body/notes
         - Open tasks: task name + due date
         - Notes section: any pinned notes
    e) Extract and store:
         - last_activity_date: date of most recent activity
         - last_activity_type: Call / Email / Task / Note
         - last_call_notes: notes from the most recent call log (if present)
         - activity_summary: combined text of last 3-5 activity entries
         - next_task: description of the first open/upcoming task
         - next_task_due: due date of that task
         - next_agreed_step: look for phrases like "next step", "follow up", "agreed to"
         - open_tasks: list of all open task descriptions
         - pinned_notes: text from the Notes section if present
         - call_attempts: count of "Call" entries in the activity timeline

  For leads that DON'T need a detail pass:
    - Keep only the surface data from Step 2
    - Set last_call_notes, activity_summary, next_agreed_step to None
"""

def step3_enrich_hot_leads(leads):
    print(f"\n[Step 3] Enriching hot leads with activity detail...")
    enriched = []
    # Claude: for each lead, determine if detail pass needed, then navigate + extract
    return enriched


# ─── STEP 4: Navigate to Opportunities List ──────────────────────────────────
"""
CLAUDE INSTRUCTIONS — Step 4:
  navigate(OPPS_LIST_URL)  ← uses My_Open_Opportunities2, already filtered to open only
  wait 3 seconds
  This list shows ONLY open opportunities (already filtered — no need to skip closed ones).
  Confirmed: ~5 records as of March 2026.

  To navigate to a record from the list: double-click the row (single click only selects it).
  Or navigate directly by clicking the row once, then once more on the highlighted row.

  Columns to capture: Opportunity Name, Account Name, Close Date,
    Opportunity Record Type, Owner Full Name
  Also capture the record URL (href on the Opportunity Name link)
"""

def step4_navigate_opps_list():
    print("\n[Step 4] Navigating to My Opportunities list view...")
    print(f"  URL: {OPPS_LIST_URL}")
    opps = []
    # Claude: navigate, wait, scroll through collecting ONLY open (Closed=False) opps
    return opps


# ─── STEP 5: Deep-Dive All Open Opportunities ────────────────────────────────
"""
CLAUDE INSTRUCTIONS — Step 5:
  For EVERY open opportunity collected in Step 4 (all of them, not just hot ones):

    a) navigate to the opportunity's SF URL
    b) wait 3 seconds for page to load
    c) get_page_text() — capture the full record page text
    d) read_page() — extract from the record detail:

    From the record fields (left panel / detail section):
         - Account Name
         - Stage (opportunity stage: Conversations, Trending Positively, Proposal Sent, etc.)
         - Close Date, Probability %
         - Next Step field

    From the Contact Roles panel (right sidebar):
         - Click on the PRIMARY contact name link to open their contact record
         - On the contact record, read: Phone, Mobile, Email
         - Then navigate back to the opportunity (browser back button)
         NOTE: Phone is on the Contact record, NOT on the Opportunity record itself
         - Close Date
         - Probability %
         - Next Step (the "Next Step" field on the record)
         - Amount (if populated)
         - Record Type

    From the Activity Timeline (right panel):
         - last_activity_date: date of most recent activity
         - last_activity_type: Call / Email / Task
         - last_call_notes: notes from most recent call log
         - activity_summary: combined text of last 3-5 activity entries
         - next_task: first open task description
         - next_task_due: due date of that task
         - next_agreed_step: any noted next agreed action
         - open_tasks: all open tasks

  Note on Activity Timeline structure in SF Lightning:
    The timeline appears as a list of cards under "Activity" heading.
    Each card shows: [type icon] [date] [subject]
    Expanding a card (or reading its text) shows the notes/body.
    Look for text patterns like:
      "You logged a call" → last_activity_type = "Call"
      "Email: [subject]" → last_activity_type = "Email"
      "Task: [description]" → task
    The most recent entry is at the TOP of the timeline.
"""

def step5_enrich_opportunities(opps):
    print(f"\n[Step 5] Reading activity detail for {len(opps)} open opportunities...")
    enriched = []
    # Claude: navigate to each opp record, extract all detail
    return enriched


# ─── STEP 6: Write Output Files ───────────────────────────────────────────────
"""
CLAUDE INSTRUCTIONS — Step 6:
  After completing Steps 2-5, write the following JSON files.
  Each lead/opportunity should follow the schema below exactly so the
  dashboard's scoring function works correctly.

  LEAD SCHEMA:
  {
    "id": "[SF record ID from URL]",
    "type": "lead",
    "name": "[full name]",
    "company": "[company]",
    "phone": "[phone]",
    "email": "[email]",
    "status": "[Lead Status]",
    "lead_source": "[Lead Source]",
    "lead_age_days": [integer, days since created_date],
    "last_activity_date": "[YYYY-MM-DD or null]",
    "last_activity_type": "[Call/Email/Task/Note or null]",
    "next_task": "[task description or null]",
    "next_task_due": "[YYYY-MM-DD or null]",
    "call_attempts": [integer],
    "city": "[city or null]",
    "state": "[state or null]",
    "notes_snippet": "[short summary from notes, or null]",
    "last_call_notes": "[full notes from last call, or null]",
    "activity_summary": "[combined text of last few activities, or null]",
    "next_agreed_step": "[what was agreed on last contact, or null]",
    "open_tasks": ["task 1", "task 2"],
    "is_recycled": [true if Lead Source contains "Recycled", else false],
    "extracted_at": "[ISO timestamp]"
  }

  OPPORTUNITY SCHEMA:
  {
    "id": "[SF record ID from URL]",
    "type": "opportunity",
    "name": "[opportunity name]",
    "account_name": "[account/company name]",
    "contact_name": "[primary contact name or null]",
    "phone": "[contact phone or null]",
    "stage": "[Stage]",
    "amount": [number or 0],
    "close_date": "[YYYY-MM-DD or null]",
    "last_activity_date": "[YYYY-MM-DD or null]",
    "last_activity_type": "[Call/Email/Task or null]",
    "next_step": "[Next Step field from SF or null]",
    "next_task_due": "[YYYY-MM-DD or null]",
    "days_in_stage": [integer],
    "probability": [integer 0-100],
    "notes_snippet": "[short notes summary or null]",
    "last_call_notes": "[full notes from last call or null]",
    "activity_summary": "[combined text of last few activities or null]",
    "next_agreed_step": "[next agreed action or null]",
    "open_tasks": ["task 1", "task 2"],
    "extracted_at": "[ISO timestamp]"
  }

  Call save_json() to write:
    - dashboard/data/leads.json
    - dashboard/data/opportunities.json
    - dashboard/data/last_refresh.json  (with refreshed_at, lead_count, opp_count)
"""

def step6_write_output(leads, opps):
    print("\n[Step 6] Writing JSON output files...")
    now = datetime.now().isoformat()

    for lead in leads:
        lead['extracted_at'] = now
        lead.setdefault('type', 'lead')

    for opp in opps:
        opp['extracted_at'] = now
        opp.setdefault('type', 'opportunity')

    save_json("leads.json", leads)
    save_json("opportunities.json", opps)
    save_json("last_refresh.json", {
        "refreshed_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
        "lead_count": len(leads),
        "opp_count": len(opps),
        "detail_pass_leads": sum(1 for l in leads if l.get("last_call_notes")),
        "detail_pass_opps": len(opps),
    })


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  SkyTab Inside Sales — Morning Salesforce Extraction")
    print(f"  {date.today().strftime('%A, %B %d, %Y')}")
    print("=" * 60)
    print("\nPRE-FLIGHT CHECK:")
    print("  [ ] You are logged into Salesforce in Chrome")
    print("  [ ] Note: Salesforce Sidekick has been removed — no action needed")
    print()

    step1_navigate_leads_list()
    leads = step2_collect_lead_records()
    leads = step3_enrich_hot_leads(leads)

    opps = step4_navigate_opps_list()
    opps = step5_enrich_opportunities(opps)

    step6_write_output(leads, opps)

    print("\n" + "=" * 60)
    print(f"  Extraction complete.")
    print(f"  {len(leads)} leads | {len(opps)} open opportunities")
    print(f"  Open http://localhost:5000 to see your daily plan.")
    print("=" * 60)

    print("\nExtraction complete — open http://localhost:5000 for your daily plan.")


if __name__ == "__main__":
    main()
