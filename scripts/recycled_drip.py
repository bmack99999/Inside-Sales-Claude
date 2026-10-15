"""Recycled lead drip: state and planning for the automated 3 touch reconnect email.

Targets unconverted recycled leads that never had a conversation (categories
no_contact and no_activity in dashboard/data/recycled_leads.json).  Leads that
belong to a current inside sales teammate are left alone, and anyone else's
leads must be old enough to count as recycled.  Bryce's own leads are always
fair game.

The scheduled Claude task does the Outlook and Salesforce work; this script owns
who is due, what each email says, and where every lead is in the sequence.

State lives in drip/recycled_state.json (gitignored, contains contact info).

Usage:
  python3 scripts/recycled_drip.py plan              # today's actions as JSON (persists enrollments and auto stops)
  python3 scripts/recycled_drip.py record LEAD STEP  # mark a touch sent (live) or drafted (shadow)
  python3 scripts/recycled_drip.py stop LEAD REASON  # remove a lead from the drip
  python3 scripts/recycled_drip.py status            # summary
  python3 scripts/recycled_drip.py pool              # who is eligible right now, without enrolling anyone
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
from collections import Counter

ROOT = os.environ.get('DRIP_ROOT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECYCLED_PATH = os.path.join(ROOT, 'dashboard', 'data', 'recycled_leads.json')
LEADS_PATH = os.path.join(ROOT, 'dashboard', 'data', 'leads.json')
TEAM_PATH = os.path.join(ROOT, 'dashboard', 'data', 'team_metrics.json')
LEAD_DRIP_STATE_PATH = os.path.join(ROOT, 'drip', 'state.json')
STATE_PATH = os.environ.get('RECYCLED_STATE') or os.path.join(ROOT, 'drip', 'recycled_state.json')

ME = 'Bryce Mack'

DEFAULT_CONFIG = {
    'daily_cap': 15,
    # Someone else's lead has to be at least this old to count as recycled.
    'min_age_days_other_owner': 90,
    # Bryce's own leads only need to be past the fresh lead window.
    'min_age_days_mine': 7,
    # Never touch leads owned by a current inside sales teammate (read from team_metrics.json).
    'exclude_teammate_owned': True,
    # Extra owner names to always skip, e.g. a peer outside the team metrics list.
    'excluded_owners': [],
    'categories': ['no_contact', 'no_activity'],
    'excluded_email_domains': ['shift4.com'],
    # Drafts only (no sends, no SF tasks) before this date; live sends on and after it.
    'go_live': '2099-01-01',
}

# Days after touch 1 that each touch goes out.
SCHEDULE = {1: 0, 2: 7, 3: 21}
LAST_STEP = max(SCHEDULE)
SUBJECT = 'Still looking at POS options?'

# Approved by Bryce 2026-10-06.  Keep verbatim.
TOUCHES = {
    1: ['Hi {first},',
        "I'm Bryce with Shift4.  You were looking into POS options a little while back, so I wanted to check back in.",
        "If you're still shopping around, Shift4 Dine is built for restaurants, and with our Advantage Program "
        'you keep 100% of your card sales.',
        'Worth a quick call to see if it\'s a fit?',
        'Thanks,'],
    2: ['Hey {first},',
        "Just wanted to bump this up.  If it's easier, I can send over a quick quote instead.",
        'Thanks,'],
    3: ['Hey {first},',
        "I wanted to check back in.  If you've already found a system or now's not the right time, "
        'no worries at all, just let me know.',
        'Thanks,'],
}

JUNK_EMAIL = re.compile(r'(noemail|^none@|^test@|^na@|n/a|example\.com)', re.I)


def today():
    return datetime.date.today()


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def load_state():
    state = load_json(STATE_PATH) or {'leads': {}}
    state['config'] = {**DEFAULT_CONFIG, **state.get('config', {})}
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def as_list(data, key):
    if isinstance(data, dict):
        return data.get(key, [])
    return data or []


def load_recycled():
    return {l['id']: l for l in as_list(load_json(RECYCLED_PATH, []), 'leads')}


def open_lead_ids():
    return {l['id'] for l in as_list(load_json(LEADS_PATH, []), 'leads')}


def teammates():
    reps = (load_json(TEAM_PATH, {}) or {}).get('reps', [])
    return {r['name'] for r in reps if r.get('name') and r['name'] != ME}


def lead_drip_emails():
    entries = (load_json(LEAD_DRIP_STATE_PATH, {}) or {}).get('leads', {})
    return {e['email'] for e in entries.values() if e.get('email')}


def first_name(full):
    token = (full or '').strip().split(' ')[0]
    return token[:1].upper() + token[1:].lower() if token.isalpha() and len(token) > 1 else 'there'


def render(step, name):
    paras = [p.format(first=first_name(name)) for p in TOUCHES[step]]
    text = '\n\n'.join(paras)
    # Browsers collapse double spaces, so keep Bryce's double space after periods with a nbsp.
    body_html = ''.join('<p>%s</p>' % html.escape(p, quote=False).replace('.  ', '.&nbsp; ') for p in paras)
    return text, body_html


def is_live(state, day):
    return day >= datetime.date.fromisoformat(state['config']['go_live'])


def lead_age(lead, day):
    created = (lead.get('lead_created') or '')[:10]
    try:
        return (day - datetime.date.fromisoformat(created)).days
    except ValueError:
        return None


def clean_email(lead):
    return (lead.get('email') or '').strip().lower()


def skip_reason(lead, cfg, day, ctx):
    """Why a recycled lead can't be in the drip right now, or None if it can."""
    email = clean_email(lead)
    owner = lead.get('owner_name') or ''
    age = lead_age(lead, day)
    if str(lead.get('is_converted')) == 'True':
        return 'converted'
    if lead.get('category') not in cfg['categories']:
        return 'had a conversation' if lead.get('category') == 'had_conversation' else 'category'
    if not email or '@' not in email or JUNK_EMAIL.search(email):
        return 'no usable email'
    if email.split('@')[-1] in cfg['excluded_email_domains']:
        return 'internal email'
    if lead.get('no_touch'):
        return 'no touch marker'
    if lead['id'] in ctx['open_ids']:
        return 'one of your open leads'
    if email in ctx['lead_drip_emails']:
        return 'in the lead drip'
    if owner in cfg['excluded_owners']:
        return 'excluded owner'
    if owner == ME:
        if age is None or age < cfg['min_age_days_mine']:
            return 'too new'
        return None
    if cfg['exclude_teammate_owned'] and owner in ctx['teammates']:
        return "teammate's lead"
    if age is None or age < cfg['min_age_days_other_owner']:
        return 'too new to be recycled'
    return None


