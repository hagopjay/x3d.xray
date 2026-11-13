/* DOM test of the notebook panel (x3dray/web/notebook.js) on the files the page build copies, in jsdom.
   usage: node test_notebook_dom.cjs <repo root> <bundle.json>
   Not a GPU or browser claim: jsdom has no layout, and Node supplies SHA-256. It checks what the panel says and does. */
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const assert = require('node:assert/strict');
const { webcrypto } = require('node:crypto');
const { pathToFileURL } = require('node:url');
const { JSDOM } = require('jsdom');

const root = process.argv[2], B0 = fs.readFileSync(process.argv[3], 'utf8');
const checks = [];
const test = async (name, fn) => { await fn(); checks.push(name); };

// the page build's copy: same files, one directory, module type
const site = fs.mkdtempSync(path.join(os.tmpdir(), 'nbsite-'));
fs.copyFileSync(path.join(root, 'x3dray/web/tools.js'), path.join(site, 'tools.js'));
fs.copyFileSync(path.join(root, 'x3dray/web/notebook.js'), path.join(site, 'notebook.js'));
fs.copyFileSync(path.join(root, 'x3dray/proof_web/investigation.js'), path.join(site, 'investigation.js'));
fs.writeFileSync(path.join(site, 'package.json'), '{"type":"module"}');

const errors = [];
function freshDom() {
  const dom = new JSDOM('<!doctype html><section id="notebook"></section>', { url: 'http://localhost/', pretendToBeVisual: true });
  const w = dom.window;
  w.HTMLAnchorElement.prototype.click = function () { this.__clicked = true; };
  w.addEventListener('error', e => errors.push(e.message));
  globalThis.document = w.document; globalThis.window = w;
  globalThis.Event = w.Event;
  return w;
}
const $ = (sel, scope) => (scope || document).querySelector(sel);
const $$ = (sel, scope) => Array.from((scope || document).querySelectorAll(sel));
const text = el => (el.textContent || '').replace(/\s+/g, ' ').trim();

