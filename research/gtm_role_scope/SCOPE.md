# 8-o Unified Agent Scope — what founders actually hire a growth person to do

> Source: 6,183 GTM job postings from 1,599 ICP companies (seed to Series A, US/UK/EU), full text, collected 2026-11-08.
> Every number below can be reproduced from the files in this folder (`README.md` → Reproduce).
> Status: v1. When results from live clients disagree with this, the results win (blueprint s.0). Update this doc then.

---

## 0. The answer

**Founders are hiring one job: a pipeline engine that learns.** In 78.6% of growth-hire postings the duties fall into a single
connected cluster: *define ICP → messaging → find accounts → why-now signals → outbound → reply → qualify → book*.
That cluster matches 8-o's Brain → Radar → Voice → Reply loop step for step. The duty that most separates a *first* GTM hire from
later hires is **"build the repeatable playbook from 0 to 1"** (55.9% of founding postings vs 24.8% of others, z = 14.7). This is
Lab's job.

| | |
|---|---|
| **What 8-o must do** | 30 of 48 measured duties (**OWN**), = 67% of total founder demand |
| **What 8-o prepares, the founder does** | 8 duties (**ASSIST**): demos, deal tracking, events, calls/LinkedIn, budget, 16% of demand |
| **What 8-o should not do** | 10 duties (**OUT**): closing, post-sale, content, ads, partner programs, managing people, 17% of demand |
| **Replacement value** | Median posted base for core growth hires: **$122.5K** (IQR $80K–$175K, n = 1,110). Founding hires: **$150K** (n = 390) |
| **Gap Meter ceiling with this scope** | Stage 1 ≈ **37%** of the posted job · Stage 2 ≈ **56%** · Stage 3 ≈ **58%** (demand-weighted, see s.7) |
| **What founders measure** | Qualified pipeline / SQLs (34.8%), revenue / quota (24.6%). Only 6.7% name "meetings booked" |

**One-line scope for the agent:**
> 8-o owns qualified pipeline for a seed-stage founder: it learns the company, builds and runs the playbook as bets, finds the right
> accounts at the right moment, writes outreach the founder approves, handles every reply, books qualified meetings, keeps the CRM
> true, and reports each week on what worked. The founder runs demos and closes.

---

## 1. Evidence base

| Item | Value |
|---|---|
| Postings collected | 6,183 (GTM families: sales, growth, GTM engineer, founding GTM, marketing, RevOps, partnerships, presales, DevRel, CS, bizops) |
| Companies | 1,599 with ≥ 1 usable posting · 897 with a core growth-hire posting |
| Full text | 99.4% (5,072 live ATS full · 572 YC WaaS live · 99 HN founder posts · 424 raw 6,000-char · 36 snippet only) |
| Usable (≥ 3 responsibility lines) | 5,547 postings · 4,005 with a detected "responsibilities" section, 1,542 parsed from body text |
| **Core growth-hire set** | **1,680 postings**: SDR/BDR 511 · GTM generalist / founding GTM 234 · demand gen 211 · growth 211 · GTM engineer 169 · RevOps 108 · other founding-titled 236 |
| Founding / first-hire postings | 494 |
| Extracted lines | 112,896 responsibility / requirement lines, each tagged |
| Market comparison | 2,352 GTM postings from scaled companies (boards with ≥ 80 open jobs) |
| Geography (core) | US 1,019 · GB 92 · DE 88 · FR 28 · NL 18 · other EU 60 · unknown 371 |
| Freshness | 55.8% posted in the last 90 days |

Method: duty patterns are matched only against the responsibilities section of each posting. Prevalence carries a 95% Wilson
interval. Comparisons use a two-proportion z-test (|z| ≥ 3 is treated as real). Bundles come from average-linkage clustering
of duty co-occurrence. Topics from NMF over 17,012 unique core responsibility lines were used to check the taxonomy. Details
and limits are in s.10.

---

## 2. What the job is: 6 data-driven clusters

Clustering on which duties appear together in the same posting (core set) gives these bundles:

