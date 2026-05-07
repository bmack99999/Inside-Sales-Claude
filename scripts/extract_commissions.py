#!/usr/bin/env python3
"""
Extract Bryce's Closed Won opportunities from Salesforce for commission tracking.
Pulls 2026 Closed Won deals only (per user choice). Upserts to Railway —
manual fields like install_date / true_up_amount are preserved.
"""

import json
import os
import subprocess
import sys
from datetime import date, datetime

try:
    import requests
except ImportError:
    requests = None

DASHBOARD_URL  = os.environ.get("DASHBOARD_URL", "https://web-production-980e0.up.railway.app")
INGEST_API_KEY = os.environ.get("INGEST_API_KEY", "d219d2be8540f1d079dd896937fbd8fe41c9754ab955629cf74d43068e99d36d")

SF_ALIAS = "shift4"
USER_ID  = "005Pd0000084UhFIAU"   # Bryce
BACKFILL_FROM = "2026-01-01"
OUTPUT_PATH = "dashboard/data/commissions.json"


class SFQueryError(RuntimeError):
    pass


def sf_query(soql):
    result = subprocess.run(
        ["sf", "data", "query", "--query", soql,
         "--target-org", SF_ALIAS, "--json"],
        capture_output=True, text=True, timeout=30
    )
    try:
        d = json.loads(result.stdout)
    except Exception as e:
        raise SFQueryError(f"Could not parse SF CLI output: {e}. stdout={result.stdout[:200]}")
    if d.get("status") not in (0, None) or d.get("name") or not isinstance(d.get("result"), dict):
        raise SFQueryError(f"SF CLI query failed: {d.get('name')} — {d.get('message') or d}")
    return d.get("result", {}).get("records", [])


def main():
    print("Extracting commission deals (2026 Closed Won) from Salesforce...")
    soql = (
        f"SELECT Id, Name, Account.Name, CloseDate "
        f"FROM Opportunity "
        f"WHERE OwnerId = '{USER_ID}' "
        f"AND IsClosed = true AND StageName = 'Closed Won' "
        f"AND CloseDate >= {BACKFILL_FROM} "
        f"ORDER BY CloseDate DESC"
    )
    try:
        rows = sf_query(soql)
    except SFQueryError as e:
        print(f"  ABORT: {e}", file=sys.stderr)
        sys.exit(1)

    extracted_at = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    commissions = []
    for r in rows:
        commissions.append({
            "id":           r["Id"],
            "deal_name":    r.get("Name") or "",
            "account_name": (r.get("Account") or {}).get("Name") or r.get("Name") or "",
            "close_date":   r.get("CloseDate"),
            "extracted_at": extracted_at,
        })

    print(f"  Found {len(commissions)} closed-won deals since {BACKFILL_FROM}")
    for c in commissions:
        print(f"    {c['close_date']}  {c['account_name']}")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump({"refreshed_at": extracted_at, "commissions": commissions}, f, indent=2)
    print(f"  Wrote {OUTPUT_PATH}")

    if requests:
        print(f"\n  Posting to Railway dashboard...")
        try:
            resp = requests.post(
                f"{DASHBOARD_URL}/api/ingest",
                json={"type": "commissions", "commissions": commissions},
                headers={"X-API-Key": INGEST_API_KEY},
                timeout=20,
            )
            if resp.ok:
                print(f"  Dashboard updated ✓ ({resp.json().get('count')} upserted)")
            else:
                print(f"  Dashboard POST failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"  Dashboard POST error: {e}")
    else:
        print("  (requests not installed — skipping Railway push)")


if __name__ == "__main__":
    main()
