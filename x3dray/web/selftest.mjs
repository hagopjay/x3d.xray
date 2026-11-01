// The tools gate: run every data tool against a bundle with fixed questions, check each result's size and a few answers that
// code can check, then talk to the MCP server over stdio the way a client would. Prints one JSON object; exit 1 on a failure.
//   node x3dray/web/selftest.mjs --bundle out/<family>/bundle.json
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { makeTools, resolveRepo, LIMIT_BYTES } from './tools.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const arg = k => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : null; };
const bundlePath = arg('--bundle');
const B = JSON.parse(fs.readFileSync(bundlePath, 'utf8'));
const tools = Object.fromEntries(makeTools(B).map(t => [t.name, t]));
const checks = [];
const check = (name, ok, detail) => checks.push({ name, ok: !!ok, detail: detail === undefined ? undefined : String(detail).slice(0, 300) });
const size = v => Buffer.byteLength(JSON.stringify(v), 'utf8');

// 1. every tool, with inputs that stress its size; nothing may pass the cap
const CALLS = [
  ['overview', {}], ['query_commits', {}], ['query_commits', { who: 'ai', sort: 'size', limit: 40 }], ['query_commits', { repo: 'x3d_ai', limit: 40 }],
  ['activity', { by: 'day' }], ['activity', { by: 'hour', who: 'you,ai' }], ['activity', { by: 'week' }], ['top_files', { limit: 40 }],
  ['ties', { limit: 30 }], ['ties', { source: 'curated', limit: 30 }], ['traverse', { from: 'x3d_mcp', depth: 3 }],
  ['traverse', { from: 'x3d-blender-io', to: 'x3d.onto' }], ['signals', {}], ['signals', { id: 'pins' }],
  ...['pipeline', 'gates', 'receipt', 'self_in_data', 'claude', 'limits', 'spec', 'identity'].map(t => ['how_built', { topic: t }]),
  ['how_built', { topic: 'signal_code', name: 'busiest_day' }], ['how_built', { topic: 'module', name: 'tools.js' }],
];
for (const [name, input] of CALLS) {
  try {
    const r = tools[name].execute(input);
    check(`${name} ${JSON.stringify(input)} fits`, size(r) <= LIMIT_BYTES + 2000, size(r) + ' bytes');
  } catch (e) { check(`${name} ${JSON.stringify(input)} runs`, false, e.message); }
}

// 2. answers code can check
const own = B.commits.length, ai = B.commits.filter(c => c.c === 'ai').length;
check('query_commits counts every own commit', tools.query_commits.execute({}).matched === own, own);
check('query_commits who=claude is the ai class', tools.query_commits.execute({ who: 'claude' }).matched === ai, ai);
const days = tools.activity.execute({ by: 'day' });
check('activity buckets add up', days.buckets.reduce((s, b) => s + b.total, 0) === own);
const priv = B.repos.filter(r => r.visibility === 'private').map(r => r.name);
const privRows = priv.length ? tools.query_commits.execute({ repo: priv[0], limit: 40 }).rows : [];
check('private repos give no sha or subject', privRows.every(r => !r.sha && !r.subject), priv.join(','));

// 3. vocabulary (H02): aliases resolve, unknown names are refused with the nearest names
const fx = [['x3d.ai', 'x3d_ai'], ['.prov', 'x3d.ML'], ['blender io', 'x3d-blender-io'], ['the fork', 'x3d_mcp'], ['X3D_MCP', 'x3d_mcp'], ['xray', 'x3d.xray']];
for (const [q, want] of fx) {
  let got; try { got = resolveRepo(B, q); } catch (e) { got = e.message; }
  check(`vocabulary: "${q}" is ${want}`, got === want, got);
}
let refusal = '';
try { resolveRepo(B, 'x3d.prov.v2'); } catch (e) { refusal = e.message; }
check('vocabulary: an unknown name is refused, with the nearest names', /^not in vocabulary/.test(refusal) && /Nearest:/.test(refusal), refusal);

