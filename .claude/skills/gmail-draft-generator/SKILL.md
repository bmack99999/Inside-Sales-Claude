---
name: gmail-draft-generator
description: Use this skill when Bryce says "generate my email drafts," "create my drafts," "make my email drafts," "draft my emails," "draft the opp target emails," "send drafts to Outlook," "process the email queue," or any variant — including when he asks for outreach emails to specific leads/recycled leads or the converted-opp targets he checked off on the Opp Targets dashboard page. Reads the queued drafts from his Railway dashboard (template queue or the Opp Targets draft queue), creates them in Outlook via MCP, and clears the queue. Also handles ad-hoc bulk email drafting (UW chases, Trending Positively revivals, stale Conversation breakups, recycled lead outreach) following Bryce's style rules.
---

# Gmail Draft Generator

> **NAME IS HISTORICAL — drafts go to OUTLOOK.** Shift4 is all-Microsoft as of 2026-08-30 (confirmed fully 2026-09-04: email, calendar, Teams, files). Google Workspace is decommissioned.
>
> **Use `mcp__75618fea-6128-4002-ab3e-adf2307c8a58__outlook_create_draft`** for new drafts, and **`outlook_create_reply_draft`** when following up on an existing thread so it threads correctly.
>
> **NEVER call the Gmail MCP** (`mcp__03fa365f-...__create_draft`). That mailbox is dead; drafts created there are invisible to Bryce.
>
> Everywhere below that says "Gmail," read "Outlook." The style rules, queue mechanics, and templates are unchanged.

This skill covers two related jobs:

1. **Queue-driven workflow** — Bryce queues drafts on the dashboard, this skill creates them in Outlook.
2. **Ad-hoc bulk drafting** — Bryce asks for outreach to a specific cohort (UW chases, breakup emails, recycled revival, etc.), this skill drafts each one with the right tone/format and pushes to Outlook.

Both paths share the same style rules and `outlook_create_draft` mechanics.

## Critical Identifiers

- **Bryce's email:** `bryce.mack@shift4.com`
- **Bryce's User ID (Salesforce):** `005Pd0000084UhFIAU`
- **Railway URL:** `https://web-production-980e0.up.railway.app`
- **Railway API key:** `d219d2be8540f1d079dd896937fbd8fe41c9754ab955629cf74d43068e99d36d` (header `X-API-Key`)
- **Draft tool:** `mcp__75618fea-6128-4002-ab3e-adf2307c8a58__outlook_create_draft` (new) / `outlook_create_reply_draft` (thread replies). NOT the Gmail MCP.

## Bryce's Email Style Rules — ABSOLUTE

These come from his MEMORY.md and apply to **every email this skill drafts**:

### Style Rules

1. **NEVER use hyphens.** Rephrase compound words. Examples:
   - "card present" not "card-present"
   - "3 year term" not "3-year term"
   - "food truck" with no hyphen
   - "US based" not "U.S.-based"
   - **No em dashes or en dashes either.** Use periods, commas, or "to" for ranges.

2. **NEVER include a signature block.** Shift4's server side signature service stamps it on send. End every email with exactly:
   ```
   Thanks,
   Bryce
   ```
   Do NOT append "Bryce Mack / Solution Specialist II / phone / email" — the server side service adds it.

3. **Subject lines are short, low-pressure.** No ALL CAPS, no emojis, no aggressive sales language. Examples that work:
   - "Following up"
   - "SkyTab POS — quick question"
   - "Quick check in re: <business name>"
   - "<First name> — quick note"

4. **Tone is consultative, not aggressive.** Bryce sells SkyTab POS to restaurants/retail. He's helpful, not pushy.

### Content / Length Rules

5. **Short emails.** 3 to 6 sentences max for cold and warm outreach. Long emails kill response rates.

6. **One ask per email.** Either a callback, a quick reply, or a meeting time — never all three.

7. **Personalize when possible.** If the lead has notes, industry, or named context, weave it in naturally.

## SkyTab Value Props (use sparingly, when relevant)

When drafting cold/warm outreach, lean on these:

- **Advantage Program (dual pricing)** — 99% merchant adoption rate, offsets processing costs
- **$0 hardware year 1** (waived when using Advantage)
- **$29.99/device/month** hardware after year 1
- **Lighthouse software** — $20/mo
- **Included with every install:** menu programming, install, training, 24/7 US based support (no hyphen!)
- **No long term contract trap.** Month to month options available.

