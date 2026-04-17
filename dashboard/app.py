import json
import os
import re
import subprocess
import uuid
from datetime import date, datetime

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
                    LeadEmailQueue, UserNotes)

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
        ("sf_task_data",   "weekly_count", "INTEGER DEFAULT 0"),
        ("sf_task_data",   "week_start",   "TEXT"),
        ("sf_task_data",   "daily_count",  "INTEGER DEFAULT 0"),
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

def score_record(record):
    score = 0

    age = days_since(record.get('last_activity_date'))
    if age <= 1:
        score += 35
    elif age <= 3:
        score += 25
    elif age <= 7:
        score += 15
    elif age <= 14:
        score += 8

    due_in = days_until(record.get('next_task_due'))
    if due_in < 0:
        score += 28
    elif due_in == 0:
        score += 30
    elif due_in == 1:
        score += 20

    rec_type = record.get('type', 'lead')
    if rec_type == 'opportunity':
        stage = (record.get('stage') or '').lower()
        if 'application' in stage:
            score += 20
        elif 'proposal' in stage:
            score += 16
        elif 'demo' in stage:
            score += 14
        elif 'closed won' in stage:
            score += 5
    else:
        status = (record.get('status') or '').lower()
        if 'connected' in status:
            score += 15
        elif 'nurturing' in status:
            score += 10
        elif 'attempted' in status:
            score += 8
        elif 'new' in status:
            score += 5

    attempts = record.get('call_attempts', 0) or 0
    if 1 <= attempts <= 3 and days_since(record.get('last_activity_date')) <= 7:
        score += 10

    if rec_type == 'lead':
        status = (record.get('status') or '').lower()
        active_statuses = ('new', 'working', 'attempted', 'connected', 'nurturing')
        if any(s in status for s in active_statuses):
            age = days_since(record.get('last_activity_date'))
            if age >= 21:
                score -= 15
            elif age >= 14:
                score -= 10
            elif age >= 7:
                score -= 5

    return max(0, score)


def badge_tier(score):
    if score >= 70:
        return ('HOT', 'badge-hot')
    elif score >= 40:
        return ('WARM', 'badge-warm')
    elif score >= 10:
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

@app.route('/')
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
                 for c in Callback.query.all()]

    log = RefreshLog.query.filter_by(
        refresh_type='salesforce').order_by(RefreshLog.id.desc()).first()
    refresh_info = log.to_dict() if log else {}

    today_str = date.today().strftime('%A, %B %d, %Y').replace(' 0', ' ')
    stale_days = data_staleness_days()

    # Build enriched leads/opps for the My Leads table embedded on this page
    color_map = {lc.sf_id: lc.color for lc in LeadColor.query.all()}
    all_leads = []
    for l in Lead.query.order_by(Lead.last_activity_date.asc().nullsfirst()).all():
        d = l.to_dict()
        d['color']  = color_map.get(l.id)
        d['timezone'] = get_phone_tz(l.phone)
        d['sf_url'] = f"{SF_BASE}/lightning/r/Lead/{l.id}/view"
        all_leads.append(d)
    all_opps = []
    for o in Opportunity.query.order_by(Opportunity.last_activity_date.asc().nullsfirst()).all():
        d = o.to_dict()
        d['color']  = color_map.get(o.id)
        d['timezone'] = get_phone_tz(o.phone)
        d['sf_url'] = f"{SF_BASE}/lightning/r/Opportunity/{o.id}/view"
        all_opps.append(d)

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
        leads.append(d)

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
        opps.append(d)

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


# ── KPIs ──────────────────────────────────────────────────────────────────────

@app.route('/workflow')
def workflow():
    return render_template('workflow.html', sf_base=SF_BASE)


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


