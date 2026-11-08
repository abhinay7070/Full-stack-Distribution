"""Taxonomy for the GTM role-scope research: archetypes, duties (mapped to the 8-o job sequence and blocks), skills,
tools, KPIs, traits and founder-intent phrases. Editable data, not logic: change a pattern here and re-run
scripts/role_scope_analyze.py. Every pattern is a case-insensitive regex matched against posting text.

Job sequence (blueprint s.4): LEARN -> DECIDE -> FIND -> REACH -> REPLY/BOOK -> REPORT/IMPROVE.
Extra stages that postings ask for but 8-o does not own today: CLOSE, POST-SALE, SYSTEMS (foundation), LEAD (people).
Block = which 8-o building block (or foundation piece) would do the duty.
"""
from __future__ import annotations

import re

R = lambda p: re.compile(p, re.I)  # noqa: E731

# ----- archetype by title (first match wins) -----
ARCHETYPES = [
    ("gtm_engineer", R(r"\b(gtm|go[- ]to[- ]market|growth|revops|revenue|marketing|sales|outbound)\s*(engineer|engineering|"
                       r"systems|automation|technologist|hacker)|growth hack|\bgtm (ops|operations) engineer|clay\b")),
    ("revops", R(r"rev(enue)?\s*ops|revenue operations|sales op|marketing op|gtm op|go[- ]to[- ]market op|sales strategy & op|"
                 r"crm (admin|manager|specialist)|salesforce admin|hubspot admin|business systems")),
    ("sdr_bdr", R(r"\b(sdr|bdr|adr|sales development|business development rep|business development associate|"
                  r"lead gen|outbound (rep|specialist|lead|sales)|appointment setter|demand generation rep)")),
    ("solutions", R(r"solutions? (engineer|architect|consultant)|sales engineer|pre-?sales|forward[- ]deployed|"
                    r"technical account|deployment strateg|implementation")),
    ("devrel", R(r"devrel|developer (relations|advocate|evangelist|experience|community)|community (manager|lead)")),
    ("customer_success", R(r"customer (success|experience|support|onboarding)|\bcsm\b|account manag|support|"
                           r"client (success|services)|renewal")),
    ("gtm_generalist_lead", R(r"\bgtm\b|go[- ]to[- ]market|founding (hustler|business|commercial|generalist|growth|operator)|"
                              r"first (business|gtm|sales|marketing) hire|head of (business|commercial)|commercial lead|"
                              r"business lead|head of revenue|\bcro\b|chief revenue|revenue lead")),
    ("sales_leader", R(r"(head|vp|vice president|director|chief) (of )?(sales|business development)|sales (lead|manager|director)")),
    ("partnerships", R(r"partner|alliance|channel (sales|manager)|business development|\bbd\b|ecosystem")),
    ("demand_gen", R(r"demand gen|performance market|paid (media|acquisition|social)|lifecycle|acquisition|"
                     r"growth market|email market|crm market|seo|sem\b|abm|account[- ]based")),
    ("growth", R(r"growth")),
    ("content_pmm_brand", R(r"content|brand|product market|\bpmm\b|communications|\bpr\b|social media|copywriter|"
                            r"creative|field market|event|marketing")),
    ("ae_closer", R(r"account exec|\bae\b|sales|closer|seller|commercial|business develop|account director|"
                    r"new business|enterprise rep")),
    ("bizops", R(r"chief of staff|founder'?s? associate|bizops|business operations|strategy|operations|generalist|"
                 r"special projects|associate")),
]
CORE_ARCHETYPES = {"gtm_engineer", "gtm_generalist_lead", "growth", "sdr_bdr", "demand_gen", "revops"}
FOUNDING = R(r"\bfounding\b|\bfirst\b.{0,25}\b(hire|gtm|sales|marketing|growth)|\b0 ?(to|->|-) ?1\b|employee #?\s?\d\b")
SENIORITY = [("intern", R(r"\bintern|internship|working student|werkstudent|apprentice|graduate\b")),
             ("exec", R(r"\b(vp|vice president|chief|cro|cmo|head of)\b")),
             ("lead", R(r"\b(director|lead|principal|staff|manager)\b")),
             ("senior", R(r"\b(senior|sr\.?)\b")),
             ("junior", R(r"\b(junior|jr\.?|associate|entry)\b"))]

