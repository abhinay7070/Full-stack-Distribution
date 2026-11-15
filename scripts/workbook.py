"""8-o Outbound OS: the workbook layer (v5). Ten linked tabs, built for Google Sheets + Apps Script.

    Home         dashboard: KPIs, today's work, learning loop, weekly trend, gaps   (formulas only)
    Queue        what to do now: reply / nudge / follow up / fix bounce / first touch (Apps Script rebuilds it)
    Log          one row per email or touch, both directions, every mailbox          (Apps Script sync + you)
    Accounts     one row per company: research brief + status + your deal fields   (pipeline + sync + you)
    Contacts     one row per person                                                (pipeline + sync + you)
    Angles       strategy: why should this buyer respond?                          (you; stats are formulas)
    Messages     writing + QC gate: the copy that expresses an angle                (you; stats are formulas)
    Experiments  controlled comparisons with a verdict formula                     (you; stats are formulas)
    Learnings    knowledge base on a confidence ladder                             (you)
    Settings     config the script reads, reply classes, mailboxes, data dictionary

Header colours say who writes a column: navy = pipeline, slate = sync (Apps Script), gold + yellow cells = you,
green = formula. Cells that need input and are empty start with "EMPTY" and are shaded pink.

The DB stays the source of truth: before a rebuild, `sync_workspace()` copies every Log / Angles / Messages /
Experiments / Learnings / Settings row and the yellow Accounts / Contacts cells into `workspace_rows`.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

import pipeline_db as pdb

TZ_OFFSET = timedelta(hours=5, minutes=30)  # sheet timezone Asia/Kolkata (Apps Script sets it on connect)
LOG_ROWS = 1500        # Log rows pre-filled with formulas; the script copies formulas further down as it appends
GOOD_EMAIL = {"published_ceo", "published_founder", "published_founders_inbox", "manual", "clay_valid",
              "derived_from_pattern"}
CLOSED_DEALS = {"Lost", "Not a fit", "Do not contact", "Paid"}
OWNERS = ["Abhinay", "Oukasha", "Hamza", "AI"]

# class, score, counts as, meaning  (scores are unique: Log formulas map score -> class with MATCH)
CLASSES = [
    ("meeting", 7, "positive + meeting", "A call or meeting is agreed or booked."),
    ("positive", 6, "positive", "Interested: wants to talk or asks to start."),
    ("referral", 5, "reply", "Points to another person."),
    ("question", 4, "reply", "Asks for information; intent unclear."),
    ("other", 3, "reply", "Unclassified human reply: set class_human."),
    ("objection", 2, "reply", "Engaged but pushes back (price, fit, already have a tool)."),
    ("not_now", 1, "reply", "Timing: later."),
    ("negative", 0, "reply", "Not interested."),
    ("unsubscribe", -1, "reply + do not contact", "Asked to stop."),
    ("ooo", -5, "not a reply", "Out-of-office or auto-reply."),
    ("bounce", -9, "not delivered", "Bounce / delivery failure."),
]
SCORE = {c[0]: c[1] for c in CLASSES}

# ------------------------------------------------------------------------------------------- styles
FONT = "Arial"
F = Font(name=FONT, size=10)
F_B = Font(name=FONT, size=10, bold=True)
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_TITLE = Font(name=FONT, size=18, bold=True, color="1F2937")
F_SUB = Font(name=FONT, size=10, color="6B7280")
F_H2 = Font(name=FONT, size=12, bold=True, color="1F2937")
F_KPI = Font(name=FONT, size=20, bold=True, color="111827")
F_KPI_L = Font(name=FONT, size=9, bold=True, color="6B7280")
F_LINK = Font(name=FONT, size=10, color="1D4ED8", underline="single")
F_INPUT = Font(name=FONT, size=10, color="0000FF")
FILL = {k: PatternFill("solid", fgColor=v) for k, v in {
    "auto": "1F3864", "sync": "475569", "you": "B45309", "f": "166534",
    "input": "FFF2CC", "section": "E5E7EB", "tile": "F3F4F6", "empty": "FCE4EC", "primary": "DCFCE7",
    "band": "F9FAFB", "red": "FEE2E2", "green": "DCFCE7", "amber": "FEF3C7", "grey": "E5E7EB", "blue": "DBEAFE"}.items()}
THIN = Side(style="thin", color="D1D5DB")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
TOP = Alignment(vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
TAB_COLOR = {"Home": "111827", "Queue": "DC2626", "Log": "475569", "Accounts": "1F3864", "Contacts": "1F3864",
             "Angles": "B45309", "Messages": "B45309", "Experiments": "B45309", "Learnings": "B45309",
             "Settings": "6B7280"}

# ------------------------------------------------------------------------------------------- columns
# (name, width, kind, fmt)   kind: auto | sync | you | f
ACC = [("rank", 6, "auto", None), ("company_domain", 22, "auto", None), ("company", 22, "auto", None),
       ("fit_tier", 16, "auto", None), ("fit_score", 7, "auto", None), ("ring", 10, "auto", None),
       ("wave", 18, "auto", None), ("sector", 13, "auto", None), ("one_liner", 40, "auto", None),
       ("hq", 6, "auto", None), ("batch_or_investor", 16, "auto", None), ("headcount", 8, "auto", None),
       ("last_round", 18, "auto", None), ("last_raise_date", 11, "auto", "yyyy-mm-dd"), ("signals", 14, "auto", None),
       ("why_now", 45, "auto", None), ("hiring_role", 26, "auto", None), ("suggested_angle", 9, "auto", None),
       ("ceo_name", 18, "auto", None), ("ceo_email", 26, "auto", None), ("email_confidence", 18, "auto", None),
       ("ceo_linkedin", 12, "auto", None), ("website", 12, "auto", None),
       ("touches", 7, "sync", "0"), ("last_touch", 11, "sync", "yyyy-mm-dd"), ("outcome", 11, "sync", None),
       ("status", 13, "sync", None), ("days_since_touch", 8, "f", "0"),
       ("owner", 10, "you", None), ("deal_stage", 14, "you", None), ("angle", 9, "you", None),
       ("assumption", 34, "you", None), ("next_step", 34, "you", None), ("next_date", 11, "you", "yyyy-mm-dd"),
       ("notes", 30, "you", None)]
CON = [("company_domain", 22, "auto", None), ("company", 20, "auto", None), ("full_name", 20, "auto", None),
       ("first_name", 11, "auto", None), ("role", 16, "auto", None), ("is_ceo", 6, "auto", None),
       ("technical", 8, "auto", None), ("email", 28, "auto", None), ("email_status", 18, "auto", None),
       ("linkedin", 12, "auto", None), ("x", 12, "auto", None), ("source", 11, "auto", None),
       ("touches", 7, "sync", "0"), ("last_touch", 11, "sync", "yyyy-mm-dd"), ("outcome", 11, "sync", None),
       ("do_not_contact", 9, "you", None), ("notes", 30, "you", None)]
LOG = [("touch_id", 17, "sync", None), ("date", 15, "sync", "yyyy-mm-dd hh:mm"), ("mailbox", 17, "sync", None),
       ("direction", 6, "sync", None), ("channel", 8, "sync", None), ("step", 6, "sync", None),
       ("contact_email", 26, "sync", None), ("contact_name", 16, "sync", None), ("company_domain", 18, "sync", None),
       ("company", 16, "f", None), ("subject", 30, "sync", None), ("snippet", 45, "sync", None),
       ("thread_key", 22, "sync", None), ("msg_id", 8, "sync", None), ("class_auto", 9, "sync", None),
       ("class_human", 10, "you", None), ("class", 9, "f", None), ("score", 6, "f", "0"),
       ("best_reply_after", 8, "f", "0"), ("outcome", 10, "f", None), ("delivered", 6, "f", "0"),
       ("replied", 6, "f", "0"), ("positive", 6, "f", "0"), ("meeting", 6, "f", "0"),
       ("our_response_h", 8, "f", "0.0"), ("notes", 30, "you", None), ("link", 8, "sync", None),
       ("thread_id", 17, "sync", None), ("rfc_id", 14, "sync", None), ("source", 13, "sync", None)]
ANG = [("angle_id", 8, "you", None), ("angle", 24, "you", None), ("belief", 46, "you", None),
       ("segment", 26, "you", None), ("trigger", 24, "you", None), ("offer", 34, "you", None),
       ("intended_response", 20, "you", None), ("evidence_needed", 28, "you", None), ("status", 9, "you", None),
       ("owner", 9, "you", None), ("created", 11, "you", "yyyy-mm-dd"), ("messages", 8, "f", "0"),
       ("delivered", 8, "f", "0"), ("replied", 8, "f", "0"), ("positive", 8, "f", "0"), ("meetings", 8, "f", "0"),
       ("reply_rate", 8, "f", "0.0%"), ("positive_rate", 9, "f", "0.0%")]
MSG = [("msg_id", 7, "you", None), ("angle_id", 8, "you", None), ("step", 6, "you", None), ("status", 8, "you", None),
       ("subject", 30, "you", None), ("body", 70, "you", None), ("word_count", 7, "f", "0"),
       ("match_phrase", 24, "you", None), ("rationale", 36, "you", None), ("qc_true", 6, "you", None),
       ("qc_relevant", 7, "you", None), ("qc_human", 7, "you", None), ("qc_one_ask", 7, "you", None),
       ("qc_safe", 6, "you", None), ("qc_status", 11, "f", None), ("ends_sequence", 8, "you", None),
       ("version_date", 11, "you", "yyyy-mm-dd"), ("sent", 6, "f", "0"), ("delivered", 8, "f", "0"),
       ("replied", 7, "f", "0"), ("positive", 7, "f", "0"), ("meetings", 8, "f", "0"),
       ("reply_rate", 8, "f", "0.0%"), ("positive_rate", 9, "f", "0.0%")]
EXP = [("exp_id", 8, "you", None), ("question", 40, "you", None), ("segment", 24, "you", None),
       ("variable", 16, "you", None), ("arm_a", 7, "you", None), ("arm_b", 7, "you", None),
       ("start", 11, "you", "yyyy-mm-dd"), ("end", 11, "you", "yyyy-mm-dd"), ("metric", 8, "you", None),
       ("min_n", 6, "you", "0"), ("status", 9, "you", None), ("a_delivered", 8, "f", "0"), ("a_hits", 6, "f", "0"),
       ("a_rate", 7, "f", "0.0%"), ("b_delivered", 8, "f", "0"), ("b_hits", 6, "f", "0"), ("b_rate", 7, "f", "0.0%"),
       ("lift", 7, "f", "+0.0%;-0.0%;0.0%"), ("z", 6, "f", "0.00"), ("verdict", 24, "f", None),
       ("decision", 34, "you", None), ("learning_id", 9, "you", None)]
LRN = [("learning_id", 8, "you", None), ("date", 11, "you", "yyyy-mm-dd"), ("statement", 60, "you", None),
       ("level", 13, "you", None), ("evidence", 22, "you", None), ("n", 5, "you", "0"), ("counter", 30, "you", None),
       ("applies_to", 20, "you", None), ("scope", 9, "you", None), ("action", 40, "you", None),
       ("owner", 10, "you", None), ("next_review", 11, "you", "yyyy-mm-dd"), ("status", 9, "you", None),
       ("review_due", 9, "f", None)]
QUE = [("priority", 7, "sync", "0"), ("action", 18, "sync", None), ("due", 11, "sync", "yyyy-mm-dd"),
       ("owner", 16, "sync", None), ("company", 18, "sync", None), ("company_domain", 18, "sync", None),
       ("contact", 16, "sync", None), ("email", 26, "sync", None), ("why", 60, "sync", None),
       ("suggested_msg", 9, "sync", None), ("link", 8, "sync", None), ("thread_id", 16, "sync", None)]

DICT = {  # tab -> {column: meaning}; columns not listed fall back to the kind
    "Log": {"touch_id": "Gmail message id (sync) or manual-… for a touch you add by hand.",
            "mailbox": "Team mailbox that owns the thread (who sent the first touch).",
            "step": "1st, FU1, FU2 … for the sequence; Reply = conversation after the prospect answered.",
            "company_domain": "Account the thread belongs to (from the first outbound recipient, not the replier).",
            "thread_key": "company_domain|normalised subject. Same conversation across mailboxes; formulas group by it.",
            "msg_id": "Message (copy) used. Auto-tagged by Messages.match_phrase; type over it if wrong. CONV = conversation.",
            "class_auto": "Rule-based reply class (sync). Never market evidence on its own: confirm in class_human.",
            "class_human": "Your reply class. Overrides class_auto.",
            "best_reply_after": "Out rows: best reply score in the thread after this message (blank = no reply).",
            "delivered": "Out rows: 1 unless the thread bounced.", "replied": "Out rows: 1 if a human reply (score >= 0) followed.",
            "positive": "Out rows: 1 if a positive or meeting reply followed.",
            "our_response_h": "In rows: hours until our next outbound in the thread.",
            "source": "gmail_sync | gmail_backfill | backfill_quoted | manual."},
    "Accounts": {"fit_tier": "Pipeline ICP tier (A-D, Outside SAM with reason, or Unscored for accounts found in Gmail).",
                 "ring": "Effort ring from the pipeline: R1 sniper, R2 rifle, R3 net, Enrich, Review.",
                 "why_now": "Verified facts with dates (pipeline). Facts only: put beliefs in assumption.",
                 "suggested_angle": "Angle the signals point to (H1/H10 -> ANG-05, fresh H2 -> ANG-06, else ANG-04).",
                 "status": "From Log: Not contacted, Contacted, Replied, Positive, Meeting, Bounced, Do not contact.",
                 "deal_stage": "Your stage after a reply: Discovery, Proposal, Design partner, Paid, Lost, Not a fit, Do not contact.",
                 "assumption": "What you believe about their pain. A hypothesis, not a fact.",
                 "angle": "Angle you chose (overrides suggested_angle)."},
    "Messages": {"match_phrase": "Lower-case phrase(s) unique to this copy, separated by |. The sync tags sent mail with it.",
                 "qc_status": "Approved only when all five QC checks are Y. Drafts are created only from Approved + Live/Testing.",
                 "ends_sequence": "Y if this message promises no more follow-ups (no FU is queued after it).",
                 "qc_true": "Every claim and personal detail is true at send time.", "qc_relevant": "Matters to this buyer.",
                 "qc_human": "Reads like a person, not a template.", "qc_one_ask": "One clear, easy ask.",
                 "qc_safe": "Compliance and deliverability: opt-out respected, no spam triggers, plain text."},
    "Experiments": {"metric": "reply = any human reply; positive = positive or meeting.",
                    "a_delivered": "Delivered sends of arm_a between start and end (end blank = today).",
                    "z": "Two-proportion z score, pooled.",
                    "verdict": "Collecting until both arms reach min_n; then |z| >= 1.96 wins, >= 1.28 leaning."},
    "Learnings": {"level": "Observation -> Hypothesis -> Repeated evidence -> Principle.",
                  "scope": "General (any campaign), System (how we operate), Client-specific (keep confidential).",
                  "review_due": "DUE when next_review has passed and status is Open."},
}
KIND_TXT = {"auto": "pipeline", "sync": "Apps Script", "you": "you", "f": "formula"}

# ------------------------------------------------------------------------------------------- helpers


def L(cols, name):
    return get_column_letter([c[0] for c in cols].index(name) + 1)


def clean(v):
    if isinstance(v, str):
        v = ILLEGAL_CHARACTERS_RE.sub("", v)
        return v[:32000]
    return v


def as_dt(v):
    """ISO string / datetime -> naive local (Asia/Kolkata) datetime."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v
    s = str(v)
    try:
        if s.endswith("Z") or "+" in s[10:]:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return (d.astimezone(timezone.utc) + TZ_OFFSET).replace(tzinfo=None)
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def as_date(v):
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def header(ws, cols, row=1):
    for i, (name, width, kind, _) in enumerate(cols, 1):
        c = ws.cell(row=row, column=i, value=name)
        c.font, c.fill, c.alignment, c.border = F_HEAD, FILL[kind], CENTER, BOX
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[row].height = 30
    ws.freeze_panes = ws.cell(row=row + 1, column=3)
    ws.auto_filter.ref = f"A{row}:{get_column_letter(len(cols))}{row}"


