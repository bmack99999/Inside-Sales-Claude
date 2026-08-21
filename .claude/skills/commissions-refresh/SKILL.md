---
name: commissions-refresh
description: Use this skill when Bryce says "refresh commissions", "update my commissions", "pull the commission sheets", "rebuild the book of business", or any variant asking to rebuild the Commissions or Book of Business dashboard pages from the Google Sheets. Claude reads the Drive sheets, parses payouts and deals, and POSTs them to the Railway ingest API.
---

# Commissions Refresh Workflow

The Commissions + Book of Business pages are built on a **MID-keyed model**: a `Deal`
registry (from Bryce's "Customers" Google Sheet) joined to `CommissionPayout` lines
(from the commission sheets) on normalized MID. There is **no Google Drive auth** in
the extraction scripts — Claude is the bridge, same as the Gmail workflow.

## Steps

1. Get the sheets:
   - **Customers sheet** (`18MyDeMFwr1p_aAWuQKIxVvwk_3ii8U2Ir2xyX8g1CaE`) — deal registry. NOTE: the Drive MCP truncates this sheet ~row 159, so ask Bryce to export it as CSV (File > Download > CSV) and parse that with `scripts/parse_customers_csv.py` → `parse_customers_csv(path)`. Only the top deal grid; the vendor/prospect/goals scratch below the deals has no MID so it's skipped automatically.
   - **Commission sheets** (owner claire.cai@shift4.com): the monthly **Digital Marketing** files and the **SkyForce Commission History** (`1CLpEVOSjg1WQ4LYJS9UoisEztQ31u4G5`), read via Drive MCP. Use the `Data` section of each DM file (per-MID payout rows) + the full SkyForce stream.
2. Parse payouts with `scripts/parse_commission_sheets.py`: `parse_payouts(text, source)` per sheet, then `dedup_payouts(...)` (DM sheets re-list SkyForce true-ups that flow through their cycle — dedup collapses them). Parse deals with `scripts/parse_customers_csv.py`.
3. POST to Railway `/api/ingest` with `X-API-Key`:
   - `{"type": "payouts", "payouts": [...]}` — delete+replace all payout lines
   - `{"type": "deals", "deals": [...]}` — delete+replace the deal registry
4. Report: payout/deal counts, and any deals with no matching payout (audit candidates) or payout MIDs not in the registry.

## Reference

Payout types: `upfront` ($250 DM / $400 old SkyForce / $200 Shift4 One), `true_up` (can be a negative clawback), `saas` (SkyTab MIDs only), `adjustment`, `upgrade`. MID normalize = strip leading zeros (`models.normalize_mid`) — the Customers sheet zero-pads to 10, the commission sheets don't. Ingest is delete+replace (the sheets are the source of truth; nothing is hand-entered on these pages).
