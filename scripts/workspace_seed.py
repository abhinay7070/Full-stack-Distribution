"""Seed the 8-o outbound workspace (Log, Angles, Messages, Experiments, Learnings, account notes).

Usage: python3 scripts/workspace_seed.py      (build_master.py calls it; safe to re-run)

Every row is INSERT OR IGNORE into `workspace_rows`, so an owner edit synced back from the workbook is
never overwritten by a seed. Log rows come from the abhinay@8-o.co mailbox as read through the Gmail API
on 2026-11-15 (message ids are Gmail ids, so the Apps Script sync de-duplicates them). The Spaceflow
conversation was sent from oukasha@8-o.co; only its last message is in abhinay's mailbox (cc), the rest is
reconstructed from the quoted history (source = backfill_quoted, times approximate to a few hours). When
oukasha's mailbox is connected, the sync replaces those rows with the real messages.
Deliverability test sends (mxverdict.com) and internal mail are excluded on purpose.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402

A, O = "abhinay@8-o.co", "oukasha@8-o.co"
GM = "https://mail.google.com/mail/?authuser={mb}#all/{tid}"


def _log(tid, date, mb, direction, step, email, name, domain, subject, snippet, thread, msg, cls="", human="",
         source="gmail_backfill", notes=""):
    return {"touch_id": tid, "date": date, "mailbox": mb, "direction": direction, "channel": "Email", "step": step,
            "contact_email": email, "contact_name": name, "company_domain": domain, "subject": subject,
            "snippet": snippet, "thread_key": f"{domain}|{pdb_subject(subject)}", "msg_id": msg, "class_auto": cls,
            "class_human": human, "notes": notes, "link": GM.format(mb=A, tid=thread), "thread_id": thread,
            "rfc_id": "", "source": source}


def pdb_subject(s: str) -> str:
    import re
    s = (s or "").strip().lower()
    while True:
        n = re.sub(r"^(re|fw|fwd|aw|sv)\s*:\s*", "", s)
        if n == s:
            break
        s = n
    return re.sub(r"\s+", " ", s)


LOG = [
    _log("1a1acc5646bd6d0b", "2026-11-05T17:33:00Z", A, "out", "1st", "nihalgunukula@phyvant.com", "Nihal", "phyvant.com",
         "Design partner invitation for Phyvant", "My cofounder and I are building 8-o. Think of it as a persistent AI agent...",
         "1a1acbd9e692bbd4", "M-01"),
    _log("1a1ace0dd493002c", "2026-11-05T18:03:00Z", A, "out", "1st", "luca@kebra.com", "Luca", "kebra.com",
         "Design partner invitation for Kebra", "My cofounder and I are building 8-o. Think of it as a persistent AI agent...",
         "1a1acbe998425cd7", "M-01"),
    _log("1a1acf41625e6951", "2026-11-05T18:24:00Z", A, "out", "1st", "aryaman@ardentech.ai", "Aryaman", "ardentech.ai",
         "Design partner invitation for Ardentech", "My cofounder and I are building 8-o. Think of it as a persistent AI agent...",
         "1a1acbf0093c7a4e", "M-01"),
    _log("1a1ad057a6d2833b", "2026-11-05T18:43:00Z", A, "out", "1st", "gurshabd@usedefinite.com", "Gursha", "usedefinite.com",
         "Design partner invitation for Usedefinite", "My cofounder and I are building 8-o. Think of it as a persistent AI agent...",
         "1a1acbf9c94e24de", "M-01"),
    _log("1a1ad16e1d8799b3", "2026-11-05T19:02:00Z", A, "out", "1st", "victor@perfectly.so", "Victor", "perfectly.so",
         "1 of 1 Design partner invitation for Perfectly", "My cofounder and I are building 8-o. Think of it as a persistent AI agent...",
         "1a1acc032593ffa5", "M-01"),
    _log("1a1c1409eee5e79a", "2026-11-09T17:00:00Z", A, "out", "FU1", "luca@kebra.com", "Luca", "kebra.com",
         "Re: Design partner invitation for Kebra", "To make my last note more concrete, we're looking for two more startups...",
         "1a1acbe998425cd7", "F-01"),
    _log("1a1c172f94c677cf", "2026-11-09T17:55:00Z", A, "out", "FU1", "gurshabd@usedefinite.com", "Gursha", "usedefinite.com",
         "Re: Design partner invitation for Usedefinite", "To make my last note concrete, we're looking for 2 more startups...",
         "1a1acbf9c94e24de", "F-01"),
    _log("1a1c1ef7dddd063a", "2026-11-09T20:11:00Z", A, "out", "FU1", "victor@perfectly.so", "Victor", "perfectly.so",
         "Re: 1 of 1 Design partner invitation for Perfectly", "A followup, if you'd like to be working with us as a design partner?...",
         "1a1acc032593ffa5", "F-03"),
    _log("1a1c2283f6fba890", "2026-11-09T21:13:00Z", A, "out", "1st", "alexis@standout.work", "Alexis", "standout.work",
         "1 on 1 Design partner invitation for Standout", "...send you a quick Friday memo on what we tested... we don't expect a dime...",
         "1a1c168ed205a027", "M-02"),
    _log("1a1c258c4e9a143a", "2026-11-09T22:06:00Z", A, "out", "1st", "mike@usedrip.ai", "Mike", "usedrip.ai",
         "1 on 1 Design partner invitation for drip AI", "...send you a quick Friday memo on what we tested... we don't expect a dime...",
         "1a1c179e05f44304", "M-02"),
    _log("1a1c25c6a0f325b6", "2026-11-09T22:09:21Z", A, "in", "Reply", "mike@drippay.dev", "Michael Levin", "usedrip.ai",
         "Re: 1 on 1 Design partner invitation for drip AI", "call me [phone number]", "1a1c179e05f44304", "", "positive",
         notes="Replied 3 min after the send, from a different domain (drippay.dev)."),
    _log("1a1c322aa8e1bd57", "2026-11-10T01:46:33Z", A, "out", "Reply", "mike@drippay.dev", "Michael Levin", "usedrip.ai",
         "Re: 1 on 1 Design partner invitation for drip AI", "I just tried calling your cell and left a quick voicemail... Google Meet/WhatsApp",
         "1a1c179e05f44304", "CONV"),
    _log("1a1c323730a314e9", "2026-11-10T01:47:10Z", A, "in", "Reply", "mike@drippay.dev", "Michael Levin", "usedrip.ai",
         "Re: 1 on 1 Design partner invitation for drip AI", "free now", "1a1c179e05f44304", "", "positive",
         notes="No later message in the mailbox as of 2026-11-15. If a call happened, set class_human=meeting."),
    _log("1a1c6593eba63254", "2026-11-10T16:45:00Z", A, "out", "1st", "nalin@getbujo.com", "Nalin", "getbujo.com",
         "Design partner invitation for bujo", "...we don't expect a dime until we actually prove it works... 2 other YC startups onboarded",
         "1a1c64e31256258d", "M-02"),
    _log("1a1c677754667882", "2026-11-10T17:18:00Z", A, "out", "1st", "tejas@meetalder.com", "Tejas", "meetalder.com",
         "Design partner invitation for Alder", "We're currently selecting 5 early-stage startups... deliberately small with YC only",
         "1a1c65457295a033", "M-03"),
    _log("1a1c6bc1f3d1005a", "2026-11-10T18:33:00Z", A, "out", "1st", "evan@clerked.ai", "Evan", "clerked.ai",
         "Early Design partner invitation for Clerked", "We're currently selecting 5 early-stage startups... deliberately small with YC only",
         "1a1c666f1f98c6d6", "M-03"),
    _log("1a1c6e46620c4512", "2026-11-10T19:17:00Z", A, "out", "1st", "evan@onegc.com", "Evan", "onegc.com",
         "Early Design partner Invitation for OneGC", "We're currently selecting 5 early-stage startups... deliberately small",
         "1a1c66fb22d4aaff", "M-03"),
    _log("1a1c71b56ad21257", "2026-11-10T20:17:00Z", A, "out", "FU1", "alexis@standout.work", "Alexis", "standout.work",
         "Re: 1 on 1 Design partner invitation for Standout", "Would you like to be working with us?... If you are not interested, let me know, i won't follow up.",
         "1a1c168ed205a027", "F-02"),
    _log("1a1cc06ab47d31b8", "2026-11-11T19:12:30Z", A, "out", "1st", "hindy@operatechnologies.com", "Hindy", "operatechnologies.com",
         "Design partner invitation for Opera Technologies", "...send you a quick Friday memo... we don't expect a dime...",
         "1a1cc058c59c77e8", "M-02"),
    _log("1a1cc20bdad242f1", "2026-11-11T19:41:00Z", A, "out", "1st", "adi@morphik.ai", "Adi", "morphik.ai",
         "1 : 1 Design partner invitation for Morphik", "...selecting 5 early-stage startups... quick Friday memo... we don't expect a dime...",
         "1a1cc06d5244c956", "M-02"),
    _log("1a1cc4cae2f68f14", "2026-11-11T20:29:00Z", A, "out", "1st", "catherine@trytruli.com", "Catherine", "trytruli.com",
         "1 on 1 Design partner invitation for Truli", "...send you a quick Friday memo... we don't expect a dime...",
         "1a1cc07a29ef3da8", "M-02"),
    _log("1a1cc6bcefcd668b", "2026-11-11T21:03:00Z", A, "out", "FU1", "nalin@getbujo.com", "Nalin", "getbujo.com",
         "Re: Design partner invitation for bujo", "Would you like to be working with us?... let me know, i won't follow up.",
         "1a1c64e31256258d", "F-02"),
    _log("1a1cc7c4a420c36a", "2026-11-11T21:21:00Z", A, "out", "FU1", "tejas@meetalder.com", "Tejas", "meetalder.com",
         "Re: Design partner invitation for Alder", "Would you like to be working with us?... let me know, i won't follow up.",
         "1a1c65457295a033", "F-02"),
    _log("1a1cc94184c606a0", "2026-11-11T21:47:00Z", A, "out", "FU1", "evan@clerked.ai", "Evan", "clerked.ai",
         "Re: Early Design partner invitation for Clerked", "Would you like to be working with us?... let me know, i won't follow up",
         "1a1c666f1f98c6d6", "F-02"),
    _log("1a1d331faeab2af9", "2026-11-13T04:37:10Z", A, "out", "1st", "mert@usederya.com", "Mert", "usederya.com",
         "design partner / Derya", "...doing the GTM work ourselves for a few startups... proof of work we show accelerators",
         "1a1d32d31536bd6a", "M-04a"),
    _log("1a1d33be439654be", "2026-11-13T04:48:00Z", A, "out", "1st", "aaron@peercv.com", "Aaron", "peercv.com",
         "1:1 design partner for PeerCV", "...stepping in as forward-deployed engineers... until demos land on your calendar",
         "1a1d3327f278942f", "M-04b"),
    _log("1a1d34e32ff512ba", "2026-11-13T05:08:00Z", A, "out", "1st", "alex@whitespacehq.ai", "Alex", "whitespacehq.ai",
         "design partner / Whitespace", "...doing the GTM work ourselves for a few startups... proof of work we show accelerators",
         "1a1d335d54f5e27d", "M-04a"),
    _log("1a1d3616c0483f5c", "2026-11-13T05:29:00Z", A, "out", "1st", "rayhan@colmez.com", "Rayhan", "colmez.com",
         "1:1 design partner / Colmez", "...stepping in as forward-deployed GTM engineers... hopefully landing demos on your calendar",
         "1a1d3366c0e6b3f3", "M-04b"),
    _log("1a1d36f29995d492", "2026-11-13T05:44:00Z", A, "out", "1st", "sasha@erinys.ai", "Sasha", "erinys.ai",
         "design partner / Erinys", "...doing the GTM work ourselves for a few startups... proof of work we show accelerators",
         "1a1d337d0a712ede", "M-04a"),
    _log("1a1d37eb969cd8e0", "2026-11-13T06:01:00Z", A, "out", "1st", "dima@codos.ai", "Dima", "codos.ai",
         "1:1 design partner/  Codos", "...stepping in as forward-deployed GTM engineers... until demos land on your calendar",
         "1a1d338840cb09d3", "M-04b"),
]
_SF = dict(email="emre@spaceflow.tech", name="Emre Isik", domain="spaceflow.tech", subject="spaceflow <> 8-o",
           thread="1a1d317896fad3ab")
for tid, when, d, step, snip, msg, cls, human in [
    ("bf-spaceflow-01", "2026-11-05T13:46:00Z", "out", "1st", "My cofounder and I are building 8-o. Think of it as a persistent AI agent... (signed Oukasha)", "M-01", "", ""),
    ("bf-spaceflow-02", "2026-11-05T19:27:00Z", "in", "Reply", "Seems interesting. I would not be paying until it works but happy to chat!", "", "positive", ""),
    ("bf-spaceflow-03", "2026-11-09T10:38:00Z", "out", "Reply", "Context memo: the problem, where Spaceflow fits, what it looks like for you (Friday memo, approval on every send)", "CONV", "", ""),
    ("bf-spaceflow-04", "2026-11-09T17:18:00Z", "in", "Reply", "Sounds great, let's talk this week. What is your availability like?", "", "positive", ""),
    ("bf-spaceflow-05", "2026-11-09T18:00:00Z", "out", "Reply", "How about Wed 9:30-10:30 am PST? Short introductory 15/20 min call.", "CONV", "", ""),
    ("bf-spaceflow-06", "2026-11-09T19:58:00Z", "in", "Reply", "Works! See you then", "", "positive", "meeting"),
    ("bf-spaceflow-07", "2026-11-11T01:49:00Z", "out", "Reply", "I have scheduled the meet for 9:30 am pst (Google Meet)", "CONV", "", ""),
    ("1a1d317896fad3ab", "2026-11-13T04:08:04Z", "out", "Reply", "Could you share the materials on Slack? Once we put together the outbound plan... start as early as next week.", "CONV", "", ""),
]:
    LOG.append(_log(tid, when, O, d, step, _SF["email"], _SF["name"], _SF["domain"],
                    ("" if step == "1st" else "Re: ") + _SF["subject"], snip, _SF["thread"], msg, cls, human,
                    source="gmail_backfill" if tid.startswith("1a") else "backfill_quoted",
                    notes="cc copy in abhinay's mailbox" if tid.startswith("1a") else
                    "Reconstructed from quoted history; replaced by the real message when oukasha's mailbox syncs."))

ANGLES = [
    {"angle_id": "ANG-01", "angle": "Learn together", "belief": "Founders help early builders who are honest about being early, "
     "and like free hands-on GTM work while 8-o learns.", "segment": "Seed / YC founders doing founder-led sales",
     "trigger": "H3 technical founders, H6 founder-led sales", "offer": "We research buyers, test angles, book demos; you shape the product",
     "intended_response": "Open to a design-partner chat", "status": "Paused", "owner": "Abhinay",
     "evidence_needed": "Positive-reply rate vs ANG-02 on >=30 delivered each", "created": "2026-11-05"},
    {"angle_id": "ANG-02", "angle": "Risk reversal: free until proven", "belief": "The blocker is risk: founders say yes when "
     "they pay nothing until pipeline shows up, and see weekly proof (Friday memo).", "segment": "Seed / YC founders without an SDR",
     "trigger": "H1 hiring GTM, H6 founder-led sales, H10 stale GTM role", "offer": "Outbound end-to-end, Friday memo, no payment until traction",
     "intended_response": "Yes to a design-partner trial", "status": "Testing", "owner": "Abhinay",
     "evidence_needed": "Positive-reply rate >= ANG-01 at >=30 delivered", "created": "2026-11-09"},
    {"angle_id": "ANG-03", "angle": "Scarcity + YC social proof", "belief": "A small, selective cohort with YC peers makes the "
     "offer feel valuable rather than free.", "segment": "YC companies", "trigger": "YC batch in batch_or_investor",
     "offer": "1 of 5 design partners, YC-only cohort", "intended_response": "Wants to be in the cohort", "status": "Paused",
     "owner": "Abhinay", "evidence_needed": "Claims must be true at send time (QC); then compare vs ANG-02", "created": "2026-11-10"},
    {"angle_id": "ANG-04", "angle": "Proof of work (short, transparent motive)", "belief": "A 60-word email with an honest reason "
     "(we need proof for our pre-seed) earns more trust and replies than a long pitch.", "segment": "Seed / YC founders",
     "trigger": "any", "offer": "We build your outbound system end-to-end as our proof of work", "intended_response": "Short yes / call",
     "status": "Testing", "owner": "Abhinay", "evidence_needed": ">=30 delivered; compare with ANG-02", "created": "2026-11-13"},
    {"angle_id": "ANG-05", "angle": "Trigger: the hire you are posting", "belief": "A founder hiring an SDR/GTM role feels the pain now; "
     "offering the outcome before the hire lands beats a generic pitch.", "segment": "Accounts tagged H1 or H10",
     "trigger": "H1 hiring GTM, H10 role open 60+ days", "offer": "Pipeline while you hire (or instead of the hire)",
     "intended_response": "Call this week", "status": "Idea", "owner": "Abhinay",
     "evidence_needed": "Run EXP-005 vs best control", "created": "2026-11-15"},
    {"angle_id": "ANG-06", "angle": "Trigger: fresh round", "belief": "Within 60 days of a raise, founders are told to build pipeline "
     "and are open to a fast experiment.", "segment": "Accounts tagged H2 (raise <= 60 days)", "trigger": "H2 fresh capital",
     "offer": "First 30 days of outbound experiments after the raise", "intended_response": "Call this week", "status": "Idea",
     "owner": "Oukasha", "evidence_needed": "Run after EXP-005", "created": "2026-11-15"},
]

BODY = {
    "M-01": "Hey {first_name},\n\nMy cofounder and I are building 8-o.\n\nThink of it as a persistent AI agent that learns your startup "
            "and its product, but acts like an embedded GTM engineer.\n\nWe're still at a very early stage, and instead of building "
            "everything in isolation, we want to understand how customer acquisition actually works on the ground.\n\nSo we're "
            "looking for a few startups to work with directly.\n\nWe'd personally handle the GTM work by researching your potential "
            "buyers, figuring out who to reach, testing different outreach angles, and hopefully getting qualified demos on your "
            "calendar.\n\nWe're more interested in proving this works with real startups, learning what breaks, and building the "
            "right product before we start.\n\nWould you be open to working with us as a design partner?\n\nBest,\n{sender}",
    "M-02": "Hi {first_name},\n\nHope you're having a great start and if you're reading this, I'd appreciate your time.\nMy cofounder "
            "and I are building 8-o. Think of it as a persistent AI agent that learns your startup and its product, but acts like an "
            "embedded GTM engineer that never leaves your startup.\n\nWe're currently selecting 2 early-stage teams alongside our "
            "first YC startup to bring on as core design partners, and we'd really like to work for {company} as a forward "
            "deployed GTM team.\n\nWe'd handle your outbound end-to-end and send you a quick Friday memo on what we tested, what's "
            "converting, and what's next. Every step we take builds a living outbound engine that learns from every reply, so you "
            "get a consistent pipeline without hiring an SDR or managing another tool.\n\nBecause we're building this alongside "
            "our first partners, we don't expect a dime until we actually prove it works and generate real traction for {company}."
            "\nWould you be open to working with us as a design partner?\n\nBest,\n{sender}",
    "M-03": "Hi {first_name},\nI appreciate your time and hope you're having a great day.\n\nWe're currently selecting 5 early-stage "
            "startups as core design partners, and we'd really like to work for {company} as a forward deployed GTM team.\n\nFor "
            "context, my cofounder and I are building 8-o, an AI GTM engineer that learns your business, runs experiments, and gets "
            "better with every conversation.\n\nWe plug in as an extension to your current GTM, and share every bit of data and "
            "playbook we test along the way. Because we're building this alongside our first partners, we don't expect a dime "
            "until we actually prove it works and generate real traction for {company}.\n\nWe've already onboarded two other YC "
            "startups, and we're keeping this group deliberately small, so we can stay hands-on.\n\nWould you be open to having us "
            "as a design partner?\n\n{sender}",
    "M-04a": "Hey {first_name},\n\nMy cofounder and I are building 8-o, an AI growth engineer that learns your business and gets "
             "better with every campaign.\n\nWe're still early, so we're doing the GTM work ourselves for a few startups. We want "
             "to build {company}'s outbound system end-to-end so it becomes the main proof of work we show accelerators for our "
             "pre-seed.\n\nWould you be open to working with us as a design partner?\n\n{sender}",
    "M-04b": "Hey {first_name},\n\nMy cofounder and I are building 8-o, an AI growth engineer that learns your business and gets "
             "better with every data point.\n\nWe're still early, so we're stepping in as forward-deployed GTM engineers for a few "
             "startups, building a custom outbound engine and company model specifically for {company}, and handling the entire "
             "GTM end to end until demos land on your calendar.\n\nThis can be our biggest proof of work we can show to "
             "accelerators for our pre-seed. If it sounds interesting, would you be open to working with us as a design partner?"
             "\n\n{sender}",
    "M-05": "Hey {first_name},\n\n[ONE VERIFIED TRIGGER: e.g. saw {company} is hiring a <role> / raised <round> on <date>.]\n\n"
            "Before that hire ramps, my cofounder and I can run {company}'s outbound end-to-end as a forward-deployed team while we "
            "build 8-o, an AI growth engineer. You approve every send, get a Friday memo, and pay nothing until it works.\n\nWorth "
            "a 15-minute call this week?\n\n{sender}",
    "F-01": "Hey {first_name},\nTo make my last note concrete, we're looking for 2 more startups where my cofounder and I can step in "
            "as a forward-deployed GTM team while we train our product 8-o.\n\nWe'll handle {company}'s outbound end-to-end (only "
            "sending what you approve), send you a quick Friday memo on what's converting, and build a living outbound engine that "
            "learns from every experiment, so you don't have to hire an SDR.\n\nTo be honest, we're still building the core product "
            "from the ground up, so we aren't asking for anything until you actually see traction and booked demos on your "
            "calendar.\n\nAs a design partner, {company} would be one of the first companies 8-o learns from the ground up. You get "
            "our full attention now, and your input shapes what we build. Would you be open to working with us?",
    "F-02": "Hey {first_name},\nWould you like to be working with us? We'll join as a forward deployed GTM team at {company}.\n\nIf "
            "you are not interested, let me know, I won't follow up.",
    "F-03": "Hi {first_name},\n\nA follow-up, if you'd like to be working with us as a design partner? {company} would be one of the "
            "first companies 8-o learns from the ground up and your input shapes what we build.\n\nWe're doing the unscalable work "
            "manually. To be honest, we're still early building the core product, so we aren't asking for anything until you "
            "actually see traction and booked demos on your calendar.\nMaybe a quick chat this week to see if this makes sense for "
            "{company}?",
}


def _msg(mid, angle, step, subject, match, rationale, qc, status, ends="N", version="2026-11-15"):
    return {"msg_id": mid, "angle_id": angle, "step": step, "status": status, "subject": subject, "body": BODY[mid],
            "match_phrase": match, "rationale": rationale, "qc_true": qc[0], "qc_relevant": qc[1], "qc_human": qc[2],
            "qc_one_ask": qc[3], "qc_safe": qc[4], "ends_sequence": ends, "version_date": version}


MESSAGES = [
    _msg("M-03", "ANG-03", "1st", "Early design partner invitation for {company}", "deliberately small",
         "Scarcity (1 of 5) + social proof (YC peers) before the product pitch.", "?YYYY", "Paused", version="2026-11-10"),
    _msg("M-02", "ANG-02", "1st", "1 on 1 Design partner invitation for {company}", "friday memo|expect a dime",
         "Risk reversal + concrete weekly deliverable; long (~170 words), generic opener.", "YY?YY",
         "Testing", version="2026-11-09"),
    _msg("M-01", "ANG-01", "1st", "Design partner invitation for {company}", "how customer acquisition actually works",
         "Honest 'we are early, learn with us'; no price talk. Spaceflow replied to this one.", "YYYYY", "Paused",
         version="2026-11-05"),
    _msg("M-04a", "ANG-04", "1st", "design partner / {company}", "doing the gtm work ourselves",
         "~60 words; transparent motive (proof for our pre-seed); one ask.", "YYYYY", "Live", version="2026-11-13"),
    _msg("M-04b", "ANG-04", "1st", "1:1 design partner / {company}", "stepping in as forward-deployed",
         "~75 words; adds the outcome (demos on your calendar).", "YYYYY", "Live", version="2026-11-13"),
    _msg("M-05", "ANG-05", "1st", "{company} + outbound before the hire", "",
         "Trigger-led: one verified fact in line 1, risk reversal, one time-bound ask. Draft until the trigger line is written per account.",
         "?Y?YY", "Draft"),
    _msg("F-01", "ANG-02", "FU1", "Re: (same thread)", "to make my last note",
         "Make the offer concrete: scope, weekly memo, approval on every send, no payment until traction.", "YYYYY", "Live",
         version="2026-11-09"),
    _msg("F-02", "ANG-02", "FU1", "Re: (same thread)", "won't follow up",
         "Permission to close: short yes/no question; promises no more follow-ups, so the sequence ends.", "YY?YY", "Live",
         ends="Y", version="2026-11-10"),
    _msg("F-03", "ANG-01", "FU1", "Re: (same thread)", "a followup, if you'd like|a follow-up, if you'd like",
         "Soft ask for a quick chat this week.", "YYYYY", "Paused", version="2026-11-09"),
]

EXPERIMENTS = [
    {"exp_id": "EXP-001", "question": "Does risk reversal (free until proven + Friday memo) beat the learn-together pitch?",
     "segment": "Seed / YC founders, US", "variable": "Offer framing", "arm_a": "M-01", "arm_b": "M-02", "start": "2026-11-05",
     "end": "2026-11-12", "metric": "reply", "min_n": 30, "status": "Readout",
     "decision": "No decision: n far below 30 per arm, different weeks and senders.", "learning_id": "L-001"},
    {"exp_id": "EXP-002", "question": "Does scarcity + YC social proof beat plain risk reversal?", "segment": "YC companies",
     "variable": "Social proof / scarcity", "arm_a": "M-02", "arm_b": "M-03", "start": "2026-11-09", "end": "2026-11-12",
     "metric": "reply", "min_n": 30, "status": "Readout", "decision": "Paused: verify the YC claim first (QC).", "learning_id": "L-007"},
    {"exp_id": "EXP-003", "question": "Short proof-of-work: 'every campaign' (A) vs 'demos land on your calendar' (B)",
     "segment": "Seed / YC founders", "variable": "Outcome line", "arm_a": "M-04a", "arm_b": "M-04b", "start": "2026-11-13",
     "end": "", "metric": "reply", "min_n": 30, "status": "Running", "decision": "", "learning_id": ""},
    {"exp_id": "EXP-004", "question": "Follow-up: concrete scope (A) vs permission-to-close (B)", "segment": "No reply after first touch",
     "variable": "Follow-up style", "arm_a": "F-01", "arm_b": "F-02", "start": "2026-11-09", "end": "", "metric": "reply",
     "min_n": 30, "status": "Running", "decision": "", "learning_id": ""},
    {"exp_id": "EXP-005", "question": "Does a verified trigger in line 1 (hiring / raise) beat the best short control?",
     "segment": "Accounts tagged H1, H2 or H10 with a verified email", "variable": "Trigger-led opening", "arm_a": "M-04a",
     "arm_b": "M-05", "start": "", "end": "", "metric": "positive", "min_n": 30, "status": "Planned",
     "decision": "Start after EXP-003 reaches 30 per arm, or now if volume allows both.", "learning_id": "L-002"},
]

LEARNINGS = [
    {"learning_id": "L-001", "date": "2026-11-15", "statement": "2 of 21 first touches got a reply (9.5%); both positive. Both "
     "were long offers promising hands-on work with no payment up front (Spaceflow via M-01, drip via M-02).",
     "level": "Observation", "evidence": "Log; EXP-001", "n": 21, "counter": "19 other first touches: no reply yet; 6 of them sent "
     "2026-11-13 (too recent).", "applies_to": "Seed / YC founders, Nov 2026", "scope": "General", "action": "Keep logging. "
     "No winner until min_n per arm.", "owner": "Abhinay", "next_review": "2026-11-20", "status": "Open"},
    {"learning_id": "L-002", "date": "2026-11-15", "statement": "Risk reversal may be the element that earns replies: Spaceflow "
     "quoted it back ('I would not be paying until it works but happy to chat').", "level": "Hypothesis",
     "evidence": "Spaceflow reply 2026-11-05", "n": 1, "counter": "", "applies_to": "Design-partner offers",
     "scope": "General", "action": "Test risk reversal in line 1 vs line 4 (after EXP-003).", "owner": "Oukasha",
     "next_review": "2026-11-27", "status": "Open"},
    {"learning_id": "L-003", "date": "2026-11-15", "statement": "Prospects can reply from a different domain than the one emailed "
     "(usedrip.ai -> drippay.dev). Matching replies by domain loses them.", "level": "Principle",
     "evidence": "drip thread 2026-11-09", "n": 1, "counter": "", "applies_to": "Reply tracking", "scope": "System",
     "action": "Applied: the sync matches replies by thread, not by domain.", "owner": "AI", "next_review": "", "status": "Applied"},
    {"learning_id": "L-004", "date": "2026-11-15", "statement": "A positive reply ('call me') stalled on logistics: the call from "
     "India did not connect, our reply took 3.6 h, and after 'free now' the thread has no further message.",
     "level": "Observation", "evidence": "drip thread 2026-11-09/10", "n": 1, "counter": "", "applies_to": "Positive replies",
     "scope": "General", "action": "Put a booking link in Settings and in every positive reply; answer within 1 h of a positive "
     "reply. Queue shows 'Reply now'.", "owner": "Abhinay", "next_review": "2026-11-17", "status": "Open"},
    {"learning_id": "L-005", "date": "2026-11-15", "statement": "First-touch copy changed 5 times in 9 days (M-01 to M-04b, 2-6 sends "
     "each). No version can reach a usable sample, so nothing is learned.", "level": "Observation", "evidence": "Messages; Log",
     "n": 21, "counter": "", "applies_to": "Experiment design", "scope": "System", "action": "One control + one challenger at a "
     "time; change copy only at readout (>= min_n delivered per arm).", "owner": "Abhinay", "next_review": "2026-11-20",
     "status": "Open"},
    {"learning_id": "L-006", "date": "2026-11-15", "statement": "8 of 21 contacted companies are Outside SAM in the pipeline "
     "(mostly 1-2 people) and 4 are not in it. The pipeline ICP (3-35 staff, built for a GTM role) is not the 8-o design-partner "
     "segment.", "level": "Observation", "evidence": "Accounts fit_tier for contacted domains", "n": 21, "counter": "",
     "applies_to": "Targeting", "scope": "General", "action": "Decide the design-partner segment (headcount, stage, sector) and "
     "write it in Angles.segment.", "owner": "Abhinay + Oukasha", "next_review": "2026-11-18", "status": "Open"},
    {"learning_id": "L-007", "date": "2026-11-15", "statement": "Copy claims changed between versions ('our first YC startup' on "
     "11-09, 'two other YC startups onboarded' on 11-10). An untrue claim costs trust when checked.", "level": "Observation",
     "evidence": "M-02 vs M-03, F-02", "n": 8, "counter": "", "applies_to": "QC", "scope": "General",
     "action": "QC gate: qc_true must be Y before a message is Live.", "owner": "Abhinay", "next_review": "2026-11-17",
     "status": "Open"},
    {"learning_id": "L-008", "date": "2026-11-15", "statement": "EMPTY: deliverability tests were sent to mxverdict.com seed "
     "addresses (2026-11-05, 2026-11-15) but the results are not recorded.", "level": "Observation", "evidence": "Sent folder",
     "n": 2, "counter": "", "applies_to": "Deliverability", "scope": "System", "action": "Paste SPF / DKIM / DMARC and "
     "inbox-placement results here; re-test monthly.", "owner": "Abhinay", "next_review": "2026-11-17", "status": "Open"},
]

ACCOUNT_NOTES = {
    "spaceflow.tech": {"owner": "Oukasha", "deal_stage": "Design partner", "angle": "ANG-01",
                       "assumption": "Wants pipeline without paying up front; values a hands-on team.",
                       "next_step": "Get materials on Slack, share outbound plan, start week of 2026-11-16",
                       "next_date": "2026-11-16", "notes": "Intro call held 2026-11-11 (9:30 PST)."},
    "usedrip.ai": {"owner": "Abhinay", "deal_stage": "Discovery", "angle": "ANG-02",
                   "assumption": "Founder answers fast and prefers calls over email.",
                   "next_step": "Reply with a booking link + 2 time slots (lead said 'free now' on 2026-11-10)",
                   "next_date": "2026-11-16", "notes": "Replies come from mike@drippay.dev."},
}

SETTINGS = {
    "team_domain": "8-o.co",
    "program_start": "2026-11-02",
    "sync_every_minutes": "15",
    "first_sync_lookback_days": "90",
    "follow_up_1_after_days": "3",
    "follow_up_2_after_days": "4",
    "max_follow_ups": "2",
    "new_first_touches_per_day": "20",
    "daily_send_cap_per_mailbox": "40",
    "bounce_alert_rate": "0.03",
    "min_n_per_arm": "30",
    "exclude_addresses": "abhinayreddy7070@gmail.com, abhinay9640@gmail.com, abhinay6308@gmail.com, oukashajaved@gmail.com, "
                         "abhinay@heyabhinay.com, oukasha@oplantia.com",
    "exclude_domains": "mxverdict.com, mail-tester.com, glockapps.com, google.com, slack.com, calendly.com, linkedin.com",
    "digest_enabled": "Y",
    "digest_hour": "8",
    "booking_link": "",
    "research_folder_id": "",
    "gmail_label": "",
}

MAILBOXES = [
    {"mailbox": "abhinay@8-o.co", "person": "Abhinay", "sender_name": "Abhinay"},
    {"mailbox": "oukasha@8-o.co", "person": "Oukasha", "sender_name": "Oukasha"},
    {"mailbox": "hamza@8-o.co", "person": "Hamza", "sender_name": "Hamza"},
]


def seed(conn) -> dict:
    n = {}
    groups = {"Log": (LOG, "touch_id"), "Angles": (ANGLES, "angle_id"), "Messages": (MESSAGES, "msg_id"),
              "Experiments": (EXPERIMENTS, "exp_id"), "Learnings": (LEARNINGS, "learning_id"),
              "Mailboxes": (MAILBOXES, "mailbox")}
    now = pdb.utcnow()
    for tab, (rows, key) in groups.items():
        n[tab] = 0
        for r in rows:
            cur = conn.execute("INSERT OR IGNORE INTO workspace_rows (tab, row_key, data, source, updated_at) "
                               "VALUES (?,?,?,?,?)", (tab, r[key], json.dumps(r), "seed", now))
            n[tab] += cur.rowcount
    for d, r in ACCOUNT_NOTES.items():
        conn.execute("INSERT OR IGNORE INTO workspace_rows (tab, row_key, data, source, updated_at) VALUES (?,?,?,?,?)",
                     ("AccountNotes", d, json.dumps(r), "seed", now))
    for k, v in SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO workspace_rows (tab, row_key, data, source, updated_at) VALUES (?,?,?,?,?)",
                     ("Settings", k, json.dumps({"key": k, "value": v}), "seed", now))
    conn.commit()
    return n


if __name__ == "__main__":
    c = pdb.init_db()
    print(json.dumps(seed(c)))
