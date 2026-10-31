// x3d-ray MCP server: serves the page's data tools over the Model Context Protocol, from the same tools.js the page uses.
// stdio, newline-delimited JSON-RPC 2.0. No dependencies.
//   node x3dray/web/mcp.mjs --bundle out/<family>/bundle.json     serve
//   node x3dray/web/mcp.mjs --list                                print every tool (data tools, and the page-only scene tools)
//   claude mcp add x3d-xray -- node /path/to/x3d.xray/x3dray/web/mcp.mjs --bundle /path/to/bundle.json
import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { makeTools } from './tools.js';

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
const INSTRUCTIONS = `x3d-ray: read-only tools over ${B.meta.title || 'a repo family'} (harvested ${B.meta.harvested_at}). ` +
  'Call overview first: it is the digest the page shows its own Claude. Lookup tools (query_commits, activity, top_files, ties, signals, concepts, ' +
  'bridges) answer exact questions from tables; traverse walks the tie graph for multi-hop questions. Say which route you used. concepts and bridges ' +
  'say what each part read and set aside (excluded); a private repo answers with counts only. Two repos that no chain of Claude sessions joins have ' +
  'no recorded session link, never "no link", and the ties may still join them. Repo names resolve through the family\'s ' +
  'vocabulary (x3d.ai is x3d_ai; .prov is a stage inside x3d.ML); an unknown name is refused with the nearest names, so never guess. A value marked ' +
  'unknown was not measured: never read it as zero. how_built explains how the data and every signal were computed, down to the source lines. ' +
  'Every number comes from git history, computed with no model.';

createInterface({ input: process.stdin }).on('line', line => {
  if (!line.trim()) return;
  let msg;
  try { msg = JSON.parse(line); } catch (e) { send({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'parse error' } }); return; }
  const { id, method, params } = msg;
  if (id === undefined || id === null) return;   // notifications (initialized, cancelled) need no answer
  try {
    if (method === 'initialize') {
      send({ jsonrpc: '2.0', id, result: { protocolVersion: (params && params.protocolVersion) || '2025-06-18', capabilities: { tools: { listChanged: false } },
        serverInfo: { name: 'x3d-xray', version: B.meta.version || '0' }, instructions: INSTRUCTIONS } });
    } else if (method === 'ping') {
      send({ jsonrpc: '2.0', id, result: {} });
    } else if (method === 'tools/list') {
      send({ jsonrpc: '2.0', id, result: { tools: tools.map(spec) } });
    } else if (method === 'tools/call') {
      const t = tools.find(x => x.name === (params && params.name));
      if (!t) { send({ jsonrpc: '2.0', id, error: { code: -32602, message: 'unknown tool: ' + (params && params.name) } }); return; }
      try {
        const r = t.execute((params && params.arguments) || {}, {});
        send({ jsonrpc: '2.0', id, result: { content: [{ type: 'text', text: typeof r === 'string' ? r : JSON.stringify(r) }] } });
      } catch (e) {
        send({ jsonrpc: '2.0', id, result: { content: [{ type: 'text', text: 'Error: ' + (e && e.message || e) }], isError: true } });
      }
    } else if (method === 'resources/list') {
      send({ jsonrpc: '2.0', id, result: { resources: [] } });
    } else if (method === 'prompts/list') {
      send({ jsonrpc: '2.0', id, result: { prompts: [] } });
    } else {
      send({ jsonrpc: '2.0', id, error: { code: -32601, message: 'method not found: ' + method } });
    }
  } catch (e) {
    send({ jsonrpc: '2.0', id, error: { code: -32603, message: String(e && e.message || e) } });
  }
});
