# "to-do stories": parked task (start only when the owner says "to-do stories")

Status: **PARKED** (2026-11-06). Do not start until the owner gives the keyword.

## Goal
Build a library of **real, little-known, verified** stories for outreach sequences (in the style of the Ice and the Letter,
Booth Upstairs and Last Ride sequences in `outreach/`). The stories are written into 3-4 email arcs with the `king-writing` skill.

## Source (tested 2026-11-06)
Wikipedia "Did You Know" archive: `Wikipedia:Did_you_know_archive/<YYYY>/<Month>` (raw: `index.php?title=...&action=raw`).
- Mar 2024 sample: 314 entries, 22 matched business keywords (~7%).
- Archive since 2004, about 80K entries in total (estimate). Usable GTM stories after LLM filtering: about 500-1,500 (estimate, untested).
- Send a declared User-Agent and rate-limit. The API returned 429 without one.

## Hard rule
Never let an LLM produce stories from memory. The LLM may only **classify and extract from fetched source text**,
and every claim must quote its source sentence. No quote, no claim.

## Pipeline
1. Harvest DYK archive pages (polite: declared user-agent, rate-limited).
2. Keyword prefilter (company, sold, customer, founder, invent, patent, advertis, merchant, store, market, brand).
3. LLM classifies each entry by GTM theme: distribution, timing, trust/credentials, category creation, persistence, pricing/incentives. Drop the rest.
4. Obscurity score from the Wikipedia pageviews API (fewer views = less known = a stronger hook).
5. Fetch the full article. The LLM extracts the skeleton (setup, turn, key detail, lesson), each field with a quoted source sentence.
6. Verify each claim against its quote, then a human check.
7. Write the top stories into email arcs with `.claude/skills/king-writing`.
Output: `stories/story_library.csv` (theme, story, key detail, source URLs + quotes, obscurity, hook).

## First run
Pilot on the last 2 years of the archive. Show the owner a sample of the story quality before the full 22-year run.
