// The live gate: serve the built page the way a claude.ai Artifact does, as far as this container can copy it, and drive it in Chromium.
//   node x3dray/web/live_gate.cjs --site out/<family> [--frames DIR] [--runs desktop-dark,phone-dark]
// The page goes inside a skeleton like the host's; only the files in publish.json are served; .x3d is application/xml; every response
// carries a strict CSP written from the Artifact page contract. The real host's policy is not visible from here, so a pass here is
// Likely there, never Confirmed.
// Checks per run: the scene loads from the page's own files; every X3D label draws; no console error or blocked request; no
// horizontal scroll; Ask says plainly that it needs Claude when no runtime is there; the scene tools move the camera, light commits,
// pin a note and recolour; a real click in the canvas opens a card. On desktop it also runs Ask end to end against a stand-in for the
// sample runtime (no model): the stand-in checks the call the way the runtime would (turns, byte cap, tool definitions, no cache with
// tools), calls the page's tools, streams an answer, then fails once with rate_limited and once with not_granted.
// Frames: every view, the first screen, the whole page, a card, and the scene after the stand-in's tool calls.
const http = require('http'), fs = require('fs'), path = require('path');
const { chromium } = require('playwright');

const arg = k => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : null; };
const SITE = path.resolve(arg('--site'));
const FR = path.resolve(arg('--frames') || path.join(SITE, 'frames'));
const only = (arg('--runs') || '').split(',').filter(Boolean);
const CDNS = 'https://cdnjs.cloudflare.com https://cdn.jsdelivr.net/npm/ https://unpkg.com https://cdn.tailwindcss.com https://code.jquery.com';
const CSP = ["default-src 'none'", "script-src 'self' 'unsafe-inline' " + CDNS, "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  'font-src https://fonts.gstatic.com', "img-src 'self' data: blob:", "connect-src 'self'", "worker-src 'self' blob:", "media-src 'self' data: blob:"].join('; ');
const SKELETON = ['<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
  '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0;font:14px system-ui,sans-serif}',
  'img{max-width:100%}[hidden]{display:none!important}</style></head><body>', '</body></html>'];
const pub = JSON.parse(fs.readFileSync(path.join(SITE, 'publish.json'), 'utf8'));
const published = new Set(Object.keys(pub).concat(['index.html']));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const CHROME = fs.existsSync('/opt/pw-browsers/chromium-1243/chrome-linux64/chrome') ? '/opt/pw-browsers/chromium-1243/chrome-linux64/chrome' : undefined;

function serve() {
  return new Promise(res => {
    const srv = http.createServer((req, rsp) => {
      const u = decodeURIComponent(new URL(req.url, 'http://x').pathname).replace(/^\/+/, '') || 'index.html';
      const f = path.join(SITE, u);
      if (!published.has(u) || !f.startsWith(SITE) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { rsp.writeHead(404, { 'content-security-policy': CSP }); rsp.end('not found'); return; }
      let body = fs.readFileSync(f);
      if (u === 'index.html') body = Buffer.from(SKELETON.slice(0, 3).join('') + body.toString('utf8') + SKELETON[3]);
      const type = u === 'index.html' ? 'text/html; charset=utf-8' : pub[u].contentType;
      rsp.writeHead(200, { 'content-type': type, 'content-security-policy': CSP, 'cache-control': 'no-store' });
      rsp.end(body);
    });
    srv.listen(0, '127.0.0.1', () => res(srv));
  });
}

// The stand-in for the sample runtime, installed before any page script runs (as the viewer does).
function stubRuntime() {
  const S = window.__stub = { calls: [], problems: [], mode: 'answer', plan: [] };
  const enc = s => new TextEncoder().encode(s).length;
  const fake = async (input, opts = {}) => {
    const call = { tools: (opts.tools || []).map(t => t.name), modelTier: opts.modelTier, has_cache: 'cache' in opts, results: [] };
    S.calls.push(call);
    if (!Array.isArray(input) || !input.length) S.problems.push('input is not a turn list');
    else {
      if (input[0].role !== 'user' || input[input.length - 1].role !== 'user') S.problems.push('turns must start and end on a user turn');
      input.forEach((t, i) => { if (!['user', 'assistant'].includes(t.role) || typeof t.content !== 'string' || !t.content) S.problems.push('bad turn ' + i); });
      call.prompt_bytes = enc(input.map(t => t.content).join(''));
      if (call.prompt_bytes > 262144) S.problems.push('prompt over 256 KiB: ' + call.prompt_bytes);
    }
    if (opts.tools && 'cache' in opts) S.problems.push('cache passed with tools');
    if (!(opts.signal instanceof AbortSignal)) S.problems.push('signal is not an AbortSignal');
    if (opts.modelTier && !['default', 'complex', 'quick'].includes(opts.modelTier)) S.problems.push('unknown modelTier ' + opts.modelTier);
    if ((opts.tools || []).length > 16) S.problems.push('more than 16 tools');
    (opts.tools || []).forEach(t => {
      if (!/^[A-Za-z0-9_-]{1,128}$/.test(t.name)) S.problems.push('bad tool name ' + t.name);
      if (!t.description || enc(t.description) > 1024) S.problems.push('description missing or over 1 KB: ' + t.name);
      if (t.inputSchema && (t.inputSchema.type !== 'object' || enc(JSON.stringify(t.inputSchema)) > 4096)) S.problems.push('inputSchema not an object or over 4 KB: ' + t.name);
      if (typeof t.execute !== 'function') S.problems.push('no execute: ' + t.name);
    });
    if (S.mode === 'rate_limited') throw { code: 'rate_limited', message: 'stand-in: rate limited' };
    if (S.mode === 'not_granted') throw { code: 'not_granted', message: 'stand-in: not granted' };
    const ctx = { signal: opts.signal };
    for (const [name, args] of S.plan) {
      const t = (opts.tools || []).find(x => x.name === name);
      if (!t) { call.results.push({ name, error: 'not offered' }); continue; }
      try {
        const r = await t.execute(args, ctx);
        const n = enc(typeof r === 'string' ? r : JSON.stringify(r));
        call.results.push({ name, bytes: n, summary: JSON.stringify(r).slice(0, 160) });
        if (n > 32768) S.problems.push(name + ' result over 32 KB: ' + n);
      } catch (e) { call.results.push({ name, error: String(e && e.message || e).slice(0, 160) }); }
    }
    const text = 'Stand-in answer (no model). The page ran ' + S.plan.length + ' tools for this question.\n\n- **lookup**: query_commits\n- **traverse**: traverse\n\nRoute: both.';
    if (opts.onText) { opts.onText({ text: text.slice(0, 24), delta: text.slice(0, 24) }); await new Promise(r => setTimeout(r, 60)); opts.onText({ text, delta: text.slice(24) }); }
    return { text, truncated: false };
  };
  fake.limits = async () => ({ maxPromptBytes: 262144, tools: { maxCount: 16 }, modelTiers: ['default', 'complex', 'quick'] });
  fake.json = async () => { throw { code: 'capability_removed', message: 'stand-in' }; };
  window.claude = { use: async name => (name === 'sample' ? fake : null) };
}

const LABELS = () => {   // every Text in the scene, and how many X_ITE has laid out (textBounds > 0)
  const X = window.xray, sc = X.browser.currentScene; let n = 0, drawn = 0, guard = 0;
  const walk = nd => {
    if (!nd || guard++ > 300000) return;
    let t; try { t = nd.getNodeTypeName(); } catch (e) { return; }
    if (t === 'Shape') { const g = nd.geometry; if (g && g.getNodeTypeName() === 'Text') { n++; const b = g.textBounds; if (b && b.x > 0 && b.y > 0) drawn++; } return; }
    let ch = null; try { ch = nd.children; } catch (e) { ch = null; }
    if (ch && typeof ch.length === 'number') for (let i = 0; i < ch.length; i++) walk(ch[i]);
  };
  const r = sc.rootNodes; for (let i = 0; i < r.length; i++) walk(r[i]);
  return { n, drawn };
};

(async () => {
  const srv = await serve(), base = 'http://127.0.0.1:' + srv.address().port + '/';
  const proxy = process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY, bypass: '127.0.0.1,localhost' } : undefined;
  const b = await chromium.launch({ executablePath: CHROME, proxy, args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const html = fs.readFileSync(path.join(SITE, 'index.html'), 'utf8') + fs.readFileSync(path.join(SITE, 'app.js'), 'utf8') + fs.readFileSync(path.join(SITE, 'tools.js'), 'utf8');
  const out = { csp: CSP, chromium: b.version(), static: { eval_or_function: /\beval\s*\(|new\s+Function\b/.test(html) }, runs: [], ok: true };
  fs.mkdirSync(FR, { recursive: true });
  const RUNS = [{ name: 'desktop-dark', viewport: { width: 1600, height: 1000 }, scheme: 'dark', stub: true },
    { name: 'desktop-light', viewport: { width: 1366, height: 860 }, scheme: 'light' },
    { name: 'phone-dark', viewport: { width: 390, height: 844 }, scheme: 'dark' }].filter(r => !only.length || only.includes(r.name));
  // Known and harmless here: Google Fonts unreachable from this container; X_ITE's own UI font (PT Sans, injected as a CSS font, which
  // font-src refuses; X_ITE's menus fall back to system fonts and the scene's labels use the page's TTF); X_ITE's preload of its default
  // SERIF font (woff2, whose decoder is not shipped because the policy refuses the string evaluation it needs; no Text node here uses
  // SERIF); X_ITE's mikktspace WebAssembly (tangents for normal maps, which this scene has none of; CSP has no 'wasm-unsafe-eval'); and
  // gates.json or receipt.json during the gate run itself, since the gates write the first and the receipt is made after them.
  const known = c => /favicon\.ico/.test(c) || /fonts\.(googleapis|gstatic)\.com/.test(c) || /PT_Sans|PTSans/.test(c) || /mikktspace|WebAssembly|wasm/i.test(c) ||
    /DroidSerif|woff2_decoder|font family 'SERIF'|Error loading font .*DroidSerif/.test(c) ||
    /ERR_TUNNEL|ERR_PROXY/.test(c) || /^(error: Failed to load resource: the server responded with a status of 404 \(Not Found\) @ )?(gates|receipt)\.json( HTTP 404)?$/.test(c);
  for (const run of RUNS) {
    const r = { name: run.name, console: [], violations: [], failed: [], external: [], checks: {}, frames: [] };
    const open = async (stub) => {
      const ctx = await b.newContext({ viewport: run.viewport, colorScheme: run.scheme, deviceScaleFactor: 1, ignoreHTTPSErrors: true, isMobile: run.viewport.width < 500, hasTouch: run.viewport.width < 500 });
      const p = await ctx.newPage(); p.setDefaultTimeout(180000);
      await p.addInitScript(() => { window.__csp = []; document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI)); });
      if (stub) await p.addInitScript(stubRuntime);
      p.on('console', m => r.console.push(m.type() + ': ' + m.text().slice(0, 240) + (m.location() && m.location().url ? ' @ ' + m.location().url.replace(base, '') : '')));
      p.on('pageerror', e => r.console.push('pageerror: ' + String(e).slice(0, 240)));
      p.on('requestfailed', q => (q.url().startsWith(base) ? r.failed : r.external).push(q.url().replace(base, '') + ' ' + (q.failure() || {}).errorText));
      p.on('response', q => { if (q.status() >= 400) (q.url().startsWith(base) ? r.failed : r.external).push(q.url().replace(base, '') + ' HTTP ' + q.status()); });
      const t0 = Date.now();
      await p.goto(base, { waitUntil: 'load' });
      await p.waitForFunction(() => document.body.dataset.scene === 'ready' || document.body.dataset.scene === 'failed', null, { timeout: 180000, polling: 200 }).catch(() => {});
      r.boot_ms = r.boot_ms || Date.now() - t0;
      return { ctx, p };
    };
    const shot = async (p, name, sel, full) => { const f = path.join(FR, `${name}-${run.name}.png`); if (sel) await p.locator(sel).screenshot({ path: f }); else await p.screenshot({ path: f, fullPage: !!full }); r.frames.push(path.basename(f)); };

    // ---- 1. the page with no Claude runtime: everything but Ask must work, and Ask must say why it is off
    const { ctx, p } = await open(false);
    await p.waitForFunction(LABELS => { try { const l = (new Function('return ' + LABELS))()(); return l.n > 0 && l.drawn === l.n; } catch (e) { return false; } }, LABELS.toString(), { timeout: 40000, polling: 300 }).catch(() => {});
    r.labels = await p.evaluate(`(${LABELS.toString()})()`).catch(e => ({ error: String(e) }));
    r.state = await p.evaluate(() => ({ scene: document.body.dataset.scene, failed: window.xray.failed, view: window.xray.view,
      xite: (document.querySelector('script[src*="x_ite"]') || {}).src, layout_labels: window.xray.L.counts.labels,
      ask: { text: document.getElementById('askState').textContent, shown: !document.getElementById('askState').hidden, input_disabled: document.getElementById('q').disabled },
      claim: !document.getElementById('capClaim').hidden && document.getElementById('capClaim').textContent,
      scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth, log: window.xray.log.slice(-5) }));
    await shot(p, 'first-screen'); await shot(p, 'whole-page', null, true);
    // scene tools, as Claude would call them, through the page's own tool list
    r.tools = await p.evaluate(async () => {
      const { makeTools } = await import('./tools.js');
      const T = Object.fromEntries(makeTools(window.xray.B, window.xray.scene).map(t => [t.name, t]));
      const call = (n, a) => { try { return T[n].execute(a); } catch (e) { return { error: e.message }; } };
      const res = {};
      res.show = call('show', { view: 'ties' });
      res.highlight = call('highlight', { who: 'ai', xray: true });
      res.note = call('note', { text: 'gate: pinned by the scene tools', repo: 'x3d_mcp' });
      res.color = call('color_by', { mode: 'repo' });
      res.fly = call('show', { repo: 'x3d.ML', place: 'city' });
      res.alias = call('show', { repo: 'x3d.ai' });
      res.vocab = call('highlight', { repo: 'x3d.prov.v2' });
      const sid = Object.keys(window.xray.L.bridges ? window.xray.L.bridges.sessions : {})[0];
      res.session = sid ? call('show', { session: sid }) : { skipped: true, bundle_has: !!window.xray.B.bridges };
      res.concept = window.xray.L.concepts ? call('show', { concept: 'claude,hagopjay' }) : { skipped: true, bundle_has: !!window.xray.B.concepts };
      res.every = window.xray.L.concepts ? call('show', { concept: 'every file' }) : { skipped: true, bundle_has: !!window.xray.B.concepts };
      // a file as the tools print it: repo:path is that repo's file, and a line number is dropped
      const root = window.xray.L.city.buildings.find(b => b.path === 'README.md');
      res.file = root ? call('show', { file: root.repo + ':README.md:3' }) : { skipped: true };
      res.file_want = root ? root.repo + ':README.md' : null;
      res.state = { view: window.xray.view, lit: window.xray.lit ? window.xray.lit.size : 0, notes: window.xray.notes, mode: window.xray.mode };
      call('highlight', { clear: true }); call('note', { clear: true }); call('color_by', { mode: 'who' });
      return res;
    });
    // a real click in the canvas opens a card: hover until a TouchSensor reports isOver (the tag shows), then press there
    const views = await p.evaluate(() => Object.keys(window.xray.L.views));
    await p.evaluate(() => window.xray.scene.show({ view: 'ties' })); await sleep(3000);
    await p.evaluate(() => document.getElementById('stage').scrollIntoView({ block: 'center' }));
    const box = await p.locator('#stage').boundingBox();
    r.click = null;
    outer: for (const gy of [0.5, 0.4, 0.6, 0.3, 0.7]) for (const gx of [0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74]) {
      const px = box.x + box.width * gx, py = box.y + box.height * gy;
      await p.mouse.move(px - 4, py - 4); await p.mouse.move(px, py, { steps: 4 }); await sleep(450);
      const over = await p.evaluate(() => !document.getElementById('tag').hidden && document.getElementById('tag').textContent);
      if (!over) continue;
      await p.mouse.down(); await sleep(150); await p.mouse.up(); await sleep(600);
      const card = await p.evaluate(() => !document.getElementById('card').hidden && document.getElementById('cardBody').textContent.slice(0, 120));
      if (card) { r.click = { at: [gx, gy], tag: over, card }; break outer; }
    }
    if (r.click) await shot(p, 'card', '#stage');
    await p.mouse.move(2, 2); await sleep(300);
    await p.evaluate(() => { document.getElementById('card').hidden = true; document.getElementById('tag').hidden = true; });
    for (const v of views) {
      await p.evaluate(id => window.xray.scene.show({ view: id }), v); await sleep(run.viewport.width < 500 ? 2200 : 2800);
      await shot(p, 'view-' + v, '#stage');
    }
    r.overflow = await p.evaluate(() => { const w = window.innerWidth, bad = [];
      document.querySelectorAll('body *').forEach(e => { const q = e.getBoundingClientRect(); if (q.width && q.right > w + 1 && !e.closest('.repos, .chat, .tbl, pre, x3d-canvas')) bad.push(e.tagName + '.' + e.className + ' ' + Math.round(q.right)); });
      return { scrollW: document.documentElement.scrollWidth, innerW: w, bad: bad.slice(0, 6) }; });
    r.violations = await p.evaluate(() => window.__csp);
    await ctx.close();

    // ---- 2. Ask end to end against the stand-in runtime (desktop only)
    if (run.stub) {
      const s = await open(true), q = s.p;
      await q.waitForFunction(() => !document.getElementById('q').disabled, null, { timeout: 15000 }).catch(() => {});
      const ai = await q.evaluate(() => window.xray.B.commits.filter(c => c.c === 'ai').length);
      await q.evaluate(() => { window.__stub.plan = [['query_commits', { who: 'ai', limit: 3 }], ['traverse', { from: 'blender io', to: 'onto' }], ['show', { view: 'claude' }],
        ['highlight', { who: 'claude', xray: true }], ['note', { text: 'gate: a note from the stand-in', repo: 'x3d.ML' }], ['top_files', { repo: 'graphify' }], ['ties', { repo: 'nonsense-repo' }],
        ['concepts', { who: 'you,ai' }], ['bridges', { between: 'x3d.ML,x3d_mcp' }]]; });
      await q.click('.qchip');
      await q.waitForFunction(() => { const a = document.querySelector('.msg.assistant .ans'); return a && /Route: both/.test(a.textContent); }, null, { timeout: 20000 }).catch(() => {});
      await sleep(2800);
      r.ask = await q.evaluate(ai => {
        const S = window.__stub, call = S.calls[0] || {};
        return { calls: S.calls.length, problems: S.problems, tools_offered: call.tools, modelTier: call.modelTier, has_cache: call.has_cache, prompt_bytes: call.prompt_bytes,
          results: call.results, answer: (document.querySelector('.msg.assistant .ans') || {}).textContent, tool_lines: document.querySelectorAll('.msg.assistant .toollog .tool').length,
          failed_lines: [...document.querySelectorAll('.msg.assistant .toollog .tool.fail')].map(x => x.textContent.slice(0, 140)),
          scene: { view: window.xray.view, lit: window.xray.lit ? window.xray.lit.size : 0, ai, notes: window.xray.notes, xray: window.xray.xray } };
      }, ai);
      await shot(q, 'ask-stand-in', '#stage'); await shot(q, 'ask-panel', '.right');
      await q.evaluate(() => { window.__stub.mode = 'rate_limited'; window.__stub.plan = []; });
      await q.fill('#q', 'a second question'); await q.press('#q', 'Enter'); await sleep(800);
      r.ask_rate_limited = await q.evaluate(() => { const m = [...document.querySelectorAll('.msg.assistant .ans')].pop(); return m && m.textContent; });
      await q.evaluate(() => { window.__stub.mode = 'not_granted'; });
      await q.fill('#q', 'a third question'); await q.press('#q', 'Enter'); await sleep(800);
      r.ask_not_granted = await q.evaluate(() => ({ answer: [...document.querySelectorAll('.msg.assistant .ans')].pop().textContent, off: document.getElementById('q').disabled,
        state: document.getElementById('askState').textContent }));
      r.violations = r.violations.concat(await q.evaluate(() => window.__csp));
      await s.ctx.close();
    }

    const S = r.state, T = r.tools;
    r.harmless = r.console.filter(c => /^(error|pageerror|warning)/.test(c) && known(c)).concat(r.violations.filter(known));
    r.checks.scene_loaded = S.scene === 'ready' && !S.failed;
    r.checks.xite_same_origin = S.xite === base + 'x_ite/x_ite.min.js';
    r.checks.every_label_drawn = r.labels && r.labels.n >= S.layout_labels && r.labels.drawn === r.labels.n;
    r.checks.claim_shown = !!S.claim;
    r.checks.ask_explains_its_absence = S.ask.shown && S.ask.input_disabled && /Claude/.test(S.ask.text);
    r.checks.scene_tools_work = T.show.shown === 'ties' && T.highlight.lit > 0 && T.note.placed === true && T.color.mode === 'repo' && T.fly.moved === true &&
      T.alias.shown === 'x3d_ai' && /not in vocabulary/.test(T.vocab.error || '') && T.state.notes === 1 &&
      (T.session.skipped ? !T.session.bundle_has : T.session.moved === true) && (T.concept.skipped ? !T.concept.bundle_has : T.concept.moved === true) &&
      (T.every.skipped ? !T.every.bundle_has : T.every.moved === true && T.every.shown === 'every file') &&
      (T.file.skipped || (T.file.moved === true && T.file.shown === T.file_want));
    r.checks.click_in_scene_opens_card = !!r.click;
    r.checks.no_horizontal_scroll = r.overflow.scrollW <= r.overflow.innerW && r.overflow.bad.length === 0;
    r.checks.no_console_errors = !r.console.some(c => /^(error|pageerror)/.test(c) && !known(c));
    r.checks.no_request_blocked = !r.violations.some(v => !known(v));
    r.checks.no_missing_files = r.failed.filter(f => !known(f)).length === 0;
    r.checks.no_eval = !out.static.eval_or_function;
    if (run.stub) {
      const A = r.ask;
      r.checks.ask_call_is_well_formed = A.calls === 1 && A.problems.length === 0 && !A.has_cache && A.modelTier === 'default' && A.prompt_bytes < 262144;
      r.checks.ask_tools_ran = A.results.length === 9 && A.results.slice(0, 5).every(x => !x.error) && /not in vocabulary/.test((A.results[6] || {}).error || '') &&
        /unknown/.test((A.results[5] || {}).summary || '') && A.results.slice(7).every(x => !x.error && /"excluded"/.test(x.summary || ''));
      r.checks.ask_moved_the_scene = A.scene.view === 'claude' && A.scene.lit === A.scene.ai && A.scene.notes === 1 && A.scene.xray === true;
      r.checks.ask_streamed_the_answer = /Route: both/.test(A.answer || '') && A.tool_lines === 9 && A.failed_lines.length === 1;
      r.checks.ask_rate_limited_copy = /busy|usage limit/.test(r.ask_rate_limited || '');
      r.checks.ask_not_granted_turns_off = r.ask_not_granted.off === true && /not allowed|off/.test(r.ask_not_granted.answer + r.ask_not_granted.state);
    }
    r.ok = Object.values(r.checks).every(Boolean);
    out.ok = out.ok && r.ok; out.runs.push(r);
    console.error((r.ok ? 'PASS ' : 'FAIL ') + run.name + ' (scene in ' + r.boot_ms + ' ms) ' + JSON.stringify(r.checks));
  }
  await b.close(); srv.close();
  fs.writeFileSync(path.join(FR, 'live-report.json'), JSON.stringify(out, null, 1));
  process.stdout.write(JSON.stringify(out) + '\n');
  process.exit(out.ok ? 0 : 1);
})().catch(e => { console.error(e); process.stdout.write(JSON.stringify({ ok: false, error: String(e && e.stack || e) }) + '\n'); process.exit(1); });
