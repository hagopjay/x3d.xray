// x3d-ray tools: the functions the page's Claude calls and the MCP server serves. One implementation for both.
// makeTools(bundle, scene?) returns [{name, description, inputSchema, execute}]. Data tools read the bundle only.
// Scene tools (show, highlight, note, color_by) exist only when a scene API is passed, which only the page does.
// Every result is plain data and stays under the 32 KB a tool result may carry.

export const LIMIT_BYTES = 30000;
export const CLASS_NAMES = { you: 'HagopJay (his accounts)', ai: 'Claude', bot: 'bots', other: 'other identities', upstream: 'unmapped or upstream' };
const WHO_ALIASES = { claude: 'ai', ai: 'ai', assistant: 'ai', hagopjay: 'you', you: 'you', human: 'you', owner: 'you', hagsmac: 'you',
  bot: 'bot', bots: 'bot', other: 'other', upstream: 'upstream' };

const day = t => new Date(t * 1000).toISOString().slice(0, 10);
const hm = t => new Date(t * 1000).toISOString().slice(11, 16);
const str = v => (v === undefined || v === null ? '' : String(v)).trim();
const num = (v, d) => { const n = Number(v); return Number.isFinite(n) ? n : d; };
const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
const list = v => (Array.isArray(v) ? v : (str(v) ? str(v).split(',') : [])).map(s => String(s).trim()).filter(Boolean);

// Keep a result under the cap by shortening its longest arrays, and say so.
export function cap(obj, limit = LIMIT_BYTES) {
  let s = JSON.stringify(obj);
  if (s.length <= limit) return obj;
  const out = JSON.parse(s);
  const arrays = [];
  (function walk(o, path) {
    if (Array.isArray(o)) { arrays.push({ o, path }); o.forEach((x, i) => walk(x, path + '[' + i + ']')); }
    else if (o && typeof o === 'object') Object.keys(o).forEach(k => walk(o[k], path + '.' + k));
  })(out, '');
  arrays.sort((a, b) => JSON.stringify(b.o).length - JSON.stringify(a.o).length);
  let guard = 0;
  while (JSON.stringify(out).length > limit && guard++ < 400) {
    const big = arrays.find(a => a.o.length > 1);
    if (!big) break;
    big.o.length = Math.max(1, Math.floor(big.o.length * 0.7));
    arrays.sort((a, b) => JSON.stringify(b.o).length - JSON.stringify(a.o).length);
  }
  out.truncated = 'Shortened to stay under the tool result limit; ask for less (a repo, a date range, a smaller limit).';
  return out;
}

function lev(a, b) {
  const m = a.length, n = b.length, d = Array.from({ length: m + 1 }, (_, i) => [i, ...Array(n).fill(0)]);
  for (let j = 1; j <= n; j++) d[0][j] = j;
  for (let i = 1; i <= m; i++) for (let j = 1; j <= n; j++) d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return d[m][n];
}

// Every repo name goes through the family's vocabulary: aliases resolve, an unknown term is refused with the nearest names.
export function resolveRepo(B, name) {
  const k = str(name).toLowerCase();
  if (!k) return '';
  const v = (B.vocab || {})[k];
  if (v) return v;
  const exact = B.repos.find(r => r.name.toLowerCase() === k);
  if (exact) return exact.name;
  const names = Object.keys(B.vocab || {}).concat(B.repos.map(r => r.name.toLowerCase()));
  const near = [...new Set(names)].map(n => [lev(k, n), n]).sort((a, b) => a[0] - b[0]).slice(0, 3).map(x => (B.vocab || {})[x[1]] || x[1]);
  throw new Error(`not in vocabulary: "${name}". Nearest: ${[...new Set(near)].join(', ')}. Known repos: ${B.repos.map(r => r.name).join(', ')}`);
}
const repoList = (B, v) => list(v).map(n => resolveRepo(B, n));

function whoOf(v) {
  const w = str(v).toLowerCase();
  return w ? (WHO_ALIASES[w] || w) : '';
}

