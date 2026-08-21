---
name: sf-activity-recap
description: Use this skill when Bryce asks "what did I do today", "what did I do this week", or wants a recap of his own Salesforce activity (tasks, calls, notes, lead/opp changes). Pulls live from Salesforce via the sf CLI — never from local JSON — and renders the required recap layout with timestamps and specifics.
---

# Daily / Weekly Activity Recap

**Always pull live from Salesforce — do not rely on local JSON for activity counts.** Local files lag and miss Tasks/Notes from the current day.

Get Bryce's user ID once: `005Pd0000084UhFIAU` (Bryce Mack)

Required data sources (SOQL via `sf data query --target-org shift4 --json`):
1. **Tasks** — `Task` where `OwnerId=$USER_ID` and `(CreatedDate=TODAY OR LastModifiedDate=TODAY OR ActivityDate=TODAY)` — fields: `Subject, Type, CallType, Status, ActivityDate, LastModifiedDate, Who.Name, What.Name, Description`
2. **Notes** — `ContentNote` where `CreatedById=$USER_ID and CreatedDate=TODAY` — fields: `Title, TextPreview, CreatedDate`
3. **Leads modified** — `Lead` where `OwnerId=$USER_ID and LastModifiedDate=TODAY` — fields: `Name, Company, Status, LastModifiedDate, LastActivityDate`
4. **Opps modified** — `Opportunity` where `OwnerId=$USER_ID and LastModifiedDate=TODAY` — fields: `Name, StageName, CloseDate, LastModifiedDate` *(NOTE: `Amount` field does NOT exist on this Opportunity object — do not query it)*

For weekly: swap `TODAY` for `THIS_WEEK` or use date range `LastModifiedDate >= 2026-MM-DD AND LastModifiedDate < 2026-MM-DD`.

Briefing structure (use this layout):
1. **Activity totals** — task count, calls, emails, notes, leads/opps modified, MTD leaderboard rank
2. **Calls / Outreach timeline** — table by time with contact, account, result
3. **Notes written** — quote the actual note content
4. **Lead/Opp changes** — group by: Converted to Qualified, Closed Lost, Status changes, Stage moves, UW progress
5. **Boss tasks waiting** — overdue close-date update tasks etc.
6. **Open follow-ups** still on the board
7. **Did you miss anything before week's end** — concrete punchlist

Tone: factual, direct, no fluff. Bryce wants to see exactly what he touched, with timestamps and specifics — not vague summaries.

**Closed Won and Underwriting Review opps are essentially done from a sales perspective** — Bryce already worked them. Do NOT list them as discos, follow-ups needed, or "things to touch." The only time these matter in a briefing is when there is an active **underwriting issue** (UW Hold, missing voided check, missing corp doc, KYC issue, principals info needed, etc.) — surface those in a dedicated "UW issues to resolve" section if they exist.

Active sales-stage opps to report on = stages: **Conversations, Trending Positively, Proposal Sent, Agreement Sent**. Closed Won / Closed Lost / Underwriting Review = post-sale, exclude from action items.
