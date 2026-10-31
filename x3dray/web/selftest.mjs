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
  ['how_built', { topic: 'stage', name: 'concepts' }], ['how_built', { topic: 'stage', name: 'bridges' }],
  ['concepts', {}], ['concepts', { who: 'you,ai', limit: 40 }], ['concepts', { who: 'ai', limit: 40 }], ['concepts', { file: 'src', limit: 40 }],
  ['concepts', { repo: 'x3d_mcp', limit: 40 }], ['concepts', { area: 'x3d_mcp/src', limit: 40 }], ['concepts', { repo: 'x3d_ai' }],
  ['bridges', {}], ['bridges', { repo: 'x3d_mcp', limit: 30 }], ['bridges', { file: 'README', limit: 30 }], ['bridges', { between: 'x3d-blender-io,x3d.onto' }],
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

// 7. concepts and bridges: counts name their commits, private repos stay counts, a missing link is "no recorded session link"
if (B.concepts && B.bridges) {
  const C = B.concepts, F = C.files_by_author_class, BR = B.bridges, S = BR.sessions;
  const known = new Map(B.commits.filter(c => c.h).map(c => [c.h, c]));
  const sum = tools.concepts.execute({});
  check('concepts: the lattice is there and every file is in exactly one concept', sum.files_lattice.concepts.reduce((n, c) => n + c.only_these, 0) === F.n_files, F.n_files);
  check('concepts: the answer counts what it set aside', sum.excluded && Object.keys(sum.excluded).length > 0 && sum.mass_edits.commits === C.mass_edits.commits, JSON.stringify(sum.excluded));
  const both = tools.concepts.execute({ who: 'claude,hagopjay' });
  const exp = Object.values(F.files).filter(r => r.by.you && r.by.ai).length;
  check('concepts: who=claude,hagopjay counts the files touched by both', both.files === exp && both.kinds.join() === 'you,ai', `${both.files} vs ${exp}`);
  const witnessed = both.top_files.every(f => Object.entries(f.kinds).every(([k, v]) => v.shas.every(h => known.has(h) && known.get(h).c === k)));
  check('concepts: every sha it cites is a commit of that kind', witnessed);
  const priv = tools.concepts.execute({ repo: 'x3d_ai' });
  check('concepts: a private repo answers counts only', priv.private === true && !JSON.stringify(priv).match(/"shas"|"file"/), JSON.stringify(priv).slice(0, 160));
  const none = tools.concepts.execute({ file: 'no/such/file.zzz' });
  check('concepts: a file not in the lattice is not called untouched', none.matched === 0 && /not the same as never touched/.test(none.note), none.note);
  const rule = tools.concepts.execute({ area: 'x3d_mcp/src' });
  check('concepts: every rule names its exceptions', rule.rules.every(r => r.exceptions_count === r.of - r.holds_in), JSON.stringify(rule.rules[0]));
  let bad = ''; try { tools.concepts.execute({ who: 'robots' }); } catch (e) { bad = e.message; }
  check('concepts: an unknown kind of author is refused', /kinds of author/.test(bad), bad);
  const pair = S.repo_pairs[0];
  const btw = tools.bridges.execute({ between: `${pair.a},${pair.b}` });
  check('bridges: two repos with a shared session are linked, with the sessions named', btw.recorded_link === true && btw.sessions_in_both.sessions.length === pair.sessions.length, JSON.stringify(btw).slice(0, 200));
  const unl = S.no_recorded_link[0];
  if (unl) {
    const u = tools.bridges.execute({ between: unl.join(',') });
    check('bridges: an unlinked pair says no recorded session link, never no link', u.recorded_link === false && /no recorded session link/.test(u.summary) && /not evidence of no link/.test(u.note), u.note);
  }
  const via = Object.entries(S.passage_points)[0];
  if (via) {
    const [a, b] = via[0].split(' -> ');
    check('bridges: passage points come back for the pair', JSON.stringify(tools.bridges.execute({ between: `${a},${b}` }).on_every_path.session_graph) === JSON.stringify(via[1]), via[0]);
  }
  const thr = (S.linked_through_others || [])[0];
  if (thr) {
    const t = tools.bridges.execute({ between: thr.join(',') });
    check('bridges: a pair joined only through other repos says so', t.recorded_link === true && /only through other repos/.test(t.how) && t.sessions_in_both === null, JSON.stringify(t).slice(0, 200));
  }
  const npairs = S.repo_pairs.length + (S.linked_through_others || []).length + S.no_recorded_link.length;
  check('bridges: every pair of repos is direct, through others, or no recorded session link', npairs === S.n_repo_pairs, `${npairs} of ${S.n_repo_pairs}`);
  check('bridges: between answers carry the session graph\'s own set-asides', /no session trailer/.test(JSON.stringify(btw.excluded)) && !/mass edits/.test(JSON.stringify(btw.excluded)), JSON.stringify(btw.excluded));
  const cut = btw.sessions_in_both;
  check('bridges: a cut sha list says how many it left out', cut.commits === cut.shas.length + (cut.shas_omitted || 0), JSON.stringify({ c: cut.commits, n: cut.shas.length, o: cut.shas_omitted }));
  const privName = (B.repos.find(r => r.visibility === 'private') || {}).name;
  if (privName) {
    const pw = tools.concepts.execute({ who: 'ai', repo: privName });
    check('concepts: a private repo with a filter answers counts only, not zero', pw.summary === 'private: counts only' && pw.private_repos && pw.files === undefined, JSON.stringify(pw).slice(0, 200));
    const pa = tools.concepts.execute({ area: privName + '/src' });
    check('concepts: a private repo\'s area is unknown, not ruleless', pa.summary === 'private: counts only', pa.summary);
    const pb = tools.bridges.execute({ repo: privName });
    check('bridges: a private repo\'s bridge files are unknown, not none', /unknown/.test(JSON.stringify(pb.bridge_files)), JSON.stringify(pb.bridge_files));
    const pf = tools.bridges.execute({ file: 'README', repo: privName });
    check('bridges: a private repo with a file filter answers counts only, not zero', pf.summary === 'private: counts only' && pf.private_repos && pf.matched === undefined
      && pf.private_repos[0].sessions_reaching_it_scope && /trailer/.test(JSON.stringify(pf.private_repos[0].sessions_reaching_it_scope)), JSON.stringify(pf).slice(0, 200));
    const alias = Object.entries(B.vocab || {}).find(([k, v]) => v === privName && k !== privName.toLowerCase());
    if (alias) {
      const pv = tools.concepts.execute({ area: alias[0] + '/src' });
      check('concepts: an alias of a private repo\'s area answers counts only', pv.summary === 'private: counts only' && pv.repo === privName, `${alias[0]}: ${pv.summary}`);
    }
  }
  const pubAlias = Object.entries(B.vocab || {}).find(([k, v]) => v === 'x3d_mcp' && k !== 'x3d_mcp');
  if (pubAlias) {
    const viaAlias = tools.concepts.execute({ area: pubAlias[0] + '/src' }), direct = tools.concepts.execute({ area: 'x3d_mcp/src' });
    check('concepts: an area named through an alias finds the same rules', viaAlias.matched === direct.matched && direct.matched > 0 && viaAlias.area === 'x3d_mcp/src', `${pubAlias[0]}: ${viaAlias.matched} vs ${direct.matched}`);
  }
  const slash = tools.concepts.execute({ area: 'x3d_mcp/src/' }), upper = tools.concepts.execute({ area: 'X3D_MCP/SRC' });
  const direct2 = tools.concepts.execute({ area: 'x3d_mcp/src' });
  check('concepts: an area with a trailing slash or in capitals finds the same rules', slash.matched === direct2.matched && upper.matched === direct2.matched && upper.area === 'x3d_mcp/src',
    `${slash.matched} ${upper.matched} ${direct2.matched}`);
  let noRepo = ''; try { tools.concepts.execute({ area: '/src' }); } catch (e) { noRepo = e.message; }
  check('concepts: an area with no repo is refused', /starts with its repo/.test(noRepo), noRepo);
  const um = tools.concepts.execute({ who: 'unmapped' });
  check('concepts: "unmapped", the digest\'s word for upstream, is understood', um.kinds && um.kinds.join() === 'upstream', JSON.stringify(um.kinds));
  if (pubAlias) {
    const dd = tools.concepts.execute({ repo: 'x3d_mcp,' + pubAlias[0] });
    check('concepts: two aliases of one repo name it once', dd.repo === 'x3d_mcp' && !dd.repos, dd.summary);
  }
  const hubsA = tools.bridges.execute({}).author_graph;
  check('bridges: the author hubs say how many there are', hubsA.hubs_total === BR.authors.betweenness.filter(x => x.id.startsWith('author:')).length &&
    hubsA.hubs.length + (hubsA.hubs_omitted || 0) === hubsA.hubs_total, JSON.stringify({ n: hubsA.hubs.length, t: hubsA.hubs_total }));
  let unk = ''; try { tools.concepts.execute({ area: 'nosuchrepo/src' }); } catch (e) { unk = e.message; }
  check('concepts: an area in an unknown repo is refused, not answered "no rule"', /not in vocabulary/.test(unk), unk.slice(0, 120));
  check('concepts: a cut rule list says how many it held and left out', sum.rules_total === C.areas.rules.length && sum.rules.length + (sum.rules_omitted || 0) === sum.rules_total,
    `${sum.rules.length} + ${sum.rules_omitted} of ${sum.rules_total}`);
  const one = tools.concepts.execute({ repo: 'x3d_mcp', limit: 1 });
  check('concepts: a repo\'s counts carry the repo lattice\'s own scope', one.commits_by_class_scope && one.commits_by_class_scope.excluded && /not a merge/.test(one.commits_by_class_scope.reads)
    && one.rules.length + (one.rules_omitted || 0) === one.rules_total, JSON.stringify(one.commits_by_class_scope));
  check('bridges: between answers scope the author graph apart from the session graph', btw.excluded.author_graph && btw.reads.author_graph && btw.excluded.session_graph && btw.reads.session_graph,
    JSON.stringify(btw.excluded));
  const two = tools.concepts.execute({ repo: 'x3d_mcp,x3d.ML' });
  check('concepts: repo takes several repos', two.repos && two.repos.length === 2, two.summary);
  const twoB = tools.bridges.execute({ repo: 'x3d_mcp,x3d.ML' });
  check('bridges: repo takes several repos', twoB.repos && twoB.repos.length === 2, twoB.summary);
  const big = Object.entries(F.files).find(([, r]) => Object.values(r.by).some(x => x.length > 12));
  if (big) {
    const fr = tools.concepts.execute({ file: big[0] }).files.find(f => f.file === big[0]);
    check('concepts: every count equals its shas plus the ones left out', Object.values(fr.kinds).every(k => k.commits === k.shas.length + (k.shas_omitted || 0)), big[0]);
  }
  const shasOk = Object.values(S.sessions).every(rec => rec.shas.every(h => known.has(h)));
  check('bridges: every session cites real commits', shasOk);
  const privEdges = BR.authors.edges.filter(e => e.private);
  check('bridges: private author edges carry counts only', privEdges.every(e => e.shas === null && e.commits > 0), privEdges.length);
  check('the digest carries concepts and bridges', /CONCEPTS/.test(tools.overview.execute({}).digest) && /BRIDGES/.test(tools.overview.execute({}).digest));
}

// 8. sample's limits on tool definitions: description at most 1 KB, input schema at most 4 KB
for (const t of makeTools(B, { show() {}, highlight() {}, note() {}, colorBy() {} })) {
  check(`${t.name} definition fits`, Buffer.byteLength(t.description) <= 1024 && size(t.inputSchema) <= 4096, `${Buffer.byteLength(t.description)} / ${size(t.inputSchema)}`);
}

// 9. the MCP server, over stdio
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
    check('mcp tools/list has the data tools and no scene tools', names.includes('traverse') && names.includes('overview') && names.includes('concepts') && names.includes('bridges') && !names.includes('show'), names.join(','));
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
