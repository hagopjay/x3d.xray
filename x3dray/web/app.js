// x3d-ray page: loads the bundle and the X3D scene (X_ITE), draws the readouts, runs the tour, and wires Claude in
// through the Artifact's sample capability, with the same tools the MCP server serves plus four that move the scene.
import { makeTools, digest, matchCommits, resolveRepo, sessionId, kindsOf, splitFile, CLASS_NAMES, kindsName } from './tools.js';

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = n => (n === null || n === undefined || n === '') ? '–' : Number(n).toLocaleString('en-US');
const day = t => new Date(t * 1000).toISOString().slice(0, 10);
const hm = t => new Date(t * 1000).toISOString().slice(11, 16);
const X = window.xray = { ready: false, failed: null, log: [], B: null, L: null, browser: null, mode: 'who', lit: null, xray: false, notes: 0, view: null };
const log = s => X.log.push((performance.now() / 1000).toFixed(2) + ' s ' + s);
const reduced = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

const CLS = { you: '#f0b443', ai: '#e8795d', bot: '#a4bf7a', other: '#a68fdc', upstream: '#7d8b97' };
const WHO = { you: 'HagopJay', ai: 'Claude', bot: 'bots', other: 'other', upstream: 'unmapped' };
const REPO_HUES = ['#5ac8e0', '#f0b443', '#e8795d', '#a4bf7a', '#a68fdc', '#e0a3d8', '#6fb7e0', '#f29e6d', '#7fd1c7', '#d9d2c3', '#8b98d6', '#c9e07a'];
const SIGNAL_VIEW = { family_span: 'helix', who_commits: 'claude', busiest_month: 'burst', busiest_day: 'peak', births: 'helix', ai_share_by_repo: 'claude',
  weekly_mix: 'claude', bot_identity: 'helix', rhythm: 'rhythm', pairing: 'claude', largest_commits: 'helix', sessions: 'claude', tests: 'city',
  x3d_assets: 'city', hotspots: 'hotspots', branches: 'helix', pins: 'fork', ties_machine: 'ties', ties_curated: 'ties', idle_forks: 'ties', second_account: 'ties',
  concepts_files: 'concepts', concepts_rules: 'concepts', bridges_sessions: 'bridges', bridges_files: 'bridges' };
const SIGNAL_HI = { bot_identity: { who: 'bot' }, pairing: { trailer: true, who: 'you' }, who_commits: { who: 'ai' }, sessions: { who: 'ai' } };

function hex3(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255]; }
function sfc(rgb, k = 1) { return new X3D.SFColor(Math.min(1, rgb[0] * k), Math.min(1, rgb[1] * k), Math.min(1, rgb[2] * k)); }
function lerp(a, b, u) { return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u]; }

// ------------------------------------------------------------------ boot
async function getJSON(name, optional) {
  try { const r = await fetch(name, { cache: 'no-store' }); if (!r.ok) throw new Error(name + ' HTTP ' + r.status); return await r.json(); }
  catch (e) { if (optional) return null; throw e; }
}

async function boot() {
  let B;
  try { B = await getJSON('bundle.json'); } catch (e) { $('status').textContent = 'The data did not load: ' + e.message; $('status').hidden = false; return; }
  const [gates, receipt] = await Promise.all([getJSON('gates.json', true), getJSON('receipt.json', true)]);
  B.gates = gates; B.receipt = receipt;
  X.B = B; X.L = B.layout;
  renderHeader(B); renderKpis(B); renderSignals(B); renderRepos(B); renderCharts(B); renderTour(B); renderViews(B);
  initAsk(B);
  startScene();
}

// ------------------------------------------------------------------ header and readouts
function renderHeader(B) {
  $('subtitle').textContent = B.spec.subtitle || '';
  const t = B.self.tool;
  const g = B.gates;
  const gs = g ? (g.ok ? 'gates pass' : 'gates FAIL') : 'gates not run';
  $('build').innerHTML = `<span class="chip">x3d.xray ${esc(t.version)}</span><span class="chip mono">head ${esc(t.git.head || '?')}${t.git.uncommitted_changes ? '+' : ''}</span>` +
    `<span class="chip mono">code ${esc(t.code_digest)}</span><span class="chip ${g ? (g.ok ? 'ok' : 'bad') : ''}">${esc(gs)}</span>` +
    (B.receipt ? `<span class="chip mono" title="sha256 over every input, module, output and gate result">receipt ${esc(B.receipt.digest.slice(0, 12))}</span>` : '') +
    `<span class="chip">harvested ${esc(String(B.meta.harvested_at).slice(0, 16).replace('T', ' '))} UTC</span>`;
}

function sig(B, id) { const s = B.signals.find(x => x.id === id); return s ? s.values : {}; }

function renderKpis(B) {
  const fs = sig(B, 'family_span'), wc = sig(B, 'who_commits'), pr = sig(B, 'pairing'), tm = sig(B, 'ties_machine'), tc = sig(B, 'ties_curated'),
    pins = sig(B, 'pins'), x3 = sig(B, 'x3d_assets'), te = sig(B, 'tests'), bm = sig(B, 'busiest_month');
  const k = [
    ['commits', fmt(fs.commits), `own work, ${fs.repos} repos`], ['active days', fmt(fs.active_days), `${String(fs.first).slice(0, 7)} to ${String(fs.last).slice(0, 7)}`],
    ['Claude', (wc.shares || {}).ai + '%', `${fmt((wc.counts || {}).ai)} commits`, 'ai'], ['HagopJay', (wc.shares || {}).you + '%', `${fmt((wc.counts || {}).you)} commits`, 'you'],
    ['co-authored', fmt(pr.commits), 'his commits with a Claude trailer'], ['busiest month', (bm.share || 0) + '%', bm.month_name || ''],
    ['machine ties', fmt(tm.pairs), `${(tm.grades || {}).C || 0} C · ${(tm.grades || {}).L || 0} L · ${(tm.grades || {}).S || 0} S`],
    ['graded ties', fmt(tc.ties), `${(tc.grades || {}).C || 0} C · ${(tc.grades || {}).L || 0} L · ${(tc.grades || {}).S || 0} S`],
    ['pins', fmt(pins.total), `${pins.top_target_share || 0}% at ${pins.top_target || ''}`], ['X3D files', fmt(x3.total), 'at the repo tips'],
    ['fork tests', fmt((te.tests || {}).x3d_mcp), 'test functions in x3d_mcp'], ['sessions', fmt(B.self.claude_in_the_data.sessions), `${B.self.claude_in_the_data.multi_repo_sessions} span repos`],
  ];
  $('kpis').innerHTML = k.map(([l, v, s, c]) => `<div class="kpi${c ? ' k-' + c : ''}"><span class="kl">${esc(l)}</span><span class="kv">${esc(v)}</span><span class="ks">${esc(s)}</span></div>`).join('');
}

function renderSignals(B) {
  $('signals').innerHTML = B.signals.map(s => `<li><button class="sig" data-id="${esc(s.id)}"><span class="kind k-${esc(s.kind)}">${esc(s.kind)}</span>` +
    `<span class="sigt">${esc(s.text)}</span></button></li>`).join('');
  $('signals').addEventListener('click', e => {
    const b = e.target.closest('.sig'); if (!b) return;
    const id = b.dataset.id;
    const stop = B.spec.tour.findIndex(t => (t.cites || []).includes(id));
    if (stop >= 0) { goStop(stop); return; }
    const v = SIGNAL_VIEW[id] || 'overview';
    location.hash = v;
    if (SIGNAL_HI[id]) setTimeout(() => sceneApi.highlight({ ...SIGNAL_HI[id], xray: true }), 300);
  });
}

