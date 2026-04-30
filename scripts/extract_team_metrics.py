#!/usr/bin/env python3
"""
Extract team leaderboard metrics from Salesforce via SF CLI.
Pulls a snapshot for every month from Mar 2026 (Bryce's start) through
the current month so the KPIs page can show any month on demand.
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
HISTORY_START_YEAR  = 2026
HISTORY_START_MONTH = 3
OUTPUT_PATH = "dashboard/data/team_metrics.json"


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


def _month_bounds(y, m):
    start = date(y, m, 1).isoformat()
    end   = date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1).isoformat()
    return start, end


def _iter_months(start_y, start_m, end_y, end_m):
    y, m = start_y, start_m
    while (y, m) <= (end_y, end_m):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def _empty_stats():
    out = defaultdict(lambda: {
        "name": "", "is_me": False,
        "leads": 0, "converted": 0,
        "calls": 0,
        "won": 0, "uw": 0, "lost": 0,
        "apv_won": 0.0,
    })
    for oid, name in TEAM.items():
        out[oid]["name"]  = name
        out[oid]["is_me"] = (oid == MY_ID)
    return out


def _pull_month(y, m, is_current):
    start, end = _month_bounds(y, m)
    stats = _empty_stats()

    # Leads created in [start, end)
    for r in sf_query(
        f"SELECT OwnerId, Owner.Name, IsConverted FROM Lead "
        f"WHERE OwnerId IN ({TEAM_IDS}) "
        f"AND CreatedDate >= {start}T00:00:00Z "
        f"AND CreatedDate < {end}T00:00:00Z"
    ):
        oid = r["OwnerId"]
        stats[oid]["name"] = r["Owner"]["Name"]
        stats[oid]["leads"] += 1
        if r["IsConverted"]:
            stats[oid]["converted"] += 1

    # Opps: current month keeps the original "OR CreatedDate" capture so UW
    # opps without a close date still show. Past months filter strictly by
    # CloseDate to avoid double counting across months.
    if is_current:
        opp_filter = (
            f"AND (IsClosed=true OR StageName='Underwriting Review') "
            f"AND (CloseDate >= {start} OR CreatedDate >= {start}T00:00:00Z)"
        )
    else:
        opp_filter = (
            f"AND (IsClosed=true OR StageName='Underwriting Review') "
            f"AND CloseDate >= {start} AND CloseDate < {end}"
        )
    for r in sf_query(
        f"SELECT OwnerId, Owner.Name, StageName, Estimated_Annual_Processing_Volume__c "
        f"FROM Opportunity WHERE OwnerId IN ({TEAM_IDS}) "
        f"{opp_filter}"
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

    # Completed calls in [start, end)
    for r in sf_query(
        f"SELECT OwnerId, Owner.Name FROM Task "
        f"WHERE OwnerId IN ({TEAM_IDS}) AND Type='Call' AND Status='Completed' "
        f"AND CreatedDate >= {start}T00:00:00Z "
        f"AND CreatedDate < {end}T00:00:00Z"
    ):
        oid = r["OwnerId"]
        stats[oid]["name"]  = r["Owner"]["Name"]
        stats[oid]["calls"] += 1

    rows = sorted(stats.values(), key=lambda x: (-(x["won"] + x["uw"]), x["name"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1

    return {
        "month_label": date(y, m, 1).strftime("%B %Y"),
        "month_start": start,
        "reps":        rows,
    }


def main():
    print("Extracting team metrics from Salesforce...")
    today = date.today()
    current_key = f"{today.year:04d}-{today.month:02d}"

    snapshots = {}
    for y, m in _iter_months(HISTORY_START_YEAR, HISTORY_START_MONTH,
                             today.year, today.month):
        key = f"{y:04d}-{m:02d}"
        is_current = (key == current_key)
        print(f"  {key}{' (current)' if is_current else ''}...")
        try:
            snapshots[key] = _pull_month(y, m, is_current)
        except SFQueryError as e:
            print(f"  ABORT on {key}: {e}", file=sys.stderr)
            print(f"  Keeping existing {OUTPUT_PATH} intact. Not posting to Railway.",
                  file=sys.stderr)
            sys.exit(1)

    current = snapshots.get(current_key)
    if not current:
        print("  ABORT: current month snapshot missing", file=sys.stderr)
        sys.exit(1)

    # Sanity check current month — if zero activity, refuse to overwrite.
    total_activity = sum(r["leads"] + r["calls"] + r["won"] + r["uw"] + r["lost"]
                         for r in current["reps"])
    if total_activity == 0:
        print("  ABORT: zero activity across all reps in current month — "
              "refusing to overwrite good data.", file=sys.stderr)
        sys.exit(1)

    output = {
        "refreshed_at":      datetime.now().strftime("%Y-%m-%d %I:%M %p"),
        "month":             current["month_label"],
        "month_start":       current["month_start"],
        "reps":              current["reps"],
        "monthly_snapshots": snapshots,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"  Done — {len(current['reps'])} reps, {len(snapshots)} months written to {OUTPUT_PATH}")
    for r in current["reps"]:
        total = r["won"] + r["uw"]
        rate  = f"{total/r['leads']*100:.0f}%" if r["leads"] else "--"
        you   = " ◀" if r["is_me"] else ""
        print(f"    #{r['rank']} {r['name']:<22} {r['leads']:>4} leads  "
              f"{r['won']}W/{r['uw']}UW={total} ({rate}){you}")

    if requests:
        print(f"\n  Posting to Railway dashboard...")
        try:
            resp = requests.post(
                f"{DASHBOARD_URL}/api/ingest",
                json={"type": "team_metrics", "team_metrics": output},
                headers={"X-API-Key": INGEST_API_KEY},
                timeout=20,
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
