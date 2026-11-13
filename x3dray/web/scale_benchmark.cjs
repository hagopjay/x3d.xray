#!/usr/bin/env node
/* Production shared architecture tool + explicitly separate synthetic SVG DOM
 * construction. Neither jsdom nor Node renders X3D/pixels or measures a GPU. */
const fs = require('node:fs');
const os = require('node:os');
const {pathToFileURL} = require('node:url');

function argsOf(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 2) out[argv[i].replace(/^--/, '')] = argv[i + 1];
  return out;
}
function stats(samples, warmups) {
  const a = [...samples].sort((x, y) => x - y), middle = Math.floor(a.length / 2);
  return {samples_ms: samples, p50_ms: a.length % 2 ? a[middle] : (a[middle - 1] + a[middle]) / 2,
    p95_ms: a[Math.ceil(0.95 * a.length) - 1], p95_method: 'nearest rank', warmups, repeats: samples.length};
}
function measure(fn, repeats, warmups) {
  for (let i = 0; i < warmups; i++) fn();
  const samples = [], memory = [], startMemory = process.memoryUsage();
  let result;
  for (let i = 0; i < repeats; i++) {
    const start = process.hrtime.bigint(); result = fn();
    samples.push(Number(process.hrtime.bigint() - start) / 1e6);
    memory.push(process.memoryUsage());
  }
  const endMemory = process.memoryUsage();
  return {timings: stats(samples, warmups), result, memory: {
    heap_used_before_bytes: startMemory.heapUsed, heap_used_after_bytes: endMemory.heapUsed,
    heap_used_delta_bytes: endMemory.heapUsed - startMemory.heapUsed,
    sampled_max_heap_used_bytes: Math.max(...memory.map(m => m.heapUsed)),
    sampled_max_rss_bytes: Math.max(...memory.map(m => m.rss)),
    scope: 'Node process snapshots outside timing spans; GC is not forced; deltas can be negative. Not peak allocations, browser or GPU memory.'}};
}
function matchesOracle(result, oracle) {
  const paths = (result.paths || []).map(p => p.nodes);
  return result.hops === oracle.hops && Number(result.total_paths) === oracle.total_paths
    && result.returned_paths === paths.length && Number(result.omitted_paths) === oracle.total_paths - paths.length
    && JSON.stringify(paths) === JSON.stringify(oracle.paths.slice(0, paths.length));
}

function svgDom(fixture, repeats, warmups) {
  let JSDOM;
  try { ({JSDOM} = require('jsdom')); }
  catch { return {available: false, scope: 'SVG DOM construction not measured: jsdom unavailable. No layout, paint or GPU assertion.'}; }
  const plan = fixture.plan, ns = 'http://www.w3.org/2000/svg';
  const build = () => {
    const dom = new JSDOM('<!doctype html><html><body></body></html>');
    const d = dom.window.document, svg = d.createElementNS(ns, 'svg');
    svg.setAttribute('data-scope', 'Synthetic DOM mirror of actual production ScenePlan; not the Workbench renderer');
    for (const record of plan.relations) {
      const path = d.createElementNS(ns, 'path');
      path.setAttribute('data-id', record.id);
      path.setAttribute('d', record.points.map((p, i) => (i ? 'L' : 'M') + p[0] + ',' + p[1]).join(' '));
      svg.append(path);
    }
    for (const entity of plan.entities) {
      const g = d.createElementNS(ns, 'g'), glyph = d.createElementNS(ns, 'circle'), label = d.createElementNS(ns, 'text');
      g.setAttribute('data-id', entity.id);
      glyph.setAttribute('cx', String(entity.position[0])); glyph.setAttribute('cy', String(entity.position[1]));
      glyph.setAttribute('r', String(entity.attachment_radius)); label.textContent = entity.source_name;
      g.append(glyph, label); svg.append(g);
    }
    d.body.append(svg);
    const result = {svg_element_nodes: svg.querySelectorAll('*').length + 1,
      entities: svg.querySelectorAll('g').length, relations: svg.querySelectorAll('path').length,
      serialized_utf8_bytes: Buffer.byteLength(svg.outerHTML, 'utf8')};
    dom.window.close(); return result;
  };
  const out = measure(build, repeats, warmups);
  return {available: true, jsdom_version: require('jsdom/package.json').version, ...out, scope: 'Actual jsdom SVG construction + serialization of a synthetic ScenePlan mirror. Not production Workbench DOM, browser layout, paint, X_ITE or GPU timing.'};
}

async function main() {
  const args = argsOf(process.argv.slice(2));
  const repeats = Number(args.repeats || 7), warmups = Number(args.warmups || 2);
  if (!Number.isInteger(repeats) || repeats < 5 || !Number.isInteger(warmups) || warmups < 1) throw Error('At least five repetitions and one warmup required');
  const fixture = JSON.parse(fs.readFileSync(args.fixture, 'utf8'));
  const {makeTools} = await import(pathToFileURL(args.tools).href);
  const tool = makeTools(fixture.bundle, null).find(t => t.name === 'architecture');
  if (!tool) throw Error('Production shared architecture tool unavailable');
  const out = {environment: {node: process.version, platform: process.platform, arch: process.arch,
    cpu_count: os.cpus().length, node_v8: process.versions.v8},
    tool: 'makeTools(bundle).find(architecture).execute(arguments)', queries: {}};
  for (const query of fixture.queries) {
    const measured = measure(() => tool.execute(query.args), repeats, warmups), result = measured.result;
    out.queries[query.id] = {timings: measured.timings, memory: measured.memory,
      oracle_equal: matchesOracle(result, fixture.oracle[query.id]), hops: result.hops,
      total_paths: result.total_paths, returned_paths: result.returned_paths, omitted_paths: result.omitted_paths,
      result_utf8_bytes: Buffer.byteLength(JSON.stringify(result), 'utf8'), work: result.work,
      presentation_limited: result.presentation_limited || null};
  }
  out.svg_dom = svgDom(fixture, repeats, warmups);
  const graph = fixture.graph;
  const oversizedNodes = {...graph, nodes: [...graph.nodes]};
  for (let i = oversizedNodes.nodes.length; i < 10001; i++) oversizedNodes.nodes.push({id: 'synthetic-overbudget-' + i});
  const oversizedRecords = {...graph, records: Array.from({length: 50001}, (_, i) => ({...graph.records[0], id: 'relation:synthetic-overbudget-' + i}))};
  out.budget_probes = {};
  for (const [name, tooLarge] of [['nodes', oversizedNodes], ['records', oversizedRecords]]) {
    const probe = makeTools({...fixture.bundle, relations: tooLarge}, null).find(t => t.name === 'architecture').execute(fixture.queries[0].args);
    out.budget_probes[name] = {status: probe.status || null, refused: probe.status === 'unavailable',
      graph_nodes: tooLarge.nodes.length, graph_records: tooLarge.records.length,
      reason: probe.reason, limits: probe.limits};
  }
  if (!Object.values(out.queries).every(x => x.oracle_equal) || !Object.values(out.budget_probes).every(x => x.refused)) throw Error('Scale oracle or budget assertion failed');
  process.stdout.write(JSON.stringify(out));
}
main().catch(error => {console.error(error.stack || error.message); process.exitCode = 1;});
