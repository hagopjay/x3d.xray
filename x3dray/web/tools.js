// x3d-ray tools: the functions the page's Claude calls and the MCP server serves. One implementation for both.
// makeTools(bundle, scene?) returns [{name, description, inputSchema, execute}]. Data tools read the bundle only.
// Scene tools (show, highlight, note, color_by) exist only when a scene API is passed, which only the page does.
// Every result is plain data and stays under the 32 KB a tool result may carry.

export const LIMIT_BYTES = 30000;
export const CLASS_NAMES = { you: 'HagopJay (his accounts)', ai: 'Claude', bot: 'bots', other: 'other identities', upstream: 'unmapped or upstream' };
const WHO_ALIASES = { claude: 'ai', ai: 'ai', assistant: 'ai', hagopjay: 'you', you: 'you', human: 'you', owner: 'you', hagsmac: 'you',
  bot: 'bot', bots: 'bot', other: 'other', upstream: 'upstream', unmapped: 'upstream' };
const SHORT = { you: 'HagopJay', ai: 'Claude', bot: 'bots', other: 'other', upstream: 'unmapped' };
const KIND_NAMES = { ...Object.fromEntries(Object.entries(CLASS_NAMES).map(([k, v]) => [v.toLowerCase(), k])),
  ...Object.fromEntries(Object.entries(SHORT).map(([k, v]) => [v.toLowerCase(), k])), ...WHO_ALIASES };
// Kinds of author in any form the tools and the scene print them ("HagopJay + Claude", "HagopJay (his accounts)", "you,ai"),
// as classes; "every file", the name printed for the concept with no kinds, is none. A name that is not one passes through, for
// the caller to refuse.
export function parseKinds(v) {
  const parts = Array.isArray(v) ? v : String(v === undefined || v === null ? '' : v).split(/\s*(?:,|\+|&|\band\b)\s*/i);
  return parts.map(x => String(x).trim().toLowerCase()).filter(x => x && x !== 'every file').map(x => KIND_NAMES[x] || x);
}
export function kindsOf(v) {
  const ks = [...new Set(parseKinds(v))], bad = ks.filter(k => !CLASS_ORDER.includes(k));
  if (bad.length) throw new Error('kinds of author are you (HagopJay), ai (Claude), bot, other and upstream, not ' + bad.join(', '));
  return ks;
}

const day = t => new Date(t * 1000).toISOString().slice(0, 10);
const hm = t => new Date(t * 1000).toISOString().slice(11, 16);
const str = v => (v === undefined || v === null ? '' : String(v)).trim();
const num = (v, d) => { const n = Number(v); return Number.isFinite(n) ? n : d; };
const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
const list = v => (Array.isArray(v) ? v : (str(v) ? str(v).split(',') : [])).map(s => String(s).trim()).filter(Boolean);

const utf8Bytes = value => new TextEncoder().encode(typeof value === 'string' ? value : JSON.stringify(value)).length;

// A transport limit must not silently rewrite evidence inside a returned record. Shorten
// top-level lists by whole records, or withhold a complete oversized field. Scalar text
// may be returned as an explicitly marked prefix. Counts already computed by a tool stay
// intact; _result_limit records this additional presentation loss separately.
export function cap(obj, limit = LIMIT_BYTES) {
  if (!Number.isInteger(limit) || limit < 256) throw new RangeError('result limit must be an integer of at least 256 bytes');
  const serialized = JSON.stringify(obj);
  if (serialized === undefined) throw new TypeError('a tool result must be JSON serializable');
  const originalBytes = utf8Bytes(serialized);
  if (originalBytes <= limit) return obj;
  const clone = JSON.parse(serialized);
  const out = clone && !Array.isArray(clone) && typeof clone === 'object' ? clone : { value: clone };
  const omissions = new Map();
  // Reserved metadata must not be mistaken for the source payload's own declaration.
  if (Object.hasOwn(out, '_result_limit') || Object.hasOwn(out, 'truncated')) {
    return { truncated: 'Entire oversized payload withheld; narrow the query.', _result_limit: { original_bytes: originalBytes, entire_payload_omitted: true } };
  }
  out.truncated = 'Result shortened by whole records or fields; see _result_limit. Narrow the query for omitted evidence.';
  out._result_limit = { original_bytes: originalBytes, omissions: [] };
  const update = (key, record) => {
    omissions.set(key, record);
    out._result_limit.omissions = [...omissions.values()].slice(0, 16);
    out._result_limit.omission_records = omissions.size;
  };
  let steps = 0;
  while (utf8Bytes(out) > limit && steps++ < 512) {
    const entries = Object.entries(out).filter(([k]) => !['truncated', '_result_limit'].includes(k));
    entries.sort((a, b) => utf8Bytes(JSON.stringify(b)) - utf8Bytes(JSON.stringify(a)));
    const entry = entries[0];
    if (!entry) break;
    const [key, value] = entry;
    const path = '/' + key.replace(/~/g, '~0').replace(/\//g, '~1');
    const previous = omissions.get(key);
    if (Array.isArray(value) && value.length) {
      const before = value.length;
      value.length = Math.floor(value.length / 2);
      const original = previous ? previous.original_items : before;
      update(key, { path: path.slice(0, 160), kind: 'array', original_items: original, returned_items: value.length, omitted_items: original - value.length });
      // Existing per-tool omissions include the newly removed whole records too.
      if (typeof out[key + '_omitted'] === 'number') out[key + '_omitted'] += before - value.length;
    } else if (typeof value === 'string' && value.length > 1) {
      const original = previous ? previous.original_utf8_bytes : utf8Bytes(value);
      // Array.from keeps surrogate pairs intact, including emoji.
      out[key] = Array.from(value).slice(0, Math.floor(Array.from(value).length / 2)).join('');
      update(key, { path: path.slice(0, 160), kind: 'text_prefix', original_utf8_bytes: original,
        returned_utf8_bytes: utf8Bytes(out[key]), omitted_utf8_bytes: original - utf8Bytes(out[key]) });
    } else {
      delete out[key];
      update(key, { path: path.slice(0, 160), kind: 'field_withheld' });
    }
  }
  if (utf8Bytes(out) <= limit) return out;
  // Huge property names or many independent fields can make explanation larger than
  // the available payload. Withholding all is more honest than an unmarked overflow.
  return { truncated: 'Entire oversized payload withheld; narrow the query.', _result_limit: { original_bytes: originalBytes, entire_payload_omitted: true } };
}

// The schemas already published to both consumers are also enforced at their shared
// execution boundary. This deliberately implements only the schema features we use.
export function validateInput(schema, value, path = 'arguments') {
  const type = schema.type;
  const isObject = value !== null && typeof value === 'object' && !Array.isArray(value);
  const valid = type === 'object' ? isObject : type === 'array' ? Array.isArray(value)
    : type === 'integer' ? Number.isInteger(value) : type === 'number' ? typeof value === 'number' && Number.isFinite(value)
      : type === 'string' ? typeof value === 'string' : type === 'boolean' ? typeof value === 'boolean' : true;
  if (!valid) throw new TypeError(path + ' must be ' + type);
  if (schema.enum && !schema.enum.includes(value)) throw new TypeError(path + ' must be one of: ' + schema.enum.join(', '));
  if (isObject) {
    for (const key of schema.required || []) if (!Object.hasOwn(value, key)) throw new TypeError(path + '.' + key + ' is required');
    for (const [key, child] of Object.entries(value)) {
      if (schema.properties && schema.properties[key]) validateInput(schema.properties[key], child, path + '.' + key);
      else if (schema.additionalProperties === false) throw new TypeError(path + '.' + key + ' is not allowed');
    }
  }
  if (Array.isArray(value) && schema.items) value.forEach((child, i) => validateInput(schema.items, child, path + '[' + i + ']'));
  return value;
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
  const tail = k.includes('/') ? k.slice(k.lastIndexOf('/') + 1) : '';      // owner/name: the name part, when it is a repo, comes first
  const viaTail = tail && ((B.vocab || {})[tail] || (B.repos.find(r => r.name.toLowerCase() === tail) || {}).name);
  const near = (viaTail ? [viaTail] : []).concat([...new Set(names)].map(n => [lev(k, n), n]).sort((a, b) => a[0] - b[0]).slice(0, 3).map(x => (B.vocab || {})[x[1]] || x[1]));
  throw new Error(`not in vocabulary: "${name}". Nearest: ${[...new Set(near)].slice(0, 3).join(', ')}. Known repos: ${B.repos.map(r => r.name).join(', ')}`);
}
const repoList = (B, v) => [...new Set(list(v).map(n => resolveRepo(B, n)))];   // two aliases of one repo name it once

// Typed architecture is a separate question from the legacy association graph.
// Bounds apply before graph construction and path enumeration, not only at the
// result serializer. Parallel observations support a hop; they do not multiply
// shortest node paths. BigInt preserves exact path counts on branching graphs.
const ARCH_LIMITS = { max_nodes: 10000, max_records: 50000, max_hops: 6, max_returned_paths: 50,
  relation_records_per_hop: 4, max_returned_records: 100, result_utf8_bytes: 14000 };
const ARCH_SCOPE = 'Recorded typed relations under explicit predicates and direction; a static reference or curated association does not prove an executable dependency.';

// Observation and validity are independent clocks. Parse the supported ISO
// profile explicitly; Date.parse silently accepts calendar rollovers and loses
// sub-millisecond distinctions. Arithmetic comparisons use exact microseconds.
function architectureInstant(value) {
  const match = typeof value === 'string' && /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?(Z|[+-][0-9]{2}:[0-9]{2})$/.exec(value);
  if (!match || match[0] !== value) throw new Error('time: expected a timezone-qualified ISO datetime with at most six fractional digits');
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number), fraction = (match[7] || '').padEnd(6, '0');
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day); date.setUTCHours(hour, minute, second, Number(fraction.slice(0, 3)));
  let offset = 0;
  if (match[8] !== 'Z') {
    const hours = Number(match[8].slice(1, 3)), minutes = Number(match[8].slice(4, 6));
    if (hours > 14 || minutes > 59 || hours === 14 && minutes !== 0) throw new Error('time: invalid calendar datetime or UTC offset');
    offset = (hours * 60 + minutes) * (match[8][0] === '+' ? 1 : -1);
  }
  if (year < 1 || date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day
      || date.getUTCHours() !== hour || date.getUTCMinutes() !== minute || date.getUTCSeconds() !== second) throw new Error('time: invalid calendar datetime or UTC offset');
  date.setTime(date.getTime() - offset * 60000);
  if (date.getUTCFullYear() < 1 || date.getUTCFullYear() > 9999) throw new Error('time: invalid calendar datetime or UTC offset');
  const micros = BigInt(date.getTime()) * 1000n + BigInt(fraction.slice(3));
  const tail = (String(date.getUTCMilliseconds()).padStart(3, '0') + fraction.slice(3)).replace(/0+$/, '');
  return { canonical: date.toISOString().slice(0, 19) + (tail ? '.' + tail : '') + 'Z', micros };
}

