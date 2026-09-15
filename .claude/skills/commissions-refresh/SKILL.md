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

> ## ⚠️ SOURCE MIGRATION UNRESOLVED (flagged 2026-09-04)
> Shift4 decommissioned Google Workspace (2026-08-30, confirmed fully 2026-09-04) and is
> now all-Microsoft. **The Drive MCP steps below may no longer work**, and the commission
> sheets are owned by claire.cai@shift4.com, not Bryce, so their new home is not knowable
> from here.
>
> **On the next "refresh commissions", FIRST try the Microsoft path, then ask Bryce if it fails:**
> 1. `sharepoint_search` / `outlook_email_search` for "Digital Marketing" and
>    "SkyForce Commission History" — Claire may now share them via SharePoint or as
>    Excel attachments.
> 2. If the Drive MCP still resolves the old file IDs (Bryce may retain personal Google
>    access for his own Customers sheet), the legacy path below is still valid.
> 3. Otherwise **ask Bryce where the sheets live now** and whether the Customers sheet
>    moved to Excel/SharePoint. Do not guess, and do not partially ingest — the ingest is
>    delete+replace, so a bad parse wipes good data.
>
> The parsers (`scripts/parse_commission_sheets.py`, `scripts/parse_customers_csv.py`) are
> format-based, not source-based. If you can get the same CSV/text out of SharePoint or an
> `.xlsx`, they still work unchanged.

## Steps

1. Get the sheets:
   - **Customers sheet** (`18MyDeMFwr1p_aAWuQKIxVvwk_3ii8U2Ir2xyX8g1CaE`) — deal registry. Pull it yourself with the Drive MCP `download_file_content` and `exportMimeType: text/csv`, then base64-decode to a file and parse with `scripts/parse_customers_csv.py` → `parse_customers_csv(path)`. Do NOT use `read_file_content` on this sheet — that path truncates around row 159. The CSV export returns all rows, so there is no need to ask Bryce to export it by hand. Only the top deal grid parses; the vendor/prospect/goals scratch below the deals has no MID so it's skipped automatically.
   - **Commission sheets** (owner claire.cai@shift4.com): the monthly **Digital Marketing** files and the **SkyForce Commission History** (`1CLpEVOSjg1WQ4LYJS9UoisEztQ31u4G5`), read via Drive MCP. Use the `Data` section of each DM file (per-MID payout rows) + the full SkyForce stream.
2. Parse payouts with `scripts/parse_commission_sheets.py`: `parse_payouts(text, source)` per sheet, then `dedup_payouts(...)` (DM sheets re-list SkyForce true-ups that flow through their cycle — dedup collapses them). Parse deals with `scripts/parse_customers_csv.py`.
3. POST to Railway `/api/ingest` with `X-API-Key`:
   - `{"type": "payouts", "payouts": [...]}` — delete+replace all payout lines
   - `{"type": "deals", "deals": [...]}` — delete+replace the deal registry
4. Report: payout/deal counts, and any deals with no matching payout (audit candidates) or payout MIDs not in the registry.

## Reference

Payout types: `upfront` ($250 DM / $400 old SkyForce / $200 Shift4 One), `true_up` (can be a negative clawback), `saas` (SkyTab MIDs only), `adjustment`, `upgrade`. MID normalize = strip leading zeros (`models.normalize_mid`) — the Customers sheet zero-pads to 10, the commission sheets don't. Ingest is delete+replace (the sheets are the source of truth; nothing is hand-entered on these pages).