def context():
    return {'open_ids': open_lead_ids(), 'lead_drip_emails': lead_drip_emails(), 'teammates': teammates()}


def stop_entry(entry, reason, day):
    entry.update(status='stopped', stop_reason=reason, stopped_on=day.isoformat())


def action(entry, step):
    text, body_html = render(step, entry['name'])
    return {'lead_id': entry['lead_id'], 'name': entry['name'], 'email': entry['email'],
            'company': entry.get('company'), 'owner': entry.get('owner'),
            'step': step, 'subject': SUBJECT if step == 1 else 'RE: ' + SUBJECT,
            'reply_on_thread': step > 1, 'touch1_message_id': (entry.get('message_ids') or {}).get('1'),
            'touch1_sent_on': entry.get('touch1_sent_on'),
            'body_text': text, 'body_html': body_html, 'sf_task_subject': 'Email'}


def eligible_pool(state, day):
    cfg = state['config']
    ctx = context()
    pool, skipped = [], Counter()
    for lead in load_recycled().values():
        if lead['id'] in state['leads']:
            continue
        reason = skip_reason(lead, cfg, day, ctx)
        if reason:
            skipped[reason] += 1
        else:
            pool.append(lead)
    # Newest first.  Dedupe by email so one person never gets two copies.
    pool.sort(key=lambda l: l.get('lead_created') or '', reverse=True)
    taken = {e['email'] for e in state['leads'].values()}
    unique = []
    for lead in pool:
        email = clean_email(lead)
        if email in taken:
            skipped['duplicate email'] += 1
            continue
        taken.add(email)
        unique.append(lead)
    return unique, skipped