export function normalizeTimeFilter(value) {
  if (value === null) return null;
  if (!value || typeof value !== 'object' || Array.isArray(value) || !Object.hasOwn(value, 'at') || !Object.hasOwn(value, 'time_mode')
      || Object.keys(value).some(k => !['at', 'time_mode', 'unknown_time'].includes(k))) throw new Error('time: filter requires at and time_mode, with optional unknown_time');
  const unknown = Object.hasOwn(value, 'unknown_time') ? value.unknown_time : 'exclude';
  if (!['observed_by', 'valid_at'].includes(value.time_mode) || !['exclude', 'include'].includes(unknown)) throw new Error('time: unsupported temporal mode or unknown-time policy');
  return { at: architectureInstant(value.at).canonical, time_mode: value.time_mode, unknown_time: unknown };
}

function architectureRecordTime(record) {
  const observed = record.observed_at === undefined || record.observed_at === null ? null : architectureInstant(record.observed_at);
  const raw = record.valid_time;
  let valid = null;
  if (raw !== undefined && raw !== null) {
    if (typeof raw !== 'object' || Array.isArray(raw) || Object.keys(raw).length !== 2 || !Object.hasOwn(raw, 'start') || !Object.hasOwn(raw, 'end')) throw new Error('time: valid_time must be unknown or an explicit start/end interval');
    valid = { start: architectureInstant(raw.start), end: architectureInstant(raw.end) };
    if (valid.start.micros >= valid.end.micros) throw new Error('time: valid_time start must precede its exclusive end');
  }
  return { observed, valid };
}

function architectureTimeRecords(records, times, query) {
  const ledger = { active: query !== null, candidate_records: records.length, eligible_records: 0, known_records: 0, unknown_records: 0,
    included_known_records: 0, included_unknown_records: 0, excluded_known_records: 0, excluded_unknown_records: 0,
    observed_time_known_records: 0, valid_time_known_records: 0 };
  const selected = [], statuses = new Map(), point = query ? architectureInstant(query.at).micros : null;
  for (const record of records) {
    const { observed, valid } = times.get(record.id);
    ledger.observed_time_known_records += Number(observed !== null); ledger.valid_time_known_records += Number(valid !== null);
    if (!query) { selected.push(record); continue; }
    const known = query.time_mode === 'observed_by' ? observed !== null : valid !== null;
    ledger[known ? 'known_records' : 'unknown_records']++;
    const include = !known ? query.unknown_time === 'include' : query.time_mode === 'observed_by' ? observed.micros <= point
      : valid.start.micros <= point && point < valid.end.micros;
    ledger[(include ? 'included_' : 'excluded_') + (known ? 'known_records' : 'unknown_records')]++;
    if (include) { selected.push(record); statuses.set(record.id, known ? 'known_time' : 'provisional_unknown_time'); }
  }
  ledger.eligible_records = selected.length;
  ledger.scope = !query ? 'No temporal filter; observation and valid-time fields were not used to infer availability.'
    : query.time_mode === 'observed_by' ? 'Observation time <= at describes recorded visibility, not historical validity.'
      : 'Claimed start <= at < end; this does not impose an observation-time knowledge cutoff. Unknown inclusion is provisional.';
  return { selected, ledger, statuses };
}

function architectureOrder(a, b) {
  // Unicode code-point order matches Python's canonical ordering, independent
  // of the browser locale and unlike default UTF-16 ordering for astral names.
  const left = a[Symbol.iterator](), right = b[Symbol.iterator]();
  for (;;) {
    const x = left.next(), y = right.next();
    if (x.done || y.done) return x.done === y.done ? 0 : x.done ? -1 : 1;
    const delta = x.value.codePointAt(0) - y.value.codePointAt(0);
    if (delta) return delta;
  }
}

function architectureNode(B, nodes, value) {
  const asked = str(value);
  if (!asked || asked.length > 256) throw new Error('architecture node must be a nonempty name of at most 256 characters');
  if (nodes.has(asked)) return asked;
  const lower = asked.toLowerCase(), vocab = B.vocab || {};
  const alias = Object.hasOwn(vocab, lower) ? vocab[lower] : null;
  if (typeof alias === 'string' && nodes.has(alias)) return alias;
  const matches = [...nodes].filter(n => n.toLowerCase() === lower);
  if (matches.length === 1) return matches[0];
  if (matches.length > 1) throw new Error('architecture node name is ambiguous; use its exact canonical spelling');
  throw new Error(`not in architecture vocabulary: "${asked.slice(0, 80)}". Known examples: ${[...nodes].sort(architectureOrder).slice(0, 8).join(', ')}`);
}

function architectureCount(value) {
  return value <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(value) : value.toString();
}

function fitArchitectureResult(out) {
  // Keep complete paths/records, updating counts after this presentation cap.
  // cap() remains the final emergency guard for every shared tool.
  if (out.coverage && utf8Bytes(out.coverage) > 4000) {
    delete out.coverage;
    out.coverage_omitted = 'Coverage object exceeds this result budget; inspect the bundle coverage record.';
  }
  if (out.paths) {
    while (out.paths.length && utf8Bytes(out) > ARCH_LIMITS.result_utf8_bytes) {
      out.paths.pop();
      out.returned_paths = out.paths.length;
      out.omitted_paths = architectureCount(BigInt(out.total_paths) - BigInt(out.returned_paths));
      out.presentation_limited = 'Whole path records omitted to fit the UTF-8 result budget.';
    }
  } else {
    while (out.records.length && utf8Bytes(out) > ARCH_LIMITS.result_utf8_bytes) {
      out.records.pop(); out.returned_records = out.records.length; out.omitted_records = out.total_records - out.returned_records;
      out.presentation_limited = 'Whole relation records omitted to fit the UTF-8 result budget.';
    }
    while (out.nodes.length && utf8Bytes(out) > ARCH_LIMITS.result_utf8_bytes) {
      out.nodes.pop(); out.returned_nodes = out.nodes.length; out.omitted_nodes = out.total_nodes - out.returned_nodes;
      out.presentation_limited = 'Whole node records omitted to fit the UTF-8 result budget.';
    }
  }
  if (utf8Bytes(out) > ARCH_LIMITS.result_utf8_bytes && out.coverage) {
    delete out.coverage; out.coverage_omitted = 'Coverage object withheld to preserve the complete query and result counts.';
  }
  if (utf8Bytes(out) > ARCH_LIMITS.result_utf8_bytes) throw new Error('architecture result metadata exceeds the shared transport budget; narrow the query');
  return out;
}

