# PLAYBOOK — turning the dataset into qualified interviews

Goal: **4-5 qualified interviews per week**, which should produce one paid role or pilot ($1.5-4.5K/mo) by end of November.
A *qualified interview* is a live conversation with a decision-maker about paid GTM-engineering work.

## First principle
`interviews = reachable decision-makers with a live reason to talk × message relevance × attempts × credibility`.
The dataset supplies the first term: who has the pain now, and how to reach them. You control the other three.
Energy goes where pain, reachability and fit intersect. The rest of the list is a compounding engine, not a to-do list.

## Layer 1 — Clusters: why they would talk now (`Master.cluster`)
| Cluster | Pain / reason now | Angle (one line) |
|---|---|---|
| C1a Hiring my exact role | They want a GTM / Growth Engineer | "Hire me, or let me run it as a 3-week pilot while you hire" |
| C2 Stale GTM role (60+ days) | Search is failing, the pipeline is idle | "Keep searching; I cover the seat now, paid on results" |
| C1 Hiring GTM (SDR/AE/marketing/RevOps) | Building a GTM team from zero | "Before the 2nd/3rd hire, build the system they will run" |
| C6 Just launched (≤45d) | Launch traffic is decaying | "30-day post-launch pipeline sprint" |
| C3 Fresh capital (≤90d), no GTM hire | Board expects pipeline from the raise | "Turn the round into meetings in 60 days" |
| C4 Founder-led, technical founders | Founder is the only seller | "Take outbound off your plate; you only take calls" |
| C5 Outbound tooling live | Paying for tools nobody runs | "Your stack, wired into a working engine" |
| C7 Revenue, no GTM team | Bootstrapped and growing | "$3-5K/mo growth retainer" |
| C8 Fit only | No trigger yet | Nurture; re-contact when a signal fires |

## Layer 2 — Rings: how much energy (`Master.ring`)
| Ring | Size | Who | Effort per account | Energy share |
|---|---|---|---|---|
| R1 Sniper | 744 | C1a, C2, C1 with a reachable founder, any company with 3+ signals | 20-30 min research; custom proof in the email; multi-channel | 50% |
| R2 Rifle | 1,337 | C1, C3, C4, C5, C6 | 5 min; personal first line from `why_now` + cluster template | 35% |
| R3 Net | 5,304 | C8 fit-only | 0-1 min; follow / engage / content; wait for a trigger | 15% |
| Enrich | 4,796 | Tier C | Run through Clay (headcount, domain, email). Promote to R2/R3 | batch, weekly |
| Review | 601 | Tier D | Ignore unless a signal fires | 0 |

## Layer 3 — Plain text proof inside the message (no links / attachments)
- **R1:** 3 specific facts about their buyers, built from the dataset. Example: three companies matching their ICP that raised in the
  last 30 days (name, amount, date). Or 2 gaps in their current GTM: tools detected but unused, a role open N days.
- **R2:** one fact. Their trigger (raise / role / launch) plus one observation.
- **Credibility line, every message:** "I built a pipeline that indexes 92k SEC filings, 55k job boards and 17 funding feeds to find
  companies like yours." It is true, specific and checkable on your LinkedIn.
- **Trust line:** "Based in India, I work 9am-2pm ET. Paid at the end via Deel. References on request."

## Layer 4 — Channel orchestration per account
| Ring | Sequence (business days) |
|---|---|
| R1 | D0 apply (if a role exists) + plain-text email to founder · D1 LinkedIn: comment on a founder post · D3 email #2 with a new fact · D5 LinkedIn connect with a 1-line note · D9 email #3 (short close) |
| R2 | D0 email · D4 email #2 (new fact) · D10 email #3 |
| R3 | Follow the founder; engage weekly; move to R2 when the weekly refresh shows a new signal |

Email order: founder's own published email, then founders@/inbox, then Clay-verified, then pattern guess (only after verification).
Always-on inbound (set once): HN "Who wants to be hired?" (monthly), YC Work at a Startup + Wellfound profiles, the
VC talent networks behind the 37 Getro boards, plus one LinkedIn post a week built from the dataset.

## Layer 5 — Compounding: the dataset as content and as triggers
- **Weekly signal refresh** (`./scripts/run_pipeline_v2.sh`): each new raise, GTM posting, launch or stale role is a fresh reason to
  contact. Accounts move from R3 to R2 to R1 automatically. The list does not decay; it re-ranks.
- **Weekly public post**, for example "This week: N seed/Series A startups raised; M are hiring their first GTM person;
  top 3 patterns". This builds credibility and brings inbound, and it doubles as your portfolio (searchable, no link needed).
- **Give-first digest:** for R1 founders who do not reply, send one plain-text "5 accounts you should talk to this week" note.
  Value with no ask.

## Layer 6 — Funnel math and feedback loop (assumptions, to be validated)
| Weekly target | Needed (at 6% reply, 50% reply-to-call) |
|---|---|
| 4-5 qualified interviews | ~8-10 positive replies → ~150 touches/week (R1 ~40 accounts × 3 touches spread; R2 ~110 first touches) |
| 10-20 interviews (stretch) | ~300-600 touches/week: needs the R2 template machine + the Enrich lane feeding it |

Run it as a bandit: after ~100 touches per cluster, compare reply rates, then move energy to the top two clusters and rewrite
the bottom one. Log every touch in `Outreach_Log`. The Dashboard shows plan vs actual.
Kill rule: a cluster under 2% replies after 100 touches gets paused and its angle rewritten.

## Layer 7 — Conversion
Reply within an hour. Call agenda: their goal → current GTM motion → 30-day plan (read out, 5 bullets) → ask for the pilot.
Offer ladder: 3-week pilot ($1,500 fixed, invoiced at the end), then retainer ($3-4.5K/mo), then full-time.
Never do more than one small free sample.

## Workbook filters (start here every day)
1. `Master`: due = DUE (follow-ups owed)
2. `Master`: ring = R1 Sniper, sort by `rank_score`; work 8-10 a day
3. `Master`: ring = R2 Rifle, filter `cluster`; batch 20-25 a day with the cluster template
4. Log every touch → Dashboard