| # | Cluster (what founders bundle into one hire) | Duties | % of core postings asking for any | 8-o block |
|---|---|---|---|---|
| 1 | **Pipeline engine** | ICP · messaging · prospect lists · signals · account research · pipeline · outbound · copy · calls/LinkedIn · reply follow-up · qualify · book | **78.6%** | Brain → Radar → Voice → Reply |
| 2 | **Growth experimentation & measurement** | experiments · channels · targets/budget · content · paid · lifecycle · founder brand · metrics · funnel | **74.4%** | Lab (+ OUT for content/paid) |
| 3 | **GTM systems & data** | enrichment · scoring/routing · CRM · automation · build tools/agents · own the stack | **62.3%** | Foundation (Memory, Connections) |
| 4 | **Strategy & playbook** | own the GTM plan · build the 0→1 playbook · feed learnings to founders | **49.3%** | Lab + Friday Memo |
| 5 | Closing | demos · negotiate/close · manage deals · pricing/RFPs | 42.5% | founder (ASSIST/OUT) |
| 6 | Events & partnerships | events/community · partner programs | 38.4% | OUT / ASSIST |

The first four clusters are 8-o. They are what a "growth hire" is. Cluster 5 is a different job (AE / closer) that founders
often add to the same posting. 8-o should hand that work back to the founder, not take it on.

```
 FOUNDER DEMAND (core postings)                                    8-o
 ─────────────────────────────────────────────────────────────    ─────────────────────
 Pipeline engine          ██████████████████████████████████ 79%  Brain·Radar·Voice·Reply
 Experiment & measure     ████████████████████████████████   74%  Lab
 Systems & data           ███████████████████████████        62%  Memory·Connections
 Strategy & playbook      █████████████████████              49%  Lab·Playbooks·Memo
 Closing                  ██████████████████                 43%  → founder
 Events & partnerships    ████████████████                   38%  → out / assist
```

---

## 3. Why founders are hiring (stated intent, core postings)

| Intent (phrase evidence in the posting) | % core | ICP vs scaled-company lift |
|---|---|---|
| Build a repeatable motion / playbook | **40.2%** | 1.96× (z 15.4) |
| AI-native / automation expected | **32.9%** | — |
| Scale after a fundraise | 16.5% | 3.17× (z 22.5) |
| Take GTM over from the founders | 14.1% | **14.3×** (z 14.9) |
| Generate pipeline / demand now | 11.4% | — |
| First GTM / sales / growth hire | 10.5% | 8.2× (z 9.7) |
| Hybrid / generalist ("wear many hats") | 10.4% | — |

Read: the buyer is a founder who has just raised, is still doing sales, and wants the motion **taken off their plate and made
repeatable**. A third already expect the hire to work through AI and automation. 8-o's pitch fits what these postings say.

Verbatim (from postings):
- "Take over our founder-led motion and turn it into a repeatable playbook."
- "Document what works, kill what doesn't. Turn scrappy founder-led selling into a repeatable, scalable motion."
- "Build agents and workflows to automate your own role. We expect you to systematically eliminate manual work from your job."
- "Book meetings that hold up. A qualified meeting means the right size, a decision-maker is on the call, and the meeting actually happens."
- "Industrialise the feedback loop: every field objection and every lost deal feeds the playbook, the messaging or the roadmap."

---

## 4. The unified scope (responsibilities the agent has to carry)

Demand index = mean of (% of core postings, % of ICP companies, % of founding postings), each scaled to its column maximum.
Must ≥ 55 · Should 30–55 · Could < 30. **Stage** = blueprint build stage. **Gap** = target Gap Meter level at that stage.
Full table with confidence intervals and z-scores: `analysis/unified_scope.csv`.

### 4.1 LEARN → 🧠 Brain

| Duty | Demand | % core | % companies | Decision | Stage | Gap |
|---|---|---|---|---|---|---|
| Positioning, messaging, narrative | **70.6** Must | 32.7 | 51.3 | OWN | 1 | 2 |
| Define ICP, segments, personas | 36.8 Should | 19.6 | 26.6 | OWN | 1 | 2 |
| Competitive / market intelligence | 35.6 Should | 15.2 | 33.8 | OWN | 2 | 2 |
| Product expertise / feed product team | 29.6 Could | 8.9 | 30.3 | OWN | 1 | 2 |
| Talk to customers (VoC) | 21.9 Could | 6.6 | 20.1 | ASSIST | 2 | 1 |

### 4.2 DECIDE → 🧪 Lab

| Duty | Demand | % core | % companies | Decision | Stage | Gap |
|---|---|---|---|---|---|---|
| **Build the repeatable playbook 0→1** | **83.4** Must | 34.1 | 49.6 | OWN | 1 | 2 |
| Own the GTM / growth strategy & plan | 55.8 Must | 24.3 | 39.5 | OWN | 2 | 2 |
| Run experiments and growth tests | 43.4 Should | 23.7 | 26.9 | OWN | 1 | 2 |
| Forecast, set targets, budget | 44.4 Should | 20.3 | 38.7 | ASSIST | 3 | 1 |
| Choose / open new channels | 21.4 Could | 11.4 | 15.6 | ASSIST | 3 | 1 |