def write_rows(ws, cols, rows, start=2, formulas=None, n_rows=None):
    """rows: list of dicts; formulas: {col: f(r) -> str}; you-columns get the yellow fill."""
    formulas = formulas or {}
    n = max(len(rows), n_rows or 0)
    for i in range(n):
        r = start + i
        d = rows[i] if i < len(rows) else {}
        for j, (name, _, kind, fmt) in enumerate(cols, 1):
            cell = ws.cell(row=r, column=j)
            if kind == "f":
                cell.value = formulas[name](r)
            else:
                v = d.get(name)
                if v not in (None, ""):
                    cell.value = clean(v)
            cell.font = F
            if kind == "you":
                cell.fill, cell.font = FILL["input"], F_INPUT
            if fmt:
                cell.number_format = fmt
    return n


def validate(ws, cols, name, options, last):
    dv = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=True, showErrorMessage=False)
    col = L(cols, name)
    dv.add(f"{col}2:{col}{last}")
    ws.add_data_validation(dv)


def cf_text(ws, rng, first_cell, mapping):
    for text, fill in mapping.items():
        ws.conditional_formatting.add(rng, FormulaRule(formula=[f'{first_cell}="{text}"'], fill=FILL[fill]))


def norm_subject(s):
    s = (s or "").strip().lower()
    while True:
        n = re.sub(r"^(re|fw|fwd|aw|sv)\s*:\s*", "", s)
        if n == s:
            break
        s = n
    return re.sub(r"\s+", " ", s)


# ------------------------------------------------------------------------------------------- 1. sync back
def _rows(ws):
    hdr = [c.value for c in ws[1]]
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        d = {h: v for h, v in zip(hdr, row) if h}
        out.append(d)
    return hdr, out


def _jsonable(v):
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, date):
        return v.isoformat()
    return v


def sync_workspace(conn, xlsx: Path) -> dict:
    """Copy the owner/automation tabs of a v5 workbook (e.g. a Google Sheets download) into workspace_rows."""
    if not xlsx.exists():
        return {"synced": False}
    wb = load_workbook(xlsx, data_only=True, read_only=True)
    if "Log" not in wb.sheetnames:
        return {"synced": False, "reason": "not a v5 workbook"}
    now = pdb.utcnow()
    n = defaultdict(int)

    def put(tab, key, data):
        conn.execute("INSERT INTO workspace_rows (tab, row_key, data, source, updated_at) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(tab, row_key) DO UPDATE SET data=excluded.data, source=excluded.source, "
                     "updated_at=excluded.updated_at", (tab, key, json.dumps(data), "workbook", now))
        n[tab] += 1

    specs = {"Log": (LOG, "touch_id"), "Angles": (ANG, "angle_id"), "Messages": (MSG, "msg_id"),
             "Experiments": (EXP, "exp_id"), "Learnings": (LRN, "learning_id")}
    for tab, (cols, key) in specs.items():
        if tab not in wb.sheetnames:
            continue
        keep = [c[0] for c in cols if c[2] != "f"]
        _, rows = _rows(wb[tab])
        for r in rows:
            k = r.get(key)
            if not k and tab == "Log" and r.get("date"):
                k = f"manual-{_jsonable(r.get('date'))}-{r.get('contact_email') or r.get('company_domain')}"
                r["touch_id"] = k
            if not k:
                continue
            put(tab, str(k), {c: _jsonable(r.get(c)) for c in keep if r.get(c) not in (None, "")})
    for tab, cols, key, notes in (("Accounts", ACC, "company_domain", "AccountNotes"),
                                  ("Contacts", CON, "email", "ContactNotes")):
        if tab not in wb.sheetnames:
            continue
        you = [c[0] for c in cols if c[2] == "you"]
        _, rows = _rows(wb[tab])
        for r in rows:
            vals = {c: _jsonable(r.get(c)) for c in you if r.get(c) not in (None, "")}
            k = r.get(key) or (f"{r.get('company_domain')}|{r.get('full_name')}" if tab == "Contacts" else None)
            if vals and k:
                put(notes, str(k).lower(), vals)
    if "Settings" in wb.sheetnames:
        ws = wb["Settings"]
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        for t in _settings_tables(grid):
            for rec in t["rows"]:
                if t["name"] == "key" and rec.get("key"):
                    put("Settings", rec["key"], {"key": rec["key"], "value": _jsonable(rec.get("value"))})
                elif t["name"] == "mailbox" and rec.get("mailbox"):
                    put("Mailboxes", rec["mailbox"], {k: _jsonable(v) for k, v in rec.items() if v not in (None, "")})
    conn.commit()
    return {"synced": True, **n}