def cmd_plan(state, day):
    cfg = state['config']
    recycled = load_recycled()
    ctx = context()
    entries = state['leads']
    live = is_live(state, day)
    stopped = []

    # Going live: shadow drafts were never sent, so those leads start over at touch 1.
    if live:
        for e in entries.values():
            if e['status'] == 'shadow':
                e.update(status='active', step_done=0, next_due=day.isoformat())

    # Auto stops from the recycled extract (Outlook reply and bounce checks happen in the task).
    for e in entries.values():
        if e['status'] != 'active':
            continue
        lead = recycled.get(e['lead_id'])
        if lead is None:
            reason = 'no longer on the recycled list'
        elif clean_email(lead) != e['email']:
            reason = 'email changed in Salesforce'
        else:
            reason = skip_reason(lead, cfg, day, {**ctx, 'lead_drip_emails': set()})
            if reason in ('too new', 'too new to be recycled'):
                reason = None
        if reason:
            stop_entry(e, reason, day)
            stopped.append({'lead_id': e['lead_id'], 'name': e['name'], 'reason': reason})

    actions = []
    if live:
        due = sorted((e for e in entries.values()
                      if e['status'] == 'active' and e['next_due'] <= day.isoformat()),
                     key=lambda e: (e['step_done'] == 0, e['next_due']))
        for e in due[:cfg['daily_cap']]:
            actions.append(action(e, e['step_done'] + 1))

    # Fill the rest of today's cap with new enrollments, newest leads first.
    room = cfg['daily_cap'] - len(actions)
    pool, _ = eligible_pool(state, day)
    for lead in pool[:max(room, 0)]:
        e = {'lead_id': lead['id'], 'name': lead.get('name') or '', 'email': clean_email(lead),
             'company': lead.get('company'), 'owner': lead.get('owner_name'),
             'lead_created': (lead.get('lead_created') or '')[:10], 'enrolled_on': day.isoformat(),
             'status': 'active', 'step_done': 0, 'next_due': day.isoformat()}
        entries[lead['id']] = e
        actions.append(action(e, 1))

    save_state(state)
    return {'date': day.isoformat(), 'mode': 'live' if live else 'shadow',
            'actions': actions, 'auto_stopped': stopped}


def cmd_record(state, lead_id, step, day, message_id=None):
    e = state['leads'][lead_id]
    if not is_live(state, day):
        e.update(status='shadow', shadow_drafted_on=day.isoformat())
    else:
        if step != e['step_done'] + 1:
            sys.exit('lead %s is at step %d, cannot record step %d' % (lead_id, e['step_done'], step))
        if step == 1:
            e['touch1_sent_on'] = day.isoformat()
        e['step_done'] = step
        e['last_sent_on'] = day.isoformat()
        e.setdefault('message_ids', {})[str(step)] = message_id
        if step == LAST_STEP:
            e['status'] = 'completed'
        else:
            anchor = datetime.date.fromisoformat(e['touch1_sent_on'])
            e['next_due'] = (anchor + datetime.timedelta(days=SCHEDULE[step + 1])).isoformat()
    save_state(state)
    return e


def cmd_status(state, day):
    entries = state['leads'].values()
    pool, skipped = eligible_pool(state, day)
    return {'config': state['config'],
            'mode_today': 'live' if is_live(state, day) else 'shadow',
            'by_status': Counter(e['status'] for e in entries),
            'active_by_step': Counter(e['step_done'] for e in entries if e['status'] == 'active'),
            'stop_reasons': Counter(e.get('stop_reason') for e in entries if e['status'] == 'stopped'),
            'not_yet_enrolled': len(pool)}


def cmd_pool(state, day):
    pool, skipped = eligible_pool(state, day)
    ages = Counter()
    for lead in pool:
        a = lead_age(lead, day) or 0
        ages['under 90 days' if a < 90 else '90 to 179' if a < 180 else '180 to 364' if a < 365 else '365+'] += 1
    return {'eligible': len(pool), 'by_owner': Counter(l.get('owner_name') for l in pool).most_common(),
            'by_age': ages, 'skipped': skipped.most_common(),
            'first_15': [{'name': l.get('name'), 'company': l.get('company'), 'created': (l.get('lead_created') or '')[:10],
                          'owner': l.get('owner_name')} for l in pool[:15]]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--today', help='override date (YYYY-MM-DD) for testing')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('plan')
    r = sub.add_parser('record')
    r.add_argument('lead_id')
    r.add_argument('step', type=int)
    r.add_argument('--message-id')
    s = sub.add_parser('stop')
    s.add_argument('lead_id')
    s.add_argument('reason')
    sub.add_parser('status')
    sub.add_parser('pool')
    args = p.parse_args()

    day = datetime.date.fromisoformat(args.today) if args.today else today()
    state = load_state()
    if args.cmd == 'plan':
        out = cmd_plan(state, day)
    elif args.cmd == 'record':
        out = cmd_record(state, args.lead_id, args.step, day, args.message_id)
    elif args.cmd == 'stop':
        e = state['leads'][args.lead_id]
        stop_entry(e, args.reason, day)
        save_state(state)
        out = e
    elif args.cmd == 'pool':
        out = cmd_pool(state, day)
    else:
        out = cmd_status(state, day)
    print(json.dumps(out, indent=2, default=str))


if __name__ == '__main__':
    main()
