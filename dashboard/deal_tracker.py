"""Inside sales deal tracker: commission projection engine.

Pure functions over dicts so the Flask layer stays thin and this is easy to
unit test. Comp plan facts (FY2025 Shift4 Direct / Digital Marketing plan,
signed 2026-03-23):

  * Upfront: $250 when a new SkyTab (Shift4 Dine) MID goes live, $200 for a
    non SkyTab product (terminal / Solo / processing only). Go live = status
    700 and $1,000 processed.
  * True up: 2 x average monthly profitability over the two full months
    after the go live month, minus the upfront already paid. Can be negative
    (true down). Upfront + true up capped at $3,000.
  * SaaS: Bryce is paid two months of SaaS, which scales with device count.
  * Timing: upfront on the first payroll after go live; true up earned on
    the last day of the second full month and paid in the second payroll
    cycle after that. Paydays here are modeled as the last bi-weekly Friday
    of the month, matching how the payout sheets actually land.
"""
import json
import re
from datetime import date, datetime, timedelta

PAY_PERIOD_ANCHOR = date(2026, 5, 15)   # Friday, bi-weekly anchor

DEFAULT_ASSUMPTIONS = {
    'upfront_dine':        250.0,   # new SkyTab / Shift4 Dine POS MID
    'upfront_other':       200.0,   # terminal, Solo, processing only
    'bonus_cap':          3000.0,   # upfront + true up ceiling
    # Calibrated 2026-09-16 against the only three dual pricing deals with both a
    # known volume and a settled true up (implied 2.73 / 2.82 / 2.16). Revisit as
    # more deals true up; three is a thin sample.
    'cost_basis_pct':        2.73,  # blended interchange + network cost, % of volume
    'cost_per_item':         0.10,  # per transaction cost
    'avg_ticket':           35.0,   # used to turn volume into a transaction count
    'saas_per_device':      29.99,  # monthly SaaS per device (terminals, handhelds, KDS, CFD)
    'saas_per_kitchen_printer': 9.99,
    'saas_months':           2,
    'default_volume':    20000.0,   # used when a deal has no volume entered
    'days_sign_to_install': 30,
    'days_install_to_live':  7,
    'stall_after_days':     30,     # signed/onboarding with no install this long = at risk
}

STATUS_LABELS = {
    'signed': 'Signed', 'onboarding': 'Onboarding',
    'install_scheduled': 'Install Scheduled', 'installed': 'Installed',
    'live': 'Live', 'stalled': 'Stalled', 'cancelled': 'Cancelled',
}
STAGE_ORDER = ['signed', 'onboarding', 'install_scheduled', 'installed',
               'live', 'upfront_paid', 'complete']
STAGE_LABELS = dict(STATUS_LABELS, upfront_paid='Upfront Paid', complete='Trued Up')

RATE_CODE_MAP = {
    'DP': 'Dual Pricing', 'CD': 'Cash Discount', 'SP': 'Surcharge',
    'SF': 'Service Fee', 'IC': 'Interchange Plus', 'AP': 'Advantage Program',
    'FLAT RATE': 'Flat Rate', 'FLAT': 'Flat Rate', 'SUPP': 'Other',
}
# Programs where the posted rate is charged to the cardholder and Shift4
# keeps the spread over cost.
PROGRAM_STRUCTURES = {'Dual Pricing', 'Cash Discount', 'Surcharge',
                      'Service Fee', 'Advantage Program'}
DINE_PRODUCTS = {'Dine', 'Conversion'}

# Every device on the account bills SaaS at saas_per_device, and Bryce is paid
# two months of it. Order matters only for display.
DEVICE_FIELDS = ('terminals', 'handhelds', 'kds', 'cfd', 'kitchen_printers', 'other_devices')
# Kitchen printers bill at a lower monthly rate than the rest; everything else
# is charged at saas_per_device.
DEVICE_SAAS_KEY = {'kitchen_printers': 'saas_per_kitchen_printer'}
DEVICE_LABELS = {'terminals': 'Terminals', 'handhelds': 'Handhelds', 'kds': 'KDS',
                 'cfd': 'CFD', 'kitchen_printers': 'Kitchen printers', 'other_devices': 'Other'}


# ── dates ────────────────────────────────────────────────────────────────────

