---
name: recycled-opp-targeter
description: Use this skill when Bryce says "target recycled opps", "filter recycled converted leads", "which converted recycled leads can I email", "show me the recycled opp email targets", or any variant asking which converted-to-opp recycled leads are safe to reach out to. Reads the local recycled leads JSON, isolates the 544 converted-to-opp records, and applies two no-touch rules before surfacing the targetable list.
---

# Recycled Opp Targeter

This skill produces a filtered, ready-to-contact list from the converted-to-opp recycled leads.

## Background

The recycled lead list contains ~1,572 leads. Of those, **544 are "converted to opp"** — meaning someone on the team spoke to the contact, gave a pitch, and likely sent a quote. An opportunity was created in Salesforce. The deal then went quiet and the opp aged off the main list.

These leads are **warm**. The contact knows what SkyTab is. They just need a nudge — not an intro.

The other ~1,028 non-converted recycled leads (no conversation ever happened) are a completely different campaign — this skill does NOT touch those.

## Data Source

```
/Users/bryce/Inside-Sales-Claude/dashboard/data/recycled_leads.json
```

Requires a recent extraction run. If the file is stale (more than 1 day old per `extracted_at`), flag it to Bryce before proceeding.

Converted-to-opp filter: `is_converted == true` AND `converted_opp_id` is not null.

## Exclusion Rules

Apply both rules to every converted-opp record. A record excluded by either rule is NOT in the targetable list.

### Rule 1: No-Touch Signal Keywords

Check `attempt_summary` + `notes_snippet` (lowercased, combined) for any of these phrases:

**Hard excludes — skip completely:**
- `do not call`, `dnc`, `dnd`, `do not contact`, `do not reach out`
- `demo scheduled`, `demo set`, `demo booked`
- `appointment scheduled`, `appointment set`, `appt set`, `appt scheduled`
- `call scheduled`, `call set`, `follow up scheduled`
- `in contact`, `working deal`, `in process`

**Soft flag — show separately for Bryce to decide:**
- `not interested`
- `not a fit`
- `going with another`
- `signed with`

When a hard-exclude keyword matches, record which keyword triggered it.

> **Note on truncation:** `attempt_summary` captures the last 5 activity subjects (40 chars each). `notes_snippet` is 100 chars. A note that says "demo sched" (truncated) might not match "demo scheduled." When a partial match is suspicious (e.g., `attempt_summary` contains `demo` but not a scheduling word), flag those records separately rather than hard-excluding — let Bryce eyeball them.

### Rule 2: Recent Contact (10-Day Window)

Compare **`last_contact_date`** against today's date.

- If within **10 calendar days** → exclude. Someone actually reached out recently.
- If **null** → treat as targetable. No real contact on record = safe to contact.
- If **older than 10 days** → targetable (passes this rule).

> **Use `last_contact_date`, NOT `last_activity_date`.** `last_contact_date` counts only real completed contact (Call/Email) and logged notes. It deliberately ignores the mandated open "Follow Up" cadence task that management requires on every record, plus completed ops/admin tasks (close-case, checklists) — none of which mean a rep actually worked the lead. `last_activity_date` includes those placeholders and will wrongly exclude good targets. If `last_contact_date` is missing from the data (older extraction), fall back to `last_activity_date` and warn that the extraction needs a re-run.

## Output Format

### Summary header

```
Recycled Opp Targets — [today's date]
──────────────────────────────────────
Total converted opps:          544
Excluded — no-touch signal:     N  (top keywords: "demo scheduled" x3, "call scheduled" x2, ...)
Excluded — active ≤ 10 days:    N
Soft-flagged (review):          N
──────────────────────────────────────
Targetable:                     N
```

### Targetable list

Group by **days since last activity** (oldest first — highest priority):

```
#  Name               Company                Phone         Email                      Last Activity   Attempts  Notes
1  Jane Smith         Rosie's Diner          5551234567    jane@rosies.com            45 days ago     3         "interested in pricing, wants to see demo"
   SF Opp: https://crmcredorax.lightning.force.com/[converted_opp_id]
...
```

Display all targetable leads. If the list exceeds 30, show the first 30 and report how many more remain.

### Soft-flagged list (if any)

Show separately with the matched phrase highlighted so Bryce can decide quickly.

### Excluded summary (compact)

List the top 10 excluded leads with the keyword that triggered exclusion — so Bryce can spot-check the filter logic.

## Hard Guardrails

1. **NEVER modify any Salesforce record** as part of this skill.
2. **NEVER email, call, or take any action** on the leads — this skill only surfaces the list.
3. **NEVER include non-converted leads** (where `is_converted == false`) in any output.
4. If the JSON file is missing or unreadable, stop and tell Bryce to run `run_extraction.sh` first.

## Step 2 (Future)

Once Bryce approves the targetable list, email drafting will happen via the `gmail-draft-generator` skill with nudge-style tone (not a cold intro — these contacts already know SkyTab). That workflow is not part of this skill.
