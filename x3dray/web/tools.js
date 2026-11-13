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

// Whole-number filter arguments are refused when they are not whole numbers, never read as absent.
function edaWhole(v, name, lo, hi) {
  if (v === undefined || v === null || v === '') return null;
  if (typeof v !== 'number' || !Number.isInteger(v) || v < lo || v > hi) {
    throw new Error(`${name} must be ${hi === Infinity ? 'a non-negative integer' : `an integer from ${lo} to ${hi}`}`);
  }
  return v;
}
// A day argument is a real calendar date or it is refused: a malformed one would compare as a string and read as "no commits".
function edaDateArg(v, name) {
  const s = str(v);
  if (!s) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (m) {
    const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]), t = new Date(Date.UTC(y, mo - 1, d));
    if (t.getUTCFullYear() === y && t.getUTCMonth() === mo - 1 && t.getUTCDate() === d) return s;
  }
  throw new Error(`${name} must be a calendar date written YYYY-MM-DD (UTC)`);
}
const EDA_DAY_NAMES = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
function edaWeekdayArg(v) {
  if (v === undefined || v === null || v === '') return null;
  if (typeof v === 'number') return edaWhole(v, 'weekday', 0, 6);
  const s = str(v).toLowerCase();
  if (/^[0-6]$/.test(s)) return Number(s);
  const k = EDA_DAY_NAMES.findIndex(n => s === n || s === n.slice(0, 3));
  if (k < 0) throw new Error('weekday must be 0 to 6 (Monday is 0) or mon, tue, wed, thu, fri, sat, sun');
  return k;
}
const EDA_GRADE_RANK = { C: 3, L: 2, S: 1 };
const edaFlagIndexes = new WeakMap();
// rule -> (commit index -> {grade, k, m}), built once per eda object from the capped flag lists.
function edaFlagIndex(eda) {
  let ix = edaFlagIndexes.get(eda);
  if (ix) return ix;
  ix = new Map();
  for (const [rule, by] of Object.entries((eda.flags && eda.flags.by_rule) || {})) {
    const m = new Map();
    for (const g of ['C', 'L', 'S']) for (const [i, k, n] of by[g] || []) if (!m.has(i)) m.set(i, { grade: g, k, m: n });
    ix.set(rule, m);
  }
  edaFlagIndexes.set(eda, ix);
  return ix;
}
// The stored flag lists stop at a cap per rule and grade; a filter built from them would count only the commits listed and
// say nothing about the rest. Unknown is not zero, so a filter that would reach into a cut list is refused instead.
function edaRequireCompleteFlags(by, rule, least) {
  const grades = ['C', 'L', 'S'], left = {}, counted = {};
  for (const g of grades) {
    const listed = (by[g] || []).length;
    counted[g] = by.counts && Number.isInteger(by.counts[g]) ? by.counts[g] : listed;
    left[g] = Math.max(counted[g] - listed, (by.omitted && by.omitted[g]) || 0);
  }
  const cut = grades.filter(g => EDA_GRADE_RANK[g] >= least && left[g] > 0);
  if (!cut.length) return;
  // "this grade or better" is complete when it and every better grade are listed in full; S is the loosest, so look from the worst down.
  const whole = grades.filter(g => grades.every(h => EDA_GRADE_RANK[h] < EDA_GRADE_RANK[g] || left[h] === 0));
  throw new Error(`the stored ${rule} flag lists are capped at ${by.cap || 200} per grade, so this filter would miss ` +
    cut.map(g => `${left[g]} of ${counted[g]} grade ${g} commits`).join(' and ') + '; ' +
    (whole.length ? `flag_grade ${whole[whole.length - 1]} or better is listed in full` : 'no grade is listed in full for this rule') +
    '; the eda flags topic shows the counts');
}
function edaFlagTest(B, q) {
  const rule = str(q.flag), grade = str(q.flag_grade).toUpperCase();
  if (!rule) {
    if (grade) throw new Error('flag_grade needs a flag');
    return null;
  }
  if (!B.eda || !B.eda.flags || !B.eda.flags.rules) throw new Error('the flag filter needs eda data, which this build does not have');
  const known = Object.keys(B.eda.flags.rules);
  if (!known.includes(rule)) throw new Error(`unknown flag ${JSON.stringify(rule.slice(0, 60))}; known: ${known.join(', ')}`);
  if (grade && !EDA_GRADE_RANK[grade]) throw new Error('flag_grade must be C, L or S');
  const least = EDA_GRADE_RANK[grade || 'S'];
  edaRequireCompleteFlags(B.eda.flags.by_rule[rule] || {}, rule, least);
  const at = edaFlagIndex(B.eda).get(rule) || new Map();
  return c => { const f = at.get(c.i); return !!f && EDA_GRADE_RANK[f.grade] >= least; };
}

