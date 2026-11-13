// x3d-ray notebook: the looks you take at the data, kept in order, with the number of looks beside every result.
//
// Why it exists: the more looks anyone takes at a body of data, the easier it is to find something that is only chance. The notebook
// keeps the count in front of you (the declared looks and the ones you add), and records each look in the tamper-evident chain of
// investigation.js, so the count survives a reload, can be exported, and can be replayed to the same numbers.
//
// What it does not do: it computes nothing itself. Every number comes from the `eda` tool of tools.js (the stored ledger, or the exact
// permutation count in EDA_INFER), and the sentence shown is the one that tool returned. Your own notes are yours: they are not
// checked, not part of the record, and never enter the chain, the bundle or a receipt.
//
// What the chain does not prove: that a look was computed, shown or read, who took it, or that nothing else was looked at. It proves
// the order and integrity of what was recorded; the counts are a lower bound. Without secure hashing (an insecure context) the notebook
// still counts looks, in memory only, and says so.
import { makeTools, EDA_INFER } from './tools.js';
import * as INV from './investigation.js';

export const NOTEBOOK_FORMAT = 'x3dray.notebook.v1';
const MAX_IMPORT_BYTES = 1048576, MAX_NOTE = 2000, MAX_ENTRIES_SHOWN = 256;
const CATALOG = Object.freeze({ entities: [], relations: [], mutations: [], findings: [], predicates: [] });
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const MEASURES = [['lines', 'lines changed'], ['files', 'files changed'], ['gap_s', 'quiet time before a commit'], ['hour4', 'UTC four-hour block'],
  ['weekday', 'UTC weekday'], ['repo', 'which repo']];
const FORKS = [['all', 'all commits'], ['no_merge', 'without merges'], ['default_branch', 'default branch only'], ['no_mass', 'without mass edits']];
const NOT_PROVEN = 'The chain records the order and integrity of the looks listed here. It does not prove that a look was computed, shown or read, who took it, ' +
  'or that nothing else was looked at; the counts are a lower bound, and the hash chain is integrity bookkeeping, not authentication.';

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sha = text => EDA_INFER.sha256Text(EDA_INFER.canon(String(text)));      // canonical JSON of the text keeps it ASCII; SHA-256 of that
const keyOf = look => INV.lookKey(look);
const lookOfEvent = ev => (ev.kind === 'declared' ? { kind: 'declared', id: ev.id } : { kind: ev.kind, selector: ev.selector });
const findings = B => (((B.eda || {}).looks || {}).findings) || [];

// The look counter without a chain: the same semantics as the tool set's own (a repeat is one look, a declared look is not counted
// as asked), plus the ordered list of looks so the panel can show them.
function memorySink() {
  const seen = new Set(), looks = [], unrecorded = [];
  let asked = 0;
  return {
    note(look) {
      const key = keyOf(look);
      if (seen.has(key)) return;
      seen.add(key); looks.push(look);
      if (look.kind !== 'declared') asked++;
    },
    get asked() { return asked; }, looks, unrecorded, settle: async () => null,
  };
}

// The inverse of a recorded look: the tool call that asks it again. Repos are positions in B.repos.
function groupArgs(B, g) {
  const out = {};
  if (g.repo) out.repo = g.repo.map(i => (B.repos[i] || {}).name).join(',');
  if (g.who) out.who = g.who.join(',');
  for (const k of ['from', 'to', 'day', 'session', 'rule', 'grade']) if (g[k] !== undefined) out[k] = g[k];
  for (const k of ['hour', 'min_lines', 'max_lines']) if (g[k] !== undefined) out[k] = g[k];
  if (g.weekday !== undefined) out.weekday = String(g.weekday);
  return out;
}
function lookToCall(B, look) {
  if (look.kind === 'declared') return { topic: 'finding', id: look.id };
  const s = look.selector;
  if (look.kind === 'slice') return { topic: 'slice', ...groupArgs(B, s), fork: s.fork, measure: s.measure };
  if (look.kind === 'compare') {
    const strip = g => { const { session, rule, grade, ...rest } = groupArgs(B, g); return rest; };
    return { topic: 'compare', a: strip(s.a), b: strip(s.b), fork: s.fork, measure: s.measure };
  }
  return null;                                                // a filtered row query: counted, not replayed here
}