def _settings_tables(grid):
    """Find the key/value and mailbox tables in the Settings grid by their header cell."""
    out = []
    for ri, row in enumerate(grid):
        for ci, v in enumerate(row):
            nx = row[ci + 1] if ci + 1 < len(row) else None
            if ((v == "key" and nx == "value") or (v == "mailbox" and nx == "person")) and ri + 1 < len(grid) \
                    and v not in {t["name"] for t in out}:
                hdr = []
                for cj in range(ci, len(row)):
                    if row[cj] in (None, ""):
                        break
                    hdr.append(row[cj])
                recs = []
                for r2 in grid[ri + 1:]:
                    if ci >= len(r2) or r2[ci] in (None, ""):
                        break
                    recs.append({h: (r2[ci + k] if ci + k < len(r2) else None) for k, h in enumerate(hdr)})
                out.append({"name": v, "rows": recs})
    return out


# ------------------------------------------------------------------------------------------- 2. derive
def workspace(conn) -> dict:
    ws = defaultdict(dict)
    for r in conn.execute("SELECT tab, row_key, data FROM workspace_rows"):
        ws[r["tab"]][r["row_key"]] = json.loads(r["data"])
    return ws


def thread_stats(log: list[dict]) -> dict:
    """thread_key -> summary used for Accounts / Contacts status and the Queue (mirrors Code.gs)."""
    th = defaultdict(list)
    for r in log:
        if r.get("thread_key"):
            th[r["thread_key"]].append(r)
    out = {}
    for k, rs in th.items():
        rs.sort(key=lambda r: r["_dt"] or datetime.min)
        ins = [r for r in rs if r.get("direction") == "in"]
        outs = [r for r in rs if r.get("direction") == "out"]
        scores = [SCORE.get(r.get("class_human") or r.get("class_auto") or "", None) for r in ins]
        scores = [s for s in scores if s is not None]
        best = max(scores) if scores else None
        out[k] = {"rows": rs, "ins": ins, "outs": outs, "best": best, "last": rs[-1],
                  "first_out": outs[0] if outs else None, "last_out": outs[-1] if outs else None,
                  "bounced": -9 in scores, "unsub": -1 in scores, "real_reply": any(s >= 0 for s in scores)}
    return out


def outcome_of(best):
    if best is None:
        return "no reply"
    for c, s, _, _ in CLASSES:
        if s == best:
            return c
    return ""


def status_of(threads):
    """Account / contact status from its threads (mirrors Code.gs statusOf)."""
    if not threads:
        return "Not contacted"
    best = max((t["best"] for t in threads if t["best"] is not None), default=None)
    if any(t["unsub"] for t in threads):
        return "Do not contact"
    if best is not None and best >= 7:
        return "Meeting"
    if best is not None and best >= 6:
        return "Positive"
    if best is not None and best >= 0:
        return "Replied"
    if any(t["bounced"] for t in threads) and not any(t["real_reply"] for t in threads):
        return "Bounced"
    return "Contacted"


def suggested_angle(r: dict) -> str:
    sig = set((r.get("signals") or "").split("+"))
    if sig & {"H1", "H10"}:
        return "ANG-05"
    if "H2" in sig and (r.get("funding_status") or "").startswith("verified") and r.get("last_raise_date") and \
            (date.today() - r["last_raise_date"]).days <= 60:
        return "ANG-06"
    return "ANG-04"


def pick_arms(exps, msgs, step):
    """Arms of the first Running experiment whose arms are usable (Live/Testing + QC Approved) for this step."""
    ok = {m["msg_id"] for m in msgs if m.get("status") in ("Live", "Testing") and m.get("_qc") == "Approved"
          and ((m.get("step") == "1st") == (step == "1st"))}
    for x in exps:
        if x.get("status") == "Running" and x.get("arm_a") in ok and x.get("arm_b") in ok:
            return [x["arm_a"], x["arm_b"]], x["exp_id"]
    return sorted(ok), ""


def build_queue(accounts, log, msgs, settings, today: date, exps=(), mailboxes=()) -> list[dict]:
    """Mirror of Code.gs buildQueue(): reply now > nudge > fix bounce > follow up > first touch > tag."""
    fu1 = int(settings.get("follow_up_1_after_days", 3) or 3)
    fu2 = int(settings.get("follow_up_2_after_days", 4) or 4)
    max_fu = int(settings.get("max_follow_ups", 2) or 2)
    n_new = int(settings.get("new_first_touches_per_day", 20) or 20)
    acc = {a["company_domain"]: a for a in accounts}
    ends = {m["msg_id"] for m in msgs if (m.get("ends_sequence") or "").upper() == "Y"}
    fu_arms, fu_exp = pick_arms(exps, msgs, "FU")
    new_arms, new_exp = pick_arms(exps, msgs, "1st")
    person_mb = {(m.get("person") or "").lower(): m.get("mailbox") for m in mailboxes if m.get("mailbox")}
    rr = [m.get("mailbox") for m in mailboxes if m.get("mailbox")]
    q, n_fu = [], 0
    for k, t in thread_stats(log).items():
        last, lo, fo = t["last"], t["last_out"], t["first_out"]
        a = acc.get((fo or last).get("company_domain") or "", {})
        owner = (fo or last).get("mailbox") or ""
        base = {"owner": owner, "company": a.get("company") or (fo or last).get("company_domain"),
                "company_domain": (fo or last).get("company_domain"), "contact": (fo or last).get("contact_name"),
                "email": (lo or last).get("contact_email"), "link": last.get("link"), "thread_id": last.get("thread_id")}
        closed = (a.get("deal_stage") or "") in CLOSED_DEALS
        if t["unsub"]:
            continue
        ls = SCORE.get(last.get("class_human") or last.get("class_auto") or "", None)
        if last.get("direction") == "in" and ls is not None and ls >= 1:
            q.append({**base, "priority": 1, "action": "Confirm meeting" if ls >= 7 else "Reply now", "due": last["_dt"].date(),
                      "why": f"{last.get('class_human') or last.get('class_auto')}: \"{(last.get('snippet') or '')[:120]}\" "
                             f"({(today - last['_dt'].date()).days} d ago). Booking link in Settings.",
                      "suggested_msg": "CONV"})
            continue
        if closed or not lo:
            continue
        days = (today - lo["_dt"].date()).days
        if t["real_reply"]:
            if lo["_dt"].date() + timedelta(days=fu1) <= today + timedelta(days=1):
                q.append({**base, "priority": 2, "action": "Nudge conversation", "due": lo["_dt"].date() + timedelta(days=fu1),
                          "why": f"Conversation is waiting on them for {days} d. Last: \"{(lo.get('snippet') or '')[:100]}\"",
                          "suggested_msg": "CONV"})
            continue
        if t["bounced"]:
            q.append({**base, "priority": 2, "action": "Fix bounce", "due": today,
                      "why": "Bounced. Find another address (Contacts / Clay) and re-send.", "suggested_msg": ""})
            continue
        seq = [r for r in t["outs"] if r.get("step") != "Reply"]
        if any(r.get("msg_id") in ends for r in seq):
            continue
        sent_fu = len(seq) - 1
        if sent_fu >= max_fu:
            continue
        due = lo["_dt"].date() + timedelta(days=fu1 if sent_fu == 0 else fu2)
        if due <= today + timedelta(days=1):
            nxt = f"FU{sent_fu + 1}"
            used = {r.get("msg_id") for r in seq}
            free = [m for m in fu_arms if m not in used]
            sugg = free[n_fu % len(free)] if free else ""
            n_fu += 1
            why = f"No reply {days} d after {lo.get('step')} ({lo.get('msg_id')})."
            why += f" {fu_exp} arm." if sugg and fu_exp else "" if sugg else " No unused approved follow-up: write one in Messages."
            q.append({**base, "priority": 3, "action": f"Follow up ({nxt})", "due": due, "why": why,
                      "suggested_msg": sugg})
    for r in log:
        if r.get("direction") == "out" and (r.get("msg_id") in (None, "", "UNTAGGED")):
            q.append({"priority": 5, "action": "Tag msg_id", "due": today, "owner": r.get("mailbox"),
                      "company_domain": r.get("company_domain"), "contact": r.get("contact_name"),
                      "email": r.get("contact_email"), "why": f"Sent {r['_dt']:%Y-%m-%d}: set msg_id in Log so it counts in experiments.",
                      "link": r.get("link"), "thread_id": r.get("thread_id")})
    rings = ["R1 Sniper", "R2 Rifle", "R3 Net", "Enrich", "Review"]
    cands = [a for a in accounts if a.get("status") == "Not contacted" and a.get("ceo_email")
             and a.get("email_confidence") in GOOD_EMAIL and not a.get("deal_stage")
             and (a.get("fit_tier") or "")[:1] in ("A", "B") and a.get("ring") in ("R1 Sniper", "R2 Rifle")]
    cands.sort(key=lambda a: (rings.index(a["ring"]), a.get("rank") or 10 ** 6))
    for i, a in enumerate(cands[:n_new]):
        owner = person_mb.get((a.get("owner") or "").lower()) or (rr[i % len(rr)] if rr else "")
        sugg = new_arms[i % len(new_arms)] if new_arms else ""
        why = (a.get("why_now") or a.get("one_liner") or "")[:200] + (f" [{new_exp} arm]" if new_exp and sugg else "")
        q.append({"priority": 4, "action": "First touch", "due": today, "owner": owner,
                  "company": a.get("company"), "company_domain": a["company_domain"], "contact": a.get("ceo_name"),
                  "email": a.get("ceo_email"), "why": why, "suggested_msg": sugg, "link": a.get("website")})
    q.sort(key=lambda r: (r["priority"], r.get("due") or today))
    return q


