#!/usr/bin/env python3
"""king_lint.py - score a draft against the On Writing toolbox.

Usage:
  python3 king_lint.py draft.txt                 # score one draft
  python3 king_lint.py draft.txt --mode business # email / post / outreach thresholds
  python3 king_lint.py first.txt --revised second.txt   # also checks 2nd = 1st - 10%
  python3 king_lint.py draft.txt --json

Score is 0-100. Each check has a weight; a check fails or passes partially.
Target: >= 90 to ship, >= 95 = "King-grade". The checks are mechanical proxies; the
rubric in evals/rubric.md covers what a script cannot see (truth, story, resonance).

Reference numbers measured from King's own prose in On Writing (craft chapters, ~41,800 words):
  mean sentence 21.3 words (median 19); 10% of sentences are 1-5 words; 21% are 31+ words
  63% one-syllable words; 11% three-plus syllables
  -ly adverbs 12.1 / 1000 words (he admits he sins); passive 3.4 / 1000 words
  em-dashes 7.6, parentheses 6.8, contractions 27.3 per 1000 words
"""
import json
import re
import statistics as st
import sys

# --- word lists -----------------------------------------------------------
NOT_ADVERBS = {
    "only", "family", "reply", "early", "likely", "july", "italy", "fly", "belly", "ugly", "holy",
    "silly", "daily", "lonely", "bully", "rally", "supply", "apply", "jelly", "lovely", "friendly",
    "curly", "folly", "ally", "anomaly", "assembly", "monopoly", "rely", "imply", "reply", "comply",
    "multiply", "butterfly", "costly", "elderly", "orderly", "deadly", "lively", "weekly", "monthly",
    "yearly", "hourly", "quarterly", "unlikely", "chilly", "hilly", "jolly", "wily", "oily", "lily",
    "billy", "emily", "kelly", "molly", "sally", "italy", "sly", "bodily", "melancholy", "homily",
}
SAID_VERBS = r"(said|asked|replied|shouted|whispered|cried|answered|added|called|told|muttered|yelled|snapped)"
STEROID_TAGS = r"\b(grated|gasped|jerked out|hissed|growled|snarled|barked|chortled|ejaculated|intoned|" \
               r"exclaimed|retorted|opined|interjected|breathed|purred)\b"
PET_PEEVES = [  # King's and Strunk's named dislikes + their business cousins
    "at this point in time", "at the end of the day", "the fact that", "along these lines",
    "that's so cool", "personalize", "student body", "zestful",
]
INFLATED = {  # long word -> plain word (King: never "emolument" when you mean "tip")
    "utilize": "use", "utilise": "use", "facilitate": "help", "commence": "start",
    "endeavor": "try", "endeavour": "try", "emolument": "tip", "ascertain": "find out",
    "approximately": "about", "subsequently": "later", "individuals": "people",
    "in order to": "to", "prior to": "before", "leverage": "use", "synergy": "(cut it)",
    "paradigm": "(cut it)", "holistic": "(cut it)", "robust": "strong", "seamless": "smooth",
    "delve": "dig", "cutting-edge": "new", "game-changer": "(say what changed)",
    "revolutionize": "change", "empower": "let", "streamline": "simplify",
}
CLICHES = [
    "ran like a madman", "pretty as a summer day", "fought like a tiger", "hot ticket",
    "dark as a cave", "cold as ice", "beating like a drum", "heart pounding", "quiet as a mouse", "white as a ghost", "last but not least",
    "in today's fast-paced world", "it goes without saying", "needless to say", "think outside the box",
    "low-hanging fruit", "move the needle", "circle back", "touch base", "hope this email finds you well",
    "i hope this finds you well", "just following up", "just checking in", "just bumping this",
    "i wanted to reach out", "i am reaching out", "i'm reaching out", "quick question",
    "in the realm of", "a testament to", "tapestry", "navigate the complexities",
]
HEDGES = ["perhaps", "somewhat", "rather", "quite", "very", "really", "just", "actually", "basically",
          "kind of", "sort of", "a bit", "a little", "i think that", "i believe that", "i feel that"]
PASSIVE_RE = re.compile(
    r"\b(am|is|are|was|were|be|been|being)\s+(\w+ly\s+)?(\w+ed|done|made|given|taken|written|seen|known|"
    r"held|shown|told|found|built|sent|kept|left|paid|put|set|run|begun|brought|bought|caught|chosen|"
    r"driven|eaten|forgotten|gotten|hidden|led|lost|met|read|said|sold|spent|thought|understood|won)\b", re.I)


def syllables(w):
    w = w.lower().strip("’'")
    if len(w) <= 3:
        return 1
    w = re.sub(r"(es|ed|e)$", "", w)
    return max(1, len(re.findall(r"[aeiouy]+", w)))


def split_sentences(text):
    flat = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?…])[\"”’)]?\s+(?=[\"“‘(A-Z0-9])", flat)
    return [p for p in parts if re.search(r"[A-Za-z]", p)]