function renderRepos(B) {
  const rows = B.repos.slice().sort((a, b) => b.own_commits - a.own_commits || b.files - a.files);
  const body = rows.map(r => {
    const bc = r.by_class || {}, tot = Object.values(bc).reduce((s, v) => s + v, 0) || 1;
    const bar = ['you', 'ai', 'bot', 'other', 'upstream'].map(c => bc[c] ? `<i style="flex:${bc[c]};background:var(--c-${c})" title="${WHO[c]} ${bc[c]}"></i>` : '').join('');
    const link = r.github ? `<a href="${esc(r.github)}" target="_blank" rel="noopener">${esc(r.name)}</a>` : esc(r.name);
    return `<tr data-repo="${esc(r.name)}"><th scope="row">${link}${r.name === 'x3d.xray' ? ' <span class="me">this tool</span>' : ''}${r.visibility === 'private' ? ' <span class="pv">private</span>' : ''}</th>` +
      `<td class="n">${fmt(r.own_commits)}</td><td class="bar"><span class="stack">${bar}</span></td><td class="n">${fmt(r.files)}</td><td class="n">${fmt(r.tests)}</td><td class="n">${fmt(r.x3d_files)}</td></tr>`;
  }).join('');
  $('repos').innerHTML = `<table><thead><tr><th scope="col">repo</th><th scope="col" class="n">own</th><th scope="col">who wrote it</th><th scope="col" class="n">files</th><th scope="col" class="n">tests</th><th scope="col" class="n">X3D</th></tr></thead><tbody>${body}</tbody></table>`;
  $('repos').addEventListener('click', e => {
    if (e.target.closest('a')) return;
    const tr = e.target.closest('tr[data-repo]'); if (!tr) return;
    sceneApi.highlight({ repo: tr.dataset.repo, xray: true });
    sceneApi.show({ repo: tr.dataset.repo, place: 'ties' });
  });
}

function renderCharts(B) {
  // commits per active day, stacked by who
  const days = B.layout.days, W = 820, H = 150, pad = 26, bw = (W - pad * 2) / days.length;
  const per = {};
  B.commits.forEach(c => { const d = day(c.t); per[d] = per[d] || { you: 0, ai: 0, bot: 0, other: 0, upstream: 0 }; per[d][c.c]++; });
  const max = Math.max(...days.map(d => d.n));
  const sy = v => (H - 34) * v / max;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Commits per active day, stacked by who"><g class="grid">`;
  [0.25, 0.5, 0.75, 1].forEach(u => { const y = H - 22 - sy(max * u); s += `<line x1="${pad}" x2="${W - pad}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"/><text x="${pad - 4}" y="${(y + 3).toFixed(1)}" text-anchor="end">${Math.round(max * u)}</text>`; });
  s += '</g>';
  let lastM = '';
  days.forEach((d, i) => {
    const x = pad + i * bw; let y = H - 22;
    ['you', 'ai', 'bot', 'other', 'upstream'].forEach(c => { const v = (per[d.day] || {})[c] || 0; if (!v) return; const h = sy(v); y -= h; s += `<rect x="${(x + 1).toFixed(1)}" y="${y.toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${h.toFixed(1)}" fill="var(--c-${c})"/>`; });
    s += `<rect class="hit" data-day="${d.day}" x="${x.toFixed(1)}" y="0" width="${bw.toFixed(1)}" height="${H - 22}" fill="transparent"><title>${d.day}: ${d.n} commits</title></rect>`;
    const m = d.day.slice(0, 7);
    if (m !== lastM) { s += `<line class="mline" x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${H - 22}" y2="${H - 6}"/><text class="mlab" x="${(x + 2).toFixed(1)}" y="${H - 8}">${new Date(d.day + 'T00:00:00Z').toLocaleString('en-US', { month: 'short', timeZone: 'UTC' })}${m.slice(2, 4)}</text>`; lastM = m; }
    if (d.n === max) s += `<text class="peak" x="${(x + bw / 2).toFixed(1)}" y="${(H - 26 - sy(d.n)).toFixed(1)}" text-anchor="middle">${d.n}</text>`;
  });
  s += '</svg>';
  $('chartDays').innerHTML = s;
  $('chartDays').addEventListener('click', e => { const r = e.target.closest('rect.hit'); if (!r) return; sceneApi.highlight({ day: r.dataset.day, xray: true }); sceneApi.show({ day: r.dataset.day }); });
  // hour of day, HagopJay up and Claude down
  const hrs = sig(B, 'rhythm').hours || {}, W2 = 420, H2 = 150, mid = 76, bw2 = (W2 - 40) / 24;
  const m2 = Math.max(...(hrs.you || [1]), ...(hrs.ai || [1]), 1);
  let t = `<svg viewBox="0 0 ${W2} ${H2}" role="img" aria-label="Commits by hour of day (UTC): HagopJay above the line, Claude below"><line class="axis" x1="20" x2="${W2 - 20}" y1="${mid}" y2="${mid}"/>`;
  for (let h = 0; h < 24; h++) {
    const x = 20 + h * bw2, u = (hrs.you || [])[h] || 0, a = (hrs.ai || [])[h] || 0, hu = 58 * u / m2, ha = 58 * a / m2;
    if (u) t += `<rect x="${(x + 1).toFixed(1)}" y="${(mid - hu).toFixed(1)}" width="${(bw2 - 2).toFixed(1)}" height="${hu.toFixed(1)}" fill="var(--c-you)"><title>${String(h).padStart(2, '0')}:00 HagopJay ${u}</title></rect>`;
    if (a) t += `<rect x="${(x + 1).toFixed(1)}" y="${mid}" width="${(bw2 - 2).toFixed(1)}" height="${ha.toFixed(1)}" fill="var(--c-ai)"><title>${String(h).padStart(2, '0')}:00 Claude ${a}</title></rect>`;
    if (h % 3 === 0) t += `<text x="${(x + bw2 / 2).toFixed(1)}" y="${H2 - 4}" text-anchor="middle">${String(h).padStart(2, '0')}</text>`;
  }
  t += `<text class="lab" x="22" y="12">HagopJay</text><text class="lab" x="22" y="${H2 - 18}">Claude</text></svg>`;
  $('chartHours').innerHTML = t;
}

// ------------------------------------------------------------------ views and tour
function renderViews(B) {
  $('views').innerHTML = B.spec.views.map(v => `<a class="vbtn" href="#${esc(v.id)}" data-view="${esc(v.id)}">${esc(v.label)}</a>`).join('');
}

let stopIdx = -1, playTimer = 0;
function renderTour(B) {
  $('tourN').textContent = `0/${B.spec.tour.length}`;
  $('tourPrev').addEventListener('click', () => goStop(stopIdx <= 0 ? B.spec.tour.length - 1 : stopIdx - 1));
  $('tourNext').addEventListener('click', () => goStop((stopIdx + 1) % B.spec.tour.length));
  $('tourPlay').addEventListener('click', () => {
    if (playTimer) { clearInterval(playTimer); playTimer = 0; $('tourPlay').textContent = 'Play tour'; $('tourPlay').setAttribute('aria-pressed', 'false'); return; }
    goStop((stopIdx + 1) % B.spec.tour.length);
    playTimer = setInterval(() => goStop((stopIdx + 1) % B.spec.tour.length), 9000);
    $('tourPlay').textContent = 'Pause'; $('tourPlay').setAttribute('aria-pressed', 'true');
  });
}

function goStop(i) {
  const B = X.B, st = B.spec.tour[i]; if (!st) return;
  stopIdx = i;
  $('tourN').textContent = `${i + 1}/${B.spec.tour.length}`;
  applyAction(st.action || {});
  bindView(st.view, { title: st.title, caption: st.caption, cites: st.cites });
  history.replaceState(null, '', '#' + st.view);
}