# ------------------------------------------------------------------------------------------- 3. build
def build(data: dict, conn, xlsx: Path, out_dir: Path) -> dict:
    W = workspace(conn)
    settings = {k: v.get("value") for k, v in W["Settings"].items()}
    today = date.today()

    # ---- Log
    log = []
    for k, r in W["Log"].items():
        r = dict(r)
        r["touch_id"] = k
        r["_dt"] = as_dt(r.get("date"))
        if not r.get("thread_key") and r.get("company_domain"):
            r["thread_key"] = f"{r['company_domain']}|{norm_subject(r.get('subject'))}"
        log.append(r)
    log = [r for r in log if r["_dt"]]
    log.sort(key=lambda r: r["_dt"])
    for r in log:
        r["date"] = r["_dt"]
    stats = thread_stats(log)

    # ---- Accounts (SAM rows in work order, then Outside SAM, then accounts only seen in Gmail)
    notes = {k.lower(): v for k, v in W["AccountNotes"].items()}
    by_dom = defaultdict(list)
    for t in stats.values():
        d = (t["first_out"] or t["last"]).get("company_domain")
        if d:
            by_dom[d].append(t)
    accounts, seen = [], set()
    for src in (data["rows"], data["outside"]):
        for m in src:
            d = m["company_domain"]
            seen.add(d)
            tier = m["icp_tier"] if m["icp_tier"] != "Outside SAM" else f"Outside SAM · {m.get('outside_sam_reason') or ''}"
            amt = m.get("raise_amount_usd")
            rnd = " ".join(x for x in [(m.get("raise_stage") or "").replace("_", " "),
                                       f"${amt / 1e6:.1f}M" if amt else ""] if x) or None
            a = {"rank": m["rank"], "company_domain": d, "company": m["company_name"], "fit_tier": tier,
                 "fit_score": m.get("icp_fit_score"), "ring": m.get("ring"), "wave": m.get("wave"),
                 "sector": m.get("sector_category"), "one_liner": m.get("one_liner"), "hq": m.get("hq_country"),
                 "batch_or_investor": m.get("batch_or_investor"), "headcount": m.get("headcount") or m.get("headcount_band"),
                 "last_round": rnd, "last_raise_date": m.get("last_raise_date"), "signals": m.get("signals"),
                 "why_now": m.get("why_now"), "hiring_role": m.get("hiring_role"), "ceo_name": m.get("ceo_name"),
                 "ceo_email": m.get("email"), "email_confidence": m.get("email_confidence"),
                 "ceo_linkedin": m.get("ceo_linkedin"), "website": m.get("website"),
                 "funding_status": m.get("funding_status")}
            a["suggested_angle"] = suggested_angle(a)
            accounts.append(a)
    for d, ts in sorted(by_dom.items()):
        if d not in seen:
            fo = ts[0]["first_out"] or ts[0]["last"]
            accounts.append({"company_domain": d, "company": d.split(".")[0].title(), "fit_tier": "Unscored (from Gmail)",
                             "ceo_name": fo.get("contact_name"), "ceo_email": fo.get("contact_email"),
                             "email_confidence": "gmail", "website": f"https://{d}", "suggested_angle": "ANG-04"})
            seen.add(d)
    for a in accounts:
        ts = by_dom.get(a["company_domain"], [])
        outs = [r for t in ts for r in t["outs"]]
        a["touches"] = len(outs) or None
        a["last_touch"] = max((r["_dt"] for t in ts for r in t["rows"]), default=None)
        a["outcome"] = outcome_of(max((t["best"] for t in ts if t["best"] is not None), default=None)) if ts else None
        a["status"] = status_of(ts)
        a.update(notes.get(a["company_domain"].lower(), {}))
        for k in ("next_date",):
            a[k] = as_date(a.get(k))
    # ---- Contacts (every founder of every account + people only seen in Gmail)
    cnotes = {k.lower(): v for k, v in W["ContactNotes"].items()}
    names = {a["company_domain"]: a["company"] for a in accounts}
    emails = {r["company_domain"]: dict(r) for r in conn.execute("SELECT * FROM company_emails")}
    contacts, cseen = [], set()
    for f in conn.execute("SELECT * FROM founders ORDER BY company_domain, founder_id"):
        d = f["company_domain"]
        if d not in names:
            continue
        e = emails.get(d) or {}
        em = f["verified_email"] or (e.get("email") if e.get("contact_name") == f["full_name"] else None)
        st = (e.get("email_confidence") if e.get("contact_name") == f["full_name"] else f["verification_status"])
        contacts.append({"company_domain": d, "company": names[d], "full_name": f["full_name"],
                         "first_name": (f["full_name"] or "").split(" ")[0] or None, "role": f["role"],
                         "is_ceo": "Y" if re.search(r"\bceo\b|chief executive", f["role"] or "", re.I) else None,
                         "technical": {1: "Y", 0: "N"}.get(f["is_technical"]), "email": em, "email_status": st,
                         "linkedin": f["linkedin_url"], "x": f"@{f['x_handle']}" if f["x_handle"] else None,
                         "source": "pipeline"})
        if em:
            cseen.add(em.lower())
    for r in log:
        em = (r.get("contact_email") or "").lower()
        if em and em not in cseen and r.get("company_domain"):
            cseen.add(em)
            contacts.append({"company_domain": r["company_domain"], "company": names.get(r["company_domain"]),
                             "full_name": r.get("contact_name"), "first_name": (r.get("contact_name") or "").split(" ")[0],
                             "email": em, "email_status": "gmail", "source": "gmail"})
    by_email = defaultdict(list)
    for r in log:
        if r.get("contact_email"):
            by_email[r["contact_email"].lower()].append(r)
    for c in contacts:
        rs = by_email.get((c.get("email") or "").lower(), [])
        if rs:
            c["touches"] = sum(r.get("direction") == "out" for r in rs) or None
            c["last_touch"] = max(r["_dt"] for r in rs)
            ks = {r["thread_key"] for r in rs}
            c["outcome"] = outcome_of(max((stats[k]["best"] for k in ks if stats[k]["best"] is not None), default=None))
        key = (c.get("email") or f"{c['company_domain']}|{c.get('full_name')}").lower()
        c.update(cnotes.get(key, {}))

    # ---- Messages / Angles / Experiments / Learnings
    def ordered(tab, key, seed_order):
        rows = list(W[tab].values())
        pos = {k: i for i, k in enumerate(seed_order)}
        return sorted(rows, key=lambda r: (pos.get(r.get(key), 10 ** 6), str(r.get(key))))
    import workspace_seed as seedmod
    msgs = ordered("Messages", "msg_id", [m["msg_id"] for m in seedmod.MESSAGES])
    for m in msgs:
        qc = [m.get(k) for k in ("qc_true", "qc_relevant", "qc_human", "qc_one_ask", "qc_safe")]
        m["_qc"] = "Rejected" if "N" in qc else "Approved" if qc.count("Y") == 5 else "Needs review"
    angles = ordered("Angles", "angle_id", [a["angle_id"] for a in seedmod.ANGLES])
    exps = ordered("Experiments", "exp_id", [e["exp_id"] for e in seedmod.EXPERIMENTS])
    lrns = ordered("Learnings", "learning_id", [x["learning_id"] for x in seedmod.LEARNINGS])
    mailboxes = list(W["Mailboxes"].values())
    queue = build_queue(accounts, log, msgs, settings, today, exps, mailboxes)

    wb = Workbook()
    home = wb.active
    home.title = "Home"
    sheets = {n: wb.create_sheet(n) for n in ["Queue", "Log", "Accounts", "Contacts", "Angles", "Messages",
                                              "Experiments", "Learnings", "Settings"]}
    sheets["Home"] = home
    for n, s in sheets.items():
        s.sheet_properties.tabColor = TAB_COLOR[n]
        s.sheet_view.showGridLines = n not in ("Home", "Settings")

    S = settings_sheet(sheets["Settings"], settings, mailboxes)
    # Log
    lg = sheets["Log"]
    header(lg, LOG)
    c = {n: L(LOG, n) for n, *_ in LOG}
    sc = f"Settings!${S['cls_col']}${S['cls_r0']}:${S['score_col']}${S['cls_r1']}"
    cls_rng = f"Settings!${S['cls_col']}${S['cls_r0']}:${S['cls_col']}${S['cls_r1']}"
    score_rng = f"Settings!${S['score_col']}${S['cls_r0']}:${S['score_col']}${S['cls_r1']}"
    M, D, B, R = (f"${c['thread_key']}:${c['thread_key']}", f"${c['direction']}:${c['direction']}",
                  f"${c['date']}:${c['date']}", f"${c['score']}:${c['score']}")
    after = lambda r, d: f'{M},{c["thread_key"]}{r},{D},"{d}",{B},">"&{c["date"]}{r}'  # noqa: E731
    lf = {
        "company": lambda r: f'=IF({c["company_domain"]}{r}="","",IFERROR(INDEX(Accounts!$C:$C,MATCH({c["company_domain"]}{r},Accounts!$B:$B,0)),""))',
        "class": lambda r: f'=IF({c["direction"]}{r}="in",IF({c["class_human"]}{r}<>"",{c["class_human"]}{r},{c["class_auto"]}{r}),"")',
        "score": lambda r: f'=IF({c["class"]}{r}="","",IFERROR(VLOOKUP({c["class"]}{r},{sc},2,FALSE),""))',
        "best_reply_after": lambda r: f'=IF({c["direction"]}{r}<>"out","",IF(COUNTIFS({after(r, "in")})=0,"",_xlfn.MAXIFS({R},{after(r, "in")})))',
        "outcome": lambda r: f'=IF({c["direction"]}{r}<>"out","",IF({c["best_reply_after"]}{r}="","no reply",IFERROR(INDEX({cls_rng},MATCH({c["best_reply_after"]}{r},{score_rng},0)),"")))',
        "delivered": lambda r: f'=IF({c["direction"]}{r}<>"out","",IF({c["best_reply_after"]}{r}="",1,IF({c["best_reply_after"]}{r}=-9,0,1)))',
        "replied": lambda r: f'=IF({c["direction"]}{r}<>"out","",IF({c["best_reply_after"]}{r}="",0,IF({c["best_reply_after"]}{r}>=0,1,0)))',
        "positive": lambda r: f'=IF({c["direction"]}{r}<>"out","",IF({c["best_reply_after"]}{r}="",0,IF({c["best_reply_after"]}{r}>=6,1,0)))',
        "meeting": lambda r: f'=IF({c["direction"]}{r}<>"out","",IF({c["best_reply_after"]}{r}="",0,IF({c["best_reply_after"]}{r}>=7,1,0)))',
        "our_response_h": lambda r: f'=IF({c["direction"]}{r}<>"in","",IF(COUNTIFS({after(r, "out")})=0,"",ROUND((_xlfn.MINIFS({B},{after(r, "out")})-{c["date"]}{r})*24,1)))',
    }
    nlog = write_rows(lg, LOG, log, formulas=lf, n_rows=LOG_ROWS)
    for i, r in enumerate(log, 2):
        if r.get("link"):
            cell = lg[f"{c['link']}{i}"]
            cell.hyperlink, cell.font = r["link"], F_LINK
    last = nlog + 1
    validate(lg, LOG, "direction", ["out", "in"], last)
    validate(lg, LOG, "channel", ["Email", "LinkedIn", "Call", "X", "Other"], last)
    validate(lg, LOG, "step", ["1st", "FU1", "FU2", "FU3", "Reply"], last)
    validate(lg, LOG, "class_human", [x[0] for x in CLASSES], last)
    cf_text(lg, f"{c['class']}2:{c['class']}{last}", f"{c['class']}2",
            {"meeting": "green", "positive": "green", "negative": "red", "unsubscribe": "red", "bounce": "grey", "ooo": "grey"})
    cf_text(lg, f"{c['outcome']}2:{c['outcome']}{last}", f"{c['outcome']}2",
            {"meeting": "green", "positive": "green", "negative": "red", "bounce": "grey"})
    lg.conditional_formatting.add(f"A2:{get_column_letter(len(LOG))}{last}",
                                  FormulaRule(formula=[f'${c["direction"]}2="in"'], fill=FILL["blue"]))

    # Accounts
    ac = sheets["Accounts"]
    header(ac, ACC)
    a_ = {n: L(ACC, n) for n, *_ in ACC}
    nacc = write_rows(ac, ACC, accounts, formulas={
        "days_since_touch": lambda r: f'=IF({a_["last_touch"]}{r}="","",TODAY()-INT({a_["last_touch"]}{r}))'})
    lastacc = nacc + 1
    for i, a in enumerate(accounts, 2):
        for k in ("ceo_linkedin", "website"):
            if a.get(k) and str(a[k]).startswith("http"):
                cell = ac[f"{a_[k]}{i}"]
                cell.value, cell.hyperlink, cell.font = "open", a[k], F_LINK
    validate(ac, ACC, "owner", OWNERS, lastacc)
    validate(ac, ACC, "deal_stage", ["Discovery", "Proposal", "Design partner", "Paid", "Lost", "Not a fit",
                                     "Do not contact"], lastacc)
    validate(ac, ACC, "angle", [x["angle_id"] for x in angles], lastacc)
    cf_text(ac, f"{a_['status']}2:{a_['status']}{lastacc}", f"{a_['status']}2",
            {"Meeting": "green", "Positive": "green", "Replied": "amber", "Contacted": "blue", "Bounced": "grey",
             "Do not contact": "red"})
    cf_text(ac, f"{a_['deal_stage']}2:{a_['deal_stage']}{lastacc}", f"{a_['deal_stage']}2",
            {"Design partner": "green", "Paid": "green", "Lost": "red", "Not a fit": "grey"})

    # Contacts
    co = sheets["Contacts"]
    header(co, CON)
    ncon = write_rows(co, CON, contacts)
    for i, ct in enumerate(contacts, 2):
        if ct.get("linkedin") and str(ct["linkedin"]).startswith("http"):
            cell = co[f"{L(CON, 'linkedin')}{i}"]
            cell.value, cell.hyperlink, cell.font = "open", ct["linkedin"], F_LINK
    validate(co, CON, "do_not_contact", ["Y"], ncon + 1)

    # Messages
    ms = sheets["Messages"]
    header(ms, MSG)
    m_ = {n: L(MSG, n) for n, *_ in MSG}
    lsum = lambda col, r: f'SUMIFS(Log!${c[col]}:${c[col]},Log!${c["msg_id"]}:${c["msg_id"]},{m_["msg_id"]}{r})'  # noqa: E731
    for m in msgs:
        m["version_date"] = as_date(m.get("version_date"))
    nmsg = write_rows(ms, MSG, msgs, n_rows=max(len(msgs) + 10, 20), formulas={
        "word_count": lambda r: f'=IF({m_["body"]}{r}="","",LEN(TRIM(SUBSTITUTE({m_["body"]}{r},CHAR(10)," ")))-LEN(SUBSTITUTE(TRIM(SUBSTITUTE({m_["body"]}{r},CHAR(10)," "))," ",""))+1)',
        "qc_status": lambda r: f'=IF({m_["msg_id"]}{r}="","",IF(COUNTIF({m_["qc_true"]}{r}:{m_["qc_safe"]}{r},"N")>0,"Rejected",IF(COUNTIF({m_["qc_true"]}{r}:{m_["qc_safe"]}{r},"Y")=5,"Approved","Needs review")))',
        "sent": lambda r: f'=IF({m_["msg_id"]}{r}="","",COUNTIFS(Log!${c["msg_id"]}:${c["msg_id"]},{m_["msg_id"]}{r},Log!${c["direction"]}:${c["direction"]},"out"))',
        "delivered": lambda r: f'=IF({m_["msg_id"]}{r}="","",{lsum("delivered", r)})',
        "replied": lambda r: f'=IF({m_["msg_id"]}{r}="","",{lsum("replied", r)})',
        "positive": lambda r: f'=IF({m_["msg_id"]}{r}="","",{lsum("positive", r)})',
        "meetings": lambda r: f'=IF({m_["msg_id"]}{r}="","",{lsum("meeting", r)})',
        "reply_rate": lambda r: f'=IF(N({m_["delivered"]}{r})=0,"",{m_["replied"]}{r}/{m_["delivered"]}{r})',
        "positive_rate": lambda r: f'=IF(N({m_["delivered"]}{r})=0,"",{m_["positive"]}{r}/{m_["delivered"]}{r})'})
    for r in range(2, nmsg + 2):
        ms.row_dimensions[r].height = 120 if r < len(msgs) + 2 else 15
        for col in ("body", "rationale", "subject"):
            ms[f"{m_[col]}{r}"].alignment = WRAP
    lm = nmsg + 1
    validate(ms, MSG, "step", ["1st", "FU1", "FU2", "FU3"], lm)
    validate(ms, MSG, "status", ["Draft", "Testing", "Live", "Paused", "Retired"], lm)
    for q in ("qc_true", "qc_relevant", "qc_human", "qc_one_ask", "qc_safe"):
        validate(ms, MSG, q, ["Y", "N", "?"], lm)
        cf_text(ms, f"{m_[q]}2:{m_[q]}{lm}", f"{m_[q]}2", {"N": "red", "?": "amber"})
    validate(ms, MSG, "ends_sequence", ["Y", "N"], lm)
    validate(ms, MSG, "angle_id", [x["angle_id"] for x in angles], lm)
    cf_text(ms, f"{m_['qc_status']}2:{m_['qc_status']}{lm}", f"{m_['qc_status']}2",
            {"Approved": "green", "Needs review": "amber", "Rejected": "red"})
    ms.freeze_panes = "C2"

    # Angles
    an = sheets["Angles"]
    header(an, ANG)
    g = {n: L(ANG, n) for n, *_ in ANG}
    msum = lambda col, r: f'SUMIFS(Messages!${m_[col]}:${m_[col]},Messages!${m_["angle_id"]}:${m_["angle_id"]},{g["angle_id"]}{r},Messages!${m_["step"]}:${m_["step"]},"1st")'  # noqa: E731
    for x in angles:
        x["created"] = as_date(x.get("created"))
    nang = write_rows(an, ANG, angles, n_rows=max(len(angles) + 8, 14), formulas={
        "messages": lambda r: f'=IF({g["angle_id"]}{r}="","",COUNTIF(Messages!${m_["angle_id"]}:${m_["angle_id"]},{g["angle_id"]}{r}))',
        "delivered": lambda r: f'=IF({g["angle_id"]}{r}="","",{msum("delivered", r)})',
        "replied": lambda r: f'=IF({g["angle_id"]}{r}="","",{msum("replied", r)})',
        "positive": lambda r: f'=IF({g["angle_id"]}{r}="","",{msum("positive", r)})',
        "meetings": lambda r: f'=IF({g["angle_id"]}{r}="","",{msum("meetings", r)})',
        "reply_rate": lambda r: f'=IF(N({g["delivered"]}{r})=0,"",{g["replied"]}{r}/{g["delivered"]}{r})',
        "positive_rate": lambda r: f'=IF(N({g["delivered"]}{r})=0,"",{g["positive"]}{r}/{g["delivered"]}{r})'})
    for r in range(2, len(angles) + 2):
        an.row_dimensions[r].height = 48
        for col in ("belief", "segment", "offer", "trigger", "evidence_needed", "intended_response"):
            an[f"{g[col]}{r}"].alignment = WRAP
    validate(an, ANG, "status", ["Idea", "Testing", "Active", "Paused", "Retired"], nang + 1)
    validate(an, ANG, "owner", OWNERS, nang + 1)
    an.freeze_panes = "C2"

    # Experiments
    ex = sheets["Experiments"]
    header(ex, EXP)
    e = {n: L(EXP, n) for n, *_ in EXP}
    for x in exps:
        x["start"], x["end"] = as_date(x.get("start")), as_date(x.get("end"))

    def arm(col_arm, what):
        def f(r):
            win = (f'Log!${c["msg_id"]}:${c["msg_id"]},{e[col_arm]}{r},Log!${c["date"]}:${c["date"]},">="&{e["start"]}{r},'
                   f'Log!${c["date"]}:${c["date"]},"<"&(IF({e["end"]}{r}="",TODAY(),{e["end"]}{r})+1)')
            if what == "n":
                return f'=IF(OR({e[col_arm]}{r}="",{e["start"]}{r}=""),"",SUMIFS(Log!${c["delivered"]}:${c["delivered"]},{win}))'
            return (f'=IF(OR({e[col_arm]}{r}="",{e["start"]}{r}=""),"",IF({e["metric"]}{r}="positive",'
                    f'SUMIFS(Log!${c["positive"]}:${c["positive"]},{win}),SUMIFS(Log!${c["replied"]}:${c["replied"]},{win})))')
        return f
    ad, ah, ar, bd, bh, br = (e[k] for k in ("a_delivered", "a_hits", "a_rate", "b_delivered", "b_hits", "b_rate"))
    nexp = write_rows(ex, EXP, exps, n_rows=max(len(exps) + 10, 15), formulas={
        "a_delivered": arm("arm_a", "n"), "a_hits": arm("arm_a", "x"), "b_delivered": arm("arm_b", "n"),
        "b_hits": arm("arm_b", "x"),
        "a_rate": lambda r: f'=IF(N({ad}{r})=0,"",{ah}{r}/{ad}{r})',
        "b_rate": lambda r: f'=IF(N({bd}{r})=0,"",{bh}{r}/{bd}{r})',
        "lift": lambda r: f'=IF(OR({ar}{r}="",{br}{r}=""),"",{br}{r}-{ar}{r})',
        "z": lambda r: (f'=IF(OR(N({ad}{r})=0,N({bd}{r})=0),"",IF(({ah}{r}+{bh}{r})*({ad}{r}+{bd}{r}-{ah}{r}-{bh}{r})=0,0,'
                        f'({br}{r}-{ar}{r})/SQRT((({ah}{r}+{bh}{r})/({ad}{r}+{bd}{r}))*(1-({ah}{r}+{bh}{r})/({ad}{r}+{bd}{r}))*(1/{ad}{r}+1/{bd}{r}))))'),
        "verdict": lambda r: (f'=IF({e["exp_id"]}{r}="","",IF({e["status"]}{r}="Planned","Planned",IF(OR(N({ad}{r})<N({e["min_n"]}{r}),N({bd}{r})<N({e["min_n"]}{r})),'
                              f'IF(OR({e["status"]}{r}="Readout",{e["status"]}{r}="Closed"),"Underpowered: ","Collecting ")&MIN(N({ad}{r}),N({bd}{r}))&"/"&{e["min_n"]}{r}&" per arm",'
                              f'IF(ABS(N({e["z"]}{r}))>=1.96,IF({br}{r}>{ar}{r},"B wins (95%)","A wins (95%)"),'
                              f'IF(ABS(N({e["z"]}{r}))>=1.28,"Leaning "&IF({br}{r}>{ar}{r},"B","A")&" (80%)","No clear difference")))))')})
    for r in range(2, len(exps) + 2):
        ex.row_dimensions[r].height = 45
        for col in ("question", "segment", "decision"):
            ex[f"{e[col]}{r}"].alignment = WRAP
    validate(ex, EXP, "metric", ["reply", "positive"], nexp + 1)
    validate(ex, EXP, "status", ["Planned", "Running", "Readout", "Closed"], nexp + 1)
    ex.conditional_formatting.add(f"{e['verdict']}2:{e['verdict']}{nexp + 1}",
                                  FormulaRule(formula=[f'ISNUMBER(SEARCH("wins",{e["verdict"]}2))'], fill=FILL["green"]))
    ex.conditional_formatting.add(f"{e['verdict']}2:{e['verdict']}{nexp + 1}",
                                  FormulaRule(formula=[f'LEFT({e["verdict"]}2,10)="Collecting"'], fill=FILL["amber"]))
    ex.conditional_formatting.add(f"{e['verdict']}2:{e['verdict']}{nexp + 1}",
                                  FormulaRule(formula=[f'LEFT({e["verdict"]}2,12)="Underpowered"'], fill=FILL["grey"]))
    ex.freeze_panes = "C2"

    # Learnings
    le = sheets["Learnings"]
    header(le, LRN)
    lr = {n: L(LRN, n) for n, *_ in LRN}
    for x in lrns:
        x["date"], x["next_review"] = as_date(x.get("date")), as_date(x.get("next_review"))
    nlr = write_rows(le, LRN, lrns, n_rows=max(len(lrns) + 30, 40), formulas={
        "review_due": lambda r: f'=IF(OR({lr["next_review"]}{r}="",{lr["status"]}{r}<>"Open"),"",IF({lr["next_review"]}{r}<=TODAY(),"DUE",""))'})
    for r in range(2, len(lrns) + 2):
        le.row_dimensions[r].height = 58
        for col in ("statement", "counter", "action", "evidence", "applies_to"):
            le[f"{lr[col]}{r}"].alignment = WRAP
    validate(le, LRN, "level", ["Observation", "Hypothesis", "Repeated evidence", "Principle"], nlr + 1)
    validate(le, LRN, "scope", ["General", "System", "Client-specific"], nlr + 1)
    validate(le, LRN, "status", ["Open", "Applied", "Disproved", "Retired"], nlr + 1)
    validate(le, LRN, "owner", OWNERS + ["Abhinay + Oukasha"], nlr + 1)
    cf_text(le, f"{lr['level']}2:{lr['level']}{nlr + 1}", f"{lr['level']}2",
            {"Principle": "green", "Repeated evidence": "blue", "Hypothesis": "amber"})
    le.conditional_formatting.add(f"{lr['statement']}2:{lr['statement']}{nlr + 1}",
                                  FormulaRule(formula=[f'LEFT({lr["statement"]}2,5)="EMPTY"'], fill=FILL["empty"]))
    cf_text(le, f"{lr['review_due']}2:{lr['review_due']}{nlr + 1}", f"{lr['review_due']}2", {"DUE": "red"})
    le.freeze_panes = "C2"

    # Queue
    qu = sheets["Queue"]
    header(qu, QUE)
    write_rows(qu, QUE, queue)
    for i, r in enumerate(queue, 2):
        if r.get("link"):
            cell = qu[f"{L(QUE, 'link')}{i}"]
            cell.hyperlink, cell.font = r["link"], F_LINK
        qu[f"{L(QUE, 'why')}{i}"].alignment = WRAP
    qa = L(QUE, "action")
    for act, fill in {"Reply now": "red", "Confirm meeting": "green", "Nudge conversation": "amber", "Fix bounce": "grey",
                      "First touch": "blue"}.items():
        qu.conditional_formatting.add(f"A2:{get_column_letter(len(QUE))}{len(queue) + 200}",
                                      FormulaRule(formula=[f'${qa}2="{act}"'], fill=FILL[fill]))
    qu.conditional_formatting.add(f"A2:{get_column_letter(len(QUE))}{len(queue) + 200}",
                                  FormulaRule(formula=[f'LEFT(${qa}2,6)="Follow"'], fill=FILL["band"]))

    build_home(home, S, c, a_, m_, e, lr, g, mailboxes)
    xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx)
    write_csvs(out_dir, accounts, contacts, log)
    return {"Accounts": len(accounts), "Contacts": len(contacts), "Log": len(log), "Queue": len(queue),
            "Messages": len(msgs), "Angles": len(angles), "Experiments": len(exps), "Learnings": len(lrns)}


