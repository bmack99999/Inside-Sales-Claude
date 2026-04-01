import json
import os
import subprocess
import uuid
from datetime import date, datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify
from dateutil import parser as dateutil_parser

app = Flask(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


def load_json(filename, default=None):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return default if default is not None else []
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, default=str)


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


def get_skipped_today():
    """Return set of record IDs skipped today."""
    data = load_json('skipped_today.json', {})
    if data.get('date') != date.today().isoformat():
        return set()
    return set(data.get('ids', []))


def save_skipped_today(ids):
    save_json('skipped_today.json', {
        'date': date.today().isoformat(),
        'ids': list(ids)
    })


def score_record(record):
    score = 0

    # --- Recency score (0-35) ---
    age = days_since(record.get('last_activity_date'))
    if age <= 1:
        score += 35
    elif age <= 3:
        score += 25
    elif age <= 7:
        score += 15
    elif age <= 14:
        score += 8

    # --- Task due score (0-30) ---
    due_in = days_until(record.get('next_task_due'))
    if due_in < 0:
        score += 28   # overdue
    elif due_in == 0:
        score += 30   # due today
    elif due_in == 1:
        score += 20   # due tomorrow

    # --- Status / stage score (0-20) ---
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

    # --- Momentum score (0-10) ---
    attempts = record.get('call_attempts', 0) or 0
    if 1 <= attempts <= 3 and days_since(record.get('last_activity_date')) <= 7:
        score += 10

    # --- Staleness penalty (0 to -15) ---
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


def data_staleness_days():
    """How many days since last SF refresh."""
    info = load_json('last_refresh.json', {})
    raw = info.get('refreshed_at', '')
    if not raw or raw == 'Sample data loaded':
        return None
    try:
        dt = dateutil_parser.parse(raw)
        return (datetime.now() - dt).days
    except Exception:
        return None


def enrich_callback(cb):
    """Add computed display fields to a callback record."""
    added = days_since(cb.get('added_date'))
    cb['_days_since_added'] = added if added != 9999 else 0

    due_in = days_until(cb.get('task_due'))
    cb['_task_overdue'] = due_in < 0
    cb['_task_due_today'] = due_in == 0

    last_called = days_since(cb.get('last_call_date'))
    cb['_days_since_call'] = last_called if last_called != 9999 else None

    return cb


@app.route('/')
def dashboard():
    leads = load_json('leads.json', [])
    opps = load_json('opportunities.json', [])
    refresh_info = load_json('last_refresh.json', {})
    skipped = get_skipped_today()
    callbacks = [enrich_callback(cb) for cb in load_json('callbacks.json', [])]

    all_records = []
    for r in leads:
        r['type'] = r.get('type', 'lead')
        if r.get('id') not in skipped:
            all_records.append(enrich_record(r))
    for r in opps:
        r['type'] = r.get('type', 'opportunity')
        if r.get('id') not in skipped:
            all_records.append(enrich_record(r))

    all_records.sort(key=lambda x: x['_score'], reverse=True)

    hot  = [r for r in all_records if r['_tier_label'] == 'HOT']
    warm = [r for r in all_records if r['_tier_label'] == 'WARM']
    cool = [r for r in all_records if r['_tier_label'] == 'COOL']
    cold = [r for r in all_records if r['_tier_label'] == 'COLD']

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


@app.route('/pipeline')
def pipeline():
    opps = load_json('opportunities.json', [])
    for r in opps:
        r['type'] = 'opportunity'
        enrich_record(r)

    stages = ['Demo Scheduled', 'Proposal Sent', 'Application', 'Closed Won', 'Other']
    grouped = {s: [] for s in stages}
    for r in opps:
        stage = r.get('stage', '')
        matched = False
        for s in stages[:-1]:
            if s.lower() in stage.lower():
                grouped[s].append(r)
                matched = True
                break
        if not matched:
            grouped['Other'].append(r)

    return render_template('pipeline.html', grouped=grouped, stages=stages)


@app.route('/kpis')
def kpis():
    kpi_log = load_json('kpi_log.json', [])
    return render_template('kpis.html', kpi_log=kpi_log)


@app.route('/api/kpis')
def api_kpis():
    kpi_log = load_json('kpi_log.json', [])
    return jsonify(kpi_log)


@app.route('/api/records')
def api_records():
    leads = load_json('leads.json', [])
    opps  = load_json('opportunities.json', [])
    for r in leads:
        r['type'] = r.get('type', 'lead')
        enrich_record(r)
    for r in opps:
        r['type'] = r.get('type', 'opportunity')
        enrich_record(r)
    return jsonify(leads + opps)