# ----- section headings -----
SECTION = [
    ("responsibilities", R(r"(what you('| wi)ll (do|own|work on|be doing|achieve)|responsibilit|the role|your role|in this role|"
                           r"day[- ]to[- ]day|what you'll|you will|your (mission|impact|job)|the job|scope|what we need|"
                           r"outcomes|key (areas|duties)|the work|what's the job|duties|deliverables|first \d+ (days|months)|"
                           r"30.?60.?90|in your first|what success looks like|your tasks|tasks|aufgaben|we need you to)")),
    ("requirements", R(r"(what we('re| are)? look(ing)? for|look for|requirement|qualifications|about you|who you are|you (have|are|bring|might be)|"
                       r"what you (bring|have|need)|must[- ]haves?|skills|experience|ideal candidate|you'll thrive|"
                       r"we('re| are) looking for|background|profil|you should|competenc|is this you|who we're looking)")),
    ("nice_to_have", R(r"(nice[- ]to[- ]have|bonus|preferred|plus|extra credit|stand out|even better|ideally)")),
    ("benefits", R(r"(benefit|perks|what we offer|compensation|salary|why join|why you'll love|we offer|package|equity)")),
    ("company", R(r"(about (us|the company|[a-z0-9 .]{2,30})$|who we are|our (mission|story|team|company)|company overview|"
                  r"the opportunity|why us|about the team)")),
    ("process", R(r"(interview|hiring process|how to apply|application process|next steps|equal opportunity|eeo|"
                  r"diversity|accommodation)")),
]

# ----- duties: (id, stage, block, label, regex) -----
D = []


def duty(i, stage, block, label, pat):
    D.append({"id": i, "stage": stage, "block": block, "label": label, "re": R(pat)})


# LEARN
duty("L1", "LEARN", "Brain", "Define ICP, segments and personas",
     r"\bicps?\b|ideal customer|customer profile|personas?\b|target (segments?|market|accounts|customers|audience)|"
     r"segmentation|\btam\b|market sizing|market map")
duty("L2", "LEARN", "Brain", "Talk to customers / voice of customer research",
     r"customer (interviews?|research|insights?|conversations|discovery)|voice of (the )?customer|talk(ing)? to (customers|users)|"
     r"user research|understand (customer|user|buyer)s?'? (needs|pain)|win[/ -]loss")
duty("L3", "LEARN", "Brain", "Positioning, messaging and narrative",
     r"positioning|messaging|value prop|narrative|storytelling|pitch (deck|narrative)?\b|how we (talk|describe)")
duty("L4", "LEARN", "Brain", "Competitive and market intelligence",
     r"competit(ive|or)|battle ?cards?|market (research|intelligence|trends|landscape)|industry trends")
duty("L5", "LEARN", "Brain", "Become a product expert / feed product team",
     r"product (feedback|roadmap|team)|feedback (to|into) (the )?product|deep (product|technical) (knowledge|understanding)|"
     r"product expert|learn the product|work (closely )?with (the )?(product|engineering)")
# DECIDE
duty("D1", "DECIDE", "Lab", "Own GTM / growth strategy and the plan",
     r"(gtm|go[- ]to[- ]market|growth|sales|marketing|revenue|outbound|demand gen\w*) (strategy|plan|roadmap|motion)|"
     r"define (our|the) (gtm|go[- ]to[- ]market|sales|growth)|own (the )?(gtm|go[- ]to[- ]market|growth|revenue|pipeline)\b|"
     r"strategic plan")
duty("D2", "DECIDE", "Lab", "Build the repeatable playbook from 0 to 1",
     r"playbooks?|from (scratch|the ground up|zero)|\b0 ?(to|->|-) ?1\b|zero to one|repeatable|scalable (process|motion|engine)|"
     r"build (out )?(the|our) (sales|gtm|growth|outbound) (motion|engine|function|process)|first principles")
duty("D3", "DECIDE", "Lab", "Run experiments and growth tests",
     r"experiment|a/b|split test|\btests?\b and (learn|iterate)|hypothes|test(ing)? new|growth loops?|iterat(e|ion) (fast|quickly|rapidly)|"
     r"rapid(ly)? test|test,? (learn|measure)|double down")
duty("D4", "DECIDE", "Lab", "Choose and open new channels",
     r"(new|acquisition|growth|marketing|distribution) channels?|channel (mix|strategy|experiments)|multi-?channel|omni-?channel|"
     r"identify (and test )?(new )?channels")