export function architectureQuery(B, q) {
  const graph = B.relations;
  if (!graph || graph.schema_version !== 1 || !Array.isArray(graph.nodes) || !Array.isArray(graph.records)) {
    return { route: 'architecture', status: 'unavailable', semantic_scope: ARCH_SCOPE,
      reason: 'A supported typed relation graph was not computed for this bundle. Rebuild it; the association graph is not a substitute.' };
  }
  if (graph.nodes.length > ARCH_LIMITS.max_nodes || graph.records.length > ARCH_LIMITS.max_records) {
    return { route: 'architecture', status: 'unavailable', semantic_scope: ARCH_SCOPE,
      reason: 'Typed graph exceeds this in-browser work budget; query a partitioned bundle.', limits: ARCH_LIMITS,
      graph_nodes: graph.nodes.length, graph_records: graph.records.length };
  }
  if (typeof graph.snapshot_digest !== 'string' || !graph.snapshot_digest || graph.snapshot_digest.length > 128
      || !graph.predicates || typeof graph.predicates !== 'object' || Array.isArray(graph.predicates)) throw new Error('invalid typed relation graph provenance or predicate definitions');
  const nodes = new Set();
  for (const node of graph.nodes) {
    if (!node || typeof node.id !== 'string' || !node.id || node.id.length > 256 || nodes.has(node.id)) throw new Error('invalid typed relation graph nodes');
    nodes.add(node.id);
  }
  const direction = q.direction || 'outgoing', depth = q.max_hops === undefined ? 3 : q.max_hops, limit = q.limit === undefined ? 12 : q.limit;
  const temporalArgs = Object.fromEntries(['at', 'time_mode', 'unknown_time'].filter(k => Object.hasOwn(q, k)).map(k => [k, q[k]]));
  const timeFilter = normalizeTimeFilter(Object.keys(temporalArgs).length ? temporalArgs : null);
  if (!['outgoing', 'incoming', 'association'].includes(direction)) throw new Error('architecture direction must be outgoing, incoming or association');
  if (!Number.isInteger(depth) || depth < 1 || depth > ARCH_LIMITS.max_hops) throw new Error('architecture max_hops must be an integer from 1 to 6');
  if (!Number.isInteger(limit) || limit < 1 || limit > ARCH_LIMITS.max_returned_paths) throw new Error('architecture limit must be an integer from 1 to 50');
  const predicates = [...new Set(list(q.predicates))].sort(architectureOrder), grades = [...new Set(list(q.grades).map(g => g.toUpperCase()))].sort(architectureOrder);
  if (predicates.length > 32 || predicates.some(p => p.length > 128) || utf8Bytes(predicates) > 2048) throw new Error('architecture predicate filter exceeds its budget');
  if (grades.some(g => !['C', 'L', 'S'].includes(g))) throw new Error('architecture grades must be C, L or S');
  const knownPredicates = new Set(Object.keys(graph.predicates || {}));
  const ids = new Set(), recordTimes = new Map();
  for (const r of graph.records) {
    if (!r || typeof r.id !== 'string' || !r.id || r.id.length > 256 || ids.has(r.id) || !nodes.has(r.from) || !nodes.has(r.to) || r.from === r.to
        || typeof r.predicate !== 'string' || !r.predicate || r.predicate.length > 128 || typeof r.directed !== 'boolean'
        || !['C', 'L', 'S'].includes(r.grade) || !Array.isArray(r.evidence) || !r.evidence.length
        || !knownPredicates.has(r.predicate) || !graph.predicates[r.predicate] || typeof graph.predicates[r.predicate].directed !== 'boolean'
        || graph.predicates[r.predicate].directed !== r.directed) throw new Error('invalid typed relation graph record');
    ids.add(r.id);
    recordTimes.set(r.id, architectureRecordTime(r));
  }
  if (predicates.some(p => !knownPredicates.has(p))) throw new Error('unknown architecture predicate; use one declared in the typed graph');
  const a = architectureNode(B, nodes, q.from), b = q.to === undefined || q.to === '' ? null : architectureNode(B, nodes, q.to);
  const candidates = graph.records.filter(r => (!predicates.length || predicates.includes(r.predicate)) && (!grades.length || grades.includes(r.grade)));
  const { selected, ledger: timeCoverage, statuses: timeStatuses } = architectureTimeRecords(candidates, recordTimes, timeFilter);
  const adj = new Map([...nodes].map(id => [id, new Map()]));
  const arc = (from, to, record) => {
    const neighbours = adj.get(from);
    if (!neighbours.has(to)) neighbours.set(to, []);
    neighbours.get(to).push(record);
  };
  for (const r of selected) {
    if (!r.directed || direction === 'association') { arc(r.from, r.to, r); if (r.from !== r.to) arc(r.to, r.from, r); }
    else if (direction === 'incoming') arc(r.to, r.from, r);
    else arc(r.from, r.to, r);
  }
  for (const neighbours of adj.values()) for (const records of neighbours.values()) records.sort((x, y) =>
    Number(timeStatuses.get(x.id) === 'provisional_unknown_time') - Number(timeStatuses.get(y.id) === 'provisional_unknown_time') || architectureOrder(x.id, y.id));
  const distances = new Map([[a, 0]]), predecessors = new Map([[a, []]]), ways = new Map([[a, 1n]]), queue = [a];
  let head = 0, examinedArcs = 0;
  while (head < queue.length) {
    const node = queue[head++], d = distances.get(node);
    if (d >= depth || (b !== null && distances.has(b) && d >= distances.get(b))) continue;
    for (const neighbour of [...adj.get(node).keys()].sort(architectureOrder)) {
      examinedArcs++;
      if (!distances.has(neighbour)) { distances.set(neighbour, d + 1); predecessors.set(neighbour, []); ways.set(neighbour, 0n); queue.push(neighbour); }
      if (distances.get(neighbour) === d + 1) {
        predecessors.get(neighbour).push(node);
        ways.set(neighbour, ways.get(neighbour) + ways.get(node));
      }
    }
  }
  const out = { route: 'architecture', semantic_scope: ARCH_SCOPE, source: a, target: b, direction,
    predicates, grades, max_hops: depth, snapshot_digest: graph.snapshot_digest || null, coverage: graph.coverage || {},
    graph: { nodes: nodes.size, records: graph.records.length, eligible_records: selected.length },
    work: { reached_nodes: distances.size, examined_arcs: examinedArcs }, limits: ARCH_LIMITS,
    direction_note: direction === 'association' ? 'Direction is intentionally ignored in this query.'
      : direction === 'incoming' ? 'Directed relations are walked in reverse; each record retains its original from/to.'
        : 'Directed relations follow from to to; symmetric relations are traversable both ways.' };
  if (timeFilter) {
    timeCoverage.candidate_scope = 'All supplied records matching the predicate and grade filters, before reachability and temporal filtering.';
    out.time_filter = timeFilter; out.time_coverage = timeCoverage;
  }
  if (b !== null) {
    const total = distances.has(b) ? ways.get(b) : 0n;
    out.hops = distances.has(b) ? distances.get(b) : null;
    out.total_paths = architectureCount(total);
    out.count_encoding = total > BigInt(Number.MAX_SAFE_INTEGER) ? 'exact decimal string' : 'safe integer';
    out.paths = [];
    const ancestors = new Set([b]), pending = [b];
    while (pending.length) for (const parent of predecessors.get(pending.pop()) || []) if (!ancestors.has(parent)) { ancestors.add(parent); pending.push(parent); }
    function* forward(node, prefix = []) {
      const path = [...prefix, node];
      if (node === b) { yield path; return; }
      for (const next of [...adj.get(node).keys()].sort(architectureOrder)) {
        if (ancestors.has(next) && distances.get(next) === distances.get(node) + 1) yield* forward(next, path);
      }
    }
    if (total) {
      for (const path of forward(a)) {
        const item = { nodes: path, hops: path.slice(1).map((to, i) => {
          const from = path[i], records = adj.get(from).get(to), returned = records.slice(0, ARCH_LIMITS.relation_records_per_hop);
          const hop = { from, to, relations: returned, total_relations: records.length, returned_relations: returned.length, omitted_relations: records.length - returned.length };
          if (timeFilter) {
            hop.known_time_relations = records.filter(r => timeStatuses.get(r.id) === 'known_time').length;
            hop.unknown_time_relations = records.length - hop.known_time_relations;
            hop.time_status = hop.known_time_relations ? 'known_time' : 'provisional_unknown_time';
          }
          return hop;
        }) };
        if (timeFilter) item.time_status = item.hops.some(h => h.time_status === 'provisional_unknown_time') ? 'provisional_unknown_time' : 'known_time';
        out.paths.push(item);
        if (out.paths.length >= limit) break;
      }
    }
    out.returned_paths = out.paths.length;
    out.omitted_paths = architectureCount(total - BigInt(out.returned_paths));
    if (!total) out.note = 'No path within the selected predicates, grades, direction, temporal filter, hop limit and recorded snapshot; this is not evidence that no relationship exists.';
    return fitArchitectureResult(out);
  }
  const reached = [...distances].filter(([id]) => id !== a).map(([id, hops]) => ({ id, hops })).sort((x, y) => x.hops - y.hops || architectureOrder(x.id, y.id));
  out.nodes = reached.slice(0, limit); out.total_nodes = reached.length; out.returned_nodes = out.nodes.length; out.omitted_nodes = reached.length - out.nodes.length;
  const retainedNodes = new Set([a, ...out.nodes.map(n => n.id)]);
  const records = selected.filter(r => distances.has(r.from) && distances.has(r.to));
  out.records = records.filter(r => retainedNodes.has(r.from) && retainedNodes.has(r.to)).sort((x, y) => architectureOrder(x.id, y.id)).slice(0, ARCH_LIMITS.max_returned_records);
  out.total_records = records.length; out.returned_records = out.records.length; out.omitted_records = records.length - out.records.length;
  out.record_scope = 'Eligible relation records with both endpoints reached within the hop limit; returned records are induced by returned nodes plus the source.';
  return fitArchitectureResult(out);
}