function commitFilter(B, q) {
  const repos = repoList(B, q.repo).map(r => r.toLowerCase());
  const who = list(q.who).map(whoOf);
  const from = str(q.from), to = str(q.to), text = str(q.text).toLowerCase(), sha = str(q.sha).toLowerCase(), sess = str(q.session);
  const dayEq = str(q.day);
  return c => {
    if (repos.length && !repos.includes(c.r.toLowerCase())) return false;
    if (who.length && !who.includes(c.c)) return false;
    const d = day(c.t);
    if (dayEq && d !== dayEq) return false;
    if (from && d < from) return false;
    if (to && d > to) return false;
    if (text && !(c.sub || '').toLowerCase().includes(text)) return false;
    if (sha && !(c.h || '').startsWith(sha)) return false;
    if (sess && !(c.s || []).includes(sess)) return false;
    if (q.merges === true && !c.m) return false;
    if (q.merges === false && c.m) return false;
    if (q.side_branch === true && c.b) return false;
    if (q.trailer === true && !c.tr) return false;
    return true;
  };
}

export function matchCommits(B, q) {
  return B.commits.filter(commitFilter(B, q || {}));
}

function countBy(rows, key) {
  const o = {};
  rows.forEach(r => { const k = key(r); o[k] = (o[k] || 0) + 1; });
  return o;
}

function commitRow(B, c) {
  const r = { repo: c.r, day: day(c.t), time: hm(c.t), who: c.w, class: c.c, lines: (c.a || 0) + (c.d || 0), add: c.a, del: c.d, files: c.f };
  if (c.h) r.sha = c.h;
  if (c.sub) r.subject = c.sub;
  if (c.m) r.merge = true;
  if (!c.b) r.side_branch = true;
  if (c.tr) r.claude_coauthor_trailer = true;
  if (c.s && c.s.length) r.sessions = c.s;
  if (!c.h && !c.sub) r.private = true;
  return r;
}

function signalById(B, id) { return B.signals.find(s => s.id === id); }

function trimValues(v, depth = 0) {
  if (Array.isArray(v)) return v.slice(0, 25).map(x => trimValues(x, depth + 1));
  if (v && typeof v === 'object') {
    const o = {};
    Object.keys(v).slice(0, 40).forEach(k => { o[k] = depth > 2 ? (typeof v[k] === 'object' ? '…' : v[k]) : trimValues(v[k], depth + 1); });
    return o;
  }
  return v;
}

