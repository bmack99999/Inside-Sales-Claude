"""
SkyTab Inside Sales — Salesforce Extraction via SF CLI
======================================================
Pulls Open Leads (Qualified + Working) and Open Opportunities
directly from Salesforce using SOQL queries via the SF CLI.

Usage:
  python3 extract_salesforce.py
  Or hit "Refresh" on the dashboard (http://localhost:5000)

Prerequisites:
  - SF CLI installed: brew install sf
  - Authenticated: sf org login web --alias shift4
"""

import json
import os
import subprocess
from datetime import date, datetime

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

import requests


# ─── Config ───────────────────────────────────────────────────────────────────
SF_ALIAS       = "shift4"
USER_ID        = "005Pd0000084UhFIAU"  # Bryce Mack
DASHBOARD_DATA = os.path.join(os.path.dirname(__file__), "dashboard", "data")
DASHBOARD_URL  = os.environ.get("DASHBOARD_URL", "http://localhost:5000")
INGEST_API_KEY = os.environ.get("INGEST_API_KEY", "dev-ingest-key")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def run_soql(query):
    """Run a SOQL query via SF CLI and return records."""
    result = subprocess.run(
        ["sf", "data", "query", "--query", query,
         "--target-org", SF_ALIAS, "--json"],
        capture_output=True, text=True, timeout=60
    )
    data = json.loads(result.stdout)
    if data.get("status") != 0:
        raise RuntimeError(f"SOQL error: {data}")
    return data["result"]["records"]