// Words for a recorded look that has no stored result in this session (taken in Ask Claude, or imported without a result).
function repoLabel(B, i) { const r = (B.repos || [])[i]; return !r ? 'repo #' + i : r.visibility === 'private' ? 'a private repo' : r.name; }
function classLabel(B, c) { return (((B.eda || {}).classes || {}).labels || {})[c] || c; }
function groupText(B, g) {
  const p = [];
  if (g.who) p.push(g.who.map(c => classLabel(B, c)).join(' + '));
  if (g.repo) p.push('in ' + g.repo.map(i => repoLabel(B, i)).join(', '));
  if (g.day) p.push('on ' + g.day);
  if (g.from || g.to) p.push(`from ${g.from || 'the start'} to ${g.to || 'the end'}`);
  if (g.hour !== undefined) p.push('UTC hour ' + g.hour);
  if (g.weekday !== undefined) p.push(WEEKDAYS[g.weekday]);
  if (g.min_lines !== undefined) p.push('at least ' + g.min_lines + ' lines');
  if (g.max_lines !== undefined) p.push('at most ' + g.max_lines + ' lines');
  if (g.rule) p.push('flagged ' + g.rule + (g.grade ? ' (grade ' + g.grade + ' or better)' : ''));
  if (g.session) p.push('in one Claude session');
  return p.join(', ') || 'all commits';
}
function describe(B, look) {
  if (look.kind === 'declared') { const f = findings(B).find(x => x.id === look.id); return { title: look.id, text: f ? f.text : look.id }; }
  const s = look.selector;
  if (look.kind === 'compare') return { title: 'compare', text: `${groupText(B, s.a)} vs ${groupText(B, s.b)}, ${s.measure}, ${s.fork}` };
  return { title: look.kind === 'slice' ? 'slice' : 'rows', text: groupText(B, s) + (s.measure ? `, ${s.measure}, ${s.fork}` : '') };
}

// What the chain is bound to: the commit rows and the stored analysis. Both digests are of canonical JSON of stored fields, so anyone can recompute them.
function digestsOf(B) {
  const I = EDA_INFER, names = (B.repos || []).map(r => r.name);
  const rows = (B.commits || []).map(c => [c.i, c.r, c.t, c.c, c.a, c.d, c.f, c.m, c.b]);
  return { fix: I.sha256Text(I.canon({ domain: 'x3dray.notebook.fixture.v1', repos: names, rows })),
    ana: I.sha256Text(I.canon({ domain: 'x3dray.notebook.analysis.v1', eda: B.eda })), names };
}

async function initChain(nb) {
  const B = nb.B;
  if (!B.eda || !B.eda.looks || !Array.isArray(B.eda.looks.findings) || !B.eda.looks.findings.length) { nb.mode = 'none'; nb.reason = 'this build has no stored looks'; return; }
  if (!(globalThis.crypto && globalThis.crypto.subtle)) { nb.mode = 'memory'; nb.reason = 'this browser context has no secure hashing (it needs https or localhost)'; return; }
  try {
    const d = digestsOf(B);
    nb.fix = d.fix; nb.ana = d.ana; nb.ctx = await INV.looksContext(B.eda.looks, d.names);
    nb.mode = 'chain';
  } catch (e) { nb.mode = 'memory'; nb.reason = 'the stored looks cannot be put in a hash chain (' + (e.code || e.name) + ')'; }
}

