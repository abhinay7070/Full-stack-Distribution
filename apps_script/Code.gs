/**
 * 8-o Outbound OS — Apps Script bound to the Google Sheet.
 *
 * What it does (no human needed):
 *   - Syncs every connected team mailbox into Log on a timer: every email to or from a prospect, both directions.
 *   - Tags each send with the Messages row it used (match_phrase), labels the step (1st / FU1 / Reply),
 *     classifies replies (bounce, ooo, unsubscribe, negative, not_now, meeting, referral, positive, objection, question).
 *   - Matches replies by thread, not by domain (prospects reply from other domains), and de-duplicates the same
 *     message seen in two mailboxes (cc) by Message-ID / sender + time.
 *   - Rolls status up into Accounts and Contacts, adds new prospects found in Gmail, rebuilds Queue.
 *   - Daily digest email per person; optional Gmail drafts from approved copy (never sends anything itself).
 *
 * Who runs it: each person opens the sheet once and clicks 8-o > Connect my mailbox. Time triggers run as the person
 * who created them, so each mailbox syncs under its owner's own Google authorisation.
 * Mirrors scripts/workbook.py (thread_stats, status_of, build_queue). Keep the two in step.
 */

const VERSION = '1.0 (2026-11-15)';
const CLASSES = {meeting: 7, positive: 6, referral: 5, question: 4, other: 3, objection: 2, not_now: 1, negative: 0,
                 unsubscribe: -1, ooo: -5, bounce: -9};
const FORMULA_COLS = {
  Log: ['company', 'class', 'score', 'best_reply_after', 'outcome', 'delivered', 'replied', 'positive', 'meeting',
        'our_response_h'],
  Accounts: ['days_since_touch'],
  Contacts: [],
};
const CLOSED_DEALS = ['Lost', 'Not a fit', 'Do not contact', 'Paid'];
const GOOD_EMAIL = ['published_ceo', 'published_founder', 'published_founders_inbox', 'manual', 'clay_valid',
                    'derived_from_pattern'];
const FREE_MAIL = ['gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com', 'yahoo.com', 'icloud.com',
                   'me.com', 'aol.com', 'proton.me', 'protonmail.com', 'gmx.com', 'yandex.com'];
const SYSTEM_SENDER = /^(no-?reply|noreply|notifications?|calendar-notification|mailer-daemon|postmaster|bounce)[^@]*@/;
const TIME_BUDGET_MS = 240000;

// ------------------------------------------------------------------------------------------------ menu
function onOpen() {
  SpreadsheetApp.getUi().createMenu('8-o')
    .addItem('Connect my mailbox (once per person)', 'connectMyMailbox')
    .addItem('Sync now', 'syncNow')
    .addItem('Rebuild Queue', 'rebuildQueue')
    .addSeparator()
    .addItem('Create Gmail drafts for my Queue', 'createMyDrafts')
    .addItem('Send me my digest now', 'sendMyDigest')
    .addSeparator()
    .addItem('Import research CSVs from Drive', 'importResearch')
    .addItem('Health check', 'healthCheck')
    .addItem('Disconnect my mailbox', 'disconnectMyMailbox')
    .addToUi();
}

function connectMyMailbox() {
  const me = me_();
  const S = settings_();
  ss_().setSpreadsheetTimeZone(String(cfg_(S, 'timezone', 'Asia/Kolkata')));
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });  // only this user's
  const every = Number(cfg_(S, 'sync_every_minutes', 15));
  const b = ScriptApp.newTrigger('syncTick').timeBased();
  if ([1, 5, 10, 15, 30].indexOf(every) >= 0) b.everyMinutes(every).create();
  else b.everyHours(Math.max(1, Math.round(every / 60))).create();
  ScriptApp.newTrigger('dailyDigest').timeBased().atHour(Number(cfg_(S, 'digest_hour', 8))).everyDays(1).create();
  PropertiesService.getUserProperties().deleteProperty('resume');
  setMailbox_(S, me, {status: 'Connected', connected_by: me});
  const n = syncMailbox_();
  toast_('Connected ' + me + '. First pass added ' + n + ' rows. Sync runs every ' + every +
         ' min (the first pass continues in the background if the mailbox is large).');
}

function disconnectMyMailbox() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  setMailbox_(settings_(), me_(), {status: 'EMPTY: disconnected'});
  toast_('Triggers removed for ' + me_() + '.');
}

function syncTick() { syncMailbox_(); }
function syncNow() { toast_('Synced ' + syncMailbox_() + ' new rows from ' + me_() + '.'); }
function rebuildQueue() {
  const S = settings_();
  const n = buildQueue_(S, readTable_('Log'), readTable_('Accounts'));
  toast_('Queue rebuilt: ' + n + ' actions.');
}

// ------------------------------------------------------------------------------------------------ sync
function syncMailbox_() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(90000)) return 0;
  try {
    const t0 = Date.now();
    const me = me_();
    const S = settings_();
    const mb = S.mailboxes.filter(function (m) { return lc_(m.mailbox) === me; })[0] || {};
    const props = PropertiesService.getUserProperties();
    let resume = JSON.parse(props.getProperty('resume') || 'null');
    let q, start;
    if (resume) { q = resume.q; start = resume.start; }
    else {
      const last = mb.last_sync instanceof Date ? mb.last_sync : null;
      const since = last ? new Date(last.getTime() - 2 * 86400000)
                         : new Date(Date.now() - Number(cfg_(S, 'first_sync_lookback_days', 90)) * 86400000);
      q = 'after:' + Utilities.formatDate(since, 'UTC', 'yyyy/MM/dd') + ' -in:chats -in:spam -in:trash';
      start = 0;
    }
    const ctx = context_(S);
    const L = logIndex_();
    let added = 0, done = false;
    while (Date.now() - t0 < TIME_BUDGET_MS) {
      const threads = GmailApp.search(q, start, 100);
      threads.forEach(function (th) { added += processThread_(th, ctx, L); });
      start += threads.length;
      if (threads.length < 100) { done = true; break; }
    }
    flushLog_(L);
    if (done) props.deleteProperty('resume'); else props.setProperty('resume', JSON.stringify({q: q, start: start}));
    const log = readTable_('Log');
    const accounts = refreshRollups_(log);
    buildQueue_(S, log, readTable_('Accounts'));
    setMailbox_(settings_(), me, {status: done ? 'Connected' : 'Connected (first pass running)',
      last_sync: done ? new Date() : (mb.last_sync || ''), rows_synced: (Number(mb.rows_synced) || 0) + added,
      connected_by: me});
    return added;
  } finally {
    lock.releaseLock();
  }
}