// A Claude session in any form the tools print it: s-..., the graphs' node id session:s-..., or "session s-..." (the digest).
// An unknown one is refused, so it never reads as a session with no commits.
export function sessionId(B, v) {
  const s = str(v).replace(/^session[:\s]\s*/i, '');
  if (!s) return '';
  const known = (B.bridges && B.bridges.sessions && B.bridges.sessions.sessions) || {};
  if (known[s] || B.commits.some(c => (c.s || []).includes(s))) return s;
  throw new Error(`no Claude session "${str(v)}" in this family: session ids look like s-0a17c88568, and the bridges tool lists them`);
}

function commitFilter(B, q) {
  const repos = repoList(B, q.repo).map(r => r.toLowerCase());
  const who = kindsOf(q.who);                                     // an unknown kind is refused, never read as none
  const from = str(q.from), to = str(q.to), text = str(q.text).toLowerCase(), sha = str(q.sha).toLowerCase(), sess = sessionId(B, q.session);
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

// ---------------------------------------------------------------- concepts and bridges (x3dray/structure.py)
const CLASS_ORDER = ['you', 'ai', 'bot', 'other', 'upstream'];
const sortCls = xs => xs.slice().sort((a, b) => CLASS_ORDER.indexOf(a) - CLASS_ORDER.indexOf(b));
export const kindsName = (xs, none = 'every file') => (xs && xs.length ? xs.map(c => CLASS_NAMES[c] || c).join(' + ') : none);
const repoOf = id => id.slice(0, id.indexOf(':'));
const pathOf = id => id.slice(id.indexOf(':') + 1);

// Repos with a cluster of at least `min` files in which no file lies between two others (a clique, say): no bridge file, not unknown.
export function noBridge(variant, best, min = 10) {
  return Object.entries((variant && variant.largest_cluster_by_repo) || {}).filter(([r, n]) => n >= min && !best[r]).map(([repo, n]) => ({ repo, largest_cluster: n }));
}

// Per repo, the file with the most shortest paths through it, in a co-change cluster of at least `min` files. A repo's files
// only share commits with files of the same repo, so a cluster never spans repos. Same rule as profile.top_bridges.
export function bestBridges(variant, min = 10) {
  const best = {};
  Object.entries((variant && variant.per_file) || {}).forEach(([id, v]) => {
    if (v.component < min || v.betweenness <= 0) return;
    const repo = repoOf(id), file = pathOf(id), cur = best[repo];
    if (!cur || v.betweenness > cur.betweenness || (v.betweenness === cur.betweenness && file > cur.file)) best[repo] = { file, ...v, share_pct: Math.round(100 * v.share) };
  });
  return best;
}

const isRepoName = (B, s) => { try { return !!resolveRepo(B, s); } catch (e) { return false; } };
const LINES = /^(.+?):(\d+(?:[-:]\d+)?(?:\s*,\s*:?\d+(?:-\d+)?)*):?$/;   // :12, :12:3, :700-774, :2,16-17,23, ":80, 162", ":7, :18", grep's "12:"
// A file in any form the tools and the tie evidence print it: path, repo:path or the evidence's "repo path", any of them with
// lines (path:12, path:700-774, path:2,16-17,23, "path:7, :18"). The head before the first ':' is a repo when the vocabulary
// knows it (an alias may hold a '/', such as owner/name, or a space); failing that, so are the first one to three words before a
// space, unless the whole is a known path. The lines are dropped, since the lattice, the graphs and the city hold whole files.
// holds(p) says whether some known path holds p, so a path with a ':' of its own stays whole and a word of a path with spaces is
// never read as a repo; isPath(p) whether a known path is p or ends in '/' + p. A known path before a ':', or a known path's part
// that holds a '.' or a space (a file name, not a repo word), has what follows dropped like a line, repo or not. Any other head
// that is no repo is refused with the nearest names when it is one word or owner/name with a known name; any other starts a path.
export function splitFile(B, v, holds = () => false, isPath = () => false) {
  const raw = str(v).replace(/^\/+/, ''), i = raw.indexOf(':'), head = i > 0 ? raw.slice(0, i) : '';
  let repo = head && isRepoName(B, head) ? resolveRepo(B, head) : '', path = repo ? raw.slice(i + 1).trim() : raw, line = '';
  const whole = raw.replace(LINES, '$1').replace(/([^:]):$/, '$1');     // the input, its lines or a bare trailing ':' dropped
  if (!repo && !holds(whole) && !(head && holds(head))) {              // the evidence's "x3d.onto FACTS.md", "fork src/a.py:700-774"
    const gaps = [...raw.matchAll(/\s+/g)].slice(0, 3);
    for (let n = gaps.length - 1; n >= 0 && !repo; n--) {
      const h = raw.slice(0, gaps[n].index);
      if (!h.includes(':') && isRepoName(B, h)) { repo = resolveRepo(B, h); path = raw.slice(gaps[n].index + gaps[n][0].length); }
    }
  }
  const ln = path.match(LINES);
  if (ln && !holds(path)) { path = ln[1]; line = ln[2]; }
  else if (/[^:]:$/.test(path) && !holds(path)) path = path.slice(0, -1);   // a bare trailing ':' ("README.md:")
  const j = path.indexOf(':');
  if (j > 0 && !holds(path)) {
    const h = path.slice(0, j), parts = h.split('/');
    if (isPath(h) || (/[\s.]/.test(h) && holds(h))) { line = path.slice(j + 1).trim(); path = h; }   // a file, then text that is no line
    else if (!repo && (parts.length === 1 || (parts.length === 2 && isRepoName(B, parts[1])))) resolveRepo(B, h);   // throws "not in vocabulary"
  }
  return { repo, path, line };
}
function fileQuery(B, ids, q) {
  const low = ids.map(id => pathOf(id).toLowerCase());
  const s = splitFile(B, q, f => low.some(x => x.includes(f.toLowerCase())), f => low.some(x => x === f.toLowerCase() || x.endsWith('/' + f.toLowerCase())));
  if (!/[\p{L}\p{N}]/u.test(s.path)) throw new Error(s.repo ? `a file needs a path: "${str(q)}" names only the repo ${s.repo}; ask with repo for the repo itself`
    : `a file needs a path: "${str(q)}" has no letter or digit`);
  if (!s.repo && !s.line && isRepoName(B, s.path) && !low.some(x => x.includes(s.path.toLowerCase())))
    throw new Error(`"${str(q)}" is a repo, not a file: ask with repo for the repo, or give repo:path for one of its files`);
  const read = s.repo ? s.repo + ':' + s.path : s.path;
  return { query: s.path, repo: s.repo, asked: s.line || read !== str(q) ? { read_as: read, ...(s.line ? { line_dropped: s.line } : {}) } : {} };
}

// files whose path matches a query, in the repos named (all when none): an exact path, else (for a file name, one with a '.' or
// a '/') a path ending at a '/', each first as written and then in any case, as the page's show finds a file; else words in the
// path. Only the path is matched, so a repo named in the query narrows the files and never has to start the path.
function matchFileIds(ids, q, repos) {
  const fq = str(q), lq = fq.toLowerCase(), inR = ids.filter(id => !repos.length || repos.includes(repoOf(id))), named = /[./]/.test(fq);
  const tiers = named ? [x => x === fq, x => x.endsWith('/' + fq), x => x.toLowerCase() === lq, x => x.toLowerCase().endsWith('/' + lq)]
    : [x => x === fq, x => x.toLowerCase() === lq];
  for (const t of tiers.concat([x => x.toLowerCase().includes(lq)])) {
    const hit = inR.filter(id => t(pathOf(id)));
    if (hit.length) return hit.sort();
  }
  return [];
}

// A count with the commits that make it up; a list cut for size says how many it left out, so the count still checks.
const cite = (shas, n) => ({ commits: shas.length, shas: shas.slice(0, n), ...(shas.length > n ? { shas_omitted: shas.length - n } : {}) });
const cutList = (name, arr, n, map = x => x) => ({ [name]: arr.slice(0, n).map(map), [name + '_total']: arr.length,
  ...(arr.length > n ? { [name + '_omitted']: arr.length - n } : {}) });
const ruleRow = r => ({ if: r.if, then: r.then, holds_in: r.both, of: r.support, pct: Math.round(100 * r.confidence),
  exceptions: r.exceptions.slice(0, 8), exceptions_count: r.exceptions.length });

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
// Shorten prose to whole sentences (or whole words) within n characters, so the digest stays one tool result.
const clip = (t, n) => {
  t = String(t || '');
  if (t.length <= n) return t;
  const cut = t.slice(0, n), dot = cut.lastIndexOf('. ');
  return dot > n * 0.5 ? cut.slice(0, dot + 1) : cut.slice(0, cut.lastIndexOf(' ')) + ' …';
};
const shortKinds = xs => xs.map(c => SHORT[c] || c).join(' + ');

export function digest(B) {
  const L = [];
  const m = B.meta;
  L.push(`x3d-ray digest: ${m.title} (${m.family}). Harvested ${m.harvested_at}; page built ${m.built_at} by x3d.xray ${m.version} at ${m.head || 'an uncommitted tree'}. ${m.privacy}`);
  L.push('\nSIGNALS (computed by code, no model):');
  B.signals.forEach(s => L.push(`- ${s.id} [${s.kind}]: ${clip(s.text, 320)}`));
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
    B.ties.curated.forEach(t => L.push(`- ${t.id} ${t.from} -> ${t.to} ${t.grade} ${t.kind}: ${clip(t.what, 105)}`));
  }
  if (B.ties.series.length) {
    L.push('\nFORK COMMIT SERIES (masterclass): ' + B.ties.series.map(s => `${s.id} ${s.title} (${s.commits} commits, ${s.first}..${s.last})`).join('; '));
  }
  const C = B.concepts, BR = B.bridges;
  if (C) {
    const F = C.files_by_author_class, me = C.mass_edits;
    L.push('\nCONCEPTS (formal concept analysis by x3dray/structure.py, no model; the concepts tool looks them up):');
    L.push(`- Set aside from the file lattice and area rules, and counted: ${Object.entries(C.excluded).map(([k, v]) => v + ' ' + k).join('; ')}. The ${me.commits} mass edits hold ${me.file_touches} of ${me.of_file_touches} file touches (${Object.entries(me.by_repo).map(([k, v]) => k + ' ' + v).join(', ')}).`);
    L.push(`- Files by who touched them (${F.n_files} files, ${F.n_commits} commits; HagopJay = his accounts; first number = touched by at least these kinds, "only" = by exactly these): ` +
      F.concepts.filter(c => c.intent.length).map(c => `${shortKinds(c.intent)} ${c.support} (only ${c.exact})`).join('; ') + '.');
    L.push('- Repos by who has committed (every commit but merges; exactly these kinds): ' + C.repos_by_author_class.concepts.filter(c => c.exact.length).map(c => `${shortKinds(c.intent)}: ${c.exact.join(', ')}`).join('; ') + '.');
    const rs = C.areas.rules.slice().sort((a, b) => b.support - a.support);
    L.push(`- Area rules (${Math.min(6, rs.length)} of ${rs.length}, most support first; of the commits that touched A, how many also touched B; exceptions named by the tool): ` +
      rs.slice(0, 6).map(r => `${r.if} => ${r.then} ${r.both}/${r.support}`).join('; ') + '.');
  }
  if (BR) {
    const S = BR.sessions, hubs = bestBridges(BR.files.variants.count), brs = bestBridges(BR.files.variants.jaccard);
    L.push('\nBRIDGES (betweenness and passage points by x3dray/structure.py, no model; the bridges tool looks them up):');
    L.push(`- Sessions (every own commit with a Claude-Session trailer, merges and mass edits included): ${S.n_sessions} sessions; ${S.n_multi_repo} committed to more than one repo. Pairs a session committed to both: ` +
      S.repo_pairs.map(p => `${p.a} + ${p.b} (${p.sessions.length} session${p.sessions.length > 1 ? 's' : ''})`).join('; ') + '.' +
      ((S.linked_through_others || []).length ? ' Linked only through other repos: ' + S.linked_through_others.map(p => p.join(' + ')).join('; ') + '.' : ''));
    L.push('- On every path between two repos in the session graph: ' + Object.entries(S.passage_points).map(([k, v]) => `${k}: ${v.map(x => x.replace(/^session:/, 'session ')).join(', ')}`).join('; ') + '.');
    L.push(`- ${S.no_recorded_link.length} of the ${S.n_repo_pairs} pairs of the ${S.n_repos} repos with own commits have no recorded session link` +
      (S.repos_without_session.length ? ` (no session reached ${S.repos_without_session.join(', ')})` : '') +
      `: ${S.commits_without_trailer} own commits carry no session trailer, so this is not evidence of no link, and ties may still join them.`);
    const hubList = Object.entries(hubs);
    const flat = noBridge(BR.files.variants.jaccard, brs);
    L.push('- Bridge file per repo, in a cluster of at least ten files (usable commits, high-churn files set aside; share of the shortest paths between the other files of its cluster): ' + (Object.keys(brs).length ? Object.entries(brs).map(([r, v]) => `${r} ${v.file} ${v.share_pct}% of ${v.component}${v.cut_vertex ? ' cut vertex' : ''}`).join('; ') : 'none') +
      (flat.length ? `${Object.keys(brs).length ? '. None in' : ', and none in'} ${flat.map(x => `${x.repo} (${x.largest_cluster})`).join(', ')}: a cluster of ten or more with no file between two others` : '') +
      (hubList.length ? `. With every file kept, hubs rank first (${Math.min(5, hubList.length)} of ${hubList.length} repos): ` + hubList.slice(0, 5).map(([r, v]) => `${r} ${v.file}`).join('; ') : '') + '.');
  }
  L.push('\nVIEWS IN THE SCENE (id: [who says it] claim, then how the caption opens; how_built topic spec has every caption in full):');
  B.spec.views.forEach(v => L.push(`- ${v.id}: ${v.claim ? '[' + v.claim.says + '] ' + v.claim.text + ' ' : ''}${clip(v.caption, 110)}`));
  const sd = B.self;
  L.push(`\nTHE TOOL ITSELF: x3d.xray ${sd.tool.version}, ${sd.tool.modules} modules, ${sd.tool.lines_of_code} lines, code digest ${sd.tool.code_digest}, git head ${sd.tool.git.head || '?'}${sd.tool.git.uncommitted_changes ? ' plus uncommitted changes' : ''}.`);
  L.push('Pipeline (modules under x3dray/; no model unless marked AI): ' + sd.pipeline.map(p => `${p.stage} (${p.module.replace(/x3dray\//g, '')}${p.model ? '; AI: ' + p.model.split('.')[0] : ''})`).join(' -> '));
  L.push('In its own data: ' + sd.in_its_own_data.note + ` Commits of x3d.xray in the data: ${sd.in_its_own_data.commits_in_data}.` +
    (sd.in_its_own_data.observer_effect ? ' Observer effect: ' + sd.in_its_own_data.observer_effect : ''));
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
  const add = (name, description, inputSchema, execute) => T.push({ name, description, inputSchema, execute: (input = {}, ctx) => {
    validateInput(inputSchema, input);
    if (utf8Bytes(input) > 30000) throw new RangeError('tool arguments exceed 30000 UTF-8 bytes');
    return cap(execute(input, ctx));
  } });
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
    'Walk the undirected association graph: from one repo, its neighbours up to depth hops away; from one repo to another, paths at most three hops with each tie, grade and source. Machine and curated ties count both ways. This answers recorded association questions; use architecture with explicit predicates and direction for typed architectural paths.',
    { type: 'object', properties: { from: { type: 'string' }, to: { type: 'string' }, depth: { type: 'integer', description: '1 to 3 (default 1)' },
      grades: { type: 'string', description: 'grades to walk, such as "C" or "C,L" (default all)' } }, required: ['from'] },
    q => {
      const semantic_scope = 'Undirected recorded associations; these paths do not establish directed or executable dependencies.';
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
        return { route: 'traverse', semantic_scope, from: a, depth, neighbours: Object.keys(seen).filter(n => n !== a).length, edges: out.slice(0, 80) };
      }
      const paths = [], queue = [[a]];
      while (queue.length && paths.length < 12) {
        const path = queue.shift(), last = path[path.length - 1];
        if (path.length > 4) break;
        if (last === b) { paths.push(path); continue; }
        (adj[last] || []).forEach(({ to }) => { if (!path.includes(to)) queue.push(path.concat(to)); });
      }
      const short = paths.length ? Math.min(...paths.map(p => p.length)) : 0;
      return { route: 'traverse', semantic_scope, from: a, to: b, hops: short ? short - 1 : null,
        paths: paths.filter(p => p.length === short).slice(0, 6).map(p => p.slice(1).map((n, i) => {
          const es = (adj[p[i]] || []).filter(x => x.to === n).map(x => x.e);
          return { from: p[i], to: n, ties: es.slice(0, 4).map(e => ({ tie: e.tie, grade: e.grade, source: e.source })) };
        })) };
    });

  add('architecture',
    'Traverse conservative typed relations from the recorded snapshot. outgoing follows directed from→to; incoming reverses direction; association ignores direction. Symmetric relations work both ways. A dependency_reference is a static reference, not proof of runtime dependency. Returns shortest node paths with original records and omissions; parallel witnesses do not multiply paths. Without to, returns reachable nodes and their recorded subgraph. Optional at + time_mode: observed_by filters observation visibility, not validity; valid_at requires claimed start <= at < end on every hop and imposes no observation cutoff. Unknown time defaults to exclude; include is provisional. No temporal arguments preserves the recorded snapshot. Missing typed relations returns unavailable.',
    { type: 'object', additionalProperties: false, required: ['from'], properties: {
      from: { type: 'string', description: 'canonical repo/external node or configured repository alias' },
      to: { type: 'string', description: 'optional target; omit to inspect reachable neighbours' },
      direction: { type: 'string', enum: ['outgoing', 'incoming', 'association'] },
      predicates: { type: 'string', description: 'comma-separated declared predicates, such as declares_use,pins_revision; default all' },
      grades: { type: 'string', description: 'comma-separated C,L,S grades; default all' },
      max_hops: { type: 'integer', description: '1 to 6 (default 3)' },
      limit: { type: 'integer', description: '1 to 50 returned node paths or neighbours (default 12)' },
      at: { type: 'string', description: 'timezone-qualified ISO datetime, up to six fractional digits; requires time_mode' },
      time_mode: { type: 'string', enum: ['observed_by', 'valid_at'], description: 'requires at; observation visibility or claimed half-open valid interval, respectively' },
      unknown_time: { type: 'string', enum: ['exclude', 'include'], description: 'requires at and time_mode; default exclude; included unknown times are provisional' }
    } }, q => architectureQuery(B, q));

  add('concepts',
    'Formal concept analysis of the commits, computed with no model. No arguments: the lattice of files by the kinds of author that touched them, repos by the kinds of author they have seen, the strongest area rules, and what was set aside. file: one file (a path or repo:path) with the commits behind each kind. who: files touched by at least these kinds, such as "you,ai" (add repo to narrow). repo: its files by kind, its authors and its rules (several repos: separate with commas). area: rules for an area such as x3d_mcp/src. Every count names its commits; a cut list says how many it left out. Mass edits, merges and private commits are set aside from the files and rules and counted under excluded: quote that when an answer could read as none. A private repo answers with counts only.',
    { type: 'object', properties: { file: { type: 'string', description: 'a path, repo:path, or "repo path" as the ties print it; lines such as :12 or :700-774 are dropped' }, repo: { type: 'string', description: 'a repo name, or several separated by commas' },
      who: { type: 'string', description: 'kinds of author: you (HagopJay), ai (Claude), bot, other, upstream; several separated by commas' },
      area: { type: 'string', description: 'repo/top-level folder, such as x3d_mcp/src; files at the root are x3d_mcp/.' },
      limit: { type: 'integer', description: 'rows, at most 40 (default 12)' } } },
    q => {
      const C = B.concepts;
      if (!C) return { route: 'lookup', status: 'concepts were not computed for this build' };
      const F = C.files_by_author_class, R = C.repos_by_author_class, A = C.areas, lim = clamp(num(q.limit, 12), 1, 40), P = C.method.params;
      const base = { route: 'lookup', excluded: C.excluded, excluded_from: 'the file lattice and the area rules',
        mass_edits: { commits: C.mass_edits.commits, file_touches: C.mass_edits.file_touches, of_file_touches: C.mass_edits.of_file_touches, by_repo: C.mass_edits.by_repo } };
      const concept = i => { const c = F.concepts[i]; return { kinds: c.intent, name: kindsName(c.intent), files: c.support, only_these: c.exact }; };
      const commitsOf = id => Object.values(F.files[id].by).reduce((n, x) => n + x.length, 0);
      const fileRow = id => {
        const kinds = {};
        Object.entries(F.files[id].by).forEach(([k, shas]) => { kinds[k] = cite(shas, 12); });
        return { file: id, kinds, concept: concept(F.files[id].concept) };
      };
      const isPriv = r => !!(R.repos[r] && R.repos[r].private) || (B.repos || []).some(x => x.name === r && x.visibility === 'private');
      const countsScope = { reads: R.reads, excluded: R.excluded || {} };
      const privRecord = r => ({ repo: r, private: true, commits_by_class: (R.repos[r] || {}).commits_by_class || {}, commits_by_class_scope: countsScope,
        note: 'A private repo enters with commit counts by kind of author only: its files, areas and shas are unknown, not zero.' });
      const repos = repoList(B, q.repo), privs = repos.filter(isPriv), pubs = repos.filter(r => !isPriv(r));
      const privPart = privs.length ? { private_repos: privs.map(privRecord) } : {};
      if (repos.length && !pubs.length && (str(q.file) || str(q.who))) return { ...base, ...privPart, summary: 'private: counts only' };
      if (str(q.file)) {
        const fq = fileQuery(B, Object.keys(F.files), q.file);
        if (fq.repo && isPriv(fq.repo)) return { ...base, ...privRecord(fq.repo), summary: 'private: counts only' };
        const ids = matchFileIds(Object.keys(F.files), fq.query, fq.repo ? [fq.repo] : pubs);
        if (!ids.length) return { ...base, ...privPart, ...fq.asked, matched: 0, summary: 'not in the lattice',
          note: `No usable commit touched a file matching "${str(q.file)}". The lattice holds files touched by public commits that are not merges and change at most ${P.max_files_per_commit} files; this file may have been touched only by mass edits or merges (see excluded), sit in a private repo, or be named differently. Not in the lattice is not the same as never touched.` };
        return { ...base, ...privPart, ...fq.asked, matched: ids.length, summary: `${ids.length} file${ids.length === 1 ? '' : 's'}`, ...cutList('files', ids, lim, fileRow) };
      }
      if (str(q.who)) {
        const want = sortCls(kindsOf(q.who));
        const ext = Object.entries(F.files).filter(([id, rec]) => want.every(k => rec.by[k]) && (!pubs.length || pubs.includes(repoOf(id))));
        const exact = ext.filter(([, rec]) => Object.keys(rec.by).length === want.length);
        const closure = ext.length ? sortCls(Object.keys(ext[0][1].by).filter(k => ext.every(([, rec]) => rec.by[k]))) : want;
        ext.sort((a, b) => commitsOf(b[0]) - commitsOf(a[0]) || (a[0] < b[0] ? -1 : 1));
        return { ...base, ...privPart, kinds: want, name: kindsName(want), repos: pubs.length ? pubs : undefined, files: ext.length, only_these: exact.length, shared_by_all_of_them: closure,
          by_repo: countBy(ext, ([id]) => repoOf(id)), summary: `${ext.length} files`, ...cutList('top_files', ext, lim, ([id]) => fileRow(id)),
          note: ext.length ? 'files = touched by at least these kinds; only_these = touched by exactly these kinds; shared_by_all_of_them = every kind all these files have in common (the concept)'
            : 'No file in the lattice was touched by all of these kinds. See excluded for what was set aside.' };
      }
      if (str(q.area)) {
        const raw = str(q.area).replace(/\/{2,}/g, '/').replace(/\/+$/, ''), low = raw.toLowerCase();
        const names = [...new Set(Object.keys(B.vocab || {}).concat(B.repos.map(r => r.name.toLowerCase())))];
        const hit = names.filter(n => low === n || low.startsWith(n + '/')).sort((x, y) => y.length - x.length)[0];   // an alias may hold a slash
        const cut = hit ? hit.length : raw.indexOf('/'), head = cut >= 0 ? raw.slice(0, cut) : raw, rest = cut >= 0 ? raw.slice(cut + 1) : '';
        if (!str(head)) throw new Error('an area starts with its repo, such as x3d_mcp/src');
        const ar = resolveRepo(B, head);                                   // an alias resolves; an unknown repo is refused
        if (isPriv(ar)) return { ...base, ...privRecord(ar), summary: 'private: counts only' };
        const top = rest.split('/')[0], pre = (top ? ar + '/' + top : ar + '/').toLowerCase();
        const exact = top ? A.rules.filter(r => [r.if, r.then].some(x => x.toLowerCase() === pre)) : [];
        const rules = exact.length ? exact : A.rules.filter(r => [r.if, r.then].some(x => x.toLowerCase().startsWith(pre)));
        const named = [...new Set(rules.flatMap(r => [r.if, r.then]))].filter(x => exact.length ? x.toLowerCase() === pre : x.toLowerCase().startsWith(pre)).sort();
        const area = exact.length ? named[0] : top ? `${ar}/${top}${rules.length ? '…' : ''}` : `${ar} (every area)`;
        const scope = { area, ...(rules.length && !exact.length ? { matched_areas: named } : {}),
          ...(rest.includes('/') ? { area_note: `areas are top-level folders, so this reads as ${ar}/${top}` } : {}) };
        if (!rules.length) return { ...base, ...scope, matched: 0, summary: 'no rule',
          note: `No rule of at least ${Math.round(100 * P.min_rule_confidence)}% involves ${str(q.area)}: the area may move with no other area that often, have fewer than ${P.min_area_commits} usable commits (${A.areas_dropped} areas were dropped for that), or not exist.`,
          areas_in_rules: [...new Set(A.rules.flatMap(r => [r.if, r.then]))].sort() };
        return { ...base, ...scope, matched: rules.length, summary: `${rules.length} rule${rules.length === 1 ? '' : 's'}`, ...cutList('rules', rules, lim, ruleRow), note: A.note };
      }
      if (repos.length) {
        const one = repo => {
          const rr = R.repos[repo];
          if (!rr) return { repo, summary: 'no commits', note: 'This repo has no own commits that are not merges, so it is in neither lattice.' };
          if (rr.private) return { ...privRecord(repo), summary: 'private: counts only' };
          const files = Object.entries(F.files).filter(([id]) => repoOf(id) === repo);
          const groups = countBy(files, ([, rec]) => kindsName(sortCls(Object.keys(rec.by))));
          return { repo, summary: `${files.length} files`, commits_by_class: rr.commits_by_class, commits_by_class_scope: countsScope,
            first_commit_per_kind: rr.first_commit_by_class, files_in_lattice: files.length, files_by_exact_kinds: groups,
            ...cutList('rules', A.rules.filter(r => r.if.startsWith(repo + '/')), lim, ruleRow) };
        };
        if (repos.length === 1) return { ...base, ...one(repos[0]) };
        const each = repos.slice(0, 6).map(one);
        return { ...base, summary: `${each.length} repos`, repos: each, left_out: repos.length > 6 ? repos.slice(6) : undefined };
      }
      return { ...base, summary: `${F.n_concepts} concepts`,
        files_lattice: { files: F.n_files, commits: F.n_commits, note: F.note,
          concepts: F.concepts.map(c => ({ kinds: c.intent, name: kindsName(c.intent), files: c.support, only_these: c.exact, top_files: c.top_files.slice(0, 3).map(t => t.id) })) },
        repos_lattice: { ...countsScope,
          concepts: R.concepts.map(c => ({ kinds: c.intent, name: kindsName(c.intent, 'every repo'), repos: c.repos.length, exactly_these: c.exact })) },
        ...cutList('rules', A.rules.slice().sort((a, b) => b.support - a.support), 8, ruleRow), rules_note: A.note };
    });

  add('bridges',
    'Betweenness and passage points, computed with no model. No arguments: which Claude sessions link which repos, the nodes every path between two repos crosses in the session graph, each repo\'s bridge files in the co-change graph (high-churn files kept, and set aside), and what each part set aside. repo: its sessions, passage-point roles, authors and bridge files (several repos: separate with commas). file: one file\'s betweenness, cluster and cut-vertex flag in both variants. between: two repos, "a,b": linked directly (a session committed to both), only through other repos, or not at all. Where no chain of sessions joins two repos, say "no recorded session link", never "no link": the ties may still join them. Compare betweenness only within one cluster.',
    { type: 'object', properties: { repo: { type: 'string', description: 'a repo name, or several separated by commas' }, file: { type: 'string', description: 'a path, repo:path, or "repo path" as the ties print it; lines such as :12 or :700-774 are dropped' },
      between: { type: 'string', description: 'two repos separated by a comma' }, limit: { type: 'integer', description: 'rows, at most 30 (default 10)' } } },
    q => {
      const BR = B.bridges;
      if (!BR) return { route: 'lookup', status: 'bridges were not computed for this build' };
      const S = BR.sessions, V = BR.files.variants, Au = BR.authors, lim = clamp(num(q.limit, 10), 1, 30);
      const sessScope = { excluded: S.excluded || { 'own commits with no session trailer': S.commits_without_trailer }, reads: S.reads };
      const trailers = { own_commits_with: S.commits_with_trailer, own_commits_without: S.commits_without_trailer };
      const pkey = (a, b) => (a < b ? `${a} -> ${b}` : `${b} -> ${a}`);
      const inSessions = n => Object.values(S.sessions).some(rec => rec.repos[n] !== undefined);
      const isPriv = r => (B.repos || []).some(x => x.name === r && x.visibility === 'private');
      const fileView = (v, id) => { const x = V[v].per_file[id]; return x ? { betweenness: x.betweenness, share_pct: Math.round(100 * x.share), cluster: x.component, degree: x.degree, cut_vertex: x.cut_vertex, commits: x.commits } : null; };
      if (str(q.between)) {
        const two = repoList(B, q.between);
        if (two.length !== 2 || two[0] === two[1]) throw new Error('between needs two different repos, such as "x3d.ML,x3d_mcp"');
        const [a, b] = two, [x, y] = a < b ? [a, b] : [b, a], k = pkey(a, b);
        const pair = S.repo_pairs.find(p => p.a === x && p.b === y);
        const through = (S.linked_through_others || []).some(p => p[0] === x && p[1] === y);
        const linked = !!pair || through;
        return { route: 'lookup', excluded: { session_graph: sessScope.excluded, author_graph: Au.excluded || {} },
          reads: { session_graph: S.reads, author_graph: Au.reads }, session_trailers: trailers, between: [a, b], recorded_link: linked,
          how: pair ? 'directly: a Claude session committed to both' : through ? 'only through other repos: no session committed to both' : null,
          summary: pair ? `${pair.sessions.length} shared session${pair.sessions.length > 1 ? 's' : ''}` : through ? 'linked only through other repos' : 'no recorded session link',
          sessions_in_both: pair ? { sessions: pair.sessions, ...cite(pair.shas, 20), private_commits: pair.private_commits || 0 } : null,
          on_every_path: { session_graph: S.passage_points[k] || [], author_graph: Au.passage_points[k] || [] },
          note: linked ? 'A passage point is a node every path between the two crosses in that graph; none means more than one route. The ties tool shows other kinds of link.'
            : `No chain of Claude sessions joins them${inSessions(a) && inSessions(b) ? '' : ' (no session reached ' + [a, b].filter(n => !inSessions(n)).join(' or ') + ')'}. ${S.commits_without_trailer} own commits carry no session trailer, so this is no recorded session link, not evidence of no link. The ties tool may still find a tie.` };
      }
      if (str(q.file)) {
        const conceptsFiles = (B.concepts && B.concepts.files_by_author_class.files) || {};
        const all = [...new Set(Object.keys(V.count.per_file).concat(Object.keys(V.jaccard.per_file), Object.keys(conceptsFiles), BR.files.high_churn.map(h => h.id)))];
        const named = repoList(B, q.repo), privs = named.filter(isPriv), pubs = named.filter(r => !isPriv(r));
        const privRec = r => ({ repo: r, private: true,
          sessions_reaching_it: Object.values(S.sessions).filter(rec => rec.repos[r] !== undefined).length, sessions_reaching_it_scope: sessScope,
          note: 'A private repo enters the bridges as commit counts only: its files are never read, so its bridge files are unknown, not none.' });
        const privPart = privs.length ? { private_repos: privs.map(privRec) } : {};
        const scope = { route: 'lookup', excluded: BR.excluded, reads: BR.files.reads };
        if (named.length && !pubs.length) return { ...scope, ...privPart, summary: 'private: counts only' };
        const fq = fileQuery(B, all, q.file);
        if (fq.repo && isPriv(fq.repo)) return { ...scope, private_repos: [privRec(fq.repo)], summary: 'private: counts only' };
        const ids = matchFileIds(all, fq.query, fq.repo ? [fq.repo] : pubs);
        if (!ids.length) return { ...scope, ...privPart, ...fq.asked, matched: 0, summary: 'not in the graph',
          note: `No usable commit touched a file matching "${str(q.file)}" (see excluded; private repos are never read), so it is not in the co-change graph. That is not the same as never changed.` };
        const high = Object.fromEntries(BR.files.high_churn.map(h => [h.id, h])), HP = BR.method.params;
        const churn = h => ({ set_aside: `high churn: ${h.commits} of its repo's ${h.of} usable commits changed it; a file is set aside when at least ${HP.high_churn_min_commits} commits and ${Math.round(100 * HP.high_churn_share)}% of its repo's usable commits changed it` });
        return { ...scope, ...privPart, ...fq.asked, matched: ids.length, summary: `${ids.length} file${ids.length === 1 ? '' : 's'}`,
          ...cutList('files', ids, lim, id => {
            const shas = conceptsFiles[id] ? [...new Set(Object.values(conceptsFiles[id].by).flat())].sort() : [];
            return { file: id,
              every_file_kept: fileView('count', id) || { betweenness: 0, note: 'no shortest path between two other files runs through it' },
              high_churn_set_aside: high[id] ? churn(high[id]) : (fileView('jaccard', id) || { betweenness: 0 }),
              ...(shas.length ? cite(shas, 12) : {}) };
          }),
          note: BR.files.note };
      }
      const repos = repoList(B, q.repo);
      if (repos.length) {
        const one = repo => {
          const sess = Object.entries(S.sessions).filter(([, rec]) => rec.repos[repo] !== undefined)
            .map(([sid, rec]) => ({ session: sid, commits_here: rec.repos[repo], other_repos: Object.keys(rec.repos).filter(r => r !== repo) }));
          const top = v => {                                // the five highest, and any file tied with the fifth (up to twelve)
            const all = Object.entries(V[v].per_file).filter(([id]) => repoOf(id) === repo).sort((a, b) => b[1].betweenness - a[1].betweenness || (a[0] < b[0] ? -1 : 1));
            let n = Math.min(5, all.length);
            while (n < all.length && n < 12 && all[n][1].betweenness === all[n - 1][1].betweenness) n++;
            const last = n ? all[n - 1][1].betweenness : 0;
            return { rows: all.slice(0, n).map(([id]) => ({ file: pathOf(id), ...fileView(v, id) })), total: all.length, tied_out: all.slice(n).filter(([, x]) => x.betweenness === last).length };
          };
          const others = p => (p[0] === repo ? p[1] : p[0]);
          return { repo, summary: `${sess.length} session${sess.length === 1 ? '' : 's'}`, ...cutList('sessions', sess, lim),
            session_graph: inSessions(repo) ? {
              betweenness: (S.betweenness.find(x => x.id === 'repo:' + repo) || {}).betweenness || 0, cut_vertex: S.cut_vertices.includes('repo:' + repo),
              on_every_path_between: Object.entries(S.passage_points).filter(([, v]) => v.includes(repo)).map(([k2]) => k2),
              its_pairs_with_a_passage_point: Object.entries(S.passage_points).filter(([k2]) => k2.split(' -> ').includes(repo)).map(([k2, v]) => ({ pair: k2, via: v })),
              linked_only_through_others_with: (S.linked_through_others || []).filter(p => p.includes(repo)).map(others),
              no_recorded_session_link_with: S.no_recorded_link.filter(p => p.includes(repo)).map(others) }
              : { note: 'No commit of this repo carries a Claude-Session trailer, so no session reaches it: no recorded session link with any repo.' },
            authors: Au.edges.filter(e => e.repo === repo).map(e => ({ author: e.author, class: e.class, commits: e.commits, private: e.private || undefined })),
            bridge_files: isPriv(repo) ? { note: 'private: its files are never read, so its bridge files are unknown, not none' } : (() => {
              const tc = top('count'), tj = top('jaccard'), out = tc.tied_out + tj.tied_out;
              const lc = v => (V[v].largest_cluster_by_repo || {})[repo] || 0;
              if (!lc('count') && !lc('jaccard')) return { note: 'No usable commit touched a file of this repo (see excluded), so none of its files is in the co-change graph and it has no bridge file to name. That is not the same as files that never change together.' };
              const why = (v, rows) => !lc(v) ? 'none of its files is in this graph' : rows.length ? null
                : `no file lies on a shortest path between two others, nor splits a cluster: its largest cluster has ${lc(v)} file${lc(v) === 1 ? '' : 's'}`;
              const notes = Object.fromEntries([['every_file_kept', why('count', tc.rows)], ['high_churn_set_aside', why('jaccard', tj.rows)]].filter(x => x[1]));
              return { every_file_kept: tc.rows, high_churn_set_aside: tj.rows, largest_cluster: { every_file_kept: lc('count'), high_churn_set_aside: lc('jaccard') },
                ...(Object.keys(notes).length ? { notes } : {}),
                shown: `the five highest of the files with any betweenness or a cut, and any tied with the fifth: ${tc.rows.length} of ${tc.total} with every file kept, ${tj.rows.length} of ${tj.total} with high churn set aside` +
                  (out ? `; ${out} more tied with the last one shown are left out` : ''),
                high_churn_files: BR.files.high_churn.filter(h => repoOf(h.id) === repo).map(h => pathOf(h.id)) };
            })() };
        };
        const scopes = { excluded: { sessions: sessScope.excluded, authors: Au.excluded || {}, bridge_files: BR.excluded }, reads: { sessions: S.reads, authors: Au.reads, bridge_files: BR.files.reads } };
        if (repos.length === 1) return { route: 'lookup', ...scopes, ...one(repos[0]) };
        const each = repos.slice(0, 4).map(one);
        return { route: 'lookup', ...scopes, summary: `${each.length} repos`, repos: each, left_out: repos.length > 4 ? repos.slice(4) : undefined };
      }
      const hubs = bestBridges(V.count), brs = bestBridges(V.jaccard), authHubs = Au.betweenness.filter(x => x.id.startsWith('author:'));
      return { route: 'lookup', excluded: { session_graph: sessScope.excluded, author_graph: Au.excluded || {}, bridge_files: BR.excluded }, session_trailers: trailers,
        summary: `${S.n_multi_repo} of ${S.n_sessions} sessions span repos`,
        session_graph: { sessions: S.n_sessions, multi_repo: S.n_multi_repo, reads: S.reads,
          pairs: S.repo_pairs.map(p => ({ a: p.a, b: p.b, sessions: p.sessions, public_commits: p.shas.length, private_commits: p.private_commits || 0 })),
          linked_only_through_others: S.linked_through_others || [],
          on_every_path: S.passage_points, no_recorded_session_link: `${S.no_recorded_link.length} of ${S.n_repo_pairs} repo pairs`,
          repos_no_session_reached: S.repos_without_session, note: S.note },
        clusters_with_no_bridge_file: noBridge(V.jaccard, brs),
        bridge_files: [...new Set(Object.keys(brs).concat(Object.keys(hubs)))].map(r => ({ repo: r,
          hub_every_file_kept: hubs[r] ? `${hubs[r].file}: on ${hubs[r].share_pct}% of the shortest paths between the other files of its cluster of ${hubs[r].component}` : null,
          bridge_high_churn_set_aside: brs[r] ? `${brs[r].file}: on ${brs[r].share_pct}% of the shortest paths between the other files of its cluster of ${brs[r].component}${brs[r].cut_vertex ? ', cut vertex' : ''}` : null })),
        high_churn_files: BR.files.high_churn.length, files_note: BR.files.note,
        author_graph: { ...cutList('hubs', authHubs, authHubs.length > 3 ? authHubs.filter(x => x.betweenness >= authHubs[2].betweenness).length : authHubs.length),
          reads: Au.reads, note: Au.note } };
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
    session: { type: 'string', description: 'a Claude session hash (s-...): its node in the session graph' },
    concept: { type: 'string', description: 'kinds of author such as "you,ai": that concept in the file lattice' },
  };
  add('show',
    'Move the camera. Give a view (overview, helix, burst, peak, rhythm, claude, ties, fork, city, hotspots, concepts, bridges, self) or a target to fly to (repo, sha, day, file, tie, session or concept). Returns what is now in view.',
    { type: 'object', properties: { view: { type: 'string' }, ...target, place: { type: 'string', enum: ['helix', 'ties', 'city', 'bridges'], description: 'for a repo: which view to find it in (default ties; bridges is the session graph)' } } },
    q => scene.show(q));
  add('highlight',
    'Light up commits in the time helix (and the matching repo nodes and districts) by repo, who, dates, subject words, hash or session; xray: true dims everything else. clear: true resets. Returns how many are lit.',
    { type: 'object', properties: { ...filterProps, xray: { type: 'boolean' }, clear: { type: 'boolean' } } },
    q => scene.highlight(q));
  add('note',
    'Pin a short note (at most 80 characters) in the scene next to a repo, commit, day, file, tie, session or concept, so the viewer can see what you mean. clear: true removes your notes. At most 12 notes.',
    { type: 'object', properties: { text: { type: 'string' }, ...target, clear: { type: 'boolean' } } },
    q => scene.note(q));
  add('color_by',
    'Recolour the helix\'s commits: who (default: HagopJay amber, Claude coral, bots sage), repo, size (lines changed), branch (default branch or side branch) or age.',
    { type: 'object', properties: { mode: { type: 'string', enum: ['who', 'repo', 'size', 'branch', 'age'] } }, required: ['mode'] },
    q => scene.colorBy(str(q.mode) || 'who'));
  return T;
}