async function newSession(nb, log) {
  nb.results = new Map(); nb.notes = new Map();
  if (nb.mode === 'chain') nb.sink = await INV.openLookSink(log || await INV.newLog(nb.fix, nb.ana), CATALOG, nb.fix, nb.ana, nb.ctx);
  else nb.sink = memorySink();
  nb.tools = makeTools(nb.B, nb.scene, { looks: nb.sink });
  nb.onTools(nb.tools);
}

async function snapshot(nb) {
  const B = nb.B, ledger = ((B.eda.looks || {}).ledger) || {};
  let looks, declared = ledger.declared, inspected, asked = nb.sink.asked, head = null, events = null;
  if (nb.mode === 'chain') {
    const log = await nb.sink.settle();
    const st = await INV.replay(log, CATALOG, nb.fix, nb.ana, nb.ctx);
    looks = log.events.filter(r => r.event.action === 'take_look').map(r => lookOfEvent(r.event));
    declared = st.looks.declared; inspected = st.looks.inspected.length; asked = Math.max(st.looks.asked, nb.sink.asked);
    head = st.event_head; events = st.event_count;
  } else { looks = nb.sink.looks.slice(); inspected = looks.filter(l => l.kind === 'declared').length; }
  return { looks, declared, inspected, asked, head, events, unrecorded: nb.sink.unrecorded.slice(), recorded: looks.length };
}

const eda = (nb, args) => nb.tools.find(t => t.name === 'eda').execute(args);

function remember(nb, look, rec) {
  const key = keyOf(look);
  if (!nb.results.has(key)) nb.results.set(key, { ...rec, sha256: sha(rec.text) });
  return key;
}

// ---- the actions; each returns a short outcome for the message line and rethrows nothing: the panel shows the refusal as it is.
async function takeDeclared(nb, id) {
  const r = eda(nb, { topic: 'finding', id });
  const look = { kind: 'declared', id };
  const known = nb.results.has(keyOf(look));
  remember(nb, look, { kind: 'declared', title: id, text: r.finding.text, grade: r.finding.grade, reason: r.finding.grade_reason });
  return known ? `${id} was already in the notebook` : `${id} added`;
}
async function takeCompare(nb, args) {
  const r = eda(nb, { topic: 'compare', ...args });
  const known = nb.results.has(keyOf(r.look));
  remember(nb, r.look, { kind: 'compare', title: 'compare', text: r.text, grade: null, q_text: r.q_text, m: r.family.m, p_text: r.null_model.p_text, call: { topic: 'compare', ...args } });
  return known ? 'the same question was already in the notebook; its answer is unchanged' : `added; q is counted over ${r.family.m} looks`;
}
async function recompute(nb) {
  const r = eda(nb, { topic: 'recompute' });
  nb.recomputed = r;
  return r.summary;
}

function exportDoc(nb, snap) {
  const results = [], notes = [];
  for (const look of snap.looks) {
    const key = keyOf(look), res = nb.results.get(key);
    if (res) results.push({ key, kind: res.kind, text: res.text, sha256: res.sha256, ...(res.call ? { call: res.call } : {}) });
    const note = nb.notes.get(key);
    if (note) notes.push({ key, note });
  }
  return { format: NOTEBOOK_FORMAT, fixture_digest: nb.fix, analysis_digest: nb.ana, looks_digest: nb.ctx.digest, log: nb.sink.log || null, results, notes,
    not_proven: NOT_PROVEN };
}