@app.route('/api/daily_workflow')
def api_daily_workflow():
    """Generate a prioritized daily workflow: opps, callbacks, recycled leads."""
    from datetime import timedelta, timezone as tz
    utc_now = datetime.now(tz.utc)
    et_offset = timedelta(hours=-4)  # EDT (Apr–Nov)
    now = utc_now + et_offset
    today_str = now.date().isoformat()

    # ── Focus Opportunities ──────────────────────────────────────────────
    opps = [enrich_record(o.to_dict()) for o in Opportunity.query.all()]
    active_opps = [o for o in opps
                   if not o.get('stage', '').lower().startswith('closed')]
    focus_opps = sorted(active_opps, key=lambda x: x['_score'], reverse=True)

    # ── Focus Leads (active, non-recycled) ───────────────────────────────
    leads = [enrich_record({**l.to_dict(), 'type': 'lead'})
             for l in Lead.query.all()]
    focus_leads = sorted(leads, key=lambda x: x['_score'], reverse=True)

    # ── Callbacks due/overdue ────────────────────────────────────────────
    callbacks = [enrich_callback(c.to_dict()) for c in Callback.query.all()
                 if not c.do_not_call]
    cb_due = sorted(
        [c for c in callbacks if c.get('_task_overdue') or c.get('_task_due_today')],
        key=lambda x: x.get('task_due') or '9999'
    )
    cb_upcoming = sorted(
        [c for c in callbacks if not c.get('_task_overdue') and not c.get('_task_due_today')],
        key=lambda x: x.get('task_due') or '9999'
    )

    # ── Recycled Leads (pick ~50) ────────────────────────────────────────
    # Determine timezone preference based on time of day (ET business hours)
    hour = now.hour
    if hour < 12:
        tz_pref = ['ET', 'CT']       # morning: call Eastern/Central first
    elif hour < 15:
        tz_pref = ['CT', 'MT', 'ET'] # midday: Central/Mountain
    else:
        tz_pref = ['MT', 'PT', 'CT'] # afternoon: Mountain/Pacific

    colors = {lc.sf_id: lc.color for lc in LeadColor.query.all()}

    all_recycled = RecycledLead.query.all()
    candidates = []
    for r in all_recycled:
        d = r.to_dict() if hasattr(r, 'to_dict') else {
            c.name: getattr(r, c.name) for c in r.__table__.columns
        }
        # Filter: must have phone, not converted, not colored red
        if not d.get('phone'):
            continue
        if d.get('is_converted'):
            continue
        color = colors.get(d['id']) or d.get('color')
        if color == 'red':
            continue

        # Score recycled leads for prioritization
        rscore = 0

        # Category weight
        cat = d.get('category', '')
        if cat == 'had_conversation':
            rscore += 30
        elif cat == 'no_contact':
            rscore += 20
        elif cat == 'no_activity':
            rscore += 10

        # Timezone match bonus
        tz = d.get('timezone') or get_phone_tz(d.get('phone'))
        if tz in tz_pref:
            rscore += 15 + (len(tz_pref) - tz_pref.index(tz)) * 5

        # Fewer attempts = more opportunity
        attempts = d.get('attempt_count') or 0
        if attempts == 0:
            rscore += 10
        elif attempts <= 3:
            rscore += 5

        # Recency bonus (if had conversation, prefer recent)
        if cat == 'had_conversation' and d.get('last_activity_date'):
            days = days_since(d['last_activity_date'])
            if days <= 7:
                rscore += 10
            elif days <= 30:
                rscore += 5

        # Has email = extra touchpoint available
        if d.get('email'):
            rscore += 3

        # Color bonus (user flagged as interesting)
        if color in ('light_green', 'dark_green', 'yellow'):
            rscore += 8

        d['_recycle_score'] = rscore
        d['_timezone'] = tz
        d['_color'] = color
        candidates.append(d)

    # Sort by score desc, take top 50
    candidates.sort(key=lambda x: x['_recycle_score'], reverse=True)
    recycled_picks = candidates[:50]

    return jsonify({
        'generated_at': now.strftime('%Y-%m-%d %I:%M %p') + ' ET',
        'focus_opps': focus_opps[:10],
        'focus_leads': focus_leads[:10],
        'callbacks_due': cb_due,
        'callbacks_upcoming': cb_upcoming[:5],
        'recycled_picks': recycled_picks,
        'recycled_total_candidates': len(candidates),
        'tz_preference': tz_pref,
    })


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

    leads = [l.to_dict() for l in query.all()]

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
            refreshed_at = t.get('refreshed_at'),
            month        = t.get('month'),
            month_start  = t.get('month_start'),
            reps         = t.get('reps', []),
        ))
        db.session.commit()
        return jsonify({'ok': True, 'reps': len(t.get('reps', []))})

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