# ------------------------------------------------------------------------------------------- Settings
CONFIG_DOC = {
    "team_domain": "Your email domain. Any sender @ this domain counts as team.",
    "timezone": "Sheet timezone (set on connect).",
    "program_start": "Monday of week 1 for the weekly trend on Home.",
    "sync_every_minutes": "How often each connected mailbox syncs: 5, 10, 15 or 30.",
    "first_sync_lookback_days": "How far back the first sync of a newly connected mailbox reads.",
    "follow_up_1_after_days": "Days after the first touch before FU1 is queued.",
    "follow_up_2_after_days": "Days after FU1 before FU2 is queued.",
    "max_follow_ups": "Follow-ups per thread before the sequence stops.",
    "new_first_touches_per_day": "First-touch rows the Queue proposes per day.",
    "daily_send_cap_per_mailbox": "Safety cap for drafts the script creates per mailbox per day.",
    "bounce_alert_rate": "Home turns red above this bounce rate.",
    "min_n_per_arm": "Default sample per experiment arm before a verdict.",
    "exclude_addresses": "Never logged (personal / internal / test).",
    "exclude_domains": "Never logged (tools, calendars, deliverability testers).",
    "digest_enabled": "Y = each connected person gets a daily email with their queue.",
    "digest_hour": "Hour of the daily digest (sheet timezone).",
    "booking_link": "Calendly / Cal.com link used in replies and digests.",
    "research_folder_id": "Google Drive folder id holding accounts.csv / contacts.csv from the pipeline (8-o > Import research).",
    "gmail_label": "If set, the sync labels tracked threads in Gmail with this label.",
}
EMPTY_HINT = {"booking_link": "EMPTY: paste your booking link (Calendly / Cal.com)",
              "research_folder_id": "EMPTY: Drive folder id for pipeline CSV refresh (optional)"}