def paragraphs(text):
    return [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


STOP = set("a an the is are was were be been am to of and or but in on at for with he she it they we you i his her its "
           "their our your my me him them this that these those had has have do did not no so as by from up out "
           "would could will can just then there here what who all every".split())


def _stem(w):
    for suf in ("ing", "ed", "es", "s", "d"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def repeated_phrases(text, n=3):
    """Near-repeats: the same n content words (stemmed, stop words dropped) used 2+ times.
    Catches 'convince a whole city' ~ 'never convinced the city' style echoes.
    Reported for review, not penalised: a deliberate callback (voice device V10) is fine."""
    raw = re.findall(r"[a-z0-9']+", text.lower())
    toks = [_stem(w) for w in raw if w not in STOP and w not in {"rohan", "abhinay", "regards", "thanks"}]
    seen = {}
    for i in range(len(toks) - n + 1):
        seen.setdefault(tuple(toks[i:i + n]), []).append(i)
    return sorted(" ".join(g) for g, pos in seen.items() if len(pos) > 1 and pos[-1] - pos[0] > n)[:15]


def analyse(text):
    norm = text.replace("’", "'").replace("“", '"').replace("”", '"')
    low = norm.lower()
    words = re.findall(r"[A-Za-z']+", norm)
    W = max(1, len(words))
    sents = split_sentences(norm)
    lens = [len(s.split()) for s in sents] or [0]
    paras = paragraphs(norm)
    adverbs = [w for w in words if w.lower().endswith("ly") and len(w) > 4 and w.lower() not in NOT_ADVERBS]
    swifties = [m.group(0) for m in re.finditer(r"\b" + SAID_VERBS + r"\s+\w+ly\b", norm, re.I)
                if m.group(0).split()[-1].lower() not in NOT_ADVERBS]
    passives = [" ".join(m.group(0).split()) for m in PASSIVE_RE.finditer(norm)
                 if not re.match(r"\w+\s+(supposed|used)$", m.group(0).strip(), re.I)]  # idioms, not true passives
    return {
        "words": W,
        "sentences": len(sents),
        "paragraphs": len(paras),
        "mean_sentence": round(st.mean(lens), 1),
        "max_sentence": max(lens),
        "sentence_sd": round(st.pstdev(lens), 1) if len(lens) > 1 else 0,
        "short_sentence_pct": round(100 * sum(x <= 6 for x in lens) / max(1, len(lens))),
        "long_sentence_pct": round(100 * sum(x >= 30 for x in lens) / max(1, len(lens))),
        "one_syllable_pct": round(100 * sum(syllables(w) == 1 for w in words) / W),
        "three_plus_syllable_pct": round(100 * sum(syllables(w) >= 3 for w in words) / W),
        "adverbs": adverbs,
        "adverbs_per_1000": round(1000 * len(adverbs) / W, 1),
        "swifties": swifties,
        "steroid_tags": re.findall(STEROID_TAGS, low),
        "passives": passives,
        "passive_per_1000": round(1000 * len(passives) / W, 1),
        "pet_peeves": [p for p in PET_PEEVES if p in low],
        "inflated": {k: v for k, v in INFLATED.items() if re.search(r"\b" + re.escape(k.rstrip("e")) + r"(e|es|ed|ing|s)?\b", low)},
        "cliches": [c for c in CLICHES if c in low],
        "hedges_per_1000": round(1000 * sum(len(re.findall(r"\b" + re.escape(h) + r"\b", low)) for h in HEDGES) / W, 1),
        "max_paragraph_words": max((len(p.split()) for p in paras), default=0),
        "one_line_paragraphs": sum(len(p.split()) <= 12 for p in paras),
        "contractions_per_1000": round(1000 * len(re.findall(r"\w'(s|t|re|ll|ve|d|m)\b", norm)) / W, 1),
        "you_per_1000": round(1000 * len(re.findall(r"\byou(r|rs)?\b", low)) / W, 1),
        "pronoun_openers": sum(bool(re.match(r"(it|this|that|they)\b", s.strip().lower())) for s in sents),
        "placeholders": re.findall(r"\{[^{}\n]{1,60}\}|\[(?:CONFIRM|TODO|TK|NAME|COMPANY|X)[^\]\n]{0,60}\]|\[[^\]\n]{0,80}(?::|\blink\b|\.\.\.|…)[^\]\n]{0,80}\]|\bTODO\b|\bXX+\b", norm),
        "repeats": repeated_phrases(norm),
    }


def score(a, mode="fiction"):
    """Return (score, findings). Weights sum to 100."""
    biz = mode == "business"
    findings, pts = [], 0.0

    def check(name, weight, ok_frac, fix):
        nonlocal pts
        ok_frac = max(0.0, min(1.0, ok_frac))
        pts += weight * ok_frac
        if ok_frac < 1:
            findings.append({"check": name, "weight": weight, "earned": round(weight * ok_frac, 1), "fix": fix})

    # 1 adverbs (King's measured rate is 12/1000; aim lower)
    target = 6 if biz else 10
    check("adverbs", 14, 1 - max(0, a["adverbs_per_1000"] - target) / 15,
          f"Cut -ly adverbs: {a['adverbs'][:12]}. Let a stronger verb or the context carry it.")
    # 2 swifties / steroid tags
    bad = len(a["swifties"]) + len(a["steroid_tags"])
    check("dialogue tags", 8, 1 - bad / 3,
          f"Use 'said'. Remove: {a['swifties'] + a['steroid_tags']}")
    # 3 passive
    check("passive voice", 12, 1 - max(0, a["passive_per_1000"] - 4) / 8,
          f"Make the actor the subject: {a['passives'][:8]}")
    # 4 plain words
    n = len(a["inflated"]) + len(a["pet_peeves"])
    check("plain vocabulary", 12, 1 - n / 4,
          f"Swap: {a['inflated']}; delete pet peeves: {a['pet_peeves']}")
    # 5 cliches
    check("fresh images", 10, 1 - len(a["cliches"]) / 2, f"Replace clichés with a seen detail: {a['cliches']}")
    # 6 sentence rhythm: mix of short and long, mean in band
    lo, hi = (8, 18) if biz else (12, 24)
    band = 1.0 if lo <= a["mean_sentence"] <= hi else 1 - min(abs(a["mean_sentence"] - lo), abs(a["mean_sentence"] - hi)) / 10
    variety = 1.0 if a["sentence_sd"] >= (4 if biz else 7) else a["sentence_sd"] / (4 if biz else 7)
    check("rhythm", 12, 0.5 * band + 0.5 * variety,
          f"Mean sentence {a['mean_sentence']} words (target {lo}-{hi}), spread {a['sentence_sd']}. "
          "Follow a long sentence with a short one. Put a one-line paragraph where the turn is.")
    # 7 short words
    check("short words", 10, 1 - max(0, (58 if not biz else 60) - a["one_syllable_pct"]) / 15,
          f"{a['one_syllable_pct']}% one-syllable words (King: 63%). Trade long words for short ones.")
    # 8 paragraphs as maps of intent
    cap = 60 if biz else 150
    check("paragraph shape", 8, 1 - max(0, a["max_paragraph_words"] - cap) / cap,
          f"Longest paragraph {a['max_paragraph_words']} words (cap {cap}). Break it where the beat changes.")
    # 9 hedging = fear
    check("no timid hedges", 8, 1 - max(0, a["hedges_per_1000"] - 8) / 15,
          f"{a['hedges_per_1000']} hedges/1000 words. Fear is the root of bad writing; commit.")
    # 10 talk-like register (contractions)
    check("sounds like talk", 6, min(1, a["contractions_per_1000"] / 12),
          "Use contractions (it's, don't, I'd). Writing is seduction; good talk is part of it.")
    if a["placeholders"]:  # unfilled template or unverified claim = never ship
        findings.append({"check": "PLACEHOLDERS (score capped at 50)", "weight": 0, "earned": 0,
                         "fix": f"Fill or verify before sending: {a['placeholders']}"})
        pts = min(pts, 50)
    if a["repeats"]:
        findings.append({"check": "repeated phrases (review, no penalty)", "weight": 0, "earned": 0,
                         "fix": f"Keep only deliberate callbacks: {a['repeats']}"})
    return round(pts), findings


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    mode = "business" if "--mode" in argv and argv[argv.index("--mode") + 1] == "business" else "fiction"
    text = open(argv[1], encoding="utf-8").read()
    a = analyse(text)
    s, f = score(a, mode)
    out = {"file": argv[1], "mode": mode, "score": s, "metrics": a, "findings": f}
    if "--revised" in argv:
        rev = open(argv[argv.index("--revised") + 1], encoding="utf-8").read()
        r = analyse(rev)
        cut = round(100 * (a["words"] - r["words"]) / a["words"], 1)
        rs, rf = score(r, mode)
        out["revision"] = {"first_words": a["words"], "second_words": r["words"], "cut_pct": cut,
                           "formula_met": cut >= 10, "revised_score": rs, "revised_findings": rf}
    if "--json" in argv:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    print(f"KING SCORE: {s}/100  ({mode})   {'SHIP' if s >= 90 else 'REVISE'}")
    print(f"words {a['words']} | sentences {a['sentences']} | mean {a['mean_sentence']} | "
          f"1-syl {a['one_syllable_pct']}% | adverbs {a['adverbs_per_1000']}/1k | passive {a['passive_per_1000']}/1k")
    for x in f:
        print(f"  - [{x['earned']}/{x['weight']}] {x['check']}: {x['fix']}")
    if "revision" in out:
        r = out["revision"]
        print(f"REVISION: {r['first_words']} -> {r['second_words']} words, cut {r['cut_pct']}% "
              f"({'OK' if r['formula_met'] else 'cut more: 2nd draft = 1st draft - 10%'}); revised score {r['revised_score']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