duty("D5", "DECIDE", "Lab", "Forecast, set targets, allocate budget",
     r"forecast|budget|quotas? (setting|planning)|capacity planning|goal[- ]setting|\bokrs?\b|set (the )?(targets|goals)|(revenue|pipeline|growth) (targets|goals) (for|and)|resource allocation")
# FIND
duty("F1", "FIND", "Radar", "Prospect and build target account / lead lists",
     r"prospect|lead lists?|build (targeted )?(lists|lead)|list building|lead gen(eration)?|generat(e|ing) (new )?leads|"
     r"source (new )?(leads|accounts|opportunities|prospects)|identify (and engage )?(new |target |potential )?"
     r"(accounts|customers|prospects|leads|buyers|decision[- ]makers)|top[- ]of[- ](the[- ])?funnel|net[- ]new|neukunden|akquis|prospection|prospectie|kaltakquise")
duty("F2", "FIND", "Radar", "Data sourcing and enrichment",
     r"enrich|data (sources|providers|vendors)|waterfall|scrap(e|ing)|\bclay\b|apollo|zoominfo|clearbit|\bcrawl|"
     r"contact data|data quality")
duty("F3", "FIND", "Radar", "Buying signals, intent and triggers (why now)",
     r"signals?\b|intent (data)?|triggers?\b|buying (signals|intent)|timing|why now|warm (leads|signals)|"
     r"product[- ]usage data|\bpql|product[- ]qualified")
duty("F4", "FIND", "Radar", "Account research and account-based targeting",
     r"account research|research (target |potential )?(accounts|prospects|companies)|account[- ]based|\babm\b|"
     r"account (mapping|plans?|planning)|map (the )?(org|buying committee)|stakeholder mapping")
duty("F5", "FIND", "Radar", "Lead scoring, routing and inbound triage",
     r"lead scor|scoring model|lead routing|rout(e|ing) (leads|inbound)|\bmqls?\b|inbound (leads|requests|pipeline|demand)|"
     r"triage|speed[- ]to[- ]lead")
duty("F6", "FIND", "Radar", "Generate pipeline / top-of-funnel demand (outcome)",
     r"(generat\w*|build\w*|creat\w*|driv\w*|sourc\w*|fill\w*|grow\w*) (\w+ ){0,3}pipeline|pipeline generation|"
     r"top[- ]of[- ](the[- ])?funnel|demand generation|generate (new )?(demand|opportunities)|new (logos|business opportunities)")
# REACH
duty("R1", "REACH", "Voice", "Run outbound campaigns and sequences",
     r"outbound|cold (email|outreach|emailing)|sequences?\b|cadences?|outreach campaigns?|email campaigns?|"
     r"multi-?touch|reach out to|outreach|kaltakquise|ansprache|outreach-kampagnen|prospection (sortante|téléphonique)")
duty("R2", "REACH", "Voice", "Write copy and personalise messages",
     r"copy ?writing|write (compelling |great |high[- ]converting )?(copy|emails|messages|messaging)|personali[sz]|"
     r"email copy|ad copy|landing page copy|writing (skills)?\b")
duty("R3", "REACH", "Voice", "Cold calls and LinkedIn / social selling",
     r"cold call|phone (outreach|prospecting|calls)|dials?\b|dialing|linkedin (outreach|prospecting|messages|messaging|engagement)|social selling|voicemail|calling (prospects|leads)")
duty("R4", "REACH", "Voice", "Email deliverability and sending infrastructure",
     r"deliverability|domain (health|warm)|warm[- ]?up|inbox placement|\bspf\b|\bdkim\b|\bdmarc\b|bounce rate|sending infrastructure|"
     r"smartlead|instantly|lemlist")
duty("R5", "REACH", "(outside: content)", "Content marketing, SEO and case studies",
     r"content (marketing|strategy|creation|engine|calendar)|blog|\bseo\b|\baeo\b|\bgeo\b|search engine|newsletter|"
     r"case stud|whitepaper|ebook|thought leadership|video content|podcast")
duty("R6", "REACH", "(outside: ads)", "Paid acquisition",
     r"paid (acquisition|media|social|search|ads|channels|campaigns)|\bppc\b|\bsem\b|google ads|meta ads|linkedin ads|"
     r"advertising|\bcac\b|\broas\b|ad spend")