function applyAction(a) {
  if (a.clear) sceneApi.highlight({ clear: true });
  if (a.color_by) sceneApi.colorBy(a.color_by.mode || 'who');
  if (a.highlight) sceneApi.highlight({ ...a.highlight, xray: !!(a.xray && a.xray.on) });
  else if (a.xray) setXray(!!a.xray.on);
}

function captionFor(id) { const v = X.B.spec.views.find(x => x.id === id); return v ? v.caption : ''; }

function bindView(id, cap) {
  const B = X.B; if (!B.layout.views[id]) return false;
  X.view = id;
  document.querySelectorAll('.vbtn').forEach(b => b.setAttribute('aria-current', String(b.dataset.view === id)));
  const label = (B.spec.views.find(v => v.id === id) || {}).label || id;
  $('capTitle').textContent = cap && cap.title ? cap.title : label;
  $('capText').textContent = cap && cap.caption ? cap.caption : captionFor(id);
  const claim = (B.spec.views.find(v => v.id === id) || {}).claim;
  $('capClaim').hidden = !claim;
  if (claim) {
    $('capSays').textContent = claim.says; $('capSays').dataset.basis = claim.basis;
    $('capSays').title = 'backed by ' + claim.backs.join(', ');
    $('capClaimText').textContent = claim.text;
  }
  if (!X.ready) return false;
  const vp = node(B.layout.views[id].def); if (!vp) return false;
  fitView(id, vp);
  vp.set_bind = true; log('bind ' + id); return true;
}

// A wide view keeps its width in frame on a narrow stage: X3D applies fieldOfView to the stage's smaller side, so widen it as needed.
function fitView(id, vp) {
  const v = X.B.layout.views[id], el = $('scene');
  if (!v.halfw || !el.clientWidth || !el.clientHeight) return;
  const d = Math.hypot(v.position[0] - v.center[0], v.position[1] - v.center[1], v.position[2] - v.center[2]);
  const a = el.clientWidth / el.clientHeight, need = v.halfw / d;
  const fov = a >= 1 ? 2 * Math.atan(need / a) : 2 * Math.atan(need);
  vp.fieldOfView = Math.min(2.2, Math.max(v.fov, fov));
}
window.addEventListener('resize', () => { if (X.ready && X.view && X.B.layout.views[X.view]) { const vp = node(X.B.layout.views[X.view].def); if (vp) fitView(X.view, vp); } });

function onHash() {
  const id = decodeURIComponent(location.hash.slice(1));
  if (id && X.B && X.B.layout.views[id]) bindView(id);
}
window.addEventListener('hashchange', onHash);

// ------------------------------------------------------------------ the scene
function node(def) { try { return X.browser.currentScene.getNamedNode(def); } catch (e) { return null; } }

function loadXite() {
  if (window.X3D) return Promise.resolve();
  const src = (document.querySelector('meta[name="loom:xite"]') || {}).content || 'x_ite/x_ite.min.js';
  return new Promise((res, rej) => { const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = () => rej(new Error(src + ' did not load')); document.head.appendChild(s); });
}

function cssFallback() {
  const el = $('scene'), root = el.shadowRoot, b = root && root.querySelector('.x_ite-private-browser');
  if (!b || b.style.display !== 'none') return;
  const st = document.createElement('style');
  st.textContent = ':host{display:block;overflow:hidden}.x_ite-private-browser{height:100%;outline:none;overflow:hidden;position:relative}.x_ite-private-surface{height:100%}.x_ite-private-canvas{height:100%;width:100%}.x_ite-private-splash-screen{display:none}';
  root.appendChild(st); b.style.display = ''; log('x_ite.css fallback');
}

function status(s, err) { const el = $('status'); el.textContent = s || ''; el.hidden = !s; el.classList.toggle('err', !!err); }

function startScene() {
  status('Loading the X3D scene…');
  loadXite().then(() => new Promise((res, rej) => {
    const el = $('scene'), t0 = performance.now();
    (function wait() { if (el.browser) return res(el.browser); if (performance.now() - t0 > 20000) return rej(new Error('X_ITE did not start')); setTimeout(wait, 50); })();
  })).then(browser => {
    X.browser = browser; setTimeout(cssFallback, 4000);
    try { browser.setBrowserOption('PrimitiveQuality', 'MEDIUM'); } catch (e) { /* option names vary */ }
    return browser.loadURL(new X3D.MFString('xray.x3d'));
  }).then(() => {
    X.ready = true;
    if (reduced) { const nav = node('Nav'); if (nav) nav.transitionType = new X3D.MFString('TELEPORT'); }
    wirePicking();
    const id = decodeURIComponent(location.hash.slice(1));
    bindView(X.B.layout.views[id] ? id : 'overview');
    status(''); document.body.dataset.scene = 'ready'; log('scene ready');
  }).catch(e => { X.failed = String(e && e.message || e); status('The X3D scene did not load (' + X.failed + '). The readouts and Ask still work.', true); document.body.dataset.scene = 'failed'; });
}

// picking: one TouchSensor per view; the nearest record in layout.json wins
const mouse = { x: 0, y: 0 };
function nearest(points, p, get) {
  let best = null, bd = Infinity;
  points.forEach((q, i) => { const a = get(q); const d = (a[0] - p.x) ** 2 + (a[1] - p.y) ** 2 + (a[2] - p.z) ** 2; if (d < bd) { bd = d; best = i; } });
  return best === null ? null : { i: best, d: Math.sqrt(bd) };
}
function pick(view, p) {
  const L = X.L;
  if (view === 'helix') { const n = nearest(L.commits, p, q => q); return n && n.d < 0.8 ? { type: 'commit', i: n.i } : null; }
  if (view === 'ties') {
    const nodes = Object.entries(L.nodes), nn = nearest(nodes, p, q => q[1].p);
    const ties = L.ties.curated.concat(L.ties.machine), nt = nearest(ties, p, q => q.p);
    if (nn && (!nt || nn.d - nodes[nn.i][1].r < nt.d)) return { type: 'node', name: nodes[nn.i][0] };
    return nt && nt.d < 1.4 ? { type: 'tie', tie: ties[nt.i] } : null;
  }
  if (view === 'city') { const n = nearest(L.city.buildings, p, q => q.p); return n && n.d < 3 ? { type: 'file', b: L.city.buildings[n.i] } : null; }
  if (view === 'self') { const st = Object.entries(L.self.stages), n = nearest(st, p, q => q[1]); return n ? { type: 'stage', name: st[n.i][0] } : null; }
  if (view === 'concepts' && L.concepts) {
    const items = L.concepts.files.map(c => ({ kind: 'files', ...c })).concat(L.concepts.repos.map(c => ({ kind: 'repos', ...c })));
    const n = nearest(items, p, q => q.p); return n && n.d < items[n.i].r + 1.2 ? { type: 'concept', of: items[n.i].kind, i: items[n.i].i } : null;
  }
  if (view === 'bridges' && L.bridges) {
    const items = Object.entries(L.bridges.repos).map(([k, v]) => ({ type: 'brepo', name: k, ...v })).concat(Object.entries(L.bridges.sessions).map(([k, v]) => ({ type: 'session', name: k, ...v })),
      L.bridges.files.map(b => ({ type: 'bfile', b, p: b.p, r: 0.3 })));
    const n = nearest(items, p, q => q.p); if (!n || n.d > items[n.i].r + 1.6) return null;
    const it = items[n.i]; return it.type === 'bfile' ? { type: 'bfile', b: it.b } : { type: it.type, name: it.name };
  }
  return null;
}
function wirePicking() {
  const tag = $('tag');
  [['TS_helix', 'helix'], ['TS_ties', 'ties'], ['TS_city', 'city'], ['TS_self', 'self'], ['TS_concepts', 'concepts'], ['TS_bridges', 'bridges']].forEach(([def, view]) => {
    const ts = node(def); if (!ts) return;
    let hp = null, over = false, raf = 0;
    ts.addFieldCallback('xr', 'hitPoint_changed', v => {
      hp = { x: v.x, y: v.y, z: v.z };
      if (!over || raf) return;
      raf = requestAnimationFrame(() => { raf = 0; const it = hp && pick(view, hp); if (!it) { tag.hidden = true; return; } tag.textContent = titleOf(it); tag.hidden = false; tag.style.left = mouse.x + 'px'; tag.style.top = mouse.y + 'px'; });
    });
    ts.addFieldCallback('xr', 'isOver', v => { over = v; if (!v) tag.hidden = true; });
    ts.addFieldCallback('xr', 'touchTime', () => { const it = hp && pick(view, hp); if (it) showCard(it); });
  });
  $('stage').addEventListener('pointermove', e => { const r = $('stage').getBoundingClientRect(); mouse.x = e.clientX - r.left + 14; mouse.y = e.clientY - r.top + 10; });
}

