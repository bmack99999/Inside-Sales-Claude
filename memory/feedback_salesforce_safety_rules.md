---
name: Salesforce Safety Rules
description: Hard rules for what Claude must NEVER do in Salesforce — set by Bryce explicitly
type: feedback
---

Never perform any of the following actions in Salesforce, even if it seems helpful or the script/workflow calls for it:

1. **Never delete a lead** — no matter the reason
2. **Never set a lead status to "Unqualified"** — Bryce does not use this status
3. **Never delete an opportunity**
4. **Never change an opportunity's Stage to "Closed"** (Closed Won, Closed Lost, or any closed variant)
5. **Never change any field on an opportunity** unless Bryce explicitly asks in that session

**Why:** Bryce gave these instructions explicitly before going to bed. These are non-negotiable data integrity rules for his sales pipeline. Violating them could corrupt his Salesforce data and harm his performance metrics.

**How to apply:** Before taking any write/update action in Salesforce via Chrome MCP, verify the action does not fall into any of the above categories. If a workflow step could potentially modify these fields, skip it and note it for the user.