## Tier Logic (Cold → Warm → Break Up)

For ad-hoc cohort drafting, match the email type to the lead's call attempt count:

| Tier | Call Attempts | Email Type | Tone |
|---|---:|---|---|
| Fresh | 0 to 3 | First touch / intro | Curious, low pressure. Ask if they're open to a quick chat. |
| Active follow up | 4 to 6 | Re-engage with value | Short reminder, restate one value prop, ask for callback or quick reply. |
| Final push | 7 to 9 | Soft pre-breakup | "Wanted to try one more time before I close this out." |
| Break up | 10+ | Last email | Polite goodbye. Door left open. No CTA pressure. |

### Sample tone per tier

**Fresh (0–3):**
> Hi <First Name>,
> Saw you reached out about POS for <Company>. I'm with Shift4 / SkyTab.
> Would a quick 10 minute call work to see if we're a fit? I can also send pricing over if that's easier.
> Thanks,
> Bryce

**Final push (7–9):**
> Hi <First Name>,
> Wanted to try once more before closing this out on my end. If timing isn't right, no worries — happy to circle back later.
> If you're still open to seeing what SkyTab looks like for <Company>, just hit reply with a time and I'll work around your schedule.
> Thanks,
> Bryce

**Break up (10+):**
> Hi <First Name>,
> I haven't been able to catch you so I'll stop reaching out for now. If POS comes back on your radar down the road, just send me a note.
> Wishing you the best with <Company>.
> Thanks,
> Bryce

## Industry Variants (when relevant)

Tailor the lead-in sentence by industry when industry is known:

- **Pizza:** "How are things going at the shop?" / mention dough sheeters, online order routing, slice tracking
- **Food truck:** "Hope the season's been busy" / mention mobile setup, single terminal package, no contract
- **Bar:** "Hope the weekend rushes are going well" / mention tabs, tab transfers, server tipouts
- **Retail:** "Hope the shop's been busy" / mention inventory tracking, integrated payments, no separate terminal
- **Coffee:** "Hope the morning rushes are smooth" / mention QSR speed, customer display, modifiers
- **Fine dining / full service:** "Hope service has been going well" / mention coursing, tableside ordering, KDS

## Workflow A: Queue-Driven (the standard ask)

When Bryce says "generate my email drafts" or similar:

1. **Pull the queue:**
   ```
   GET https://web-production-980e0.up.railway.app/api/email_drafts_data
   ```
   No auth needed for read (confirm if 401/403). Returns `{drafts: [...], skipped: [...]}` with `{first_name}`, `{full_name}`, `{company}` tokens already resolved server-side.

2. **For each draft in the response:**
   Call `outlook_create_draft` with:
   ```
   to:      [draft.to]              # array, even for single recipient
   subject: draft.subject
   body:    draft.body
   ```
   Catch any MCP errors per draft — keep going on the rest.

3. **Clear the queue once all drafts are created:**
   ```
   POST https://web-production-980e0.up.railway.app/api/email_queue/clear
   Header: X-API-Key: d219d2be8540f1d079dd896937fbd8fe41c9754ab955629cf74d43068e99d36d
   Body: {"all": true}
   ```

4. **Report back to Bryce:**
   - N drafts created successfully
   - Any skipped items from the original response (with reason)
   - Any per-draft MCP errors
   - Confirmation queue was cleared

### Important: don't re-edit queue drafts

The drafts coming from the queue are pre-rendered by Bryce's chosen templates. Do NOT rewrite them. Pass through verbatim. The style rules above apply only when *I* am drafting (Workflow B).

## Workflow B: Ad-Hoc Bulk Drafting

When Bryce asks for outreach to a cohort like:
- "Draft UW chase emails for my open UW opps"
- "Send breakup emails to my stale Conversations"
- "Draft revival emails for Trending Positively"
- "Email the cold proposals"
- "Reach out to the recycled bucket with X industry"

### Procedure

1. **Identify the cohort** by SOQL from Salesforce. Common ones:
   - **UW chases:** `Opportunity` where `StageName = 'Underwriting Review'`, owned by Bryce, with primary contact email.
   - **Stale Conversations:** `Opportunity` where `StageName = 'Conversations'` and `LastActivityDate < TODAY - 14 days`.
   - **Cold Proposal Sent:** same as above but `StageName = 'Proposal Sent'` and 14+ days quiet.
   - **Trending Positively revival:** `StageName = 'Trending Positively'` with no recent touch.
   - **Recycled by industry:** `Lead` with industry filter from `dashboard/data/recycled_leads.json`.

