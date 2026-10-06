"""Lead drip campaign: state and planning for the automated 4 touch email drip.

Targets open, non recycled, inbound leads 7+ days old with no conversation note.
The scheduled Claude task does the Outlook and Salesforce work; this script owns
who is due, what each email says, and where every lead is in the sequence.

State lives in drip/state.json (gitignored, contains contact info).

Usage:
  python3 scripts/lead_drip.py plan              # today's actions as JSON (persists enrollments and auto stops)
  python3 scripts/lead_drip.py record LEAD STEP  # mark a touch sent (live) or drafted (shadow)
  python3 scripts/lead_drip.py stop LEAD REASON  # remove a lead from the drip
  python3 scripts/lead_drip.py status            # summary
"""
import argparse
import datetime
import html
import json
import os
import sys

ROOT = os.environ.get('DRIP_ROOT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEADS_PATH = os.path.join(ROOT, 'dashboard', 'data', 'leads.json')
STATE_PATH = os.path.join(ROOT, 'drip', 'state.json')

DEFAULT_CONFIG = {
    'daily_cap': 25,
    'min_age_days': 7,
    'max_age_days': 180,
    # Drafts only (no sends, no SF tasks) before this date; live sends on and after it.
    'go_live': '2026-10-12',
    # Outbound prospecting leads never asked for info, so the copy doesn't fit them.
    'excluded_source_prefixes': ['Prospecting'],
    'excluded_email_domains': ['shift4.com'],
}

# Days after touch 1 that each touch goes out.
SCHEDULE = {1: 0, 2: 4, 3: 11, 4: 21}
SUBJECT = 'The Shift4 Dine Information You Requested'
BOOKING_LINK = 'https://outlook.office.com/bookwithme/user/b9237b445d1c48e98700ab0d318e60a1@shift4.com?anonymous'

TOUCHES = {
    1: ['Hi {first},',
        "I'm Bryce with Shift4.  Saw you were looking into POS options and wanted to reach out.",
        'Shift4 Dine is built for restaurants, and with our Advantage Program you keep 100% of your card sales.  '
        'Menu programming, install, and 24/7 US based support are all included.',
        'Do you have 10 minutes this week to hop on a quick call?',
        'Thanks,'],
    2: ['Hey {first},',
        "Just wanted to bump this up in your inbox.  If it's easier, I can put together a quick quote instead of a call.",
        'Thanks,'],
    3: ['Hey {first},',
        'Most restaurants I talk to are paying 2.6 to 3.5 percent on every card sale.  '
        'With dual pricing, most of that goes back in your pocket.',
        "If you want to see what it'd look like for you, grab a time here: {link}",
        'Thanks,'],
    4: ['Hey {first},',
        "I wanted to check back in.  If now's not the right time, no worries at all, just let me know.",
        'Thanks,'],
}


def today():
    return datetime.date.today()


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            state = json.load(f)
    else:
        state = {'leads': {}}
    state['config'] = {**DEFAULT_CONFIG, **state.get('config', {})}
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def load_leads():
    with open(LEADS_PATH) as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get('leads', [])
    return {l['id']: l for l in data}


def first_name(full):
    token = (full or '').strip().split(' ')[0]
    return token[:1].upper() + token[1:].lower() if token.isalpha() and len(token) > 1 else 'there'


def render(step, name):
    paras = [p.format(first=first_name(name), link=BOOKING_LINK) for p in TOUCHES[step]]
    text = '\n\n'.join(paras)
    # Browsers collapse double spaces, so keep Bryce's double space after periods with a nbsp.
    body_html = ''.join('<p>%s</p>' % html.escape(p, quote=False).replace('.  ', '.&nbsp; ') for p in paras)
    return text, body_html


def is_live(state, day):
    return day >= datetime.date.fromisoformat(state['config']['go_live'])


def eligible(lead, cfg):
    email = (lead.get('email') or '').strip().lower()
    age = lead.get('lead_age_days')
    return (bool(email) and '@' in email
            and not lead.get('is_recycled')
            and not lead.get('note_count')
            and age is not None and cfg['min_age_days'] <= age <= cfg['max_age_days']
            and not any((lead.get('lead_source') or '').startswith(p) for p in cfg['excluded_source_prefixes'])
            and email.split('@')[-1] not in cfg['excluded_email_domains'])


def stop_entry(entry, reason, day):
    entry.update(status='stopped', stop_reason=reason, stopped_on=day.isoformat())


def action(entry, step):
    text, body_html = render(step, entry['name'])
    return {'lead_id': entry['lead_id'], 'name': entry['name'], 'email': entry['email'],
            'step': step, 'subject': SUBJECT if step == 1 else 'RE: ' + SUBJECT,
            'reply_on_thread': step > 1, 'touch1_sent_on': entry.get('touch1_sent_on'),
            'body_text': text, 'body_html': body_html,
            'sf_task_subject': 'Drip email %d of 4' % step}


def cmd_plan(state, day):
    cfg = state['config']
    leads = load_leads()
    entries = state['leads']
    live = is_live(state, day)
    stopped = []

    # Going live: shadow drafts were never sent, so those leads start over at touch 1.
    if live:
        for e in entries.values():
            if e['status'] == 'shadow':
                e.update(status='active', step_done=0, next_due=day.isoformat())

    # Auto stops from Salesforce data (Outlook reply checks happen in the task).
    for e in entries.values():
        if e['status'] != 'active':
            continue
        lead = leads.get(e['lead_id'])
        if lead is None:
            reason = 'no longer an open lead'
        elif lead.get('note_count'):
            reason = 'conversation note logged'
        elif (lead.get('email') or '').strip().lower() != e['email']:
            reason = 'email changed in Salesforce'
        else:
            continue
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
    new = sorted((l for l in leads.values() if l['id'] not in entries and eligible(l, cfg)),
                 key=lambda l: l['lead_age_days'])
    for lead in new[:max(room, 0)]:
        e = {'lead_id': lead['id'], 'name': lead.get('name') or '', 'email': lead['email'].strip().lower(),
             'company': lead.get('company'), 'enrolled_on': day.isoformat(),
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
        if step == 4:
            e['status'] = 'completed'
        else:
            anchor = datetime.date.fromisoformat(e['touch1_sent_on'])
            e['next_due'] = (anchor + datetime.timedelta(days=SCHEDULE[step + 1])).isoformat()
    save_state(state)
    return e


def cmd_status(state):
    from collections import Counter
    entries = state['leads'].values()
    return {'config': state['config'],
            'by_status': Counter(e['status'] for e in entries),
            'active_by_step': Counter(e['step_done'] for e in entries if e['status'] == 'active'),
            'stop_reasons': Counter(e.get('stop_reason') for e in entries if e['status'] == 'stopped')}


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
    else:
        out = cmd_status(state)
    print(json.dumps(out, indent=2, default=str))


if __name__ == '__main__':
    main()
