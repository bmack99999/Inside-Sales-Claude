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
        "AND (Status = 'New' OR Status = 'Qualified' OR Status = 'Working')"
    )
    print(f"  Found {len(leads_raw)} open leads")

    # Get open tasks for these leads
    print("\n[2/4] Querying lead tasks...")
    lead_ids = [l["Id"] for l in leads_raw]
    tasks_by_lead = get_tasks_for_records(lead_ids, "WhoId")

    # Get recent activities (completed tasks = call logs, emails, etc.)
    activities_by_lead = get_recent_activities(lead_ids, "WhoId")

    # Get ContentNotes for leads
    content_notes_by_lead = get_content_notes(lead_ids)

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
        # Fall back to any activity description if no call notes
        if not last_call_notes:
            for a in activities:
                if a.get("Description"):
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
            "notes_snippet": (last_call_notes or content_notes_by_lead.get(lid) or "")[:100] or None,
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
        "SELECT Id, Name, StageName, CloseDate, CreatedDate, LastActivityDate, "
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

    # Get ContentNotes for opps
    content_notes_by_opp = get_content_notes(opp_ids)

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
        # Fall back to any activity description if no call notes
        if not last_call_notes:
            for a in activities:
                if a.get("Description"):
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
            "email": contact.get("Email"),
            "stage": r.get("StageName"),
            "amount": 0,
            "created_date": (r.get("CreatedDate") or "")[:10] or None,
            "close_date": r.get("CloseDate"),
            "last_activity_date": r.get("LastActivityDate"),
            "last_activity_type": get_last_activity_type(activities),
            "next_step": r.get("Description"),
            "next_task_due": next_task_due,
            "days_in_stage": days_in_stage,
            "probability": r.get("Probability") or 0,
            "notes_snippet": (last_call_notes or content_notes_by_opp.get(oid) or r.get("Description") or "")[:100] or None,
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


def get_content_notes(record_ids):
    """Get most recent ContentNote TextPreview grouped by record ID."""
    if not record_ids:
        return {}
    batch_size = 200
    notes_by_id = {}
    for i in range(0, len(record_ids), batch_size):
        batch = record_ids[i:i + batch_size]
        id_list = "','".join(batch)
        doc_links = run_soql(
            f"SELECT ContentDocumentId, LinkedEntityId "
            f"FROM ContentDocumentLink WHERE LinkedEntityId IN ('{id_list}')"
        )
        if not doc_links:
            continue
        doc_ids = list(set(d['ContentDocumentId'] for d in doc_links))
        entity_map = {d['ContentDocumentId']: d['LinkedEntityId'] for d in doc_links}
        for j in range(0, len(doc_ids), batch_size):
            doc_batch = doc_ids[j:j + batch_size]
            doc_id_list = "','".join(doc_batch)
            notes = run_soql(
                f"SELECT Id, TextPreview, CreatedDate "
                f"FROM ContentNote WHERE Id IN ('{doc_id_list}') "
                f"ORDER BY CreatedDate DESC"
            )
            for note in notes:
                eid = entity_map.get(note['Id'])
                if eid and eid not in notes_by_id and note.get('TextPreview'):
                    notes_by_id[eid] = note['TextPreview']
    return notes_by_id


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


# ─── Boss Metrics ────────────────────────────────────────────────────────────