function titleOf(it) {
  const B = X.B;
  if (it.type === 'commit') { const c = B.commits[it.i]; return `${c.r} · ${day(c.t)} ${hm(c.t)} · ${WHO[c.c]}${c.sub ? ' · ' + c.sub.slice(0, 60) : ''}`; }
  if (it.type === 'node') return it.name;
  if (it.type === 'tie') return (it.tie.id ? it.tie.id + ' ' : '') + it.tie.from + ' → ' + it.tie.to + ' · ' + it.tie.grade;
  if (it.type === 'file') return it.b.repo + ' · ' + it.b.path;
  if (it.type === 'stage') return 'stage: ' + it.name;
  if (it.type === 'concept') { const c = conceptOf(it); return c ? `${kindsName(c.intent, it.of === 'repos' ? 'every repo' : 'every file')} · ${c.support} ${it.of}` : ''; }
  if (it.type === 'brepo') return it.name + ' · session graph';
  if (it.type === 'session') return 'Claude session ' + it.name;
  if (it.type === 'bfile') return `${it.b.repo} · ${it.b.variant} · ${it.b.file}`;
  return '';
}
function conceptOf(it) { const C = X.B.concepts; if (!C) return null; return (it.of === 'repos' ? C.repos_by_author_class : C.files_by_author_class).concepts[it.i]; }

function ghFile(repo, path) { const r = X.B.repos.find(x => x.name === repo); return r && r.github ? r.github + '/blob/HEAD/' + path.split('/').map(encodeURIComponent).join('/') : null; }
function ghCommit(repo, sha) { const r = X.B.repos.find(x => x.name === repo); return r && r.github && sha ? r.github + '/commit/' + sha : null; }

function showCard(it) {
  const B = X.B; let h = '', ask = '';
  if (it.type === 'commit') {
    const c = B.commits[it.i], gh = ghCommit(c.r, c.h);
    h = `<p class="ck">commit · ${esc(c.r)}</p><h3>${esc(c.sub || '(private repo: no subject)')}</h3><dl>` +
      `<dt>when</dt><dd>${day(c.t)} ${hm(c.t)} UTC</dd><dt>who</dt><dd>${esc(c.w)} (${esc(WHO[c.c])})</dd><dt>size</dt><dd>+${fmt(c.a)} −${fmt(c.d)} in ${fmt(c.f)} files</dd>` +
      (c.h ? `<dt>sha</dt><dd class="mono">${gh ? `<a href="${esc(gh)}" target="_blank" rel="noopener">${esc(c.h)}</a>` : esc(c.h)}</dd>` : '') +
      (c.tr ? '<dt>trailer</dt><dd>Claude co-author</dd>' : '') + (c.s && c.s.length ? `<dt>session</dt><dd class="mono">${esc(c.s.join(' '))}</dd>` : '') +
      (!c.b ? '<dt>branch</dt><dd>side branch only</dd>' : '') + '</dl>';
    ask = `Tell me about the ${c.r} commit ${c.h || 'at ' + day(c.t) + ' ' + hm(c.t)}: what it changed and what came before and after it.`;
  } else if (it.type === 'node') {
    const r = B.repos.find(x => x.name === it.name);
    const tie = B.ties.machine.filter(t => t.from === it.name || t.to === it.name).length + B.ties.curated.filter(t => t.from === it.name || t.to === it.name).length;
    h = `<p class="ck">${r ? esc(r.kind + ' · ' + r.visibility) : 'node'}</p><h3>${r && r.github ? `<a href="${esc(r.github)}" target="_blank" rel="noopener">${esc(it.name)}</a>` : esc(it.name)}</h3>` +
      (r ? `<p>${esc(r.role || '')}</p><dl><dt>own commits</dt><dd>${fmt(r.own_commits)} (${Object.entries(r.by_class || {}).map(([k, v]) => WHO[k] + ' ' + v).join(', ')})</dd>` +
        `<dt>span</dt><dd>${esc(r.first || '–')} to ${esc(r.last || '–')}</dd><dt>tip</dt><dd>${fmt(r.files)} files, ${fmt(r.lines_at_tip)} lines, ${fmt(r.tests)} tests</dd>` +
        `<dt>ties</dt><dd>${tie}</dd></dl>` : `<p>${tie} ties in the data.</p>`);
    ask = `What ties ${it.name} to the rest of the family? Show the strongest evidence.`;
  } else if (it.type === 'tie') {
    const t = it.tie.id ? B.ties.curated.find(x => x.id === it.tie.id) : B.ties.machine.find(x => x.from === it.tie.from && x.to === it.tie.to);
    h = `<p class="ck">${it.tie.id ? 'graded by the masterclass' : 'found by the machine'} · <b class="g g${esc(t.grade)}">${esc(t.grade)}</b></p><h3>${esc(t.from)} → ${esc(t.to)}</h3>` +
      (t.what ? `<p>${esc(t.what)}</p>` : `<p>${esc(t.reason)}</p><p class="mono small">${esc(Object.entries(t.kinds).map(([k, v]) => k + ' ' + v).join(' · '))}</p>`) +
      ((t.evidence || []).slice(0, 3).map(e => `<p class="ev mono">${esc(typeof e === 'string' ? e : (e.path || e.kind) + (e.line ? ':' + e.line : '') + (e.text ? '  ' + e.text : ''))}</p>`).join(''));
    ask = `Explain the tie ${it.tie.id || t.from + ' → ' + t.to} and how sure we can be of it.`;
  } else if (it.type === 'file') {
    const b = it.b, gh = ghFile(b.repo, b.path);
    h = `<p class="ck">file · ${esc(b.repo)} · ${esc(b.kind)}</p><h3 class="mono">${gh ? `<a href="${esc(gh)}" target="_blank" rel="noopener">${esc(b.path)}</a>` : esc(b.path)}</h3><dl><dt>lines</dt><dd>${fmt(b.lines)}</dd><dt>churn</dt><dd>${fmt(b.churn)}</dd></dl>`;
    ask = `Why was ${b.repo}:${b.path} rewritten so much? Is it hand-written or vendored?`;
  } else if (it.type === 'stage') {
    const p = B.self.pipeline.find(x => x.stage === it.name);
    h = `<p class="ck">x3d.xray stage · ${p.model ? 'AI takes part' : 'no model'}</p><h3>${esc(p.stage)}</h3><p class="mono small">${esc(p.module)} · ${fmt(p.lines)} lines · ${esc(Object.values(p.sha256 || {}).join(' '))}</p><p>${esc(p.does)}</p>` +
      (p.model ? `<p class="ai">${esc(p.model)}</p>` : '');
    ask = `How does the ${p.stage} stage of x3d.xray work, and what checks it?`;
  } else if (it.type === 'concept') {
    const c = conceptOf(it), C = B.concepts, name = kindsName(c.intent, it.of === 'repos' ? 'every repo' : 'every file');
    if (it.of === 'files') {
      h = `<p class="ck">concept · files by who touched them</p><h3>${esc(name)}</h3><dl><dt>files</dt><dd>${fmt(c.support)} touched by at least these kinds</dd>` +
        `<dt>only these</dt><dd>${fmt(c.exact)} touched by exactly these kinds</dd><dt>repos</dt><dd>${esc(Object.entries(c.by_repo).slice(0, 5).map(([k, v]) => k + ' ' + v).join(', '))}</dd></dl>` +
        c.top_files.slice(0, 4).map(t => `<p class="ev mono">${esc(t.id)} · ${t.commits} commit${t.commits === 1 ? '' : 's'}</p>`).join('') +
        `<p class="small">Set aside and counted: ${esc(Object.entries(C.excluded).map(([k, v]) => v + ' ' + k.split(' (')[0]).join(', '))}.</p>`;
      ask = c.intent.length ? `Which files did ${name} touch, and what were those commits about?` : 'What does the concept lattice say about who touched which files?';
    } else {
      h = `<p class="ck">concept · repos by who has committed</p><h3>${esc(name)}</h3><dl><dt>repos</dt><dd>${esc(c.repos.join(', '))}</dd>` +
        `<dt>exactly these</dt><dd>${esc(c.exact.join(', ') || 'none')}</dd></dl>`;
      ask = `Which repos have had commits from ${name}, and how many from each?`;
    }
  } else if (it.type === 'brepo' || it.type === 'session') {
    const S = B.bridges.sessions;
    if (it.type === 'session') {
      const rec = S.sessions[it.name], pp = Object.entries(S.passage_points).filter(([, v]) => v.includes('session:' + it.name)).map(([k]) => k);
      h = `<p class="ck">Claude session · ${Object.keys(rec.repos).length > 1 ? 'spans repos' : 'one repo'}</p><h3 class="mono">${esc(it.name)}</h3><dl>` +
        `<dt>commits</dt><dd>${esc(Object.entries(rec.repos).map(([k, v]) => k + ' ' + v).join(', '))}</dd>` + (pp.length ? `<dt>passage point</dt><dd>on every session path between ${esc(pp.join('; '))}</dd>` : '') + '</dl>' +
        rec.shas.slice(0, 3).map(sh => `<p class="ev mono">${esc(sh)}</p>`).join('');
      ask = `What did Claude session ${it.name} do, and which repos does it link?`;
    } else {
      const n = Object.values(S.sessions).filter(rec => rec.repos[it.name] !== undefined).length, pp = Object.entries(S.passage_points).filter(([, v]) => v.includes(it.name)).map(([k]) => k);
      h = `<p class="ck">repo · session graph</p><h3>${esc(it.name)}</h3><dl><dt>sessions</dt><dd>${n}</dd>` + (pp.length ? `<dt>passage point</dt><dd>on every session path between ${esc(pp.join('; '))}</dd>` : '') +
        `<dt>through others only</dt><dd>with ${(S.linked_through_others || []).filter(p => p.includes(it.name)).length} repos</dd>` +
        `<dt>no recorded session link</dt><dd>with ${S.no_recorded_link.filter(p => p.includes(it.name)).length} repos</dd></dl>`;
      ask = `What links ${it.name} to the other repos, and where is there no recorded session link?`;
    }
  } else if (it.type === 'bfile') {
    const b = it.b, gh = ghFile(b.repo, b.file), v = B.bridges.files.variants[b.variant === 'hub' ? 'count' : 'jaccard'].per_file[b.repo + ':' + b.file] || {};
    h = `<p class="ck">${b.variant === 'hub' ? 'hub · every file kept' : 'bridge · high-churn files set aside'} · ${esc(b.repo)}</p><h3 class="mono">${gh ? `<a href="${esc(gh)}" target="_blank" rel="noopener">${esc(b.file)}</a>` : esc(b.file)}</h3>` +
      `<dl><dt>paths through it</dt><dd>${Math.round(100 * (v.share || 0))}% of those between the other files of its cluster of ${fmt(v.component)}</dd><dt>cut vertex</dt><dd>${v.cut_vertex ? 'yes: removing it splits its cluster' : 'no'}</dd><dt>commits</dt><dd>${fmt(v.commits)}</dd></dl>`;
    ask = `Why is ${b.repo}:${b.file} a ${b.variant} in the co-change graph, and what changes with it?`;
  }
  $('cardBody').innerHTML = h;
  $('cardAsk').hidden = !askState.on; $('cardAsk').dataset.q = ask;
  $('card').hidden = false;
}
$('cardClose').addEventListener('click', () => { $('card').hidden = true; });
$('cardAsk').addEventListener('click', () => { const q = $('cardAsk').dataset.q; if (q) { $('q').value = q; askNow(); } });

