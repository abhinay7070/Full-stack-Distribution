# 8-o Outbound OS: Google Sheets + Apps Script setup

The workbook (`master/gtm_master.xlsx`) is the import vehicle. The live system is the Google Sheet, and this
script runs it.

## One-time setup (about 10 minutes)

| # | Who | Step |
|---|---|---|
| 1 | Abhinay | Google Drive > New > File upload > `gtm_master.xlsx`. Open it with Google Sheets, then File > **Save as Google Sheets**. Work in that copy from now on. |
| 2 | Abhinay | Share the sheet with `oukasha@8-o.co` and `hamza@8-o.co` (Editor). |
| 3 | Abhinay | In the sheet: Extensions > Apps Script. Replace `Code.gs` with `apps_script/Code.gs`. Project Settings > tick "Show appsscript.json" and paste `apps_script/appsscript.json`. Save. |
| 4 | Each person (Abhinay, Oukasha, Hamza) | Reload the sheet. Menu **8-o > Connect my mailbox** > allow the Google permissions. This person's mailbox now syncs every 15 min (Settings `sync_every_minutes`). The first pass reads the last 90 days. |
| 5 | Abhinay | Settings: fill `booking_link`. Optional: `research_folder_id`, `gmail_label`. |

Each person has to do step 4 from their own Google account. Apps Script time triggers run as the person who
created them, so each mailbox is read under its owner's own authorisation. No admin setup or shared passwords
are needed.

## What runs automatically

| When | What |
|---|---|
| Every 15 min, per connected mailbox | Gmail > Log (both directions); auto-tag `msg_id` with `Messages.match_phrase`; label the step (1st / FU1 / Reply); classify replies; roll status into Accounts and Contacts; add new prospects found in Gmail; rebuild Queue |
| Daily at `digest_hour` | Each person gets an email with the scoreboard and their own queue |

## Menu (8-o)

| Item | Does |
|---|---|
| Connect my mailbox | Installs your triggers, runs the first sync |
| Sync now / Rebuild Queue | On demand |
| Create Gmail drafts for my Queue | Drafts (never sends) for your first touches and follow-ups, from Live + QC-Approved messages only; reply drafts with the booking link. Capped by `daily_send_cap_per_mailbox` |
| Send me my digest now | Test the digest |
| Import research CSVs from Drive | Merges `accounts.csv` / `contacts.csv` from the pipeline (pipeline columns only; your yellow cells and sync columns stay) |
| Health check | Tabs, columns, mailbox status, your triggers |
| Disconnect my mailbox | Removes your triggers |

## Rules the sync follows

- A thread counts as outreach when a team member emailed an external address in it. Internal threads, newsletters,
  calendar mail and Settings `exclude_*` addresses are never logged.
- Replies are matched by thread, not by domain. Example: drip replied from `drippay.dev` to a mail sent to `usedrip.ai`.
- The same message seen in two mailboxes (cc) is logged once, de-duplicated by Message-ID or sender + time (2 min).
- `class_auto` is rule-based. Confirm or correct it in `class_human`, which always wins. Do not treat auto classes as
  market evidence.
- The script never overwrites formula columns or yellow columns. Formulas are copied to new rows.

## Refreshing research from the pipeline

1. Run `python3 scripts/build_master.py` in the repo. It writes `master/accounts.csv` and `master/contacts.csv`.
2. Upload both files to the Drive folder whose id is in Settings `research_folder_id`.
3. Click 8-o > Import research CSVs from Drive.

To keep the DB in step with the sheet (Log, Angles, Messages, Experiments, Learnings, yellow cells), download the
sheet as .xlsx to `master/gtm_master.xlsx` before running `build_master.py`. The build syncs it into
`workspace_rows` first.

## Testing

`apps_script/Code.gs` mirrors `scripts/workbook.py` (`thread_stats`, `status_of`, `build_queue`). If you change one,
change the other. Syntax check: `node --check` on a `.js` copy.
