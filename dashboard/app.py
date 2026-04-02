import json
import os
import uuid
from datetime import date, datetime

from flask import Flask, render_template, request, redirect, url_for, jsonify
from dateutil import parser as dateutil_parser

from config import Config
from models import (db, Lead, Opportunity, Callback, KpiLog,
                    RecycledLead, RefreshLog, SkippedToday)

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    db.create_all()

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

    return render_template('dashboard.html',
        hot=hot, warm=warm, cool=cool, cold=cold,
        total=len(all_records),
        skipped_count=len(skipped),
        refresh_info=refresh_info,
        today_str=today_str,
        stale_days=stale_days,
        callbacks=callbacks,
    )


# ── Pipeline ──────────────────────────────────────────────────────────────────

@app.route('/pipeline')
def pipeline():
    opps = [enrich_record(o.to_dict()) for o in Opportunity.query.all()]
    stages = ['Demo Scheduled', 'Proposal Sent', 'Application', 'Closed Won', 'Other']
    grouped = {s: [] for s in stages}
    for r in opps:
        stage = r.get('stage', '')
        matched = any(s.lower() in stage.lower() and not grouped[s].append(r)
                      for s in stages[:-1])
        if not matched:
            grouped['Other'].append(r)
    return render_template('pipeline.html', grouped=grouped, stages=stages)


# ── KPIs ──────────────────────────────────────────────────────────────────────

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
    category      = request.args.get('category', 'no_contact')
    source_filter = request.args.get('source', '')
    phone_only    = request.args.get('phone_only', '1') == '1'
    converted     = request.args.get('converted', 'leads')  # 'leads' | 'opps' | 'all'

    query = RecycledLead.query.filter_by(category=category)
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

    counts = {
        'no_contact':       RecycledLead.query.filter_by(category='no_contact').count(),
        'no_activity':      RecycledLead.query.filter_by(category='no_activity').count(),
        'had_conversation': RecycledLead.query.filter_by(category='had_conversation').count(),
    }

    # Converted vs lead counts for current category
    base = RecycledLead.query.filter_by(category=category)
    converted_counts = {
        'leads': base.filter_by(is_converted=False).count(),
        'opps':  base.filter(RecycledLead.is_converted == True,
                             RecycledLead.converted_opp_id != None).count(),
        'all':   base.count(),
    }

    log = RefreshLog.query.filter_by(
        refresh_type='recycled').order_by(RefreshLog.id.desc()).first()
    refresh_info = log.to_dict() if log else {}

    return render_template('recycled.html',
        leads=leads,
        counts=counts,
        converted_counts=converted_counts,
        category=category,
        converted=converted,
        source_filter=source_filter,
        phone_only=phone_only,
        sources=sources,
        refresh_info=refresh_info,
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
        RecycledLead.query.delete()
        for item in data.get('leads', []):
            db.session.merge(RecycledLead(**item))

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

    return jsonify({'error': 'Unknown ingest type'}), 400


if __name__ == '__main__':
    app.run(debug=True, port=5000)