### 4.3 FIND → 📡 Radar

| Duty | Demand | % core | % companies | Decision | Stage | Gap |
|---|---|---|---|---|---|---|
| Prospect, build account / lead lists | **74.6** Must | 40.8 | 50.2 | OWN | 1 | 3 |
| Generate pipeline / top of funnel (outcome) | 57.6 Must | 29.5 | 39.0 | OWN | 1 | 2 |
| Buying signals, intent, triggers (why now) | 28.9 Could | 14.1 | 22.2 | OWN | 1 | 3 |
| Account research, account-based targeting | 22.7 Could | 11.7 | 18.1 | OWN | 1 | 3 |
| Data sourcing and enrichment | 20.3 Could | 12.1 | 13.4 | OWN | 1 | 3 |
| Lead scoring, routing, inbound triage | 18.5 Could | 10.2 | 14.6 | OWN | 2 | 2 |

### 4.4 REACH → ✍️ Voice

| Duty | Demand | % core | % companies | Decision | Stage | Gap |
|---|---|---|---|---|---|---|
| **Run outbound campaigns & sequences** | **87.1** Must | 47.7 | 52.2 | OWN | 1 | 2 |
| Lifecycle / nurture email | 31.2 Should | 14.3 | 27.1 | OWN | 2 | 2 |
| Write copy, personalise | 26.7 Could | 13.6 | 21.5 | OWN | 1 | 2 |
| Email deliverability / sending infra | 3.9 Could | 2.0 | 2.7 | OWN | 1 | 3 |
| Founder-led selling / founder voice | 34.9 Should | 15.0 | 30.1 | ASSIST | 2 | 1 |
| Cold calls, LinkedIn, social selling | 21.6 Could | 14.5 | 12.4 | ASSIST | 2 | 1 |
| Events, conferences, community | 66.8 Must | 28.9 | 52.3 | ASSIST | 3 | 1 |
| Partnerships, channel, referral | 52.3 Should | 19.2 | 44.7 | OUT | — | 0 |
| Content marketing, SEO, case studies | 33.1 Should | 13.2 | 29.1 | OUT | — | 0 |
| Paid acquisition | 19.1 Could | 10.6 | 14.5 | OUT | — | 0 |

### 4.5 REPLY & BOOK → 📥 Reply

| Duty | Demand | % core | % companies | Decision | Stage | Gap |
|---|---|---|---|---|---|---|
| **Qualify prospects (discovery)** | **65.2** Must | 37.1 | 42.5 | OWN | 1 | 2 |
| Respond to replies / inbound, follow up | 28.4 Could | 13.2 | 22.3 | OWN | 1 | 2 |
| Hand off qualified opportunity to founder | 22.6 Could | 12.1 | 19.9 | OWN | 1 | 3 |
| Handle objections | 17.2 Could | 7.6 | 13.2 | OWN | 2 | 2 |
| Book meetings and demos | 14.4 Could | 8.8 | 10.1 | OWN | 1 | 2 |

### 4.6 REPORT & IMPROVE → 🧪 Lab + Friday Memo

| Duty | Demand | % core | % companies | Decision | Stage | Gap |
|---|---|---|---|---|---|---|
| **Track metrics, dashboards, reporting** | **87.2** Must | 46.5 | 63.0 | OWN | 2 | 3 |
| Optimise funnel and conversion | 66.2 Must | 38.3 | 46.7 | OWN | 2 | 2 |
| Feed learnings to founders / product | 33.3 Should | 11.8 | 22.5 | OWN | 2 | 3 |

### 4.7 SYSTEMS → Foundation (Memory, Connections, Playbooks)

| Duty | Demand | % core | % companies | Decision | Stage | Gap |
|---|---|---|---|---|---|---|
| **Automate workflows, integrate tools** | **76.6** Must | 36.2 | 57.3 | OWN | 1 | 3 |
| Own the CRM and data hygiene | 52.3 Should | 30.8 | 40.0 | OWN | 1 | 3 |
| Build internal tools, scripts, AI agents | 41.8 Should | 20.2 | 31.4 | OWN | 1 | 3 |
| Document processes and playbooks | 24.1 Could | 8.7 | 25.9 | OWN | 2 | 3 |
| Select and own the GTM stack | 18.3 Could | 9.2 | 15.4 | OWN | 2 | 2 |
| Enable and train a team | 32.5 Should | 15.8 | 29.0 | OUT | — | 0 |

