---
name: deal-status-monitor
description: Use this skill when Bryce says "update my deal statuses", "check my installs", "scan email for install dates", "sync the commissions page", "any stalled onboardings", or as a standing step inside the daily briefing's Onboarding to Install section. Reads Outlook for Shift4 onboarding, kickoff, install, go live, and not processing emails, matches them to deals on the Commissions page (Railway deal tracker), and pushes status, date, and specialist updates plus a timeline note through the tracker API. Never touches Salesforce.
---

# Deal Status Monitor

The Commissions page (`/commissions` on Railway) is the source of truth for every
deal Bryce closed on the inside sales team. Each deal has a `status`
(`signed → onboarding → install_scheduled → installed → live`, plus `stalled` /
`cancelled`), dates, device counts, and a timeline. Real payouts join by MID and
override the status automatically (upfront paid, trued up). This skill keeps the
pre payout part of that pipeline current by reading email.

## 1. Load the tracker

```
GET https://web-production-980e0.up.railway.app/api/tracked_deals
```

No auth needed for reads. Use `deals[]`: `id`, `site`, `mid`, `mid_raw`, `status`,
`stage`, `install_scheduled_date`, `install_date`, `go_live_date`,
`specialist_name`, `days_since_sign`, `risks[]`, `events[]`. Only deals with
`stage` in `signed`, `onboarding`, `install_scheduled`, `installed`, `live`,
`stalled` need attention. Skip `complete` and `cancelled`.

## 2. Search Outlook (Microsoft MCP only, never Gmail)

Use `outlook_email_search` with `afterDateTime` = the newest `events[].at` with
`source = email_monitor` across all deals, or the last 14 days on a first run.
Run each of these queries, then `read_resource` on the `uri` of anything that
matches a tracked deal:

| Signal | Query | Sender pattern | Meaning |
|---|---|---|---|
| Onboarding started | `Welcome to Shift4 Dine` | `*@shift4.com` pre launch specialist | status → `onboarding`, capture `specialist_name` / `specialist_email` from the sender |
| Specialist assigned | `Meet Your Shift4 Pre-Launch Specialist` | `*@shift4.com` | same as above (body says whose deal only in the Welcome email; cross check by date) |
| Kickoff / install date | `Kickoff Recap`, `Kick off Call` | specialist | status → `install_scheduled`, `install_scheduled_date` = "Tentative Install Date/Time" in the body |
| Menu presentation | `Menu Presentation` (Gemini notes) | `gemini-notes@google.com` | body often confirms the install appointment; treat as `install_scheduled` if it names a date |
| Reschedule / delay | `reschedule`, `postpone`, `pushed` in a kickoff thread | specialist or merchant | update `install_scheduled_date`; add a note |
| Installed | `install complete`, `installation completed`, `went live`, `is live` | specialist / installer | status → `installed` (`install_date`) or `live` (`go_live_date`) |
| Not processing | `Not Processing Transactions` | `bryce.delaney@shift4.com` forward or `*@shift4.com` | subject carries `| MID: 00xxxxxxxx`. Deal is installed but not live: add a risk note, set `status = stalled` with `stall_reason` = "Installed, not processing" ONLY if it is already `installed` and more than 14 days have passed |
| Merchant cold feet / cancel | `cancel`, `hold off`, `not moving forward` from the merchant | merchant | do NOT auto cancel. Add a note and flag it to Bryce |

Subjects carry the DBA in ALL CAPS (`Welcome to Shift4 Dine - MAD FISH ST PETE
BEACH`). Match to `site` case insensitively, ignoring punctuation and words like
"the", "llc", "inc". If the subject or body has a MID, match on `mid` instead
(normalize by stripping leading zeros). If nothing matches, list it under
"unmatched" in the report instead of guessing.

## 3. Push updates

```
POST https://web-production-980e0.up.railway.app/api/tracked_deals/<id>
Content-Type: application/json
{
  "status": "install_scheduled",
  "install_scheduled_date": "2026-09-22",
  "specialist_name": "Matt Curry",
  "specialist_email": "matt.curry@shift4.com",
  "_source": "email_monitor",
  "_event_note": "Kickoff recap 9/10: tentative install Tue Sep 22, 9:00 AM CT"
}
```

Rules:
- Only send the fields you actually learned. Blank keys overwrite.
- Never move a status backwards (`installed` must not become `onboarding`).
  Order: signed < onboarding < install_scheduled < installed < live.
- `_event_note` should quote the email date and the fact, one line. It shows on
  the deal timeline with an "email monitor" tag.
- One POST per deal per run, even if several emails matched. Combine facts.
- To add a note without changing anything: `POST /api/tracked_deals/<id>/events`
  with `{"note": "...", "kind": "email", "source": "email_monitor"}`.
- Never delete deals. Never set `cancelled` from email. Never write to Salesforce.

## 4. Report back

Short list, one line per deal touched: site, what changed, the email it came
from. Then "unmatched" emails (DBA + subject) and "stalled candidates" (deals
where `risks[]` is non empty and no email was found). Keep it terse, no hyphens.

## Notes

- Dine deals are worth $250 at go live; everything else $200. True up lands
  roughly three months after go live, so a slipped install date moves money
  across a pay period. Say so when an install moves months.
- New deals Bryce closes should be added on the page itself ("+ New deal", paste
  the Salesforce opp URL). If an email references a deal that is not tracked yet,
  tell Bryce rather than creating it.