function context_(S) {
  const teamDomain = lc_(cfg_(S, 'team_domain', '8-o.co'));
  const team = {};
  S.mailboxes.forEach(function (m) { if (m.mailbox) team[lc_(m.mailbox)] = 1; });
  const exA = {};
  list_(S, 'exclude_addresses').forEach(function (a) { exA[a] = 1; });
  const exD = list_(S, 'exclude_domains');
  const ctx = {
    team: team, label: String(cfg_(S, 'gmail_label', '')),
    msgs: readTable_('Messages').filter(function (m) { return m.msg_id; }),
    isTeam: function (e) { return !!team[e] || (teamDomain && e.slice(-(teamDomain.length + 1)) === '@' + teamDomain); },
    isExcluded: function (e) {
      if (exA[e] || SYSTEM_SENDER.test(e)) return true;
      const d = e.split('@')[1] || '';
      return exD.some(function (x) { return d === x || d.slice(-(x.length + 1)) === '.' + x; });
    },
  };
  return ctx;
}

function processThread_(th, ctx, L) {
  const msgs = th.getMessages();
  let firstOut = null, contact = null;
  for (let i = 0; i < msgs.length && !firstOut; i++) {
    const m = msgs[i];
    if (m.isDraft()) continue;
    if (ctx.isTeam(addr_(m.getFrom()))) {
      const ext = externals_(m, ctx);
      if (ext.length) { firstOut = m; contact = ext[0]; }
    }
  }
  if (!firstOut) return 0;  // internal-only or inbound-only thread: not outreach
  const domain = rootDomain_(contact.email);
  const key = (domain || contact.email) + '|' + normSubject_(firstOut.getSubject());
  const owner = addr_(firstOut.getFrom());
  let added = 0, seqOut = 0, realIn = false;
  msgs.forEach(function (m) {
    if (m.isDraft()) return;
    const from = addr_(m.getFrom());
    const body = newText_(m.getPlainBody() || '');
    const r = {touch_id: m.getId(), date: m.getDate(), channel: 'Email', company_domain: domain, subject: m.getSubject(),
               snippet: body.replace(/\s+/g, ' ').trim().slice(0, 240), thread_key: key, link: th.getPermalink(),
               thread_id: th.getId(), rfc_id: header_(m, 'Message-ID'), source: 'gmail_sync', class_auto: '', msg_id: ''};
    if (ctx.isTeam(from)) {
      const ext = externals_(m, ctx);
      if (!ext.length) return;
      r.direction = 'out'; r.mailbox = from; r.contact_email = ext[0].email; r.contact_name = ext[0].name;
      if (realIn) { r.step = 'Reply'; r.msg_id = 'CONV'; }
      else { r.step = seqOut === 0 ? '1st' : 'FU' + seqOut; seqOut++; r.msg_id = tag_(body, r.step, ctx.msgs); }
    } else {
      const cls = classify_(m, from, body);
      if (cls !== 'bounce' && ctx.isExcluded(from)) return;
      r.direction = 'in'; r.mailbox = owner; r.step = 'Reply'; r.class_auto = cls;
      r.contact_email = cls === 'bounce' ? contact.email : from;
      r.contact_name = cls === 'bounce' ? contact.name : name_(m.getFrom());
      if (CLASSES[cls] >= 0) realIn = true;
    }
    added += upsertLog_(L, r);
  });
  if (added && ctx.label) {
    const lab = GmailApp.getUserLabelByName(ctx.label) || GmailApp.createLabel(ctx.label);
    th.addLabel(lab);
  }
  return added;
}

function logIndex_() {
  const sheet = sh_('Log');
  const H = headers_(sheet);
  const last = lastDataRow_(sheet, [H.idx.touch_id + 1, H.idx.date + 1]);
  const vals = last > 1 ? sheet.getRange(2, 1, last - 1, H.list.length).getValues() : [];
  const L = {sheet: sheet, H: H, last: last, ids: {}, rfc: {}, byContact: {}, appends: [], updates: [], fills: []};
  vals.forEach(function (v, i) {
    const row = i + 2;
    let id = String(v[H.idx.touch_id] || '');
    const d = v[H.idx.date];
    if (!id && d instanceof Date) {  // a touch typed by hand
      id = 'manual-' + Utilities.getUuid().slice(0, 8);
      L.fills.push({row: row, col: 'touch_id', value: id});
      if (!v[H.idx.source]) L.fills.push({row: row, col: 'source', value: 'manual'});
      if (!v[H.idx.thread_key]) {
        L.fills.push({row: row, col: 'thread_key', value: (v[H.idx.company_domain] || lc_(v[H.idx.contact_email])) +
                      '|' + lc_(v[H.idx.channel] || 'manual')});
      }
    }
    if (id) L.ids[id] = row;
    const rf = String(v[H.idx.rfc_id] || '');
    if (rf) L.rfc[rf] = row;
    if (d instanceof Date) {
      const k = lc_(v[H.idx.contact_email]) + '|' + v[H.idx.direction];
      (L.byContact[k] = L.byContact[k] || []).push({t: d.getTime(), source: String(v[H.idx.source] || ''), row: row,
                                                     hasRfc: !!rf});
    }
  });
  return L;
}