duty("R7", "REACH", "(outside: events)", "Events, conferences and community",
     r"events?\b|conferences?|trade ?shows?|meetups?|webinars?|community|hackathons?|field marketing|booth")
duty("R8", "REACH", "Voice", "Lifecycle, nurture and email marketing",
     r"lifecycle|nurtur|drip|email marketing|onboarding (emails|flows|sequences)|re-?engage|activation (campaigns|emails)|"
     r"newsletters?")
duty("R9", "REACH", "(adjacent: partners)", "Partnerships, channel and referral programs",
     r"partnerships?\b|(channel|technology|integration|strategic|reseller|referral|agency|cloud|implementation) partners?\b|resellers?|alliances|referral (program|partners)|affiliate|co-?marketing|co-?sell|ecosystem|recruit (new )?partners|partner (program|ecosystem|channel|network|pipeline|relationships|sourced|-sourced)")
duty("R10", "REACH", "Voice", "Founder-led selling support / founder brand",
     r"founder[- ]led|founders?'? (brand|voice|network|linkedin)|ghost ?writ|executive (brand|presence)|social media|"
     r"(build|grow) (our|the) (brand|audience)|audience")
# REPLY / BOOK
duty("B1", "REPLY", "Reply", "Respond to replies and inbound, follow up",
     r"follow[- ]?ups?|respond(ing)? to (inbound|leads|inquiries|replies)|inbound (inquiries|interest)|engage (with )?(inbound|leads)|"
     r"handle (replies|responses|inquiries)|nurture (leads|prospects)")
duty("B2", "REPLY", "Reply", "Qualify prospects (discovery)",
     r"qualif(y|ying|ication)|discovery (calls?|meetings?)|\bbant\b|meddp?icc?|spiced|needs analysis|qualified (leads|opportunities|meetings|pipeline)|\bsqls?\b|\bsals?\b|qualifizier|qualification des|kwalificeren")
duty("B3", "REPLY", "Reply", "Book meetings and demos",
     r"book(ing)? (meetings|demos|calls)|set(ting)? (up )?(meetings|appointments|demos)|schedul(e|ing) (meetings|demos|calls)|"
     r"meetings? (booked|set)|appointments?\b|first meetings|generate (qualified )?meetings|demos? (booked|set)|termine (vereinbaren|buchen|legen)|prise de rendez-vous|rendez-vous|afspraken")
duty("B4", "REPLY", "Reply", "Handle objections",
     r"objection")
duty("B5", "REPLY", "Reply", "Hand off qualified opportunities to AE / founder",
     r"hand[- ]?offs?\b|hand (off|over)|pass (qualified )?(leads|opportunities)|transition (leads|deals)|partner with (account executives|aes)|"
     r"support (the )?(aes|account executives|founders) (with|in)")
# CLOSE (adjacent)
duty("C1", "CLOSE", "(outside: closing)", "Run demos and sales presentations",
     r"\bdemos?\b|product demonstrations?|present(ations)? to|pitch(ing)? (to )?(prospects|customers|buyers)")
duty("C2", "CLOSE", "(outside: closing)", "Negotiate and close deals / carry quota",
     r"\bclos(e|ing) (deals|business|new|revenue|contracts)|negotiat|full[- ]cycle|end[- ]to[- ]end sales|quota|contracts?\b|"
     r"commercial terms|win (new )?(business|deals|customers)")
duty("C3", "CLOSE", "Lab", "Manage the pipeline and deal flow",
     r"(manag\w+|maintain\w*|own\w*|run\w*) (a |the |your )?(healthy |robust )?(sales )?pipeline|pipeline (management|hygiene|reviews?|discipline|velocity)|deal (flow|desk|reviews?|stages?|cycles?)|opportunit(y|ies) management|manage (a |your )?(book|portfolio|deals)|move deals|deals? (through|forward)|sales cycles?")
duty("C4", "CLOSE", "(outside: closing)", "Pricing, proposals, RFPs and procurement",
     r"pricing|proposals?|\brfps?\b|\brfis?\b|procurement|security (reviews|questionnaires)|\bsows?\b|pilots? (design|scoping)")
# POST-SALE (adjacent)
duty("P1", "POST-SALE", "(outside: post-sale)", "Onboard and implement customers",
     r"onboard(ing)? (new )?(customers|clients|users)|implementation|deploy(ment)? (with|for) customers|go[- ]live|time[- ]to[- ]value")
