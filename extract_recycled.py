"""
SkyTab Inside Sales — Recycled Leads Extraction via SF CLI
==========================================================
Scans the recycled leads pool from Salesforce, reads activity notes,
and categorizes leads by contact status.

Usage:
  python3 extract_recycled.py
  Or hit "Scan Recycled" on the dashboard recycled page
"""

import json
import os
import subprocess
from collections import defaultdict
from datetime import date, datetime

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

import requests


# ─── Config ───────────────────────────────────────────────────────────────────
SF_ALIAS       = "shift4"
DASHBOARD_DATA = os.path.join(os.path.dirname(__file__), "dashboard", "data")
DASHBOARD_URL  = os.environ.get("DASHBOARD_URL", "http://localhost:5000")
INGEST_API_KEY = os.environ.get("INGEST_API_KEY", "dev-ingest-key")

# Matches the Recycled Leads Report filters
LEAD_SOURCES = (
    "Google", "Meta", "MVF", "Natural Intelligence",
    "Paid Ad", "Possibly", "Ryze", "SPO Tower", "TFLI"
)

NO_CONTACT_PATTERNS = [
    'na', 'n/a', 'no answer', 'no response', 'vm', 'voicemail',
    'vm full', 'mailbox full', 'left message', 'left vm', 'no pick',
    "didn't answer", 'not available', 'unreachable', 'unresponsive',
    'text', 'sms', 'sent email', 'sent text', 'no answer/sms',
    'straight to voicemail', 'disconnected',
]