def parse_iso(s):
    if not s:
        return None
    if isinstance(s, date):
        return s
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%m.%d.%y', '%m.%d.%Y'):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def last_payday_of_month(d):
    """Last bi-weekly Friday (anchored to PAY_PERIOD_ANCHOR) in d's month."""
    first = date(d.year, d.month, 1)
    diff = (first - PAY_PERIOD_ANCHOR).days
    cur = PAY_PERIOD_ANCHOR + timedelta(days=(diff // 14) * 14)   # <= first
    while cur < first:
        cur += timedelta(days=14)
    last = cur
    while cur.month == d.month and cur.year == d.year:
        last = cur
        cur += timedelta(days=14)
    return last


def _add_months(d, n):
    y, m = d.year, d.month + n
    while m > 12:
        y, m = y + 1, m - 12
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def next_payday_for(d):
    """First month-end payday on or after d."""
    if not d:
        return None
    candidate = last_payday_of_month(d)
    if candidate < d:
        candidate = last_payday_of_month(_add_months(d, 1))
    return candidate


def _payday_not_before(d, floor):
    """Snap an estimated payday forward if it is already behind us."""
    if d is None:
        return None
    return d if d >= floor else next_payday_for(floor)


# ── rate parsing (for import + quick entry) ─────────────────────────────────

_NUM = re.compile(r'\d+(?:\.\d+)?|\.\d+')


def parse_rate(raw, method=None):
    """'2.75% + $.15' -> (2.75, 0.15, 'Flat Rate'); '4% DP' -> (4.0, None, 'Dual Pricing').
    method is the sheet's Method column (DP / Flat Rate / IC ...) if available."""
    pct = per_item = None
    structure = None
    txt = (raw or '').strip()
    nums = _NUM.findall(txt.replace('..', '.'))
    if nums:
        try:
            pct = float(nums[0])
        except ValueError:
            pct = None
        if len(nums) > 1:
            try:
                v = float(nums[1])
                per_item = v / 100.0 if v >= 1 else v   # "15" means 15 cents
            except ValueError:
                per_item = None
    code = (method or '').strip().upper()
    if not code:
        m = re.search(r'([A-Za-z ]+)\s*$', txt)
        code = m.group(1).strip().upper() if m else ''
    structure = RATE_CODE_MAP.get(code)
    if structure is None:
        if per_item is not None:
            structure = 'Flat Rate'
        elif pct is not None and pct >= 3:
            structure = 'Dual Pricing'
        else:
            structure = 'Other'
    return pct, per_item, structure


def assumptions_from_json(raw):
    a = dict(DEFAULT_ASSUMPTIONS)
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError):
            data = {}
        for k, v in data.items():
            if k in a:
                try:
                    a[k] = float(v) if isinstance(a[k], float) else int(float(v))
                except (ValueError, TypeError):
                    pass
    return a


# ── per deal math ────────────────────────────────────────────────────────────

def monthly_profit(deal, a):
    """Estimated monthly processing profit Shift4 books on this MID."""
    vol = deal.get('mo_volume')
    assumed = False
    if not vol:
        vol = a['default_volume']
        assumed = True
    pct = deal.get('rate_pct')
    per_item = deal.get('per_item') or 0.0
    structure = deal.get('rate_structure') or 'Other'
    txns = vol / a['avg_ticket'] if a['avg_ticket'] else 0
    if pct is None:
        return None, assumed
    if structure == 'Interchange Plus':
        profit = vol * pct / 100.0 + txns * per_item
    else:
        margin = pct - a['cost_basis_pct']
        profit = vol * margin / 100.0
        if structure not in PROGRAM_STRUCTURES:
            profit += txns * (per_item - a['cost_per_item'])
    return round(max(profit, 0.0), 2), assumed


def devices_total(deal):
    return sum(int(deal.get(k) or 0) for k in DEVICE_FIELDS)


def saas_monthly(deal, a):
    """Monthly SaaS across all devices. Kitchen printers bill at their own
    rate; every other device type bills saas_per_device."""
    if deal.get('saas_monthly'):
        return float(deal['saas_monthly'])
    total = 0.0
    for f in DEVICE_FIELDS:
        n = int(deal.get(f) or 0)
        if not n:
            continue
        total += n * a[DEVICE_SAAS_KEY.get(f, 'saas_per_device')]
    return round(total, 2)


def saas_breakdown(deal, a):
    """Per device-type SaaS lines, for showing the math in the UI."""
    out = []
    for f in DEVICE_FIELDS:
        n = int(deal.get(f) or 0)
        if not n:
            continue
        rate = a[DEVICE_SAAS_KEY.get(f, 'saas_per_device')]
        out.append({'field': f, 'label': DEVICE_LABELS[f], 'count': n,
                    'rate': rate, 'monthly': round(n * rate, 2)})
    return out


def derive_stage(deal, paid, today=None):
    status = deal.get('status') or 'signed'
    if status == 'cancelled':
        return 'cancelled'
    if status == 'stalled':
        return 'stalled'
    if paid['true_up_lines']:
        return 'complete'
    if paid['upfront'] > 0:
        return 'upfront_paid'
    if status == 'live' or deal.get('go_live_date'):
        return 'live'
    sfp = parse_iso(deal.get('sf_start_processing_date'))
    if sfp and sfp <= (today or date.today()):
        return 'live'
    if status == 'installed' or deal.get('install_date'):
        return 'installed'
    if status == 'install_scheduled' or deal.get('install_scheduled_date'):
        return 'install_scheduled'
    if status == 'onboarding':
        return 'onboarding'
    return 'signed'


def summarize_payouts(lines):
    paid = {'upfront': 0.0, 'true_up': 0.0, 'saas': 0.0, 'adjustment': 0.0,
            'upgrade': 0.0, 'total': 0.0, 'true_up_lines': 0, 'saas_lines': 0,
            'clawback': 0.0, 'last_paid': None, 'lines': []}
    for p in lines:
        amt = float(p.get('amount') or 0.0)
        t = p.get('payout_type') or 'adjustment'
        if t not in paid:
            t = 'adjustment'
        paid[t] += amt
        paid['total'] += amt
        if t == 'true_up':
            paid['true_up_lines'] += 1
        if t == 'saas':
            paid['saas_lines'] += 1
        if amt < 0:
            paid['clawback'] += amt
        dp = p.get('date_paid')
        if dp and (paid['last_paid'] is None or dp > paid['last_paid']):
            paid['last_paid'] = dp
        paid['lines'].append({
            'type': t, 'amount': round(amt, 2), 'date_paid': dp,
            'pay_cycle': p.get('pay_cycle'), 'department': p.get('department'),
        })
    paid['lines'].sort(key=lambda x: (x['date_paid'] or ''))
    for k in ('upfront', 'true_up', 'saas', 'adjustment', 'upgrade', 'total', 'clawback'):
        paid[k] = round(paid[k], 2)
    return paid


def compute_deal(deal, payout_lines, a, today=None):
    """Return the deal dict enriched with paid / expected / remaining / timing."""
    today = today or date.today()
    d = dict(deal)
    paid = summarize_payouts(payout_lines)
    stage = derive_stage(d, paid, today)
    sign = parse_iso(d.get('sign_date'))
    d['days_since_sign'] = (today - sign).days if sign else None

    # ── expected components ──
    upfront_exp = a['upfront_dine'] if (d.get('product') in DINE_PRODUCTS) else a['upfront_other']
    profit, vol_assumed = monthly_profit(d, a)
    if profit is None:
        true_up_exp = None
    else:
        gross = min(2.0 * profit, a['bonus_cap'])
        true_up_exp = round(gross - upfront_exp, 2)
        # Don't project a clawback we inferred from a guessed volume. With no
        # real volume a thin flat rate always lands negative, which reads as a
        # warning but is really just missing data.
        if vol_assumed and true_up_exp < 0:
            true_up_exp = 0.0
    saas_mo = saas_monthly(d, a)
    saas_exp = round(saas_mo * a['saas_months'], 2)
    dev_total = devices_total(d)

    # ── timing ──
    go_live = parse_iso(d.get('go_live_date'))
    install = parse_iso(d.get('install_date'))
    sched = parse_iso(d.get('install_scheduled_date'))
    # Salesforce Start_Processing_Date__c: authoritative. In the past it means
    # the MID is live; in the future it is the scheduled/expected go live.
    sf_proc = parse_iso(d.get('sf_start_processing_date'))
    go_live_est = None
    go_live_basis = None
    if go_live:
        go_live_est, go_live_basis = go_live, 'go live date'
    elif sf_proc:
        go_live_est, go_live_basis = sf_proc, ('Salesforce start processing'
                                               if sf_proc <= today else 'Salesforce scheduled processing')
    elif install:
        go_live_est, go_live_basis = install + timedelta(days=a['days_install_to_live']), 'install date'
    elif sched:
        go_live_est, go_live_basis = sched + timedelta(days=a['days_install_to_live']), 'scheduled install'
    elif paid['upfront'] > 0:
        # upfront pays the payroll after go live; back into the month
        up_dates = [l['date_paid'] for l in paid['lines'] if l['type'] == 'upfront' and l['date_paid']]
        up = parse_iso(min(up_dates)) if up_dates else None
        if up:
            go_live_est, go_live_basis = up - timedelta(days=21), 'upfront payout'
    if go_live_est is None and sign:
        go_live_est = sign + timedelta(days=a['days_sign_to_install'] + a['days_install_to_live'])
        go_live_basis = 'sign date + %dd' % (a['days_sign_to_install'] + a['days_install_to_live'])
    if go_live_est and go_live_est < today and stage in ('signed', 'onboarding', 'install_scheduled') and not go_live:
        go_live_est = today + timedelta(days=a['days_install_to_live'])
        go_live_basis = 'not live yet, assumes soon'

    upfront_pay_est = next_payday_for(go_live_est) if go_live_est else None
    true_up_pay_est = None
    if go_live_est:
        # earned end of 2nd full month after go live month; paid second cycle after
        true_up_pay_est = last_payday_of_month(_add_months(go_live_est, 3))

    terminal = stage in ('cancelled',)
    frozen = stage in ('stalled',)
    # A deal only drops out of the forecast when there is no forward signal at
    # all: no Salesforce start-processing date, no install date or scheduled
    # install, and it has been sitting past the stall window. Commission is
    # driven by the install date, so once we have one the money is forecastable.
    has_signal = bool(sf_proc or install or go_live or (sched and sched >= today))
    stale = (stage in ('signed', 'onboarding') and not has_signal
             and d['days_since_sign'] is not None
             and d['days_since_sign'] > a['stall_after_days'])

    remaining = {'upfront': 0.0, 'true_up': 0.0, 'saas': 0.0, 'total': 0.0}
    if not terminal:
        if paid['upfront'] <= 0:
            remaining['upfront'] = upfront_exp
        if not paid['true_up_lines'] and true_up_exp is not None:
            remaining['true_up'] = true_up_exp
        if not paid['saas_lines'] and saas_exp:
            remaining['saas'] = saas_exp
    remaining['total'] = round(sum(remaining.values()), 2)

    # Do NOT push an overdue estimate forward to the next payday: that stacks
    # months of unpaid items onto one cycle and overstates it. Keep the date the
    # comp plan implies and surface it as overdue instead.
    upfront_overdue = bool(remaining['upfront'] and upfront_pay_est and upfront_pay_est < today)
    true_up_overdue = bool((remaining['true_up'] or remaining['saas'])
                           and true_up_pay_est and true_up_pay_est < today)

    # ── risk flags ──
    risks = []
    dss = d['days_since_sign']
    if stale:
        risks.append('No install date or Salesforce processing date %d days after signing, left out of the forecast' % dss)
    if stage == 'install_scheduled' and sched and sched < today - timedelta(days=3):
        risks.append('Scheduled install date passed without an install')
    if stage in ('installed', 'live') and go_live_est and (today - go_live_est).days > 45:
        risks.append('No upfront paid %d days after go live' % (today - go_live_est).days)
    if upfront_overdue:
        risks.append('Upfront looks overdue, expected around %s' % upfront_pay_est.isoformat())
    if true_up_overdue and (today - true_up_pay_est).days > 14:
        risks.append('True up overdue, expected around %s' % true_up_pay_est.isoformat())
    if paid['clawback'] < 0:
        risks.append('Clawback on record')
    if stage == 'stalled' and d.get('stall_reason'):
        risks.append(d['stall_reason'])

    # ── next event ──
    next_event = None
    if stage == 'cancelled':
        next_event = None
    elif stage == 'complete':
        next_event = {'label': 'Fully paid', 'date': paid['last_paid']}
    elif remaining['upfront']:
        if stage in ('signed', 'onboarding'):
            next_event = {'label': 'Install', 'date': (sched.isoformat() if sched else None)}
        elif stage == 'install_scheduled':
            next_event = {'label': 'Install', 'date': sched.isoformat() if sched else None}
        else:
            next_event = {'label': 'Upfront', 'date': upfront_pay_est.isoformat() if upfront_pay_est else None,
                          'amount': remaining['upfront']}
    elif remaining['true_up'] or remaining['saas']:
        next_event = {'label': 'True up + SaaS', 'date': true_up_pay_est.isoformat() if true_up_pay_est else None,
                      'amount': round(remaining['true_up'] + remaining['saas'], 2)}

    d.update({
        'stage': stage,
        'stage_label': STAGE_LABELS.get(stage, stage),
        'stage_index': STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1,
        'status_label': STATUS_LABELS.get(d.get('status') or 'signed'),
        'devices_total': dev_total,
        'saas_monthly_est': saas_mo,
        'saas_breakdown': saas_breakdown(d, a),
        'profit_monthly_est': profit,
        'volume_assumed': vol_assumed,
        'confidence': ('paid' if paid['total'] else
                       ('estimated' if (d.get('mo_volume') and d.get('rate_pct') is not None)
                        else 'rough')),
        'expected': {
            'upfront': upfront_exp,
            'true_up': true_up_exp,
            'saas': saas_exp,
            'total': round(upfront_exp + (true_up_exp or 0.0) + saas_exp, 2),
        },
        'paid': paid,
        'remaining': remaining,
        'forecast_counts': not (terminal or frozen or stale),
        'stale': stale,
        'dates': {
            'go_live_est': go_live_est.isoformat() if go_live_est else None,
            'go_live_basis': go_live_basis,
            'upfront_pay_est': upfront_pay_est.isoformat() if upfront_pay_est else None,
            'true_up_pay_est': true_up_pay_est.isoformat() if true_up_pay_est else None,
        },
        'overdue': {'upfront': upfront_overdue, 'true_up': true_up_overdue},
        'risks': risks,
        'at_risk': bool(risks) and stage not in ('complete', 'cancelled'),
        'next_event': next_event,
        'rate_display': rate_display(d),
    })
    return d


def rate_display(d):
    pct = d.get('rate_pct')
    per = d.get('per_item')
    if pct is None:
        return d.get('rate_raw') or ''
    s = ('%g%%' % pct)
    if per:
        s += ' + $%.2f' % per
    return s


# ── page level view ──────────────────────────────────────────────────────────

def _month_key(d):
    return '%04d-%02d' % (d.year, d.month)


def build_view(deals, payouts, events, assumptions_raw=None, today=None):
    today = today or date.today()
    a = assumptions_from_json(assumptions_raw)
    by_mid = {}
    for p in payouts:
        if p.get('mid'):
            by_mid.setdefault(p['mid'], []).append(p)
    ev_by_deal = {}
    for e in events:
        ev_by_deal.setdefault(e['deal_id'], []).append(e)

    rows = []
    for deal in deals:
        lines = by_mid.get(deal.get('mid') or '', []) if deal.get('mid') else []
        row = compute_deal(deal, lines, a, today)
        row['events'] = sorted(ev_by_deal.get(deal['id'], []), key=lambda e: e.get('at') or '', reverse=True)
        rows.append(row)
    rows.sort(key=lambda r: (r.get('sign_date') or '', r.get('site') or ''), reverse=True)

    # ── summary ──
    year = str(today.year)
    paid_total = round(sum(r['paid']['total'] for r in rows), 2)
    paid_ytd = round(sum(l['amount'] for r in rows for l in r['paid']['lines']
                         if (l['date_paid'] or '').startswith(year)), 2)
    projected_total = round(sum(r['remaining']['total'] for r in rows if r['forecast_counts']), 2)
    horizon = today + timedelta(days=90)
    proj_90 = 0.0
    overdue_total = 0.0
    for r in rows:
        if not r['forecast_counts']:
            continue
        up = parse_iso(r['dates']['upfront_pay_est'])
        tu = parse_iso(r['dates']['true_up_pay_est'])
        if r['remaining']['upfront'] and up:
            if r['overdue']['upfront']:
                overdue_total += r['remaining']['upfront']
            elif up <= horizon:
                proj_90 += r['remaining']['upfront']
        tail = r['remaining']['true_up'] + r['remaining']['saas']
        if tail and tu:
            if r['overdue']['true_up']:
                overdue_total += tail
            elif tu <= horizon:
                proj_90 += tail
    stage_counts = {}
    for r in rows:
        stage_counts[r['stage']] = stage_counts.get(r['stage'], 0) + 1
    month_key = _month_key(today)
    signed_this_month = sum(1 for r in rows if (r.get('sign_date') or '').startswith(month_key))
    paid_deals = [r for r in rows if r['paid']['total']]
    avg_per_paid_deal = round(paid_total / len(paid_deals), 2) if paid_deals else 0.0
    in_flight = [r for r in rows if r['stage'] not in ('complete', 'cancelled')]

    summary = {
        'deals': len(rows),
        'paid_total': paid_total,
        'paid_ytd': paid_ytd,
        'projected_total': projected_total,
        'projected_90d': round(proj_90, 2),
        'overdue_total': round(overdue_total, 2),
        'overdue_count': sum(1 for r in rows if r['overdue']['upfront'] or r['overdue']['true_up']),
        'signed_this_month': signed_this_month,
        'avg_per_paid_deal': avg_per_paid_deal,
        'paid_deal_count': len(paid_deals),
        'in_flight': len(in_flight),
        'at_risk': sum(1 for r in rows if r['at_risk']),
        'stalled': stage_counts.get('stalled', 0),
        'awaiting_install': sum(1 for r in rows if r['stage'] in ('signed', 'onboarding', 'install_scheduled')),
        'awaiting_payout': sum(1 for r in rows if r['stage'] in ('installed', 'live', 'upfront_paid')),
        'stage_counts': stage_counts,
        'missing_mid': sum(1 for r in rows if not r.get('mid') and r['stage'] not in ('cancelled',)),
        'missing_devices': sum(1 for r in rows if not r['devices_total'] and r['stage'] not in ('cancelled',)),
    }

    # ── forecast by month: 3 back, 6 forward ──
    start = _add_months(today, -3)
    months = [_month_key(_add_months(start, i)) for i in range(10)]
    paid_by_m = {m: 0.0 for m in months}
    proj_by_m = {m: 0.0 for m in months}
    proj_detail = {m: [] for m in months}
    overdue_items = []
    for r in rows:
        for l in r['paid']['lines']:
            k = (l['date_paid'] or '')[:7]
            if k in paid_by_m:
                paid_by_m[k] += l['amount']
        if not r['forecast_counts']:
            continue
        up = r['dates']['upfront_pay_est']
        tu = r['dates']['true_up_pay_est']
        if r['remaining']['upfront'] and up:
            item = {'site': r['site'], 'what': 'Upfront', 'amount': r['remaining']['upfront'],
                    'due': up, 'mid': r.get('mid')}
            if r['overdue']['upfront']:
                overdue_items.append(item)
            elif up[:7] in proj_by_m:
                proj_by_m[up[:7]] += r['remaining']['upfront']
                proj_detail[up[:7]].append(item)
        tail = r['remaining']['true_up'] + r['remaining']['saas']
        if tail and tu:
            item = {'site': r['site'], 'what': 'True up + SaaS', 'amount': round(tail, 2),
                    'due': tu, 'mid': r.get('mid')}
            if r['overdue']['true_up']:
                overdue_items.append(item)
            elif tu[:7] in proj_by_m:
                proj_by_m[tu[:7]] += tail
                proj_detail[tu[:7]].append(item)
    overdue_items.sort(key=lambda x: (x['due'] or '', -x['amount']))
    forecast = {
        'overdue': overdue_items,
        'overdue_total': round(sum(i['amount'] for i in overdue_items), 2),
        'months': months,
        'paid': [round(paid_by_m[m], 2) for m in months],
        'projected': [round(proj_by_m[m], 2) for m in months],
        'detail': {m: sorted(v, key=lambda x: -x['amount']) for m, v in proj_detail.items()},
        'current_month': month_key,
    }

    return {
        'deals': rows,
        'summary': summary,
        'forecast': forecast,
        'assumptions': a,
        'options': {
            'statuses': [{'value': k, 'label': v} for k, v in STATUS_LABELS.items()],
            'products': ['Dine', 'Solo', 'Terminal', 'Processing Only', 'Conversion', 'Other'],
            'rate_structures': ['Dual Pricing', 'Cash Discount', 'Surcharge', 'Service Fee',
                                'Flat Rate', 'Interchange Plus', 'Advantage Program', 'Other'],
            'stages': [{'value': s, 'label': STAGE_LABELS[s]} for s in STAGE_ORDER + ['stalled', 'cancelled']],
        },
        'today': today.isoformat(),
    }