def extract_boss_metrics():
    """Pull leads received, converted to opp, and closed won for boss reporting."""
    today       = date.today()
    month_start = today.replace(day=1).isoformat()
    START_DATE  = '2026-03-23'   # Bryce's first day at Shift4

    print("\n[Metrics] Querying boss metrics...")

    # All leads created since start date
    leads_raw = run_soql(
        f"SELECT Id, Lead_Created_Date__c, Status "
        f"FROM Lead WHERE OwnerId = '{USER_ID}' "
        f"AND Lead_Created_Date__c >= {START_DATE}"
    )

    # Leads converted to opp since start date
    converted_raw = run_soql(
        f"SELECT Id, ConvertedDate "
        f"FROM Lead WHERE OwnerId = '{USER_ID}' "
        f"AND IsConverted = true "
        f"AND ConvertedDate >= {START_DATE}"
    )

    # Closed Won opportunities since start date
    closed_won_raw = run_soql(
        f"SELECT Id, CloseDate, Name "
        f"FROM Opportunity WHERE OwnerId = '{USER_ID}' "
        f"AND (StageName = 'Closed Won' OR StageName = 'Underwriting Review') "
        f"AND CloseDate >= {START_DATE}"
    )

    def period_counts(leads, converted, closed, since):
        l  = [r for r in leads     if (r.get('Lead_Created_Date__c') or '') >= since]
        c  = [r for r in converted if (r.get('ConvertedDate')         or '') >= since]
        w  = [r for r in closed    if (r.get('CloseDate')             or '') >= since]
        uq = [r for r in l if (r.get('Status') or '').lower() == 'unqualified']
        tl = len(l)
        tc = len(c)
        tw = len(w)
        tu = len(uq)
        workable = tl - tu   # leads that weren't immediately unqualified
        return {
            'total_leads':               tl,
            'converted_opps':            tc,
            'closed_won':                tw,
            'unqualified':               tu,
            'workable_leads':            workable,
            'unqualified_rate':          round(tu / tl * 100, 1) if tl else 0,
            'conv_rate':                 round(tc / tl * 100, 1) if tl else 0,
            'conv_rate_workable':        round(tc / workable * 100, 1) if workable else 0,
            'close_rate_vs_leads':       round(tw / tl * 100, 1) if tl else 0,
            'close_rate_vs_workable':    round(tw / workable * 100, 1) if workable else 0,
            'close_rate_vs_opps':        round(tw / tc * 100, 1) if tc else 0,
        }

    # Monthly breakdown for chart (last 12 months)
    monthly = {}
    for r in leads_raw:
        m = (r.get('Lead_Created_Date__c') or '')[:7]  # YYYY-MM
        if m:
            monthly.setdefault(m, {'leads': 0, 'unqualified': 0, 'converted': 0, 'closed_won': 0})
            monthly[m]['leads'] += 1
            if (r.get('Status') or '').lower() == 'unqualified':
                monthly[m]['unqualified'] += 1
    for r in converted_raw:
        m = (r.get('ConvertedDate') or '')[:7]
        if m: monthly.setdefault(m, {'leads': 0, 'converted': 0, 'closed_won': 0})['converted'] += 1
    for r in closed_won_raw:
        m = (r.get('CloseDate') or '')[:7]
        if m: monthly.setdefault(m, {'leads': 0, 'converted': 0, 'closed_won': 0})['closed_won'] += 1

    monthly_list = [{'month': k, **v} for k, v in sorted(monthly.items())]

    result = {
        'refreshed_at': datetime.now().strftime('%Y-%m-%d %I:%M %p'),
        'start_date':   START_DATE,
        'mtd':   period_counts(leads_raw, converted_raw, closed_won_raw, month_start),
        'ytd':   period_counts(leads_raw, converted_raw, closed_won_raw, START_DATE),
        'monthly': monthly_list,
    }
    print(f"  MTD  → leads: {result['mtd']['total_leads']}, "
          f"opps: {result['mtd']['converted_opps']}, "
          f"closed won: {result['mtd']['closed_won']}")
    print(f"  Since start → leads: {result['ytd']['total_leads']}, "
          f"opps: {result['ytd']['converted_opps']}, "
          f"closed won: {result['ytd']['closed_won']}")
    return result


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

    # Notes created today (ContentNote on any lead or opp)
    notes_today_raw = run_soql(
        f"SELECT Id FROM ContentNote "
        f"WHERE CreatedById = '{USER_ID}' "
        f"AND CreatedDate = TODAY "
        f"LIMIT 2000"
    )

    # Weekly completed count — Monday of current week through today (tasks + notes)
    from datetime import timedelta
    today = date.today()
    monday = today - timedelta(days=today.weekday())  # weekday(): Mon=0 … Sun=6
    weekly_tasks_raw = run_soql(
        f"SELECT Id "
        f"FROM Task "
        f"WHERE OwnerId = '{USER_ID}' "
        f"AND IsClosed = true "
        f"AND ActivityDate >= {monday.isoformat()} "
        f"AND ActivityDate <= TODAY "
        f"LIMIT 2000"
    )
    weekly_notes_raw = run_soql(
        f"SELECT Id FROM ContentNote "
        f"WHERE CreatedById = '{USER_ID}' "
        f"AND CreatedDate = THIS_WEEK "
        f"LIMIT 2000"
    )
    weekly_count = len(weekly_tasks_raw) + len(weekly_notes_raw)

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

    completed  = [fmt(t) for t in completed_raw]
    scheduled  = [fmt(t) for t in scheduled_raw]
    daily_count = len(completed) + len(notes_today_raw)

    print(f"  Completed today:    {daily_count} ({len(completed)} tasks + {len(notes_today_raw)} notes)")
    print(f"  Completed this week:{weekly_count} ({len(weekly_tasks_raw)} tasks + {len(weekly_notes_raw)} notes)")
    print(f"  Upcoming scheduled: {len(scheduled)}")

    return {
        'refreshed_at':  datetime.now().strftime('%Y-%m-%d %I:%M %p'),
        'date':          today_str,
        'completed':     completed,
        'scheduled':     scheduled,
        'daily_count':   daily_count,
        'weekly_count':  weekly_count,
        'week_start':    monday.isoformat(),
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

    leads   = extract_leads()
    opps    = extract_opportunities()
    tasks   = extract_tasks()
    metrics = extract_boss_metrics()

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

    # Post boss metrics
    try:
        resp = requests.post(
            f"{DASHBOARD_URL}/api/ingest",
            json={"type": "metrics", "metrics": metrics},
            headers={"X-API-Key": INGEST_API_KEY},
            timeout=30,
        )
        if resp.status_code == 200:
            print(f"  Boss metrics updated.")
        else:
            print(f"  Metrics POST failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"  Metrics POST error: {e}")
    save_json("boss_metrics.json", metrics)

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