// ---------------------------------------------------------------- the digest (what the page puts in front of every question)
export function digest(B) {
  const L = [];
  const m = B.meta;
  L.push(`x3d-ray digest: ${m.title} (${m.family}). Harvested ${m.harvested_at}; page built ${m.built_at} by x3d.xray ${m.version} at ${m.head || 'an uncommitted tree'}. ${m.privacy}`);
  L.push('\nSIGNALS (computed by code, no model):');
  B.signals.forEach(s => L.push(`- ${s.id} [${s.kind}]: ${s.text.slice(0, 420)}`));
  L.push('\nREPOS (name | kind | visibility | own commits: HagopJay/Claude/bots/other | first..last | files | lines at tip | tests | X3D files | branches | role):');
  B.repos.forEach(r => {
    const bc = r.by_class || {}, cov = r.coverage || {}, tm = !cov.tree || cov.tree === 'measured' || cov.tree.startsWith('counts');
    const u = v => (tm ? v : 'unknown');
    L.push(`- ${r.name} | ${r.kind} | ${r.visibility} | ${r.own_commits}: ${bc.you || 0}/${bc.ai || 0}/${bc.bot || 0}/${(bc.other || 0) + (bc.upstream || 0)} | ${r.first || '-'}..${r.last || '-'} | ${u(r.files)} | ${u(r.lines_at_tip)} | ${u(r.tests)} | ${u(r.x3d_files)} | ${r.branches} | ${r.role || ''}${cov.tree && cov.tree !== 'measured' ? ' [tree: ' + cov.tree + '; history: ' + cov.history + ']' : ''}`);
  });
  L.push('Unknown means not measured (a tree not harvested, or a private repo): never read it as zero.');
  if (B.vocab_notes) Object.entries(B.vocab_notes).forEach(([k, v]) => L.push(`Vocabulary: ${k}: ${v}`));
  L.push('Aliases: ' + Object.entries(B.vocab || {}).filter(([k, v]) => k !== v.toLowerCase()).map(([k, v]) => `${k} = ${v}`).join('; '));
  if (B.upstream && B.upstream.length) L.push('UPSTREAM HISTORY IN FORKS: ' + B.upstream.map(u => `${u.repo} ${u.commits} upstream commits ${u.first}..${u.last}`).join('; '));
  L.push('\nMACHINE TIES (from -> to, grade, why; evidence kinds with counts):');
  B.ties.machine.forEach(t => L.push(`- ${t.from} -> ${t.to} ${t.grade} (${t.reason}): ` + Object.entries(t.kinds).map(([k, v]) => k + ' ' + v).join(', ')));
  if (B.ties.curated.length) {
    L.push('\nCURATED TIES (the x3d_mcp fork masterclass, graded by reading code, commits and CI; C Confirmed, L Likely, S Suspected):');
    B.ties.curated.forEach(t => L.push(`- ${t.id} ${t.from} -> ${t.to} ${t.grade} ${t.kind}: ${t.what.slice(0, 170)}`));
  }
  if (B.ties.series.length) {
    L.push('\nFORK COMMIT SERIES (masterclass): ' + B.ties.series.map(s => `${s.id} ${s.title} (${s.commits} commits, ${s.first}..${s.last})`).join('; '));
  }
  L.push('\nVIEWS IN THE SCENE (id: [who says it] claim, then the caption):');
  B.spec.views.forEach(v => L.push(`- ${v.id}: ${v.claim ? '[' + v.claim.says + '] ' + v.claim.text + ' ' : ''}${v.caption}`));
  const sd = B.self;
  L.push(`\nTHE TOOL ITSELF: x3d.xray ${sd.tool.version}, ${sd.tool.modules} modules, ${sd.tool.lines_of_code} lines, code digest ${sd.tool.code_digest}, git head ${sd.tool.git.head || '?'}${sd.tool.git.uncommitted_changes ? ' plus uncommitted changes' : ''}.`);
  L.push('Pipeline: ' + sd.pipeline.map(p => `${p.stage} (${p.module}${p.model ? '; AI: ' + p.model.split('.')[0] : '; no model'})`).join(' -> '));
  L.push('In its own data: ' + sd.in_its_own_data.note + ` Commits of x3d.xray in the data: ${sd.in_its_own_data.commits_in_data}.`);
  const cl = sd.claude_in_the_data;
  L.push(`Claude in the data: ${cl.commits} of ${cl.of} own commits (${cl.share_pct}%), ${cl.sessions} sessions with a session trailer, ${cl.multi_repo_sessions} of them in more than one repo, ${cl.paired_commits} of HagopJay's commits carry a Claude co-author trailer. ${cl.note}`);
  if (cl.denominators) L.push('Claude\'s share by denominator: ' + Object.entries(cl.denominators).map(([k, v]) => `${k}: ${v}`).join('; '));
  if (sd.identity) L.push('Identity: ' + Object.entries(sd.identity).map(([k, v]) => `${k}: ${v}`).join('; '));
  L.push('Limits: ' + sd.limits.join(' '));
  return L.join('\n');
}