### 4.8 Handed back to the founder (not the growth hire's job at 3–35 people)

| Duty | Demand | % core | Decision | What 8-o still does |
|---|---|---|---|---|
| Negotiate & close / carry quota | 61.6 Must | 21.5 | OUT | — |
| Manage pipeline / deal flow | 47.7 Should | 17.5 | ASSIST (S2) | Memory keeps stage and next step for each deal |
| Run demos | 45.9 Should | 18.2 | ASSIST (S2) | Pre-call brief: who, why now, what they replied |
| Retain / expand accounts | 48.1 Should | 17.2 | OUT | — |
| Pricing, proposals, RFPs | 35.9 Should | 12.0 | OUT | — |
| Exec presence, hiring a team, onboarding customers | < 23 | < 6 | OUT | — |

---

## 5. What is specific to the first hire and to seed stage

### 5.1 Founding / first-hire postings vs other ICP postings (n = 494 vs 5,053)

| Founding hires get MORE of | Founding % | Others % | Lift | z |
|---|---|---|---|---|
| Build the repeatable playbook 0→1 | 55.9 | 24.8 | 2.25× | 14.7 |
| Feed learnings back to founders | 22.1 | 8.5 | 2.59× | 9.7 |
| Run outbound | 43.9 | 28.3 | 1.55× | 7.3 |
| Run experiments | 21.1 | 10.8 | 1.95× | 6.8 |
| Own the GTM strategy | 30.0 | 19.7 | 1.52× | 5.4 |

| Founding hires get LESS of | Founding % | Others % | Lift | z |
|---|---|---|---|---|
| Onboard / implement customers | 5.9 | 11.6 | 0.50× | −3.9 |
| Enable / train the team | 10.3 | 16.7 | 0.62× | −3.7 |
| Competitive intel | 11.9 | 18.2 | 0.66× | −3.5 |
| CRM hygiene | 16.2 | 23.0 | 0.71× | −3.4 |
| Dashboards / reporting | 35.8 | 43.3 | 0.83× | −3.2 |

Read: the first hire is asked to **invent the motion and report back**, not maintain a machine. That is Lab plus the Friday
Memo, which is the moat in the blueprint. Founders mention CRM hygiene and dashboards less often, but 8-o still needs them to
run. They are invisible requirements, not selling points.

### 5.2 ICP startups vs scaled companies (same text basis)

| Over-indexed at ICP startups | ICP % | Scaled % | Lift | z |
|---|---|---|---|---|
| Startup / early-stage experience | 52.7 | 24.6 | 2.14× | 23.3 |
| Build the repeatable playbook | 43.8 | 21.9 | 2.00× | 18.7 |
| Take GTM over from founders | 10.3 | 0.7 | 14.3× | 14.9 |
| Feed learnings to founders | 15.4 | 4.8 | 3.24× | 13.3 |
| HubSpot | 12.7 | 3.3 | 3.88× | 12.9 |
| Write copy / personalise | 20.5 | 10.8 | 1.90× | 10.4 |
| Run outbound | 39.8 | 28.1 | 1.41× | 10.0 |
| Run experiments | 18.7 | 9.9 | 1.89× | 9.8 |
| Deliverability tools (Smartlead / Instantly / Lemlist) | 2.5 | 0.1 | 28.9× | 7.4 |

| Under-indexed at ICP startups | ICP % | Scaled % | Lift | z |
|---|---|---|---|---|
| Salesforce | 14.0 | 27.2 | 0.51× | −14.3 |
| Customer empathy | 18.2 | 26.1 | 0.70× | −8.1 |
| Revenue / quota KPI | 30.5 | 36.6 | 0.83× | −5.4 |
| Pricing, proposals, RFPs | 23.9 | 29.2 | 0.82× | −5.0 |

---

## 6. What founders measure → what 8-o reports

