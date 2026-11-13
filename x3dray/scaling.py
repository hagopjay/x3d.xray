"""Synthetic architecture scale experiments over the production code paths.

These generated relation records are not harvested repositories. Scene building
calls compiler.constellation_region, checking calls scenecheck.verify_scene, and
querying calls the production typed Python and shared JavaScript tools. No full
helix/FCA/family harvest, browser paint, X_ITE draw or GPU throughput is measured.
"""
from __future__ import annotations

from bisect import bisect_left, insort
from collections import defaultdict, deque
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from time import perf_counter_ns
import tracemalloc
import xml.etree.ElementTree as ET

from . import compiler, relations, scenecheck

MAX_NODES = 10_000
MAX_RECORDS = 50_000
TOPOLOGIES = ("chain", "modular", "branching")
DISCLOSURE = ("Pure synthetic generated repository identities and relation observations; "
              "not a harvest of 10/100/1000 real Git repositories. Static compile/check and "
              "CPU/DOM/query measurements only; no rendered pixels or GPU throughput.")
ROOT = Path(__file__).resolve().parents[1]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _name(i, long_ids):
    return f"synthetic-{i:05d}" + ("-" + "漢字-α-" * 20 if long_ids and i % 23 == 0 else "")


def _source_edges(active, topology):
    edges = set()
    if topology == "branching":
        edges.update(((i - 1) // 2, i) for i in range(1, active))
    elif topology == "chain":
        edges.update((i, i + 1) for i in range(active - 1))
    else:
        # Separate eight-entity modules, with an initial diamond in each. The
        # diamond has two shortest node paths despite parallel observations.
        for start in range(0, active, 8):
            stop = min(start + 8, active)
            edges.update((i, i + 1) for i in range(start, stop - 1))
            if stop - start >= 4:
                edges.discard((start + 1, start + 2))
                edges.update(((start, start + 2), (start + 1, start + 3), (start + 2, start + 3)))
    return sorted(edges)


def compile_constellation(dataset, ties, curated, profile):
    """Compile just the actual instrumented production constellation."""
    scene = compiler.Scene()
    layout = {"nodes": {}, "ties": {"machine": [], "curated": []}, "regions": {}}
    compiler.constellation_region(scene, layout, dataset, ties, curated, profile)
    region = layout["regions"]["ties"]
    center, radius = region["center"], region["r"]
    distance = max(24., radius / math.tan(0.4) * 1.18)
    position = (center[0], center[1], center[2] + distance)
    navigation = ("<Background skyColor='0.035 0.071 0.094'/>"
                  "<NavigationInfo type='\"EXAMINE\" \"ANY\"' headlight='true'/>"
                  f"<Viewpoint DEF='VP_scale' description='Synthetic architecture scale fixture' position='{compiler.v3(position)}' "
                  f"centerOfRotation='{compiler.v3(center)}' fieldOfView='0.8'/>")
    xml = ("<?xml version='1.0' encoding='UTF-8'?>"
           "<X3D profile='Immersive' version='4.0'><head><meta name='description' "
           "content='Pure synthetic generated architecture scale fixture; no real repository harvest'/></head><Scene>"
           + navigation + "".join(scene.out) + "</Scene></X3D>")
    return {"xml": xml, "layout": layout, "plan": layout["scene_plan"]}


def geometry_metrics(plan):
    """Static glyph-bound overlaps and closest centres, excluding labels/edges.

    The overlap grid handles glyph bounding spheres in three dimensions. Box
    diagonal spheres are conservative bounds, not exact box intersection tests.
    The closest-centre sweep is independent of the compiler's ring algorithm.
    These are world-space diagnostics, not a screen-space readability score.
    """
    entities = plan.get("entities", [])
    def glyph_bound(entity):
        geometry = entity.get("geometry", {})
        if geometry.get("type") == "Box":
            return math.sqrt(sum(float(v) ** 2 for v in geometry["size"])) / 2
        return float(geometry.get("radius", entity["attachment_radius"]))
    points = [(tuple(e["position"]), glyph_bound(e), e["id"]) for e in entities]
    if len(points) < 2:
        return {"glyph_overlap_pairs": 0, "closest_center_distance": None, "closest_pair_surface_gap": None,
                "scope": "World-space glyph bounds only; no labels, edge crossings or screen-space layout assessment."}
    cell = max(2 * r for _, r, _ in points)
    grid, overlaps = defaultdict(list), 0
    for p, radius, ident in points:
        key = tuple(math.floor(v / cell) for v in p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for q, other_radius, _ in grid[(key[0] + dx, key[1] + dy, key[2] + dz)]:
                        if math.dist(p, q) < radius + other_radius:
                            overlaps += 1
        grid[key].append((p, radius, ident))
    ordered = sorted(points)
    best = math.dist(ordered[0][0], ordered[1][0])
    pair = (ordered[0], ordered[1])
    active, pending = [], deque()
    for point, radius, ident in ordered:
        x, y, z = point
        while pending and pending[0][0][0] < x - best:
            q, r, name = pending.popleft()
            active.pop(bisect_left(active, (q[1], q[0], q[2], r, name)))
        at = bisect_left(active, (y - best, -math.inf, -math.inf, -math.inf, ""))
        while at < len(active) and active[at][0] <= y + best:
            yy, xx, zz, r, name = active[at]
            q = (xx, yy, zz)
            d = math.dist(point, q)
            if d < best:
                best, pair = d, ((point, radius, ident), (q, r, name))
            at += 1
        insort(active, (y, x, z, radius, ident))
        pending.append((point, radius, ident))
    return {"glyph_overlap_pairs": overlaps, "closest_center_distance": best,
            "closest_pair_surface_gap": best - pair[0][1] - pair[1][1],
            "scope": "World-space glyph bounds only; no labels, edge crossings or screen-space layout assessment."}


def path_oracle(graph, query):
    """Independent bounded simple-path enumeration, small-degree fixtures only."""
    wanted = set(query.get("predicates", "").split(",")) - {""}
    adjacency = defaultdict(set)
    for r in graph["records"]:
        if wanted and r["predicate"] not in wanted:
            continue
        a, b = r["from"], r["to"]
        if not r["directed"] or query.get("direction") == "association":
            adjacency[a].add(b)
            adjacency[b].add(a)
        elif query.get("direction") == "incoming":
            adjacency[b].add(a)
        else:
            adjacency[a].add(b)
    found, stack = [], [[query["from"]]]
    while stack:
        path = stack.pop()
        if path[-1] == query["to"]:
            found.append(path)
        elif len(path) - 1 < query.get("max_hops", 6):
            for target in adjacency[path[-1]]:
                if target not in path:
                    stack.append(path + [target])
    shortest = min((len(path) for path in found), default=0)
    paths = sorted(path for path in found if len(path) == shortest)
    return {"hops": shortest - 1 if shortest else None, "total_paths": len(paths), "paths": paths}


def build_fixture(n, topology="chain", *, long_ids=True):
    """Return dataset/ties/curated/plan/XML/graph plus reproducible test context."""
    if type(n) is not int or not 10 <= n <= MAX_NODES:
        raise ValueError("scale fixture requires 10 to 10000 synthetic entities")
    if topology not in TOPOLOGIES:
        raise ValueError("unknown synthetic scale topology")
    names = [_name(i, long_ids) for i in range(n)]
    active = n - max(1, n // 10)
    dataset = {"family": f"synthetic-scale-{topology}", "lineage": [], "repos": [
        {"name": name, "kind": "own", "visibility": "public", "present": True, "commits": []}
        for name in names]}
    profile = {"per_repo": {name: {"own_commits": 0, "by_class": {}, "tests": 0, "kind": "own"} for name in names}}
    pairs, curated = [], {"ties": [], "nodes": [], "series": []}
    for i, (a, b) in enumerate(_source_edges(active, topology)):
        pair = {"from": names[a], "to": names[b], "grade": "C", "grade_reason": "synthetic declared reference",
                "kinds": {"uses": 1, "mention:dependency": 1}, "evidence": [
                    {"kind": "uses", "path": "synthetic-family.json", "line": i + 1,
                     "text": "Generated configuration observation; not an observed real runtime dependency."},
                    {"kind": "mention:dependency", "path": "synthetic-package.json", "line": i + 1,
                     "text": "Generated static manifest reference; parallel observation, not a second node path."}]}
        pairs.append(pair)
        if i % 10 == 0:
            curated["ties"].append({"id": f"SYN-{i:05d}", "from": names[a], "to": names[b], "grade": "L",
                                     "kind": "synthetic reading", "evidence": ["Generated curator association; no execution claim."]})
    ties = {"pairs": pairs}
    graph = relations.build_relations(dataset, ties, curated)
    if len(graph["nodes"]) > MAX_NODES or len(graph["records"]) > MAX_RECORDS:
        raise ValueError("generated fixture exceeds common architecture graph budgets")
    built = compile_constellation(dataset, ties, curated, profile)
    source, target = names[0], names[min(4, active - 1)]
    filters = "declares_use,dependency_reference"
    queries = [
        {"id": "forward", "args": {"from": source, "to": target, "direction": "outgoing", "predicates": filters, "max_hops": 6, "limit": 12}},
        {"id": "incoming", "args": {"from": target, "to": source, "direction": "incoming", "predicates": filters, "max_hops": 6, "limit": 12}},
        {"id": "disconnected", "args": {"from": source, "to": names[-1], "direction": "outgoing", "predicates": filters, "max_hops": 6, "limit": 12}},
    ]
    oracle = {q["id"]: path_oracle(graph, q["args"]) for q in queries}
    bundle = {"repos": [{"name": name} for name in names], "vocab": {}, "relations": graph,
              "ties": {"machine": pairs, "curated": curated["ties"]}}
    return {"dataset": dataset, "ties": ties, "curated": curated, "profile": profile, **built,
            "graph": graph, "bundle": bundle, "queries": queries, "oracle": oracle,
            "geometry": geometry_metrics(built["plan"]),
            "metadata": {"entities": n, "topology": topology, "isolated_entities": n - active,
                         "max_identifier_characters": max(map(len, names)), "disclosure": DISCLOSURE,
                         "fixture_digest": _digest([dataset, ties, curated]), "generated_source_pairs": len(pairs)}}


def statistics_ms(samples, *, warmups=0):
    ordered = sorted(samples)
    return {"samples_ms": samples, "p50_ms": statistics.median(ordered),
            "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
            "p95_method": "nearest rank", "warmups": warmups, "repeats": len(samples)}


def _measure(fn, repeats, warmups):
    result = None
    for _ in range(warmups):
        result = fn()
    samples = []
    for _ in range(repeats):
        start = perf_counter_ns()
        result = fn()
        samples.append((perf_counter_ns() - start) / 1_000_000)
    return statistics_ms(samples, warmups=warmups), result


def _memory(fn):
    # Separate untimed traced call: tracing would otherwise distort timings.
    tracemalloc.start()
    fn()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"tracemalloc_current_bytes": current, "tracemalloc_peak_bytes": peak,
            "scope": "Python allocations in one separate traced call; not total RSS, browser or GPU memory."}


def _run_python(graph, query):
    args = dict(query)
    source, target = args.pop("from"), args.pop("to", None)
    return relations.traverse(graph, source, target, **args)


def _assert_query(result, oracle):
    if result.get("hops") != oracle["hops"] or int(result.get("total_paths", -1)) != oracle["total_paths"]:
        raise AssertionError("production query disagrees with independent path oracle")
    returned = [p["nodes"] for p in result.get("paths", [])]
    if returned != oracle["paths"][:len(returned)] or result.get("returned_paths") != len(returned):
        raise AssertionError("production returned path records disagree with oracle")


def _expansion_budget_probe():
    fixture = build_fixture(10)
    parts = ["<Group DEF='D0'/>"]
    parts += [f"<Group DEF='D{i}'><Group USE='D{i-1}'/><Group USE='D{i-1}'/></Group>" for i in range(1, 27)]
    # Keep the actual valid source/plan context. Only add a compact DAG outside
    # its semantic layers; the global expansion budget must still bound work.
    xml = fixture["xml"].replace("<Scene>", "<Scene>" + "".join(parts), 1)
    start = perf_counter_ns()
    result = scenecheck.verify_scene(xml, fixture["plan"], fixture["bundle"])
    codes = [r["code"] for r in result["findings"]]
    if result["ok"] or result["checks"]["expanded_visits"] != scenecheck.MAX_EXPANDED_VISITS or "scene_expansion_limit" not in codes:
        raise AssertionError("default expanded-work budget was not enforced as expected")
    return {"kind": "shared_USE_expansion", "scope": "Resource refusal at real default budget; not a semantic pass.",
            "elapsed_ms": (perf_counter_ns() - start) / 1_000_000, "xml_bytes": len(xml.encode()),
            "accepted": result["ok"], "expanded_visits": result["checks"]["expanded_visits"],
            "limit": scenecheck.MAX_EXPANDED_VISITS, "finding_codes": codes}


def _graph_budget_probes():
    fixture = build_fixture(10)
    graph = fixture["graph"]
    nodes = copy.deepcopy(graph)
    nodes["nodes"] += [{"id": f"synthetic-overbudget-{i}"} for i in range(len(nodes["nodes"]), MAX_NODES + 1)]
    records = dict(graph)
    records["records"] = [{**graph["records"][0], "id": f"relation:synthetic-overbudget-{i}"} for i in range(MAX_RECORDS + 1)]
    out = {}
    for label, too_large in (("nodes", nodes), ("records", records)):
        try:
            _run_python(too_large, fixture["queries"][0]["args"])
        except ValueError as error:
            if "graph exceeds bounded traversal budget" not in str(error):
                raise
            out[label] = {"refused": True, "nodes": len(too_large["nodes"]), "records": len(too_large["records"]),
                          "reason": str(error), "scope": "Actual production preflight work-budget refusal."}
        else:
            raise AssertionError("oversized typed graph was accepted")
    return out


def benchmark(*, sizes=(10, 100, 1000), topologies=TOPOLOGIES, repeats=7, warmups=2,
              output_dir=None, jsdom_modules=None):
    """Measure production stages and shared tools; persist fixture artifacts."""
    if type(repeats) is not int or repeats < 5 or type(warmups) is not int or warmups < 1:
        raise ValueError("scale benchmarks require at least five repetitions and one warmup")
    output_dir = Path(output_dir or "scale-benchmark-output").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    measured_sources = [ROOT / "x3dray" / f for f in (
        "scaling.py", "compiler.py", "scenecheck.py", "relations.py", "web/tools.js",
        "web/scale_benchmark.cjs", "witness_contract.py", "locators.py", "temporal.py", "gitio.py", "structure.py")]
    project_harnesses = [ROOT / "experiments" / name for name in (
        "benchmark_architecture_scale.py", "benchmark_architecture_scale.cjs")]
    measured_sources += [p for p in project_harnesses if p.is_file()]
    source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in measured_sources}
    report = {"schema_version": 1, "disclosure": DISCLOSURE,
              "environment": {"python": sys.version, "platform": platform.platform(), "cpu_count": os.cpu_count(),
                              "machine": platform.machine(), "processor": platform.processor()},
              "limits": {"graph_nodes": MAX_NODES, "relation_records": MAX_RECORDS,
                         "verifier_expanded_visits": scenecheck.MAX_EXPANDED_VISITS},
              "source_sha256": source_hashes, "rows": [],
              "unavailable_project_harnesses": [str(p.relative_to(ROOT)) for p in project_harnesses if not p.is_file()],
              "measurement_conditions": "Local shared-host diagnostic samples without CPU pinning or forced JavaScript GC; not a production SLA or confidence interval.",
              "timing_scope": "Separate stage calls. Fixture generation, imports, JSON/IPC and memory probes excluded from stage timings unless stated.",
              "memory_scope": "Separate Python tracemalloc calls; JavaScript heap fields are process snapshots. No GPU or rendered-pixel measurements."}
    report["artifact_directory"] = output_dir.name
    # The actual Node harness is package data, so the installed-wheel command
    # does not depend on a source checkout's experiments/ directory.
    script = Path(__file__).with_name("web") / "scale_benchmark.cjs"
    for topology in topologies:
        for n in sizes:
            fixture = build_fixture(n, topology)
            stem = f"{topology}-{n}"
            scene_path, fixture_path = output_dir / f"{stem}.x3d", output_dir / f"{stem}.json"
            scene_path.write_text(fixture["xml"], encoding="utf-8")
            # XML lives separately; retain complete graph/plan and original
            # synthetic source observations rather than a preview-capped subset.
            fixture_path.write_text(json.dumps({k: v for k, v in fixture.items() if k != "xml"}, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
            compile_fn = lambda: compile_constellation(fixture["dataset"], fixture["ties"], fixture["curated"], fixture["profile"])
            verify_fn = lambda: scenecheck.verify_scene(fixture["xml"], fixture["plan"], fixture["bundle"])
            project_fn = lambda: relations.build_relations(fixture["dataset"], fixture["ties"], fixture["curated"])
            compilation, built = _measure(compile_fn, repeats, warmups)
            verification, checked = _measure(verify_fn, repeats, warmups)
            projection, graph = _measure(project_fn, repeats, warmups)
            if not checked["ok"]:
                raise AssertionError("synthetic production constellation did not pass independent checks")
            if built["xml"] != fixture["xml"] or graph != fixture["graph"]:
                raise AssertionError("fixture is not deterministic across production repetitions")
            row = {"size": n, "topology": topology, "metadata": fixture["metadata"], "geometry": fixture["geometry"],
                   "scene": {"xml_bytes": len(fixture["xml"].encode("utf-8")), "xml_elements": sum(1 for _ in ET.fromstring(fixture["xml"]).iter()),
                             "verified": checked["ok"], "checks": checked["checks"],
                             "observed_entities_omitted": checked.get("observed", {}).get("entities_omitted"),
                             "observed_relations_omitted": checked.get("observed", {}).get("relations_omitted")},
                   "graph": {"nodes": len(graph["nodes"]), "records": len(graph["records"])},
                   "timings": {"compile": compilation, "verify": verification, "typed_projection": projection},
                   "memory": {"compile": _memory(compile_fn), "verify": _memory(verify_fn), "typed_projection": _memory(project_fn)},
                   "python_queries": {}, "artifacts": {"fixture": fixture_path.name, "scene": scene_path.name}}
            for query in fixture["queries"]:
                fn = lambda query=query: _run_python(graph, query["args"])
                timings, result = _measure(fn, repeats, warmups)
                _assert_query(result, fixture["oracle"][query["id"]])
                row["python_queries"][query["id"]] = {"timings": timings, "memory": _memory(fn), "oracle_equal": True,
                    "hops": result.get("hops"), "total_paths": result.get("total_paths"), "returned_paths": result.get("returned_paths"),
                    "omitted_paths": result.get("omitted_paths"),
                    "result_utf8_bytes": len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))}
            env = dict(os.environ)
            if jsdom_modules:
                env["NODE_PATH"] = str(Path(jsdom_modules).resolve())
            proc = subprocess.run(["node", str(script), "--fixture", str(fixture_path), "--tools", str(ROOT / "x3dray/web/tools.js"),
                                   "--repeats", str(repeats), "--warmups", str(warmups)], capture_output=True, text=True, check=True, env=env, timeout=180)
            row["javascript"] = json.loads(proc.stdout)
            if any(not item["oracle_equal"] for item in row["javascript"]["queries"].values()):
                raise AssertionError("shared JavaScript query disagrees with independent path oracle")
            report["rows"].append(row)
    report["budget_probes"] = {"verifier": _expansion_budget_probe(), "python_query": _graph_budget_probes()}
    after_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in measured_sources}
    if source_hashes != after_hashes:
        raise RuntimeError("measured source changed during benchmark; rerun against one frozen source snapshot")
    return report
