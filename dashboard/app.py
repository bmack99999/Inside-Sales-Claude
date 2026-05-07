import json
import os
import re
import subprocess
import uuid
from datetime import date, datetime, timedelta

import phonenumbers
from phonenumbers import timezone as _phone_tz

_TZ_LABELS = {
    'America/New_York':'ET','America/Detroit':'ET','America/Kentucky/Louisville':'ET',
    'America/Kentucky/Monticello':'ET','America/Indiana/Indianapolis':'ET',
    'America/Indiana/Vevay':'ET','America/Indiana/Marengo':'ET',
    'America/Indiana/Vincennes':'ET','America/Indiana/Winamac':'ET',
    'America/Chicago':'CT','America/Indiana/Knox':'CT','America/Indiana/Tell_City':'CT',
    'America/Menominee':'CT','America/North_Dakota/Center':'CT',
    'America/North_Dakota/New_Salem':'CT','America/North_Dakota/Beulah':'CT',
    'America/Denver':'MT','America/Boise':'MT','America/Phoenix':'MT',
    'America/Los_Angeles':'PT','America/Anchorage':'AK','America/Juneau':'AK',
    'America/Sitka':'AK','America/Yakutat':'AK','America/Nome':'AK',
    'Pacific/Honolulu':'HI','America/Adak':'HI',
    'America/Puerto_Rico':'ET','America/Toronto':'ET','America/Vancouver':'PT',
    'America/Winnipeg':'CT','America/Edmonton':'MT','America/Calgary':'MT',
}

def get_phone_tz(phone):
    if not phone:
        return None
    try:
        parsed = phonenumbers.parse(phone, 'US')
        if phonenumbers.is_valid_number(parsed):
            tzs = _phone_tz.time_zones_for_number(parsed)
            if tzs:
                for tz in tzs:
                    lbl = _TZ_LABELS.get(tz)
                    if lbl:
                        return lbl
                return tzs[0].split('/')[-1].replace('_', ' ')
    except Exception:
        pass
    return None

from flask import Flask, render_template, request, redirect, url_for, jsonify
from dateutil import parser as dateutil_parser