def settings_sheet(ws, settings, mailboxes) -> dict:
    ws["A1"], ws["A1"].font = "Settings", F_TITLE
    ws["A2"] = "The Apps Script reads these tables by their header cells (key, class, mailbox). Yellow = edit."
    ws["A2"].font = F_SUB
    for col, w in zip("ABCDEFGHIJKLMNOP", [26, 34, 52, 2, 13, 7, 18, 40, 2, 18, 11, 12, 14, 18, 10, 18]):
        ws.column_dimensions[col].width = w
    # config
    ws["A3"], ws["A3"].font = "Config", F_H2
    for j, h in enumerate(["key", "value", "meaning"], 1):
        cell = ws.cell(row=4, column=j, value=h)
        cell.font, cell.fill, cell.alignment = F_HEAD, FILL["you"], CENTER
    keys = list(CONFIG_DOC)
    settings.setdefault("timezone", "Asia/Kolkata")
    pos = {}
    for i, k in enumerate(keys, 5):
        v = settings.get(k)
        if v in (None, "") and k in EMPTY_HINT:
            v = EMPTY_HINT[k]
        if k == "program_start":
            v = as_date(v)
        elif isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v):
            v = float(v) if "." in v else int(v)
        ws.cell(row=i, column=1, value=k).font = F_B
        cv = ws.cell(row=i, column=2, value=v)
        cv.fill, cv.font = FILL["input"], F_INPUT
        if k == "program_start":
            cv.number_format = "yyyy-mm-dd"
        if k == "bounce_alert_rate":
            cv.number_format = "0%"
        ws.cell(row=i, column=3, value=CONFIG_DOC[k]).font = F_SUB
        pos[k] = i
    cfg_last = 4 + len(keys)
    ws.conditional_formatting.add(f"B5:B{cfg_last}", FormulaRule(formula=['LEFT(B5,5)="EMPTY"'], fill=FILL["empty"]))
    # reply classes
    ws["E3"], ws["E3"].font = "Reply classes", F_H2
    for j, h in enumerate(["class", "score", "counts as", "meaning"], 5):
        cell = ws.cell(row=4, column=j, value=h)
        cell.font, cell.fill, cell.alignment = F_HEAD, FILL["sync"], CENTER
    for i, (cl, s, cnt, mean) in enumerate(CLASSES, 5):
        for j, v in enumerate([cl, s, cnt, mean], 5):
            ws.cell(row=i, column=j, value=v).font = F
    cls_r1 = 4 + len(CLASSES)
    # mailboxes
    ws["J3"], ws["J3"].font = "Mailboxes (each person: 8-o > Connect my mailbox, once)", F_H2
    mcols = ["mailbox", "person", "sender_name", "status", "last_sync", "rows_synced", "connected_by"]
    for j, h in enumerate(mcols, 10):
        cell = ws.cell(row=4, column=j, value=h)
        cell.font, cell.fill, cell.alignment = F_HEAD, FILL["you" if j < 13 else "sync"], CENTER
    for i in range(5, 10):
        mb = mailboxes[i - 5] if i - 5 < len(mailboxes) else {}
        st = mb.get("status") or ("EMPTY: not connected" if mb.get("mailbox") else None)
        if mb.get("mailbox") == "abhinay@8-o.co" and not mb.get("status"):
            st = "Backfilled 2026-11-15 (not connected)"
        vals = [mb.get("mailbox"), mb.get("person"), mb.get("sender_name"), st, as_dt(mb.get("last_sync")),
                mb.get("rows_synced"), mb.get("connected_by")]
        for j, v in enumerate(vals, 10):
            cell = ws.cell(row=i, column=j, value=v)
            cell.font = F
            if j < 13:
                cell.fill, cell.font = FILL["input"], F_INPUT
        ws.cell(row=i, column=14).number_format = "yyyy-mm-dd hh:mm"
    ws.conditional_formatting.add("M5:M9", FormulaRule(formula=['LEFT(M5,5)="EMPTY"'], fill=FILL["empty"]))
    # dictionary
    r0 = max(cfg_last, cls_r1, 9) + 3
    ws.cell(row=r0, column=1, value="Data dictionary").font = F_H2
    for j, h in enumerate(["tab", "column", "written by", "meaning"], 1):
        cell = ws.cell(row=r0 + 1, column=j, value=h)
        cell.font, cell.fill = F_HEAD, FILL["auto"]
    r = r0 + 2
    for tab, cols in (("Log", LOG), ("Accounts", ACC), ("Contacts", CON), ("Angles", ANG), ("Messages", MSG),
                      ("Experiments", EXP), ("Learnings", LRN), ("Queue", QUE)):
        for name, _, kind, _ in cols:
            mean = DICT.get(tab, {}).get(name, "")
            for j, v in enumerate([tab, name, KIND_TXT[kind], mean], 1):
                ws.cell(row=r, column=j, value=v).font = F
            r += 1
    ws.freeze_panes = "A5"
    return {"pos": pos, "cls_col": "E", "score_col": "F", "cls_r0": 5, "cls_r1": cls_r1, "mb_r0": 5, "mb_r1": 9}