// scene API: what the tools and the UI call
function setXray(on) { X.xray = on; $('xray').setAttribute('aria-pressed', String(on)); applyColors(); }
function baseColor(c, i) {
  const B = X.B;
  if (X.mode === 'repo') { const k = B.repos.findIndex(r => r.name === c.r); return hex3(REPO_HUES[k % REPO_HUES.length]); }
  if (X.mode === 'size') { const u = Math.min(1, Math.log10(1 + c.a + c.d) / 5); return lerp(hex3('#2c4a63'), hex3('#eef5f8'), u); }
  if (X.mode === 'branch') return hex3(c.b ? '#5ac8e0' : '#f0b443');
  if (X.mode === 'age') { const t0 = B.commits[0].t, t1 = B.commits[B.commits.length - 1].t; return lerp(hex3('#5a4a8a'), hex3('#7fe3f0'), (c.t - t0) / Math.max(1, t1 - t0)); }
  return hex3(CLS[c.c] || CLS.other);
}
function applyColors() {
  if (!X.ready) return;
  const B = X.B, lit = X.lit;
  B.commits.forEach((c, i) => {
    const m = node('m' + i), tr = node('c' + i); if (!m) return;
    const base = baseColor(c, i), on = lit ? lit.has(i) : false;
    m.diffuseColor = sfc(base);
    if (on) { m.emissiveColor = sfc(lerp(base, [1, 1, 1], 0.35)); m.transparency = 0; tr.scale = new X3D.SFVec3f(1.8, 1.8, 1.8); }
    else { m.emissiveColor = sfc(base, X.xray && lit ? 0.04 : 0.38); m.transparency = X.xray && lit ? 0.9 : (c.b ? 0 : 0.45); tr.scale = new X3D.SFVec3f(1, 1, 1); }
  });
  const hiRepos = X.hiRepos || new Set();
  Object.entries(X.L.nodes).forEach(([n, nd]) => { const m = node(nd.def + '_m'); if (!m) return; const d = m.diffuseColor; m.emissiveColor = hiRepos.has(n) ? new X3D.SFColor(0.75, 0.82, 0.86) : new X3D.SFColor(d.r * 0.3, d.g * 0.3, d.b * 0.3); });
  Object.entries(X.L.city.districts).forEach(([n, dd]) => { const m = node(dd.def + '_m'); if (!m) return; const d = m.diffuseColor; m.emissiveColor = hiRepos.has(n) ? new X3D.SFColor(0.55, 0.62, 0.66) : new X3D.SFColor(d.r * 0.4, d.g * 0.4, d.b * 0.4); });
  $('legendMode').textContent = { who: 'colour: who', repo: 'colour: repo', size: 'colour: lines changed', branch: 'colour: branch', age: 'colour: age' }[X.mode];
}