from config import Config
from models import (db, Lead, Opportunity, Callback, KpiLog,
                    RecycledLead, RefreshLog, SkippedToday, LeadColor,
                    SFTaskData, BossMetrics, TeamMetrics, EmailTemplate,
                    LeadEmailQueue, UserNotes, Commission)

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    db.create_all()
    # Migration: safely add new columns without relying on exception swallowing
    from sqlalchemy import text as sa_text, inspect as sa_inspect
    _inspector = sa_inspect(db.engine)
    def _col_exists(table, col):
        try:
            return col in [c['name'] for c in _inspector.get_columns(table)]
        except Exception:
            return False
    _migrations = [
        ("recycled_leads", "color",         "TEXT"),
        ("recycled_leads", "timezone",      "TEXT"),
        ("recycled_leads", "notes_snippet", "TEXT"),
        ("opportunities",  "created_date",  "TEXT"),
        ("opportunities",  "email",         "TEXT"),
        ("sf_task_data",   "weekly_count", "INTEGER DEFAULT 0"),
        ("sf_task_data",   "week_start",   "TEXT"),
        ("sf_task_data",   "daily_count",  "INTEGER DEFAULT 0"),
        ("team_metrics",   "monthly_snapshots", "TEXT"),
    ]
    for tbl, col, col_type in _migrations:
        if not _col_exists(tbl, col):
            with db.engine.connect() as _c:
                _c.execute(sa_text(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_type}"))
                _c.commit()

    # Seed email template slots if not present
    for slot in (1, 2, 3):
        if not EmailTemplate.query.filter_by(slot=slot).first():
            db.session.add(EmailTemplate(
                slot=slot,
                name=f'Email {slot}',
                subject='',
                body='',
            ))
    # Seed slot 4 (Recycled Intro) — used by /api/queue_recycled_intro
    if not EmailTemplate.query.filter_by(slot=4).first():
        db.session.add(EmailTemplate(
            slot=4,
            name='Recycled Intro',
            subject='Quick check in',
            body=(
                "Hi {first_name},\n\n"
                "Wanted to circle back on the POS inquiry you put in a little while back. "
                "Still looking, or did things move in another direction?\n\n"
                "Happy to share a quick rundown of what we offer if it's useful."
            ),
        ))
    db.session.commit()

SF_BASE = "https://crmcredorax.lightning.force.com"


# ── Date helpers ──────────────────────────────────────────────────────────────

def parse_date(date_str):
    if not date_str:
        return None
    try:
        return dateutil_parser.parse(str(date_str)).date()
    except Exception:
        return None


def days_since(date_str):
    d = parse_date(date_str)
    if d is None:
        return 9999
    return (date.today() - d).days


def days_until(date_str):
    d = parse_date(date_str)
    if d is None:
        return 9999
    return (d - date.today()).days


# ── Skipped-today helpers ─────────────────────────────────────────────────────

def get_skipped_today():
    today = date.today().isoformat()
    rows = SkippedToday.query.filter_by(skip_date=today).all()
    return set(r.record_id for r in rows)


def add_skipped_today(record_id):
    today = date.today().isoformat()
    exists = SkippedToday.query.filter_by(
        record_id=record_id, skip_date=today).first()
    if not exists:
        db.session.add(SkippedToday(record_id=record_id, skip_date=today))
        db.session.commit()


# ── Scoring ───────────────────────────────────────────────────────────────────

_REAL_CONTACT_KEYWORDS = (
    'spoke', 'talked', 'connected', 'discussed', 'call w/', 'call with',
    'meeting', 'replied', 'responded', 'response from', 'answered',
    'conversation', 'reached',
)

_NO_CONTACT_KEYWORDS = (
    'lvm', 'vm', 'voicemail', 'no answer', ' na ', 'left message',
    'no response', 'unanswered', 'went to voicemail', 'mailbox',
)

_FOOD_KEYWORDS = (
    'restaurant', 'cafe', 'grill', 'pizza', 'bar ', ' bar', 'kitchen',
    'bistro', 'diner', 'taco', 'burger', 'deli', 'bakery', 'bbq',
    'food', 'eatery', 'tavern', 'pub', 'sushi', 'thai', 'mexican',
    'chinese', 'asian', 'juice', 'coffee', 'donut', 'ice cream',
    'wings', 'seafood', 'steakhouse', 'catering', 'cuisine', 'grille',
    'noodle', 'ramen', 'curry', 'bagel', 'sandwich', 'smoothie',
    'creamery', 'brewery', 'winery', 'saloon', 'cantina', 'trattoria',
    'pizzeria', 'rotisserie', 'hibachi', 'teriyaki', 'pho', 'dim sum',
    'eats', 'kitchen', 'buffet', 'grocer', 'market',
)


def _text_blob(record):
    return (
        (record.get('activity_summary') or '') + ' ' +
        (record.get('notes_snippet') or '') + ' ' +
        (record.get('last_call_notes') or '')
    ).lower()


def _comm_quality(notes):
    """Return +adjustment based on whether notes show real two-way contact."""
    has_real = any(kw in notes for kw in _REAL_CONTACT_KEYWORDS)
    has_no_contact = any(kw in notes for kw in _NO_CONTACT_KEYWORDS)
    if has_real:
        return 'real'
    if has_no_contact:
        return 'none'
    return 'neutral'


def _looks_like_food_business(company):
    if not company:
        return True  # no company name — don't penalize
    c = ' ' + company.lower() + ' '
    return any(kw in c for kw in _FOOD_KEYWORDS)


def score_opportunity(record):
    """Score an opp 1-100: stage sets base, activity decay + comm quality adjust."""
    stage = (record.get('stage') or '').lower()

    # Stage base (low → high)
    if 'underwriting' in stage:
        base = 98
    elif 'agreement' in stage:
        base = 95
    elif 'merchant application' in stage or 'application' in stage:
        base = 95
    elif 'proposal' in stage:
        base = 85
    elif 'trending' in stage:   # Trending Positively
        base = 75
    elif 'conversation' in stage:
        base = 65
    else:
        base = 65   # fallback → treat as Conversations (default stage)

    # Activity decay (days since last activity; fall back to days since created)
    age = days_since(record.get('last_activity_date'))
    if age == 9999:
        age = days_since(record.get('created_date'))
    if age <= 2:      decay = 0
    elif age <= 7:    decay = 5
    elif age <= 14:   decay = 15
    elif age <= 30:   decay = 30
    else:             decay = 45

    # Communication quality
    comm = _comm_quality(_text_blob(record))
    if comm == 'real':    comm_adj = 5
    elif comm == 'none':  comm_adj = -5
    else:                 comm_adj = 0

    # Days-in-stage stall penalty
    dis = record.get('days_in_stage', 0) or 0
    if dis > 45:      stall = 10
    elif dis > 21:    stall = 5
    else:             stall = 0

    score = base - decay + comm_adj - stall
    return max(1, min(100, score))


def score_lead(record):
    """Score a lead 1-100: fresher + real comm = higher; stale + many attempts = lower."""
    # Base by lead age
    age = record.get('lead_age_days')
    if age is None:
        age = days_since(record.get('lead_created'))
        if age == 9999:
            age = 0
    if age <= 2:       base = 70
    elif age <= 7:     base = 55
    elif age <= 14:    base = 40
    elif age <= 30:    base = 25
    else:              base = 12

    # Activity recency bonus
    act_age = days_since(record.get('last_activity_date'))
    if act_age <= 1:        act_adj = 15
    elif act_age <= 4:      act_adj = 10
    elif act_age <= 10:     act_adj = 5
    elif act_age <= 21:     act_adj = 0
    else:                   act_adj = -5

    # Call-attempts curve
    attempts = record.get('call_attempts', 0) or 0
    if attempts == 0:       att_adj = 5
    elif attempts <= 3:     att_adj = 10
    elif attempts <= 6:     att_adj = 5
    elif attempts <= 10:    att_adj = -5
    elif attempts <= 15:    att_adj = -15
    else:                   att_adj = -25

    # Communication quality
    comm = _comm_quality(_text_blob(record))
    if comm == 'real':      comm_adj = 10
    elif comm == 'none':    comm_adj = -5
    else:                   comm_adj = 0

    # Non-restaurant penalty (only bites when attempts pile up)
    company = record.get('company')
    food_adj = 0
    if not _looks_like_food_business(company):
        if attempts >= 12:   food_adj = -25
        elif attempts >= 6:  food_adj = -15

    # Status nudge
    status = (record.get('status') or '').lower()
    if any(s in status for s in ('working', 'qualified', 'contacted')):
        status_adj = 5
    else:
        status_adj = 0

    score = base + act_adj + att_adj + comm_adj + food_adj + status_adj
    return max(1, min(100, score))


def score_record(record):
    """Dispatch to lead or opp scorer. Returns 1-100."""
    if record.get('type') == 'opportunity':
        return score_opportunity(record)
    return score_lead(record)


def badge_tier(score):
    if score >= 75:
        return ('HOT', 'badge-hot')
    elif score >= 50:
        return ('WARM', 'badge-warm')
    elif score >= 25:
        return ('COOL', 'badge-cool')
    else:
        return ('COLD', 'badge-cold')


def enrich_record(record):
    record['_score'] = score_record(record)
    record['_tier_label'], record['_tier_class'] = badge_tier(record['_score'])
    due_in = days_until(record.get('next_task_due'))
    record['_task_overdue'] = due_in < 0
    record['_task_due_today'] = due_in == 0
    record['_days_since_activity'] = days_since(record.get('last_activity_date'))
    return record


def enrich_callback(cb):
    added = days_since(cb.get('added_date'))
    cb['_days_since_added'] = added if added != 9999 else 0
    due_in = days_until(cb.get('task_due'))
    cb['_task_overdue'] = due_in < 0
    cb['_task_due_today'] = due_in == 0
    last_called = days_since(cb.get('last_call_date'))
    cb['_days_since_call'] = last_called if last_called != 9999 else None
    return cb


def data_staleness_days():
    log = RefreshLog.query.filter_by(
        refresh_type='salesforce').order_by(
        RefreshLog.id.desc()).first()
    if not log or not log.refreshed_at:
        return None
    try:
        dt = dateutil_parser.parse(log.refreshed_at)
        return (datetime.now() - dt).days
    except Exception:
        return None


# ── Main Dashboard ────────────────────────────────────────────────────────────

@app.route('/notes')
def dashboard():
    skipped = get_skipped_today()

    leads = [enrich_record({**l.to_dict(), 'type': 'lead'})
             for l in Lead.query.all()
             if l.id not in skipped]

    opps = [enrich_record({**o.to_dict()})
            for o in Opportunity.query.all()
            if o.id not in skipped]

    all_records = sorted(leads + opps, key=lambda x: x['_score'], reverse=True)

    hot  = [r for r in all_records if r['_tier_label'] == 'HOT']
    warm = [r for r in all_records if r['_tier_label'] == 'WARM']
    cool = [r for r in all_records if r['_tier_label'] == 'COOL']
    cold = [r for r in all_records if r['_tier_label'] == 'COLD']

    callbacks = [enrich_callback(c.to_dict())
                 for c in Callback.query.order_by(Callback.added_date.desc()).all()]

    log = RefreshLog.query.filter_by(
        refresh_type='salesforce').order_by(RefreshLog.id.desc()).first()
    refresh_info = log.to_dict() if log else {}

    today_str = date.today().strftime('%A, %B %d, %Y').replace(' 0', ' ')
    stale_days = data_staleness_days()

    # Build enriched leads/opps for the My Leads table embedded on this page
    color_map = {lc.sf_id: lc.color for lc in LeadColor.query.all()}
    all_leads = []
    for l in Lead.query.all():
        d = l.to_dict()
        d['color']   = color_map.get(l.id)
        d['timezone'] = get_phone_tz(l.phone)
        d['sf_url']  = f"{SF_BASE}/lightning/r/Lead/{l.id}/view"
        d['type']    = 'lead'
        d['score']   = score_record(d)
        all_leads.append(d)
    all_leads.sort(key=lambda x: x['score'], reverse=True)

    all_opps = []
    for o in Opportunity.query.all():
        d = o.to_dict()
        d['color']   = color_map.get(o.id)
        d['timezone'] = get_phone_tz(o.phone)
        d['sf_url']  = f"{SF_BASE}/lightning/r/Opportunity/{o.id}/view"
        d['type']    = 'opportunity'
        d['score']   = score_record(d)
        all_opps.append(d)
    all_opps.sort(key=lambda x: x['score'], reverse=True)

    # Briefing stats
    today_iso = date.today().isoformat()
    new_leads         = [l for l in all_leads if (l.get('lead_age_days') or 999) <= 1]
    callbacks_today   = [c for c in callbacks if c.get('task_due') == today_iso]
    overdue_followups = [r for r in all_leads + all_opps
                         if days_since(r.get('last_activity_date')) >= 14]
    hot_count         = sum(1 for r in all_leads + all_opps if r['score'] >= 65)

    # Personal KPIs + team standings from TeamMetrics
    my_stats   = None
    team_reps  = []
    team_max   = 1
    tm = TeamMetrics.query.order_by(TeamMetrics.id.desc()).first()
    if tm and tm.reps:
        team_reps = tm.reps
        for rep in team_reps:
            if rep.get('is_me'):
                my_stats = rep
            total = (rep.get('won', 0) or 0) + (rep.get('uw', 0) or 0)
            if total > team_max:
                team_max = total

    return render_template('dashboard.html',
        hot=hot, warm=warm, cool=cool, cold=cold,
        total=len(all_records),
        skipped_count=len(skipped),
        refresh_info=refresh_info,
        today_str=today_str,
        stale_days=stale_days,
        callbacks=callbacks,
        all_leads=all_leads,
        all_opps=all_opps,
        sf_base=SF_BASE,
        new_leads=new_leads,
        callbacks_today=callbacks_today,
        overdue_followups=overdue_followups,
        hot_count=hot_count,
        my_stats=my_stats,
        team_reps=team_reps,
        team_max=team_max,
        tm_refreshed_at=tm.refreshed_at if tm else None,
    )



# ── My Leads & Opps ──────────────────────────────────────────────────────────

@app.route('/my_leads')
def my_leads():
    tab        = request.args.get('tab', 'leads')
    phone_only = request.args.get('phone_only', '0') == '1'
    source_filter = request.args.get('source', '')
    stage_filter  = request.args.get('stage', '')

    color_map = {lc.sf_id: lc.color for lc in LeadColor.query.all()}

    # ── Leads ──
    lead_q = Lead.query
    if phone_only:
        lead_q = lead_q.filter(Lead.phone != None, Lead.phone != '')
    if source_filter:
        lead_q = lead_q.filter(Lead.lead_source == source_filter)
    leads_raw = lead_q.order_by(Lead.last_activity_date.asc().nullsfirst()).all()

    leads = []
    for l in leads_raw:
        d = l.to_dict()
        d['color']    = color_map.get(l.id)
        d['timezone'] = get_phone_tz(l.phone)
        d['sf_url']   = f"{SF_BASE}/lightning/r/Lead/{l.id}/view"
        d['type']     = 'lead'
        d['score']    = score_record(d)
        leads.append(d)
    leads.sort(key=lambda x: x['score'], reverse=True)

    lead_sources = sorted(set(l.lead_source for l in Lead.query.all() if l.lead_source))

    # ── Opps ──
    opp_q = Opportunity.query
    if phone_only:
        opp_q = opp_q.filter(Opportunity.phone != None, Opportunity.phone != '')
    if stage_filter:
        opp_q = opp_q.filter(Opportunity.stage == stage_filter)
    opps_raw = opp_q.order_by(Opportunity.last_activity_date.asc().nullsfirst()).all()

    opps = []
    for o in opps_raw:
        d = o.to_dict()
        d['color']    = color_map.get(o.id)
        d['timezone'] = get_phone_tz(o.phone)
        d['sf_url']   = f"{SF_BASE}/lightning/r/Opportunity/{o.id}/view"
        d['type']     = 'opportunity'
        d['score']    = score_record(d)
        opps.append(d)
    opps.sort(key=lambda x: x['score'], reverse=True)

    opp_stages = sorted(set(o.stage for o in Opportunity.query.all() if o.stage))

    log = RefreshLog.query.filter_by(
        refresh_type='salesforce').order_by(RefreshLog.id.desc()).first()
    refresh_info = log.to_dict() if log else {}

    email_queue = {q.sf_id: q.slot for q in LeadEmailQueue.query.all()}

    return render_template('my_leads.html',
        leads=leads, opps=opps,
        tab=tab, phone_only=phone_only,
        source_filter=source_filter, stage_filter=stage_filter,
        lead_sources=lead_sources, opp_stages=opp_stages,
        refresh_info=refresh_info,
        email_queue=email_queue,
        sf_base=SF_BASE,
    )


# ── Commissions ───────────────────────────────────────────────────────────────

PAY_PERIOD_ANCHOR = date(2026, 5, 15)   # Friday — bi-weekly anchor

def _bi_weekly_fridays(start, end):
    """Return list of bi-weekly Fridays anchored to PAY_PERIOD_ANCHOR within [start, end]."""
    diff = (start - PAY_PERIOD_ANCHOR).days
    weeks = -(-diff // 14) if diff > 0 else diff // 14   # ceil for positive
    cur = PAY_PERIOD_ANCHOR + timedelta(days=weeks * 14)
    while cur < start:
        cur += timedelta(days=14)
    out = []
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=14)
    return out

def _last_payday_of_month(d):
    """Return the last bi-weekly Friday in d.year/d.month (commission payday)."""
    diff = (d - PAY_PERIOD_ANCHOR).days
    weeks = diff // 14
    cur = PAY_PERIOD_ANCHOR + timedelta(days=weeks * 14)
    # walk forward until we leave the month, then back up one step
    while cur.month == d.month and cur.year == d.year:
        nxt = cur + timedelta(days=14)
        if nxt.month != d.month or nxt.year != d.year:
            return cur
        cur = nxt
    return cur - timedelta(days=14)

def _next_payday_for(paid_iso):
    """Given an ISO date string when a commission was 'paid' to Bryce in Salesforce
    terms, return the actual payday Bryce gets the money — last bi-weekly Friday
    in that month, or if that's already past, the last payday of next month."""
    if not paid_iso:
        return None
    try:
        d = datetime.strptime(paid_iso, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None
    candidate = _last_payday_of_month(d)
    if candidate < d:
        # rolled over — push to next month
        nxt_month_start = (date(d.year + (1 if d.month == 12 else 0),
                                1 if d.month == 12 else d.month + 1, 1))
        candidate = _last_payday_of_month(nxt_month_start)
    return candidate

def _compute_commission_kpis(items):
    today = date.today()
    ytd_install = ytd_true = 0.0
    mtd_install = mtd_true = 0.0
    for it in items:
        bonus_paid = it.get('install_bonus_paid_date')
        true_paid  = it.get('true_up_paid_date')
        bonus_amt  = float(it.get('install_bonus_amount') or 0)
        true_amt   = float(it.get('true_up_amount') or 0)
        if bonus_paid:
            try:
                bp = datetime.strptime(bonus_paid, '%Y-%m-%d').date()
                if bp.year == today.year:
                    ytd_install += bonus_amt
                    if bp.month == today.month:
                        mtd_install += bonus_amt
            except (ValueError, TypeError):
                pass
        if true_paid:
            try:
                tp = datetime.strptime(true_paid, '%Y-%m-%d').date()
                if tp.year == today.year:
                    ytd_true += true_amt
                    if tp.month == today.month:
                        mtd_true += true_amt
            except (ValueError, TypeError):
                pass
    return {
        'mtd_install': round(mtd_install, 2),
        'mtd_true_up': round(mtd_true, 2),
        'mtd_total':   round(mtd_install + mtd_true, 2),
        'ytd_install': round(ytd_install, 2),
        'ytd_true_up': round(ytd_true, 2),
        'ytd_total':   round(ytd_install + ytd_true, 2),
    }

def _compute_pay_periods(items):
    """Bucket paid items by commission payday (last bi-weekly Friday of paid-date month).
    Also produces a 'next payday' card with projected payouts."""
    today = date.today()
    buckets = {}   # iso payday → {'pay_date', 'install_payouts', 'true_up_payouts', 'total'}
    def _bucket(d_iso):
        return buckets.setdefault(d_iso, {
            'pay_date': d_iso, 'install_payouts': [], 'true_up_payouts': [], 'total': 0.0
        })
    for it in items:
        bonus_paid = it.get('install_bonus_paid_date')
        true_paid  = it.get('true_up_paid_date')
        bonus_amt  = float(it.get('install_bonus_amount') or 0)
        true_amt   = float(it.get('true_up_amount') or 0)
        if bonus_paid and bonus_amt:
            pd = _next_payday_for(bonus_paid)
            if pd:
                b = _bucket(pd.isoformat())
                b['install_payouts'].append({'account': it['account_name'], 'amount': bonus_amt})
                b['total'] += bonus_amt
        if true_paid and true_amt:
            pd = _next_payday_for(true_paid)
            if pd:
                b = _bucket(pd.isoformat())
                b['true_up_payouts'].append({'account': it['account_name'], 'amount': true_amt})
                b['total'] += true_amt
    rolled = sorted(buckets.values(), key=lambda x: x['pay_date'], reverse=True)
    # projected next payday = last bi-weekly Friday of current month (or next if past)
    next_pay = _last_payday_of_month(today)
    if next_pay < today:
        nxt_month_start = (date(today.year + (1 if today.month == 12 else 0),
                                1 if today.month == 12 else today.month + 1, 1))
        next_pay = _last_payday_of_month(nxt_month_start)
    projected = {'pay_date': next_pay.isoformat(),
                 'install_payouts': [], 'true_up_payouts': [], 'total': 0.0}
    for it in items:
        # already-paid bonuses landing on this payday
        bonus_paid = it.get('install_bonus_paid_date')
        if bonus_paid:
            pd = _next_payday_for(bonus_paid)
            if pd and pd == next_pay:
                continue   # already counted in rolled bucket; skip in projected
        # if installed but bonus not yet paid, project it onto next_pay
        if it.get('install_date') and not bonus_paid:
            projected['install_payouts'].append({
                'account': it['account_name'], 'amount': Commission.INSTALL_BONUS,
            })
            projected['total'] += Commission.INSTALL_BONUS
        # if true_up_amount entered but not paid, project it
        true_paid = it.get('true_up_paid_date')
        true_amt  = float(it.get('true_up_amount') or 0)
        if true_amt and not true_paid:
            projected['true_up_payouts'].append({
                'account': it['account_name'], 'amount': true_amt,
            })
            projected['total'] += true_amt
    projected['total'] = round(projected['total'], 2)
    return {'history': rolled, 'projected': projected}


@app.route('/commissions')
def commissions():
    rows  = Commission.query.order_by(Commission.close_date.desc()).all()
    items = [r.to_dict() for r in rows]
    kpis  = _compute_commission_kpis(items)
    periods = _compute_pay_periods(items)
    refreshed_at = max([i.get('extracted_at') or '' for i in items], default='')
    return render_template('commissions.html',
                           items=items, kpis=kpis, periods=periods,
                           refreshed_at=refreshed_at)


@app.route('/api/commissions_data')
def commissions_data():
    items = [r.to_dict() for r in Commission.query.all()]
    return jsonify({'commissions': items})


@app.route('/api/commissions/update', methods=['POST'])
def commissions_update():
    payload = request.get_json(force=True) or {}
    opp_id = payload.get('id')
    if not opp_id:
        return jsonify({'error': 'missing id'}), 400
    c = Commission.query.get(opp_id)
    if not c:
        return jsonify({'error': 'not found'}), 404
    for field in ('install_date', 'install_bonus_paid_date',
                  'true_up_amount', 'true_up_paid_date', 'mid', 'notes'):
        if field in payload:
            val = payload[field]
            if field == 'true_up_amount':
                try:
                    val = float(val) if val not in (None, '') else 0
                except (ValueError, TypeError):
                    val = 0
            setattr(c, field, val if val != '' else None)
    db.session.commit()
    return jsonify({'ok': True, 'item': c.to_dict()})


# ── KPIs ──────────────────────────────────────────────────────────────────────

@app.route('/')
@app.route('/kpis')
def kpis():
    kpi_log = [k.to_dict() for k in
               KpiLog.query.order_by(KpiLog.date.desc()).all()]
    return render_template('kpis.html', kpi_log=kpi_log)


@app.route('/api/kpis')
def api_kpis():
    kpi_log = [k.to_dict() for k in
               KpiLog.query.order_by(KpiLog.date.desc()).all()]
    return jsonify(kpi_log)


@app.route('/api/metrics')
def api_metrics():
    row = BossMetrics.query.order_by(BossMetrics.id.desc()).first()
    return jsonify(row.to_dict() if row else {})


@app.route('/api/team_metrics')
def api_team_metrics():
    row = TeamMetrics.query.order_by(TeamMetrics.id.desc()).first()
    if not row:
        return jsonify({'error': 'No team data yet — run Refresh SF Data'})
    return jsonify(row.to_dict())


@app.route('/api/tasks')
def api_tasks():
    try:
        row = SFTaskData.query.order_by(SFTaskData.id.desc()).first()
        return jsonify(row.to_dict() if row else {})
    except Exception as e:
        return jsonify({'error': str(e), 'completed': [], 'scheduled': [],
                        'weekly_count': 0})


@app.route('/api/records')
def api_records():
    leads = [enrich_record({**l.to_dict(), 'type': 'lead'})
             for l in Lead.query.all()]
    opps  = [enrich_record(o.to_dict()) for o in Opportunity.query.all()]
    return jsonify(leads + opps)


# ── Log KPI ───────────────────────────────────────────────────────────────────

@app.route('/log_kpi', methods=['POST'])
def log_kpi():
    today = date.today().isoformat()
    entry = KpiLog.query.filter_by(date=today).first()
    if not entry:
        entry = KpiLog(date=today)
        db.session.add(entry)
    entry.dials        = int(request.form.get('dials', 0))
    entry.connects     = int(request.form.get('connects', 0))
    entry.voicemails   = int(request.form.get('voicemails', 0))
    entry.demos_set    = int(request.form.get('demos_set', 0))
    entry.applications = int(request.form.get('applications', 0))
    entry.closes       = int(request.form.get('closes', 0))
    entry.logged_at    = datetime.now().isoformat()
    db.session.commit()
    return redirect(url_for('dashboard'))


# ── Log Call ──────────────────────────────────────────────────────────────────

@app.route('/log_call', methods=['POST'])
def log_call():
    record_id = request.form.get('record_id')
    outcome   = request.form.get('outcome', 'Attempted')
    notes     = request.form.get('notes', '').strip()
    next_task = request.form.get('next_task', '').strip()
    next_due  = request.form.get('next_task_due', '').strip()
    today     = date.today().isoformat()

    record = Lead.query.get(record_id) or Opportunity.query.get(record_id)
    if record:
        record.last_activity_date = today
        record.last_activity_type = 'Call'
        if outcome == 'Connected':
            if isinstance(record, Lead):
                record.status = 'Connected'
            record.call_attempts = (record.call_attempts or 0) + 1
        elif outcome in ('Voicemail', 'No Answer', 'Attempted'):
            if isinstance(record, Lead) and (record.status or '').lower() in ('new', ''):
                record.status = 'Attempted'
            record.call_attempts = (record.call_attempts or 0) + 1
        if notes:
            record.last_call_notes = notes
            record.notes_snippet   = notes[:120] + ('...' if len(notes) > 120 else '')
        if next_task:
            record.next_task = next_task
            if isinstance(record, Opportunity):
                record.next_step = next_task
        if next_due:
            record.next_task_due = next_due
        db.session.commit()

    return redirect(url_for('dashboard'))


# ── Add Record ────────────────────────────────────────────────────────────────

@app.route('/add_record', methods=['POST'])
def add_record():
    rec_type = request.form.get('type', 'lead')
    today    = date.today().isoformat()
    new_id   = 'manual-' + str(uuid.uuid4())[:8]

    if rec_type == 'opportunity':
        record = Opportunity(
            id             = new_id,
            name           = request.form.get('name', '').strip(),
            account_name   = request.form.get('company', '').strip(),
            contact_name   = request.form.get('contact_name', '').strip(),
            phone          = request.form.get('phone', '').strip(),
            stage          = request.form.get('stage', 'Demo Scheduled'),
            amount         = 0,
            close_date     = request.form.get('close_date', '').strip() or None,
            last_activity_date = today,
            next_step      = request.form.get('next_task', '').strip() or None,
            next_task_due  = request.form.get('next_task_due', '').strip() or None,
            days_in_stage  = 0,
            probability    = 0,
            notes_snippet  = request.form.get('notes', '').strip() or None,
            open_tasks     = [],
            manually_added = True,
            extracted_at   = datetime.now().isoformat(),
        )
    else:
        record = Lead(
            id             = new_id,
            name           = request.form.get('name', '').strip(),
            company        = request.form.get('company', '').strip(),
            phone          = request.form.get('phone', '').strip(),
            email          = request.form.get('email', '').strip() or None,
            status         = request.form.get('status', 'New'),
            lead_source    = request.form.get('lead_source', 'Manual Entry'),
            lead_age_days  = 0,
            last_activity_date = today,
            next_task      = request.form.get('next_task', '').strip() or None,
            next_task_due  = request.form.get('next_task_due', '').strip() or None,
            call_attempts  = 0,
            notes_snippet  = request.form.get('notes', '').strip() or None,
            open_tasks     = [],
            is_recycled    = False,
            manually_added = True,
            extracted_at   = datetime.now().isoformat(),
        )

    db.session.add(record)
    db.session.commit()
    return redirect(url_for('dashboard'))


# ── Skip Today ────────────────────────────────────────────────────────────────

@app.route('/skip_today', methods=['POST'])
def skip_today():
    add_skipped_today(request.form.get('record_id'))
    return redirect(url_for('dashboard'))


# ── Refresh (local Mac only) ──────────────────────────────────────────────────

@app.route('/refresh')
def refresh():
    import subprocess
    script = os.path.join(os.path.dirname(__file__), '..', 'extract_salesforce.py')
    if os.path.exists(script):
        subprocess.Popen(['python3', script])
    return redirect(url_for('dashboard'))


@app.route('/refresh_recycled')
def refresh_recycled():
    import subprocess
    script = os.path.join(os.path.dirname(__file__), '..', 'extract_recycled.py')
    if os.path.exists(script):
        subprocess.Popen(['python3', script])
    return redirect(url_for('recycled'))


# ── Callbacks ─────────────────────────────────────────────────────────────────

@app.route('/add_callback', methods=['POST'])
def add_callback():
    today = date.today().isoformat()
    cb = Callback(
        id           = 'cb-' + str(uuid.uuid4())[:8],
        name         = request.form.get('name', '').strip(),
        contact_name = request.form.get('contact_name', '').strip() or None,
        phone        = request.form.get('phone', '').strip() or None,
        sf_url       = request.form.get('sf_url', '').strip() or None,
        task_due     = request.form.get('task_due', '').strip() or None,
        notes        = request.form.get('notes', '').strip() or None,
        do_not_call  = False,
        added_date   = today,
        call_attempts = 0,
    )
    db.session.add(cb)
    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/log_callback_call', methods=['POST'])
def log_callback_call():
    cb = Callback.query.get(request.form.get('cb_id'))
    if cb:
        cb.last_call_date  = date.today().isoformat()
        cb.call_attempts   = (cb.call_attempts or 0) + 1
        notes = request.form.get('notes', '').strip()
        if notes:
            cb.last_call_notes = notes
        task_due = request.form.get('task_due', '').strip()
        if task_due:
            cb.task_due = task_due
        cb.do_not_call = request.form.get('do_not_call') == '1'
        db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/edit_callback', methods=['POST'])
def edit_callback():
    cb = Callback.query.get(request.form.get('cb_id'))
    if cb:
        cb.name         = request.form.get('name', '').strip() or cb.name
        cb.contact_name = request.form.get('contact_name', '').strip() or None
        cb.phone        = request.form.get('phone', '').strip() or None
        cb.sf_url       = request.form.get('sf_url', '').strip() or None
        cb.task_due     = request.form.get('task_due', '').strip() or None
        cb.notes        = request.form.get('notes', '').strip() or None
        db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/delete_callback', methods=['POST'])
def delete_callback():
    cb = Callback.query.get(request.form.get('cb_id'))
    if cb:
        db.session.delete(cb)
        db.session.commit()
    return redirect(url_for('dashboard'))


# ── Recycled Leads ────────────────────────────────────────────────────────────

@app.route('/recycled')
def recycled():
    source_filter = request.args.get('source', '')
    phone_only    = request.args.get('phone_only', '1') == '1'
    converted     = request.args.get('converted', 'leads')  # 'leads' | 'opps' | 'all'

    query = RecycledLead.query
    if phone_only:
        query = query.filter(RecycledLead.phone.isnot(None),
                             RecycledLead.phone != '')
    if source_filter:
        query = query.filter_by(lead_source=source_filter)
    if converted == 'leads':
        query = query.filter_by(is_converted=False)
    elif converted == 'opps':
        query = query.filter(RecycledLead.is_converted == True,
                             RecycledLead.converted_opp_id != None)

    leads = []
    for r in query.all():
        d = r.to_dict()
        # Map recycled fields to my_leads-equivalent fields for template reuse
        d['type']             = 'lead'
        d['call_attempts']    = d.get('attempt_count') or 0
        d['activity_summary'] = d.get('attempt_summary')
        d['lead_age_days']    = days_since(d.get('lead_created'))
        if d['lead_age_days'] == 9999:
            d['lead_age_days'] = None
        d['sf_url']           = f"{SF_BASE}/lightning/r/Lead/{r.id}/view"
        d['score']            = score_record(d)
        leads.append(d)
    leads.sort(key=lambda x: x['score'] or 0, reverse=True)

    sources = sorted(set(
        r.lead_source for r in RecycledLead.query.all()
        if r.lead_source
    ))

    # Converted vs lead counts (across all categories)
    base = RecycledLead.query
    converted_counts = {
        'leads': base.filter_by(is_converted=False).count(),
        'opps':  base.filter(RecycledLead.is_converted == True,
                             RecycledLead.converted_opp_id != None).count(),
        'all':   base.count(),
    }

    log = RefreshLog.query.filter_by(
        refresh_type='recycled').order_by(RefreshLog.id.desc()).first()
    refresh_info = log.to_dict() if log else {}

    email_queue = {q.sf_id: q.slot for q in LeadEmailQueue.query.all()}

    return render_template('recycled.html',
        leads=leads,
        converted_counts=converted_counts,
        converted=converted,
        source_filter=source_filter,
        phone_only=phone_only,
        sources=sources,
        refresh_info=refresh_info,
        email_queue=email_queue,
        sf_base=SF_BASE,
    )


# ── API: Ingest (called by extraction scripts) ────────────────────────────────

@app.route('/api/ingest', methods=['POST'])
def api_ingest():
    # Authenticate
    api_key = request.headers.get('X-API-Key', '')
    if api_key != app.config['INGEST_API_KEY']:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json(force=True)
    ingest_type = data.get('type')

    if ingest_type == 'salesforce':
        # Replace all non-manually-added leads and opps
        Lead.query.filter_by(manually_added=False).delete()
        Opportunity.query.filter_by(manually_added=False).delete()

        for item in data.get('leads', []):
            item.pop('type', None)
            item.setdefault('manually_added', False)
            db.session.merge(Lead(**item))

        for item in data.get('opps', []):
            item.pop('type', None)
            item.setdefault('manually_added', False)
            db.session.merge(Opportunity(**item))

        info = data.get('refresh_info', {})
        db.session.add(RefreshLog(
            refresh_type      = 'salesforce',
            refreshed_at      = info.get('refreshed_at'),
            lead_count        = info.get('lead_count'),
            opp_count         = info.get('opp_count'),
            detail_pass_leads = info.get('detail_pass_leads'),
            detail_pass_opps  = info.get('detail_pass_opps'),
        ))
        db.session.commit()
        return jsonify({'ok': True, 'leads': len(data.get('leads', [])),
                        'opps': len(data.get('opps', []))})

    elif ingest_type == 'recycled':
        # Load saved colors BEFORE deleting so they survive the re-scan
        color_map = {lc.sf_id: lc.color for lc in LeadColor.query.all()}
        # synchronize_session=False: skip identity-map sync since we're replacing everything
        RecycledLead.query.delete(synchronize_session=False)
        db.session.flush()
        for item in data.get('leads', []):
            rl = RecycledLead(**item)
            rl.color = color_map.get(item['id'])
            db.session.add(rl)

        info = data.get('refresh_info', {})
        db.session.add(RefreshLog(
            refresh_type     = 'recycled',
            refreshed_at     = info.get('refreshed_at'),
            total_leads      = info.get('total_leads'),
            no_activity      = info.get('no_activity'),
            no_contact       = info.get('no_contact'),
            had_conversation = info.get('had_conversation'),
        ))
        db.session.commit()
        return jsonify({'ok': True, 'leads': len(data.get('leads', []))})

    elif ingest_type == 'metrics':
        m = data.get('metrics', {})
        BossMetrics.query.delete()
        db.session.add(BossMetrics(
            refreshed_at = m.get('refreshed_at'),
            mtd          = m.get('mtd', {}),
            ytd          = m.get('ytd', {}),
            monthly      = m.get('monthly', []),
        ))
        db.session.commit()
        return jsonify({'ok': True})

    elif ingest_type == 'tasks':
        t = data.get('tasks', {})
        SFTaskData.query.delete()
        db.session.add(SFTaskData(
            refreshed_at = t.get('refreshed_at'),
            date         = t.get('date'),
            completed    = t.get('completed', []),
            scheduled    = t.get('scheduled', []),
            daily_count  = t.get('daily_count', len(t.get('completed', []))),
            weekly_count = t.get('weekly_count', 0),
            week_start   = t.get('week_start'),
        ))
        db.session.commit()
        return jsonify({'ok': True,
                        'completed': len(t.get('completed', [])),
                        'weekly':    t.get('weekly_count', 0),
                        'scheduled': len(t.get('scheduled', []))})

    elif ingest_type == 'team_metrics':
        t = data.get('team_metrics', {})
        TeamMetrics.query.delete()
        db.session.add(TeamMetrics(
            refreshed_at      = t.get('refreshed_at'),
            month             = t.get('month'),
            month_start       = t.get('month_start'),
            reps              = t.get('reps', []),
            monthly_snapshots = t.get('monthly_snapshots', {}),
        ))
        db.session.commit()
        return jsonify({'ok': True, 'reps': len(t.get('reps', [])),
                        'months': len(t.get('monthly_snapshots', {}) or {})})

    elif ingest_type == 'commissions':
        # Upsert (don't delete) — preserve manually-entered fields.
        items = data.get('commissions', [])
        for item in items:
            opp_id = item.get('id')
            if not opp_id:
                continue
            existing = Commission.query.get(opp_id)
            if existing:
                existing.deal_name    = item.get('deal_name')    or existing.deal_name
                existing.account_name = item.get('account_name') or existing.account_name
                existing.close_date   = item.get('close_date')   or existing.close_date
                existing.extracted_at = item.get('extracted_at') or existing.extracted_at
            else:
                db.session.add(Commission(
                    id           = opp_id,
                    deal_name    = item.get('deal_name'),
                    account_name = item.get('account_name'),
                    close_date   = item.get('close_date'),
                    extracted_at = item.get('extracted_at'),
                    true_up_amount = 0,
                ))
        db.session.commit()
        return jsonify({'ok': True, 'count': len(items)})

    return jsonify({'error': 'Unknown ingest type'}), 400


@app.route('/api/sf_lookup')
def sf_lookup():
    """Parse a Salesforce URL and return record details via SF CLI."""
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    # Extract record type + ID from Lightning URL
    # e.g. /lightning/r/Lead/00Q.../view  or  /lightning/r/Opportunity/006.../view
    m = re.search(r'/lightning/r/(\w+)/([A-Za-z0-9]{15,18})(?:/view)?', url)
    if not m:
        return jsonify({'error': 'Could not parse Salesforce URL — paste the full record URL'}), 400

    record_type = m.group(1)   # Lead | Opportunity | Contact | Account
    record_id   = m.group(2)

    SF_ALIAS = 'shift4'

    def run_soql(query):
        result = subprocess.run(
            ['sf', 'data', 'query', '--query', query,
             '--target-org', SF_ALIAS, '--json'],
            capture_output=True, text=True, timeout=20
        )
        data = json.loads(result.stdout)
        if data.get('status') != 0:
            raise RuntimeError(data.get('message', 'SOQL error'))
        return data['result']['records']

    try:
        if record_type == 'Lead':
            rows = run_soql(
                f"SELECT Name, Company, Phone, Email FROM Lead WHERE Id = '{record_id}'"
            )
            if not rows:
                return jsonify({'error': 'Lead not found'}), 404
            r = rows[0]
            return jsonify({
                'name':         r.get('Company') or r.get('Name'),
                'contact_name': r.get('Name'),
                'phone':        r.get('Phone') or '',
                'sf_url':       url,
                'record_type':  'Lead',
            })

        elif record_type == 'Opportunity':
            rows = run_soql(
                f"SELECT Name, Account.Name, ContactId FROM Opportunity WHERE Id = '{record_id}'"
            )
            if not rows:
                return jsonify({'error': 'Opportunity not found'}), 404
            r = rows[0]
            account = r.get('Account') or {}
            biz_name = (account.get('Name') if isinstance(account, dict) else None) or r.get('Name')

            contact_name = ''
            phone = ''
            if r.get('ContactId'):
                contacts = run_soql(
                    f"SELECT Name, Phone, MobilePhone FROM Contact WHERE Id = '{r['ContactId']}'"
                )
                if contacts:
                    c = contacts[0]
                    contact_name = c.get('Name') or ''
                    phone = c.get('Phone') or c.get('MobilePhone') or ''

            return jsonify({
                'name':         biz_name,
                'contact_name': contact_name,
                'phone':        phone,
                'sf_url':       url,
                'record_type':  'Opportunity',
            })

        elif record_type == 'Contact':
            rows = run_soql(
                f"SELECT Name, Phone, MobilePhone, Account.Name FROM Contact WHERE Id = '{record_id}'"
            )
            if not rows:
                return jsonify({'error': 'Contact not found'}), 404
            r = rows[0]
            account = r.get('Account') or {}
            return jsonify({
                'name':         (account.get('Name') if isinstance(account, dict) else None) or '',
                'contact_name': r.get('Name') or '',
                'phone':        r.get('Phone') or r.get('MobilePhone') or '',
                'sf_url':       url,
                'record_type':  'Contact',
            })

        elif record_type == 'Account':
            rows = run_soql(
                f"SELECT Name, Phone FROM Account WHERE Id = '{record_id}'"
            )
            if not rows:
                return jsonify({'error': 'Account not found'}), 404
            r = rows[0]
            return jsonify({
                'name':         r.get('Name') or '',
                'contact_name': '',
                'phone':        r.get('Phone') or '',
                'sf_url':       url,
                'record_type':  'Account',
            })

        else:
            return jsonify({'error': f'Unsupported record type: {record_type}'}), 400

    except FileNotFoundError:
        # SF CLI not available (Railway) — return URL only
        return jsonify({
            'name': '', 'contact_name': '', 'phone': '',
            'sf_url': url, 'record_type': record_type,
            'warning': 'SF CLI not available — fields filled from URL only',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/lead_color', methods=['POST'])
def set_lead_color():
    data  = request.get_json() or {}
    sf_id = data.get('sf_id', '').strip()
    color = data.get('color', '').strip() or None   # empty string → clear
    if not sf_id:
        return jsonify({'error': 'sf_id required'}), 400

    # Persist in LeadColor so it survives re-scans
    lc = LeadColor.query.get(sf_id)
    if color:
        if lc:
            lc.color = color
        else:
            db.session.add(LeadColor(sf_id=sf_id, color=color))
    else:
        if lc:
            db.session.delete(lc)

    # Also update the live RecycledLead row immediately
    rl = RecycledLead.query.get(sf_id)
    if rl:
        rl.color = color

    db.session.commit()
    return jsonify({'ok': True, 'sf_id': sf_id, 'color': color})


@app.route('/api/clear_colors', methods=['POST'])
def clear_lead_colors():
    """
    Bulk clear color tags.
    Body: {"color": "yellow"}   -> clear only yellow-tagged rows
          {"color": "all"} | {} -> clear every color tag
    Returns: {"ok": true, "cleared": N, "color": "yellow"|"all"}
    """
    data  = request.get_json() or {}
    color = (data.get('color') or '').strip().lower()

    valid = {'yellow', 'red', 'blue', 'light_green', 'dark_green', 'purple'}
    if color and color != 'all' and color not in valid:
        return jsonify({'error': f'invalid color: {color}'}), 400

    if not color or color == 'all':
        cleared = LeadColor.query.delete(synchronize_session=False)
        RecycledLead.query.filter(RecycledLead.color.isnot(None)) \
                          .update({'color': None}, synchronize_session=False)
    else:
        cleared = LeadColor.query.filter_by(color=color) \
                                 .delete(synchronize_session=False)
        RecycledLead.query.filter_by(color=color) \
                          .update({'color': None}, synchronize_session=False)

    db.session.commit()
    return jsonify({'ok': True, 'cleared': cleared, 'color': color or 'all'})


# ── Settings ─────────────────────────────────────────────────────────────────

@app.route('/settings')
@app.route('/settings/<section>')
def settings(section='email_templates'):
    templates = {t.slot: t.to_dict()
                 for t in EmailTemplate.query.order_by(EmailTemplate.slot).all()}
    return render_template('settings.html',
        section=section,
        templates=templates,
    )


@app.route('/api/email_template', methods=['POST'])
def save_email_template():
    data  = request.get_json() or {}
    slot  = int(data.get('slot', 0))
    if slot not in (1, 2, 3):
        return jsonify({'error': 'slot must be 1, 2, or 3'}), 400
    t = EmailTemplate.query.filter_by(slot=slot).first()
    if not t:
        t = EmailTemplate(slot=slot)
        db.session.add(t)
    t.name    = data.get('name', '').strip()
    t.subject = data.get('subject', '').strip()
    t.body    = data.get('body', '').strip()
    db.session.commit()
    return jsonify({'ok': True, 'slot': slot})


@app.route('/api/email_templates')
def get_email_templates():
    templates = {str(t.slot): t.to_dict()
                 for t in EmailTemplate.query.order_by(EmailTemplate.slot).all()}
    return jsonify(templates)


# ── Email Queue ────────────────────────────────────────────────────────────────

@app.route('/api/email_queue', methods=['POST'])
def set_email_queue():
    """Set or clear the email template queued for a lead/opp."""
    data  = request.get_json() or {}
    sf_id = data.get('sf_id', '').strip()
    slot  = data.get('slot')   # int 1/2/3 or None/0 to clear
    if not sf_id:
        return jsonify({'error': 'sf_id required'}), 400
    existing = LeadEmailQueue.query.get(sf_id)
    if slot:
        slot = int(slot)
        if existing:
            existing.slot = slot
        else:
            db.session.add(LeadEmailQueue(
                sf_id=sf_id, slot=slot,
                queued_at=datetime.now().isoformat(),
            ))
    else:
        if existing:
            db.session.delete(existing)
    db.session.commit()
    return jsonify({'ok': True, 'sf_id': sf_id, 'slot': slot})


@app.route('/api/email_queue')
def get_email_queue():
    """Return all queued items as {sf_id: slot}."""
    queue = {q.sf_id: q.slot for q in LeadEmailQueue.query.all()}
    return jsonify(queue)


def _resolve_tokens(text, first_name, full_name, company):
    """Replace {first_name}/{full_name}/{company} tokens in template text."""
    if not text:
        return ''
    return (text
        .replace('{first_name}', first_name or '')
        .replace('{full_name}',  full_name  or '')
        .replace('{company}',    company    or ''))


@app.route('/api/email_drafts_data')
def get_email_drafts_data():
    """Return queued items joined with lead + template data, tokens resolved.
    Used by the Claude agent to create Gmail drafts via MCP.
    """
    templates = {t.slot: t for t in EmailTemplate.query.all()}
    queue_rows = LeadEmailQueue.query.all()

    drafts = []
    skipped = []
    for q in queue_rows:
        tpl = templates.get(q.slot)
        if not tpl or not (tpl.subject or tpl.body):
            skipped.append({'sf_id': q.sf_id, 'slot': q.slot, 'reason': 'empty template'})
            continue

        # Look up the record in Lead, RecycledLead, or Opportunity
        lead = Lead.query.get(q.sf_id) or RecycledLead.query.get(q.sf_id)
        opp  = Opportunity.query.get(q.sf_id) if not lead else None

        if lead:
            to_email  = lead.email
            full_name = (lead.name or '').strip()
            company   = (lead.company or '').strip()
        elif opp:
            to_email  = opp.email
            full_name = (opp.contact_name or '').strip()
            company   = (opp.account_name or '').strip()
        else:
            skipped.append({'sf_id': q.sf_id, 'slot': q.slot, 'reason': 'record not found'})
            continue

        if not to_email:
            skipped.append({'sf_id': q.sf_id, 'slot': q.slot, 'reason': 'no email on record'})
            continue

        first_name = full_name.split()[0] if full_name else ''

        drafts.append({
            'sf_id':      q.sf_id,
            'slot':       q.slot,
            'to':         to_email,
            'first_name': first_name,
            'full_name':  full_name,
            'company':    company,
            'subject':    _resolve_tokens(tpl.subject, first_name, full_name, company),
            'body':       _resolve_tokens(tpl.body,    first_name, full_name, company),
        })

    return jsonify({'drafts': drafts, 'skipped': skipped})


@app.route('/api/email_queue/clear', methods=['POST'])
def clear_email_queue():
    """Remove queue entries. JSON: {"sf_ids": [...]} or {"all": true}."""
    data = request.get_json() or {}
    if data.get('all'):
        count = LeadEmailQueue.query.delete()
        db.session.commit()
        return jsonify({'ok': True, 'cleared': count})

    sf_ids = data.get('sf_ids') or []
    if not sf_ids:
        return jsonify({'error': 'sf_ids or all required'}), 400
    count = LeadEmailQueue.query.filter(LeadEmailQueue.sf_id.in_(sf_ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'ok': True, 'cleared': count})


# ── Recycled Intro Auto-Queue ────────────────────────────────────────────────

_RECYCLED_SKIP_KEYWORDS = (
    'call back', 'callback', 'reach out', 'reachout',
    'follow up', 'followup', 'scheduled', 'supposed to',
    'will contact', 'will call', 'reengag', 're engag',
)


def _business_days_ago(n):
    """Return the date n business days (Mon–Fri) before today."""
    d = date.today()
    counted = 0
    while counted < n:
        d = d - timedelta(days=1)
        if d.weekday() < 5:
            counted += 1
    return d


@app.route('/api/queue_recycled_intro', methods=['POST'])
def queue_recycled_intro():
    """Pick up to 20 recycled leads with no recent contact and queue
    Recycled Intro (slot 4) drafts for them."""
    cutoff = _business_days_ago(4)
    already_queued = {q.sf_id for q in LeadEmailQueue.query.all()}

    eligible = []
    skipped = {'no_email': 0, 'recent_contact': 0,
               'callback_keyword': 0, 'already_queued': 0,
               'converted': 0}
    for r in RecycledLead.query.all():
        if r.id in already_queued:
            skipped['already_queued'] += 1
            continue
        if r.is_converted:
            skipped['converted'] += 1
            continue
        if not r.email:
            skipped['no_email'] += 1
            continue
        last = parse_date(r.last_attempt)
        if last and last >= cutoff:
            skipped['recent_contact'] += 1
            continue
        blob = ((r.notes_snippet or '') + ' ' +
                (r.attempt_summary or '')).lower()
        if any(kw in blob for kw in _RECYCLED_SKIP_KEYWORDS):
            skipped['callback_keyword'] += 1
            continue
        eligible.append(r)

    eligible.sort(key=lambda r: parse_date(r.last_attempt) or date.min)
    picked = eligible[:20]

    now = datetime.now().isoformat()
    for r in picked:
        db.session.add(LeadEmailQueue(sf_id=r.id, slot=4, queued_at=now))
    db.session.commit()

    return jsonify({
        'ok': True,
        'queued': len(picked),
        'eligible_total': len(eligible),
        'skipped': skipped,
    })


@app.route('/api/log_email_tasks', methods=['POST'])
def log_email_tasks():
    """Create completed Email tasks in Salesforce for the given leads.

    Expects JSON: {"tasks": [{"sf_id": "...", "subject": "..."}]}
    Uses the SF CLI (alias: shift4) to create Task records.
    """
    SF_ALIAS = 'shift4'
    data = request.get_json() or {}
    tasks = data.get('tasks', [])
    if not tasks:
        return jsonify({'error': 'tasks list required'}), 400

    results = []
    for t in tasks:
        sf_id   = t.get('sf_id', '').strip()
        subject = t.get('subject', 'Email').strip()
        if not sf_id:
            results.append({'sf_id': sf_id, 'ok': False, 'error': 'missing sf_id'})
            continue
        try:
            values = (
                f"WhoId='{sf_id}' "
                f"Subject='{subject}' "
                f"Status='Completed' "
                f"Type='Email'"
            )
            result = subprocess.run(
                ['sf', 'data', 'create', 'record',
                 '--target-org', SF_ALIAS,
                 '--sobject', 'Task',
                 '--values', values],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                results.append({'sf_id': sf_id, 'ok': True})
            else:
                results.append({'sf_id': sf_id, 'ok': False, 'error': result.stderr.strip()})
        except Exception as e:
            results.append({'sf_id': sf_id, 'ok': False, 'error': str(e)})

    return jsonify({'results': results})


# ── Notes pad ─────────────────────────────────────────────────────────────────

@app.route('/api/notes/<note_key>')
def get_notes(note_key):
    row = UserNotes.query.get(note_key)
    return jsonify({'content': row.content if row else ''})


@app.route('/api/notes/<note_key>', methods=['POST'])
def save_notes(note_key):
    data = request.get_json() or {}
    row = UserNotes.query.get(note_key)
    if not row:
        row = UserNotes(note_key=note_key, content='')
        db.session.add(row)
    row.content    = data.get('content', '')
    row.updated_at = datetime.now().isoformat()
    db.session.commit()
    return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