// Replays an exported notebook: the chain must verify against this page's data, and every look that has a call is asked again on a scratch tool
// set, in the recorded order, so each answer sees the same number of earlier looks as it did the first time. The sha-256 of each sentence must
// equal the exported one. Only then does it replace the (empty) notebook.
async function importText(nb, text) {
  if (nb.mode !== 'chain') throw new Error('importing needs the hash chain, which this context does not have');
  if (typeof text !== 'string' || text.length > MAX_IMPORT_BYTES) throw new Error('that is not a notebook (too large)');
  let doc;
  try { doc = JSON.parse(text); } catch (e) { throw new Error('that is not JSON'); }
  if (!doc || typeof doc !== 'object' || doc.format !== NOTEBOOK_FORMAT || !doc.log || typeof doc.log !== 'object') throw new Error('that is not an x3d-ray notebook');
  const now = await snapshot(nb);
  if (now.looks.length) throw new Error('this notebook already has looks: start a new notebook before importing one');
  let state;
  try { state = await INV.replay(doc.log, CATALOG, nb.fix, nb.ana, nb.ctx); }
  catch (e) {
    const why = { context_mismatch: 'it was made for different data', looks_digest_mismatch: 'its stored looks differ from this build', looks_context_missing: 'it has no looks context' }[e.code];
    throw new Error('the notebook is refused: ' + (why || 'its chain does not replay (' + (e.code || e.name) + ')'));
  }
  const scratch = memorySink(), rt = makeTools(nb.B, null, { looks: scratch }), asked = new Map();
  for (const ex of Array.isArray(doc.results) ? doc.results : []) if (ex && typeof ex.key === 'string' && typeof ex.sha256 === 'string') asked.set(ex.key, ex);
  const fresh = new Map();
  let replayed = 0, same = 0, differ = [], skipped = 0;
  for (const rec of doc.log.events) {
    if (rec.event.action !== 'take_look') continue;
    const look = lookOfEvent(rec.event), key = keyOf(look), ex0 = asked.get(key);
    // The chain keeps a comparison's groups in canonical order, but the sentence reads A against B as it was asked, so the export carries the call.
    const own = ex0 && ex0.call && look.kind === 'compare' && ex0.call.topic === 'compare' ? ex0.call : null, call = own || lookToCall(nb.B, look);
    if (!call) { scratch.note(look); skipped++; continue; }
    let r;
    try { r = rt.find(t => t.name === 'eda').execute(call); } catch (e) { differ.push(key); continue; }
    if (look.kind === 'compare' && keyOf(r.look) !== key) { differ.push(key); continue; }
    const textOut = look.kind === 'declared' ? r.finding.text : r.text;
    replayed++;
    const ex = asked.get(key), digest = sha(textOut);
    if (ex) { if (ex.sha256 === digest) same++; else differ.push(key); }
    fresh.set(key, look.kind === 'declared'
      ? { kind: 'declared', title: look.id, text: textOut, grade: r.finding.grade, reason: r.finding.grade_reason, sha256: digest }
      : { kind: 'compare', title: look.kind, text: textOut, grade: null, q_text: r.q_text, m: r.family ? r.family.m : null, p_text: r.null_model ? r.null_model.p_text : null, sha256: digest, ...(own ? { call: own } : {}) });
  }
  if (differ.length) throw new Error(`the notebook is refused: ${differ.length} of ${replayed} looks replay to a different answer here`);
  await newSession(nb, doc.log);
  for (const [k, v] of fresh) nb.results.set(k, v);
  for (const n of Array.isArray(doc.notes) ? doc.notes : []) if (n && typeof n.key === 'string' && typeof n.note === 'string') nb.notes.set(n.key, n.note.slice(0, MAX_NOTE));
  return `imported ${state.event_count} events: ${replayed} looks replayed, ${same} answers identical to the export${skipped ? `, ${skipped} filtered row queries counted` : ''}`;
}

