// Mock Apps Script runtime: sheets from the recalculated xlsx, Gmail from a fixture.
// python3 apps_script/test/export_sheet.py master/gtm_master.xlsx && TZ=Asia/Kolkata node apps_script/test/run.js apps_script/Code.gs
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const data = JSON.parse(fs.readFileSync(__dirname + '/sheet.json'));
const rev = v => (v && v.__d ? new Date(v.__d) : v);

class Range {
  constructor(sh, r, c, nr = 1, nc = 1) { Object.assign(this, {sh, r, c, nr, nc}); }
  cells(fn) { const o = []; for (let i = 0; i < this.nr; i++) { const row = []; for (let j = 0; j < this.nc; j++) row.push(fn(this.r + i - 1, this.c + j - 1)); o.push(row); } return o; }
  getValues() { return this.cells((i, j) => { const v = (this.sh.vals[i] || [])[j]; return v === undefined ? '' : v; }); }
  getDisplayValues() { return this.getValues().map(r => r.map(String)); }
  getFormulas() { return this.cells((i, j) => (this.sh.forms[i] || [])[j] || ''); }
  setValues(v) { assert.strictEqual(v.length, this.nr, 'rows'); v.forEach((row, i) => { assert.strictEqual(row.length, this.nc, 'cols'); row.forEach((x, j) => this.sh.set(this.r + i - 1, this.c + j - 1, x)); }); return this; }
  setValue(x) { this.sh.set(this.r - 1, this.c - 1, x); return this; }
  clearContent() { for (let i = 0; i < this.nr; i++) for (let j = 0; j < this.nc; j++) this.sh.set(this.r + i - 1, this.c + j - 1, ''); return this; }
  copyTo(t) { const f = this.getFormulas()[0][0]; for (let i = 0; i < t.nr; i++) { t.sh.ensure(t.r + i - 1); t.sh.forms[t.r + i - 1][t.c - 1] = f.replace(/(\D)2\b/g, '$1' + (t.r + i)); } t.sh.copies = (t.sh.copies || 0) + t.nr; }
  setNumberFormat() { return this; }
}
class Sheet {
  constructor(name, d) { this.name = name; this.vals = d.vals.map(r => r.map(rev)); this.forms = d.forms; this.writes = 0; this.width = d.vals[0].length; }
  ensure(i) { while (this.vals.length <= i) { this.vals.push(Array(this.width).fill('')); this.forms.push(Array(this.width).fill('')); } }
  set(i, j, x) { this.ensure(i); if (this.forms[i][j]) throw new Error(`write over formula ${this.name}!R${i + 1}C${j + 1}`); this.vals[i][j] = x; this.writes++; }
  getRange(r, c, nr, nc) { if (typeof r === 'string') return new Range(this, 5, 1, 2, 8); return new Range(this, r, c, nr || 1, nc || 1); }
  getLastRow() { for (let i = this.vals.length - 1; i >= 0; i--) if (this.vals[i].some(v => v !== '') || this.forms[i].some(Boolean)) return i + 1; return 0; }
  getLastColumn() { return this.width; }
  getMaxRows() { return this.vals.length; }
  insertRowsAfter(_, n) { this.ensure(this.vals.length + n - 1); }
  getDataRange() { return new Range(this, 1, 1, this.getLastRow(), this.width); }
}
const sheets = {}; Object.keys(data).forEach(n => sheets[n] = new Sheet(n, data[n]));

