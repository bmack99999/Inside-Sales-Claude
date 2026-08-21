---
name: accountability-cleanup
description: Use this skill when Bryce asks to clean up his Salesforce accountability dashboard, prep for his bi-weekly accountability score, or says any of "accountability cleanup", "clean my dashboard", "prep for accountability", "score is running", "tile cleanup". The accountability dashboard runs every other Wednesday morning — this skill bulk-fixes the red tiles before the snapshot.
---

# Salesforce Accountability Dashboard Cleanup

Bryce's accountability score is computed every other Wednesday morning from a dashboard of red/orange/green tiles. Most red tiles measure SF hygiene (open tasks per record, expired dates, etc.), not sales output. This skill systematically clears them.

## Critical Identifiers

- **Bryce's User ID:** `005Pd0000084UhFIAU`
- **SF CLI alias:** `shift4` — always pass `--target-org shift4 --json`
- **Dashboard ID:** `01ZPd000008P9LFMA0` ("My View — Inside Sales Account Executive")
- **Org instance URL:** `https://crmcredorax.my.salesforce.com`

## Cadence

- Score runs **every other Wednesday morning**.
- Run this skill **Tuesday afternoon or evening before** so the next-morning snapshot picks up the changes.
- Confirm the exact run date with Bryce; he tracks it.

## The Critical Task Pattern

The accountability dashboard filters open tasks by `Status = 'Open'`. Tasks created with `Status = 'Not Started'` are invisible to the report — they will NOT clear the tiles. Use this exact field combination on every task created:

| Field | Value | Notes |
|---|---|---|
| `Status` | `Open` | NOT "Not Started" |
| `Type` | `Call` | Required for dashboard recognition |
| `TaskSubtype` | `Task` | **Read-only after creation — must be set in the CREATE payload** |
| `Subject` | empty string | Matches SF Lightning UI default |
| `Priority` | `Normal` | |
| `IsReminderSet` | `true` | |
| `OwnerId` | `005Pd0000084UhFIAU` | Bryce |

### Linking the task to the right record

| Record type | Set `WhoId` | Set `WhatId` |
|---|---|---|
| Lead | Lead Id | (leave blank) |
| Opportunity | Primary Contact Id from OpportunityContactRole | Opp Id |
| Account (Prospect) | (leave blank) | Account Id |

For opps, fetch the primary contact via:
```
SELECT OpportunityId, ContactId, Contact.Name, IsPrimary FROM OpportunityContactRole WHERE Opportunity.OwnerId = '005Pd0000084UhFIAU' AND Opportunity.IsClosed = false
```
Pick `IsPrimary = true` if any; else first row.

## The Six Red Tiles and Their Cleanup

### Tile 1 — Leads with No Open Task