| KPI named in posting (core) | % | 8-o metric (Friday Memo) |
|---|---|---|
| Qualified pipeline / SQLs / opportunities | **34.8%** | Qualified meetings held, pipeline $ created per bet |
| Revenue / ARR / quota | 24.6% | Meetings → opportunities → closed (from CRM, founder-owned) |
| Activation / retention / NRR | 15.2% | — (out of scope) |
| Traffic / sign-ups | 13.3% | Audit sign-ups (8-o's own inbound) |
| Conversion rates | 9.3% | Sent → reply → positive → booked → held, per bet |
| CAC / payback | 6.9% | Cost and human-minutes per qualified meeting (Manual Log) |
| Meetings / demos booked | 6.7% | Booked and held, ICP-matched (blueprint definition) |

Implication for positioning: founders write **"qualified pipeline"** five times as often as **"meetings booked."** Keep "qualified
demos on your calendar" as the promise, and report it as pipeline.

---

## 7. Gap Meter projection (from this scope)

Each duty is weighted by its demand index. % = Σ(target level × demand) ÷ (3 × Σ demand).

| Stage | Duties live | % of the posted growth-hire job | % of the in-scope job (excl. OUT) |
|---|---|---|---|
| Stage 1 · First Demo | 20 OWN at L2–L3 | **37%** | 45% |
| Stage 2 · Clients Pay and Stay | + 15 (10 OWN, 5 ASSIST) | **56%** | 68% |
| Stage 3 · Investors Lean In | + 3 ASSIST | **58%** | 71% |

The ceiling (~58% of the full posting, ~71% in scope) comes from two choices: sending stays at Level 2 on purpose, and closing,
post-sale, content and ads are OUT. Investor story: *"8-o does 37% of a $122K growth hire's job at Stage 1, 56% at Stage 2,
every week measured from the Manual Log."* The live meter must come from the Manual Log. This table is the target, not a
measurement.

---

## 8. Skill sets the agent needs

Founders list these requirements for a human. Each one maps to a capability someone has to build or write.

### 8.1 What founders require (core postings)

| Hard skills | % | Traits | % | Tools | % |
|---|---|---|---|---|---|
| B2B SaaS experience | 27.3 | Speed / urgency | 55.2 | LLMs / AI tools | 23.2 |
| Startup / early-stage experience | 26.4 | Ownership / autonomy | 54.1 | Spreadsheets | 21.4 |
| Quota-carrying track record | 21.4 | Competitive drive | 50.8 | HubSpot | 15.7 |
| Prospecting / outbound craft | 17.2 | Curiosity / learning | 31.6 | Salesforce | 14.2 |
| Data analysis / quantitative | 15.2 | Comfort with ambiguity | 21.7 | APIs / webhooks | 10.2 |
| Technical fluency | 14.0 | Data-driven | 20.5 | SQL | 8.2 |
| AI fluency | 13.0 | Builder mindset | 18.2 | Clay | 7.1 |
| Coding / scripting | 11.1 | Scrappy / hustle | 14.5 | Apollo | 5.1 |
| Written communication / copy | 9.5 | | | Sales Navigator | 3.5 |
| Experimentation mindset | 8.6 | | | | |

Median experience asked: **4 years** (n = 779). Posted base: SDR/BDR $80K · GTM generalist $140K · RevOps $147.5K ·
growth $153K · GTM engineer $157.5K · founding (any function) $150K.

### 8.2 Agent capability clusters (what has to exist inside 8-o)

| # | Capability | Covers duties | Owner: playbook / scorecard (Abhinay) | Owner: build (Oukasha) | Stage |
|---|---|---|---|---|---|
| K1 | **Company understanding**: ICP, personas, positioning, product and competitor facts, each with a source | L1 L3 L4 L5 | ICP + messaging playbook, Brain scorecard | Site/doc ingestion, fact store with source + date, correction loop | 1 |
| K2 | **Account & signal research**: list building, enrichment, verification, why-now line | F1 F2 F3 F4 | Signal playbook (which events = why now), lead scorecard | Rented data/enrichment connectors, signal watchers, verifier | 1 |
| K3 | **Outbound copywriting**: short, specific, founder voice, self-graded | R1 R2 R8 R10 | Voice playbook, message scorecard, founder-taste examples | Drafting + self-check against scorecard, edit-diff capture | 1 |
| K4 | **Sending safety**: domains, warm-up, caps, bounce guard | R4 | Volume rules | Rented sender integration, guardrail monitor | 1 |
| K5 | **Reply handling & qualification**: classify, draft, qualify against ICP, book, brief | B1 B2 B3 B4 B5 F5 | Reply taxonomy, qualification criteria, objection playbook | Inbox watcher, classifier, calendar booking, pre-call brief | 1–2 |
| K6 | **Bet design & judgment**: audience × trigger × angle, kill/scale rules | D1 D2 D3 | Bet template, judgment rules | Bet ledger with status + evidence, simple rule engine | 1 |
| K7 | **Measurement & memo**: funnel per bet, learnings, next moves | M1 M2 M3 | Friday Memo template | Event store → per-bet funnel → memo draft | 2 |
| K8 | **GTM systems**: CRM sync, workflow automation, stack wiring | S1 S2 S3 S4 S5 | Manual Log format | Memory, HubSpot-first CRM sync, connectors | 1–2 |
| K9 | **Founder interface**: one-tap approve/edit/reject with reason | (all sends) | Approval scorecard | Slack approval surface, decision store | 1 |

Priority order, from the evidence: **HubSpot before Salesforce** (3.9× over-indexed at ICP startups, Salesforce 0.5×). **Email
first**: LinkedIn automation stays Not Doing, and calls/LinkedIn appear in only 14.5% of core postings. **AI fluency is
expected already**, so founders will not be surprised that an agent does this job.

---

## 9. Where the data pushes back on the blueprint

| Finding | Evidence | Recommendation |
|---|---|---|
| Founders often bundle **closing** into the same hire | C2 in 51.2% of companies, 32.6% of founding postings | Keep closing OUT. Say plainly in sales: "8-o fills your calendar, you close." Stage 2 adds demo briefs + deal-stage tracking (ASSIST) |
| **Events** are asked for at more than half of companies | R7 in 52.3% of companies | ASSIST at Stage 3: Radar uses event attendance and speakers as a signal. Attending stays human |
| **Partnerships** are common but not part of the core bundle | R9 44.7% of companies, clusters with events, not pipeline | OUT for now (rule 1) |
| **"Meetings booked" is rarely the stated KPI** | 6.7% vs 34.8% for qualified pipeline | Report as qualified pipeline; keep the blueprint's *qualified demo* definition |
| **Deliverability and signals are rarely requested**, yet they decide outcomes | R4 2.0%, F3 14.1% of core | Build them anyway (guardrail and moat). Founders won't ask for them, so they shouldn't be the pitch |
| Content / SEO is asked for in 29% of companies | R5 | Stays OUT (s.13). Revisit only if clients ask for it as a condition of renewing |

None of these findings change the North Star. Two change Stage 2: add demo briefs and deal-stage memory.

---

## 10. Method and limits

- **Collection** (`scripts/role_corpus_collect.py`): GTM-family postings from `db/gtm_pipeline.db` (`jobs`) + YC WaaS roles from
  `master/open_roles.csv`. Full text was re-fetched once per board from public ATS APIs (Ashby, Greenhouse, Lever) and public YC
  job pages, with list structure kept. Closed postings fall back to the stored 6,000-char payload. No login or anti-bot wall
  was crossed.
- **Extraction** (`scripts/role_scope_analyze.py`): headings are detected and classified as responsibilities, requirements,
  nice-to-have, benefits, company or process. Duties are matched only against responsibility lines. Requirements feed the
  skills and tools tallies.
- **Taxonomy** (`scripts/role_taxonomy.py`): 48 duties mapped to the blueprint job sequence and blocks, plus tools, skills,
  traits, KPIs and intent. These are editable regexes.
- **Validation**: (a) NMF (k = 30) over 17,012 unique core responsibility lines. 61.3% of those lines match at least one duty.
  The unmatched topics are non-duty text (location/hybrid, "fast-paced", language requirements, "join our team"), with one
  exception: technical pre-sales, which is OUT anyway. (b) Manual spot check of 4 random matched lines per duty; precision
  is roughly 75–85%. The weakest patterns are R10, D5, M1 and L4, which over-match generic wording, so read them as upper bounds.
- **Synthesis** (`scripts/role_scope_synthesize.py`): demand index, priority, clusters (average-linkage on 1 − φ, cut 0.86).
  The OWN / ASSIST / OUT decisions, stages and Gap Meter targets are judgment calls written in `DECISION` in that file, with a
  reason for each, based on blueprint s.9 and s.13.
- **Limits**: postings state intent, not what the hire ends up doing. Large boards post many similar roles, so company-level
  % is reported next to posting-level %. Headcount is known for only 893 of 5,547 usable postings. The comparison set is
  scaled companies, not all companies. FX conversion uses fixed 2026 rates. The Clay tool count is low partly because the
  scaled-company set includes Clay's own job board.

---

*Living document. When client results say a duty matters more or less than this, change `DECISION` and re-run.*
