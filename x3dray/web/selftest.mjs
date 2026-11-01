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
  // names come back in every form the tools print them: a file through a repo alias, a session id, kinds of author
  if (pubAlias) {
    const fa = tools.concepts.execute({ file: pubAlias[0] + ':README.md' }), fd = tools.concepts.execute({ file: 'x3d_mcp:README.md' });
    const ba = tools.bridges.execute({ file: pubAlias[0] + ':README.md' }), bd = tools.bridges.execute({ file: 'x3d_mcp:README.md' });
    check('concepts and bridges: a file named through a repo alias is the same file', fa.matched === fd.matched && fd.matched > 0 && ba.matched === bd.matched && bd.matched > 0,
      `${fa.matched}/${fd.matched} ${ba.matched}/${bd.matched}`);
  }
  if (privName) {
    const pc = tools.concepts.execute({ file: privName + ':README.md' }), pbf = tools.bridges.execute({ file: privName + ':README.md' });
    check('concepts and bridges: a private repo:path answers counts only, not "not in the lattice"', pc.summary === 'private: counts only' && pbf.summary === 'private: counts only'
      && pc.matched === undefined && pbf.matched === undefined, `${pc.summary} / ${pbf.summary}`);
  }
  let unkFile = ''; try { tools.concepts.execute({ file: 'nosuchrepo:README.md' }); } catch (e) { unkFile = e.message; }
  check('concepts: a file in an unknown repo is refused', /not in vocabulary/.test(unkFile), unkFile.slice(0, 120));
  const ppS = Object.values(S.passage_points).flat().find(x => x.startsWith('session:'));
  if (ppS) {
    const bare = ppS.slice('session:'.length), n = v => tools.query_commits.execute({ session: v }).matched;
    check('query_commits: a session as the bridges and the digest print it finds its commits', n(ppS) === n(bare) && n('session ' + bare) === n(bare) && n(bare) > 0, `${n(ppS)} ${n(bare)}`);
  }
  let unkS = ''; try { tools.query_commits.execute({ session: 's-0000000000' }); } catch (e) { unkS = e.message; }
  check('query_commits: an unknown session is refused, not answered with zero', /no Claude session/.test(unkS), unkS.slice(0, 120));
  let unkW = ''; try { tools.query_commits.execute({ who: 'robots' }); } catch (e) { unkW = e.message; }
  check('query_commits: an unknown kind of author is refused, not answered with zero', /kinds of author/.test(unkW), unkW.slice(0, 120));
  const named = tools.concepts.execute({ who: 'HagopJay + Claude' }), keyed = tools.concepts.execute({ who: 'you,ai' });
  check('concepts: kinds as the tools print them ("HagopJay + Claude") are understood', named.files === keyed.files && named.kinds.join() === 'you,ai', `${named.files} ${keyed.files}`);
  const hc = BR.files.high_churn[0];
  if (hc) {
    const hr = tools.bridges.execute({ file: hc.id }).files.find(f => f.file === hc.id);
    check('bridges: a high-churn file says how many of its repo\'s usable commits changed it', hr && hr.high_churn_set_aside.set_aside.includes(`${hc.commits} of its repo's ${hc.of} usable commits`),
      hr && hr.high_churn_set_aside.set_aside);
  }
  const tieOk = B.repos.filter(r => r.visibility !== 'private').every(r => {   // a file tied with the last one shown is kept
    const bf = tools.bridges.execute({ repo: r.name }).bridge_files;
    if (!bf || !bf.every_file_kept) return true;
    return [['every_file_kept', 'count'], ['high_churn_set_aside', 'jaccard']].every(([k, v]) => {
      const rows = bf[k]; if (!rows.length) return true;
      const last = rows[rows.length - 1].betweenness, shown = new Set(rows.map(x => x.file));
      const tied = Object.entries(BR.files.variants[v].per_file).filter(([id, x]) => id.startsWith(r.name + ':') && x.betweenness === last && !shown.has(id.slice(r.name.length + 1)));
      return !tied.length || (rows.length >= 12 && /left out/.test(bf.shown));
    });
  });
  check('bridges: a file tied with the last bridge file shown is kept, or counted as left out', tieOk);
  // a file as the tie evidence prints it (path:line, repo:path:line) is that file, its line dropped; a path is never read as a repo
  const ids = Object.keys(F.files), pathOf = id => id.slice(id.indexOf(':') + 1);
  const uniq = ids.find(id => pathOf(id).includes('/') && ids.filter(x => pathOf(x) === pathOf(id)).length === 1);
  if (uniq) {
    const pl = tools.concepts.execute({ file: pathOf(uniq) + ':120' }), rpl = tools.bridges.execute({ file: uniq + ':12:3' });
    check('concepts and bridges: path:line and repo:path:line are that file, the line dropped', pl.matched === 1 && pl.files[0].file === uniq && pl.line_dropped === '120'
      && rpl.matched === 1 && rpl.files[0].file === uniq && rpl.read_as === uniq && rpl.line_dropped === '12:3', `${uniq}: ${pl.matched} ${pl.read_as} / ${rpl.matched} ${rpl.read_as}`);
  }
  if (uniq) {   // line ranges and lists, as the graded ties' evidence prints them
    const forms = ['700-774', '2,16-17,23', '80, 162'].map(l => [l, tools.concepts.execute({ file: pathOf(uniq) + ':' + l })]);
    check('concepts: a path with a line range or list is that file, the lines dropped', forms.every(([l, r]) => r.matched === 1 && r.files[0].file === uniq && r.line_dropped === l),
      forms.map(([l, r]) => `${l}: ${r.matched} ${r.line_dropped}`).join(' | '));
  }
  const slashAlias = Object.entries(B.vocab || {}).find(([k, v]) => k.includes('/') && ids.some(id => id.startsWith(v + ':')));
  if (slashAlias) {   // an alias may hold a '/' (owner/name): it still names the repo in a file
    const [al, rn] = slashAlias, id = ids.find(x => x.startsWith(rn + ':')), sa = tools.concepts.execute({ file: al + ':' + pathOf(id) }), sb = tools.bridges.execute({ file: al + ':' + pathOf(id) });
    check('concepts and bridges: a file named through an owner/name alias is that repo\'s file', sa.matched >= 1 && sa.files.some(f => f.file === id) && sb.matched >= 1, `${al}: ${sa.summary} / ${sb.summary}`);
  }
  const ownMiss = B.repos.filter(r => r.visibility !== 'private' && !(('hagopjay/' + r.name.toLowerCase()) in (B.vocab || {}))).filter(r => {
    let m = ''; try { tools.concepts.execute({ file: `hagopjay/${r.name}:README.md` }); } catch (e) { m = e.message; }
    return !m.startsWith(`not in vocabulary: "hagopjay/${r.name}". Nearest: ${r.name}`);   // the edit distance alone puts others first
  }).map(r => r.name);
  check('concepts: an owner/name that is not in the vocabulary is refused, its repo named first, for every repo', !ownMiss.length, ownMiss.join(', '));
  // every file in the lattice, in the forms the graded ties print it: "repo path", "repo: path" with lines, repo:file name, and a
  // line list that repeats its ':'
  const formMiss = [], repoOfId = id => id.slice(0, id.indexOf(':'));
  for (const id of ids) {
    const r = repoOfId(id), p = pathOf(id), base = p.slice(p.lastIndexOf('/') + 1);
    const forms = [`${r} ${p}`, `${r}: ${p}:700-774`, `${p}:7, :18`];
    if (base.trim() === base && ids.filter(x => repoOfId(x) === r && (pathOf(x) === base || pathOf(x).endsWith('/' + base))).length === 1) forms.push(`${r}:${base}`);   // a name that is its repo's only one, with no space at its ends
    for (const f of forms) {
      let res; try { res = tools.concepts.execute({ file: f, limit: 40 }); } catch (e) { res = {}; }
      if (!(res.files || []).some(x => x.file === id)) formMiss.push(f);
    }
  }
  check('concepts: every file in the lattice is read in the forms the ties print it ("repo path", "repo: path:700-774", "path:7, :18", repo:file name)', !formMiss.length,
    `${formMiss.length} missed: ${formMiss.slice(0, 4).join(' | ')}`);
  const pubRepo = repoOfId(ids[0]);
  let ro = ''; try { tools.concepts.execute({ file: pubRepo + ':' }); } catch (e) { ro = e.message; }
  const colon = tools.bridges.execute({ file: ':' });
  check('concepts and bridges: a repo with no path is refused, and ":" names no file', ro.startsWith('a file needs a path') && colon.matched === 0, `${ro.slice(0, 80)} / ${colon.summary}`);
  const bareRepo = B.repos.find(r => r.visibility !== 'private' && !ids.some(id => pathOf(id).toLowerCase().includes(r.name.toLowerCase())));
  if (bareRepo) {
    let br = ''; try { tools.bridges.execute({ file: bareRepo.name }); } catch (e) { br = e.message; }
    check('bridges: a repo name given as a file is refused, pointing to repo', br.startsWith(`"${bareRepo.name}" is a repo, not a file`), br.slice(0, 100));
  }
  const flat = tools.bridges.execute({}).clusters_with_no_bridge_file || [], sig = B.signals.find(s => s.id === 'bridges_files');
  check('bridges: a repo with a cluster of ten files and no file between others is named, in the tool and the digest', sig && flat.length === sig.values.with_cluster - sig.values.with_bridge
    && flat.every(x => tools.overview.execute({}).digest.includes(`${x.repo} (${x.largest_cluster})`)), JSON.stringify(flat));
  const inGraph = r => [BR.files.variants.count, BR.files.variants.jaccard].some(v => (v.largest_cluster_by_repo || {})[r]);
  const pubs = B.repos.filter(r => r.visibility !== 'private'), bfOf = r => tools.bridges.execute({ repo: r }).bridge_files || {};
  const outBad = pubs.filter(r => !inGraph(r.name)).filter(r => { const bf = bfOf(r.name); return !/^No usable commit touched a file of this repo/.test(bf.note || '') || 'largest_cluster' in bf; });
  check('bridges: a repo with no file in the co-change graph says so, with no cluster size and no claim about its cluster', !outBad.length,
    `${pubs.filter(r => !inGraph(r.name)).length} such repos; wrong: ${outBad.map(r => r.name).join(', ')}`);
  const noteBad = pubs.filter(r => inGraph(r.name)).filter(r => { const bf = bfOf(r.name);
    return ['every_file_kept', 'high_churn_set_aside'].some(k => !(bf[k] || []).length !== !!(bf.notes || {})[k]); });
  check('bridges: a repo with no file between others in one graph says so for that graph, and only then', !noteBad.length, noteBad.map(r => r.name).join(', '));
  const drawn = new Set((((B.layout || {}).bridges || {}).files || []).map(f => f.repo)).size, sv = (sig || {}).values || {};
  const wantRows = new Set(Object.keys(sv.per_repo || {}).concat(Object.keys(sv.hubs || {}))).size;
  check('bridges: the panel draws the rows the caption counts, up to its cap, and names the rest', sig && drawn === sv.rows && sv.rows <= sv.rows_max && sv.rows + (sv.rows_left_out || []).length === wantRows,
    `${drawn} drawn, rows ${sv.rows} of at most ${sv.rows_max}, ${(sv.rows_left_out || []).length} left out, ${wantRows} with a hub or bridge`);
  const np = tools.concepts.execute({ file: 'no/such/dir:file.md' });
  check('concepts: a path with a folder before its ":" is a path, not an unknown repo', np.summary === 'not in the lattice', np.summary);
  const every = tools.concepts.execute({ who: 'every file' });
  check('concepts: "every file", the name printed for the concept with no kinds, is every file', every.files === F.n_files && every.kinds.length === 0, `${every.files} of ${F.n_files}`);
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