@app.route('/log_kpi', methods=['POST'])
def log_kpi():
    kpi_log = load_json('kpi_log.json', [])
    entry = {
        'date': date.today().isoformat(),
        'dials':        int(request.form.get('dials', 0)),
        'connects':     int(request.form.get('connects', 0)),
        'voicemails':   int(request.form.get('voicemails', 0)),
        'demos_set':    int(request.form.get('demos_set', 0)),
        'applications': int(request.form.get('applications', 0)),
        'closes':       int(request.form.get('closes', 0)),
        'logged_at': datetime.now().isoformat()
    }
    kpi_log = [e for e in kpi_log if e.get('date') != entry['date']]
    kpi_log.append(entry)
    save_json('kpi_log.json', kpi_log)
    return redirect(url_for('dashboard'))


@app.route('/log_call', methods=['POST'])
def log_call():
    """Log a call outcome for a record. Updates last_activity_date and notes."""
    record_id  = request.form.get('record_id')
    outcome    = request.form.get('outcome', 'Attempted')   # Connected / Voicemail / No Answer / Attempted
    notes      = request.form.get('notes', '').strip()
    next_task  = request.form.get('next_task', '').strip()
    next_due   = request.form.get('next_task_due', '').strip()

    today = date.today().isoformat()

    for filename in ('leads.json', 'opportunities.json'):
        records = load_json(filename, [])
        changed = False
        for r in records:
            if r.get('id') == record_id:
                r['last_activity_date'] = today
                r['last_activity_type'] = 'Call'
                if outcome == 'Connected':
                    if r.get('type') == 'lead':
                        r['status'] = 'Connected'
                    r['call_attempts'] = (r.get('call_attempts') or 0) + 1
                elif outcome in ('Voicemail', 'No Answer', 'Attempted'):
                    if r.get('type') == 'lead' and (r.get('status') or '').lower() in ('new', ''):
                        r['status'] = 'Attempted'
                    r['call_attempts'] = (r.get('call_attempts') or 0) + 1
                if notes:
                    r['last_call_notes'] = notes
                    r['notes_snippet'] = notes[:120] + ('...' if len(notes) > 120 else '')
                if next_task:
                    r['next_task'] = next_task
                    r['next_step'] = next_task
                if next_due:
                    r['next_task_due'] = next_due
                changed = True
                break
        if changed:
            save_json(filename, records)

    return redirect(url_for('dashboard'))


@app.route('/add_record', methods=['POST'])
def add_record():
    """Manually add a lead or opportunity."""
    rec_type = request.form.get('type', 'lead')
    today = date.today().isoformat()

    if rec_type == 'opportunity':
        record = {
            'id': 'manual-' + str(uuid.uuid4())[:8],
            'type': 'opportunity',
            'name': request.form.get('name', '').strip(),
            'account_name': request.form.get('company', '').strip(),
            'contact_name': request.form.get('contact_name', '').strip(),
            'phone': request.form.get('phone', '').strip(),
            'stage': request.form.get('stage', 'Demo Scheduled'),
            'amount': 0,
            'close_date': request.form.get('close_date', '').strip() or None,
            'last_activity_date': today,
            'last_activity_type': None,
            'next_step': request.form.get('next_task', '').strip() or None,
            'next_task_due': request.form.get('next_task_due', '').strip() or None,
            'days_in_stage': 0,
            'probability': 0,
            'notes_snippet': request.form.get('notes', '').strip() or None,
            'last_call_notes': None,
            'activity_summary': None,
            'next_agreed_step': request.form.get('next_task', '').strip() or None,
            'open_tasks': [],
            'extracted_at': datetime.now().isoformat(),
            'manually_added': True,
        }
        records = load_json('opportunities.json', [])
        records.append(record)
        save_json('opportunities.json', records)
    else:
        record = {
            'id': 'manual-' + str(uuid.uuid4())[:8],
            'type': 'lead',
            'name': request.form.get('name', '').strip(),
            'company': request.form.get('company', '').strip(),
            'phone': request.form.get('phone', '').strip(),
            'email': request.form.get('email', '').strip() or None,
            'status': request.form.get('status', 'New'),
            'lead_source': request.form.get('lead_source', 'Manual Entry'),
            'lead_age_days': 0,
            'last_activity_date': today,
            'last_activity_type': None,
            'next_task': request.form.get('next_task', '').strip() or None,
            'next_task_due': request.form.get('next_task_due', '').strip() or None,
            'call_attempts': 0,
            'city': None, 'state': None,
            'notes_snippet': request.form.get('notes', '').strip() or None,
            'last_call_notes': None,
            'activity_summary': None,
            'next_agreed_step': None,
            'open_tasks': [],
            'is_recycled': False,
            'extracted_at': datetime.now().isoformat(),
            'manually_added': True,
        }
        records = load_json('leads.json', [])
        records.append(record)
        save_json('leads.json', records)

    return redirect(url_for('dashboard'))


