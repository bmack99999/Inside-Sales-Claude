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


# ─── Config ───────────────────────────────────────────────────────────────────
SF_ALIAS = "shift4"
DASHBOARD_DATA = os.path.join(os.path.dirname(__file__), "dashboard", "data")

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
        f"AND Lead_Created_Date__c < LAST_N_DAYS:4"
    )
    print(f"  Found {len(leads_raw)} recycled leads")

    lead_ids = [l['Id'] for l in leads_raw]

    # Step 2: Pull all tasks in batches
    print("\n[2/3] Querying activities (batched)...")
    all_tasks = []
    batch_size = 200
    for i in range(0, len(lead_ids), batch_size):
        batch = lead_ids[i:i + batch_size]
        id_list = "','".join(batch)
        tasks = run_soql(
            f"SELECT Id, WhoId, Subject, Description, ActivityDate, TaskSubtype, Status "
            f"FROM Task WHERE WhoId IN ('{id_list}') "
            f"ORDER BY ActivityDate DESC"
        )
        all_tasks.extend(tasks)
        batch_num = i // batch_size + 1
        total_batches = (len(lead_ids) + batch_size - 1) // batch_size
        print(f"  Batch {batch_num}/{total_batches}: {len(batch)} leads → {len(tasks)} tasks")

    print(f"  Total tasks: {len(all_tasks)}")

    # Group tasks by lead
    tasks_by_lead = defaultdict(list)
    for t in all_tasks:
        tasks_by_lead[t['WhoId']].append(t)

    # Step 3: Categorize leads
    print("\n[3/3] Categorizing leads...")
    output_leads = []

    for lead in leads_raw:
        lid = lead['Id']
        lead_tasks = tasks_by_lead.get(lid, [])

        # Determine category
        if not lead_tasks:
            category = 'no_activity'
        elif any(had_real_conversation(t) for t in lead_tasks):
            category = 'had_conversation'
        else:
            category = 'no_contact'

        # Build attempt summary
        attempt_count = len(lead_tasks)
        last_attempt = lead_tasks[0].get('ActivityDate') if lead_tasks else None

        summaries = []
        for t in lead_tasks[:5]:
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

    # Save
    print("\n[Writing output files...]")
    save_json('recycled_leads.json', output_leads)
    save_json('recycled_refresh.json', {
        'refreshed_at': datetime.now().strftime('%Y-%m-%d %I:%M %p'),
        'total_leads': len(output_leads),
        'no_activity': counts['no_activity'],
        'no_contact': counts['no_contact'],
        'had_conversation': counts['had_conversation'],
    })

    print("\n" + "=" * 60)
    print(f"  Scan complete. {len(output_leads)} recycled leads categorized.")
    print(f"  Open http://localhost:5000/recycled to view.")
    print("=" * 60)


if __name__ == "__main__":
    main()