duty("P2", "POST-SALE", "(outside: post-sale)", "Retain, expand and upsell accounts",
     r"retention|churn|expansion|upsell|cross[- ]sell|renewals?|net revenue retention|\bnrr\b|account growth|land and expand")
# REPORT / IMPROVE
duty("M1", "IMPROVE", "Lab", "Track metrics, dashboards and reporting",
     r"metrics|\bkpis?\b|dashboards?|reporting|analytics|track(ing)? (performance|results|metrics)|measure|attribution|"
     r"data[- ]driven|insights")
duty("M2", "IMPROVE", "Lab", "Optimise funnel and conversion rates",
     r"funnel|conversion|optimi[sz](e|ation)|reply rates?|response rates?|open rates?|win rates?|cro\b|activation")
duty("M3", "IMPROVE", "Lab", "Feed learnings back to founders / product / sales",
     r"feedback loops?|share (learnings|insights)|report (directly )?to (the )?(ceo|founders?|leadership)|weekly (reports?|updates|reviews)|"
     r"(learnings|insights) (to|with) (the )?(team|founders|product|leadership)|retrospective|post[- ]mortem|"
     r"(work|partner) (directly|closely) with (the |our )?(founders?|ceo|co-?founders?)")
# SYSTEMS (foundation)
duty("S1", "SYSTEMS", "Memory", "Own the CRM and data hygiene",
     r"\bcrm\b|hubspot|salesforce|attio|pipedrive|data hygiene|clean data|single source of truth|record keeping|log (activity|activities)")
duty("S2", "SYSTEMS", "Connections", "Automate workflows and integrate tools",
     r"automat|workflows?|zapier|\bmake\.com|\bn8n\b|integrat(e|ion)s?|\bapis?\b|webhooks?|pipelines? (between|from)")
duty("S3", "SYSTEMS", "Lab", "Build internal tools, scripts and AI agents",
     r"internal tools|build (tools|agents|automations|systems)|(python|automation|sql) scripts|\bpython\b|\bsql\b|\bllms?\b|ai (agents?|tools|workflows|automation)|"
     r"(build|create|deploy|design)\w* (\w+ ){0,3}(agents|automations|tools)|prompt(s|ing| engineering)|\bgpt\b|chatgpt|claude code|cursor|vibe[- ]?cod|no[- ]code|low[- ]code")
duty("S4", "SYSTEMS", "Connections", "Select and own the GTM tech stack",
     r"(gtm|sales|marketing|martech|revenue) (tech )?stack|tech stack|tooling|tools? (selection|evaluation)|vendor (selection|management)|"
     r"evaluate (new )?tools")
duty("S5", "SYSTEMS", "Playbooks", "Document processes and playbooks",
     r"document(ation|ing)? (processes|playbooks|learnings|best practices|workflows)|\bsops?\b|best practices|templates|knowledge base|runbooks?|(build|create|define|design|establish) (\w+ ){0,2}process(es)?")
duty("S6", "SYSTEMS", "Playbooks", "Enable and train the team",
     r"enablement|train(ing)? (the |new )?(team|reps|sales|hires)|coach|onboard (new )?(reps|hires|team members)|sales (collateral|materials)")
# LEAD (human)
duty("O1", "LEAD", "(human)", "Hire and manage a team",
     r"hire (and|&) (lead|manage|build)|build (and|&) (lead|manage|scale) (a |the |our )?team|manage (a )?team|team of \d|"
     r"recruit (and|&)|direct reports|people management|grow the team")
duty("O2", "LEAD", "(human)", "Represent the company externally / exec presence",
     r"represent (the company|us)|face of|executive (relationships|presence|sponsors?)|c-?suite|board[- ]level|investor")
DUTIES = D
STAGE_ORDER = ["LEARN", "DECIDE", "FIND", "REACH", "REPLY", "IMPROVE", "SYSTEMS", "CLOSE", "POST-SALE", "LEAD"]

