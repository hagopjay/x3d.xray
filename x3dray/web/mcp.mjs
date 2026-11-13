// x3d-ray MCP server: serves the page's data tools over the Model Context Protocol, from the same tools.js the page uses.
// stdio, newline-delimited JSON-RPC 2.0. No dependencies.
//   node x3dray/web/mcp.mjs --bundle out/<family>/bundle.json     serve
//   node x3dray/web/mcp.mjs --list                                print every tool (data tools, and the page-only scene tools)
//   claude mcp add x3d-xray -- node /path/to/x3d.xray/x3dray/web/mcp.mjs --bundle /path/to/bundle.json
import { readFileSync } from 'node:fs';
import { makeTools, cap } from './tools.js';

const args = process.argv.slice(2);
const flag = n => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : null; };
const spec = t => ({ name: t.name, description: t.description, inputSchema: t.inputSchema });

if (args.includes('--list')) {
  const stub = { meta: {}, commits: [], files: {}, ties: { machine: [], curated: [], series: [], grades: {} }, signals: [], repos: [],
    spec: { views: [], tour: [] }, self: { tool: { git: {} }, pipeline: [], signal_code: {}, modules: [], limits: [], in_its_own_data: {}, claude_in_the_data: {} } };
  const noop = () => ({});
  const data = makeTools(stub, null).map(spec);
  const page = makeTools(stub, { show: noop, highlight: noop, note: noop, colorBy: noop }).map(spec);
  const names = new Set(data.map(t => t.name));
  process.stdout.write(JSON.stringify({ mcp_and_page: data.filter(t => t.name !== 'overview'), mcp_only: data.filter(t => t.name === 'overview'),
    page_only: page.filter(t => !names.has(t.name)) }, null, 1) + '\n');
  process.exit(0);
}

const bundlePath = flag('--bundle');
if (!bundlePath) { process.stderr.write('usage: mcp.mjs --bundle path/to/bundle.json | --list\n'); process.exit(2); }
const B = JSON.parse(readFileSync(bundlePath, 'utf8'));
for (const extra of ['gates', 'receipt']) {
  try { B[extra] = JSON.parse(readFileSync(bundlePath.replace(/bundle\.json$/, extra + '.json'), 'utf8')); } catch (e) { /* optional */ }
}
const tools = makeTools(B, null);
const send = obj => process.stdout.write(JSON.stringify(obj) + '\n');
const SUPPORTED_VERSIONS = ['2025-06-18'];
const MAX_LINE_BYTES = 65536;
const MAX_TOOL_RESPONSE_BYTES = 32000;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const error = (id, code, message) => send({ jsonrpc: '2.0', id, error: { code, message } });
function toolResult(id, value, isError = false) {
  const envelope = result => ({ jsonrpc: '2.0', id, result: { content: [{ type: 'text', text: typeof result === 'string' ? result : JSON.stringify(result) }], ...(isError ? { isError: true } : {}) } });
  let response = envelope(cap(value));
  // Text content JSON-encodes the result a second time; quote-heavy data can expand.
  // Conservatively retry at a smaller payload budget, then assert the actual wire size.
  if (Buffer.byteLength(JSON.stringify(response)) > MAX_TOOL_RESPONSE_BYTES) response = envelope(cap(value, 14000));
  if (Buffer.byteLength(JSON.stringify(response)) > MAX_TOOL_RESPONSE_BYTES) {
    response = envelope({ truncated: 'Entire result withheld because its transport envelope exceeds the byte budget.' });
  }
  send(response);
}
const INSTRUCTIONS = `x3d-ray: read-only tools over ${B.meta.title || 'a repo family'} (harvested ${B.meta.harvested_at}). ` +
  'Call overview first: it is the digest the page shows its own Claude. Lookup tools (query_commits, activity, top_files, ties, signals, concepts, ' +
  'bridges) answer exact questions from tables; traverse walks undirected recorded associations, while architecture walks typed relations under explicit predicates and direction. ' +
  'Architecture at+time_mode separates observed_by (recorded visibility, not historical validity) from valid_at (claimed half-open validity at the same instant on all hops, without an observation cutoff); unknown_time defaults to exclude and include is provisional. ' +
  'Static references and curated associations do not prove executable dependencies. Say which route and direction you used. concepts and bridges ' +
  'say what each part read and set aside (excluded); a private repo answers with counts only. Two repos that no chain of Claude sessions joins have ' +
  'no recorded session link, never "no link", and the ties may still join them. Repo names resolve through the family\'s ' +
  'vocabulary (x3d.ai is x3d_ai; .prov is a stage inside x3d.ML); an unknown name is refused with the nearest names, so never guess. A value marked ' +
  'unknown was not measured: never read it as zero. how_built explains how the data and every signal were computed, down to the source lines. ' +
  'The eda tool reads the exploration layer (data audit, distributions, rhythm, concentration, flags, 12 declared looks with grades) and computes ungraded slices: ' +
  'a grade here is robustness over declared checks, never a probability; slices asked through eda are descriptive and ungraded. ' +
  'Every number comes from git history, computed with no model.';

