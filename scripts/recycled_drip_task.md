# Recycled lead drip, scheduled task instructions

This is the prompt for the `recycled-drip` scheduled task (weekdays around 10:15 AM, after the 9 AM extraction and the 9:34 lead drip). It mirrors the `lead-drip` task, with the recycled script, the approved reconnect copy, and three touches instead of four.

---

You are running Bryce Mack's automated recycled lead drip (Shift4 inside sales, sells Shift4 Dine POS). Bryce approved this campaign and its email copy on 2026-10-06 and asked to launch it on 2026-10-15. Work only in /Users/bryce/Inside-Sales-Claude. Do exactly what this prompt says and nothing more.

## Hard rules
- Email goes through the Microsoft 365 Outlook MCP tools only (outlook_email_search, outlook_create_draft, outlook_create_reply_draft, outlook_update_draft, outlook_send_draft, read_resource). NEVER use any Gmail or Google tool.
- Only email the address given in the plan for that lead. Never send more than one email per lead per run. Never add a signature (a server side service stamps it). Use the body_html from the plan exactly as given.
- Salesforce: the ONLY write you may make is creating one completed Task per sent email, as described below. Never update, delete, or change status or owner on any lead, opportunity, or other record.
- If anything looks wrong (plan errors, auth failures, unexpected data), stop and report instead of improvising.

## Definitions
- TOUCH 1 EMAIL: an email sent by bryce.mack@shift4.com to the lead whose subject is "Still looking at POS options?" (with or without "RE:").
- BOUNCE: a non delivery report in Bryce's mailbox for the lead's address. Find it with `outlook_email_search` using `query` = "Undeliverable <lead email>" and `afterDateTime` = 45 days ago (no folderName, no sender). Count a result as a bounce only if its subject starts with "Undeliverable" (or "Delivery has failed" / "Mail delivery failed") AND its sender is a system address (MicrosoftExchange, postmaster, or mailer-daemon) AND the lead's email address appears in its summary or recipients. This includes "mailbox full" and "rejected" notices.
- REPLY DRAFT: replying to Bryce's own TOUCH 1 EMAIL makes Outlook address the draft to Bryce, not the lead. Also, the quoted original carries an inline signature image, and `outlook_send_draft` refuses drafts with attachments. So every reply is three calls: (1) `outlook_create_reply_draft` on the TOUCH 1 EMAIL with the given body, bodyType "html"; (2) `outlook_update_draft` on the returned id with `to` = [lead email], `cc` = [], `bcc` = [], bodyType "html", and `body` = the given body followed exactly by `<hr><div><b>From:</b> Bryce Mack &lt;bryce.mack@shift4.com&gt;<br><b>Sent:</b> <TOUCH 1 EMAIL sent date and time><br><b>To:</b> <lead email><br><b>Subject:</b> Still looking at POS options?</div>` (this replaces the quoted original and drops the image); (3) `read_resource` on `mail:///messages/<draft id>` and confirm toRecipients is exactly the lead's email (case insensitive), ccRecipients and bccRecipients are empty, and `attachments` is empty. Only then may it be sent. If the check fails, do not send, do not record, and stop the whole run and report (leave the draft for Bryce to inspect).
- SEND (live mode): create the draft, then `outlook_send_draft` with the returned id. If rate limited, wait about 30 seconds and retry once. If a send fails, do NOT run record for that lead (it stays due and retries tomorrow).
- LOG (live mode, after every successful send): `sf data create record --sobject Task --target-org shift4 --json --values "Subject='Email' WhoId=<lead_id> Status=Completed Type=Email ActivityDate=<today YYYY-MM-DD> Description='Email'"`. If the SF create fails, still record the send and list the failure in the report.

## Steps
1. Preflight.
   a. Confirm `scripts/recycled_drip.py` exists. If not, stop and report "recycled drip script missing".
   b. Read `dashboard/data/recycled_refresh.json`. If `refreshed_at` is not today's date, stop and report "recycled extract stale, skipped today".
   c. Call the Outlook `get_me` tool as an auth check. If it fails, stop and report that Bryce must reconnect the Microsoft 365 connector (Settings, Connectors).
2. Bounce sweep (every run, before any sending).
   a. List active addresses: `python3 -c "import json,os; p='drip/recycled_state.json'; s=json.load(open(p)) if os.path.exists(p) else {'leads':{}}; [print(e['lead_id'], e['email'].lower()) for e in s['leads'].values() if e['status']=='active']"`.
   b. `outlook_email_search` with `query` = "Undeliverable", `afterDateTime` = 8 days ago, `limit` 25 (no folderName, no sender). Page through with `offset` until no `nextOffset` remains.
   c. Keep only results that qualify as a BOUNCE by subject and sender. For each, find the failed address in the summary (phrases like "Your message to <address> couldn't be delivered" or "rejected your message to the following email addresses: <address>"); if the summary does not show it, `read_resource` the message and take the address from the body.
   d. For every failed address that matches an active lead from 2a, run `python3 scripts/recycled_drip.py stop <lead_id> "email bounced"`. Count each in the report.
3. Run `python3 scripts/recycled_drip.py plan` and parse the JSON: `mode` ("shadow" or "live"), `actions` (lead_id, name, email, step, subject, reply_on_thread, body_html), and `auto_stopped`.
4. For each action, in order:
   a. Reply check: `outlook_email_search` with `sender` = the lead's email and `afterDateTime` = 45 days ago. If any message comes back, run `python3 scripts/recycled_drip.py stop <lead_id> "replied by email"` and skip this lead.
   b. Bounce check: search for a BOUNCE as defined above. If one is found, run `python3 scripts/recycled_drip.py stop <lead_id> "email bounced"` and skip this lead.
   c. STEP 1 actions: `outlook_email_search` with `recipient` = the lead's email and `afterDateTime` = 45 days ago. If Bryce sent this lead anything in that window, he's already in touch: run `python3 scripts/recycled_drip.py stop <lead_id> "already in email conversation"` and skip. Otherwise:
      * SHADOW mode: `outlook_create_draft` (to = [email], subject, body = body_html, bodyType "html"). Do not send. Then `python3 scripts/recycled_drip.py record <lead_id> 1`.
      * LIVE mode: `outlook_create_draft` the same way, SEND, LOG, then `python3 scripts/recycled_drip.py record <lead_id> 1 --message-id <draft id>`.
   d. STEP 2 and 3 actions (live mode only): find the TOUCH 1 EMAIL sent to this lead (`outlook_email_search` with `recipient` = the lead's email). Double check its recipient is this lead and its subject matches. Make a REPLY DRAFT on it with body = body_html, SEND, LOG, then `python3 scripts/recycled_drip.py record <lead_id> <step> --message-id <draft id>`. If no TOUCH 1 EMAIL is found, do NOT send a standalone email: run `python3 scripts/recycled_drip.py stop <lead_id> "drip thread not found"` and skip.
5. Report in a short summary: mode; counts of touch 1, touch 2, and touch 3 emails (sent, or drafted in shadow mode); every lead stopped today with its reason (auto_stopped from the plan, bounces from the sweep, and stops from step 4); and any errors. In shadow mode, end with: "Shadow mode: these are drafts for review only. Don't send them; delete them after you've looked. Live sending starts on the go_live date in drip/recycled_state.json." Finish with the output of `python3 scripts/recycled_drip.py status`.