2. **Pull the contact email** for each record. For opps, use `OpportunityContactRole` to find the primary contact, then query that Contact's Email.

3. **Determine tier per record** (for leads, use `call_attempts`; for opps, use stage + age in stage).

4. **Draft each email** following:
   - Style rules (no hyphens, no signature, "Thanks, Bryce" close)
   - Tier-appropriate tone
   - Industry variant if industry is known
   - Personalization (first name, company, any note context from `notes_snippet`)
   - One ask per email

5. **Show Bryce 2 to 3 sample drafts BEFORE sending the batch.** Get his OK on the tone, then proceed with the rest.

6. **For each approved draft, call `outlook_create_draft`:**
   ```
   to:      [recipient_email]
   subject: <subject>
   body:    <body>
   ```

7. **Report back:** count created, count skipped (no email on file), any errors.

## Draft tool reference

Use the Outlook MCP tool:

```
mcp__03fa365f-dcf7-4dcd-9464-faafb7ebb02b__create_draft
```

Parameters:
- `to` — array of recipient email strings, required
- `subject` — string, required
- `body` — string, required (plain text or HTML; plain text is fine)
- `cc` — optional array
- `bcc` — optional array

Drafts land in Outlook's Drafts folder. Bryce reviews and sends manually — this skill does NOT send.

## Hard Guardrails

1. **NEVER send emails.** Only create drafts. Bryce reviews and sends himself.
2. **NEVER include a signature block.** "Thanks, Bryce" closes every email.
3. **NEVER use hyphens, em dashes, or en dashes** in body or subject.
4. **NEVER include opportunities in queue draft pulls** — opps don't have email on the model (per CLAUDE.md). Only leads and recycled leads.
5. **NEVER bypass the per-batch sample preview** in ad-hoc drafting. Always show 2 to 3 examples and get OK before mass-creating.
6. **NEVER modify Salesforce records** as part of this skill (no status changes, no stage updates, no notes). This skill only reads from SF and writes Outlook drafts.

## Reporting Format

End every run with:

```
✅ Drafts created: N
⏭️  Skipped: N
   - <reason 1>: <count>
   - <reason 2>: <count>
❌ Errors: N
   - <draft id or recipient>: <error>

Queue cleared: yes/no
```

Then list the first 3 to 5 created drafts with subject + recipient so Bryce can spot-check before opening Outlook.

## Opp Target Re-engagement (dashboard checkbox queue)

When Bryce says **"draft the opp target emails"** (or similar — these are the converted-opp recycled leads he checked off on the dashboard **Opp Targets** page):

1. `GET https://web-production-980e0.up.railway.app/api/opp_draft_queue` → returns `{count, leads:[{id, first_name, full_name, company, email, opp_id}]}`.
2. If empty, tell Bryce nothing is checked on the Opp Targets page.
3. For each lead, create an Outlook draft with the **locked v1 re-engagement email** below. Normalize first names (title-case; fix junk like "Chef Mike" → "Mike", "Sushi House Mark" → "Mark"); drop the `{company}` phrase if the company value is junk.
4. After creating all drafts, `POST .../api/opp_draft_queue/clear` with `{"all": true}`.
5. Report with the standard format. Do NOT log Salesforce activities here — that happens only when Bryce sends and explicitly asks to log.

**Locked v1 email (neutral attribution — these opps were worked by a different rep, so never claim Bryce had the prior conversation):**

```
Subject: Better time to reconnect?

Hi {first_name},

Your interest in SkyTab for {company} came across my desk, but it looks like the conversation trailed off before you reached a decision. That is normal when things get busy.

Is now a better time to reconnect?

Thanks,
Bryce
```

No-company variant (when company is junk): first sentence becomes "Your interest in SkyTab came across my desk, but it looks like the conversation trailed off before you reached a decision."

## Notes for Future Refinement

- **Tier logic** above is the deferred sales-email-drafter design from MEMORY.md. Bryce wanted to tighten this before fully encoding. Treat the tier templates as starting points — when Bryce gives feedback on a real run, update this skill.
- **Industry variants** should expand over time as we see what works. Log which variant was used per cohort.
- If Bryce starts using a CRM cadence (e.g., 10 day cadence from `Inside Sales Cadence & Templates (Claude).docx`), we should integrate those exact templates here.