function handleLine(line) {
  if (!line.trim()) return;
  let msg;
  try { msg = JSON.parse(line); } catch (e) { error(null, -32700, 'parse error'); return; }
  if (!object(msg) || msg.jsonrpc !== '2.0' || typeof msg.method !== 'string' || !msg.method
      || (Object.hasOwn(msg, 'id') && !(msg.id === null || typeof msg.id === 'string' || Number.isSafeInteger(msg.id)))
      || (Object.hasOwn(msg, 'id') && Buffer.byteLength(JSON.stringify(msg.id)) > 128)) {
    error(null, -32600, 'invalid JSON-RPC request envelope'); return;
  }
  const { id, method, params } = msg;
  if (id === undefined) return;   // well-formed notifications need no answer
  if (params !== undefined && !object(params)) { error(id, -32602, 'params must be an object'); return; }
  try {
    if (method === 'initialize') {
      if (!params || typeof params.protocolVersion !== 'string') { error(id, -32602, 'initialize requires a string protocolVersion'); return; }
      const version = SUPPORTED_VERSIONS.includes(params.protocolVersion) ? params.protocolVersion : SUPPORTED_VERSIONS[SUPPORTED_VERSIONS.length - 1];
      send({ jsonrpc: '2.0', id, result: { protocolVersion: version, capabilities: { tools: { listChanged: false } },
        serverInfo: { name: 'x3d-xray', version: B.meta.version || '0' }, instructions: INSTRUCTIONS } });
    } else if (method === 'ping') {
      send({ jsonrpc: '2.0', id, result: {} });
    } else if (method === 'tools/list') {
      send({ jsonrpc: '2.0', id, result: { tools: tools.map(spec) } });
    } else if (method === 'tools/call') {
      if (!params || typeof params.name !== 'string' || (params.arguments !== undefined && !object(params.arguments))) {
        error(id, -32602, 'tools/call requires a tool name and object arguments'); return;
      }
      const t = tools.find(x => x.name === (params && params.name));
      if (!t) { error(id, -32602, 'unknown tool: ' + params.name.slice(0, 160)); return; }
      try {
        const r = t.execute(params.arguments === undefined ? {} : params.arguments, {});
        toolResult(id, r);
      } catch (e) {
        toolResult(id, { error: String(e && e.message || e).slice(0, 1000) }, true);
      }
    } else if (method === 'resources/list') {
      send({ jsonrpc: '2.0', id, result: { resources: [] } });
    } else if (method === 'prompts/list') {
      send({ jsonrpc: '2.0', id, result: { prompts: [] } });
    } else {
      error(id, -32601, 'method not found: ' + method.slice(0, 160));
    }
  } catch (e) {
    error(id, -32603, 'internal handler error');
  }
}

// Bound memory before parsing a line. A line beyond the limit is discarded through
// its newline; the next valid request is still served. readline buffers whole lines.
let pending = [], pendingBytes = 0, discarding = false;
function fragment(part, endOfLine) {
  if (!discarding && pendingBytes + part.length > MAX_LINE_BYTES) { discarding = true; pending = []; pendingBytes = 0; }
  if (!discarding && part.length) { pending.push(part); pendingBytes += part.length; }
  if (!endOfLine) return;
  if (discarding) error(null, -32600, 'request line exceeds 65536 UTF-8 bytes');
  else handleLine(Buffer.concat(pending, pendingBytes).toString('utf8'));
  pending = []; pendingBytes = 0; discarding = false;
}
process.stdin.on('data', chunk => {
  let start = 0, newline;
  while ((newline = chunk.indexOf(10, start)) !== -1) { fragment(chunk.subarray(start, newline), true); start = newline + 1; }
  if (start < chunk.length) fragment(chunk.subarray(start), false);
});
process.stdin.on('end', () => { if (pendingBytes || discarding) fragment(Buffer.alloc(0), true); });
