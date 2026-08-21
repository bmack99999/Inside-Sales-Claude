#!/usr/bin/env python3
"""Parse commission sheet text into payout ingest rows, and Customers-sheet
rows into deal ingest rows. Used by the "refresh commissions" workflow.

This is NOT a standalone extractor — it has no Google Drive auth. Claude reads
the sheets via the Drive MCP, saves the raw 'Data' section text (commission
sheets) and the deal rows (Customers sheet), then calls these helpers and POSTs
the result to Railway /api/ingest with types 'payouts' and 'deals'.

See CLAUDE.md → "Commissions Refresh Workflow".

Payout row shape (SkyForce flat rows and DM 'Data' rows share column order):
  empId , name , MID , dept , type , datePaid , amtPaid , dateRetract , amtRetract , cycle [ , Int , DBA , MID Timestamp , ... ]
Amounts may contain commas ($1,553.43) and be negative/parenthesized ($(384.55)).
A retract populates amtRetract instead of amtPaid → stored as a negative amount.
"""
import hashlib
import re
from datetime import datetime

TYPE_MAP = {
    "upfront": "upfront", "dms - upfront": "upfront",
    "true up/down": "true_up", "dms - true-up/down": "true_up", "true up": "true_up",
    "dms - saas": "saas", "saas": "saas",
    "adjustment": "adjustment", "upgrade": "upgrade",
}

# Groups: mid, dept, type, datePaid, amtPaid, dateRet, amtRet, cycle, dba(optional)
_ROW = re.compile(
    r"(?:2UP590729|9D7ELMAF3),\s*(?:BRYCE MACK|Bryce Mack),\s*"
    r"(\d{6,}),\s*"
    r"([A-Za-z0-9 ]+?),\s*"
    r"(Upfront|True up/down|Upgrade|Adjustment|DMS - Upfront|DMS - True-up/down|DMS - SaaS),\s*"
    r"([\d/]*),\s*"
    r"(\"?\s*\$?\s*\(?[\d,]*\.?\d*\)?\s*\"?|\$-|\$ -|)\s*,\s*"
    r"([\d/]*),\s*"
    r"(\"?\s*\$?\s*\(?[\d,]*\.?\d*\)?\s*\"?|\$-|\$ -|)\s*,\s*"
    r"(\d{8})"
    r"(?:,\s*[A-Za-z ]*,\s*([^,]*?)\s*,\s*[\d/.]*,)?",
    re.I,
)


def _money(s):
    s = (s or "").strip().strip('"').strip()
    if s in ("", "$-", "-", "$", "NULL", "$ -"):
        return 0.0
    neg = s.startswith("(") or s.startswith("$(") or s.startswith("-")
    s = re.sub(r"[^\d.]", "", s)
    return (-float(s) if neg else float(s)) if s else 0.0


def _iso(s):
    s = (s or "").strip()
    for f in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return None


def parse_payouts(text, source, now=None):
    """Parse one sheet's text (a 'Data' section or the flat SkyForce stream)
    into a list of payout ingest dicts. Dedup across sheets by row id afterward."""
    now = now or datetime.now().strftime("%Y-%m-%d %I:%M %p")
    out = []
    for m in _ROW.finditer(text):
        mid, dept, ctype, dpaid, apaid, dret, aret, cycle, dba = [(g or "").strip() for g in m.groups()]
        amt_paid, amt_ret = _money(apaid), _money(aret)
        amount = amt_paid if amt_paid else (-abs(amt_ret) if amt_ret else 0.0)
        eff = _iso(dpaid) or _iso(dret)
        if not amount:
            continue
        rid = hashlib.md5(f"{mid}|{ctype}|{cycle}|{amount}|{eff}|{source}".encode()).hexdigest()[:16]
        out.append({
            "id": rid, "mid": mid, "mid_raw": mid, "dba_name": dba,
            "department": dept, "payout_type": TYPE_MAP.get(ctype.lower(), ctype.lower()),
            "amount": amount, "date_paid": eff, "pay_cycle": cycle,
            "source_sheet": source, "extracted_at": now,
        })
    return out


def dedup_payouts(payouts):
    """DM sheets duplicate SkyForce true-ups that flow through their pay cycle.
    Same row id (mid|type|cycle|amount|date) collapses to one."""
    return list({p["id"]: p for p in payouts}.values())