function targetPoint(q) {
  const B = X.B, L = X.L;
  if (q.repo) q = { ...q, repo: resolveRepo(B, q.repo) };   // aliases resolve; an unknown name throws "not in vocabulary"
  if (q.sha) { const i = B.commits.findIndex(c => (c.h || '').startsWith(String(q.sha).toLowerCase())); if (i >= 0) return { p: L.commits[i], kind: 'helix', label: B.commits[i].r + ' ' + B.commits[i].h }; }
  if (q.day) { const d = L.days.find(x => x.day === q.day); if (d) return { p: [(d.x0 + d.x1) / 2, 0, 0], kind: 'helix', label: q.day }; }
  if (q.file) {   // a file as the tools and the ties print it (splitFile), lines dropped: an exact path, then a path ending at a '/', in that repo if named
    const bs = L.city.buildings, lowPaths = bs.map(b => b.path.toLowerCase());   // in any case, as the tools read a file
    const f = splitFile(B, q.file, x => lowPaths.some(p => p.includes(x.toLowerCase())), x => lowPaths.some(p => p === x.toLowerCase() || p.endsWith('/' + x.toLowerCase())));
    const repo = f.repo || q.repo || '', path = f.path, low = path.toLowerCase(), inR = bs.filter(x => !repo || x.repo === repo);
    const b = inR.find(x => x.path === path) || inR.find(x => x.path.endsWith('/' + path))
      || inR.find(x => x.path.toLowerCase() === low) || inR.find(x => x.path.toLowerCase().endsWith('/' + low));
    if (b) return { p: b.p, kind: 'city', label: b.repo + ':' + b.path };
  }
  if (q.tie) { const t = L.ties.curated.find(x => x.id === String(q.tie).toUpperCase()); if (t) return { p: t.p, kind: 'ties', label: t.id }; }
  if (q.session && L.bridges) {   // s-..., session:s-... or "session s-...", as the tools print it; an unknown one is refused
    const sid = sessionId(B, q.session), s = L.bridges.sessions[sid]; if (s) return { p: s.p, kind: 'bridges', label: 'session ' + sid };
  }
  if (q.concept && L.concepts && B.concepts) {
    // kinds as any tool or the scene prints them ("you,ai", "HagopJay + Claude"); the concept for them is their closure: of the
    // concepts whose kinds include them all, the one holding the most files
    const wk = kindsOf(q.concept);
    const c = B.concepts.files_by_author_class.concepts.filter(x => wk.every(k => x.intent.includes(k))).sort((a, b) => b.support - a.support)[0];
    const at = c && L.concepts.files.find(x => x.i === c.i); if (at) return { p: at.p, kind: 'concepts', label: kindsName(c.intent) };
  }
  if (q.repo) {
    const place = q.place || 'ties';
    if (place === 'city' && L.city.districts[q.repo]) return { p: L.city.districts[q.repo].center, kind: 'city', label: q.repo };
    if (place === 'bridges' && L.bridges && L.bridges.repos[q.repo]) return { p: L.bridges.repos[q.repo].p, kind: 'bridges', label: q.repo + ' in the session graph' };
    if (place === 'helix') { const idx = B.commits.map((c, i) => c.r === q.repo ? i : -1).filter(i => i >= 0); if (idx.length) return { p: L.commits[idx[Math.floor(idx.length / 2)]], kind: 'helix', label: q.repo }; }
    if (L.nodes[q.repo]) return { p: L.nodes[q.repo].p, kind: 'ties', label: q.repo };
  }
  return null;
}
function lookAt(pos, tgt) {   // axis-angle for a viewpoint at pos looking at tgt, +Y up (same maths as compiler.look_at)
  const n = v => { const l = Math.hypot(...v) || 1; return v.map(x => x / l); }, cr = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const f = n([tgt[0] - pos[0], tgt[1] - pos[1], tgt[2] - pos[2]]); let r = cr(f, [0, 1, 0]); if (Math.hypot(...r) < 1e-6) r = [1, 0, 0]; r = n(r); const u = cr(r, f);
  const m = [[r[0], u[0], -f[0]], [r[1], u[1], -f[1]], [r[2], u[2], -f[2]]], tr = m[0][0] + m[1][1] + m[2][2];
  let w, x, y, z, s;
  if (tr > 0) { s = Math.sqrt(tr + 1) * 2; w = 0.25 * s; x = (m[2][1] - m[1][2]) / s; y = (m[0][2] - m[2][0]) / s; z = (m[1][0] - m[0][1]) / s; }
  else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) { s = Math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]) * 2; w = (m[2][1] - m[1][2]) / s; x = 0.25 * s; y = (m[0][1] + m[1][0]) / s; z = (m[0][2] + m[2][0]) / s; }
  else if (m[1][1] > m[2][2]) { s = Math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]) * 2; w = (m[0][2] - m[2][0]) / s; x = (m[0][1] + m[1][0]) / s; y = 0.25 * s; z = (m[1][2] + m[2][1]) / s; }
  else { s = Math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]) * 2; w = (m[1][0] - m[0][1]) / s; x = (m[0][2] + m[2][0]) / s; y = (m[1][2] + m[2][1]) / s; z = 0.25 * s; }
  w = Math.max(-1, Math.min(1, w)); const sh = Math.sqrt(Math.max(0, 1 - w * w));
  return sh < 1e-6 ? [0, 0, 1, 0] : [x / sh, y / sh, z / sh, 2 * Math.acos(w)];
}
let flyFlip = false;
function flyTo(t) {
  const off = t.kind === 'city' ? [2.5, 6, 8] : t.kind === 'ties' ? [0.8, 1.2, 8.5] : (t.kind === 'concepts' || t.kind === 'bridges') ? [0.4, 0.8, 9.5] : [0.6, 2.2, 6.5];
  const pos = [t.p[0] + off[0], t.p[1] + off[1], t.p[2] + off[2]];
  const vp = node(flyFlip ? 'VP_fly2' : 'VP_fly'); flyFlip = !flyFlip; if (!vp) return false;
  const o = lookAt(pos, t.p);
  vp.position = new X3D.SFVec3f(...pos); vp.orientation = new X3D.SFRotation(...o); vp.centerOfRotation = new X3D.SFVec3f(...t.p);
  vp.set_bind = true;
  $('capTitle').textContent = 'Flying to ' + t.label; $('capText').textContent = ''; $('capClaim').hidden = true;
  return true;
}