// ---------------------------------------------------------------- tools
export function makeTools(B, scene) {
  const T = [];
  const add = (name, description, inputSchema, execute) => T.push({ name, description, inputSchema, execute: (input, ctx) => cap(execute(input || {}, ctx)) });
  const filterProps = {
    repo: { type: 'string', description: 'repo name, or several separated by commas' },
    who: { type: 'string', description: 'you (HagopJay), ai (Claude), bot, other or upstream; several separated by commas' },
    from: { type: 'string', description: 'first day, YYYY-MM-DD (UTC)' },
    to: { type: 'string', description: 'last day, YYYY-MM-DD (UTC)' },
    day: { type: 'string', description: 'one day, YYYY-MM-DD' },
    text: { type: 'string', description: 'words that must appear in the commit subject' },
    sha: { type: 'string', description: 'commit hash prefix' },
    session: { type: 'string', description: 'a Claude session hash (s-...)' },
  };

  add('query_commits',
    'Find the family\'s own commits (not upstream history) by repo, who, dates, subject words, hash or Claude session. Returns the count, the split by who and by repo, total lines, and up to 40 rows with day, time (UTC), who, sha, subject and size. Private repos have no subject or sha.',
    { type: 'object', properties: { ...filterProps,
      merges: { type: 'boolean', description: 'true: merges only; false: no merges' },
      trailer: { type: 'boolean', description: 'true: only commits with a Claude co-author trailer' },
      sort: { type: 'string', enum: ['time', 'size'], description: 'time (default) or size (lines changed)' },
      order: { type: 'string', enum: ['asc', 'desc'] },
      limit: { type: 'integer', description: 'rows to return, at most 40 (default 15)' } } },
    q => {
      const rows = matchCommits(B, q);
      const sort = str(q.sort) === 'size' ? (a, b) => (b.a + b.d) - (a.a + a.d) : (a, b) => a.t - b.t;
      const sorted = rows.slice().sort(sort);
      if (str(q.order) === 'desc' && str(q.sort) !== 'size') sorted.reverse();
      const lim = clamp(num(q.limit, 15), 1, 40);
      return {
        matched: rows.length,
        by_who: countBy(rows, c => CLASS_NAMES[c.c] || c.c),
        by_repo: countBy(rows, c => c.r),
        first: rows.length ? day(Math.min(...rows.map(c => c.t))) : null,
        last: rows.length ? day(Math.max(...rows.map(c => c.t))) : null,
        lines: { add: rows.reduce((s, c) => s + (c.a || 0), 0), del: rows.reduce((s, c) => s + (c.d || 0), 0) },
        rows: sorted.slice(0, lim).map(c => commitRow(B, c)),
      };
    });

  add('activity',
    'Count the family\'s own commits in buckets: by day, week, month, hour of day (UTC), weekday or repo, split by who (you = HagopJay, ai = Claude, bot, other, upstream). Same filters as query_commits.',
    { type: 'object', properties: { ...filterProps, by: { type: 'string', enum: ['day', 'week', 'month', 'hour', 'weekday', 'repo'], description: 'bucket (default day)' } } },
    q => {
      const rows = matchCommits(B, q);
      const by = str(q.by) || 'day';
      const wd = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
      const key = c => {
        const d = new Date(c.t * 1000);
        if (by === 'hour') return String(d.getUTCHours()).padStart(2, '0');
        if (by === 'weekday') return wd[(d.getUTCDay() + 6) % 7];
        if (by === 'month') return day(c.t).slice(0, 7);
        if (by === 'repo') return c.r;
        if (by === 'week') { const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())); const dn = (t.getUTCDay() + 6) % 7; t.setUTCDate(t.getUTCDate() - dn + 3); const y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 4)); return t.getUTCFullYear() + '-W' + String(1 + Math.round(((t - y0) / 864e5 - 3 + ((y0.getUTCDay() + 6) % 7)) / 7)).padStart(2, '0'); }
        return day(c.t);
      };
      const b = {};
      rows.forEach(c => { const k = key(c); b[k] = b[k] || { key: k, total: 0, you: 0, ai: 0, bot: 0, other: 0, upstream: 0 }; b[k].total++; b[k][c.c] = (b[k][c.c] || 0) + 1; });
      let buckets = Object.values(b);
      buckets.sort(by === 'repo' ? (x, y) => y.total - x.total : (x, y) => (x.key < y.key ? -1 : 1));
      if (by === 'weekday') buckets.sort((x, y) => wd.indexOf(x.key) - wd.indexOf(y.key));
      return { by, matched: rows.length, buckets: buckets.slice(0, 160) };
    });

  add('top_files',
    'Files at each repo\'s tip, ranked by churn (lines added plus deleted over history), lines, commits or bytes. Filter by repo, kind (code, tests, docs, data, x3d, config, web, ci, media, log, other) or path words. The page holds each repo\'s top files, not every file; private repos have none.',
    { type: 'object', properties: { repo: filterProps.repo, kind: { type: 'string' }, path: { type: 'string', description: 'words in the path' },
      by: { type: 'string', enum: ['churn', 'lines', 'commits', 'bytes'] }, limit: { type: 'integer', description: 'at most 40 (default 12)' } } },
    q => {
      const repos = repoList(B, q.repo);
      const kind = str(q.kind), pathq = str(q.path).toLowerCase(), by = str(q.by) || 'churn';
      const unknown = repos.filter(r => { const x = B.repos.find(y => y.name === r); return x && x.coverage && x.coverage.tree !== 'measured'; });
      if (repos.length && unknown.length === repos.length) return { matched: 0, unknown: unknown.map(r => ({ repo: r, tree: B.repos.find(y => y.name === r).coverage.tree })), note: 'not measured, so unknown rather than zero' };
      const field = { churn: 'ch', lines: 'l', commits: 'n', bytes: 'by' }[by] || 'ch';
      let rows = [];
      Object.entries(B.files).forEach(([repo, fs]) => {
        if (repos.length && !repos.includes(repo)) return;
        fs.forEach(f => { if ((!kind || f.k === kind) && (!pathq || f.p.toLowerCase().includes(pathq))) rows.push({ repo, ...f }); });
      });
      rows.sort((a, b) => (b[field] || 0) - (a[field] || 0));
      const lim = clamp(num(q.limit, 12), 1, 40);
      return { by, matched: rows.length, note: 'the page keeps each repo\'s top files by churn and by lines, not every file',
        rows: rows.slice(0, lim).map(f => ({ repo: f.repo, path: f.p, kind: f.k, lines: f.l, bytes: f.by, commits: f.n, churn: f.ch, first: (f.first || '').slice(0, 10), last: (f.last || '').slice(0, 10), share: f.sh })) };
    });

  add('ties',
    'Ties between repos. source machine: found by rule (pins = commit hashes of one repo written in another, names in manifests, imports, CI, code and docs, identical files, one Claude session in two repos, forks, lineage), graded C/L/S with evidence lines. source curated: the fork masterclass\'s hand-graded ties (ids E01-E40) with what each says and its evidence. Filter by repo, grade, kind or id.',
    { type: 'object', properties: { repo: filterProps.repo, grade: { type: 'string', enum: ['C', 'L', 'S'] }, source: { type: 'string', enum: ['machine', 'curated', 'both'] },
      kind: { type: 'string' }, id: { type: 'string', description: 'a curated tie id such as E09' }, limit: { type: 'integer', description: 'at most 30 per source (default 12)' } } },
    q => {
      const repos = repoList(B, q.repo), grade = str(q.grade), src = str(q.source) || 'both', kind = str(q.kind).toLowerCase(), id = str(q.id).toUpperCase();
      const lim = clamp(num(q.limit, 12), 1, 30);
      const hit = t => (!repos.length || repos.includes(t.from) || repos.includes(t.to)) && (!grade || t.grade === grade);
      const out = {};
      if (src !== 'curated') {
        out.machine = B.ties.machine.filter(t => hit(t) && (!kind || Object.keys(t.kinds).some(k => k.toLowerCase().includes(kind))))
          .slice(0, lim).map(t => ({ from: t.from, to: t.to, grade: t.grade, why: t.reason, kinds: t.kinds, pins: t.pins || undefined, pin_targets: t.pin_targets, evidence: t.evidence }));
      }
      if (src !== 'machine') {
        out.curated = B.ties.curated.filter(t => hit(t) && (!kind || (t.kind || '').toLowerCase().includes(kind)) && (!id || t.id === id))
          .slice(0, lim).map(t => ({ id: t.id, from: t.from, to: t.to, grade: t.grade, kind: t.kind, what: t.what, first_date: t.first_date, evidence: t.evidence }));
      }
      out.grades = B.ties.grades;
      return out;
    });

  add('traverse',
    'Walk the tie graph instead of looking one tie up: from one repo, its neighbours up to depth hops away; from one repo to another, the shortest paths (at most three hops) with every hop\'s tie, grade and source. Machine and graded ties count both ways. Use it for "what connects A to B" or "what depends on A".',
    { type: 'object', properties: { from: { type: 'string' }, to: { type: 'string' }, depth: { type: 'integer', description: '1 to 3 (default 1)' },
      grades: { type: 'string', description: 'grades to walk, such as "C" or "C,L" (default all)' } }, required: ['from'] },
    q => {
      const a = resolveRepo(B, q.from), b = q.to ? resolveRepo(B, q.to) : '';
      const grades = new Set(list(q.grades).map(g => g.toUpperCase()));
      const edges = [];
      B.ties.machine.forEach(t => { if (!grades.size || grades.has(t.grade)) edges.push({ a: t.from, b: t.to, tie: `${t.from} -> ${t.to}`, grade: t.grade, source: 'machine', why: t.reason }); });
      B.ties.curated.forEach(t => { if (!grades.size || grades.has(t.grade)) edges.push({ a: t.from, b: t.to, tie: t.id, grade: t.grade, source: 'graded', why: t.kind }); });
      const adj = {};
      edges.forEach(e => { (adj[e.a] = adj[e.a] || []).push({ to: e.b, e }); (adj[e.b] = adj[e.b] || []).push({ to: e.a, e }); });
      if (!b) {
        const depth = clamp(num(q.depth, 1), 1, 3), seen = { [a]: 0 }, out = [];
        let frontier = [a];
        for (let d = 1; d <= depth; d++) {
          const next = [];
          frontier.forEach(n => (adj[n] || []).forEach(({ to, e }) => { if (seen[to] === undefined) { seen[to] = d; next.push(to); } if (seen[to] === d) out.push({ hop: d, via: n, to, tie: e.tie, grade: e.grade, source: e.source, why: e.why }); }));
          frontier = next;
        }
        return { route: 'traverse', from: a, depth, neighbours: Object.keys(seen).filter(n => n !== a).length, edges: out.slice(0, 80) };
      }
      const paths = [], queue = [[a]];
      while (queue.length && paths.length < 12) {
        const path = queue.shift(), last = path[path.length - 1];
        if (path.length > 4) break;
        if (last === b) { paths.push(path); continue; }
        (adj[last] || []).forEach(({ to }) => { if (!path.includes(to)) queue.push(path.concat(to)); });
      }
      const short = paths.length ? Math.min(...paths.map(p => p.length)) : 0;
      return { route: 'traverse', from: a, to: b, hops: short ? short - 1 : null,
        paths: paths.filter(p => p.length === short).slice(0, 6).map(p => p.slice(1).map((n, i) => {
          const es = (adj[p[i]] || []).filter(x => x.to === n).map(x => x.e);
          return { from: p[i], to: n, ties: es.slice(0, 4).map(e => ({ tie: e.tie, grade: e.grade, source: e.source })) };
        })) };
    });

  add('signals',
    'The profile\'s signals (totals, shares, rhythm, outliers, hotspots, pins, ties), each a sentence computed by code with its numbers. Without an id: every signal\'s sentence. With an id: that signal\'s full values.',
    { type: 'object', properties: { id: { type: 'string' } } },
    q => {
      const id = str(q.id);
      if (!id) return { signals: B.signals.map(s => ({ id: s.id, kind: s.kind, text: s.text })) };
      const s = signalById(B, id);
      if (!s) throw new Error('no signal ' + id + '; ids: ' + B.signals.map(x => x.id).join(', '));
      return { id: s.id, kind: s.kind, text: s.text, values: trimValues(s.values), computed_by: (B.self.signal_code[s.id] || {}).file };
    });

  add('how_built',
    'How this page and its numbers were made, from x3d.xray\'s description of itself. topic: pipeline (every stage, its module and whether a model takes part), stage (one stage, by name), signal_code (the source lines that compute a signal, by name), module (one source file\'s hash, size and definitions, by path), gates (the checks this build passed), receipt, self_in_data (x3d.xray inside its own data), claude (Claude\'s commits in the data, the denominators behind its share, and what that means), limits, spec (how the captions were written and checked), identity (what makes two things the same thing here, and the vocabulary of aliases).',
    { type: 'object', properties: { topic: { type: 'string', enum: ['pipeline', 'stage', 'signal_code', 'module', 'gates', 'receipt', 'self_in_data', 'claude', 'limits', 'spec', 'identity'] },
      name: { type: 'string', description: 'stage name, signal id or module path' } }, required: ['topic'] },
    q => {
      const S = B.self, topic = str(q.topic), name = str(q.name);
      if (topic === 'pipeline') return { tool: S.tool, pipeline: S.pipeline.map(p => ({ stage: p.stage, module: p.module, model: p.model || 'none', does: p.does })), ai_roles: S.ai_roles };
      if (topic === 'stage') { const p = S.pipeline.find(x => x.stage === name); if (!p) throw new Error('stages: ' + S.pipeline.map(x => x.stage).join(', ')); return p; }
      if (topic === 'signal_code') { const c = S.signal_code[name]; if (!c) throw new Error('signals: ' + Object.keys(S.signal_code).join(', ')); return { signal: name, ...c, helpers: 'own = the family\'s commits that are not upstream history; _pct rounds a share to a whole percent; _day is the UTC day' }; }
      if (topic === 'module') { const m = S.modules.find(x => x.path === name || x.path.endsWith('/' + name)); if (!m) throw new Error('modules: ' + S.modules.map(x => x.path).join(', ')); return m; }
      if (topic === 'gates') return B.gates || { status: 'not run for this build' };
      if (topic === 'receipt') return B.receipt || { status: 'no receipt for this build' };
      if (topic === 'self_in_data') return S.in_its_own_data;
      if (topic === 'identity') return { identity: S.identity, vocabulary: B.vocab, notes: B.vocab_notes };
      if (topic === 'claude') return S.claude_in_the_data;
      if (topic === 'limits') return { limits: S.limits, privacy: B.meta.privacy };
      if (topic === 'spec') return { author: B.spec.author, how: 'Claude wrote the prose; every number is a {slot} that x3dray/spec.py fills from profile.json; the fact gate fails a digit outside a slot or a slot that does not resolve',
        claims: B.spec.views.filter(v => v.claim).map(v => ({ view: v.id, claim: v.claim.text, template: v.claim.template, says: v.claim.says, backs: v.claim.backs })),
        tour: B.spec.tour.map(t => ({ view: t.view, title: t.title, template: t.template, cites: t.cites })) };
      throw new Error('unknown topic');
    });

  if (!scene) {
    add('overview',
      'The digest the page shows Claude before every question: signals, every repo with its numbers, every tie, the views, the tool\'s own pipeline and its limits. Read this first.',
      { type: 'object', properties: {} },
      () => ({ digest: digest(B) }));
    return T;
  }

  const target = {
    repo: { type: 'string', description: 'a repo: its node, ring or district' },
    sha: { type: 'string', description: 'a commit hash prefix' },
    day: { type: 'string', description: 'a day in the helix, YYYY-MM-DD' },
    file: { type: 'string', description: 'a file path in the code city' },
    tie: { type: 'string', description: 'a curated tie id such as E09' },
  };
  add('show',
    'Move the camera. Give a view (overview, helix, burst, peak, rhythm, claude, ties, fork, city, hotspots, self) or a target to fly to (repo, sha, day, file or tie). Returns what is now in view.',
    { type: 'object', properties: { view: { type: 'string' }, ...target, place: { type: 'string', enum: ['helix', 'ties', 'city'], description: 'for a repo: which view to find it in (default ties)' } } },
    q => scene.show(q));
  add('highlight',
    'Light up commits in the time helix (and the matching repo nodes and districts) by repo, who, dates, subject words, hash or session; xray: true dims everything else. clear: true resets. Returns how many are lit.',
    { type: 'object', properties: { ...filterProps, xray: { type: 'boolean' }, clear: { type: 'boolean' } } },
    q => scene.highlight(q));
  add('note',
    'Pin a short note (at most 80 characters) in the scene next to a repo, commit, day, file or tie, so the viewer can see what you mean. clear: true removes your notes. At most 12 notes.',
    { type: 'object', properties: { text: { type: 'string' }, ...target, clear: { type: 'boolean' } } },
    q => scene.note(q));
  add('color_by',
    'Recolour the helix\'s commits: who (default: HagopJay amber, Claude coral, bots sage), repo, size (lines changed), branch (default branch or side branch) or age.',
    { type: 'object', properties: { mode: { type: 'string', enum: ['who', 'repo', 'size', 'branch', 'age'] } }, required: ['mode'] },
    q => scene.colorBy(str(q.mode) || 'who'));
  return T;
}