// ---- the panel
const gradeBadge = g => (g ? `<b class="g g${g}" title="grade ${g}">${g}</b>` : '<span class="dim" title="no grade">–</span>');
function who(B) { const o = ((B.eda || {}).classes || {}).order || []; return o.filter(c => (B.commits || []).some(x => x.c === c)); }
function groupFields(B, p, pair, def) {
  const ws = who(B), repos = B.repos || [];
  return `<fieldset><legend>${p === 'a' ? 'Group A' : 'Group B'}</legend>` +
    `<label>who <select name="${p}_who"><option value="">any</option>${ws.map(c => `<option value="${esc(c)}"${c === def ? ' selected' : ''}>${esc(classLabel(B, c))}</option>`).join('')}</select></label>` +
    `<label>repo <select name="${p}_repo"><option value="">any</option>${repos.map((r, i) => `<option value="${esc(r.name)}">${esc(r.visibility === 'private' ? 'private repo #' + i : r.name)}</option>`).join('')}</select></label>` +
    `<label>UTC hour <select name="${p}_hour"><option value="">any</option>${Array.from({ length: 24 }, (_, h) => `<option value="${h}">${h}</option>`).join('')}</select></label>` +
    `<label>weekday <select name="${p}_weekday"><option value="">any</option>${WEEKDAYS.map((w, i) => `<option value="${i}">${w}</option>`).join('')}</select></label></fieldset>`;
}
function readGroup(form, p) {
  const v = n => form.elements[p + '_' + n].value, g = {};
  if (v('who')) g.who = v('who');
  if (v('repo')) g.repo = v('repo');
  if (v('hour') !== '') g.hour = Number(v('hour'));
  if (v('weekday') !== '') g.weekday = v('weekday');
  return g;
}

function renderStatic(nb) {
  const B = nb.B, fs = findings(B), pair = (B.eda.classes || {}).pair || [];
  const rows = fs.map(f => `<tr data-id="${esc(f.id)}"><td class="mono">${esc(f.id)}</td><td>${gradeBadge(f.grade)}</td>` +
    `<td>${f.status === 'evaluated' ? esc(f.grade_reason) : 'not run'}</td><td class="mono">${f.status === 'evaluated' ? esc(f.q_text) : '–'}</td>` +
    `<td><button type="button" data-act="take" data-id="${esc(f.id)}">Take this look</button></td></tr>`).join('');
  nb.root.innerHTML =
    `<h2>Notebook</h2>` +
    `<p class="nbwhat">Take looks at the data and keep them. The more looks anyone takes, the easier it is to find something that is only chance, so the count stays beside every answer.</p>` +
    `<p class="nbstatus" data-nb="status" role="status" aria-live="polite"></p>` +
    `<div class="nbbar"><button type="button" data-act="recompute">Recompute the declared looks in this browser</button>` +
    `<button type="button" data-act="export">Export notebook</button><button type="button" data-act="clear" data-nb="clear">Start a new notebook</button></div>` +
    `<p class="nbmsg" data-nb="msg" role="status" aria-live="polite"></p>` +
    `<h3>The declared looks</h3><div class="tbl"><table><thead><tr><th scope="col">Look</th><th scope="col">Grade</th><th scope="col">Why</th><th scope="col">q</th><th scope="col"><span class="sr">Take</span></th></tr></thead><tbody>${rows}</tbody></table></div>` +
    `<h3>Compare two groups of commits</h3>` +
    `<form data-nb="form" class="nbform">${groupFields(B, 'a', pair, pair[0])}${groupFields(B, 'b', pair, pair[1])}` +
    `<fieldset><legend>Question</legend><label>measure <select name="measure">${MEASURES.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join('')}</select></label>` +
    `<label>commits <select name="fork">${FORKS.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join('')}</select></label>` +
    `<button type="submit">Compare</button></fieldset></form>` +
    `<p class="nbnote">A comparison is never graded: a grade needs the declared robustness checks. It is added to the count, so the next q is counted over one more look.</p>` +
    `<h3>Entries</h3><ol class="nbentries" data-nb="entries"></ol>` +
    `<details class="nbio"><summary>Export and import</summary><p class="nbnote">Export gives the chain and the answers; import replays them against this data and refuses anything that does not match.</p>` +
    `<textarea data-nb="io" rows="5" spellcheck="false" aria-label="Notebook export or text to import"></textarea>` +
    `<div class="nbbar"><button type="button" data-act="import">Import the text above</button></div></details>` +
    `<p class="nbfoot">${esc(NOT_PROVEN)}</p>`;
}