@app.route('/skip_today', methods=['POST'])
def skip_today():
    """Hide a record for the rest of today."""
    record_id = request.form.get('record_id')
    skipped = get_skipped_today()
    skipped.add(record_id)
    save_skipped_today(skipped)
    return redirect(url_for('dashboard'))


@app.route('/refresh')
def refresh():
    script = os.path.join(os.path.dirname(__file__), '..', 'extract_salesforce.py')
    if os.path.exists(script):
        subprocess.Popen(['python3', script])
    return redirect(url_for('dashboard'))


# ── Callback Routes ──────────────────────────────────────────────────────────

@app.route('/add_callback', methods=['POST'])
def add_callback():
    """Add a manual callback request (someone else's SF opp)."""
    today = date.today().isoformat()
    cb = {
        'id': 'cb-' + str(uuid.uuid4())[:8],
        'name':         request.form.get('name', '').strip(),
        'contact_name': request.form.get('contact_name', '').strip() or None,
        'phone':        request.form.get('phone', '').strip() or None,
        'sf_url':       request.form.get('sf_url', '').strip() or None,
        'task_due':     request.form.get('task_due', '').strip() or None,
        'notes':        request.form.get('notes', '').strip() or None,
        'do_not_call':  False,
        'added_date':   today,
        'last_call_date': None,
        'last_call_notes': None,
        'call_attempts': 0,
    }
    callbacks = load_json('callbacks.json', [])
    callbacks.append(cb)
    save_json('callbacks.json', callbacks)
    return redirect(url_for('dashboard'))


@app.route('/log_callback_call', methods=['POST'])
def log_callback_call():
    """Log call notes / outcome for a callback entry."""
    cb_id      = request.form.get('cb_id')
    notes      = request.form.get('notes', '').strip()
    task_due   = request.form.get('task_due', '').strip()
    do_not_call = request.form.get('do_not_call') == '1'

    callbacks = load_json('callbacks.json', [])
    for cb in callbacks:
        if cb.get('id') == cb_id:
            cb['last_call_date']  = date.today().isoformat()
            cb['call_attempts']   = (cb.get('call_attempts') or 0) + 1
            if notes:
                cb['last_call_notes'] = notes
            if task_due:
                cb['task_due'] = task_due
            cb['do_not_call'] = do_not_call
            break
    save_json('callbacks.json', callbacks)
    return redirect(url_for('dashboard'))


@app.route('/delete_callback', methods=['POST'])
def delete_callback():
    """Remove a callback entry."""
    cb_id = request.form.get('cb_id')
    callbacks = load_json('callbacks.json', [])
    callbacks = [cb for cb in callbacks if cb.get('id') != cb_id]
    save_json('callbacks.json', callbacks)
    return redirect(url_for('dashboard'))


SF_BASE = "https://crmcredorax.lightning.force.com"


@app.route('/recycled')
def recycled():
    leads = load_json('recycled_leads.json', [])
    refresh_info = load_json('recycled_refresh.json', {})

    # Filter by category
    category = request.args.get('category', 'no_contact')
    source_filter = request.args.get('source', '')
    phone_only = request.args.get('phone_only', '1') == '1'

    filtered = [l for l in leads if l.get('category') == category]
    if phone_only:
        filtered = [l for l in filtered if l.get('phone')]
    if source_filter:
        filtered = [l for l in filtered if l.get('lead_source') == source_filter]

    # Get unique lead sources for filter dropdown
    sources = sorted(set(l.get('lead_source', '') for l in leads if l.get('lead_source')))

    # Count by category
    counts = {
        'no_contact': sum(1 for l in leads if l.get('category') == 'no_contact'),
        'no_activity': sum(1 for l in leads if l.get('category') == 'no_activity'),
        'had_conversation': sum(1 for l in leads if l.get('category') == 'had_conversation'),
    }

    return render_template('recycled.html',
        leads=filtered,
        counts=counts,
        category=category,
        source_filter=source_filter,
        phone_only=phone_only,
        sources=sources,
        refresh_info=refresh_info,
        sf_base=SF_BASE,
    )


@app.route('/refresh_recycled')
def refresh_recycled():
    script = os.path.join(os.path.dirname(__file__), '..', 'extract_recycled.py')
    if os.path.exists(script):
        subprocess.Popen(['python3', script])
    return redirect(url_for('recycled'))


if __name__ == '__main__':
    app.run(debug=True, port=5000)
