#!/usr/bin/env python3
"""One-time (re-runnable) import of Bryce's inside sales deals from the
Customers spreadsheet into the dashboard deal tracker.

Rows 146 and up in the "Commisions" sheet are inside sales team deals.
Columns: B site, C sign date (m.d.yy), E deal type, F MID, G rate text,
H discount rate %, I per transaction, J method code, K monthly volume.

Usage:
    python3 scripts/import_inside_sales_deals.py                  # push to Railway
    python3 scripts/import_inside_sales_deals.py --dry-run        # just print
    python3 scripts/import_inside_sales_deals.py --xlsx path.xlsx --from-row 146

Upsert semantics on the server (match on MID, then site + sign date), so
running it twice does not duplicate. Hand-entered fields on the dashboard
are only overwritten by the keys this script sends; pass --fill-blanks-only
to never overwrite anything that already has a value.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'dashboard'))
from deal_tracker import parse_rate  # noqa: E402

RAILWAY = 'https://web-production-980e0.up.railway.app'


def ingest_key():
    """INGEST_API_KEY from the environment, else from the project .env."""
    if os.environ.get('INGEST_API_KEY'):
        return os.environ['INGEST_API_KEY']
    # walk up from the project root so git worktrees find the main checkout's .env
    d = os.path.abspath(os.path.join(HERE, '..'))
    for _ in range(6):
        env_path = os.path.join(d, '.env')
        if os.path.exists(env_path):
            for line in open(env_path):
                m = re.match(r'\s*INGEST_API_KEY\s*=\s*["\']?([^"\'\s]+)', line)
                if m:
                    return m.group(1)
        d = os.path.dirname(d)
    sys.exit('INGEST_API_KEY not set and no .env found')


PRODUCT_MAP = [
    (re.compile(r'micros|posi|conversion|to skytab|to st\b|to dine', re.I), 'Conversion'),
    (re.compile(r'skytab|dine', re.I), 'Dine'),
    (re.compile(r'solo', re.I), 'Solo'),
    (re.compile(r'terminal', re.I), 'Terminal'),
    (re.compile(r'processing', re.I), 'Processing Only'),
]


def sheet_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = re.sub(r'\.+', '.', str(v).strip())
    for fmt in ('%m.%d.%y', '%m.%d.%Y', '%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def product_for(deal_type):
    t = (deal_type or '').strip()
    for rx, name in PRODUCT_MAP:
        if rx.search(t):
            return name
    return 'Other'


def mid_str(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    digits = ''.join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    return digits.zfill(10) if len(digits) < 10 else digits


def parse_rows(path, from_row):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    out = []
    for r in range(from_row, ws.max_row + 1):
        row = [c.value for c in ws[r]]
        site = (row[1] or '') if len(row) > 1 else ''
        site = str(site).strip()
        if not site or site.lower() in ('total', 'site'):
            continue
        deal_type = row[4] if len(row) > 4 else None
        mid = mid_str(row[5]) if len(row) > 5 else None
        rate_raw = (str(row[6]).strip() if len(row) > 6 and row[6] not in (None, '') else None)
        pct_col = row[7] if len(row) > 7 else None
        per_col = row[8] if len(row) > 8 else None
        method = (str(row[9]).strip() if len(row) > 9 and row[9] else None)
        vol = row[10] if len(row) > 10 else None

        pct, per_item, structure = parse_rate(rate_raw or '', method)
        if pct_col not in (None, ''):
            try:
                pct = float(pct_col)
            except (TypeError, ValueError):
                pass
        if per_col not in (None, ''):
            try:
                v = float(per_col)
                per_item = v / 100.0 if v >= 1 else v
            except (TypeError, ValueError):
                pass
        if not rate_raw:
            rate_raw = ('%g%%' % pct if pct is not None else '') + (' + $%.2f' % per_item if per_item else '')
            rate_raw = rate_raw.strip() or None
        notes = ['Imported from Customers sheet row %d' % r]
        if deal_type and product_for(deal_type) in ('Other', 'Conversion'):
            notes.append('Sheet type: %s' % str(deal_type).strip())
        if method and method.upper() in ('SUPP',):
            notes.append('Sheet method code: %s' % method)
        item = {
            'site': site,
            'sign_date': sheet_date(row[2]) if len(row) > 2 else None,
            'product': product_for(deal_type),
            'mid_raw': mid,
            'mid': mid,
            'rate_raw': rate_raw,
            'rate_pct': pct,
            'per_item': per_item,
            'rate_structure': structure,
            'mo_volume': float(vol) if isinstance(vol, (int, float)) else None,
            'notes': '\n'.join(notes),
            'source': 'sheet_import',
        }
        out.append(item)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--xlsx', default=os.path.expanduser('~/Downloads/Customers 1.xlsx'))
    ap.add_argument('--from-row', type=int, default=146)
    ap.add_argument('--url', default=RAILWAY)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--fill-blanks-only', action='store_true')
    args = ap.parse_args()

    items = parse_rows(args.xlsx, args.from_row)
    print('parsed %d deals from row %d on' % (len(items), args.from_row))
    seen = {}
    for it in items:
        if it['mid']:
            seen.setdefault(it['mid'], []).append(it['site'])
    for mid, sites in seen.items():
        if len(sites) > 1:
            print('  WARNING shared MID %s: %s' % (mid, ', '.join(sites)))
    if args.dry_run:
        for it in items:
            print('  %-38s %s  %-15s %-16s %-8s %s' % (it['site'][:38], it['sign_date'], it['product'],
                                                       it['rate_structure'], it['rate_pct'], it['mid']))
        return
    import requests
    payload = {'type': 'tracked_deals', 'tracked_deals': items,
               'fill_blanks_only': args.fill_blanks_only}
    r = requests.post(args.url.rstrip('/') + '/api/ingest', json=payload,
                      headers={'X-API-Key': ingest_key()}, timeout=120)
    print(r.status_code, r.text[:400])


if __name__ == '__main__':
    main()