// ---- Gmail fixture
let ME = 'abhinay@8-o.co';
const mailboxes = {};
function msg(id, from, to, date, subject, body, extra = {}) {
  return {getId: () => id, getFrom: () => from, getTo: () => to, getCc: () => extra.cc || '', getDate: () => new Date(date),
          getSubject: () => subject, getPlainBody: () => body, isDraft: () => false,
          getHeader: h => (h === 'Message-ID' ? (extra.rfc || '<' + id + '@mail>') : (extra.headers || {})[h] || ''),
          createDraftReplyAll: b => { drafts.push({to, body: b}); return {getMessage: () => ({getTo: () => to}), update: () => {}}; }};
}
function thread(id, msgs) { return {getId: () => id, getMessages: () => msgs, getPermalink: () => 'https://mail.google.com/mail/#all/' + id, getFirstMessageSubject: () => msgs[0].getSubject(), addLabel: () => {}}; }
const drafts = [], mails = [];
mailboxes['abhinay@8-o.co'] = [
  // already backfilled (same Gmail ids) -> must be skipped
  thread('1a1c179e05f44304', [
    msg('1a1c258c4e9a143a', 'Abhinay Reddy <abhinay@8-o.co>', 'mike@usedrip.ai', '2026-11-10T03:36:00', '1 on 1 Design partner invitation for drip AI', 'Hey mike,\nwe\'d handle your outbound end-to-end and send you a quick Friday memo...'),
    msg('1a1c25c6a0f325b6', 'Michael Levin <mike@drippay.dev>', 'abhinay@8-o.co', '2026-11-10T03:39:21', 'Re: 1 on 1 Design partner invitation for drip AI', 'call me 310\n\nOn Mon, Nov 9, 2026 at 2:06 PM Abhinay Reddy <abhinay@8-o.co>\nwrote:\n> Hey mike'),
    // NEW: our booking-link reply + their confirmation
    msg('new-drip-1', 'Abhinay Reddy <abhinay@8-o.co>', 'Michael Levin <mike@drippay.dev>', '2026-11-15T20:00:00', 'Re: 1 on 1 Design partner invitation for drip AI', 'Here is my link https://cal.com/x\n\nOn Tue wrote:\n> free now'),
    msg('new-drip-2', 'Michael Levin <mike@drippay.dev>', 'abhinay@8-o.co', '2026-11-15T21:00:00', 'Re: 1 on 1 Design partner invitation for drip AI', 'Booked for Tuesday, see you then.\n\nOn Sun, Nov 15 Abhinay wrote:\n> link'),
  ]),
  // NEW first touch, tagged by match phrase, then a not-now reply
  thread('t-forge', [
    msg('m-forge-1', 'Abhinay Reddy <abhinay@8-o.co>', '"Jack Strauss" <jack@forgepoint.ai>', '2026-11-16T10:00:00', 'design partner / Forgepoint', "Hey Jack,\nWe're still early, so we’re doing the GTM work ourselves for a few startups."),
    msg('m-forge-2', 'Jack Strauss <jack@forgepoint.ai>', 'abhinay@8-o.co', '2026-11-16T12:00:00', 'Re: design partner / Forgepoint', 'Not right now, maybe next quarter.\n\nOn Mon, 16 Nov 2026 at 10:00, Abhinay Reddy <abhinay@8-o.co> wrote:\n> Hey'),
  ]),
  // NEW first touch that bounced
  thread('t-bounce', [
    msg('m-b-1', 'abhinay@8-o.co', 'ceo@arpio.io', '2026-11-16T11:00:00', 'design partner / Arpio', 'Hey,\nstepping in as forward-deployed GTM engineers...'),
    msg('m-b-2', 'Mail Delivery Subsystem <mailer-daemon@googlemail.com>', 'abhinay@8-o.co', '2026-11-16T11:01:00', 'Delivery Status Notification (Failure)', 'Address not found'),
  ]),
  // untagged copy + internal-only + newsletter (ignored)
  thread('t-untag', [msg('m-u-1', 'abhinay@8-o.co', 'a@manufact.com', '2026-11-16T12:00:00', 'quick q', 'Totally new copy')]),
  thread('t-int', [msg('m-i-1', 'abhinay@8-o.co', 'oukasha@8-o.co', '2026-11-16T12:00:00', 'internal', 'hi')]),
  thread('t-news', [msg('m-n-1', 'news@substack.com', 'abhinay@8-o.co', '2026-11-16T12:00:00', 'digest', 'hi')]),
  // cc copy of an Oukasha message (same as backfill id)
  thread('1a1d317896fad3ab', [msg('1a1d317896fad3ab', 'oukasha javed <oukasha@8-o.co>', 'emre@spaceflow.tech', '2026-11-13T09:38:04', 'Re: spaceflow <> 8-o', 'Could you share the materials on Slack', {cc: 'abhinay@8-o.co', rfc: '<sf-last@mail>'})]),
];
mailboxes['oukasha@8-o.co'] = [
  thread('o-sf', [
    msg('o1', 'oukasha javed <oukasha@8-o.co>', 'emre@spaceflow.tech', '2026-11-05T19:16:30', 'spaceflow <> 8-o', "Hey Emre,\nwe want to understand how customer acquisition actually works on the ground."),
    msg('o2', 'Emre Isik <emre@spaceflow.tech>', 'oukasha@8-o.co', '2026-11-06T00:57:10', 'Re: spaceflow <> 8-o', 'Seems interesting. I would not be paying until it works but happy to chat!\n\nOn Thu, 5 Nov 2026 at 7:16 pm, oukasha javed <oukasha@8-o.co> wrote:\n> Hey'),
    msg('o3', 'oukasha javed <oukasha@8-o.co>', 'emre@spaceflow.tech', '2026-11-09T16:08:00', 'Re: spaceflow <> 8-o', 'Hi Emre, Before we meet, here is some context'),
    msg('o4', 'Emre Isik <emre@spaceflow.tech>', 'oukasha@8-o.co', '2026-11-09T22:48:00', 'Re: spaceflow <> 8-o', "Sounds great, let's talk this week. What is your availability like?\n\noukasha javed <oukasha@8-o.co>, 9 Kas 2026 Pzt, 16:08 tarihinde şunu\nyazdı:\n> Hi"),
    msg('o5', 'oukasha javed <oukasha@8-o.co>', 'emre@spaceflow.tech', '2026-11-09T23:30:00', 'Re: spaceflow <> 8-o', 'How about Wed 9:30 - 10:30 am PST'),
    msg('o6', 'Emre Isik <emre@spaceflow.tech>', 'oukasha@8-o.co', '2026-11-10T01:28:00', 'Re: spaceflow <> 8-o', 'Works! See you then'),
    msg('o7', 'oukasha javed <oukasha@8-o.co>', 'emre@spaceflow.tech', '2026-11-11T07:19:00', 'Re: spaceflow <> 8-o', 'I have scheduled the meet'),
    msg('o8', 'oukasha javed <oukasha@8-o.co>', 'emre@spaceflow.tech', '2026-11-13T09:38:04', 'Re: spaceflow <> 8-o', 'Could you share the materials on Slack', {cc: 'abhinay@8-o.co', rfc: '<sf-last@mail>'}),
  ]),
];