(async () => {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
  const NB = await import(pathToFileURL(path.join(site, 'notebook.js')).href);
  const T = await import(pathToFileURL(path.join(site, 'tools.js')).href);
  const open = async (B, extra) => { const w = freshDom(); let shared = null;
    const nb = await NB.startNotebook(B, { root: $('#notebook'), scene: null, onTools: t => { shared = t; }, ...(extra || {}) });
    await nb.settled(); return { w, nb, shared: () => shared }; };
  const settle = async nb => { await nb.settled(); await new Promise(r => setTimeout(r, 0)); await nb.settled(); };
  const click = async (nb, el) => { el.click(); await settle(nb); };
  const status = () => text($('[data-nb="status"]')), msg = () => text($('[data-nb="msg"]'));
  const entries = () => $$('[data-nb="entries"] > li');
  const classes = JSON.parse(B0).eda.classes, [PA, PB] = classes.pair;
  const compareForm = async (nb, a, b, measure) => {
    const f = $('form[data-nb="form"]');
    f.elements.a_who.value = a.who || ''; f.elements.b_who.value = b.who || '';
    f.elements.a_hour.value = a.hour === undefined ? '' : String(a.hour); f.elements.b_hour.value = b.hour === undefined ? '' : String(b.hour);
    f.elements.a_repo.value = a.repo || ''; f.elements.b_repo.value = b.repo || '';
    f.elements.measure.value = measure; f.elements.fork.value = 'all';
    f.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await settle(nb);
  };

  let saved;
  await test('opens in chain mode with the twelve declared looks and a zero count', async () => {
    const { nb } = await open(JSON.parse(B0));
    assert.equal(nb.mode, 'chain');
    assert.equal($$('tbody tr[data-id]').length, 12);
    assert.match(status(), /12 declared looks, 0 taken\. 0 looks asked beyond them\./);
    assert.match(status(), /counted over 12/);
    assert.equal(entries().length, 0);
    assert.match(text($('.nbfoot')), /does not prove that a look was computed, shown or read/);
  });

  await test('taking a declared look adds one entry, once, with the tool sentence', async () => {
    const { nb } = await open(JSON.parse(B0));
    const b = $('button[data-id="contrast:lines"]');
    await click(nb, b);
    assert.equal(entries().length, 1);
    assert.match(status(), /12 declared looks, 1 taken\. 0 looks asked/);
    const sentence = JSON.parse(JSON.stringify(nb.tools.find(t => t.name === 'eda').execute({ topic: 'finding', id: 'contrast:lines' }))).finding.text;
    assert.equal(text($('.nbtext', entries()[0])), sentence);
    assert.equal(b.textContent, 'Taken'); assert.equal(b.getAttribute('aria-pressed'), 'true');
    await click(nb, b);
    assert.equal(entries().length, 1); assert.match(msg(), /already in the notebook/);
    assert.match(status(), /1 taken\. 0 looks asked/);
  });

  await test('a comparison is ungraded, counted, and asking it again changes nothing', async () => {
    const { nb } = await open(JSON.parse(B0));
    await compareForm(nb, { who: PA }, { who: PB }, 'lines');
    assert.equal(entries().length, 1);
    assert.match(status(), /1 look asked beyond them\./); assert.match(status(), /counted over 13/);
    assert.ok($('.chip', entries()[0].querySelector('.nbhead')) && /ungraded/.test(text(entries()[0])));
    const first = text($('.nbtext', entries()[0]));
    assert.match(first, /q|p/);
    await compareForm(nb, { who: PA }, { who: PB }, 'lines');
    assert.equal(entries().length, 1); assert.match(msg(), /already in the notebook/);
    assert.equal(text($('.nbtext', entries()[0])), first); assert.match(status(), /1 look asked beyond them\./);
    await compareForm(nb, { who: PA }, { who: PB }, 'files');
    assert.equal(entries().length, 2); assert.match(status(), /2 looks asked beyond them\./); assert.match(status(), /counted over 14/);
  });

  await test('a refused comparison is shown as the tool refused it and counts nothing', async () => {
    const { nb } = await open(JSON.parse(B0));
    await compareForm(nb, { who: PA, hour: 3, repo: JSON.parse(B0).repos[0].name }, { who: PA, hour: 3, repo: JSON.parse(B0).repos[0].name }, 'lines');
    assert.equal(entries().length, 0);
    assert.ok($('[data-nb="msg"]').classList.contains('bad')); assert.ok(msg().length > 10);
    assert.match(status(), /0 looks asked beyond them\./);
  });

  await test('looks Claude takes through the shared tool set are counted and listed here', async () => {
    const { nb, shared } = await open(JSON.parse(B0));
    assert.equal(shared(), nb.tools);
    nb.tools.find(t => t.name === 'eda').execute({ topic: 'slice', who: PA, measure: 'lines' });
    await nb.refresh(); await settle(nb);
    assert.equal(entries().length, 1); assert.match(text(entries()[0]), /taken outside this panel/);
    assert.match(status(), /1 look asked beyond them\./);
  });

  await test('recompute says identical and names the digest', async () => {
    const { nb } = await open(JSON.parse(B0));
    await click(nb, $('button[data-act="recompute"]'));
    assert.match(msg(), /identical/); assert.match(msg(), /sha256 [0-9a-f]{16}/);
    assert.equal(entries().length, 0);
  });

  await test('notes are kept per look and are not part of the answer', async () => {
    const { nb } = await open(JSON.parse(B0));
    await click(nb, $('button[data-id="contrast:hour4"]'));
    const ta = $('textarea[data-note]'); ta.value = 'my own thought'; ta.dispatchEvent(new window.Event('input', { bubbles: true }));
    await nb.refresh(); await settle(nb);
    assert.equal($('textarea[data-note]').value, 'my own thought');
    assert.doesNotMatch(text($('.nbtext', entries()[0])), /my own thought/);
  });

  await test('export carries chain, answers and note; a new notebook empties; import replays it', async () => {
    const B = JSON.parse(B0);
    const { nb } = await open(B);
    await click(nb, $('button[data-id="contrast:lines"]'));
    await compareForm(nb, { who: PA }, { who: PB }, 'hour4');
    const ta = $('textarea[data-note]'); ta.value = 'kept'; ta.dispatchEvent(new window.Event('input', { bubbles: true }));
    await click(nb, $('button[data-act="export"]'));
    saved = $('[data-nb="io"]').value;
    const doc = JSON.parse(saved);
    assert.equal(doc.format, 'x3dray.notebook.v1'); assert.equal(doc.results.length, 2); assert.equal(doc.notes.length, 1);
    assert.equal(doc.log.events.filter(r => r.event.action === 'take_look').length, 2);
    assert.match(doc.not_proven, /does not prove/);
    const before = status();
    await click(nb, $('button[data-act="clear"]')); assert.equal(entries().length, 2); assert.match(msg(), /empties the notebook/);   // armed
    await click(nb, $('button[data-act="clear"]')); assert.equal(entries().length, 0); assert.match(status(), /0 taken\. 0 looks asked/);
    $('[data-nb="io"]').value = saved;
    await click(nb, $('button[data-act="import"]'));
    assert.match(msg(), /2 looks replayed, 2 answers identical/);
    assert.equal(entries().length, 2); assert.equal(status().replace(/head [0-9a-f]+/, ''), before.replace(/head [0-9a-f]+/, ''));
    assert.equal($('textarea[data-note]').value, 'kept');
  });

  await test('import refuses a notebook whose chain was altered, whose answer differs, or that is for other data', async () => {
    const B = JSON.parse(B0);
    const attempt = async (mutate, expect) => {
      const { nb } = await open(B);
      const doc = JSON.parse(saved); mutate(doc);
      $('[data-nb="io"]').value = JSON.stringify(doc);
      await click(nb, $('button[data-act="import"]'));
      assert.ok($('[data-nb="msg"]').classList.contains('bad'), 'refusal shown as a refusal');
      assert.match(msg(), expect); assert.equal(entries().length, 0); assert.match(status(), /0 taken\. 0 looks asked/);
    };
    await attempt(d => { d.log.events.find(r => r.event.action === 'take_look' && r.event.kind === 'compare').event.selector.measure = 'lines'; }, /refused/);
    await attempt(d => { d.results[0].sha256 = '0'.repeat(64); }, /replay to a different answer/);
    await attempt(d => { d.log.fixture_digest = 'f'.repeat(64); }, /refused/);
    await attempt(d => { d.format = 'other'; }, /not an x3d-ray notebook/);
    const { nb } = await open(B);
    $('[data-nb="io"]').value = '{nope'; await click(nb, $('button[data-act="import"]')); assert.match(msg(), /not JSON/);
    $('[data-nb="io"]').value = 'x'.repeat(1048577); await click(nb, $('button[data-act="import"]')); assert.match(msg(), /too large/);
  });

  await test('import into a notebook that already has looks is refused', async () => {
    const { nb } = await open(JSON.parse(B0));
    await click(nb, $('button[data-id="contrast:files"]'));
    $('[data-nb="io"]').value = saved; await click(nb, $('button[data-act="import"]'));
    assert.match(msg(), /already has looks/); assert.equal(entries().length, 1);
  });

  await test('markup in a note or a repo name is shown as text, never as markup', async () => {
    const B = JSON.parse(B0); B.repos[0].name = '<img src=x onerror=1>repo';
    const { nb } = await open(B);
    await nb.compare({ a: { who: PA }, b: { who: PB }, measure: 'repo', fork: 'all' }); await settle(nb);
    const ta = $('textarea[data-note]'); ta.value = '<script>1</script>'; ta.dispatchEvent(new window.Event('input', { bubbles: true }));
    await nb.refresh(); await settle(nb);
    assert.equal($$('#notebook img, #notebook script').length, 0);
  });

  await test('without secure hashing the notebook counts in memory, says so, and refuses export', async () => {
    const real = globalThis.crypto;
    Object.defineProperty(globalThis, 'crypto', { value: {}, configurable: true });
    try {
      const { nb } = await open(JSON.parse(B0));
      assert.equal(nb.mode, 'memory');
      await click(nb, $('button[data-id="contrast:lines"]'));
      assert.match(status(), /1 taken/); assert.match(status(), /Counted in memory only: this browser context has no secure hashing/);
      await click(nb, $('button[data-act="export"]')); assert.match(msg(), /needs the hash chain/);
    } finally { Object.defineProperty(globalThis, 'crypto', { value: real, configurable: true }); }
  });

  await test('a bundle with no stored looks hides the panel and still hands over a tool set', async () => {
    const B = JSON.parse(B0); delete B.eda;
    const { nb, w, shared } = await open(B);
    assert.equal(nb.mode, 'none'); assert.equal($('#notebook').hidden, true);
    assert.ok(Array.isArray(shared()) && shared().length > 0);
  });

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ ok: true, checks }));
  fs.rmSync(site, { recursive: true, force: true });
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