CONVERSATION_SIGNALS = [
    'spoke', 'talked', 'discussed', 'interested', 'wants',
    'agreed', 'scheduled', 'demo', 'meeting', 'callback',
    'will call back', 'owner', 'manager', 'decision maker',
    'pricing', 'quote', 'proposal', 'current system',
    'currently using', 'contract', 'happy with', 'not interested',
    'already has', 'under contract', 'switching', 'considering',
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def run_soql(query):
    result = subprocess.run(
        ["sf", "data", "query", "--query", query,
         "--target-org", SF_ALIAS, "--json"],
        capture_output=True, text=True, timeout=120
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


def is_no_contact(task):
    subj = (task.get('Subject') or '').lower().strip()
    desc = (task.get('Description') or '').lower().strip()
    combined = f"{subj} {desc}"

    if any(p in combined for p in NO_CONTACT_PATTERNS):
        return True
    if subj in ('call', 'call1', 'call2', 'call3', 'called',
                'fu call', 'follow up', 'outreach 1', 'outreach'):
        if not desc or desc in ('na', 'n/a', '', 'no answer', 'no preview'):
            return True
    return False


def had_real_conversation(task):
    desc = (task.get('Description') or '').lower()
    subj = (task.get('Subject') or '').lower()
    combined = f"{subj} {desc}"
    return any(s in combined for s in CONVERSATION_SIGNALS)


# ─── Main Extraction ─────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Recycled Leads Scan (SF CLI)")
    print(f"  {date.today().strftime('%A, %B %d, %Y')}")
    print("=" * 60)

    # Step 1: Pull all recycled leads
    print("\n[1/3] Querying recycled leads...")
    source_list = "','".join(LEAD_SOURCES)
    leads_raw = run_soql(
        f"SELECT Id, Name, Company, Phone, Email, Status, LeadSource, "
        f"Lead_Created_Date__c, LastActivityDate, IsConverted, ConvertedOpportunityId "
        f"FROM Lead "
        f"WHERE LeadSource IN ('{source_list}') "
        f"AND Lead_Created_Date__c >= 2025-01-01 "
        f"AND CountryCode = 'US' "
        f"AND Email != 'steve.maraschiello@shift4.com' "
        f"AND Status != 'Unqualified' "
        f"AND Lead_Created_Date__c < LAST_N_DAYS:4 "
        f"AND Loss_Reason__c NOT IN ('Duplicate lead','Incorrect contact information','Not Applicable','Spam')"
    )
    print(f"  Found {len(leads_raw)} recycled leads")

    # Filter out converted leads whose opportunity is Closed Lost
    converted_opp_ids = [l['ConvertedOpportunityId'] for l in leads_raw
                         if l.get('IsConverted') and l.get('ConvertedOpportunityId')]

    excluded_opp_ids = set()
    if converted_opp_ids:
        print("  Checking converted opportunity stages...")
        batch_size_opps = 200
        for i in range(0, len(converted_opp_ids), batch_size_opps):
            batch = converted_opp_ids[i:i + batch_size_opps]
            id_list = "','".join(batch)
            opps = run_soql(
                f"SELECT Id, StageName FROM Opportunity "
                f"WHERE Id IN ('{id_list}') AND StageName IN ('Closed Lost', 'Closed Won')"
            )
            excluded_opp_ids.update(o['Id'] for o in opps)
        print(f"  Excluding {len(excluded_opp_ids)} Closed Lost/Won opportunities")

    leads_raw = [l for l in leads_raw
                 if l.get('ConvertedOpportunityId') not in excluded_opp_ids]
    print(f"  {len(leads_raw)} leads after exclusions")

    lead_ids = [l['Id'] for l in leads_raw]

    # Build map of lead -> opp ID for converted leads
    lead_to_opp = {l['Id']: l['ConvertedOpportunityId']
                   for l in leads_raw
                   if l.get('IsConverted') and l.get('ConvertedOpportunityId')}
    opp_to_lead = {v: k for k, v in lead_to_opp.items()}
    active_opp_ids = list(lead_to_opp.values())

    # Step 2: Pull all tasks in batches (leads + opp activities)
    print("\n[2/3] Querying activities (batched)...")
    all_tasks = []
    batch_size = 200

    # 2a: Tasks on lead records (WhoId)
    for i in range(0, len(lead_ids), batch_size):
        batch = lead_ids[i:i + batch_size]
        id_list = "','".join(batch)
        tasks = run_soql(
            f"SELECT Id, WhoId, WhatId, Subject, Description, ActivityDate, TaskSubtype, Status "
            f"FROM Task WHERE WhoId IN ('{id_list}') "
            f"ORDER BY ActivityDate DESC"
        )
        all_tasks.extend(tasks)
        batch_num = i // batch_size + 1
        total_batches = (len(lead_ids) + batch_size - 1) // batch_size
        print(f"  Lead batch {batch_num}/{total_batches}: {len(batch)} leads → {len(tasks)} tasks")

    # 2b: Tasks on converted opportunity records (WhatId)
    opp_tasks_by_lead = defaultdict(list)
    if active_opp_ids:
        print(f"  Pulling activities from {len(active_opp_ids)} converted opportunities...")
        for i in range(0, len(active_opp_ids), batch_size):
            batch = active_opp_ids[i:i + batch_size]
            id_list = "','".join(batch)
            opp_tasks = run_soql(
                f"SELECT Id, WhatId, Subject, Description, ActivityDate, TaskSubtype, Status "
                f"FROM Task WHERE WhatId IN ('{id_list}') "
                f"ORDER BY ActivityDate DESC"
            )
            for t in opp_tasks:
                lead_id = opp_to_lead.get(t.get('WhatId'))
                if lead_id:
                    opp_tasks_by_lead[lead_id].append(t)
        total_opp_tasks = sum(len(v) for v in opp_tasks_by_lead.values())
        print(f"  Found {total_opp_tasks} opportunity tasks across converted leads")

    # 2c: Pull ContentNotes for lead records
    lead_notes_by_lead = defaultdict(list)
    print(f"  Pulling notes from lead records...")
    for i in range(0, len(lead_ids), batch_size):
        batch = lead_ids[i:i + batch_size]
        id_list = "','".join(batch)
        doc_links = run_soql(
            f"SELECT ContentDocumentId, LinkedEntityId "
            f"FROM ContentDocumentLink WHERE LinkedEntityId IN ('{id_list}')"
        )
        if doc_links:
            doc_ids = list(set(d['ContentDocumentId'] for d in doc_links))
            entity_map = {d['ContentDocumentId']: d['LinkedEntityId'] for d in doc_links}
            for j in range(0, len(doc_ids), batch_size):
                doc_batch = doc_ids[j:j + batch_size]
                doc_id_list = "','".join(doc_batch)
                notes = run_soql(
                    f"SELECT Id, Title, TextPreview, CreatedDate "
                    f"FROM ContentNote WHERE Id IN ('{doc_id_list}') "
                    f"ORDER BY CreatedDate DESC"
                )
                for note in notes:
                    lead_id = entity_map.get(note['Id'])
                    if lead_id:
                        lead_notes_by_lead[lead_id].append(note)
    total_lead_notes = sum(len(v) for v in lead_notes_by_lead.values())
    print(f"  Found {total_lead_notes} notes across lead records")

    # 2d: Pull ContentNotes for opp records
    opp_notes_by_lead = defaultdict(list)
    if active_opp_ids:
        print(f"  Pulling notes from converted opportunities...")
        for i in range(0, len(active_opp_ids), batch_size):
            batch = active_opp_ids[i:i + batch_size]
            id_list = "','".join(batch)
            doc_links = run_soql(
                f"SELECT ContentDocumentId, LinkedEntityId "
                f"FROM ContentDocumentLink WHERE LinkedEntityId IN ('{id_list}')"
            )
            if doc_links:
                doc_ids = list(set(d['ContentDocumentId'] for d in doc_links))
                entity_map = {d['ContentDocumentId']: d['LinkedEntityId'] for d in doc_links}
                for j in range(0, len(doc_ids), batch_size):
                    doc_batch = doc_ids[j:j + batch_size]
                    doc_id_list = "','".join(doc_batch)
                    notes = run_soql(
                        f"SELECT Id, Title, TextPreview, CreatedDate "
                        f"FROM ContentNote WHERE Id IN ('{doc_id_list}') "
                        f"ORDER BY CreatedDate DESC"
                    )
                    for note in notes:
                        opp_id = entity_map.get(note['Id'])
                        lead_id = opp_to_lead.get(opp_id)
                        if lead_id:
                            opp_notes_by_lead[lead_id].append(note)

    print(f"  Total lead tasks: {len(all_tasks)}")

    # Group lead tasks by lead
    tasks_by_lead = defaultdict(list)
    for t in all_tasks:
        tasks_by_lead[t['WhoId']].append(t)

    # Step 3: Categorize leads
    print("\n[3/3] Categorizing leads...")
    output_leads = []

    for lead in leads_raw:
        lid = lead['Id']
        lead_tasks  = tasks_by_lead.get(lid, [])
        opp_tasks   = opp_tasks_by_lead.get(lid, [])
        lead_notes  = lead_notes_by_lead.get(lid, [])
        opp_notes   = opp_notes_by_lead.get(lid, [])

        # Combine tasks from lead + opp records, sort by date desc
        all_lead_activities = lead_tasks + opp_tasks
        all_lead_activities.sort(key=lambda t: t.get('ActivityDate') or '', reverse=True)

        # Add notes (lead + opp) as pseudo-activities
        for note in lead_notes + opp_notes:
            all_lead_activities.append({
                'Subject': note.get('Title') or 'Note',
                'Description': note.get('TextPreview') or '',
                'ActivityDate': (note.get('CreatedDate') or '')[:10],
                'TaskSubtype': 'Note',
            })

        # Determine category based on combined activity
        if not all_lead_activities:
            category = 'no_activity'
        elif any(had_real_conversation(t) for t in all_lead_activities):
            category = 'had_conversation'
        else:
            category = 'no_contact'

        # Build attempt summary
        attempt_count = len(all_lead_activities)
        last_attempt = all_lead_activities[0].get('ActivityDate') if all_lead_activities else None

        summaries = []
        for t in all_lead_activities[:5]:
            subj = (t.get('Subject') or '')[:40]
            desc = (t.get('Description') or '')[:40].replace('\n', ' ')
            entry = subj
            if desc and desc.lower() not in ('na', 'n/a', ''):
                entry += f" — {desc}"
            summaries.append(entry)
        attempt_summary = ' | '.join(summaries) if summaries else 'No activity'

        output_leads.append({
            'id': lid,
            'name': lead.get('Name'),
            'company': lead.get('Company'),
            'phone': lead.get('Phone'),
            'email': lead.get('Email'),
            'status': lead.get('Status'),
            'lead_source': lead.get('LeadSource'),
            'lead_created': lead.get('Lead_Created_Date__c'),
            'last_activity_date': lead.get('LastActivityDate'),
            'is_converted': lead.get('IsConverted', False),
            'converted_opp_id': lead.get('ConvertedOpportunityId'),
            'category': category,
            'attempt_count': attempt_count,
            'last_attempt': last_attempt,
            'attempt_summary': attempt_summary,
        })

    # Sort: no_contact by most recent lead created, no_activity same
    output_leads.sort(key=lambda x: x.get('lead_created') or '', reverse=True)

    # Counts
    counts = defaultdict(int)
    for l in output_leads:
        counts[l['category']] += 1

    print(f"\n  No activity:          {counts['no_activity']}")
    print(f"  Attempted, no contact: {counts['no_contact']}")
    print(f"  Had conversation:      {counts['had_conversation']}")

    refresh_info = {
        'refreshed_at': datetime.now().strftime('%Y-%m-%d %I:%M %p'),
        'total_leads': len(output_leads),
        'no_activity': counts['no_activity'],
        'no_contact': counts['no_contact'],
        'had_conversation': counts['had_conversation'],
    }

    # Post to dashboard
    print(f"\n[Posting to dashboard at {DASHBOARD_URL}...]")
    try:
        resp = requests.post(
            f"{DASHBOARD_URL}/api/ingest",
            json={"type": "recycled", "leads": output_leads, "refresh_info": refresh_info},
            headers={"X-API-Key": INGEST_API_KEY},
            timeout=60
        )
        if resp.status_code == 200:
            print(f"  Dashboard updated: {resp.json().get('leads')} leads")
        else:
            print(f"  Dashboard POST failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"  Dashboard POST error: {e}")

    # Local backup files
    print("[Writing local backup files...]")
    save_json('recycled_leads.json', output_leads)
    save_json('recycled_refresh.json', refresh_info)

    print("\n" + "=" * 60)
    print(f"  Scan complete. {len(output_leads)} recycled leads categorized.")
    print(f"  Open {DASHBOARD_URL}/recycled to view.")
    print("=" * 60)


if __name__ == "__main__":
    main()