# ----- tools (requirement side) -----
TOOLS = {
    "HubSpot": r"hubspot", "Salesforce": r"salesforce|\bsfdc\b", "Attio": r"\battio\b", "Pipedrive": r"pipedrive",
    "Clay": r"\bclay\b", "Apollo": r"\bapollo(\.io)?\b", "ZoomInfo": r"zoominfo", "Clearbit": r"clearbit",
    "LinkedIn Sales Navigator": r"sales nav", "Outreach.io": r"outreach\.io|salesloft,? (or|and|/) outreach|outreach,? (or|and|/) salesloft|\(outreach|outreach\)",
    "Salesloft": r"salesloft", "Smartlead / Instantly / Lemlist": r"smartlead|instantly|lemlist",
    "Gong / call recording": r"\bgong\b|chorus|fathom|fireflies", "Zapier": r"zapier", "Make": r"\bmake\.com|\bmake\b(?=,| or | and)",
    "n8n": r"\bn8n\b", "SQL": r"\bsql\b", "Python": r"\bpython\b", "JavaScript / TypeScript": r"javascript|typescript|node\.?js",
    "APIs / webhooks": r"\bapis?\b|webhook", "LLMs / AI tools": r"\bllms?\b|chatgpt|openai|\bgpt|claude|anthropic|prompt engineering|"
                                                                 r"ai tools|generative ai|genai|cursor",
    "Product analytics (Amplitude, Mixpanel, PostHog)": r"amplitude|mixpanel|posthog|heap\b",
    "Google Analytics / GA4": r"google analytics|\bga4\b", "Segment / CDP": r"\bsegment\b|\bcdp\b|rudderstack",
    "BI (Looker, Tableau, Metabase)": r"looker|tableau|metabase|power bi|mode analytics|hex\b",
    "Spreadsheets": r"excel|google sheets|spreadsheet", "Webflow / CMS": r"webflow|wordpress|\bcms\b|framer",
    "Marketing automation (Marketo, Customer.io)": r"marketo|pardot|customer\.io|braze|iterable|mailchimp|klaviyo",
    "Notion": r"\bnotion\b", "Common Room / intent tools": r"common room|6sense|bombora|demandbase|koala|rb2b|warmly|unify",
    "Ads platforms": r"google ads|meta ads|linkedin ads|facebook ads",
    "Calendly / scheduling": r"calendly|chili ?piper|savvycal",
}
TOOLS = {k: R(v) for k, v in TOOLS.items()}

# ----- skills / competencies (requirement side) -----
SKILLS = {
    "Written communication / copywriting": r"written communication|copywrit|writing skills|strong writer|excellent writ|crisp writ|write well",
    "Verbal communication / presenting": r"verbal communication|presentation skills|public speaking|communicat(e|ion) skills|articulate",
    "Data analysis / quantitative": r"analytical|data analysis|quantitative|data[- ]driven|numbers|metrics[- ]driven|statistic",
    "Technical fluency (sell to / work with engineers)": r"technical (background|aptitude|fluency|acumen|understanding|product|buyers|audience)|"
                                                         r"developer(s|-focused)|engineers?\b|\bcs degree|computer science|technical degree",
    "Coding / scripting": r"\bpython\b|\bsql\b|javascript|coding|code\b|scripting|programming",
    "Experimentation mindset": r"experiment|test and learn|hypothes|a/b",
    "Prospecting / outbound craft": r"prospecting|outbound|cold (call|email|outreach)",
    "Discovery & qualification": r"discovery|qualif",
    "Closing / negotiation": r"negotiat|closing|close deals|quota",
    "Strategic thinking": r"strateg(ic|y)",
    "Project / process management": r"project management|organized|organised|process[- ](oriented|driven)|detail[- ]oriented|attention to detail",
    "Customer empathy": r"empath|customer[- ](obsess|centric|focus)|listen",
    "AI fluency": r"\bai[- ](native|first|fluent|savvy|tools)|\bllms?\b|generative ai|prompt",
    "Domain / industry expertise": r"(industry|domain|vertical) (expertise|experience|knowledge)|experience (selling|working) (in|into|with) (healthcare|fintech|finance|legal|insurance|government|manufacturing|security)",
    "Startup / early-stage experience": r"early[- ]stage|startup|seed|series a|\b0 ?(to|->|-) ?1\b|zero to one|first (gtm|sales|marketing) hire",
    "B2B SaaS experience": r"b2b|saas",
    "Quota-carrying track record": r"quota|track record|president'?s club|exceed(ed|ing) targets",
}
SKILLS = {k: R(v) for k, v in SKILLS.items()}