function upsertLog_(L, r) {
  if (L.ids[r.touch_id]) {
    const row = L.ids[r.touch_id];
    if (r.rfc_id && !L.rfc[r.rfc_id]) { L.fills.push({row: row, col: 'rfc_id', value: r.rfc_id}); L.rfc[r.rfc_id] = row; }
    return 0;
  }
  if (r.rfc_id && L.rfc[r.rfc_id]) return 0;
  const k = lc_(r.contact_email) + '|' + r.direction;
  const near = L.byContact[k] || [];
  const t = r.date.getTime();
  // the same message already logged from another mailbox (cc'd teammate) or from a backfill without Message-ID
  if (near.some(function (x) { return x.source !== 'backfill_quoted' && Math.abs(x.t - t) <= 120000; })) return 0;
  // a reconstructed (quoted) row: replace it with the real message, keep the human columns
  let best = null;
  near.forEach(function (x) {
    if (x.source === 'backfill_quoted' && !x.used) {
      const dd = Math.abs(x.t - t);
      if (dd <= 36 * 3600000 && (!best || dd < best.dd)) best = {x: x, dd: dd};
    }
  });
  L.ids[r.touch_id] = -1;
  if (r.rfc_id) L.rfc[r.rfc_id] = -1;
  if (best) { best.x.used = true; L.updates.push({row: best.x.row, r: r}); return 1; }
  L.appends.push(r);
  (L.byContact[k] = L.byContact[k] || []).push({t: t, source: 'gmail_sync', row: -1});
  return 1;
}

function flushLog_(L) {
  const s = L.sheet, H = L.H;
  L.fills.forEach(function (f) { s.getRange(f.row, H.idx[f.col] + 1).setValue(f.value); });
  ['touch_id', 'date', 'mailbox', 'subject', 'snippet', 'link', 'thread_id', 'rfc_id', 'source'].forEach(function (c) {
    L.updates.forEach(function (u) { s.getRange(u.row, H.idx[c] + 1).setValue(u.r[c]); });
  });
  if (L.appends.length) appendObjects_(s, H, L.appends, FORMULA_COLS.Log, L.last);
}

/** Write objects below `last`, column segment by segment so formula columns are never overwritten. */
function appendObjects_(sheet, H, objs, formulaCols, last) {
  const n = objs.length, start = last + 1;
  if (start + n - 1 > sheet.getMaxRows()) sheet.insertRowsAfter(sheet.getMaxRows(), start + n - 1 - sheet.getMaxRows() + 50);
  const isF = {};
  formulaCols.forEach(function (c) { isF[c] = 1; });
  let a = null;
  for (let i = 0; i <= H.list.length; i++) {
    const name = H.list[i];
    const writable = i < H.list.length && name && !isF[name];
    if (writable && a === null) a = i;
    if (!writable && a !== null) {
      const block = objs.map(function (o) {
        const row = [];
        for (let j = a; j < i; j++) { const v = o[H.list[j]]; row.push(v === undefined || v === null ? '' : v); }
        return row;
      });
      sheet.getRange(start, a + 1, n, i - a).setValues(block);
      a = null;
    }
  }
  formulaCols.forEach(function (c) {
    const col = H.idx[c];
    if (col === undefined) return;
    const target = sheet.getRange(start, col + 1, n, 1);
    if (!target.getFormulas().every(function (f) { return f[0]; })) sheet.getRange(2, col + 1).copyTo(target);
  });
  if (H.idx.date !== undefined) sheet.getRange(start, H.idx.date + 1, n, 1).setNumberFormat('yyyy-mm-dd hh:mm');
}

// ------------------------------------------------------------------------------------------------ rollups
function threads_(log) {
  const T = {};
  log.forEach(function (r) {
    if (!r.thread_key || !(r.date instanceof Date)) return;
    (T[r.thread_key] = T[r.thread_key] || []).push(r);
  });
  const out = {};
  Object.keys(T).forEach(function (k) {
    const rs = T[k].sort(function (a, b) { return a.date - b.date; });
    const ins = rs.filter(function (r) { return r.direction === 'in'; });
    const outs = rs.filter(function (r) { return r.direction === 'out'; });
    const scores = ins.map(function (r) { return CLASSES[r.class_human || r.class_auto]; })
                      .filter(function (s) { return s !== undefined; });
    out[k] = {rows: rs, ins: ins, outs: outs, last: rs[rs.length - 1], firstOut: outs[0] || null,
              lastOut: outs[outs.length - 1] || null, best: scores.length ? Math.max.apply(null, scores) : null,
              bounced: scores.indexOf(-9) >= 0, unsub: scores.indexOf(-1) >= 0,
              realReply: scores.some(function (s) { return s >= 0; })};
  });
  return out;
}

function outcomeOf_(best) {
  if (best === null || best === undefined) return 'no reply';
  for (const c in CLASSES) if (CLASSES[c] === best) return c;
  return '';
}

function statusOf_(ts) {
  if (!ts.length) return 'Not contacted';
  const bests = ts.map(function (t) { return t.best; }).filter(function (b) { return b !== null; });
  const best = bests.length ? Math.max.apply(null, bests) : null;
  if (ts.some(function (t) { return t.unsub; })) return 'Do not contact';
  if (best !== null && best >= 7) return 'Meeting';
  if (best !== null && best >= 6) return 'Positive';
  if (best !== null && best >= 0) return 'Replied';
  if (ts.some(function (t) { return t.bounced; }) && !ts.some(function (t) { return t.realReply; })) return 'Bounced';
  return 'Contacted';
}