function commitFilter(B, q) {
  const repos = repoList(B, q.repo).map(r => r.toLowerCase());
  const who = kindsOf(q.who);                                     // an unknown kind is refused, never read as none
  const from = edaDateArg(q.from, 'from'), to = edaDateArg(q.to, 'to'), text = str(q.text).toLowerCase(), sha = str(q.sha).toLowerCase(), sess = sessionId(B, q.session);
  const dayEq = edaDateArg(q.day, 'day');
  const minLines = edaWhole(q.min_lines, 'min_lines', 0, Infinity), maxLines = edaWhole(q.max_lines, 'max_lines', 0, Infinity);
  if (minLines !== null && maxLines !== null && minLines > maxLines) throw new Error(`min_lines ${minLines} is above max_lines ${maxLines}`);
  const hour = edaWhole(q.hour, 'hour', 0, 23), weekday = edaWeekdayArg(q.weekday), flagged = edaFlagTest(B, q);
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
    if (q.side_branch === false && !c.b) return false;
    if (q.trailer === true && !c.tr) return false;
    if (minLines !== null || maxLines !== null) {
      const lines = (c.a || 0) + (c.d || 0);
      if (minLines !== null && lines < minLines) return false;
      if (maxLines !== null && lines > maxLines) return false;
    }
    if (hour !== null && edaHourOf(c.t) !== hour) return false;
    if (weekday !== null && edaWeekdayOf(c.t) !== weekday) return false;
    if (flagged && !flagged(c)) return false;
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

// ---------------------------------------------------------------- the descriptive kernel of x3dray/stats.py, in integers
// A slice asked for in the page is computed with the arithmetic of the stored profile: the same nearest-rank quantiles, log2 bins,
// lower medians and exact Gini, so the two agree to the digit. No Date, no Math.round, no toFixed, no float division on a path
// that feeds a number into a result; sums and the Gini use BigInt. Inference (permutations, q-values, grades) is Python-only.
export const EDA_FORKS = ['all', 'no_merge', 'default_branch', 'no_mass'];
const EDA_MASS_FILES = 15;                  // stats.MASS_FILES: more files than this is a mass edit
const EDA_SAFE = Number.MAX_SAFE_INTEGER;   // stats.SAFE_INT = 2**53 - 1
const edaMod = (a, b) => ((a % b) + b) % b;
const edaFloorDiv = (a, b) => (a - edaMod(a, b)) / b;       // exact for safe integers: a - (a mod b) is a multiple of b

function edaBitLength(n) {
  if (n === 0) return 0;
  if (n < 2 ** 31) return 32 - Math.clz32(n);
  return edaBitLength(Math.floor(n / 2 ** 31)) + 31;       // division by a power of two is exact
}
export function edaBin(x, cap, unit = 1) {
  if (!Number.isInteger(x) || x < 0 || x > EDA_SAFE) throw new RangeError('edaBin needs an integer from 0 to 2**53-1');
  if (!Number.isInteger(unit) || unit < 1) throw new RangeError('edaBin needs a positive integer unit');
  return Math.min(cap, edaBitLength(edaFloorDiv(x, unit)));
}
export function edaQuantile(sorted, num, den) {
  const n = sorted.length;
  if (n === 0) throw new RangeError('edaQuantile needs at least one value');
  const k = Math.max(1, Math.floor((n * num + den - 1) / den));    // ceil(n*num/den), at least the first
  return sorted[k - 1];
}
const edaLowerMedian = sorted => sorted[(sorted.length - 1) >> 1];
// permille of num/den, half away from zero, in BigInt; callers pass den > 0.
function edaPermille(num, den) {
  const neg = num < 0n, a = neg ? -num : num, v = (2000n * a + den) / (2n * den);
  return Number(neg ? -v : v);
}
const edaPcts = [['p10', 1, 10], ['p25', 1, 4], ['p50', 1, 2], ['p75', 3, 4], ['p90', 9, 10], ['p99', 99, 100]];
export function edaSummarize(values, binCap, binUnit = 1) {
  const n = values.length;
  if (n === 0) return { n: 0 };
  if (values.some(v => !Number.isInteger(v) || v < 0 || v > EDA_SAFE)) throw new RangeError('edaSummarize needs integers from 0 to 2**53-1');
  const xs = values.slice().sort((a, b) => a - b);
  const bins = new Array(binCap + 1).fill(0);
  for (const x of xs) bins[edaBin(x, binCap, binUnit)]++;
  let total = 0n, weighted = 0n, top = 0n;
  const kTop = Math.floor((n + 9) / 10);
  xs.forEach((x, j) => {
    const b = BigInt(x);
    total += b;
    weighted += BigInt(j + 1) * b;
    if (j >= n - kTop) top += b;
  });
  const out = { n, sum: total <= BigInt(EDA_SAFE) ? Number(total) : total.toString(), min: xs[0], max: xs[n - 1] };
  for (const [name, num, den] of edaPcts) out[name] = edaQuantile(xs, num, den);
  const med = edaLowerMedian(xs);
  out.mad = edaLowerMedian(xs.map(x => Math.abs(x - med)).sort((a, b) => a - b));
  out.bins = bins;
  out.bin_rule = { unit: binUnit, cap: binCap };
  const share = n >= 10 && total > 0n;
  out.gini_permille = share ? edaPermille(2n * weighted - BigInt(n + 1) * total, BigInt(n) * total) : null;
  out.top_decile_permille = share ? edaPermille(top, total) : null;
  return out;
}
export const edaHourOf = t => edaFloorDiv(edaMod(t, 86400), 3600);
export const edaWeekdayOf = t => edaMod(edaFloorDiv(t, 86400) + 3, 7);      // Monday is 0; 1970-01-01 was a Thursday
export function edaInFork(c, fork) {
  if (fork === 'all') return true;
  if (fork === 'no_merge') return !c.m;
  if (fork === 'default_branch') return c.b === 1 && !c.m;
  if (fork === 'no_mass') return !c.m && c.f <= EDA_MASS_FILES;
  throw new Error(`unknown fork ${JSON.stringify(String(fork).slice(0, 40))}; forks: ${EDA_FORKS.join(', ')}`);
}
const EDA_MEASURES = { lines: { cap: 16, unit: 1 }, files: { cap: 16, unit: 1 }, gap_s: { cap: 19, unit: 60 } };
// Members in (t, i) order; a gap is the seconds since the previous member, so the first member has none (unknown, not zero).
function edaMeasureValues(members, measure) {
  if (!EDA_MEASURES[measure]) throw new Error(`unknown measure ${JSON.stringify(String(measure).slice(0, 40))}; measures: ${Object.keys(EDA_MEASURES).join(', ')}`);
  const ordered = members.slice().sort((a, b) => (a.t - b.t) || (a.i - b.i));
  if (measure === 'lines') return ordered.map(c => (c.a || 0) + (c.d || 0));
  if (measure === 'files') return ordered.map(c => c.f || 0);
  return ordered.slice(1).map((c, k) => c.t - ordered[k].t);
}
export function edaSliceValues(B, spec) {
  const slice = String(spec.slice);
  const inSlice = slice === 'family' ? () => true : slice.startsWith('class:') ? c => c.c === slice.slice(6)
    : slice.startsWith('repo:') ? c => c.r === slice.slice(5) : null;
  if (!inSlice) throw new Error('a slice is family, class:<class> or repo:<name>');
  const members = B.commits.filter(c => inSlice(c) && edaInFork(c, spec.fork));
  return { members: members.length, values: edaMeasureValues(members, spec.measure) };
}

// ---------------------------------------------------------------- the inference of x3dray/stats.py and x3dray/looks.py, in exact integers
// Rows in, numbers out. The block between BEGIN and END reads no clock, no random source and no float: a test lints its text and runs it
// with Date and Math.random removed. Python is the specification (stats.py, looks.py, looks.verify); this is its twin, and a stored
// looks section must come back byte for byte. Names stay inside the closure: only EDA_INFER is a top-level name (tools.js is inlined
// as a classic script into the Observatory, where a second top-level name could collide).
// BEGIN EDA INFERENCE KERNEL
export const EDA_INFER = (() => {
  const freeze = o => { Object.values(o).forEach(v => { if (v !== null && typeof v === 'object') freeze(v); }); return Object.freeze(o); };     // the constants below are the manifest: nothing may edit them
  const T = freeze({ MIN_SIDE: 20, MIN_HALF: 8, FLOOR_CLIFF: [147, 1000], FLOOR_TV: [1, 10], ALPHA: [1, 20], Q_BH: [1, 10], Q_CONFIRM: [1, 20],
    WORK_CAP: 10000000, M_MIN: 199, M_MAX: 999, MIN_STRATUM_SIDE: 20 });                          // looks.THRESHOLDS; the manifest hash below proves it
  const ORDINAL = ['lines', 'files', 'gap_s'], PAIR_ORDER = ['you', 'ai', 'bot'];
  const DECLARED = freeze([...['lines', 'files', 'gap_s', 'hour4', 'weekday', 'repo'].map(m => ['contrast:' + m, 'contrast', m]),
    ...['first', 'second'].flatMap(w => ['lines', 'hour4', 'weekday'].map(m => [`drift:${w}:${m}`, 'drift', m]))]);
  const SEED_PREFIX = 'x3dray.eda.v1|', ADHOC_PREFIX = 'x3dray.eda.adhoc.v1|', NULL_NAME = 'label permutation with an add-one count';
  const LIMITS = freeze({ MAX_ROWS: 200000, T_LIMIT: 8640000000000, MAX_EXACT: 2 ** 53, BELOW_K: 2 ** 21, MAX_BINS: 65536,
    RECOMPUTE_DRAWS: 25000000, ADHOC_CAP: 4000000, ADHOC_LOOKS: 30 });
  const SCOPE = 'the recorded commits of this family; commits are treated as exchangeable under the null; not a population and not a cause';
  const BH_NOTE = 'BH assumes independent or positively dependent looks; looks here share commits, so q is indicative';
  const SAY_AT_MOST = { C: 'a pattern that survived every declared check in this history; not causal, not predictive',
    L: 'a likely pattern in this history; at least one robustness check failed or could not be run', S: 'worth a second look; do not call it real' };
  const GRADE_WORD = { C: 'Confirmed', L: 'Likely', S: 'Suspected' };
  const MEASURE_PHRASE = { lines: 'lines changed per commit', files: 'files changed per commit', gap_s: 'quiet time before a commit',
    hour4: 'UTC four-hour block', weekday: 'UTC weekday', repo: 'which repo the commit is in' };
  const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const gradeRule = () => 'A look is graded by the first rule that matches: not run or effect below the floor ' +
    `(|Cliff's delta| ${T.FLOOR_CLIFF[0]}/${T.FLOOR_CLIFF[1]}, total variation ${T.FLOOR_TV[0]}/${T.FLOOR_TV[1]}) ` +
    `or add-one count above ${T.ALPHA[0]}/${T.ALPHA[1]} gives no grade; q above ${T.Q_BH[0]}/${T.Q_BH[1]} ` +
    'or an opposite effect in a repo gives Suspected; otherwise Likely unless q is at most ' +
    `${T.Q_CONFIRM[0]}/${T.Q_CONFIRM[1]}, at least one repo holds ` +
    `${T.MIN_STRATUM_SIDE} commits of each group, the interleaved-week halves replicate and at least 3 distinct forks ` +
    '(forks holding exactly the same commits count once) and ' +
    '3 leave-one-repo-out runs are evaluable and all agree, which gives Confirmed. ' +
    'A robustness grade over declared checks, not a likelihood or a chance estimate; Confirmed does not mean causal or predictive.';

  // ---- order, canonical JSON, SHA-256 (synchronous: crypto.subtle is async and tools.js imports nothing)
  function cmpStr(a, b) {                       // by code point, as Python orders str; < would order by UTF-16 unit
    let i = 0, j = 0;
    while (i < a.length && j < b.length) {
      const x = a.codePointAt(i), y = b.codePointAt(j);
      if (x !== y) return x < y ? -1 : 1;
      i += x > 0xFFFF ? 2 : 1; j += y > 0xFFFF ? 2 : 1;
    }
    return i < a.length ? 1 : j < b.length ? -1 : 0;
  }
  const cmpKey = (a, b) => (typeof a === 'string' ? cmpStr(a, b) : a < b ? -1 : a > b ? 1 : 0);
  // eda.canonical_json: sorted keys, no spaces, printable ASCII only (DEL and everything above it as lower-case \uXXXX); a float or an integer beyond 2**53 is refused.
  function canon(x) {
    if (x === null) return 'null';
    if (typeof x === 'boolean') return x ? 'true' : 'false';
    if (typeof x === 'number') {
      if (!Number.isSafeInteger(x)) throw new RangeError('canonical JSON takes safe integers only');
      return String(x);
    }
    if (typeof x === 'string') return JSON.stringify(x).replace(/[\u007f-\uffff]/g, ch => '\\u' + ch.charCodeAt(0).toString(16).padStart(4, '0'));
    if (Array.isArray(x)) return '[' + x.map(canon).join(',') + ']';
    if (typeof x === 'object') return '{' + Object.keys(x).sort(cmpStr).map(k => canon(k) + ':' + canon(x[k])).join(',') + '}';
    throw new TypeError('canonical JSON cannot hold a ' + typeof x);
  }
  const SHA_K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
  function sha256(bytes) {                      // Uint8Array -> 32 bytes
    const n = bytes.length, padded = new Uint8Array(((n + 9 + 63) >> 6) << 6);
    padded.set(bytes); padded[n] = 0x80;
    const dv = new DataView(padded.buffer), bits = n * 8;
    dv.setUint32(padded.length - 8, Math.floor(bits / 4294967296)); dv.setUint32(padded.length - 4, bits >>> 0);
    const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]), w = new Uint32Array(64);
    const rr = (x, k) => (x >>> k) | (x << (32 - k));
    for (let off = 0; off < padded.length; off += 64) {
      for (let t = 0; t < 16; t++) w[t] = dv.getUint32(off + 4 * t);
      for (let t = 16; t < 64; t++) {
        const s0 = rr(w[t - 15], 7) ^ rr(w[t - 15], 18) ^ (w[t - 15] >>> 3), s1 = rr(w[t - 2], 17) ^ rr(w[t - 2], 19) ^ (w[t - 2] >>> 10);
        w[t] = (w[t - 16] + s0 + w[t - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let t = 0; t < 64; t++) {
        const t1 = (hh + (rr(e, 6) ^ rr(e, 11) ^ rr(e, 25)) + ((e & f) ^ (~e & g)) + SHA_K[t] + w[t]) >>> 0;
        const t2 = ((rr(a, 2) ^ rr(a, 13) ^ rr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
        hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }
    const out = new Uint8Array(32), o = new DataView(out.buffer);
    h.forEach((v, k) => o.setUint32(4 * k, v));
    return out;
  }
  const hex = u8 => Array.from(u8, b => b.toString(16).padStart(2, '0')).join('');
  function sha256Text(s) {                      // seed and manifest labels are ASCII by construction; anything else is refused, never re-encoded
    if (/[^ -~]/.test(s)) throw new RangeError('a seed label is printable ASCII');
    return hex(sha256(Uint8Array.from(s, ch => ch.charCodeAt(0))));
  }
  const manifestSha256 = () => sha256Text(canon([...DECLARED.map(d => d.slice()), T]));
  const seedHiLo = label => { const h = sha256Text(label); return [parseInt(h.slice(0, 8), 16), parseInt(h.slice(8, 16), 16)]; };
  const seedText = ([hi, lo]) => ((BigInt(hi) << 32n) | BigInt(lo)).toString();

  // ---- SplitMix64 on two uint32 halves (no BigInt in the hot loop); below(k) = (u64 * k) >> 64, exact for k <= 2**21
  const mulHi = (a, b) => {
    const a0 = a & 0xFFFF, a1 = a >>> 16, b0 = b & 0xFFFF, b1 = b >>> 16;
    const mid = a0 * b1 + a1 * b0 + ((a0 * b0) >>> 16);
    return (a1 * b1 + Math.floor(mid / 65536)) >>> 0;
  };
  function splitMix(seedHi, seedLo) {
    let hi = seedHi >>> 0, lo = seedLo >>> 0, zh = 0, zl = 0;
    function next() {
      const l = lo + 0x7F4A7C15;
      hi = (hi + 0x9E3779B9 + (l > 0xFFFFFFFF ? 1 : 0)) >>> 0; lo = l >>> 0;
      let h = hi, k = lo;
      k = (k ^ ((h << 2) | (k >>> 30))) >>> 0; h = (h ^ (h >>> 30)) >>> 0;                                         // z ^= z >> 30
      let nh = (mulHi(k, 0x1CE4E5B9) + Math.imul(k, 0xBF58476D) + Math.imul(h, 0x1CE4E5B9)) >>> 0;                    // z *= 0xBF58476D1CE4E5B9
      k = Math.imul(k, 0x1CE4E5B9) >>> 0; h = nh;
      k = (k ^ ((h << 5) | (k >>> 27))) >>> 0; h = (h ^ (h >>> 27)) >>> 0;                                         // z ^= z >> 27
      nh = (mulHi(k, 0x133111EB) + Math.imul(k, 0x94D049BB) + Math.imul(h, 0x133111EB)) >>> 0;                        // z *= 0x94D049BB133111EB
      k = Math.imul(k, 0x133111EB) >>> 0; h = nh;
      zl = (k ^ ((h << 1) | (k >>> 31))) >>> 0; zh = (h ^ (h >>> 31)) >>> 0;                                       // z ^ (z >> 31)
    }
    return {
      next, word: () => (next(), [zh, zl]),
      below(k) {
        if (!(Number.isInteger(k) && k >= 1 && k <= LIMITS.BELOW_K)) throw new RangeError('below needs an integer 1 <= k <= 2**21');
        next();
        return Math.floor((zh * k + Math.floor(zl * k / 4294967296)) / 4294967296);
      },
    };
  }
  // Known answers, checked before any recomputation is trusted: SHA-256 of "abc", the first words of SplitMix64 from seed 0 (the
  // published reference stream), the manifest of the declared battery. A runtime that fails one of them computes nothing.
  function selfCheck() {
    const bad = [];
    if (sha256Text('abc') !== 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad') bad.push('sha256');
    const r = splitMix(0, 0), want = ['e220a8397b1dcdaf', '6e789e6aa1b965f4', '06c45d188009454f'];
    for (const w of want) { const [h, l] = r.word(); if (hex(Uint8Array.of(h >>> 24, h >>> 16, h >>> 8, h, l >>> 24, l >>> 16, l >>> 8, l)) !== w) bad.push('splitmix64'); }
    if (seedText(seedHiLo('x3dray.eda.v1|contrast:lines')) !== '15220541219178821952') bad.push('seed_from');
    return bad;
  }

  // ---- exact helpers
  const gcd = (a, b) => { while (b) [a, b] = [b, a % b]; return a; };
  const frac = (n, d) => { const g = gcd(n, d) || 1; return [n / g, d / g]; };
  const fracLt = (x, y) => x[0] * y[1] < y[0] * x[1];
  function decimalStr(num, den, places) {       // stats.decimal_str, half away from zero
    const n = BigInt(num), d = BigInt(den), neg = n < 0n, v = (2n * 10n ** BigInt(places) * (neg ? -n : n) + d) / (2n * d);
    const digits = v.toString().padStart(places + 1, '0'), text = places > 0 ? digits.slice(0, -places) + '.' + digits.slice(-places) : digits;
    return neg && v > 0n ? '-' + text : text;
  }
  const permilleOf = (num, den) => edaPermille(BigInt(num), BigInt(den));
  const sign = x => (x > 0) - (x < 0);
  const weekOf = t => edaFloorDiv(edaFloorDiv(t, 86400) + 3, 7);

  // ---- rows: the contract of stats.py, checked on every row, with a message that names the place and the field and never the value
  function checkRows(commits) {
    if (!Array.isArray(commits)) throw new TypeError('the bundle has no commit list');
    if (commits.length > LIMITS.MAX_ROWS) throw new RangeError(`the bundle has ${commits.length} commits; the in-browser check takes at most ${LIMITS.MAX_ROWS}`);
    const whole = v => Number.isInteger(v) && v >= 0 && v < LIMITS.MAX_EXACT;
    return commits.map((c, k) => {
      const where = `commit row #${k}`;
      if (!c || typeof c !== 'object') throw new TypeError(`${where} is not an object`);
      if (c.i !== k) throw new TypeError(`${where}: i must equal its position`);
      if (!Number.isInteger(c.t) || Math.abs(c.t) > LIMITS.T_LIMIT) throw new TypeError(`${where}: t must be an integer within plus or minus ${LIMITS.T_LIMIT}`);
      for (const f of ['a', 'd', 'f']) if (!whole(c[f])) throw new TypeError(`${where}: ${f} must be an integer from 0 to 2**53 - 1`);
      if (!whole(c.a + c.d)) throw new TypeError(`${where}: a + d reaches 2**53`);
      if ((c.m !== 0 && c.m !== 1) || (c.b !== 0 && c.b !== 1)) throw new TypeError(`${where}: m and b must be 0 or 1`);
      if (typeof c.r !== 'string' || !c.r || typeof c.c !== 'string') throw new TypeError(`${where}: r and c must be strings`);
      return { i: k, r: c.r, t: c.t, c: c.c, a: c.a, d: c.d, f: c.f, m: c.m, b: c.b };
    });
  }

  // ---- looks._Sets: restricted, (t, i)-ordered views of all rows, cached; a gap is measured inside the restricted set, before any class is cut
  const sortRows = rows => rows.slice().sort((x, y) => (x.t - y.t) || (x.i - y.i));
  function makeSets(rows) {
    const all = sortRows(rows), cache = new Map(), repos = [...new Set(all.map(r => r.r))].sort(cmpStr);
    const first = new Map(), aliases = {};
    for (const fork of EDA_FORKS) {
      const key = all.filter(r => edaInFork(r, fork)).map(r => r.i).join(',');
      if (!first.has(key)) first.set(key, fork);
      aliases[fork] = first.get(key);
    }
    return { all, repos, aliases, get(kind, arg) {
      const key = kind + '\0' + arg;
      let hit = cache.get(key);
      if (!hit) {
        const rs = kind === 'fork' ? all.filter(r => edaInFork(r, arg)) : kind === 'drop' ? all.filter(r => r.r !== arg)
          : kind === 'only' ? all.filter(r => r.r === arg) : all.filter(r => edaMod(weekOf(r.t), 2) === arg);
        hit = { rs, gaps: rs.map((r, k) => (k === 0 ? null : r.t - rs[k - 1].t)) };
        cache.set(key, hit);
      }
      return hit;
    } };
  }
  const valueOf = (measure, r) => measure === 'lines' ? r.a + r.d : measure === 'files' ? r.f : measure === 'hour4' ? edaFloorDiv(edaHourOf(r.t), 4)
    : measure === 'weekday' ? edaWeekdayOf(r.t) : r.r;
  // looks._cut: group a and group b as parallel value and repo lists in (t, i) order
  function cut(kind, measure, classes, rs, gaps) {
    const g = { av: [], ar: [], bv: [], br: [] };
    if (kind === 'contrast') {
      for (let k = 0; k < rs.length; k++) {
        const r = rs[k];
        if (r.c !== classes[0] && r.c !== classes[1]) continue;
        if (measure === 'gap_s' && gaps[k] === null) continue;
        const v = measure === 'gap_s' ? gaps[k] : valueOf(measure, r), first = r.c === classes[0];
        (first ? g.av : g.bv).push(v); (first ? g.ar : g.br).push(r.r);
      }
      return g;
    }
    const mine = rs.filter(r => r.c === classes), kk = mine.length >> 1;
    mine.forEach((r, k) => { const later = k >= kk; (later ? g.av : g.bv).push(valueOf(measure, r)); (later ? g.ar : g.br).push(r.r); });
    return g;
  }

  // ---- stats.cliff_counts, doubled_midranks, tv_parts and the two permutation counts
  const lowerBound = (s, x) => { let lo = 0, hi = s.length; while (lo < hi) { const m = (lo + hi) >>> 1; if (s[m] < x) lo = m + 1; else hi = m; } return lo; };
  const upperBound = (s, x) => { let lo = 0, hi = s.length; while (lo < hi) { const m = (lo + hi) >>> 1; if (s[m] <= x) lo = m + 1; else hi = m; } return lo; };
  function cliffCounts(av, bv) {
    const bs = Float64Array.from(bv).sort();
    let gt = 0, lt = 0;
    for (const x of av) { gt += lowerBound(bs, x); lt += bs.length - upperBound(bs, x); }
    return [gt, lt];
  }
  function doubledMidranks(values) {            // twice the average rank; a tie group at positions p..q gets p + q
    const n = values.length, order = Array.from({ length: n }, (_, k) => k).sort((x, y) => (values[x] < values[y] ? -1 : values[x] > values[y] ? 1 : x - y));
    const out = new Int32Array(n);
    for (let pos = 0; pos < n;) {
      let end = pos;
      while (end + 1 < n && values[order[end + 1]] === values[order[pos]]) end++;
      for (let q = pos; q <= end; q++) out[order[q]] = (pos + 1) + (end + 1);
      pos = end + 1;
    }
    return out;
  }
  const tally = vs => { const m = new Map(); for (const v of vs) m.set(v, (m.get(v) || 0) + 1); return m; };
  function tvParts(ca, cb, na, nb, ref) {       // (T, bin, signed difference at the bin); the bin is the first of the largest gaps unless ref is given
    const keys = [...new Set([...ca.keys(), ...cb.keys()])].sort(cmpKey), diffs = new Map();
    let total = 0, best = null;
    for (const k of keys) {
      const d = (ca.get(k) || 0) * nb - (cb.get(k) || 0) * na;
      diffs.set(k, d); total += Math.abs(d);
      if (best === null || Math.abs(d) > Math.abs(diffs.get(best))) best = k;
    }
    const bin = ref === null ? best : ref;
    return [total, bin, diffs.get(bin) || 0];
  }
  function stat(measure, av, bv, ref) {         // looks._stat
    const na = av.length, nb = bv.length;
    if (ORDINAL.includes(measure)) {
      const [gt, lt] = cliffCounts(av, bv), s = gt - lt;
      return { n_a: na, n_b: nb, num: s, den: na * nb, direction: sign(s), k: null };
    }
    const [t, k, diff] = tvParts(tally(av), tally(bv), na, nb, ref === undefined ? null : ref);
    return { n_a: na, n_b: nb, num: t, den: 2 * na * nb, direction: sign(diff), k };
  }
  function floorMet(measure, st, half) {
    if (st.den <= 0) return false;
    const [fn, fd] = ORDINAL.includes(measure) ? T.FLOOR_CLIFF : T.FLOOR_TV;
    return (half ? 2 : 1) * fd * Math.abs(st.num) >= fn * st.den;
  }
  function permCountCliff(r2, nDraw, sAbsObs, M, rng) {   // |S| >= observed in how many of M permutations; the array carries over between them
    const arr = Int32Array.from(r2), n = arr.length, off = nDraw * (n + 1);
    if (!(nDraw >= 0 && nDraw <= n)) throw new RangeError('n_draw out of range');
    let count = 0;
    for (let m = 0; m < M; m++) {
      let sum = 0;
      for (let j = 0; j < nDraw; j++) { const r = j + rng.below(n - j), x = arr[r]; arr[r] = arr[j]; arr[j] = x; sum += x; }
      if (Math.abs(sum - off) >= sAbsObs) count++;
    }
    return count;
  }
  function permCountTv(bins, nDraw, tObs, M, rng) {       // T >= observed in how many of M permutations; bins are dense ids, drawn group first
    const arr = Int32Array.from(bins), n = arr.length;
    if (!(nDraw >= 0 && nDraw <= n)) throw new RangeError('n_draw out of range');
    let kBins = 0;
    for (const v of arr) if (v + 1 > kBins) kBins = v + 1;
    if (kBins > LIMITS.MAX_BINS) throw new RangeError('too many distinct values for a permutation count');
    const tot = new Int32Array(kBins), d = new Int32Array(kBins), nOther = n - nDraw;
    for (const v of arr) tot[v]++;
    let count = 0;
    for (let m = 0; m < M; m++) {
      d.fill(0);
      for (let j = 0; j < nDraw; j++) { const r = j + rng.below(n - j), x = arr[r]; arr[r] = arr[j]; arr[j] = x; d[x]++; }
      let t = 0;
      for (let k = 0; k < kBins; k++) t += Math.abs(d[k] * nOther - (tot[k] - d[k]) * nDraw);
      if (t >= tObs) count++;
    }
    return count;
  }
  // looks._null_count: draw the smaller group (ties: a); both stay in (t, i) order, the drawn group first
  function nullCount(measure, av, bv, head, M, label) {
    const [drawn, other] = av.length <= bv.length ? [av, bv] : [bv, av], arranged = drawn.concat(other);
    const [hi, lo] = seedHiLo(label), rng = splitMix(hi, lo);
    if (ORDINAL.includes(measure)) return permCountCliff(doubledMidranks(arranged), drawn.length, Math.abs(head.num), M, rng);
    const dense = new Map([...new Set(arranged)].sort(cmpKey).map((k, n) => [k, n]));
    return permCountTv(arranged.map(v => dense.get(v)), drawn.length, head.num, M, rng);
  }

  // ---- stats.bh_q: Benjamini-Hochberg over a family of size m; items are [name, [num, den]] and the rest of the family counts through m
  function bhQ(items, m) {
    if (m < items.length) throw new RangeError('family size m is smaller than the number of items');
    const ordered = items.slice().sort((x, y) => (fracLt(x[1], y[1]) ? -1 : fracLt(y[1], x[1]) ? 1 : cmpStr(x[0], y[0]))), out = new Map();
    let running = [1, 1];
    for (let rank = ordered.length; rank >= 1; rank--) {
      const [name, p] = ordered[rank - 1];
      if (!(p[0] > 0 && p[0] <= p[1])) throw new RangeError('p must lie in (0, 1]');
      const cand = frac(m * p[0], p[1] * rank);
      if (fracLt(cand, running)) running = cand;
      out.set(name, running);
    }
    return out;
  }

  // ---- looks: checks, grade, text
  const effectOf = (measure, st) => ({ kind: ORDINAL.includes(measure) ? 'cliff_delta' : 'tv', num: st.num, den: st.den,
    permille: st.den > 0 ? permilleOf(st.num, st.den) : 0, direction: st.direction, dominant_bin: st.k, floor_met: floorMet(measure, st, false) });
  const agrees = (measure, head, st, minimum, half) => st.n_a >= minimum && st.n_b >= minimum && floorMet(measure, st, half) &&
    head.direction !== 0 && st.direction === head.direction;
  function checks(kind, measure, classes, sets, head, hg) {
    const run = (kind2, arg) => { const { rs, gaps } = sets.get(kind2, arg), g = cut(kind, measure, classes, rs, gaps); return stat(measure, g.av, g.bv, head.k); };
    const pm = st => (st.den > 0 ? permilleOf(st.num, st.den) : 0);
    const perFork = {};
    let evaluable = 0, agree = 0;
    for (const fork of EDA_FORKS) {
      const st = run('fork', fork), ok = st.n_a >= T.MIN_SIDE && st.n_b >= T.MIN_SIDE, yes = agrees(measure, head, st, T.MIN_SIDE, false);
      const same = sets.aliases[fork] !== fork ? sets.aliases[fork] : null;
      if (same === null) { evaluable += ok ? 1 : 0; agree += yes ? 1 : 0; }
      perFork[fork] = { n_a: st.n_a, n_b: st.n_b, evaluable: ok, permille: pm(st), agrees: yes, same_as: same };
    }
    const loro = { total: 0, agree: 0 };
    let strata = null;
    if (measure !== 'repo') {
      for (const repo of [...new Set(hg.ar.concat(hg.br))].sort(cmpStr)) {
        const st = run('drop', repo);
        if (st.n_a >= T.MIN_SIDE && st.n_b >= T.MIN_SIDE) { loro.total++; loro.agree += agrees(measure, head, st, T.MIN_SIDE, false) ? 1 : 0; }
      }
      strata = { n: 0, same: 0, opposite: 0 };
      for (const repo of sets.repos) {
        const st = run('only', repo);
        if (st.n_a < T.MIN_STRATUM_SIDE || st.n_b < T.MIN_STRATUM_SIDE) continue;
        strata.n++;
        if (head.direction !== 0 && st.direction === head.direction) strata.same++;
        else if (head.direction !== 0 && st.direction === -head.direction && floorMet(measure, st, false)) strata.opposite++;
      }
    }
    const perHalf = {};
    let halfOk = true, halfAgree = true;
    for (const parity of [0, 1]) {
      const st = run('parity', parity), yes = agrees(measure, head, st, T.MIN_HALF, true);
      halfOk = halfOk && st.n_a >= T.MIN_HALF && st.n_b >= T.MIN_HALF; halfAgree = halfAgree && yes;
      perHalf[String(parity)] = { n_a: st.n_a, n_b: st.n_b, permille: pm(st), agrees: yes };
    }
    return { forks: { distinct: new Set(Object.values(sets.aliases)).size, evaluable, agree, per_fork: perFork }, loro, strata,
      halves: { evaluable: halfOk, replicates: halfOk ? halfAgree : null, per_half: perHalf } };
  }
  function grade(look) {                        // looks.grade: the first rule that matches wins
    if (look.status !== 'evaluated') return [null, 'not_run'];
    if (!look.effect.floor_met) return [null, 'effect_below_floor'];
    const nm = look.null_model, [an, ad] = T.ALPHA;
    if (ad * (nm.k_ge + 1) > an * (nm.M + 1)) return [null, 'not_surprising'];
    const [qn, qd] = look.q, [tn, td] = T.Q_BH, ck = look.checks;
    if (td * qn > tn * qd) return ['S', 'not_surviving_BH'];
    if (ck.strata && ck.strata.opposite >= 1) return ['S', 'contrary_effect_in_a_repo'];
    const [cn, cd] = T.Q_CONFIRM;
    if (cd * qn > cn * qd) return ['L', 'q_above_confirm_level'];
    if (ck.strata !== null && ck.strata.n === 0) return ['L', 'no_stratum_with_both_groups'];
    const h = ck.halves;
    if (!h.evaluable) return ['L', 'halves_not_evaluable'];
    if (h.replicates === false) return ['L', 'not_replicated_in_halves'];
    const f = ck.forks;
    if (f.evaluable < 3) return ['L', 'fewer_than_3_distinct_forks_evaluable'];
    if (f.agree < f.evaluable) return ['L', 'fork_disagreement'];
    const lo = ck.loro;
    if (lo.total < 3) return ['L', 'leave_one_repo_out_unavailable'];
    if (lo.agree < lo.total) return ['L', 'leave_one_repo_out_disagreement'];
    return ['C', 'all_checks_pass'];
  }
  const binLabel = (measure, k) => measure === 'hour4' ? String(4 * k).padStart(2, '0') + '-' + String(4 * k + 3).padStart(2, '0') : measure === 'weekday' ? WEEKDAYS[k] : String(k);
  function renderText(look) {
    if (look.status !== 'evaluated') return `${look.id}: not run (${look.reason})`;
    const e = look.effect, nm = look.null_model, word = GRADE_WORD[look.grade];
    const effect = e.kind === 'cliff_delta' ? "Cliff's delta " + decimalStr(e.permille, 1000, 3) + ' (positive means the first group is larger)'
      : 'distribution distance ' + decimalStr(e.permille, 1000, 3) + ' (largest gap in ' + binLabel(look.measure, e.dominant_bin) + ')';
    return `${look.groups.a.label} vs ${look.groups.b.label}, ${MEASURE_PHRASE[look.measure]}: ${effect}; ${nm.k_ge} of ${nm.M} label shuffles ` +
      `were at least as extreme (q ${look.q_text}); ${word ? word : `no grade (${look.grade_reason})`}.`;
  }
  const choosePair = rows => {
    const counts = new Map(PAIR_ORDER.map(c => [c, 0]));
    for (const r of rows) if (!r.m && counts.has(r.c)) counts.set(r.c, counts.get(r.c) + 1);
    const ok = PAIR_ORDER.filter(c => counts.get(c) >= T.MIN_SIDE).sort((x, y) => (counts.get(y) - counts.get(x)) || (PAIR_ORDER.indexOf(x) - PAIR_ORDER.indexOf(y)));
    return ok.length < 2 ? null : [ok[0], ok[1]];
  };
  const label = (labels, c) => (Object.hasOwn(labels, c) ? labels[c] : c);
  const clampM = w => Math.max(T.M_MIN, Math.min(T.M_MAX, Math.floor(T.WORK_CAP / Math.max(1, w))));

  // looks.run: the twelve declared looks from the rows and the class labels. maxDraws refuses before any permutation is drawn.
  function runLooks(rows, labels, maxDraws) {
    const sets = makeSets(rows), pair = choosePair(rows), looks = {}, heads = {};
    const pairReason = pair ? null : `fewer than two author classes have ${T.MIN_SIDE} or more commits outside merges`;
    const blank = (id, kind, measure, status, reason, groups) => ({ id, kind, measure, status, reason, grade_kind: 'robustness', groups, effect: null,
      null_model: null, q: null, q_text: null, checks: null, grade: null, grade_reason: 'not_run', say_at_most: null, text: '', highlight: null, scope: SCOPE });
    let W = 0;
    for (const [id, kind, measure] of DECLARED) {
      if (!pair) { looks[id] = blank(id, kind, measure, 'not_run', pairReason, null); continue; }
      const classes = kind === 'contrast' ? pair : id.split(':')[1] === 'first' ? pair[0] : pair[1], { rs, gaps } = sets.get('fork', 'all');
      const g = cut(kind, measure, classes, rs, gaps);
      const lab = kind === 'contrast' ? [label(labels, pair[0]), label(labels, pair[1])] : ['later commits of ' + label(labels, classes), 'earlier commits of ' + label(labels, classes)];
      const groups = { a: { label: lab[0], n: g.av.length }, b: { label: lab[1], n: g.bv.length } };
      if (Math.min(g.av.length, g.bv.length) < T.MIN_SIDE) {
        looks[id] = blank(id, kind, measure, 'not_run', `a group has fewer than ${T.MIN_SIDE} commits (n_a=${g.av.length}, n_b=${g.bv.length})`, groups);
      } else {
        looks[id] = blank(id, kind, measure, 'evaluated', null, groups); heads[id] = { classes, g }; W += Math.min(g.av.length, g.bv.length);
      }
    }
    const M = clampM(W);
    if (maxDraws !== undefined && W * M > maxDraws) throw new RangeError(`recomputing needs ${W * M} permutation draws; this check takes at most ${maxDraws}`);
    for (const [id, kind, measure] of DECLARED) {
      if (!heads[id]) continue;
      const { classes, g } = heads[id], head = stat(measure, g.av, g.bv), look = looks[id], seedLabel = SEED_PREFIX + id;
      const kGe = nullCount(measure, g.av, g.bv, head, M, seedLabel);
      look.effect = effectOf(measure, head);
      look.null_model = { name: NULL_NAME, M, k_ge: kGe, seed: seedText(seedHiLo(seedLabel)), p: [kGe + 1, M + 1], p_text: decimalStr(kGe + 1, M + 1, 4) };
      look.checks = checks(kind, measure, classes, sets, head, g);
    }
    const qs = bhQ(DECLARED.map(([id]) => [id, looks[id].status === 'evaluated' ? [looks[id].null_model.k_ge + 1, M + 1] : [1, 1]]), DECLARED.length);
    const byGrade = { C: 0, L: 0, S: 0, none: 0 };
    for (const [id, kind] of DECLARED) {
      const look = looks[id];
      if (look.status === 'evaluated') { const q = qs.get(id); look.q = [q[0], q[1]]; look.q_text = decimalStr(q[0], q[1], 4); }
      [look.grade, look.grade_reason] = grade(look);
      look.say_at_most = look.grade ? SAY_AT_MOST[look.grade] : null;
      look.text = renderText(look);
      if (kind === 'contrast' && pair) look.highlight = { who: pair[0], xray: true };
      byGrade[look.grade || 'none']++;
    }
    const evaluated = Object.keys(heads).length;
    return { section: { pair, pair_reason: pairReason,
      ledger: { declared: DECLARED.length, evaluated, not_run: DECLARED.length - evaluated, by_grade: byGrade, alpha: T.ALPHA.slice(), q_threshold: T.Q_BH.slice(),
        q_confirm: T.Q_CONFIRM.slice(), M, W, work_cap: T.WORK_CAP, work_exceeded: W * M > T.WORK_CAP, manifest_sha256: manifestSha256(), bh_note: BH_NOTE },
      grade_rule: gradeRule(), say_at_most: { ...SAY_AT_MOST }, scope: SCOPE, findings: DECLARED.map(([id]) => looks[id]) }, draws: W * M };
  }

  // Type-strict difference of two JSON values, as paths only (looks._diff): a value is never printed. `stored` is what the bundle holds.
  function diffPaths(path, got, want, out) {
    if (want !== null && typeof want === 'object' && !Array.isArray(want)) {
      if (got === null || typeof got !== 'object' || Array.isArray(got)) { out.push(path); return; }
      for (const k of [...new Set([...Object.keys(got), ...Object.keys(want)])].sort(cmpStr)) {
        if (!Object.hasOwn(got, k) || !Object.hasOwn(want, k)) out.push(`${path}.${k}`); else diffPaths(`${path}.${k}`, got[k], want[k], out);
      }
    } else if (Array.isArray(want)) {
      if (!Array.isArray(got) || got.length !== want.length) { out.push(path); return; }
      want.forEach((w, n) => diffPaths(`${path}[${n}]`, got[n], w, out));
    } else if (got !== want || typeof got !== typeof want) out.push(path);
  }
  const WORDING = /(?:^|\.)(?:text|scope|say_at_most|grade_rule|bh_note|reason|pair_reason)(?:\.|$|\[)/;     // fixed prose; every other path is a number, a flag or an id

  // ---- an ad hoc comparison of two groups of commit rows: the same statistic, null and seeding rule as a declared contrast, no checks, no grade.
  // The question is the SAMPLE, not the words that asked for it: the seed comes from the digests of the two value lists, put in digest order, so
  // (a, b) and (b, a), a filter spelled two ways, and a fork that changes nobody in either group are one question with one stream. A question cannot
  // be re-rolled by rewording it. `swapped` says the asker's order was the other one.
  function sample(rows, idxA, idxB, measure, fork) {
    if (!EDA_FORKS.includes(fork)) throw new RangeError('unknown fork');
    if (!['lines', 'files', 'gap_s', 'hour4', 'weekday', 'repo'].includes(measure)) throw new RangeError('unknown measure');
    const inA = new Set(idxA), inB = new Set(idxB);
    for (const i of inB) if (inA.has(i)) throw new RangeError('the two groups share commits: a label permutation needs disjoint groups');
    const { rs, gaps } = makeSets(rows).get('fork', fork), g = { av: [], bv: [] };
    for (let k = 0; k < rs.length; k++) {
      const a = inA.has(rs[k].i), b = inB.has(rs[k].i);
      if (!a && !b) continue;
      if (measure === 'gap_s' && gaps[k] === null) continue;                     // the first row of the restricted set has no gap: unknown, not zero
      (a ? g.av : g.bv).push(measure === 'gap_s' ? gaps[k] : valueOf(measure, rs[k]));
    }
    const da = sha256Text(canon(g.av)), db = sha256Text(canon(g.bv)), swapped = cmpStr(db, da) < 0;
    return { ...g, swapped, label: ADHOC_PREFIX + canon({ measure, a: swapped ? db : da, b: swapped ? da : db }) };
  }
  const adhocM = w => Math.max(T.M_MIN, Math.min(T.M_MAX, Math.floor(LIMITS.ADHOC_CAP / w)));     // looks.compare: the same clamp as a declared look, on its own cap
  // The test on a sample, in canonical order (the group with the smaller digest first); the caller turns it back with flipEffect when `swapped`.
  function compare(s, measure) {
    const [x, y] = s.swapped ? [s.bv, s.av] : [s.av, s.bv], w = Math.min(x.length, y.length);
    if (w < T.MIN_SIDE) throw new RangeError(`a group has fewer than ${T.MIN_SIDE} commits (n_a=${s.av.length}, n_b=${s.bv.length})`);
    const M = adhocM(w);
    if (w * M > LIMITS.ADHOC_CAP) throw new RangeError(`the smaller group has ${w} commits; a comparison takes at most ${Math.floor(LIMITS.ADHOC_CAP / T.M_MIN)}`);
    const head = stat(measure, x, y), kGe = nullCount(measure, x, y, head, M, s.label);
    return { n_first: x.length, n_second: y.length, effect: effectOf(measure, head), M, k_ge: kGe, seed: seedText(seedHiLo(s.label)), p: [kGe + 1, M + 1], draws: w * M };
  }
  const flipEffect = e => ({ ...e, num: e.kind === 'cliff_delta' ? 0 - e.num : e.num, permille: e.kind === 'cliff_delta' ? 0 - e.permille : e.permille, direction: 0 - e.direction });

  return Object.freeze({ T, DECLARED, LIMITS, SEED_PREFIX, ADHOC_PREFIX, NULL_NAME, SCOPE, WORDING, canon, sha256Text, manifestSha256, seedHiLo, seedText, splitMix, selfCheck,
    cmpStr, decimalStr, checkRows, runLooks, bhQ, sample, compare, diffPaths, doubledMidranks, cliffCounts, tvParts, permCountCliff, permCountTv, nullCount, frac,
    hex, sha256, sample, flipEffect, binLabel, MEASURE_PHRASE, ORDINAL, adhocM, clampM });
})();
// END EDA INFERENCE KERNEL

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
  if (B.eda) {
    // Capped at six lines: the exploration layer is a pointer to the eda tool, not a second digest.
    const E = B.eda, K = (E.looks || {}).ledger, lab = (E.classes || {}).labels || {}, pair = (E.classes || {}).pair;
    L.push('\nEXPLORATION (rule-bound looks computed by code, no model; the eda tool looks them up):');
    if (K) {
      const g = K.by_grade || {};
      L.push(`- ${K.declared} declared looks, m fixed at ${K.declared}${pair ? ', pair ' + pair.map(c => lab[c] || c).join(' vs ') : ', no pair with 20 commits on each side'}: ${K.evaluated} evaluated, ${K.not_run} not run; Confirmed ${g.C || 0}, Likely ${g.L || 0}, Suspected ${g.S || 0}, ungraded ${g.none || 0}.`);
    }
    const checks = ((E.audit || {}).checks || []).filter(c => c.status === 'flag');
    if (E.audit) L.push(`- Audit: ${checks.length} of ${E.audit.checks.length} checks flagged (${['C', 'L', 'S'].map(x => x + ' ' + checks.filter(c => c.grade === x).length).join(', ')})${checks.length ? ': ' + checks.map(c => c.id + ' ' + c.count).join(', ') : ''}.`);
    const graded = (((E.looks || {}).findings) || []).filter(f => f.grade).sort((a, b) => EDA_GRADE_RANK[b.grade] - EDA_GRADE_RANK[a.grade]).slice(0, 2);
    if (K) L.push('- ' + (graded.length ? 'Top findings: ' + graded.map(f => `${f.id} [${f.grade}] ${clip(f.text, 150)}`).join(' | ') : 'No look is graded.'));
    const by = (E.flags || {}).by_rule || {};
    if (E.flags) L.push('- Flags (commit indices; the highlight tool lights them with flag and flag_grade): ' + Object.entries(by).map(([r, v]) => `${r} C ${v.counts.C} L ${v.counts.L} S ${v.counts.S} of ${v.counts.evaluable}`).join('; ') + '.');
    L.push('- A grade is robustness over declared checks, never a probability; slices asked through eda are descriptive and ungraded.');
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

// ---------------------------------------------------------------- the eda tool (x3dray/eda.py and looks.py stored in bundle.eda)
const EDA_TOPICS = ['ledger', 'finding', 'audit', 'profile', 'flags', 'rhythm', 'concentration', 'slice', 'method', 'recompute', 'compare'];
const EDA_DESC = 'Exploration under rules, from code with no model: stored profile, rhythm, concentration, audit and graded looks. ' +
  'topic: ledger (the 12 declared looks, their grades and what a grade means), finding (one look in full; id), audit (data checks and per-repo coverage; grade), ' +
  'profile (size, files and gap distributions per slice; repo, who, fork, measure), flags (atypical commits; rule size_high or gap_long, grade), ' +
  'rhythm (hours, quiet window, bursts; repo, who), concentration (churn across files; repo), method (thresholds, forks, rules), ' +
  'slice (compute a distribution over commits filtered by repo, who, from, to, day, session, hour, weekday, min_lines, max_lines, rule, grade; fork, measure; ungraded), ' +
  'recompute (re-derive the 12 looks from the rows here; match or differing paths), ' +
  'compare (a vs b, disjoint commit groups: exact permutation test of a measure, ungraded; q counts every look taken). ' +
  'A grade is robustness over declared checks, never a probability. Unknown is not zero; private repos give counts only.';
const EDA_MEASURES_ALL = ['lines', 'files', 'gap_s', 'hour4', 'weekday', 'repo'];
const EDA_GROUP_KEYS = ['repo', 'who', 'from', 'to', 'day', 'hour', 'weekday', 'min_lines', 'max_lines'];
const EDA_GROUP = { type: 'object', additionalProperties: false, description: 'compare: one group of commits, filtered like slice; give at least one filter',
  properties: { repo: { type: 'string' }, who: { type: 'string' }, from: { type: 'string' }, to: { type: 'string' }, day: { type: 'string' }, hour: { type: 'integer' },
    weekday: { type: 'string' }, min_lines: { type: 'integer' }, max_lines: { type: 'integer' } } };
const EDA_SCHEMA = { type: 'object', additionalProperties: false, required: ['topic'], properties: {
  topic: { type: 'string', enum: EDA_TOPICS },
  id: { type: 'string', description: 'a look id such as contrast:lines (finding)' },
  repo: { type: 'string', description: 'repo name; several separated by commas' },
  who: { type: 'string', description: 'author class: you (HagopJay), ai (Claude), bot, other, upstream' },
  from: { type: 'string', description: 'first day, YYYY-MM-DD (UTC); slice' },
  to: { type: 'string', description: 'last day, YYYY-MM-DD (UTC); slice' },
  day: { type: 'string', description: 'one day, YYYY-MM-DD; slice' },
  session: { type: 'string', description: 'a Claude session hash (s-...); slice' },
  hour: { type: 'integer', description: 'UTC author hour, 0 to 23; slice' },
  weekday: { type: 'string', description: 'UTC weekday: 0 (Monday) to 6, or mon to sun; slice' },
  min_lines: { type: 'integer', description: 'at least this many lines changed (add+del); slice' },
  max_lines: { type: 'integer', description: 'at most this many lines changed; slice' },
  fork: { type: 'string', enum: EDA_FORKS, description: 'all (default for compare), no_merge (default for slice), default_branch, no_mass' },
  measure: { type: 'string', enum: EDA_MEASURES_ALL, description: 'lines (add+del), files, gap_s (seconds since the previous commit); default lines; compare also takes hour4, weekday, repo' },
  a: EDA_GROUP, b: EDA_GROUP,
  rule: { type: 'string', enum: ['size_high', 'gap_long'], description: 'flag rule (flags; slice keeps only commits it flags)' },
  grade: { type: 'string', enum: ['C', 'L', 'S'], description: 'this grade or better (C best)' },
  limit: { type: 'integer', description: 'rows to return, 1 to 40 (default 15)' } } };
const EDA_WORDING = 'a grade here is robustness over declared checks, never a probability';
const EDA_RESULT_BUDGET = 26000;     // bytes for one result before cap() could act; cap() would add the keys the eda tool never emits

// First `limit` rows of `rows`, fewer when the result would pass the byte budget; the counters always add up.
function edaList(base, name, rows, limit) {
  const make = k => ({ ...base, [name]: rows.slice(0, k), [name + '_total']: rows.length, [name + '_omitted']: rows.length - k });
  let k = Math.min(rows.length, limit);
  while (k > 0 && utf8Bytes(make(k)) > EDA_RESULT_BUDGET) k--;
  return make(k);
}
const edaLimit = q => clamp(Math.trunc(num(q.limit, 15)), 1, 40);
const edaLeast = g => (g ? EDA_GRADE_RANK[str(g).toUpperCase()] || 1 : 1);
const edaPrivateRepos = B => new Set((B.repos || []).filter(r => r.visibility === 'private').map(r => r.name));

// The stored slice ids named by repo or who; null when neither is given.
function edaSliceIds(B, q) {
  const r = str(q.repo), w = str(q.who);
  if (r && w) throw new Error('give repo or who, not both: a stored slice is the family, one author class or one repo');
  if (r) return repoList(B, q.repo).map(n => 'repo:' + n);
  if (w) {
    const ks = kindsOf(q.who);
    if (ks.length !== 1) throw new Error('who names one author class here: you, ai, bot, other or upstream');
    return ['class:' + ks[0]];
  }
  return null;
}
const edaOnly = (q, keys) => Object.fromEntries(keys.filter(k => str(q[k])).map(k => [k, str(q[k])]));

function edaCoverageRow(r) {
  // Private repos are counts only by construction: whatever the stored row holds, only these fields leave.
  if (r.visibility !== 'private') return r;
  return { repo: r.repo, visibility: r.visibility, n: r.n, n_nonmerge: r.n_nonmerge, n_default: r.n_default, tree: r.tree, eligible: r.eligible,
    unknown: 'files, paths and kinds are not measured for a private repo' };
}

function edaFlagRow(B, c, priv) {
  const r = commitRow(B, c);
  if (priv.has(c.r)) { delete r.sha; delete r.subject; delete r.sessions; r.private = true; }
  return r;
}

// An ad hoc look as the closed, name-free object x3dray/investigation.py records: repos are ordinals of B.repos, a grade is explicit, nothing is
// free text. Built after the filter was accepted, so every value is already known to be valid. Its insertion order is fixed, so JSON.stringify
// of it is one text per distinct look.
function edaSelector(B, p) {
  const s = {};
  if (p.repo && p.repo.length) s.repo = p.repo.map(n => B.repos.findIndex(r => r.name === n)).sort((a, b) => a - b);
  if (p.who && p.who.length) s.who = p.who.slice().sort();
  for (const k of ['from', 'to', 'day', 'session']) if (p[k]) s[k] = p[k];
  if (p.rule) { s.rule = p.rule; s.grade = (p.grade || 'S').toUpperCase(); }
  for (const k of ['hour', 'weekday', 'min_lines', 'max_lines']) if (p[k] !== null && p[k] !== undefined) s[k] = p[k];
  if (p.fork) { s.fork = p.fork; s.measure = p.measure; }
  return s;
}

// The distinct looks of one tool set, as an object with note(look) and the number asked. look is {kind: 'declared', id} (a stored look read
// through the finding topic: not counted in asked, as before), or {kind: 'slice'|'rows', selector}. A host that keeps them elsewhere, the
// investigation chain, passes its own object through makeTools(B, scene, {looks}) and the count then comes from the chain; this default keeps
// them in memory for this tool set only.
function edaLookSet() {
  const seen = new Set();
  let asked = 0;
  return {
    note(look) {
      const key = look.kind === 'declared' ? 'declared:' + look.id : look.kind + ':' + JSON.stringify(look.selector);
      if (seen.has(key)) return;
      seen.add(key);
      if (look.kind !== 'declared') asked++;
    },
    get asked() { return asked; },
  };
}

// A request to query_commits, activity or highlight that carries an hour, weekday, size or flag filter is a look at the data
// just as a slice is, so it joins the same set; plain navigation (a repo, a day, words) does not. Its kind is 'rows', so it never collides
// with a slice. Call it after the filter has been accepted.
function edaNoteLook(B, q, looks) {
  const hour = edaWhole(q.hour, 'hour', 0, 23), weekday = edaWeekdayArg(q.weekday), flag = str(q.flag);
  const lo = edaWhole(q.min_lines, 'min_lines', 0, Infinity), hi = edaWhole(q.max_lines, 'max_lines', 0, Infinity);
  if (hour === null && weekday === null && lo === null && hi === null && !flag) return;
  looks.note({ kind: 'rows', selector: edaSelector(B, { repo: str(q.repo) ? repoList(B, q.repo) : [], who: str(q.who) ? kindsOf(q.who) : [],
    from: str(q.from), to: str(q.to), day: str(q.day), session: str(q.session) ? sessionId(B, q.session) : '', rule: flag, grade: str(q.flag_grade),
    hour, weekday, min_lines: lo, max_lines: hi }) });
}

const EDA_ASKED_NOTE = 'distinct eda slices and compare questions plus query_commits, activity and highlight calls that carry an hour, weekday, size or flag filter; plain lookups are not counted';

// A filter in one spelling: repo names resolved, classes as classes, a weekday as its number. Call it after commitFilter has accepted q.
function edaFilterEcho(B, q) {
  const hour = edaWhole(q.hour, 'hour', 0, 23), weekday = edaWeekdayArg(q.weekday);
  const lo = edaWhole(q.min_lines, 'min_lines', 0, Infinity), hi = edaWhole(q.max_lines, 'max_lines', 0, Infinity);
  return { ...(str(q.repo) ? { repo: repoList(B, q.repo) } : {}), ...(str(q.who) ? { who: kindsOf(q.who) } : {}),
    ...edaOnly(q, ['from', 'to', 'day', 'session', 'rule', 'grade']), ...(hour !== null ? { hour } : {}), ...(weekday !== null ? { weekday } : {}),
    ...(lo !== null ? { min_lines: lo } : {}), ...(hi !== null ? { max_lines: hi } : {}) };
}

function edaSlice(B, E, q, looks) {
  const fork = str(q.fork) || 'no_merge', measure = str(q.measure) || 'lines';
  if (!EDA_FORKS.includes(fork)) throw new Error(`unknown fork ${JSON.stringify(fork.slice(0, 40))}; forks: ${EDA_FORKS.join(', ')}`);
  if (!EDA_MEASURES[measure]) throw new Error(`unknown measure ${JSON.stringify(measure.slice(0, 40))}; measures: ${Object.keys(EDA_MEASURES).join(', ')}`);
  if (str(q.grade) && !str(q.rule)) throw new Error('grade needs a rule: it is the grade of that flag');
  const filter = { repo: q.repo, who: q.who, from: q.from, to: q.to, day: q.day, session: q.session, flag: q.rule, flag_grade: q.grade,
    hour: q.hour, weekday: q.weekday, min_lines: q.min_lines, max_lines: q.max_lines };
  const pass = commitFilter(B, filter);
  // Echoed in one spelling (a weekday as its number) so that two spellings of one slice count once in looks_asked_this_session.
  const echo = edaFilterEcho(B, q);
  const look = { kind: 'slice', selector: edaSelector(B, { repo: echo.repo, who: echo.who, from: echo.from, to: echo.to, day: echo.day,
    session: str(q.session) ? sessionId(B, q.session) : '', rule: echo.rule, grade: echo.grade, hour: echo.hour === undefined ? null : echo.hour,
    weekday: echo.weekday === undefined ? null : echo.weekday, min_lines: echo.min_lines === undefined ? null : echo.min_lines,
    max_lines: echo.max_lines === undefined ? null : echo.max_lines, fork, measure }) };
  const matched = B.commits.filter(pass);
  if (matched.length > 50000) throw new Error(`the filter matches ${matched.length} commits; a slice takes at most 50000, so narrow it`);
  looks.note(look);        // after the refusals: a request that returns no answer is not a look
  const members = matched.filter(c => edaInFork(c, fork));
  const out = { route: 'compute', summary: '', topic: 'slice', filter: echo, fork, measure, matched: matched.length, members: members.length, statistics: null,
    grade: null, grade_note: 'descriptive and ungraded: outside the declared family of 12 looks', looks_asked_this_session: looks.asked,
    looks_asked_note: EDA_ASKED_NOTE,
    declared_looks: ((E.looks || {}).ledger || {}).declared || 12, computed_by: 'edaSummarize (twin of x3dray/stats.py summarize)' };
  const priv = edaPrivateRepos(B);
  if (matched.length && members.length < 20 && matched.every(c => priv.has(c.r))) {
    out.summary = 'private: counts only';
    out.note = 'every matched commit is in a private repo and fewer than 20 remain in this fork: the count is all that is shown';
    return out;
  }
  const values = edaMeasureValues(members, measure);
  out.statistics = edaSummarize(values, EDA_MEASURES[measure].cap, EDA_MEASURES[measure].unit);
  const st = out.statistics;
  out.summary = values.length ? `${measure} over ${st.n} of ${members.length} commits: median ${st.p50}, p90 ${st.p90}, max ${st.max}` : `no ${measure} values: ${members.length} commits in this slice and fork`;
  return out;
}

// ---------------------------------------------------------------- recompute and compare: the inference of EDA_INFER behind the eda tool
const edaRowsCache = new WeakMap();
function edaRows(B) {       // the commit rows checked once per commit list; a row that breaks the stats.py contract refuses the whole call
  const hit = edaRowsCache.get(B.commits);
  if (hit && hit.n === B.commits.length) return hit.rows;
  const rows = EDA_INFER.checkRows(B.commits);
  edaRowsCache.set(B.commits, { n: B.commits.length, rows });
  return rows;
}
const edaRecomputed = new WeakMap();      // eda object -> its recomputation, so a button and Ask share one run

// The twelve declared looks again, from the rows and the stored class labels, in this runtime; the stored section must come back byte for byte.
function edaRecompute(B, E) {
  const I = EDA_INFER, L = E.looks;
  if (!L || !Array.isArray(L.findings)) throw new Error('eda has no looks section to recompute');
  let r = edaRecomputed.get(E);
  if (!r) {
    const bad = I.selfCheck();
    if (bad.length) throw new Error(`this runtime failed its known-answer checks (${bad.join(', ')}); nothing was recomputed`);
    const labels = (E.classes || {}).labels;
    if (!labels || typeof labels !== 'object' || Array.isArray(labels) || Object.values(labels).some(v => typeof v !== 'string')) throw new Error('eda.classes.labels is not a map of strings');
    const { section, draws } = I.runLooks(edaRows(B), labels, I.LIMITS.RECOMPUTE_DRAWS);
    const raw = [];
    I.diffPaths('looks', L, section, raw);
    const ids = section.findings.map(f => f.id), paths = raw.map(x => x.replace(/findings\[(\d+)\]/, (_, k) => `findings[${ids[+k] || k}]`));
    const digest = v => { try { return I.sha256Text(I.canon(v)); } catch (e) { return null; } };
    r = { section, draws, paths, wording: raw.filter(x => I.WORDING.test(x)).length, stored_sha256: digest(L), recomputed_sha256: digest(section), rows: B.commits.length };
    edaRecomputed.set(E, r);
  }
  const n = r.paths.length, numbers = n - r.wording;
  const rows = r.section.findings.map((f, k) => ({ id: f.id, status: f.status, grade: f.grade, k_ge: f.null_model ? f.null_model.k_ge : null, M: f.null_model ? f.null_model.M : null,
    q_text: f.q_text, match: !r.paths.some(x => x.startsWith(`looks.findings[${f.id}]`)) }));
  return edaList({ route: 'compute', topic: 'recompute',
    summary: n === 0 ? `recomputed from ${r.rows} commit rows in this runtime: the stored looks section matches byte for byte (${rows.length} looks, ${r.section.ledger.evaluated} evaluated)`
      : `recomputed from ${r.rows} commit rows: ${n} stored values differ (${numbers} numbers or flags, ${r.wording} fixed wording)`,
    match: n === 0, numbers_match: numbers === 0, wording_match: r.wording === 0, commit_rows: r.rows, draws: r.draws,
    stored_sha256: r.stored_sha256, recomputed_sha256: r.recomputed_sha256,
    digest_of: 'sha256 of the canonical JSON of eda.looks (sorted keys, no spaces, ASCII); Python: hashlib.sha256(eda.canonical_json(eda["looks"]).encode()).hexdigest()',
    kernel_checks: 'passed: sha256 of abc, the reference SplitMix64 words from seed 0, the seed of contrast:lines, the manifest of the declared battery is part of the section',
    inputs_not_recomputed: 'the commit rows themselves (the Python witness gate ties them to the dataset), eda.classes.labels (group names), and whether a stored number was written 1 or 1.0 (JSON.parse reads both as 1)',
    ledger: r.section.ledger, looks: rows, wording: EDA_WORDING }, 'differences', r.paths, 40);
}

// compare: one group of commits from a filter. Only filters computable from the stats.py row contract are allowed.
function edaGroup(B, v, name) {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error(`${name} must be an object of filters`);
  const extra = Object.keys(v).filter(k => !EDA_GROUP_KEYS.includes(k));
  if (extra.length) throw new Error(`${name} takes ${EDA_GROUP_KEYS.join(', ')}, not ${extra.join(', ')}`);
  const given = EDA_GROUP_KEYS.filter(k => v[k] !== undefined && v[k] !== null && v[k] !== '');
  if (!given.length) throw new Error(`${name} needs at least one filter: with none it would be the whole family`);
  const q = Object.fromEntries(given.map(k => [k, v[k]])), pass = commitFilter(B, q), echo = edaFilterEcho(B, q);
  return { echo, idx: B.commits.filter(pass).map(c => c.i) };
}
// What leaves the tool: a private repo is a count, never a name.
function edaShowGroup(B, E, echo) {
  const priv = edaPrivateRepos(B), lab = (E.classes || {}).labels || {}, pub = (echo.repo || []).filter(n => !priv.has(n)), nPriv = (echo.repo || []).length - pub.length;
  const shown = { ...echo }; delete shown.repo;
  if (pub.length) shown.repo = pub;
  if (nPriv) shown.private_repos = nPriv;
  const parts = [];
  if (echo.who) parts.push(echo.who.map(c => lab[c] || CLASS_NAMES[c] || c).join(' + '));
  if (echo.repo) parts.push('in ' + [...pub, ...(nPriv ? [nPriv === 1 ? 'a private repo' : `${nPriv} private repos`] : [])].join(', '));
  if (echo.day) parts.push('on ' + echo.day);
  if (echo.from || echo.to) parts.push(`from ${echo.from || 'the start'} to ${echo.to || 'the end'}`);
  if (echo.hour !== undefined) parts.push('UTC hour ' + echo.hour);
  if (echo.weekday !== undefined) parts.push(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][echo.weekday]);
  if (echo.min_lines !== undefined) parts.push('at least ' + echo.min_lines + ' lines');
  if (echo.max_lines !== undefined) parts.push('at most ' + echo.max_lines + ' lines');
  return { filter: shown, label: parts.join(', ').slice(0, 160) };
}

// A comparison as the closed, name-free look x3dray/investigation.py records: both groups as selectors (repos are ordinals), written in one
// canonical order so that (a, b) and (b, a) are one look, with the fork and the measure.
function edaCompareSelector(B, ea, eb, fork, measure) {
  const group = e => edaSelector(B, { repo: e.repo, who: e.who, from: e.from, to: e.to, day: e.day, hour: e.hour, weekday: e.weekday,
    min_lines: e.min_lines, max_lines: e.max_lines });
  const [x, y] = [group(ea), group(eb)].sort((p, r) => { const u = EDA_INFER.canon(p), v = EDA_INFER.canon(r); return u < v ? -1 : u > v ? 1 : 0; });
  return { a: x, b: y, fork, measure };
}

function edaCompare(B, E, q, looks, compared) {
  const I = EDA_INFER, L = E.looks || {}, fork = str(q.fork) || 'all', measure = str(q.measure) || 'lines';
  if (!EDA_FORKS.includes(fork)) throw new Error(`unknown fork ${JSON.stringify(fork.slice(0, 40))}; forks: ${EDA_FORKS.join(', ')}`);
  if (!EDA_MEASURES_ALL.includes(measure)) throw new Error(`unknown measure ${JSON.stringify(measure.slice(0, 40))}; measures: ${EDA_MEASURES_ALL.join(', ')}`);
  if (!Array.isArray(L.findings) || L.findings.length !== I.DECLARED.length) throw new Error('compare counts the 12 declared looks in its family, and this build has no stored looks');
  const ga = edaGroup(B, q.a, 'a'), gb = edaGroup(B, q.b, 'b'), sm = I.sample(edaRows(B), ga.idx, gb.idx, measure, fork), key = 'cmp|' + sm.label;
  const look = { kind: 'compare', selector: edaCompareSelector(B, ga.echo, gb.echo, fork, measure) };       // what a host records; names no repo
  let rec = compared.get(key);
  if (!rec) {
    if (compared.size >= I.LIMITS.ADHOC_LOOKS) throw new Error(`this tool set has already taken ${compared.size} comparisons, the most it takes: every one raises the number of looks behind q`);
    rec = { ...I.compare(sm, measure), measure, fork };
    compared.set(key, rec);
    looks.note(look);      // after every refusal, once per question
  }
  // The family: the 12 declared looks (their stored p, or 1 when not run), every comparison taken so far, and every other look asked, which counts through m with p = 1.
  const declared = L.findings.map(f => {
    const p = f.status === 'evaluated' && f.null_model ? f.null_model.p : [1, 1];
    if (!Array.isArray(p) || p.length !== 2 || !p.every(Number.isInteger) || !(p[0] > 0 && p[0] <= p[1])) throw new Error(`stored p of ${str(f.id).slice(0, 40)} is not a pair of integers`);
    return [f.id, p];
  });
  const m = I.DECLARED.length + looks.asked, qs = I.bhQ(declared.concat([...compared].map(([k, r]) => [k, r.p])), m), qv = qs.get(key);
  const effect = sm.swapped ? I.flipEffect(rec.effect) : rec.effect, [na, nb] = sm.swapped ? [rec.n_second, rec.n_first] : [rec.n_first, rec.n_second];
  const A = edaShowGroup(B, E, ga.echo), Bg = edaShowGroup(B, E, gb.echo), priv = edaPrivateRepos(B);
  const binPriv = measure === 'repo' && priv.has(effect.dominant_bin);
  const shownEffect = { ...effect, dominant_bin: binPriv ? null : effect.dominant_bin, ...(binPriv ? { dominant_bin_private: true } : {}) };
  const pText = EDA_INFER.decimalStr(rec.p[0], rec.p[1], 4), qText = EDA_INFER.decimalStr(qv[0], qv[1], 4);
  const eff = effect.kind === 'cliff_delta' ? "Cliff's delta " + I.decimalStr(effect.permille, 1000, 3) + ' (positive means the first group is larger)'
    : 'distribution distance ' + I.decimalStr(effect.permille, 1000, 3) + ' (largest gap in ' + (binPriv ? 'a private repo' : I.binLabel(measure, effect.dominant_bin)) + ')';
  const text = `${A.label} vs ${Bg.label}, ${I.MEASURE_PHRASE[measure]}: ${eff}; ${rec.k_ge} of ${rec.M} label shuffles were at least as extreme (p ${pText}, q ${qText} over m = ${m} looks); ungraded.`;
  const [an, ad] = I.T.ALPHA, [tn, td] = I.T.Q_BH;
  const taken = [...compared].map(([k, r], n) => ({ n: n + 1, id: 'cmp-' + I.sha256Text(k).slice(0, 8), measure: r.measure, fork: r.fork, p: r.p, q: qs.get(k) }));
  return { route: 'compute', topic: 'compare', summary: text, text, look,
    question: { a: A.filter, b: Bg.filter, fork, measure, note: 'the question is the sample: (a, b) and (b, a), or two spellings of one group, draw one stream' },
    groups: { a: { label: A.label, n: na }, b: { label: Bg.label, n: nb } }, effect: shownEffect,
    null_model: { name: I.NULL_NAME, M: rec.M, k_ge: rec.k_ge, seed: rec.seed, p: rec.p, p_text: pText, draws: rec.draws, draws_cap: I.LIMITS.ADHOC_CAP },
    q: qv, q_text: qText, p_at_most_alpha: ad * rec.p[0] <= an * rec.p[1], q_at_most_q_threshold: td * qv[0] <= tn * qv[1],
    family: { declared: I.DECLARED.length, comparisons: compared.size, other_looks: Math.max(0, looks.asked - compared.size), m,
      note: 'q is Benjamini-Hochberg over the 12 declared looks (stored p), every comparison taken so far, and every other look asked (p = 1); the next look raises m',
      counted_in: 'this tool set only: a page reload or a new MCP process counts again from 12' },
    taken, grade: null, grade_note: 'ungraded: a grade needs the declared robustness checks, which exist only for the 12 declared looks; read p and q as description',
    say_at_most: 'a difference in these recorded commits that the label shuffle did or did not reproduce; not a graded finding, not causal', scope: I.SCOPE,
    looks_asked_this_session: looks.asked, looks_asked_note: EDA_ASKED_NOTE, declared_looks: I.DECLARED.length,
    declared_p_source: edaRecomputed.has(E) && edaRecomputed.get(E).paths.length === 0 ? 'recomputed in this runtime and matching the stored section' : 'stored in bundle.eda (the Python witness gate recomputes them; the recompute topic does it here)',
    computed_by: 'EDA_INFER.compare (twin of x3dray/looks.py compare)', wording: EDA_WORDING };
}

// The arguments each topic reads, besides limit (a page size: it never changes which data an answer describes). EDA_SCHEMA is one flat object, so without this a topic would take any of them and drop the
// ones it does not use, answering a different question from the one asked.
const EDA_TOPIC_ARGS = {
  ledger: [], finding: ['id'], audit: ['grade'], profile: ['repo', 'who', 'fork', 'measure'], flags: ['rule', 'grade'],
  rhythm: ['repo', 'who'], concentration: ['repo'], method: [],
  slice: ['repo', 'who', 'from', 'to', 'day', 'session', 'hour', 'weekday', 'min_lines', 'max_lines', 'rule', 'grade', 'fork', 'measure'],
  recompute: [], compare: ['a', 'b', 'fork', 'measure'],
};
function edaRefuseUnusedArgs(topic, q) {
  const takes = EDA_TOPIC_ARGS[topic];
  if (!takes) return;
  const given = Object.keys(q).filter(k => k !== 'topic' && k !== 'limit' && q[k] !== undefined && q[k] !== null && q[k] !== '');
  const unused = given.filter(k => !takes.includes(k));
  if (unused.length) throw new Error(`topic ${topic} does not use ${unused.join(', ')}; it takes ${takes.length ? takes.join(', ') : 'no arguments'}`);
}

function edaRun(B, q, looks, compared) {
  const E = B.eda;
  if (!E) return { route: 'lookup', status: 'unavailable', summary: 'eda unavailable: eda was not computed for this build', reason: 'eda was not computed for this build' };
  const topic = str(q.topic), L = E.looks || {}, lim = edaLimit(q);
  edaRefuseUnusedArgs(topic, q);
  if (topic === 'ledger') {
    const K = L.ledger || {}, g = K.by_grade || {}, lab = (E.classes || {}).labels || {}, pair = (E.classes || {}).pair;
    const rows = (L.findings || []).map(f => ({ id: f.id, kind: f.kind, measure: f.measure, grade: f.grade, grade_reason: f.grade_reason,
      effect_permille: f.effect ? f.effect.permille : null, direction: f.effect ? f.effect.direction : null,
      n_a: f.groups ? f.groups.a.n : null, n_b: f.groups ? f.groups.b.n : null, p_text: f.null_model ? f.null_model.p_text : null,
      q_text: f.q_text, text: f.text }));
    return edaList({ route: 'lookup', summary: `${K.declared} declared looks: ${K.evaluated} evaluated, ${K.not_run} not run; Confirmed ${g.C || 0}, Likely ${g.L || 0}, Suspected ${g.S || 0}, ungraded ${g.none || 0}`,
      topic, ledger: K, looks_asked_this_session: looks.asked, looks_asked_note: EDA_ASKED_NOTE,
      pair: pair ? pair.map(c => lab[c] || c) : null, pair_reason: (E.classes || {}).pair_reason || null, grade_rule: L.grade_rule,
      scope: L.scope, say_at_most: L.say_at_most, bh_note: K.bh_note, wording: EDA_WORDING }, 'findings', rows, 40);
  }
  if (topic === 'finding') {
    const ids = (L.findings || []).map(f => f.id), id = str(q.id), f = (L.findings || []).find(x => x.id === id);
    if (!f) throw new Error(`${id ? 'no look ' + JSON.stringify(id.slice(0, 60)) : 'finding needs an id'}; known: ${ids.join(', ')}`);
    looks.note({ kind: 'declared', id: f.id });
    return { route: 'lookup', summary: `${f.id}: ${f.grade ? 'grade ' + f.grade : 'no grade'} (${f.grade_reason})`, topic, finding: f, grade_rule: L.grade_rule, wording: EDA_WORDING };
  }
  if (topic === 'audit') {
    const A = E.audit || {}, least = edaLeast(q.grade);
    const checks = (A.checks || []).filter(c => !str(q.grade) || (c.status === 'flag' && EDA_GRADE_RANK[c.grade] >= least))
      .map(c => ({ ...c, by_repo: (c.by_repo || []).slice(0, 10), ...((c.by_repo || []).length > 10 ? { by_repo_omitted: c.by_repo.length - 10 } : {}) }));
    const flagged = (A.checks || []).filter(c => c.status === 'flag');
    const unav = (A.checks || []).filter(c => c.status === 'unavailable').length;
    return edaList({ route: 'lookup', summary: `${flagged.length} of ${(A.checks || []).length} audit checks flagged${unav ? `, ${unav} could not run` : ''}`, topic, checks, checks_total: checks.length,
      limitations: A.limitations, scope: A.scope }, 'coverage', (A.coverage || []).map(edaCoverageRow), lim);
  }
  if (topic === 'profile' || topic === 'rhythm') {
    const ids = edaSliceIds(B, q), fork = str(q.fork), measure = str(q.measure);
    if (measure && !EDA_MEASURES[measure]) throw new Error(`unknown measure ${JSON.stringify(measure.slice(0, 40))}; stored profiles have ${Object.keys(EDA_MEASURES).join(', ')}`);
    const src = topic === 'profile' ? (E.profile || {}).records || [] : (E.rhythm || {}).slices || [];
    const rows = src.filter(r => (!ids || ids.includes(r.slice)) && (!fork || r.fork === undefined || r.fork === fork) && (!measure || r.measure === undefined || r.measure === measure));
    const base = topic === 'profile' ? { scope: (E.profile || {}).scope, measure_defs: (E.profile || {}).measure_defs }
      : { scope: (E.rhythm || {}).scope };
    return edaList({ route: 'lookup', summary: `${rows.length} stored ${topic === 'profile' ? 'profile records' : 'rhythm slices'}`, topic, ...base,
      filter: { ...(ids ? { slices: ids } : {}), ...edaOnly(q, topic === 'profile' ? ['fork', 'measure'] : []) } }, topic === 'profile' ? 'records' : 'slices', rows, lim);
  }
  if (topic === 'concentration') {
    const names = str(q.repo) ? repoList(B, q.repo) : null, C = E.concentration || {};
    const rows = (C.repos || []).filter(r => !names || names.includes(r.repo));
    return edaList({ route: 'lookup', summary: `${rows.length} repos, ${rows.filter(r => r.unknown).length} unknown`, topic, scope: C.scope,
      note: 'unknown means not measured (a private repo or an unharvested tree): never read it as zero' }, 'repos', rows, lim);
  }
  if (topic === 'flags') {
    const F = E.flags || {}, by = F.by_rule || {}, rules = Object.keys(by), rule = str(q.rule);
    if (rule && !rules.includes(rule)) throw new Error(`unknown rule ${JSON.stringify(rule.slice(0, 60))}; rules: ${rules.join(', ')}`);
    const least = edaLeast(q.grade), use = rule ? [rule] : rules, priv = edaPrivateRepos(B), items = [];
    for (const g of ['C', 'L', 'S']) {
      if (EDA_GRADE_RANK[g] < least) continue;
      for (const r of use) for (const [i, k, m] of by[r][g] || []) items.push({ g, r, i, k, m });
    }
    items.sort((a, b) => (EDA_GRADE_RANK[b.g] - EDA_GRADE_RANK[a.g]) || (a.i - b.i) || (rules.indexOf(a.r) - rules.indexOf(b.r)));
    const rows = items.map(x => {
      const c = B.commits[x.i];
      return { ...(c ? edaFlagRow(B, c, priv) : { missing: 'no commit with this index' }), index: x.i, rule: x.r, grade: x.g, fired_in: x.k, of: x.m };
    });
    const rulesOut = Object.fromEntries(use.map(r => [r, { definition: ((F.rules || {})[r] || {}).definition, stratum: ((F.rules || {})[r] || {}).stratum,
      counts: by[r].counts, omitted: by[r].omitted, cap: by[r].cap }]));
    return edaList({ route: 'lookup', summary: `${rows.length} flagged commits listed`, topic, scope: F.scope, rules: rulesOut,
      note: 'flag lists hold at most 200 per rule and grade; counts and omitted say what the stored lists left out' }, 'flags', rows, lim);
  }
  if (topic === 'method') {
    const M = E.method || {};
    return { route: 'lookup', summary: `thresholds, forks and rules of the eda stage (schema ${E.schema_version})`, topic, kernel: M.kernel, rounding: M.rounding, quantile: M.quantile,
      thresholds: M.thresholds, forks: M.forks, seed_label: M.seed_label, code_sha256: M.code_sha256, limitations: (E.audit || {}).limitations, grade_rule: L.grade_rule, wording: EDA_WORDING };
  }
  if (topic === 'slice') return edaSlice(B, E, q, looks);
  if (topic === 'recompute') return edaRecompute(B, E);
  if (topic === 'compare') return edaCompare(B, E, q, looks, compared);
  throw new Error('unknown topic; topics: ' + EDA_TOPICS.join(', '));
}

// ---------------------------------------------------------------- tools
export function makeTools(B, scene, opts) {
  const T = [];
  const edaAsked = (opts && opts.looks) || edaLookSet();       // the distinct looks asked in this tool set (slices, comparisons and filtered row tools), so multiplicity is visible
  const edaCompared = new Map();    // comparison key -> its result, so asking again repeats the answer and does not draw again
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
    min_lines: { type: 'integer', description: 'at least this many lines changed (add+del)' },
    max_lines: { type: 'integer', description: 'at most this many lines changed' },
    hour: { type: 'integer', description: 'UTC author hour, 0 to 23' },
    weekday: { type: 'string', description: 'UTC weekday: 0 (Monday) to 6, or mon, tue, wed, thu, fri, sat, sun' },
    flag: { type: 'string', description: 'only commits an eda flag rule marks: size_high or gap_long; refused when the stored list is capped' },
    flag_grade: { type: 'string', enum: ['C', 'L', 'S'], description: 'with flag: this grade or better (default S)' },
    side_branch: { type: 'boolean', description: 'true: only commits off the default branch; false: only on it' },
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
      edaNoteLook(B, q, edaAsked);
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
      edaNoteLook(B, q, edaAsked);
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

  add('eda', EDA_DESC, EDA_SCHEMA, q => edaRun(B, q, edaAsked, edaCompared));

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
    q => { const out = scene.highlight(q); edaNoteLook(B, q, edaAsked); return out; });
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