TRAITS = {
    "Ownership / autonomy": r"ownership|own(s)? (it|the outcome)|autonom|self[- ](starter|directed|motivated)|independen",
    "Scrappy / hustle": r"scrapp|hustl|resourceful|roll up (your|their) sleeves|get(s)? (stuff|shit|things) done|\bgsd\b|bias (to|for) action",
    "Comfort with ambiguity": r"ambigu|uncertain|chaos|unstructured|figure (it|things) out|wear many hats",
    "Builder mindset": r"builder|build(ing)? from scratch|\bbuild\b.{0,20}\bfrom (the ground|zero)|first principles|0 ?(to|->|-) ?1",
    "Speed / urgency": r"fast[- ]paced|move fast|urgency|speed|velocity|quickly",
    "Curiosity / learning": r"curio|learn(er| quickly| fast)|growth mindset|coachab",
    "Competitive drive": r"competitive|driven|hungry|grit|resilien|tenaci",
    "Customer obsession": r"customer[- ]obsess|customer[- ]centric|empath",
    "Data-driven": r"data[- ]driven|metrics[- ]driven|analytical",
}
TRAITS = {k: R(v) for k, v in TRAITS.items()}

KPIS = {
    "Meetings / demos booked": r"meetings? (booked|set|held)|demos? (booked|set)|appointments? set|book(ing)? meetings",
    "Qualified pipeline / SQLs / opportunities": r"qualified (pipeline|opportunities|meetings|leads)|\bsqls?\b|\bsqos?\b|\bsals?\b|"
                                                 r"pipeline (generated|generation|created|growth|targets?)|generate pipeline",
    "MQLs / leads": r"\bmqls?\b|lead volume|number of leads|leads generated",
    "Revenue / ARR / quota": r"\barr\b|\bmrr\b|revenue (targets?|goals?|growth)|quota|bookings|new business revenue|\bacv\b",
    "Conversion rates": r"conversion rates?|win rates?|reply rates?|response rates?|open rates?|close rates?",
    "CAC / payback / efficiency": r"\bcac\b|payback|\bltv\b|unit economics|cost per (lead|acquisition|meeting)|\broas\b",
    "Activation / retention / NRR": r"activation|retention|churn|\bnrr\b|\bndr\b|expansion revenue",
    "Traffic / signups": r"traffic|sign[- ]?ups|signups|trials?|installs|downloads|waitlist",
}
KPIS = {k: R(v) for k, v in KPIS.items()}

INTENT = {
    "First GTM / sales / growth hire": r"first (gtm|go[- ]to[- ]market|sales|growth|marketing|business|commercial|revenue|non[- ]technical) (hire|person|team member|employee)|"
                                      r"founding (gtm|sales|growth|marketing|account|business|commercial|ae|sdr|bdr)",
    "Take GTM over from the founders": r"founder[- ]led (sales|growth|gtm)|take over from (the )?founders?|offload|"
                                      r"(currently|today),? (our )?(founders|ceo) (run|handle|lead|own|do)|"
                                      r"work (directly|closely|side by side|alongside) with (the |our )?(founders?|ceo|co-?founders?)|"
                                      r"report(s|ing)? (directly )?to (the |our )?(ceo|founders?|co-?founders?)",
    "Build a repeatable motion / playbook": r"repeatable|playbook|scalable (process|motion|engine|pipeline)|systemati[sz]",
    "Generate pipeline / demand now": r"(generate|build|create|drive|fill) (the )?(top of (the )?funnel|pipeline|demand|meetings)",
    "Find product-market fit / early customers": r"product[- ]market fit|\bpmf\b|first (\d+ |hundred |ten |few )?(customers|logos|users)|early (customers|adopters|design partners)|design partners",
    "Scale after fundraise": r"(just|recently) (raised|closed)|backed by|series a|seed (round|funding)|well[- ]funded",
    "Hybrid / generalist (wear many hats)": r"wear many hats|generalist|jack of all trades|across (sales|marketing|growth|ops)|"
                                            r"part (sales|marketing)|sales,? marketing,? (and|&)",
    "AI-native / automation expected": r"\bai[- ](native|first|powered|leveraged|enabled)|leverage ai|automat(e|ion) (everything|first)|"
                                       r"use (ai|llms|agents)|agentic|build agents",
}
INTENT = {k: R(v) for k, v in INTENT.items()}