function refreshRollups_(log) {
  const T = threads_(log);
  const byDom = {}, byEmail = {};
  Object.keys(T).forEach(function (k) {
    const t = T[k], d = (t.firstOut || t.last).company_domain;
    if (d) (byDom[d] = byDom[d] || []).push(t);
  });
  log.forEach(function (r) { const e = lc_(r.contact_email); if (e) (byEmail[e] = byEmail[e] || []).push(r); });

  // Accounts
  const as = sh_('Accounts'), AH = headers_(as);
  const alast = lastDataRow_(as, [AH.idx.company_domain + 1]);
  const SYNC_A = ['touches', 'last_touch', 'outcome', 'status'];
  const aStart = AH.idx.touches;
  const doms = alast > 1 ? as.getRange(2, AH.idx.company_domain + 1, alast - 1, 1).getValues() : [];
  const cur = alast > 1 ? as.getRange(2, aStart + 1, alast - 1, SYNC_A.length).getValues() : [];
  const seen = {};
  let changed = false;
  const next = doms.map(function (d, i) {
    const dom = lc_(d[0]);
    seen[dom] = 1;
    const ts = byDom[dom] || [];
    const outs = ts.reduce(function (n, t) { return n + t.outs.length; }, 0);
    const lastT = ts.reduce(function (m, t) { return t.last.date > m ? t.last.date : m; }, null);
    const bests = ts.map(function (t) { return t.best; }).filter(function (b) { return b !== null; });
    const v = [outs || '', lastT || '', ts.length ? outcomeOf_(bests.length ? Math.max.apply(null, bests) : null) : '',
               statusOf_(ts)];
    if (!changed && v.some(function (x, j) { return String(x) !== String(cur[i][j]); })) changed = true;
    return v;
  });
  if (changed) as.getRange(2, aStart + 1, next.length, SYNC_A.length).setValues(next);
  const newAcc = Object.keys(byDom).filter(function (d) { return !seen[d]; }).map(function (d) {
    const t = byDom[d][0], fo = t.firstOut || t.last;
    const bests = byDom[d].map(function (x) { return x.best; }).filter(function (b) { return b !== null; });
    return {company_domain: d, company: d.split('.')[0].replace(/^./, function (c) { return c.toUpperCase(); }),
            fit_tier: 'Unscored (from Gmail)', ceo_name: fo.contact_name, ceo_email: fo.contact_email,
            email_confidence: 'gmail', website: 'https://' + d, suggested_angle: 'ANG-04',
            touches: byDom[d].reduce(function (n, x) { return n + x.outs.length; }, 0),
            last_touch: byDom[d].reduce(function (m, x) { return x.last.date > m ? x.last.date : m; }, null),
            outcome: outcomeOf_(bests.length ? Math.max.apply(null, bests) : null), status: statusOf_(byDom[d])};
  });
  if (newAcc.length) appendObjects_(as, AH, newAcc, FORMULA_COLS.Accounts, alast);

  // Contacts
  const cs = sh_('Contacts'), CH = headers_(cs);
  const clast = lastDataRow_(cs, [CH.idx.company_domain + 1]);
  const cStart = CH.idx.touches;
  const ems = clast > 1 ? cs.getRange(2, CH.idx.email + 1, clast - 1, 1).getValues() : [];
  const ccur = clast > 1 ? cs.getRange(2, cStart + 1, clast - 1, 3).getValues() : [];
  const cseen = {};
  let cchanged = false;
  const cnext = ems.map(function (e, i) {
    const em = lc_(e[0]);
    if (em) cseen[em] = 1;
    const rs = byEmail[em] || [];
    if (!rs.length) return ccur[i];
    const ks = {};
    rs.forEach(function (r) { ks[r.thread_key] = 1; });
    const bests = Object.keys(ks).map(function (k) { return T[k] ? T[k].best : null; })
                        .filter(function (b) { return b !== null; });
    const v = [rs.filter(function (r) { return r.direction === 'out'; }).length || '',
               rs.reduce(function (m, r) { return r.date > m ? r.date : m; }, null),
               outcomeOf_(bests.length ? Math.max.apply(null, bests) : null)];
    if (!cchanged && v.some(function (x, j) { return String(x) !== String(ccur[i][j]); })) cchanged = true;
    return v;
  });
  if (cchanged) cs.getRange(2, cStart + 1, cnext.length, 3).setValues(cnext);
  const newCon = [];
  log.forEach(function (r) {
    const em = lc_(r.contact_email);
    if (!em || cseen[em] || !r.company_domain) return;
    cseen[em] = 1;
    newCon.push({company_domain: r.company_domain, full_name: r.contact_name,
                 first_name: String(r.contact_name || '').split(' ')[0], email: em, email_status: 'gmail',
                 source: 'gmail', touches: (byEmail[em] || []).filter(function (x) { return x.direction === 'out'; }).length || ''});
  });
  if (newCon.length) appendObjects_(cs, CH, newCon, FORMULA_COLS.Contacts, clast);
  return next.length + newAcc.length;
}

// ------------------------------------------------------------------------------------------------ queue
function pickArms_(exps, msgs, firstStep) {
  const ok = {};
  msgs.forEach(function (m) {
    if ((m.status === 'Live' || m.status === 'Testing') && m.qc_status === 'Approved' &&
        ((String(m.step) === '1st') === firstStep)) ok[m.msg_id] = 1;
  });
  for (let i = 0; i < exps.length; i++) {
    const x = exps[i];
    if (x.status === 'Running' && ok[x.arm_a] && ok[x.arm_b]) return {arms: [x.arm_a, x.arm_b], exp: x.exp_id};
  }
  return {arms: Object.keys(ok).sort(), exp: ''};
}