const sceneApi = {
  show(q) {
    if (q.view) {
      if (!X.B.layout.views[q.view]) throw new Error('views: ' + Object.keys(X.B.layout.views).join(', '));
      bindView(q.view); history.replaceState(null, '', '#' + q.view);
      const vw = X.B.spec.views.find(v => v.id === q.view) || {};
      return { shown: q.view, claim: vw.claim ? `[${vw.claim.says}] ${vw.claim.text}` : undefined, caption: captionFor(q.view), scene_ready: X.ready };
    }
    const t = targetPoint(q);
    if (!t) throw new Error('nothing to show: give a view, or a repo, sha, day, file, tie, session or concept that exists');
    const ok = X.ready && flyTo(t);
    return { shown: t.label, in: t.kind, scene_ready: X.ready, moved: !!ok };
  },
  highlight(q) {
    if (q.clear) { X.lit = null; X.hiRepos = null; X.xray = false; $('xray').setAttribute('aria-pressed', 'false'); applyColors(); $('legendLit').textContent = ''; return { cleared: true }; }
    const rows = matchCommits(X.B, q), set = new Set(rows.map(c => c.i));
    X.lit = set; X.hiRepos = new Set(String(q.repo || '').split(',').map(s => s.trim()).filter(Boolean).map(n => resolveRepo(X.B, n)));
    if (q.xray !== undefined) X.xray = !!q.xray;
    $('xray').setAttribute('aria-pressed', String(X.xray));
    applyColors();
    $('legendLit').textContent = `${set.size} lit`;
    return { lit: set.size, of: X.B.commits.length, xray: X.xray, scene_ready: X.ready };
  },
  note(q) {
    const g = X.ready && node('Notes');
    if (q.clear) { if (g) g.children = new X3D.MFNode(); X.notes = 0; $('legendNotes').textContent = ''; return { notes: 0 }; }
    const text = String(q.text || '').trim().slice(0, 80);
    if (!text) throw new Error('a note needs text');
    if (X.notes >= 12) throw new Error('12 notes already; clear them first');
    const t = targetPoint(q); if (!t) throw new Error('give a repo, sha, day, file, tie, session or concept to pin the note to');
    if (!g) return { placed: false, scene_ready: false };
    const sc = X.browser.currentScene, mk = n => sc.createNode(n);
    const words = text.split(' '), lines = []; let cur = '';
    words.forEach(w => { if ((cur + ' ' + w).trim().length > 28) { lines.push(cur.trim()); cur = w; } else cur += ' ' + w; }); if (cur.trim()) lines.push(cur.trim());
    const tr = mk('Transform'); tr.translation = new X3D.SFVec3f(t.p[0], t.p[1] + 1.1, t.p[2] + 0.3);
    const bb = mk('Billboard'); bb.axisOfRotation = new X3D.SFVec3f(0, 0, 0);
    const sh = mk('Shape'), ap = mk('Appearance'), mt = mk('Material'), tx = mk('Text'), fs = mk('FontStyle');
    mt.diffuseColor = new X3D.SFColor(0, 0, 0); mt.emissiveColor = sfc(hex3('#7fe3f0')); ap.material = mt; sh.appearance = ap;
    fs.family = new X3D.MFString('fonts/UbuntuMono-R.ttf', 'TYPEWRITER'); fs.size = 0.34; fs.justify = new X3D.MFString('MIDDLE', 'MIDDLE');
    tx.string = new X3D.MFString(...lines); tx.fontStyle = fs; tx.solid = false; sh.geometry = tx;
    bb.children = new X3D.MFNode(sh); tr.children = new X3D.MFNode(bb);
    const ln = mk('Shape'), la = mk('Appearance'), lm = mk('Material'), ls = mk('LineSet'), lc = mk('Coordinate');
    lm.emissiveColor = sfc(hex3('#7fe3f0')); lm.diffuseColor = new X3D.SFColor(0, 0, 0); la.material = lm; ln.appearance = la;
    lc.point = new X3D.MFVec3f(new X3D.SFVec3f(t.p[0], t.p[1], t.p[2]), new X3D.SFVec3f(t.p[0], t.p[1] + 0.8, t.p[2] + 0.3)); ls.coord = lc; ls.vertexCount = new X3D.MFInt32(2); ln.geometry = ls;
    g.addChildren = new X3D.MFNode(tr, ln);
    X.notes++; $('legendNotes').textContent = `${X.notes} note${X.notes > 1 ? 's' : ''} by Claude`;
    return { placed: true, at: t.label, notes: X.notes };
  },
  colorBy(mode) {
    if (!['who', 'repo', 'size', 'branch', 'age'].includes(mode)) throw new Error('modes: who, repo, size, branch, age');
    X.mode = mode; $('mode').value = mode; applyColors(); return { mode };
  },
};
X.scene = sceneApi;

$('xray').addEventListener('click', () => { if (!X.lit) { sceneApi.highlight({ who: 'ai', xray: true }); return; } setXray(!X.xray); });
$('mode').addEventListener('change', e => sceneApi.colorBy(e.target.value));
$('clear').addEventListener('click', () => { sceneApi.highlight({ clear: true }); sceneApi.note({ clear: true }); });
$('views').addEventListener('click', e => { const a = e.target.closest('.vbtn'); if (a && a.getAttribute('href') === location.hash) { e.preventDefault(); bindView(a.dataset.view); } });

// ------------------------------------------------------------------ Ask Claude (the Artifact's sample capability)
const askState = { on: false, sample: null, tools: [], turns: [], busy: false, ctl: null, tier: 'default', toolsOk: true, maxTools: 16 };
const RULES = B => `You are Claude, answering inside x3d-ray: an X3D page that x-rays HagopJay's family of X3D repositories from their git history.

What you are looking at: a tool called x3d.xray built this page with no model in its pipeline. Claude, in Claude Code sessions, wrote that tool's code and this page's captions, and code filled every number in those captions. x3d.xray is itself one of the repos in the data. Claude wrote ${B.self.claude_in_the_data.commits} of the ${B.self.claude_in_the_data.of} commits here; you are not those sessions and remember none of them, so you know that work only from this data.

How to answer:
- Ground every number in the digest below or in a tool result. If the data does not hold the answer, say so plainly. Never guess what a private repo contains.
- Two routes. Lookup: query_commits, activity, top_files, ties, signals, concepts and bridges answer exact questions from tables. Traverse: traverse walks the tie graph for "what connects A to B" or "what depends on A". End each answer with one short line saying which route you used (lookup, traverse, both, or digest only).
- concepts (who touched which files; which areas move together) and bridges (which sessions link which repos; which files lie on the most shortest paths between others) say what each part read and set aside (excluded): quote it whenever an answer could read as none. A private repo answers with counts only: its files are unknown, never none. Two repos one session committed to are linked directly; two joined only through other repos are linked through others; two that no chain of sessions joins have no recorded session link, never "no link", and the ties may still join them. Betweenness compares files only within one cluster. A list cut for size says how many it left out.
- how_built says how the page, a stage or a signal was made (it can show source lines), what makes two things the same thing here (identity), and the denominators behind Claude's share.
- Repo names go through the family's vocabulary: "x3d.ai" is x3d_ai, ".prov" is the provenance stage inside x3d.ML (there is no .prov repo). When a tool answers "not in vocabulary", say so and offer the nearest names; never guess which repo was meant.
- Unknown is not zero. A repo whose tree was not measured, or a private repo's contents, is unknown: say "not measured", never "none". A tie with no witness is not shown here, so never assert one.
- When you state Claude's share of the commits, name the denominator (all own commits, without merges, without bots, or by lines changed).
- The viewer is looking at the 3D scene beside you. When a place in the scene answers the question, call show (camera), highlight (light commits; xray dims the rest), note (pin a short label) or color_by. Prefer showing over describing.
- Lead with the answer, then the evidence: repo, day, sha, file. About 150 words unless asked for more. Plain sentences and short lists; no wide tables. Times are UTC.

DIGEST
${digest(B)}`;

function askOff(msg) {
  askState.on = false;
  $('askState').textContent = msg; $('askState').hidden = false;
  $('q').disabled = true; $('askBtn').disabled = true; $('cardAsk').hidden = true;
  document.querySelectorAll('.qchip').forEach(b => { b.disabled = true; });
}