const props = {};
const ctx = {
  console, Date, JSON, Math, Object, String, Number, Array, RegExp, Error,
  SpreadsheetApp: {getActive: () => ({getSheetByName: n => sheets[n], setSpreadsheetTimeZone: () => {}, getSpreadsheetTimeZone: () => 'Asia/Kolkata', toast: m => console.log('TOAST:', m), getUrl: () => 'https://sheet'}), getUi: () => ({alert: m => console.log('ALERT:', m)}), flush: () => {}},
  GmailApp: {search: (q, s, n) => mailboxes[ME].slice(s, s + n), getUserLabelByName: () => null, createLabel: () => ({}),
             getDrafts: () => [], createDraft: (to, subj, body) => drafts.push({to, subj, body}),
             getThreadById: id => mailboxes[ME].find(t => t.getId() === id) || null},
  LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock: () => {}})},
  PropertiesService: {getUserProperties: () => ({getProperty: k => props[ME + k] || null, setProperty: (k, v) => props[ME + k] = v, deleteProperty: k => delete props[ME + k]})},
  Session: {getEffectiveUser: () => ({getEmail: () => ME}), getActiveUser: () => ({getEmail: () => ME})},
  Utilities: {formatDate: (d, tz, f) => d.toISOString().slice(0, 10).replace(/-/g, f.indexOf('/') >= 0 ? '/' : (f === 'yyyyMMdd' ? '' : '-')), getUuid: () => 'abcd1234-xyz'},
  ScriptApp: {getProjectTriggers: () => [], deleteTrigger: () => {}, newTrigger: () => { const b = {timeBased: () => b, everyMinutes: () => b, everyHours: () => b, atHour: () => b, everyDays: () => b, create: () => b}; return b; }},
  MailApp: {sendEmail: o => mails.push(o)}, Logger: {log: console.log},
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2]).toString(), ctx);
const run = c => vm.runInContext(c, ctx);

// unit checks
assert.strictEqual(run('rootDomain_("mike@app.drippay.dev")'), 'drippay.dev');
assert.strictEqual(run('rootDomain_("a@x.co.uk")'), 'x.co.uk');
assert.strictEqual(run('rootDomain_("a@gmail.com")'), '');
assert.strictEqual(run('normSubject_("Re: RE:  Design  partner")'), 'design partner');
assert.strictEqual(run(`newText_("free now\\n\\nOn Mon, Nov 9, 2026, 5:46 PM Abhinay Reddy <abhinay@8-o.co> wrote:\\n> x")`), 'free now');
const mk = (from, subj, body, h) => run(`classify_({getSubject:()=>${JSON.stringify(subj)},getHeader:n=>(${JSON.stringify(h || {})})[n]||''},${JSON.stringify(from)},${JSON.stringify(body)})`);
const cases = [['x@y.com', 'Re', 'call me 310', 'positive'], ['x@y.com', 'Re', 'free now', 'positive'], ['x@y.com', 'Re', 'Seems interesting. I would not be paying until it works but happy to chat!', 'positive'],
  ['x@y.com', 'Re', 'Works! See you then', 'meeting'], ['x@y.com', 'Re', 'Not interested, thanks', 'negative'], ['x@y.com', 'Re', 'Please remove me from your list', 'unsubscribe'],
  ['mailer-daemon@googlemail.com', 'Delivery Status Notification (Failure)', '', 'bounce'], ['x@y.com', 'Automatic reply: away', 'I am out of the office', 'ooo'],
  ['x@y.com', 'Re', 'Not right now, maybe next quarter.', 'not_now'], ['x@y.com', 'Re', 'Please reach out to my cofounder Sam', 'referral'],
  ['x@y.com', 'Re', 'How much does it cost?', 'objection'], ['x@y.com', 'Re', 'What exactly do you do?', 'question'], ['x@y.com', 'Re', 'ok', 'other']];
