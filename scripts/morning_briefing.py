#!/usr/bin/env python3
"""
Morning Briefing — sends an iMessage summary to Bryce after extraction.
Reads from local JSON files written by the extraction scripts.
"""

import json
import os
import subprocess
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "dashboard", "data")
PHONE = "+16172514554"


def load(filename):
    path = os.path.join(DATA, filename)
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def send_imessage(phone, message):
    if os.name == 'nt':
        print("iMessage not available on Windows — briefing printed above.")
        return
    # Escape backslashes and double quotes for AppleScript
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy "{phone}" of targetService
        send "{safe}" to targetBuddy
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"iMessage error: {result.stderr}")
    else:
        print("Briefing sent.")


def main():
    tm    = load("team_metrics.json")
    leads = load("leads.json") or []
    opps  = load("opportunities.json") or []

    today = date.today().strftime("%a, %B %d")
    label = "Morning" if date.today().weekday() < 4 or True else "EOD"  # always label by time
    import datetime
    hour = datetime.datetime.now().hour
    label = "Morning" if hour < 13 else "EOD"

    lines = [f"☀️ {label} Briefing — {today}", ""]

    # Personal KPIs
    my = None
    if tm and tm.get("reps"):
        my = next((r for r in tm["reps"] if r.get("is_me")), None)

    if my:
        total = my["won"] + my["uw"]
        rate  = f"{int(total / my['leads'] * 100)}%" if my["leads"] else "--"
        lines += [
            f"📊 Your MTD — Rank #{my['rank']}",
            f"  Leads: {my['leads']} | Calls: {my['calls']}",
            f"  Won: {my['won']} | UW: {my['uw']} | Total: {total}",
            f"  Close Rate: {rate} | APV: ${my['apv_won']:,.0f}",
            "",
        ]
    else:
        lines += ["📊 No team metrics — run extraction.", ""]

    # Team standings
    if tm and tm.get("reps"):
        lines.append(f"🏆 Team Standings ({tm['month']}):")
        for r in tm["reps"]:
            t      = r["won"] + r["uw"]
            marker = " ◀" if r.get("is_me") else ""
            lines.append(f"  #{r['rank']} {r['name']}: {t} ({r['won']}W/{r['uw']}UW){marker}")
        lines.append("")

    # Pipeline
    lines += [
        "📋 Pipeline",
        f"  Leads: {len(leads)} | Opps: {len(opps)}",
    ]

    if tm and tm.get("refreshed_at"):
        lines += ["", f"Data as of: {tm['refreshed_at']}"]

    message = "\n".join(lines)
    print(message)
    send_imessage(PHONE, message)


if __name__ == "__main__":
    main()