async function initAsk(B) {
  $('qchips').innerHTML = B.spec.questions.map(q => `<button class="qchip" type="button">${esc(q)}</button>`).join('');
  $('qchips').addEventListener('click', e => { const b = e.target.closest('.qchip'); if (!b || b.disabled) return; $('q').value = b.textContent; askNow(); });
  $('askForm').addEventListener('submit', e => { e.preventDefault(); askNow(); });
  $('q').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); askNow(); } });
  $('stopBtn').addEventListener('click', () => { if (askState.ctl) askState.ctl.abort(); });
  document.querySelectorAll('input[name="tier"]').forEach(r => r.addEventListener('change', () => { askState.tier = r.value; }));
  $('newChat').addEventListener('click', () => { askState.turns = []; $('chat').innerHTML = ''; $('q').focus(); });
  askOff('Connecting to Claude…');
  let sample = null;
  try { sample = window.claude && typeof window.claude.use === 'function' ? await window.claude.use('sample') : null; } catch (e) { sample = null; }
  if (!sample) { askOff('Ask works when this page is open in Claude (claude.ai or the Claude app). Everything else on the page works without it.'); log('sample: none'); return; }
  askState.sample = sample;
  let lim = null;
  try { lim = await sample.limits(); } catch (e) { lim = null; }
  askState.toolsOk = !!(lim && lim.tools);
  askState.maxTools = lim && lim.tools ? lim.tools.maxCount : 0;
  if (lim && lim.modelTiers) document.querySelectorAll('input[name="tier"]').forEach(r => { r.disabled = !lim.modelTiers.includes(r.value); });
  askState.tools = makeTools(B, sceneApi);
  askState.on = true;
  $('askState').hidden = true; $('q').disabled = false; $('askBtn').disabled = false;
  document.querySelectorAll('.qchip').forEach(b => { b.disabled = false; });
  if (!askState.toolsOk) { $('askState').textContent = 'This view cannot run the page\'s tools, so Claude answers from the digest alone and cannot move the scene.'; $('askState').hidden = false; }
  log('sample: ready, tools ' + askState.toolsOk + ' max ' + askState.maxTools);
}

function md(s) {   // a small, safe Markdown: escape first, then bold, code, headings and lists
  const out = []; let inList = null;
  esc(s).split('\n').forEach(line => {
    const li = /^\s*(?:[-*]|\d+\.)\s+(.*)$/.exec(line), hd = /^#{1,4}\s+(.*)$/.exec(line);
    const inline = t => t.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/(^|[\s(])\*([^*\s][^*]*)\*(?=[\s).,;:]|$)/g, '$1<i>$2</i>');
    if (li) { const tag = /^\s*\d+\./.test(line) ? 'ol' : 'ul'; if (inList !== tag) { if (inList) out.push(`</${inList}>`); out.push(`<${tag}>`); inList = tag; } out.push('<li>' + inline(li[1]) + '</li>'); return; }
    if (inList) { out.push(`</${inList}>`); inList = null; }
    if (hd) out.push('<h4>' + inline(hd[1]) + '</h4>'); else if (line.trim()) out.push('<p>' + inline(line) + '</p>');
  });
  if (inList) out.push(`</${inList}>`);
  return out.join('');
}

function bubble(role, html) {
  const d = document.createElement('div'); d.className = 'msg ' + role; d.innerHTML = html || '';
  $('chat').appendChild(d); $('chat').scrollTop = $('chat').scrollHeight; return d;
}

function summarize(name, r) {
  if (!r || typeof r !== 'object') return 'done';
  if (r.summary) return r.summary;
  if (r.status) return r.status;
  if (r.route === 'traverse') return r.to ? (r.hops === null ? 'no path within three hops' : `${r.hops} hop${r.hops === 1 ? '' : 's'}`) : `${r.neighbours} neighbours`;
  if (r.unknown) return 'unknown: not measured';
  if (r.matched !== undefined) return `${r.matched} matched`;
  if (r.lit !== undefined) return `${r.lit} lit`;
  if (r.shown) return 'showing ' + r.shown;
  if (r.placed !== undefined) return r.placed ? 'note placed' : 'no scene';
  if (r.machine || r.curated) return `${(r.machine || []).length} machine, ${(r.curated || []).length} curated`;
  if (r.signals) return `${r.signals.length} signals`;
  if (r.mode) return 'colour: ' + r.mode;
  return 'done';
}

const ERR = {
  rate_limited: 'Claude is busy, or your usage limit is reached. Try again in a little while.',
  session_expired: 'Your Claude sign-in expired. Sign in again, then ask.',
  refused: 'Claude declined to answer that. Try asking it another way.',
  empty_completion: 'No answer came back. Try a narrower question.',
  prompt_too_large: 'The conversation got too long, so the oldest turns were dropped. Ask again.',
  tools_unavailable: 'This view cannot run the page\'s tools. Ask again and Claude will answer from the digest alone.',
  invalid_request: 'The page sent a request Claude could not take (a page bug).',
  transform_error: 'The page sent a request Claude could not take (a page bug).',
  queue_overflow: 'Too many requests queued. Reload the page.',
};
const OFF = new Set(['not_granted', 'sampling_disabled', 'not_declared', 'capability_disabled', 'capability_removed']);

async function askNow() {
  const q = $('q').value.trim();
  if (!q || askState.busy || !askState.on) return;
  askState.busy = true; $('askBtn').disabled = true; $('stopBtn').hidden = false; $('q').value = '';
  bubble('user', '<p>' + esc(q) + '</p>');
  const b = bubble('assistant', '<p class="thinking">Thinking…</p>');
  const toolLog = document.createElement('div'); toolLog.className = 'toollog';
  const ans = document.createElement('div'); ans.className = 'ans'; ans.innerHTML = '<p class="thinking">Thinking…</p>';
  b.innerHTML = ''; b.appendChild(toolLog); b.appendChild(ans);
  const ctl = new AbortController(); askState.ctl = ctl;
  const wrapped = askState.toolsOk ? askState.tools.slice(0, askState.maxTools || askState.tools.length).map(t => ({
    name: t.name, description: t.description, inputSchema: t.inputSchema,
    execute: async (input, ctx) => {
      const line = document.createElement('p'); line.className = 'tool';
      const args = Object.entries(input || {}).map(([k, v]) => k + '=' + (typeof v === 'string' ? v : JSON.stringify(v))).join(' ');
      line.textContent = '▸ ' + t.name + (args ? ' ' + args.slice(0, 120) : '') + ' …'; toolLog.appendChild(line); $('chat').scrollTop = $('chat').scrollHeight;
      try { const r = await t.execute(input || {}, ctx); line.textContent = '▸ ' + t.name + (args ? ' ' + args.slice(0, 120) : '') + ' → ' + summarize(t.name, r); return r; }
      catch (e) { line.textContent = '▸ ' + t.name + ' → ' + (e && e.message || e); line.classList.add('fail'); throw e; }
    },
  })) : null;
  const turns = [{ role: 'user', content: RULES(X.B) }, ...askState.turns.slice(-6), { role: 'user', content: q }];
  try {
    const opts = { signal: ctl.signal, modelTier: askState.tier, onText: ({ text }) => { ans.innerHTML = md(text); $('chat').scrollTop = $('chat').scrollHeight; } };
    if (wrapped) opts.tools = wrapped; else opts.cache = false;
    const { text, truncated } = await askState.sample(turns, opts);
    ans.innerHTML = md(text) + (truncated ? '<p class="note">Cut short. Ask for less at a time.</p>' : '');
    askState.turns.push({ role: 'user', content: q }, { role: 'assistant', content: text });
  } catch (e) {
    const code = e && e.code;
    if (code === 'cancelled') { ans.innerHTML = e.text ? md(e.text) + '<p class="note">Stopped.</p>' : '<p class="note">Stopped.</p>'; }
    else if (OFF.has(code)) { ans.innerHTML = '<p class="note">Claude is not allowed for this page in this view.</p>'; askOff('Claude is off for this page in this view. You can allow it from the page\'s permissions.'); }
    else {
      if (code === 'refused') ans.innerHTML = '';
      else ans.innerHTML = e && e.text ? md(e.text) : '';
      if (code === 'prompt_too_large') askState.turns = askState.turns.slice(-2);
      if (code === 'tools_unavailable') askState.toolsOk = false;
      ans.insertAdjacentHTML('beforeend', `<p class="note">${esc(ERR[code] || 'The answer was interrupted. Ask again to retry.')}</p>`);
    }
  } finally {
    askState.busy = false; askState.ctl = null; $('stopBtn').hidden = true; $('askBtn').disabled = !askState.on; $('chat').scrollTop = $('chat').scrollHeight;
  }
}

boot();