function buildQueue_(S, log, accounts) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = 86400000;
  const fu1 = Number(cfg_(S, 'follow_up_1_after_days', 3)), fu2 = Number(cfg_(S, 'follow_up_2_after_days', 4));
  const maxFu = Number(cfg_(S, 'max_follow_ups', 2)), nNew = Number(cfg_(S, 'new_first_touches_per_day', 20));
  const msgs = readTable_('Messages').filter(function (m) { return m.msg_id; });
  const exps = readTable_('Experiments').filter(function (x) { return x.exp_id; });
  const ends = {};
  msgs.forEach(function (m) { if (String(m.ends_sequence).toUpperCase() === 'Y') ends[m.msg_id] = 1; });
  const fuA = pickArms_(exps, msgs, false), newA = pickArms_(exps, msgs, true);
  const personMb = {}, rr = [];
  S.mailboxes.forEach(function (m) { if (m.mailbox) { personMb[lc_(m.person)] = lc_(m.mailbox); rr.push(lc_(m.mailbox)); } });
  const acc = {};
  accounts.forEach(function (a) { if (a.company_domain) acc[lc_(a.company_domain)] = a; });
  const T = threads_(log);
  const q = [];
  let nFu = 0;
  const dOnly = function (d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
  Object.keys(T).forEach(function (k) {
    const t = T[k], last = t.last, lo = t.lastOut, fo = t.firstOut;
    const ref = fo || last, a = acc[lc_(ref.company_domain)] || {};
    const base = {owner: ref.mailbox || '', company: a.company || ref.company_domain, company_domain: ref.company_domain,
                  contact: ref.contact_name, email: (lo || last).contact_email, link: last.link, thread_id: last.thread_id};
    if (t.unsub) return;
    const ls = CLASSES[last.class_human || last.class_auto];
    if (last.direction === 'in' && ls !== undefined && ls >= 1) {
      q.push(Object.assign({}, base, {priority: 1, action: ls >= 7 ? 'Confirm meeting' : 'Reply now', due: dOnly(last.date), suggested_msg: 'CONV',
        why: (last.class_human || last.class_auto) + ': "' + String(last.snippet || '').slice(0, 120) + '" (' +
             Math.round((today - dOnly(last.date)) / day) + ' d ago). Booking link in Settings.'}));
      return;
    }
    if (CLOSED_DEALS.indexOf(a.deal_stage) >= 0 || !lo) return;
    const days = Math.round((today - dOnly(lo.date)) / day);
    if (t.realReply) {
      const due = new Date(dOnly(lo.date).getTime() + fu1 * day);
      if (due <= new Date(today.getTime() + day)) {
        q.push(Object.assign({}, base, {priority: 2, action: 'Nudge conversation', due: due, suggested_msg: 'CONV',
          why: 'Conversation is waiting on them for ' + days + ' d. Last: "' + String(lo.snippet || '').slice(0, 100) + '"'}));
      }
      return;
    }
    if (t.bounced) {
      q.push(Object.assign({}, base, {priority: 2, action: 'Fix bounce', due: today, suggested_msg: '',
        why: 'Bounced. Find another address (Contacts / Clay) and re-send.'}));
      return;
    }
    const seq = t.outs.filter(function (r) { return r.step !== 'Reply'; });
    if (seq.some(function (r) { return ends[r.msg_id]; })) return;
    const sent = seq.length - 1;
    if (sent >= maxFu) return;
    const due = new Date(dOnly(lo.date).getTime() + (sent === 0 ? fu1 : fu2) * day);
    if (due > new Date(today.getTime() + day)) return;
    const used = {};
    seq.forEach(function (r) { used[r.msg_id] = 1; });
    const free = fuA.arms.filter(function (m) { return !used[m]; });
    const sugg = free.length ? free[nFu % free.length] : '';
    nFu++;
    let why = 'No reply ' + days + ' d after ' + lo.step + ' (' + lo.msg_id + ').';
    why += sugg ? (fuA.exp ? ' ' + fuA.exp + ' arm.' : '') : ' No unused approved follow-up: write one in Messages.';
    q.push(Object.assign({}, base, {priority: 3, action: 'Follow up (FU' + (sent + 1) + ')', due: due,
                                    suggested_msg: sugg, why: why}));
  });
  log.forEach(function (r) {
    if (r.direction === 'out' && (!r.msg_id || r.msg_id === 'UNTAGGED')) {
      q.push({priority: 5, action: 'Tag msg_id', due: today, owner: r.mailbox, company: '', company_domain: r.company_domain,
              contact: r.contact_name, email: r.contact_email, link: r.link, thread_id: r.thread_id, suggested_msg: '',
              why: 'Sent ' + Utilities.formatDate(r.date, ss_().getSpreadsheetTimeZone(), 'yyyy-MM-dd') +
                   ': set msg_id in Log so it counts in experiments.'});
    }
  });
  const rings = ['R1 Sniper', 'R2 Rifle'];
  const cands = accounts.filter(function (a) {
    return a.status === 'Not contacted' && a.ceo_email && GOOD_EMAIL.indexOf(a.email_confidence) >= 0 && !a.deal_stage &&
           ['A', 'B'].indexOf(String(a.fit_tier).charAt(0)) >= 0 && rings.indexOf(a.ring) >= 0;
  }).sort(function (x, y) { return rings.indexOf(x.ring) - rings.indexOf(y.ring) || (Number(x.rank) || 1e9) - (Number(y.rank) || 1e9); });
  cands.slice(0, nNew).forEach(function (a, i) {
    const sugg = newA.arms.length ? newA.arms[i % newA.arms.length] : '';
    q.push({priority: 4, action: 'First touch', due: today, owner: personMb[lc_(a.owner)] || (rr.length ? rr[i % rr.length] : ''),
            company: a.company, company_domain: a.company_domain, contact: a.ceo_name, email: a.ceo_email,
            why: String(a.why_now || a.one_liner || '').slice(0, 200) + (newA.exp && sugg ? ' [' + newA.exp + ' arm]' : ''),
            suggested_msg: sugg, link: a.website, thread_id: ''});
  });
  q.sort(function (x, y) { return x.priority - y.priority || (x.due - y.due); });

  const qs = sh_('Queue'), QH = headers_(qs);
  const last = Math.max(qs.getLastRow(), 2);
  qs.getRange(2, 1, last - 1, QH.list.length).clearContent();
  if (q.length) {
    qs.getRange(2, 1, q.length, QH.list.length).setValues(q.map(function (o) {
      return QH.list.map(function (h) { const v = o[h]; return v === undefined || v === null ? '' : v; });
    }));
  }
  return q.length;
}

// ------------------------------------------------------------------------------------------------ digest + drafts
function dailyDigest() {
  const S = settings_();
  if (String(cfg_(S, 'digest_enabled', 'Y')).toUpperCase() !== 'Y') return;
  sendDigest_(S, me_());
}
function sendMyDigest() { sendDigest_(settings_(), me_()); toast_('Digest sent to ' + me_()); }

function sendDigest_(S, me) {
  const q = readTable_('Queue').filter(function (r) { return r.action && (!r.owner || lc_(r.owner) === me); });
  const home = sh_('Home').getRange('A5:H6').getDisplayValues();
  const url = ss_().getUrl();
  const esc = function (s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); };
  let html = '<div style="font-family:Arial,sans-serif;font-size:13px">';
  html += '<table cellpadding="6" style="border-collapse:collapse"><tr>' + home[0].map(function (h) {
    return '<td style="color:#6b7280;font-size:11px">' + esc(h) + '</td>'; }).join('') + '</tr><tr>' +
    home[1].map(function (v) { return '<td style="font-size:18px;font-weight:bold">' + esc(v) + '</td>'; }).join('') + '</tr></table>';
  html += '<h3>Your queue (' + q.length + ')</h3><table cellpadding="4" style="border-collapse:collapse">';
  q.slice(0, 40).forEach(function (r) {
    html += '<tr><td><b>' + esc(r.action) + '</b></td><td>' + esc(r.company || r.company_domain || '') + '</td><td>' +
            esc(r.contact || '') + '</td><td style="color:#6b7280">' + esc(String(r.why || '').slice(0, 140)) + '</td></tr>';
  });
  html += '</table><p><a href="' + url + '">Open the sheet</a> &middot; 8-o &gt; Create Gmail drafts for my Queue</p></div>';
  MailApp.sendEmail({to: me, subject: '8-o daily: ' + q.length + ' actions (' +
                     q.filter(function (r) { return r.action === 'Reply now'; }).length + ' reply now)', htmlBody: html});
}