cases.forEach(([f, s, b, want]) => assert.strictEqual(mk(f, s, b), want, b));
console.log('unit checks ok:', cases.length, 'classifier cases');

// sync as abhinay
const logBefore = sheets.Log.getLastRow();
ME = 'abhinay@8-o.co';
let n = run('syncMailbox_()');
console.log('abhinay sync added', n);
n = run('syncMailbox_()');
assert.strictEqual(n, 0, 'second sync must add nothing (idempotent)');
ME = 'oukasha@8-o.co';
n = run('syncMailbox_()');
console.log('oukasha sync added', n, '(quoted rows replaced in place)');
assert.strictEqual(run('syncMailbox_()'), 0, 'oukasha second sync idempotent');

const L = run('readTable_("Log")');
const show = r => [r.touch_id, r.date instanceof Date ? r.date.toISOString().slice(0, 16) : r.date, r.mailbox, r.direction, r.step, r.contact_email, r.company_domain, r.msg_id, r.class_auto, r.source].join(' | ');
L.filter(r => /spaceflow|forgepoint|arpio|manufact|drip/.test(r.company_domain)).forEach(r => console.log(show(r)));
assert.ok(!L.some(r => r.source === 'backfill_quoted'), 'all quoted rows replaced');
assert.strictEqual(L.filter(r => r.company_domain === 'spaceflow.tech').length, 8, 'spaceflow: 8 rows, no duplicates');
assert.strictEqual(L.find(r => r.touch_id === 'm-forge-1').msg_id, 'M-04a');
assert.strictEqual(L.find(r => r.touch_id === 'm-u-1').msg_id, 'UNTAGGED');
assert.strictEqual(L.find(r => r.touch_id === 'new-drip-1').step, 'Reply');
assert.strictEqual(L.find(r => r.touch_id === 'm-b-2').class_auto, 'bounce');
assert.strictEqual(L.find(r => r.touch_id === 'm-b-2').contact_email, 'ceo@arpio.io');
assert.ok(!L.some(r => r.touch_id === 'm-i-1' || r.touch_id === 'm-n-1'), 'internal + newsletter ignored');
{ const H = run('headers_(sh_("Log"))'); const row = L.findIndex(r => r.touch_id === 'm-u-1') + 2; assert.ok(sheets.Log.forms[row - 1][H.idx.outcome], 'new row has formulas'); }

const A = run('readTable_("Accounts")');
const acc = d => A.find(a => a.company_domain === d);
console.log('Accounts:', ['usedrip.ai', 'forgepoint.ai', 'arpio.io', 'spaceflow.tech', 'manufact.com'].map(d => d + '=' + (acc(d) || {}).status + '/' + (acc(d) || {}).outcome).join(', '));
assert.strictEqual(acc('usedrip.ai').status, 'Meeting');
assert.strictEqual(acc('forgepoint.ai').status, 'Replied');
assert.strictEqual(acc('arpio.io').status, 'Bounced');
const Q = run('readTable_("Queue")');
console.log('Queue', Q.length, 'rows; by action:', JSON.stringify(Q.reduce((m, r) => (m[r.action] = (m[r.action] || 0) + 1, m), {})));
Q.slice(0, 6).forEach(r => console.log('  ', r.priority, r.action, r.company_domain, r.owner, r.suggested_msg, String(r.why).slice(0, 60)));
assert.ok(Q.some(r => r.action === 'Fix bounce' && r.company_domain === 'arpio.io'));
assert.ok(!Q.some(r => r.action === 'Reply now' && r.company_domain === 'usedrip.ai' && false));
assert.ok(Q.some(r => r.action === 'Tag msg_id'));
assert.ok(Q.filter(r => r.action === 'First touch').every(r => r.owner));
const C = run('readTable_("Contacts")');
assert.ok(C.some(c => c.email === 'jack@forgepoint.ai' || c.company_domain === 'forgepoint.ai'));
ME = 'abhinay@8-o.co';
run('sendMyDigest()');
console.log('digest:', mails[0].subject);
run('createMyDrafts()');
console.log('drafts:', drafts.length, drafts.slice(0, 2).map(d => d.to + ' :: ' + String(d.subj || '') + ' :: ' + d.body.slice(0, 50).replace(/\n/g, ' ')));
assert.ok(drafts.every(d => !/\{first_name\}|\{company\}|\{sender\}/.test(d.body)), 'placeholders filled');
run('healthCheck()');
console.log('ALL CHECKS PASSED');