# ------------------------------------------------------------------------------------------- Home
def _box(ws, rng, fill=None):
    for row in ws[rng]:
        for cell in row:
            cell.border = BOX
            if fill:
                cell.fill = fill


def build_home(ws, S, c, a_, m_, e, lr, g, mailboxes):
    for col, w in zip("ABCDEFGHIJ", [24, 15, 15, 15, 15, 15, 15, 15, 3, 3]):
        ws.column_dimensions[col].width = w
    ws["A1"], ws["A1"].font = "8-o Outbound OS", F_TITLE
    ws["A2"] = ("Research > Strategy > Writing > QC > Experiment > Learn. Every number below is a formula over Log; "
                "the Apps Script keeps Log, Accounts status and Queue current.")
    ws["A2"].font, ws["A2"].alignment = F_SUB, WRAP
    ws.merge_cells("A2:H2")
    ws.row_dimensions[2].height = 26
    ws["F1"] = (f'="Last sync: "&IF(MAX(Settings!$N${S["mb_r0"]}:$N${S["mb_r1"]})=0,"never",'
                f'TEXT(MAX(Settings!$N${S["mb_r0"]}:$N${S["mb_r1"]}),"yyyy-mm-dd hh:mm"))')
    ws["F1"].font = F_SUB
    lg = lambda col: f"Log!${c[col]}:${c[col]}"  # noqa: E731
    first = f'{lg("step")},"1st"'
    # KPI tiles
    ws["A4"], ws["A4"].font = "Scoreboard (first touches; delivered = denominator)", F_H2
    tiles = [
        ("First touches", f'=COUNTIFS({lg("direction")},"out",{first})', "0"),
        ("Delivered", f'=SUMIFS({lg("delivered")},{first})', "0"),
        ("Reply rate", f'=IF(B6=0,"",SUMIFS({lg("replied")},{first})/B6)', "0.0%"),
        ("Positive rate (KPI)", f'=IF(B6=0,"",SUMIFS({lg("positive")},{first})/B6)', "0.0%"),
        ("Meetings", f'=SUMIFS({lg("meeting")},{first})', "0"),
        ("Design partners", f'=COUNTIF(Accounts!${a_["deal_stage"]}:${a_["deal_stage"]},"Design partner")+COUNTIF(Accounts!${a_["deal_stage"]}:${a_["deal_stage"]},"Paid")', "0"),
        ("Bounce rate", '=IF(A6=0,"",(A6-B6)/A6)', "0.0%"),
        ("Median reply time (h)", f'=IFERROR(MEDIAN({lg("our_response_h")}),"")', "0.0"),
    ]
    for j, (label, f, fmt) in enumerate(tiles, 1):
        lc = ws.cell(row=5, column=j, value=label)
        lc.font, lc.alignment = F_KPI_L, CENTER
        vc = ws.cell(row=6, column=j, value=f)
        vc.font, vc.alignment, vc.number_format = F_KPI, CENTER, fmt
    _box(ws, "A5:H6", FILL["tile"])
    for cell in ("D5", "D6"):
        ws[cell].fill = FILL["primary"]
    ws.row_dimensions[6].height = 36
    ws.conditional_formatting.add("G6", FormulaRule(formula=[f'AND(G6<>"",G6>Settings!$B${S["pos"]["bounce_alert_rate"]})'],
                                                    fill=FILL["red"]))
    ws["A7"] = ("Positive rate = positive or meeting replies / delivered first touches. Reply time = hours from their "
                "reply to our next message. Rates are observations until an experiment arm reaches min_n.")
    ws["A7"].font = F_SUB
    ws.merge_cells("A7:H7")
    ws["A7"].alignment = WRAP
    ws.row_dimensions[7].height = 26

    # Today + learning loop
    ws["A9"], ws["A9"].font = "Today (Queue)", F_H2
    ws["E9"], ws["E9"].font = "Learning loop", F_H2
    today_rows = [("Reply now / confirm", '=COUNTIF(Queue!$B:$B,"Reply now")+COUNTIF(Queue!$B:$B,"Confirm meeting")'),
                  ("Nudge conversation", '=COUNTIF(Queue!$B:$B,"Nudge conversation")'),
                  ("Follow-ups due", '=COUNTIF(Queue!$B:$B,"Follow up*")'),
                  ("Fix bounce", '=COUNTIF(Queue!$B:$B,"Fix bounce")'),
                  ("First touches ready", '=COUNTIF(Queue!$B:$B,"First touch")'),
                  ("Untagged sends", '=COUNTIF(Queue!$B:$B,"Tag msg_id")')]
    ex = lambda col: f"Experiments!${e[col]}:${e[col]}"  # noqa: E731
    lv = lambda col: f"Learnings!${lr[col]}:${lr[col]}"  # noqa: E731
    loop_rows = [("Experiments running", f'=COUNTIF({ex("status")},"Running")'),
                 ("Ready for readout", f'=COUNTIFS({ex("status")},"Running",{ex("verdict")},"<>Collecting*",{ex("verdict")},"<>")'),
                 ("Observations / hypotheses", f'=COUNTIF({lv("level")},"Observation")&" / "&COUNTIF({lv("level")},"Hypothesis")'),
                 ("Repeated / principles", f'=COUNTIF({lv("level")},"Repeated evidence")&" / "&COUNTIF({lv("level")},"Principle")'),
                 ("Learning reviews due", f'=COUNTIF({lv("review_due")},"DUE")'),
                 ("Messages needing QC", f'=COUNTIF(Messages!${m_["qc_status"]}:${m_["qc_status"]},"Needs review")')]
    for i, ((l1, f1), (l2, f2)) in enumerate(zip(today_rows, loop_rows), 10):
        ws.cell(row=i, column=1, value=l1).font = F
        ws.cell(row=i, column=2, value=f1).font = F_B
        ws.cell(row=i, column=5, value=l2).font = F
        ws.merge_cells(start_row=i, start_column=5, end_row=i, end_column=6)
        c7 = ws.cell(row=i, column=7, value=f2)
        c7.font, c7.alignment = F_B, Alignment(horizontal="right")
    _box(ws, "A10:B15")
    _box(ws, "E10:G15")
    ws.conditional_formatting.add("B10", CellIsRule(operator="greaterThan", formula=["0"], fill=FILL["red"]))
    ws.conditional_formatting.add("G14", CellIsRule(operator="greaterThan", formula=["0"], fill=FILL["amber"]))

    # by mailbox
    ws["A17"], ws["A17"].font = "By mailbox (first touches)", F_H2
    hdr = ["mailbox", "first touches", "delivered", "replied", "positive", "meetings", "reply rate", "positive rate"]
    for j, h in enumerate(hdr, 1):
        cell = ws.cell(row=18, column=j, value=h)
        cell.font, cell.fill, cell.alignment = F_HEAD, FILL["f"], CENTER
    for k in range(5):
        r = 19 + k
        mbcell = f"Settings!$J${S['mb_r0'] + k}"
        ws.cell(row=r, column=1, value=f'=IF({mbcell}="","",{mbcell})')
        crit = f'{lg("mailbox")},$A{r},{first}'
        ws.cell(row=r, column=2, value=f'=IF($A{r}="","",COUNTIFS({lg("direction")},"out",{crit}))')
        for j, col in ((3, "delivered"), (4, "replied"), (5, "positive"), (6, "meeting")):
            ws.cell(row=r, column=j, value=f'=IF($A{r}="","",SUMIFS({lg(col)},{crit}))')
        ws.cell(row=r, column=7, value=f'=IF(N(C{r})=0,"",D{r}/C{r})').number_format = "0.0%"
        ws.cell(row=r, column=8, value=f'=IF(N(C{r})=0,"",E{r}/C{r})').number_format = "0.0%"
    ws.cell(row=24, column=1, value="Total").font = F_B
    for j in range(2, 7):
        L_ = get_column_letter(j)
        ws.cell(row=24, column=j, value=f"=SUM({L_}19:{L_}23)").font = F_B
    ws["G24"], ws["H24"] = '=IF(N(C24)=0,"",D24/C24)', '=IF(N(C24)=0,"",E24/C24)'
    ws["G24"].number_format = ws["H24"].number_format = "0.0%"
    _box(ws, "A18:H24")

    # weekly trend
    ws["A26"], ws["A26"].font = "Weekly trend (16 weeks from Settings program_start)", F_H2
    hdr = ["week", "starts", "first touches", "delivered", "replied", "positive", "reply rate", "positive rate"]
    for j, h in enumerate(hdr, 1):
        cell = ws.cell(row=27, column=j, value=h)
        cell.font, cell.fill, cell.alignment = F_HEAD, FILL["f"], CENTER
    ps = f"Settings!$B${S['pos']['program_start']}"
    for k in range(16):
        r = 28 + k
        ws.cell(row=r, column=1, value=f"W{k + 1}")
        ws.cell(row=r, column=2, value=f"={ps}+{7 * k}").number_format = "yyyy-mm-dd"
        win = f'{first},{lg("date")},">="&$B{r},{lg("date")},"<"&($B{r}+7)'
        ws.cell(row=r, column=3, value=f'=COUNTIFS({lg("direction")},"out",{win})')
        for j, col in ((4, "delivered"), (5, "replied"), (6, "positive")):
            ws.cell(row=r, column=j, value=f"=SUMIFS({lg(col)},{win})")
        ws.cell(row=r, column=7, value=f'=IF(D{r}=0,"",E{r}/D{r})').number_format = "0.0%"
        ws.cell(row=r, column=8, value=f'=IF(D{r}=0,"",F{r}/D{r})').number_format = "0.0%"
        if k % 2:
            for j in range(1, 9):
                ws.cell(row=r, column=j).fill = FILL["band"]
    _box(ws, "A27:H43")
    ws.conditional_formatting.add("A28:H43", FormulaRule(formula=["AND($B28<=TODAY(),$B28+7>TODAY())"], fill=FILL["primary"]))

    bar = BarChart()
    bar.type, bar.title, bar.height, bar.width = "col", "Delivered first touches per week", 7, 15
    bar.add_data(Reference(ws, min_col=4, min_row=27, max_row=43), titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=1, min_row=28, max_row=43))
    line = LineChart()
    line.title, line.height, line.width = "Reply and positive rate per week", 7, 15
    line.add_data(Reference(ws, min_col=7, max_col=8, min_row=27, max_row=43), titles_from_data=True)
    line.set_categories(Reference(ws, min_col=1, min_row=28, max_row=43))
    line.y_axis.number_format = "0%"
    for ch in (bar, line):
        ch.roundedCorners = False
        ch.legend.overlay = False
        ch.x_axis.delete = False
        ch.y_axis.delete = False
    bar.legend = None
    ws.add_chart(bar, "K4")
    ws.add_chart(line, "K20")

    # rhythm
    ws["A45"], ws["A45"].font = "Operating rhythm", F_H2
    rhythm = [
        ("Daily (15-30 min)", "Work the Queue top-down: Reply now (< 1 h) > Nudge > Follow up > First touch. "
                              "Confirm class_human on every reply in Log. Every send uses a Live, Approved message."),
        ("Weekly (Friday)", "Experiments: read the verdicts, decide, write the Learning (level + evidence + action). "
                            "Pick next week's control and one challenger. Retire copy only at readout."),
        ("Monthly", "Promote Learnings that repeat to Principle; demote ones with counterexamples. Re-test deliverability. "
                    "Refresh research (pipeline CSV import). Review Settings (cadence, caps)."),
        ("Roles", "Strategist: angles + experiment priorities. Writer: messages + QC. Operator: sending, follow-ups, "
                  "replies. Learning owner: Experiments + Learnings. AI: sync, tagging, Queue, digests, drafts."),
    ]
    for i, (k, v) in enumerate(rhythm, 46):
        ws.cell(row=i, column=1, value=k).font = F_B
        cell = ws.cell(row=i, column=2, value=v)
        cell.font, cell.alignment = F, WRAP
        ws.merge_cells(start_row=i, start_column=2, end_row=i, end_column=8)
        ws.row_dimensions[i].height = 30
    _box(ws, "A46:H49")

    # gaps
    ws["A51"], ws["A51"].font = "Gaps (pink = EMPTY, needs input)", F_H2
    pos = S["pos"]
    gaps = [
        ("Mailboxes connected", f'=IF(COUNTIF(Settings!$M${S["mb_r0"]}:$M${S["mb_r1"]},"Connected*")>=COUNTA(Settings!$J${S["mb_r0"]}:$J${S["mb_r1"]}),"OK","EMPTY: "&COUNTA(Settings!$J${S["mb_r0"]}:$J${S["mb_r1"]})-COUNTIF(Settings!$M${S["mb_r0"]}:$M${S["mb_r1"]},"Connected*")&" mailbox(es) not connected: open the sheet as that person > 8-o > Connect my mailbox")'),
        ("Booking link", f'=IF(OR(Settings!$B${pos["booking_link"]}="",LEFT(Settings!$B${pos["booking_link"]},5)="EMPTY"),"EMPTY: add a booking link in Settings (positive replies stalled without one, L-004)","OK")'),
        ("Deliverability", f'=IF(COUNTIFS({lv("statement")},"EMPTY*")>0,"EMPTY: record SPF / DKIM / DMARC + inbox-placement results (Learnings L-008)","OK")'),
        ("Design-partner segment", f'=IF(COUNTIFS(Learnings!$A:$A,"L-006",{lv("status")},"Open")>0,"EMPTY: decide the segment you test on (headcount, stage, sector), then close L-006","OK")'),
        ("Sample size", '=IF(B6<60,"EMPTY: "&B6&" delivered first touches so far; ~60 (2 arms x 30) needed for the first readout","OK")'),
        ("Untagged sends", '=IF(B15>0,"EMPTY: "&B15&" sends without msg_id (Queue)","OK")'),
        ("Headcount data", f'=IF(COUNTA(Accounts!${a_["headcount"]}:${a_["headcount"]})<0.5*COUNTA(Accounts!$B:$B),"EMPTY: "&TEXT(1-COUNTA(Accounts!${a_["headcount"]}:${a_["headcount"]})/COUNTA(Accounts!$B:$B),"0%")&" of accounts have no headcount (Clay / LinkedIn import moves C to A/B)","OK")'),
        ("Warm paths (H9)", '="EMPTY: LinkedIn Connections.csv not imported (inputs/linkedin_connections.csv)"'),
    ]
    for i, (k, f) in enumerate(gaps, 52):
        ws.cell(row=i, column=1, value=k).font = F_B
        cell = ws.cell(row=i, column=2, value=f)
        cell.font, cell.alignment = F, WRAP
        ws.merge_cells(start_row=i, start_column=2, end_row=i, end_column=8)
    ws.conditional_formatting.add("B52:B59", FormulaRule(formula=['LEFT(B52,5)="EMPTY"'], fill=FILL["empty"]))
    ws.conditional_formatting.add("B52:B59", FormulaRule(formula=['B52="OK"'], fill=FILL["green"]))
    _box(ws, "A52:H59")

    # map
    ws["A61"], ws["A61"].font = "How the tabs link", F_H2
    tmap = [("Tab", "Module", "Written by", "Purpose"),
            ("Queue", "Operate", "Apps Script", "What to do now; rebuilt every sync"),
            ("Log", "05 Experiment", "Apps Script + you", "Every email both ways; class_human is yours"),
            ("Accounts", "01 Research", "Pipeline + sync + you", "Facts (why_now) vs beliefs (assumption); deal stage"),
            ("Contacts", "01 Research", "Pipeline + sync + you", "People, emails, do-not-contact"),
            ("Angles", "02 Strategy", "You", "Why should this buyer respond? Stats roll up from Messages"),
            ("Messages", "03 Writing + 04 QC", "You", "Copy per angle; QC gate; stats from Log by msg_id"),
            ("Experiments", "05 Experiment", "You", "A vs B on msg_id + date window; verdict formula"),
            ("Learnings", "06 Learning", "You", "Observation > Hypothesis > Repeated > Principle"),
            ("Settings", "System", "You + Apps Script", "Config, reply classes, mailboxes, dictionary")]
    for i, row in enumerate(tmap, 62):
        for j, v in enumerate(row, 1):
            cell = ws.cell(row=i, column=j, value=v)
            cell.font = F_HEAD if i == 62 else F
            if i == 62:
                cell.fill = FILL["auto"]
        ws.merge_cells(start_row=i, start_column=4, end_row=i, end_column=8)
    _box(ws, "A62:H71")
    ws.freeze_panes = "A4"


# ------------------------------------------------------------------------------------------- CSVs
def write_csvs(out_dir: Path, accounts, contacts, log):
    def dump(name, rows, cols):
        with open(out_dir / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in r.items()
                            if k in cols})
    dump("accounts.csv", accounts, [c[0] for c in ACC if c[2] == "auto"])
    dump("contacts.csv", contacts, [c[0] for c in CON if c[2] == "auto"])
    dump("log.csv", log, [c[0] for c in LOG if c[2] != "f"])
