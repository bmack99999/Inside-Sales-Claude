---
name: commissions-refresh
description: Use this skill when Bryce says "refresh commissions", "update my commissions", "pull the commission sheets", "rebuild the book of business", or any variant asking to rebuild the Commissions or Book of Business dashboard pages from the Google Sheets. Claude reads the Drive sheets, parses payouts and deals, and POSTs them to the Railway ingest API.
---

# Commissions Refresh Workflow

> **2026-09-15:** `/commissions` was rebuilt as an inside sales deal tracker (`TrackedDeal`,
> dashboard is the source of truth, see CLAUDE.md). This skill still owns the **payout
> sheets → `CommissionPayout`** path, which the tracker joins by MID. The Customers sheet
> → `Deal` registry now only feeds Book of Business; do not use it to add inside sales
> deals, Bryce adds those on the page.

The Commissions + Book of Business pages are built on a **MID-keyed model**: a `Deal`
registry (from Bryce's "Customers" sheet) joined to `CommissionPayout` lines
(from the commission sheets) on normalized MID. There is no sheet auth in
the extraction scripts — Claude is the bridge.

> ## SOURCE RESOLVED 2026-09-21 — statements live in Mike Seymour's OneDrive
> **The commission statements are published here, and Bryce does NOT get them by email:**
>
> `mseymour_shift4corp_com` → `Hospitality ED_EM_PD Commissions` → `Digital Marketing Sales Team` → `Bryce Mack (SF Transfer to DMS - 3_20_2026)`
>
> driveId `b!4FxXtYw65kKygZChfi2XxHi7dPr0dqRHi8ZgVvXH4KQnj7AMri1yQrpPENmjSj5E`
>
> Read the folder with `read_resource` on
> `file:///{driveId}/Hospitality ED_EM_PD Commissions/Digital Marketing Sales Team/Bryce Mack (SF Transfer to DMS - 3_20_2026)`
> then `read_resource` the individual `.xlsx` — the workbook comes back as parsed sheets,
> no download needed. **Caution:** the folder listing concatenates each item id directly
> onto the next filename with no delimiter, so ids are easy to truncate by one character.
> If a read 400s with "Invalid request", you clipped the id; re-read the folder.
>
> Files are named `Digital Marketing Commission YYYYMM02 (Final) - Bryce Mack.xlsx`, one
> per month, plus the legacy `SkyForce Commission 20260402` and `SkyForce Commission History`.
> Search hint: a plain "commission" SharePoint search drowns in European Global Blue
> marketing decks — search `SkyForce` or go straight to the path above.
>
> **The merchant portal export Bryce still supplies himself** (`Merchants_ST4DM.CSV` in
> ~/Downloads). Ask for a fresh one each cycle; the parsers are format based so CSV or
> XLSX both work.
>
> Prior guidance said Bryce supplies everything and no hunting is needed. That is still
> true for the portal export, but the statements are now self serve — check OneDrive for
> a newer month before asking him.

## Monthly refresh: the two files Bryce provides (updated 2026-09-16)

Bryce now supplies TWO files each cycle. Use both; they answer different questions.

### 1. Merchant portal export — `Merchants_ST4DM.xlsx`
Shift4's own merchant list. **Authoritative on account status**, more so than
email or Salesforce. One row per MID. Columns that matter:

| Column | Use |
|---|---|
| `Merchant ID` | join key. Normalize with `models.normalize_mid` (strip leading zeros) |
| `Status Code` | **the whole point.** 700 = Approved processing = the comp plan go live threshold. 600 = Approved but not processing yet. 200 Declined, 300 Closed merchant cancelled, 325 Closed termination = dead |
| `Status Name` | human label for the note |
| `Software Product Group` | Shift4 Dine / Shift4 One / Standalone Terminal. Shift4 One and Standalone earn NO device SaaS and the $200 upfront rather than $250 |
| `Advantage Program` | Included / Not Included |
| `Last Batch Date` | was empty in the 9/16 export; if populated later it is the best "actually transacting" signal |

Map status code to tracker status:
- 700 → `live`
- 600 → leave as is if pre install; do not mark live, it has not processed
- 200 / 300 / 325 → `cancelled`, with `stall_reason` naming the status
Record the portal status as a timeline note on every matched deal.

**Trust this over Salesforce `Start_Processing_Date__c`.** That field means an
expected or scheduled date and is often in the future; status 700 means the MID
actually processed. The 9/16 reconciliation found 5 disagreements across 77
matched deals, and the portal was right in every case.

### 2. Commission statements (Digital Marketing + SkyForce)
Same parsers as before: `scripts/parse_commission_sheets.py` then
`dedup_payouts()`, POST as `{"type":"payouts", ...}` (delete and replace).

### Order of operations
1. Ingest payouts first, so real money overrides every estimate.
2. Apply the merchant portal statuses.
3. Re-run the back test in the calibration section below and update
   `cost_basis_pct` if the sample has grown.
4. Report: new payouts, status changes, deals now live, deals now dead, and the
   updated projection.

### Calibration (re-do each cycle — but read this first)
`cost_basis_pct` in `deal_tracker.py` is 2.73%. To recalibrate: for every deal with a
settled true up and a known volume, implied cost basis =
`rate_pct - ((true_up + upfront) / 2 / mo_volume * 100)`. Actuals live under the deal's
`paid` block (`paid.true_up`, `paid.upfront`), NOT in top level `true_up_actual` fields.

**Do not just take the median.** The 2026-09-21 pass (n=9, median 2.82) showed that
number is an artifact of bad volume estimates, not cost. Always cross check each deal's
stored `mo_volume` against the **actual processed volume** in the statement's Analysis
sheet (`2026MM Processing Vol` column) before trusting an implied figure:

- Any implied cost basis **above the card rate** (e.g. >4% on a 4% dual pricing deal) is
  mathematically impossible and means the volume is overstated, not that costs are high.
- On 9/21 the only deal whose estimate matched actuals (Corner Bar, 0.98x) implied 2.71 —
  right on the current setting. The rest ran 0.00x to 0.73x of estimate. Earnestines was
  booked at $30k/mo and processed $42.
- **Conclusion: 2.73 held.** The model's error is volume capture at disco, not cost basis.
  Raising the parameter would fit it to volume error and degrade well estimated deals.

Only move `cost_basis_pct` when deals with *verified accurate* volumes disagree with it.

## Reference

Payout types: `upfront` ($250 DM / $400 old SkyForce / $200 Shift4 One), `true_up` (can be a negative clawback), `saas` (SkyTab MIDs only), `adjustment`, `upgrade`. MID normalize = strip leading zeros (`models.normalize_mid`) — the Customers sheet zero-pads to 10, the commission sheets don't. Ingest is delete+replace (the sheets are the source of truth; nothing is hand-entered on these pages).