**SOQL to find them** (semi-joins on Task aren't allowed; pull all + diff in Python):
```
sf data query --query "SELECT Id, Name, Company FROM Lead WHERE OwnerId = '005Pd0000084UhFIAU' AND IsConverted = false AND Status = 'Working'" --target-org shift4 --json
sf data query --query "SELECT WhoId FROM Task WHERE OwnerId = '005Pd0000084UhFIAU' AND IsClosed = false AND WhoId != null" --target-org shift4 --json
```
Diff: leads whose Id is not in the WhoId set.

**Fix:** Create one task per lead with `WhoId = Lead.Id`, `ActivityDate = 2026-07-01` (or a near-future date — confirm with Bryce). Use the critical task pattern above.

### Tile 2 — Open Opportunities with No Open Task

**SOQL:**
```
sf data query --query "SELECT Id, Name, StageName FROM Opportunity WHERE OwnerId = '005Pd0000084UhFIAU' AND IsClosed = false" --target-org shift4 --json
sf data query --query "SELECT WhatId FROM Task WHERE OwnerId = '005Pd0000084UhFIAU' AND IsClosed = false AND WhatId != null" --target-org shift4 --json
```
Diff: opps whose Id is not in the WhatId set.

**Fix:** For each opp, fetch its primary contact (see above). Create task with `WhoId = Contact.Id` AND `WhatId = Opp.Id`, `ActivityDate = 2026-07-01` (or per Bryce's instruction).

### Tile 3 — Prospects with No Task Scheduled

The dashboard report is "My Prospects w/o Open Task - Legal" (report ID `00OPd000007jQ6UMAU`). It targets `Account` records (not Leads/Opps) where:
- `AccountStage__c = 'Prospect'`
- `RecordType.Name = 'Legal account'`
- `Substage__c NOT IN ('Former Customer','Not Pursuing','Pending Enterprise Agreement','Permanently Closed','Rejected by Underwriting')`
- AND has no future Activity (cross-filter, ActivityDate >= TODAY)
- AND has no open Opp not in Closed Won/Lost (cross-filter)

**SOQL:**
```
sf data query --query "SELECT Id, Name, Substage__c FROM Account WHERE OwnerId = '005Pd0000084UhFIAU' AND AccountStage__c = 'Prospect' AND RecordType.Name = 'Legal account' AND (NOT Substage__c IN ('Former Customer','Not Pursuing','Pending Enterprise Agreement','Permanently Closed','Rejected by Underwriting'))" --target-org shift4 --json

sf data query --query "SELECT AccountId FROM Opportunity WHERE Account.OwnerId = '005Pd0000084UhFIAU' AND IsClosed = false AND StageName NOT IN ('Closed Won','Closed Lost')" --target-org shift4 --json

sf data query --query "SELECT AccountId FROM Task WHERE Account.OwnerId = '005Pd0000084UhFIAU' AND ActivityDate >= TODAY AND AccountId != null" --target-org shift4 --json
```
Diff: prospects NOT in either the opp-account set or the active-task-account set.

**Fix:** Bryce's directive — these are essentially dead-weight prospects. Bury them by creating a task with `WhatId = Account.Id`, `ActivityDate = 2035-01-01` (far future). One task per account. Use the critical task pattern.

### Tile 4 — Due/Overdue Tasks

**SOQL:**
```
sf data query --query "SELECT Id, Subject, ActivityDate, WhoId, Who.Name, WhatId, What.Name FROM Task WHERE OwnerId = '005Pd0000084UhFIAU' AND IsClosed = false AND ActivityDate < TODAY ORDER BY ActivityDate ASC" --target-org shift4 --json
```

**Fix:** Bryce's directive — push all to `ActivityDate = <original date + 30 days>`. Compute each task's new date individually (original ActivityDate + 30 calendar days). Use composite PATCH on the Task object.

⚠️ **Flag any task subjects that look like genuine action items** in the summary back to Bryce (e.g., "Demo Scheduled", "Call Scheduled", "Contract", "FU", named follow-ups). The "Update Expired Close Date" and "Closed Won/Lost Check In" tasks are pure noise — safe to bury permanently. Real action items should be surfaced so Bryce can rescue them later.

### Tile 5 — Closed Won, Not Live

**SOQL:**
```
sf data query --query "SELECT Id, Name, CloseDate, Expected_go_live_date__c, Go_Live_Check__c FROM Opportunity WHERE OwnerId = '005Pd0000084UhFIAU' AND StageName = 'Closed Won' AND Go_Live_Check__c = false" --target-org shift4 --json
```

**Fix:** Per-record judgment required. Ask Bryce per opp whether it actually went live:
- If yes: update `Go_Live_Check__c = true` and set actual `Expected_go_live_date__c` to the real go-live date.
- If no (future install): push `Expected_go_live_date__c` to a realistic future date.

Do NOT bulk-flip without per-record approval — these affect post-sale tracking and onboarding emails.

### Tile 6 — Expired Go-Live Dates

Subset of Tile 5. Same fix.

## Execution Flow

1. **Pull current dashboard state** via the Analytics REST API to confirm the count on each red tile. Use:
   ```
   curl -H "Authorization: Bearer $TOK" "https://crmcredorax.my.salesforce.com/services/data/v60.0/analytics/dashboards/01ZPd000008P9LFMA0"
   ```
   Get the token via:
   ```
   sf org display --target-org shift4 --verbose --json
   ```
   Pull `.result.accessToken`.

2. **Summarize the dashboard** back to Bryce: tile-by-tile, current count, color, target after cleanup.

3. **For each tile, confirm the cleanup pattern with Bryce before executing**:
   - "Leads with No Open Task" — confirm target ActivityDate (default July 1 of current year).
   - "Open Opps with No Open Task" — confirm target ActivityDate.
   - "Prospects with No Task Scheduled" — confirm 2035-01-01 (far future bury).
   - "Due/Overdue Tasks" — confirm +30 days from each task's current ActivityDate.
   - "Closed Won, Not Live" / "Expired Go-Live Dates" — walk through per-record.

4. **Execute via the Salesforce Composite API** for batch operations. The endpoint:
   ```
   POST  https://crmcredorax.my.salesforce.com/services/data/v60.0/composite/sobjects   (create batch)
   PATCH https://crmcredorax.my.salesforce.com/services/data/v60.0/composite/sobjects   (update batch)
   ```
   Use `allOrNone: false`. Max 200 records per request.

5. **Verify** by re-running each tile's SOQL and confirming count = 0 (or expected).

6. **Report results back to Bryce** in a table: tile, before, after, status. Flag any failures, any genuinely-actionable tasks that got buried, and any per-record items that still need attention (Tile 5/6).

## Composite API payload template (create)

```json
{
  "allOrNone": false,
  "records": [
    {
      "attributes": {"type": "Task"},
      "WhoId": "<id-or-omit>",
      "WhatId": "<id-or-omit>",
      "OwnerId": "005Pd0000084UhFIAU",
      "ActivityDate": "2026-07-01",
      "Status": "Open",
      "Type": "Call",
      "TaskSubtype": "Task",
      "Priority": "Normal",
      "IsReminderSet": true
    }
  ]
}
```

## Composite API payload template (update / push date)

```json
{
  "allOrNone": false,
  "records": [
    {
      "attributes": {"type": "Task"},
      "id": "<TaskId>",
      "ActivityDate": "<original ActivityDate + 30 days>"
    }
  ]
}
```

Note: `TaskSubtype` cannot be updated after creation. If a task needs the right subtype and was created wrong, the only fix is delete + recreate.

## Salesforce Safety Rules — ABSOLUTE

These come from Bryce's CLAUDE.md. **Never violate them, even when executing this cleanup**:

1. Never delete a lead.
2. Never set a lead status to "Unqualified".
3. Never delete an opportunity.
4. Never change an opportunity Stage to any Closed variant.
5. Never modify any Salesforce record beyond what this skill explicitly authorizes (task create/update with the documented pattern). Anything else needs Bryce's per-session explicit approval.

This skill ONLY:
- Creates Task records on Leads/Opps/Accounts.
- Updates `ActivityDate` on existing Tasks.
- For Tile 5/6 (Go-Live), updates Opportunity fields ONLY after per-record Bryce approval.

Never updates Lead.Status, Opportunity.StageName, Account anything, or any other field.

## Reporting Format

End every run with a table like this so Bryce can see the score impact:

| Tile | Before | After | Notes |
|---|---:|---:|---|
| 🔴 Leads — No Open Task | 18 | 0 | ✅ Created N tasks |
| 🔴 Open Opps — No Open Task | 19 | 0 | ✅ Created N tasks |
| 🔴 Prospects — No Task Scheduled | 86 | 0 | ✅ Buried to 2035 |
| 🔴 Due/Overdue Tasks | 41 | 0 | ✅ Buried to 2035 |
| 🔴 Closed Won, Not Live | 30 | 30 | Needs per-record review |
| 🔴 Expired Go-Live Dates | 16 | 16 | Needs per-record review |

And flag any overdue tasks that were pushed +30 days that may still need attention (genuine follow-ups, demos, contracts, scheduled calls). Prospects buried to 2035 are intentional — no flag needed.
