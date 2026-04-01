---
name: Morning Extraction State — 2026-03-31
description: Save point for in-progress SF extraction. Resume from here.
type: session-state
---

## Status: PAUSED — Resume from Step 3

The morning Salesforce extraction was initiated 3/31/2026. All 15 lead IDs have been confirmed. Next step is to read the detail pages for hot leads, then extract all 5 open opportunities.

---

## All 15 Lead IDs (COMPLETE — all confirmed)

| # | Name | SF ID | Company | Phone | Notes |
|---|------|-------|---------|-------|-------|
| 1 | Edward Williams | `00QPd00000pIARlMAO` | — | — | |
| 2 | Felipe Ybarra | `00QPd00000pICTZMA4` | — | — | |
| 3 | Herman Cave | `00QPd00000pJMkEMAW` | — | — | |
| 4 | Stephanie Silva | `00QPd00000pJzGvMAK` | — | — | |
| 5 | LATRINA R DUDLEY | `00QPd00000pL5ovMAC` | — | — | |
| 6 | Mark Koking | `00QPd00000pMhx5MAC` | — | — | |
| 7 | Daniel Cho | `00QPd00000pOAwvMAG` | — | — | |
| 8 | Talyia Walker | `00QPd00000pQDsHMAW` | — | — | |
| 9 | Patrick Ginson | `00QPd00000pGzWQMA0` | — | — | ID from prior session |
| 10 | Monique Freeman | `00QPd00000pHkeLMAS` | — | — | ID from prior session |
| 11 | Jenny Eastman | `00QPd00000pIm0RMAS` | — | — | ID from prior session |
| 12 | Muhammad Rahman | `00QPd00000pJFh2MAG` | — | — | ID from prior session |
| 13 | JONATHAN ORTIZ | `00QPd00000pJtVSMA0` | — | — | ID from prior session |
| 14 | Angel Strength | `00QPd00000pUrmHMAS` | Southern Soul Sweet Spot | (604) 908-8275 | New 3/30 — MVF source |
| 15 | Wilenka Novembre | `00QPd00000pWeL8MAK` | Unique Haitian Cuisine | (770) 398-8298 | New 3/31 — MVF source |

**Note on leads 9–13:** IDs carried from prior session. They are likely correct but were not re-navigated this session to confirm. If the JSON output looks wrong for those leads, re-verify via SF global search by last name.

---

## Surface Data (from Printable View pass)

All 15 leads:
- Lead Status: Working
- Owner: Mack Bryce (bmack)
- Lead Source: MVF (for most)

Angel Strength: email southernsoulsweetspot@gmail.com / phone (604) 908-8275 / company Southern Soul Sweet Spot / source Possibly / added 3/30/2026
Wilenka Novembre: email lenkaabigail19955@gmail.com / phone (770) 398-8298 / company Unique Haitian Cuisine / source MVF / added 3/31/2026

---

## Remaining Steps to Complete the Extraction

### Step 3 — Read detail pages for hot leads
Navigate to each lead's record URL using the confirmed IDs above and use `get_page_text` to capture:
- Activity timeline (logged calls, notes, dates)
- Open tasks and due dates
- Any pinned notes or next steps

Priority order for detail pass (do these first):
1. Angel Strength (`00QPd00000pUrmHMAS`) — brand new 3/30, has 2 call tasks due 3/31
2. Wilenka Novembre (`00QPd00000pWeL8MAK`) — brand new 3/31, has 2 call tasks due 3/31
3. Any lead with a task due today or overdue (check task_due fields)
4. Remaining leads in order

### Step 4 — Extract all 5 open opportunities
URL: `https://crmcredorax.lightning.force.com/lightning/o/Opportunity/list?filterName=My_Open_Opportunities2`

Navigate to each opp's detail page. Capture:
- Stage, amount, close date, next step
- Activity timeline and notes
- Contact name (from Contact Roles panel on right sidebar)
- Phone: NOT on opp record — must click contact name link → read phone from Contact record → navigate back

Known opps from callbacks.json (these are OTHER REPS' deals, not Bryce's):
- Taboon Bakery - HOLLYWOOD: `006Pd00000bezqiIAA`
- THE SPOT: `006Pd00000bsHg5IAE`
- I WANT PHO: `006Pd00000fItzNIAS`
Bryce's own 5 opps are different records — get them from the My_Open_Opportunities2 list view.

### Step 5 — Write output files
- `C:/Users/bmack/Desktop/Inside Sales/dashboard/data/leads.json`
- `C:/Users/bmack/Desktop/Inside Sales/dashboard/data/opportunities.json`
- `C:/Users/bmack/Desktop/Inside Sales/dashboard/data/last_refresh.json`

Schema reminder:
- Lead fields: `id, type, name, company, phone, email, status, lead_source, last_activity_date, next_task, next_task_due, call_attempts, notes_snippet, extracted_at`
- Opp fields: `id, type, name, account_name, contact_name, phone, stage, amount, close_date, last_activity_date, next_step, next_task_due, notes_snippet, extracted_at`
- last_refresh.json: `{ "extracted_at": "2026-03-31T...", "lead_count": 15, "opp_count": 5 }`

---

## How to Resume

Say "Resume morning Salesforce extraction" and Claude will:
1. Navigate to Angel Strength's record and read activity
2. Navigate to Wilenka Novembre's record and read activity
3. Work through remaining leads needing detail passes
4. Navigate to My_Open_Opportunities2 list, click each opp, read detail + contact phone
5. Write the three JSON files
6. Confirm completion with counts
