#!/usr/bin/env python3
"""
Extract team leaderboard metrics from Salesforce via SF CLI.
Writes dashboard/data/team_metrics.json and POSTs to Railway.
"""

import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime

try:
    import requests
except ImportError:
    requests = None

DASHBOARD_URL  = os.environ.get("DASHBOARD_URL", "https://web-production-980e0.up.railway.app")
INGEST_API_KEY = os.environ.get("INGEST_API_KEY", "d219d2be8540f1d079dd896937fbd8fe41c9754ab955629cf74d43068e99d36d")

SF_ALIAS = "shift4"
MY_ID    = "005Pd0000084UhFIAU"

TEAM = {
    "005Pd000008yCGSIA2": "Bryan Robinson",
    "005Pd0000084UhFIAU": "Bryce Mack",
    "005Pd000009wS1xIAE": "Daniel Wellen",
    "005Pd000009EiX3IAK": "Drew Copeland",
    "005Pd000008yCGRIA2": "Farzad Minooei",
    "005Pd000009ixaHIAQ": "Hector Garcia",
    "005Pd000009g1grIAA": "Michael Hernandez",
    "005Pd000009EiNNIA0": "Miles Coughlin",
    "005Pd000009i3EjIAI": "Nicholas Olson",
    "005Pd000009EiaHIAS": "Tyler Benz",
}

TEAM_IDS = ",".join(f"'{i}'" for i in TEAM)
MONTH_START = f"{date.today().year}-{date.today().month:02d}-01"
OUTPUT_PATH = "dashboard/data/team_metrics.json"


def sf_query(soql):
    result = subprocess.run(
        ["sf", "data", "query", "--query", soql,
         "--target-org", SF_ALIAS, "--json"],
        capture_output=True, text=True, timeout=30
    )
    try:
        d = json.loads(result.stdout)
        return d.get("result", {}).get("records", [])
    except Exception:
        print(f"  WARN: query failed — {soql[:60]}...", file=sys.stderr)
        return []


def main():
    print("Extracting team metrics from Salesforce...")

    stats = defaultdict(lambda: {
        "name": "", "is_me": False,
        "leads": 0, "converted": 0,
        "calls": 0,
        "won": 0, "uw": 0, "lost": 0,
        "apv_won": 0.0,
    })

    # Seed names so everyone appears even with 0 activity
    for oid, name in TEAM.items():
        stats[oid]["name"]  = name
        stats[oid]["is_me"] = (oid == MY_ID)

    # Leads MTD
    print("  Leads...")
    for r in sf_query(
        f"SELECT OwnerId, Owner.Name, IsConverted FROM Lead "
        f"WHERE OwnerId IN ({TEAM_IDS}) AND CreatedDate >= {MONTH_START}T00:00:00Z"
    ):
        oid = r["OwnerId"]
        stats[oid]["name"] = r["Owner"]["Name"]
        stats[oid]["leads"] += 1
        if r["IsConverted"]:
            stats[oid]["converted"] += 1

    # Opps closed MTD + in Underwriting Review
    print("  Opportunities...")
    for r in sf_query(
        f"SELECT OwnerId, Owner.Name, StageName, Estimated_Annual_Processing_Volume__c "
        f"FROM Opportunity WHERE OwnerId IN ({TEAM_IDS}) "
        f"AND (IsClosed=true OR StageName='Underwriting Review') "
        f"AND (CloseDate >= {MONTH_START} OR CreatedDate >= {MONTH_START}T00:00:00Z)"
    ):
        oid = r["OwnerId"]
        stats[oid]["name"] = r["Owner"]["Name"]
        apv = r.get("Estimated_Annual_Processing_Volume__c") or 0
        if r["StageName"] == "Closed Won":
            stats[oid]["won"]     += 1
            stats[oid]["apv_won"] += apv
        elif r["StageName"] == "Underwriting Review":
            stats[oid]["uw"]  += 1
        else:
            stats[oid]["lost"] += 1

    # Completed calls MTD
    print("  Calls...")
    for r in sf_query(
        f"SELECT OwnerId, Owner.Name FROM Task "
        f"WHERE OwnerId IN ({TEAM_IDS}) AND Type='Call' AND Status='Completed' "
        f"AND CreatedDate >= {MONTH_START}T00:00:00Z"
    ):
        oid = r["OwnerId"]
        stats[oid]["name"]  = r["Owner"]["Name"]
        stats[oid]["calls"] += 1

    # Sort by (won + uw) desc, then name
    rows = sorted(
        stats.values(),
        key=lambda x: (-(x["won"] + x["uw"]), x["name"])
    )

    # Assign rank
    for i, r in enumerate(rows):
        r["rank"] = i + 1

    output = {
        "refreshed_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
        "month":        date.today().strftime("%B %Y"),
        "month_start":  MONTH_START,
        "reps":         rows,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"  Done — {len(rows)} reps written to {OUTPUT_PATH}")
    for r in rows:
        total = r["won"] + r["uw"]
        rate  = f"{total/r['leads']*100:.0f}%" if r["leads"] else "--"
        you   = " ◀" if r["is_me"] else ""
        print(f"    #{r['rank']} {r['name']:<22} {r['leads']:>4} leads  "
              f"{r['won']}W/{r['uw']}UW={total} ({rate}){you}")

    # POST to Railway dashboard so the live site updates immediately
    if requests:
        print(f"\n  Posting to Railway dashboard...")
        try:
            resp = requests.post(
                f"{DASHBOARD_URL}/api/ingest",
                json={"type": "team_metrics", "team_metrics": output},
                headers={"X-API-Key": INGEST_API_KEY},
                timeout=15,
            )
            if resp.ok:
                print(f"  Dashboard updated ✓")
            else:
                print(f"  Dashboard POST failed: {resp.status_code} {resp.text[:100]}")
        except Exception as e:
            print(f"  Dashboard POST error: {e}")
    else:
        print("  (requests not installed — skipping Railway push)")


if __name__ == "__main__":
    main()