def save_json(filename, data):
    path = os.path.join(DASHBOARD_DATA, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    count = len(data) if isinstance(data, list) else 1
    print(f"  Saved {count} records → {path}")


def days_between(date_str):
    """Days between a date string and today. Returns None if date_str is None."""
    if not date_str:
        return None
    d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    return (date.today() - d).days


# ─── Extract Leads ───────────────────────────────────────────────────────────

def extract_leads():
    print("\n[1/4] Querying open leads...")
    leads_raw = run_soql(
        "SELECT Id, Name, FirstName, LastName, Company, Phone, Email, "
        "Status, LeadSource, CreatedDate, Lead_Created_Date__c, "
        "LastActivityDate, City, State, HasOptedOutOfEmail "
        f"FROM Lead WHERE OwnerId = '{USER_ID}' "
        "AND IsConverted = false "
        "AND (Status = 'Qualified' OR Status = 'Working')"
    )
    print(f"  Found {len(leads_raw)} open leads")

    # Get open tasks for these leads
    print("\n[2/4] Querying lead tasks...")
    lead_ids = [l["Id"] for l in leads_raw]
    tasks_by_lead = get_tasks_for_records(lead_ids, "WhoId")

    # Get recent activities (completed tasks = call logs, emails, etc.)
    activities_by_lead = get_recent_activities(lead_ids, "WhoId")

    now = datetime.now().isoformat()
    leads = []
    for r in leads_raw:
        lid = r["Id"]
        open_tasks = tasks_by_lead.get(lid, [])
        activities = activities_by_lead.get(lid, [])

        # Find next task due
        next_task = None
        next_task_due = None
        if open_tasks:
            soonest = sorted(open_tasks, key=lambda t: t.get("ActivityDate") or "9999")
            next_task = soonest[0].get("Subject")
            next_task_due = soonest[0].get("ActivityDate")

        # Count call attempts from activities
        call_attempts = sum(1 for a in activities if a.get("TaskSubtype") == "Call"
                           or (a.get("Subject") or "").lower().startswith("call"))

        # Build activity summary from last 3 activities
        activity_summary = None
        if activities:
            summaries = []
            for a in activities[:3]:
                adate = (a.get("ActivityDate") or "")[:10]
                subj = a.get("Subject") or ""
                summaries.append(f"{adate}: {subj}")
            activity_summary = " | ".join(summaries)

        # Last call notes
        last_call_notes = None
        for a in activities:
            if a.get("TaskSubtype") == "Call" or (a.get("Subject") or "").lower().startswith("call"):
                last_call_notes = a.get("Description")
                break

        created = r.get("Lead_Created_Date__c") or r.get("CreatedDate")
        lead_age = days_between(created) if created else None

        leads.append({
            "id": lid,
            "type": "lead",
            "name": r.get("Name"),
            "company": r.get("Company"),
            "phone": r.get("Phone"),
            "email": r.get("Email"),
            "status": r.get("Status"),
            "lead_source": r.get("LeadSource"),
            "lead_age_days": lead_age,
            "last_activity_date": r.get("LastActivityDate"),
            "last_activity_type": get_last_activity_type(activities),
            "next_task": next_task,
            "next_task_due": next_task_due,
            "call_attempts": call_attempts,
            "city": r.get("City"),
            "state": r.get("State"),
            "notes_snippet": (last_call_notes or "")[:100] or None,
            "last_call_notes": last_call_notes,
            "activity_summary": activity_summary,
            "next_agreed_step": None,
            "open_tasks": [t.get("Subject") for t in open_tasks],
            "is_recycled": False,
            "extracted_at": now,
        })

    return leads


# ─── Extract Opportunities ───────────────────────────────────────────────────

def extract_opportunities():
    print("\n[3/4] Querying open opportunities...")
    opps_raw = run_soql(
        "SELECT Id, Name, StageName, CloseDate, LastActivityDate, "
        "HasOverdueTask, ContactId, Probability, "
        "LastStageChangeDate, Description, Next_service_provider_task__c "
        f"FROM Opportunity WHERE OwnerId = '{USER_ID}' "
        "AND IsClosed = false"
    )
    print(f"  Found {len(opps_raw)} open opportunities")

    # Get contact phone numbers
    contact_ids = [o["ContactId"] for o in opps_raw if o.get("ContactId")]
    contacts = {}
    if contact_ids:
        id_list = "','".join(contact_ids)
        contact_records = run_soql(
            f"SELECT Id, Name, Phone, MobilePhone, Email "
            f"FROM Contact WHERE Id IN ('{id_list}')"
        )
        for c in contact_records:
            contacts[c["Id"]] = c

    # Get account names
    opp_ids = [o["Id"] for o in opps_raw]
    account_map = {}
    if opps_raw:
        id_list = "','".join(opp_ids)
        acct_records = run_soql(
            f"SELECT Id, Account.Name FROM Opportunity WHERE Id IN ('{id_list}')"
        )
        for a in acct_records:
            acct = a.get("Account")
            if acct:
                account_map[a["Id"]] = acct.get("Name")

    # Get tasks and activities
    print("\n[4/4] Querying opportunity tasks...")
    tasks_by_opp = get_tasks_for_records(opp_ids, "WhatId")
    activities_by_opp = get_recent_activities(opp_ids, "WhatId")

    now = datetime.now().isoformat()
    opps = []
    for r in opps_raw:
        oid = r["Id"]
        contact = contacts.get(r.get("ContactId"), {})
        open_tasks = tasks_by_opp.get(oid, [])
        activities = activities_by_opp.get(oid, [])

        next_task = None
        next_task_due = None
        if open_tasks:
            soonest = sorted(open_tasks, key=lambda t: t.get("ActivityDate") or "9999")
            next_task = soonest[0].get("Subject")
            next_task_due = soonest[0].get("ActivityDate")

        activity_summary = None
        if activities:
            summaries = []
            for a in activities[:3]:
                adate = (a.get("ActivityDate") or "")[:10]
                subj = a.get("Subject") or ""
                summaries.append(f"{adate}: {subj}")
            activity_summary = " | ".join(summaries)

        last_call_notes = None
        for a in activities:
            if a.get("TaskSubtype") == "Call" or (a.get("Subject") or "").lower().startswith("call"):
                last_call_notes = a.get("Description")
                break

        days_in_stage = days_between(r.get("LastStageChangeDate")) or 0

        opps.append({
            "id": oid,
            "type": "opportunity",
            "name": r.get("Name"),
            "account_name": account_map.get(oid),
            "contact_name": contact.get("Name"),
            "phone": contact.get("Phone") or contact.get("MobilePhone"),
            "stage": r.get("StageName"),
            "amount": 0,
            "close_date": r.get("CloseDate"),
            "last_activity_date": r.get("LastActivityDate"),
            "last_activity_type": get_last_activity_type(activities),
            "next_step": r.get("Description"),
            "next_task_due": next_task_due,
            "days_in_stage": days_in_stage,
            "probability": r.get("Probability") or 0,
            "notes_snippet": (last_call_notes or "")[:100] or None,
            "last_call_notes": last_call_notes,
            "activity_summary": activity_summary,
            "next_agreed_step": None,
            "open_tasks": [t.get("Subject") for t in open_tasks],
            "extracted_at": now,
        })

    return opps


# ─── Task / Activity Helpers ─────────────────────────────────────────────────

def get_tasks_for_records(record_ids, id_field):
    """Get open tasks grouped by parent record ID."""
    if not record_ids:
        return {}
    id_list = "','".join(record_ids)
    tasks = run_soql(
        f"SELECT Id, {id_field}, Subject, ActivityDate, Status, Priority "
        f"FROM Task WHERE {id_field} IN ('{id_list}') "
        "AND IsClosed = false "
        "ORDER BY ActivityDate ASC"
    )
    grouped = {}
    for t in tasks:
        parent = t.get(id_field)
        if parent:
            grouped.setdefault(parent, []).append(t)
    return grouped


def get_recent_activities(record_ids, id_field):
    """Get recent completed tasks (activities) grouped by parent record ID."""
    if not record_ids:
        return {}
    id_list = "','".join(record_ids)
    activities = run_soql(
        f"SELECT Id, {id_field}, Subject, ActivityDate, Description, "
        f"TaskSubtype, CallType, Status "
        f"FROM Task WHERE {id_field} IN ('{id_list}') "
        "AND IsClosed = true "
        "ORDER BY ActivityDate DESC "
        "LIMIT 200"
    )
    grouped = {}
    for a in activities:
        parent = a.get(id_field)
        if parent:
            grouped.setdefault(parent, []).append(a)
    return grouped


def get_last_activity_type(activities):
    """Determine the type of the most recent activity."""
    if not activities:
        return None
    a = activities[0]
    if a.get("TaskSubtype") == "Call" or (a.get("Subject") or "").lower().startswith("call"):
        return "Call"
    if a.get("TaskSubtype") == "Email" or (a.get("Subject") or "").lower().startswith("email"):
        return "Email"
    return "Task"


# ─── Extract Tasks ───────────────────────────────────────────────────────────

def extract_tasks():
    """Pull today's completed tasks + upcoming scheduled tasks for the KPI page."""
    today_str = date.today().isoformat()

    print("\n[5/5] Querying Salesforce tasks...")

    # Completed tasks with ActivityDate = today
    completed_raw = run_soql(
        f"SELECT Id, Subject, Description, ActivityDate, Status, TaskSubtype, "
        f"Who.Name, What.Name "
        f"FROM Task "
        f"WHERE OwnerId = '{USER_ID}' "
        f"AND ActivityDate = TODAY "
        f"AND IsClosed = true "
        f"ORDER BY LastModifiedDate DESC "
        f"LIMIT 200"
    )

    # Open/scheduled tasks from today onward
    scheduled_raw = run_soql(
        f"SELECT Id, Subject, Description, ActivityDate, Status, Priority, "
        f"Who.Name, What.Name "
        f"FROM Task "
        f"WHERE OwnerId = '{USER_ID}' "
        f"AND IsClosed = false "
        f"AND ActivityDate >= TODAY "
        f"ORDER BY ActivityDate ASC "
        f"LIMIT 100"
    )

    def fmt(t):
        who  = t.get('Who')  or {}
        what = t.get('What') or {}
        return {
            'id':           t.get('Id'),
            'subject':      t.get('Subject') or '',
            'description':  (t.get('Description') or '')[:200],
            'activity_date': t.get('ActivityDate') or '',
            'status':       t.get('Status') or '',
            'task_subtype': t.get('TaskSubtype') or '',
            'priority':     t.get('Priority') or '',
            'who_name':     who.get('Name', '')  if isinstance(who,  dict) else '',
            'what_name':    what.get('Name', '') if isinstance(what, dict) else '',
        }

    completed = [fmt(t) for t in completed_raw]
    scheduled = [fmt(t) for t in scheduled_raw]

    print(f"  Completed today:    {len(completed)}")
    print(f"  Upcoming scheduled: {len(scheduled)}")

    return {
        'refreshed_at': datetime.now().strftime('%Y-%m-%d %I:%M %p'),
        'date':         today_str,
        'completed':    completed,
        'scheduled':    scheduled,
    }


# ─── Post to Dashboard ───────────────────────────────────────────────────────

def post_to_dashboard(leads, opps, refresh_info):
    """POST extracted data to the dashboard's ingest endpoint."""
    url = f"{DASHBOARD_URL}/api/ingest"
    payload = {
        "type": "salesforce",
        "leads": leads,
        "opps": opps,
        "refresh_info": refresh_info,
    }
    try:
        resp = requests.post(url, json=payload,
                             headers={"X-API-Key": INGEST_API_KEY},
                             timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            print(f"  Dashboard updated: {result.get('leads')} leads, {result.get('opps')} opps")
            return True
        else:
            print(f"  Dashboard POST failed: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  Dashboard POST error: {e}")
        return False


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  SkyTab Inside Sales — Salesforce Extraction (SF CLI)")
    print(f"  {date.today().strftime('%A, %B %d, %Y')}")
    print("=" * 60)

    leads = extract_leads()
    opps  = extract_opportunities()
    tasks = extract_tasks()

    refresh_info = {
        "refreshed_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
        "lead_count": len(leads),
        "opp_count": len(opps),
        "detail_pass_leads": sum(1 for l in leads if l.get("last_call_notes")),
        "detail_pass_opps": len(opps),
    }

    print(f"\n[Posting to dashboard at {DASHBOARD_URL}...]")
    success = post_to_dashboard(leads, opps, refresh_info)

    # Post tasks separately
    try:
        resp = requests.post(
            f"{DASHBOARD_URL}/api/ingest",
            json={"type": "tasks", "tasks": tasks},
            headers={"X-API-Key": INGEST_API_KEY},
            timeout=30,
        )
        if resp.status_code == 200:
            print(f"  Tasks updated: {len(tasks.get('completed', []))} completed, "
                  f"{len(tasks.get('scheduled', []))} scheduled")
        else:
            print(f"  Tasks POST failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"  Tasks POST error: {e}")

    # Always write local JSON files as backup
    print("[Writing local backup files...]")
    save_json("leads.json", leads)
    save_json("opportunities.json", opps)
    save_json("last_refresh.json", refresh_info)
    save_json("sf_tasks.json", tasks)

    print("\n" + "=" * 60)
    print(f"  Extraction complete.")
    print(f"  {len(leads)} leads | {len(opps)} open opportunities")
    if success:
        print(f"  Dashboard updated at {DASHBOARD_URL}")
    else:
        print(f"  Warning: dashboard POST failed — local files written as backup")
    print("=" * 60)


if __name__ == "__main__":
    main()