async function renderDynamic(nb) {
  const snap = await snapshot(nb), root = nb.root, q = n => root.querySelector(`[data-nb="${n}"]`);
  const taken = new Set(snap.looks.map(keyOf));
  const shown = snap.looks.slice(0, MAX_ENTRIES_SHOWN);
  q('entries').innerHTML = shown.map((look, n) => {
    const key = keyOf(look), res = nb.results.get(key), d = describe(nb.B, look);
    const own = res ? esc(res.text) : `${esc(d.text)} <span class="dim">(taken outside this panel; the answer is in the chat)</span>`;
    const badge = look.kind === 'declared' ? gradeBadge(res ? res.grade : (findings(nb.B).find(f => f.id === look.id) || {}).grade) : '<span class="chip">ungraded</span>';
    return `<li data-key="${esc(key)}"><div class="nbhead"><span class="chip">${n + 1}</span> <b>${esc(d.title)}</b> ${badge}</div><p class="nbtext">${own}</p>` +
      `<label class="nbnotel">Your note <span class="dim">(yours, unchecked, not part of the record)</span><textarea data-note="${esc(key)}" rows="2" maxlength="${MAX_NOTE}">${esc(nb.notes.get(key) || '')}</textarea></label></li>`;
  }).join('') + (snap.looks.length > shown.length ? `<li class="dim">${snap.looks.length - shown.length} more looks are in the log and not shown</li>` : '');
  root.querySelectorAll('button[data-act="take"]').forEach(b => {
    const done = taken.has(keyOf({ kind: 'declared', id: b.dataset.id }));
    b.textContent = done ? 'Taken' : 'Take this look'; b.setAttribute('aria-pressed', done ? 'true' : 'false');
  });
  const unrec = snap.unrecorded.length ? ` ${snap.unrecorded.length} ${snap.unrecorded.length === 1 ? 'look' : 'looks'} could not be recorded (${esc([...new Set(snap.unrecorded)].join(', '))}) and still count.` : '';
  const recordedNote = nb.mode === 'chain' ? ` ${snap.events} ${snap.events === 1 ? 'event' : 'events'} in the log, head ${esc(String(snap.head).slice(0, 12))}.` : ` Counted in memory only: ${esc(nb.reason)}.`;
  q('status').innerHTML = `<b>${snap.declared} declared looks, ${snap.inspected} taken. ${snap.asked} ${snap.asked === 1 ? 'look' : 'looks'} asked beyond them.</b> q on a comparison is counted over ${snap.declared + snap.asked}.${recordedNote}${unrec}`;
  const msg = q('msg');
  if (nb.recomputed && !nb.messageSet) msg.textContent = '';
  const rc = nb.recomputed;
  if (rc) {
    const paths = (rc.differences || []).slice(0, 8).map(d => (typeof d === 'string' ? d : d.path)).join(', ');
    msg.innerHTML = `<span class="chip ${rc.match ? 'ok' : 'bad'}">${rc.match ? 'identical' : 'differs'}</span> ${esc(rc.summary)}` +
      (rc.match ? ` <span class="mono dim">sha256 ${esc(String(rc.recomputed_sha256).slice(0, 16))}</span>` : (paths ? ` <span class="mono">${esc(paths)}</span>` : ''));
  }
  return snap;
}

function say(nb, text, bad) {
  nb.recomputed = null;
  const msg = nb.root.querySelector('[data-nb="msg"]');
  msg.className = 'nbmsg' + (bad ? ' bad' : '');
  msg.textContent = text;
}