// 4. traverse (H16): a path the lineage guarantees, and the route is named
const tr = tools.traverse.execute({ from: 'x3d-blender-io', to: 'x3d.onto' });
check('traverse finds a path from x3d-blender-io to x3d.onto', tr.hops >= 1 && tr.paths.length > 0, JSON.stringify(tr).slice(0, 200));
check('traverse names its route', tr.route === 'traverse');

// 5. unknown is not zero (H11): a repo whose tree was not measured never answers zero files
const unmeasured = B.repos.filter(r => r.coverage && r.coverage.tree === 'not harvested').map(r => r.name);
if (unmeasured.length) {
  const tf = tools.top_files.execute({ repo: unmeasured[0] });
  check(`top_files for ${unmeasured[0]} answers unknown, not zero`, tf.unknown && tf.unknown.length === 1, JSON.stringify(tf).slice(0, 160));
  check('the digest marks unmeasured trees unknown', tools.overview.execute({}).digest.includes('unknown'));
}

// 6. witnesses (H03): every tie the tools can return carries evidence
check('every machine tie has a witness', B.ties.machine.every(t => t.evidence && t.evidence.length > 0), B.ties.machine.length);
check('every graded tie has a witness', B.ties.curated.every(t => t.evidence && t.evidence.length > 0), B.ties.curated.length);

// 7. sample's limits on tool definitions: description at most 1 KB, input schema at most 4 KB
for (const t of makeTools(B, { show() {}, highlight() {}, note() {}, colorBy() {} })) {
  check(`${t.name} definition fits`, Buffer.byteLength(t.description) <= 1024 && size(t.inputSchema) <= 4096, `${Buffer.byteLength(t.description)} / ${size(t.inputSchema)}`);
}

// 8. the MCP server, over stdio
async function mcpSmoke() {
  const p = spawn(process.execPath, [path.join(HERE, 'mcp.mjs'), '--bundle', bundlePath], { stdio: ['pipe', 'pipe', 'pipe'] });
  let buf = '';
  const waiting = new Map();
  p.stdout.on('data', d => {
    buf += d;
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 1);
      if (!line.trim()) continue;
      const msg = JSON.parse(line);
      if (waiting.has(msg.id)) { waiting.get(msg.id)(msg); waiting.delete(msg.id); }
    }
  });
  let id = 0;
  const call = (method, params) => new Promise((res, rej) => {
    const k = ++id; waiting.set(k, res);
    p.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: k, method, params }) + '\n');
    setTimeout(() => rej(new Error(method + ' timed out')), 8000);
  });
  try {
    const init = await call('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'x3dray-selftest', version: '0' } });
    check('mcp initialize', init.result && init.result.serverInfo, JSON.stringify(init.result || init.error).slice(0, 160));
    p.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
    const list = await call('tools/list', {});
    const names = (list.result && list.result.tools || []).map(t => t.name);
    check('mcp tools/list has the data tools and no scene tools', names.includes('traverse') && names.includes('overview') && !names.includes('show'), names.join(','));
    const ov = await call('tools/call', { name: 'query_commits', arguments: { repo: 'x3d.ai', limit: 2 } });
    check('mcp tools/call resolves an alias', ov.result && !ov.result.isError && /x3d_ai/.test(ov.result.content[0].text), JSON.stringify(ov.result || ov.error).slice(0, 160));
    const bad = await call('tools/call', { name: 'query_commits', arguments: { repo: 'nonsense-repo' } });
    check('mcp tools/call refuses an unknown name as a tool error', bad.result && bad.result.isError && /not in vocabulary/.test(bad.result.content[0].text), JSON.stringify(bad.result || bad.error).slice(0, 160));
  } catch (e) { check('mcp smoke', false, e.message); }
  p.kill();
}
await mcpSmoke();

const failed = checks.filter(c => !c.ok);
console.log(JSON.stringify({ ok: failed.length === 0, checks: checks.length, failed, all: checks }, null, 1));
process.exit(failed.length ? 1 : 0);
