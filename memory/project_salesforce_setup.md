---
name: Salesforce Instance Setup
description: SF instance URL, list view URLs, record patterns, and known Chrome MCP limitations for Bryce's SF
type: project
---

Bryce's Salesforce instance is `crmcredorax.lightning.force.com` (redirects to `crmcredorax.my.salesforce.com`).

**Key URLs (confirmed working as of 3/30/2026):**
- My Open Leads list: `https://crmcredorax.lightning.force.com/lightning/o/Lead/list?filterName=My_Open_Leads` (14 leads)
- My Open Opps list: `https://crmcredorax.lightning.force.com/lightning/o/Opportunity/list?filterName=My_Open_Opportunities2` (5 open opps — use this, NOT MyOpportunities)
- `MyOpportunities` returns 50+ records including closed — DO NOT use
- Lead record URL pattern: `https://crmcredorax.lightning.force.com/lightning/r/Lead/[ID]/view`
- Opportunity record URL pattern: `https://crmcredorax.lightning.force.com/lightning/r/Opportunity/[ID]/view`

**SF Reports already set up in Bryce's workspace tabs:**
- Recycled Leads Report: `/lightning/r/Report/00OPd000008afo1MAA/view`
- Qualified Leads - No Recent Activity: `/lightning/r/Report/00OPd00000969D7MAI/view`
- My View - Solution Specialist (Dashboard): `/lightning/r/Dashboard/01ZPd000007vLqTMAU/view`
- Digital Sales Team Close Ratio (Dashboard): `/lightning/r/Dashboard/01ZPd0000084DoTMAU/view`
- Digital Sales Team Dashboard: `/lightning/r/Dashboard/01ZPd000007z6FlMAI/view`

**Salesforce Sidekick:** Extension has been REMOVED from Chrome as of 3/30/2026 — no longer needs to be disabled before extraction. Pre-flight check only requires being logged into Salesforce.

**Chrome MCP Limitation (historical note, Sidekick now gone):**
When Sidekick was active, `computer.screenshot/click/key/scroll` and `javascript_tool` all failed. Removed.

**List view behavior:** SF uses virtual scrolling. Only ~1-3 visible rows render at a time in the MCP DOM. `get_page_text` on list views returns surface data for visible records only. Scrolling via `computer.scroll` or Page Down reveals more rows.

**Navigation:** Single-click on a list row only SELECTS it (highlights blue). Must DOUBLE-CLICK to navigate to the record detail page.

**Phone numbers on Opportunities:** Phone is NOT on the Opportunity record itself. It lives on the Contact record, accessible via the Contact Roles panel on the right sidebar. Must click the contact name link to open their Contact record, read the phone, then navigate back.

**SF REST API:** `fetch('/services/data/v59.0/query/')` in `javascript_tool` returns `INVALID_SESSION_ID`. The Lightning UI uses a different cookie-based session than the REST API. Do not use this approach — use the UI navigation method instead.

**Why:** Documented during March 2026 extraction sessions.