function createMyDrafts() {
  const me = me_(), S = settings_();
  const sender = (S.mailboxes.filter(function (m) { return lc_(m.mailbox) === me; })[0] || {}).sender_name ||
                 me.split('@')[0].replace(/^./, function (c) { return c.toUpperCase(); });
  const booking = String(cfg_(S, 'booking_link', ''));
  const cap = Number(cfg_(S, 'daily_send_cap_per_mailbox', 40));
  const props = PropertiesService.getUserProperties();
  const dayKey = 'drafts_' + Utilities.formatDate(new Date(), ss_().getSpreadsheetTimeZone(), 'yyyyMMdd');
  let made = Number(props.getProperty(dayKey) || 0);
  const msgs = {};
  readTable_('Messages').forEach(function (m) { if (m.msg_id) msgs[m.msg_id] = m; });
  const pending = {};
  GmailApp.getDrafts().forEach(function (d) { pending[lc_(d.getMessage().getTo())] = 1; });
  const fill = function (txt, r) {
    const first = String(r.contact || '').split(' ')[0] || 'there';
    return String(txt || '').replace(/\{first_name\}/g, first).replace(/\{company\}/g, r.company || r.company_domain || '')
                            .replace(/\{sender\}/g, sender);
  };
  const report = {made: 0, skipped: []};
  readTable_('Queue').forEach(function (r) {
    if (lc_(r.owner) !== me || made >= cap) return;
    const to = lc_(r.email);
    if (!to || Object.keys(pending).some(function (p) { return p.indexOf(to) >= 0; })) return;
    try {
      if (r.action === 'First touch' || String(r.action).indexOf('Follow up') === 0) {
        const m = msgs[r.suggested_msg];
        if (!m || ['Live', 'Testing'].indexOf(m.status) < 0 || m.qc_status !== 'Approved') {
          report.skipped.push(r.company_domain + ': no approved message'); return;
        }
        const body = fill(m.body, r);
        if (/\[[A-Z ]{4,}/.test(body)) { report.skipped.push(r.company_domain + ': template has an unfilled [PLACEHOLDER]'); return; }
        if (r.action === 'First touch') GmailApp.createDraft(to, fill(m.subject, r), body);
        else replyDraft_(r, to, body);
      } else if ((r.action === 'Reply now' || r.action === 'Nudge conversation') && booking) {
        replyDraft_(r, to, fill('Hi {first_name},\n\nThanks! Here is a link to grab any slot that suits you: ' + booking +
                                '\n\nOr send two times that work and I will send an invite.\n\n{sender}', r));
      } else return;
      made++; report.made++; pending[to] = 1;
    } catch (e) { report.skipped.push(r.company_domain + ': ' + e.message); }
  });
  props.setProperty(dayKey, String(made));
  toast_(report.made + ' drafts created in Gmail (review and send there).' +
         (report.skipped.length ? ' Skipped: ' + report.skipped.slice(0, 5).join('; ') : ''));
}

function replyDraft_(r, to, body) {
  const th = r.thread_id ? GmailApp.getThreadById(String(r.thread_id)) : null;
  if (!th) throw new Error('thread not in this mailbox');
  const msgs = th.getMessages();
  const d = msgs[msgs.length - 1].createDraftReplyAll(body);
  if (lc_(d.getMessage().getTo()).indexOf(to) < 0) d.update(to, 'Re: ' + normSubjectKeepCase_(th.getFirstMessageSubject()), body);
}

// ------------------------------------------------------------------------------------------------ research import
function importResearch() {
  const S = settings_();
  const id = String(cfg_(S, 'research_folder_id', ''));
  if (!id) { toast_('Set research_folder_id in Settings (a Drive folder with accounts.csv / contacts.csv).'); return; }
  const folder = DriveApp.getFolderById(id);
  const out = [];
  [['Accounts', 'accounts.csv', 'company_domain'], ['Contacts', 'contacts.csv', 'email']].forEach(function (spec) {
    const files = folder.getFilesByName(spec[1]);
    if (!files.hasNext()) { out.push(spec[1] + ' not found'); return; }
    const csv = Utilities.parseCsv(files.next().getBlob().getDataAsString());
    out.push(spec[0] + ': ' + mergeCsv_(spec[0], csv, spec[2]));
  });
  toast_('Research import: ' + out.join(' | '));
}

/** Update pipeline columns by key, append new keys; never touches sync / you / formula columns. */
function mergeCsv_(tab, csv, keyCol) {
  const sheet = sh_(tab), H = headers_(sheet);
  const hdr = csv[0], ki = hdr.indexOf(keyCol);
  const last = lastDataRow_(sheet, [H.idx.company_domain + 1]);
  const width = hdr.length;  // pipeline columns are the first `width` columns, same order as the CSV
  if (hdr.join('|') !== H.list.slice(0, width).join('|')) return 'header mismatch, skipped';
  const cur = last > 1 ? sheet.getRange(2, 1, last - 1, width).getValues() : [];
  const pos = {};
  cur.forEach(function (r, i) {
    const k = keyCol === 'email' ? lc_(r[ki]) || lc_(r[0]) + '|' + lc_(r[2]) : lc_(r[ki]);
    if (k) pos[k] = i;
  });
  let upd = 0;
  const add = [];
  csv.slice(1).forEach(function (r) {
    const k = keyCol === 'email' ? lc_(r[ki]) || lc_(r[0]) + '|' + lc_(r[2]) : lc_(r[ki]);
    if (!k) return;
    if (pos[k] !== undefined) { cur[pos[k]] = r; upd++; } else add.push(r);
  });
  if (cur.length) sheet.getRange(2, 1, cur.length, width).setValues(cur);
  if (add.length) {
    const objs = add.map(function (r) { const o = {}; hdr.forEach(function (h, j) { o[h] = r[j]; }); return o; });
    appendObjects_(sheet, H, objs, FORMULA_COLS[tab] || [], last);
  }
  return upd + ' updated, ' + add.length + ' added';
}

// ------------------------------------------------------------------------------------------------ health
function healthCheck() {
  const need = {Home: [], Queue: ['priority', 'action', 'owner', 'thread_id'],
    Log: ['touch_id', 'date', 'mailbox', 'direction', 'step', 'contact_email', 'company_domain', 'thread_key', 'msg_id',
          'class_auto', 'class_human', 'rfc_id', 'source'],
    Accounts: ['company_domain', 'touches', 'last_touch', 'outcome', 'status', 'deal_stage', 'owner'],
    Contacts: ['company_domain', 'email', 'touches', 'last_touch', 'outcome'],
    Angles: ['angle_id'], Messages: ['msg_id', 'match_phrase', 'qc_status', 'status', 'step', 'body', 'subject'],
    Experiments: ['exp_id', 'arm_a', 'arm_b', 'status'], Learnings: ['learning_id'], Settings: []};
  const issues = [];
  Object.keys(need).forEach(function (t) {
    const s = ss_().getSheetByName(t);
    if (!s) { issues.push('Missing tab ' + t); return; }
    if (need[t].length) {
      const H = headers_(s);
      need[t].forEach(function (c) { if (H.idx[c] === undefined) issues.push(t + ': missing column ' + c); });
    }
  });
  const S = settings_();
  const mb = S.mailboxes.map(function (m) { return m.mailbox + ': ' + (m.status || 'not connected'); });
  const trig = ScriptApp.getProjectTriggers().map(function (t) { return t.getHandlerFunction(); });
  SpreadsheetApp.getUi().alert('8-o health check (' + VERSION + ')\n\n' +
    (issues.length ? 'Problems:\n- ' + issues.join('\n- ') : 'Structure OK.') +
    '\n\nMailboxes:\n- ' + mb.join('\n- ') + '\n\nYour triggers: ' + (trig.join(', ') || 'none (run Connect my mailbox)'));
}

// ------------------------------------------------------------------------------------------------ text rules
function classify_(m, from, body) {
  const subj = String(m.getSubject() || '').toLowerCase();
  const t = String(body || '').toLowerCase().replace(/[’]/g, "'");
  if (/^(mailer-daemon|postmaster)@/.test(from) ||
      /(undeliverable|delivery status notification|address not found|mail delivery (failed|subsystem)|returned mail|delivery has failed)/.test(subj)) return 'bounce';
  const auto = lc_(header_(m, 'Auto-Submitted'));
  if ((auto && auto !== 'no') ||
      /(out of (the )?office|auto(matic)?[- ]?reply|on (vacation|holiday|leave|pto)\b|away until|limited access to (my )?e-?mail|i('m| am) currently (away|out))/.test(t + ' ' + subj)) return 'ooo';
  if (/(unsubscribe|remove me|take me off|stop (emailing|contacting|sending)|do not (contact|email)|don't (contact|email) me|opt[- ]?out)/.test(t)) return 'unsubscribe';
  if (/(not interested|no thanks|no thank you|not a (good )?fit|we('re| are) (good|all set)|not looking|pass on this|not for us|not at this time)/.test(t)) return 'negative';
  if (/(not (right )?now\b|maybe later|in a few (weeks|months)|next (quarter|year|month)|circle back|reach out (again )?(in|after)|not the right time|revisit)/.test(t)) return 'not_now';
  if (/(calendly\.com|cal\.com\/|meet\.google\.com|zoom\.us\/|invite (sent|you)|\bbooked\b|see you (then|on|tomorrow|next)|\bscheduled\b)/.test(t)) return 'meeting';
  if (/(reach out to|talk to my|speak (with|to) my|contact (my|our)|looping in|loop in|cc'?ing|best person|right person)/.test(t)) return 'referral';
  if (/(interested|happy to (chat|talk|connect|hop)|let's (chat|talk|connect|do it|set)|sounds (good|great|interesting)|call me|free now|tell me more|open to|would love|\bkeen\b|works for me|set up a (call|time)|hop on a call|book a time|send (me )?(a few |some )?times|\bavailable\b)/.test(t)) return 'positive';
  if (/(how much|pricing|\bprice\b|\bcost\b|budget|already (have|use|using|work with)|we use|why would)/.test(t)) return 'objection';
  if (t.indexOf('?') >= 0) return 'question';
  return 'other';
}

/** Keep only the new text of a message: cut at the quoted history. */
function newText_(b) {
  const lines = String(b).split(/\r?\n/), out = [];
  const quoteHead = /^\s*(On|Le|Am|El|Il|Op|Em)\s.{4,240}(wrote|écrit|schrieb|escribió|scritto|geschreven|escreveu)\s*:?\s*$/i;
  const otherHead = /(tarihinde şunu\s*yazdı|написал|kirjoitti)\s*:?\s*$/i;
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i], two = ln + ' ' + (lines[i + 1] || '');
    if (/^\s*>/.test(ln) || quoteHead.test(ln) || quoteHead.test(two) || otherHead.test(ln) || otherHead.test(two) ||
        /^-{2,}\s*(Original Message|Forwarded message)/i.test(ln) || (/^From:\s/.test(ln) && out.length)) break;
    out.push(ln);
  }
  return out.join('\n').trim();
}

function tag_(body, step, msgs) {
  const t = String(body).toLowerCase().replace(/[’]/g, "'").replace(/\s+/g, ' ');
  const first = step === '1st';
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    if (!m.match_phrase || ((String(m.step) === '1st') !== first)) continue;
    const ps = String(m.match_phrase).toLowerCase().replace(/[’]/g, "'").split('|')
      .map(function (s) { return s.trim(); }).filter(String);
    if (ps.some(function (p) { return t.indexOf(p) >= 0; })) return m.msg_id;
  }
  return 'UNTAGGED';
}

function normSubject_(s) {
  let x = String(s || '').trim().toLowerCase(), n;
  while ((n = x.replace(/^(re|fw|fwd|aw|sv)\s*:\s*/, '')) !== x) x = n;
  return x.replace(/\s+/g, ' ');
}
function normSubjectKeepCase_(s) {
  let x = String(s || '').trim(), n;
  while ((n = x.replace(/^(re|fw|fwd|aw|sv)\s*:\s*/i, '')) !== x) x = n;
  return x;
}

function rootDomain_(email) {
  const d = lc_(String(email).split('@')[1] || '').replace(/^www\./, '');
  if (!d || FREE_MAIL.indexOf(d) >= 0) return '';
  const p = d.split('.');
  if (p.length <= 2) return d;
  const sld = p[p.length - 2], tld = p[p.length - 1];
  if (tld.length === 2 && ['co', 'com', 'org', 'net', 'ac', 'gov', 'edu', 'ltd', 'plc'].indexOf(sld) >= 0) return p.slice(-3).join('.');
  return p.slice(-2).join('.');
}

function parseAddrs_(s) {
  const out = [];
  const re = /(?:"?([^"<>,]*?)"?\s*<)?([A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})>?/g;
  let m;
  while ((m = re.exec(String(s || ''))) !== null) out.push({email: m[2].toLowerCase(), name: (m[1] || '').trim()});
  return out;
}
function addr_(s) { const a = parseAddrs_(s); return a.length ? a[0].email : lc_(s); }
function name_(s) { const a = parseAddrs_(s); return a.length && a[0].name ? a[0].name : (a.length ? a[0].email.split('@')[0] : ''); }
function externals_(m, ctx) {
  return parseAddrs_(m.getTo() + ',' + m.getCc()).filter(function (a) { return !ctx.isTeam(a.email) && !ctx.isExcluded(a.email); })
    .map(function (a) { return {email: a.email, name: a.name || a.email.split('@')[0]}; });
}
function header_(m, name) { try { return String(m.getHeader(name) || ''); } catch (e) { return ''; } }

// ------------------------------------------------------------------------------------------------ sheet helpers
function ss_() { return SpreadsheetApp.getActive(); }
function sh_(name) { const s = ss_().getSheetByName(name); if (!s) throw new Error('Missing tab ' + name); return s; }
function me_() { return lc_(Session.getEffectiveUser().getEmail() || Session.getActiveUser().getEmail()); }
function lc_(v) { return String(v === undefined || v === null ? '' : v).trim().toLowerCase(); }
function toast_(msg) { try { ss_().toast(msg, '8-o', 10); } catch (e) { Logger.log(msg); } }

function headers_(sheet) {
  const list = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(String);
  const idx = {};
  list.forEach(function (h, i) { if (h && idx[h] === undefined) idx[h] = i; });
  return {list: list, idx: idx};
}

function lastDataRow_(sheet, cols) {
  const max = sheet.getLastRow();
  if (max < 2) return 1;
  let last = 1;
  cols.forEach(function (c) {
    const v = sheet.getRange(2, c, max - 1, 1).getValues();
    for (let i = v.length - 1; i >= 0; i--) if (v[i][0] !== '' && v[i][0] !== null) { last = Math.max(last, i + 2); break; }
  });
  return last;
}

/** Rows as objects (skips rows whose first two columns are empty, e.g. pre-filled formula rows). */
function readTable_(name) {
  const s = sh_(name);
  const vals = s.getDataRange().getValues();
  const hdr = vals[0].map(String);
  return vals.slice(1).filter(function (r) { return r[0] !== '' || r[1] !== ''; }).map(function (r) {
    const o = {};
    hdr.forEach(function (h, i) { if (h && o[h] === undefined) o[h] = r[i]; });
    return o;
  });
}

function settings_() {
  const grid = sh_('Settings').getDataRange().getValues();
  const out = {cfg: {}, classes: {}, mailboxes: [], mbTable: null};
  for (let r = 0; r < grid.length; r++) {
    for (let c = 0; c < grid[r].length - 1; c++) {
      const v = grid[r][c], nx = grid[r][c + 1];
      if (v === 'key' && nx === 'value' && !Object.keys(out.cfg).length) {
        for (let k = r + 1; k < grid.length && grid[k][c] !== ''; k++) out.cfg[String(grid[k][c])] = grid[k][c + 1];
      }
      if (v === 'class' && nx === 'score' && !Object.keys(out.classes).length) {
        for (let k = r + 1; k < grid.length && grid[k][c] !== ''; k++) out.classes[grid[k][c]] = Number(grid[k][c + 1]);
      }
      if (v === 'mailbox' && nx === 'person' && !out.mbTable) {
        const hdr = [];
        for (let j = c; j < grid[r].length && grid[r][j] !== ''; j++) hdr.push(String(grid[r][j]));
        out.mbTable = {row: r + 1, col: c + 1, hdr: hdr};
        for (let k = r + 1; k < grid.length && grid[k][c] !== ''; k++) {
          const o = {_row: k + 1};
          hdr.forEach(function (h, j) { o[h] = grid[k][c + j]; });
          out.mailboxes.push(o);
        }
      }
    }
  }
  return out;
}

function cfg_(S, key, dflt) {
  const v = S.cfg[key];
  if (v === undefined || v === null || v === '' || String(v).indexOf('EMPTY') === 0) return dflt;
  return v;
}
function list_(S, key) { return lc_(cfg_(S, key, '')).split(/[,\s]+/).filter(String); }

function setMailbox_(S, mailbox, fields) {
  const t = S.mbTable;
  if (!t) return;
  const sheet = sh_('Settings');
  let row = (S.mailboxes.filter(function (m) { return lc_(m.mailbox) === mailbox; })[0] || {})._row;
  if (!row) { row = t.row + 1 + S.mailboxes.length; sheet.getRange(row, t.col).setValue(mailbox); }
  Object.keys(fields).forEach(function (k) {
    const j = t.hdr.indexOf(k);
    if (j >= 0) sheet.getRange(row, t.col + j).setValue(fields[k]);
  });
}
