#!/usr/bin/env python3
"""Parse the 'Customers' sheet CSV export into deal ingest rows.

Bryce exports the Customers sheet as CSV (File > Download > CSV) — the Drive
MCP truncates ~row 159, so CSV export is the reliable way to get all rows.
Claude runs this, then POSTs {"type":"deals","deals":[...]} to /api/ingest.
See CLAUDE.md → "Commissions Refresh Workflow".

Columns (0-indexed): 1 Site, 2 Sign Date, 4 Notes/deal-type, 5 MID,
6 Mo.Volume, 7 Rate, 9 Notes, 10 contact, 11 email, 12-15 Lead/Organic/Existing/Conversion.
Newer (2026) rows put the rate in col 6 and leave col 7 blank; detect and shunt.
Rows sharing a normalized MID (ownership changes) keep the first; rest skipped
(the Deal PK is the normalized MID).
"""
import csv
import re
import sys
from datetime import datetime


def _iso(s):
    s = (s or "").strip()
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", s)
    if not m:
        return s or None
    mo, d, y = m.groups()
    y = int(y)
    y = 2000 + y if y < 100 else y
    try:
        return f"{y:04d}-{int(mo):02d}-{int(d):02d}"
    except ValueError:
        return None


def _looks_like_rate(s):
    s = (s or "").strip()
    return bool(s) and bool(re.search(r"%|DP|SP|AP|Supp|\+|\bic\b", s, re.I)) and not s.startswith("$")


def _money(s):
    s = (s or "").strip()
    if not s or _looks_like_rate(s):
        return None
    try:
        return float(re.sub(r"[^\d.]", "", s)) or None
    except ValueError:
        return None


def _flag(row):
    for idx, name in [(12, "lead"), (13, "organic"), (14, "existing"), (15, "conversion")]:
        if idx < len(row) and (row[idx] or "").strip() == "1":
            return name
    return None


def parse_customers_csv(path, now=None):
    now = now or datetime.now().strftime("%Y-%m-%d %I:%M %p")
    with open(path) as f:
        rows = list(csv.reader(f))
    deals, seen = [], set()
    for row in rows[1:]:
        if len(row) < 6:
            continue
        site = (row[1] or "").strip()
        mid_raw = (row[5] or "").strip()
        if not site or site.lower() == "total" or not mid_raw:
            continue
        mid = mid_raw.lstrip("0")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        col6 = row[6] if len(row) > 6 else ""
        col7 = row[7] if len(row) > 7 else ""
        if _looks_like_rate(col6):
            rate, vol = col6.strip(), None
        else:
            rate, vol = (col7 or "").strip() or None, _money(col6)
        deals.append({
            "mid": mid, "mid_raw": mid_raw, "site": site,
            "sign_date": _iso(row[2]) if len(row) > 2 else None,
            "deal_type": ((row[4] or "").strip() or None) if len(row) > 4 else None,
            "mo_volume": vol, "rate": rate,
            "contact_name": ((row[10] or "").strip() or None) if len(row) > 10 else None,
            "contact_email": ((row[11] or "").strip() or None) if len(row) > 11 else None,
            "notes": ((row[9] or "").strip() or None) if len(row) > 9 else None,
            "source_flag": _flag(row), "extracted_at": now,
        })
    return deals


if __name__ == "__main__":
    import json
    path = sys.argv[1] if len(sys.argv) > 1 else "customers.csv"
    d = parse_customers_csv(path)
    print(json.dumps(d, indent=1))
    print(f"# {len(d)} deals", file=sys.stderr)