function bind(nb) {
  const run = fn => { nb.q = nb.q.then(fn).catch(e => say(nb, e && e.message ? e.message : String(e), true)); return nb.q; };
  nb.act = {
    take: id => run(async () => { say(nb, await takeDeclared(nb, id)); await renderDynamic(nb); }),
    compare: args => run(async () => { say(nb, await takeCompare(nb, args)); await renderDynamic(nb); }),
    recompute: () => run(async () => { const s = await recompute(nb); await renderDynamic(nb); if (!nb.recomputed) say(nb, s); }),
    refresh: () => run(async () => { await renderDynamic(nb); }),
    export: () => run(async () => {
      if (nb.mode !== 'chain') throw new Error('exporting needs the hash chain, which this context does not have');
      const text = JSON.stringify(exportDoc(nb, await snapshot(nb)), null, 1);
      nb.root.querySelector('[data-nb="io"]').value = text;
      nb.root.querySelector('details.nbio').open = true;
      try { // a download where the host allows one; the text above is the fallback
        const doc = nb.root.ownerDocument, a = doc.createElement('a');
        a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' })); a.download = 'x3dray-notebook.json';
        doc.body.appendChild(a); a.click(); a.remove();
      } catch (e) { /* the text is in the box */ }
      say(nb, 'exported: the notebook is in the box below (and downloaded where this page may)');
    }),
    import: () => run(async () => {
      const text = nb.root.querySelector('[data-nb="io"]').value;
      say(nb, await importText(nb, text)); await renderDynamic(nb);
    }),
    clear: () => run(async () => {
      if (!nb.armed) { nb.armed = true; nb.root.querySelector('[data-nb="clear"]').textContent = 'Press again to start over'; say(nb, 'this empties the notebook (the page data is untouched)'); return; }
      nb.armed = false; nb.root.querySelector('[data-nb="clear"]').textContent = 'Start a new notebook';
      await newSession(nb, null); say(nb, 'a new notebook: no looks taken yet'); await renderDynamic(nb);
    }),
  };
  nb.root.addEventListener('click', ev => {
    const b = ev.target.closest && ev.target.closest('button[data-act]');
    if (!b || !nb.root.contains(b)) return;
    const act = b.dataset.act;
    if (act !== 'clear' && nb.armed) { nb.armed = false; nb.root.querySelector('[data-nb="clear"]').textContent = 'Start a new notebook'; }
    if (act === 'take') nb.act.take(b.dataset.id); else nb.act[act]();
  });
  nb.root.addEventListener('submit', ev => {
    const form = ev.target.closest && ev.target.closest('form[data-nb="form"]');
    if (!form) return;
    ev.preventDefault();
    nb.act.compare({ a: readGroup(form, 'a'), b: readGroup(form, 'b'), measure: form.elements.measure.value, fork: form.elements.fork.value });
  });
  nb.root.addEventListener('input', ev => {
    const k = ev.target && ev.target.dataset && ev.target.dataset.note;
    if (k !== undefined) nb.notes.set(k, String(ev.target.value).slice(0, MAX_NOTE));
  });
}

// Starts the notebook in `root`. `scene` is the page's scene API (or null); `onTools(tools)` receives the one tool set the page should share with
// Ask Claude, now and again after every new notebook, so the looks Claude takes are counted and listed here too.
export async function startNotebook(B, { root, scene = null, onTools = () => {} } = {}) {
  const nb = { B, root, scene, onTools, mode: 'none', reason: '', q: Promise.resolve(), armed: false, recomputed: null, results: new Map(), notes: new Map() };
  await initChain(nb);
  if (nb.mode === 'none') { nb.tools = makeTools(B, scene); nb.onTools(nb.tools); if (root) root.hidden = true; return { mode: 'none', reason: nb.reason, tools: nb.tools, refresh: async () => {}, settled: async () => {} }; }
  await newSession(nb, null);
  renderStatic(nb); bind(nb);
  await renderDynamic(nb);
  return {
    get mode() { return nb.mode; }, get reason() { return nb.reason; }, get tools() { return nb.tools; }, get sink() { return nb.sink; },
    fixture_digest: nb.fix, analysis_digest: nb.ana, looks_digest: nb.ctx ? nb.ctx.digest : null,
    refresh: () => nb.act.refresh(), take: id => nb.act.take(id), compare: a => nb.act.compare(a), recompute: () => nb.act.recompute(),
    snapshot: () => snapshot(nb), settled: () => nb.q,
  };
}
