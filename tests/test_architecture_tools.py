"""Typed shared-tool semantics, independently checked against simple node paths.

Node executes the actual page/MCP tools without packages. The Python oracle
enumerates small simple paths rather than reusing the production BFS/counting.
"""
from collections import defaultdict
import json
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "x3dray" / "web"


def record(i, a, b, predicate="declares_use", directed=True, grade="C", text="Synthetic declared use"):
    return {"id": f"relation:{i}", "from": a, "to": b, "predicate": predicate, "directed": directed,
            "grade": grade, "source": "machine", "method": {"detector": "synthetic-test"},
            "evidence": [{"id": f"evidence:{i}", "kind": predicate, "text": text}],
            "evidence_records": {"total": 1, "returned": 1, "omitted": 0}}


def bundle(records, nodes=None):
    names = sorted(set(nodes or []) | {r[k] for r in records for k in ("from", "to")})
    definitions = {r["predicate"]: {"directed": r["directed"], "definition": "Synthetic test observation"} for r in records}
    return {"repos": [{"name": n} for n in names if n != "External/Project"], "vocab": {"a": "alpha"},
            "relations": {"schema_version": 1, "snapshot_digest": "synthetic-fixture",
                          "nodes": [{"id": n, "name": n} for n in names], "records": records,
                          "predicates": definitions, "coverage": {"recorded_relations": len(records)}},
            "ties": {"machine": [], "curated": []}}


def oracle(graph, query):
    """All simple node paths through at most six hops, tiny fixtures only."""
    outgoing = defaultdict(set)
    predicates = set(query.get("predicates", "").split(",")) - {""}
    grades = set(query.get("grades", "").split(",")) - {""}
    direction = query.get("direction", "outgoing")
    for r in graph["records"]:
        if predicates and r["predicate"] not in predicates or grades and r["grade"] not in grades:
            continue
        a, b = r["from"], r["to"]
        if not r["directed"] or direction == "association":
            outgoing[a].add(b)
            outgoing[b].add(a)
        elif direction == "incoming":
            outgoing[b].add(a)
        else:
            outgoing[a].add(b)
    paths = []
    stack = [[query["from"]]]
    while stack:
        path = stack.pop()
        if path[-1] == query["to"]:
            paths.append(path)
            continue
        if len(path) - 1 < query.get("max_hops", 3):
            for target in outgoing[path[-1]]:
                if target not in path:
                    stack.append(path + [target])
    shortest = min((len(p) for p in paths), default=0)
    return sorted(p for p in paths if len(p) == shortest)


@unittest.skipUnless(shutil.which("node"), "Node is required for shared architecture tool tests")
class ArchitectureTools(unittest.TestCase):
    def query(self, data, queries, timeout=20):
        script = """
          import fs from 'node:fs';
          const {makeTools}=await import(process.argv[1]);
          const input=JSON.parse(fs.readFileSync(0,'utf8'));
          const tools=makeTools(input.bundle,null);
          const run=tools.find(t=>t.name==='architecture');
          const before=JSON.stringify(input.bundle);
          const results=input.queries.map(q=>{try{return {value:run.execute(q)}}catch(e){return {error:e.message}}});
          console.log(JSON.stringify({results,unchanged:before===JSON.stringify(input.bundle)}));
        """
        process = subprocess.run(["node", "--input-type=module", "-e", script, (WEB / "tools.js").as_uri()],
                                 input=json.dumps({"bundle": data, "queries": queries}), capture_output=True,
                                 text=True, check=True, timeout=timeout)
        result = json.loads(process.stdout)
        self.assertTrue(result["unchanged"])
        return result["results"]

    def values(self, data, queries):
        rows = self.query(data, queries)
        self.assertFalse([r for r in rows if "error" in r], rows)
        return [r["value"] for r in rows]

    def test_directed_chain_reverse_incoming_and_association(self):
        data = bundle([record(1, "alpha", "beta"), record(2, "beta", "gamma")])
        q = [{"from": "a", "to": "gamma"}, {"from": "gamma", "to": "alpha"},
             {"from": "gamma", "to": "alpha", "direction": "incoming"},
             {"from": "gamma", "to": "alpha", "direction": "association"}]
        out = self.values(data, q)
        self.assertEqual([r["hops"] for r in out], [2, None, 2, 2])
        self.assertEqual(out[0]["source"], "alpha")
        self.assertEqual(out[2]["paths"][0]["nodes"], ["gamma", "beta", "alpha"])
        hop = out[2]["paths"][0]["hops"][0]
        self.assertEqual((hop["from"], hop["to"]), ("gamma", "beta"))
        self.assertEqual((hop["relations"][0]["from"], hop["relations"][0]["to"]), ("beta", "gamma"))
        self.assertIn("not evidence", out[1]["note"])

    def test_symmetric_relations_and_declared_external_nodes(self):
        data = bundle([record(1, "alpha", "External/Project", "shares_blob", False)])
        out = self.values(data, [{"from": "external/project", "to": "alpha"},
                                 {"from": "alpha", "to": "External/Project", "direction": "incoming"}])
        self.assertEqual([r["hops"] for r in out], [1, 1])
        self.assertFalse(out[0]["paths"][0]["hops"][0]["relations"][0]["directed"])

    def test_predicate_and_grade_filters_keep_semantics_explicit(self):
        data = bundle([record(1, "alpha", "beta"), record(2, "beta", "gamma", "mentions", grade="L"),
                       record(3, "alpha", "gamma", "dependency_reference", grade="C")])
        out = self.values(data, [{"from": "alpha", "to": "gamma", "predicates": "declares_use"},
                                 {"from": "alpha", "to": "gamma", "predicates": "declares_use,mentions", "grades": "C"},
                                 {"from": "alpha", "to": "gamma", "predicates": "declares_use,mentions", "grades": "C,L"}])
        self.assertEqual([r["hops"] for r in out], [None, None, 2])
        self.assertIn("does not prove", out[-1]["semantic_scope"])

    def test_parallel_witnesses_do_not_multiply_node_paths(self):
        records = [record(i, "alpha", "beta") for i in range(8)]
        out = self.values(bundle(records), [{"from": "alpha", "to": "beta"}])[0]
        self.assertEqual(out["total_paths"], 1)
        hop = out["paths"][0]["hops"][0]
        self.assertEqual((hop["total_relations"], hop["returned_relations"], hop["omitted_relations"]), (8, 4, 4))
        self.assertEqual(hop["relations"], records[:4])

    def test_shortest_paths_are_counted_before_whole_path_presentation_limit(self):
        data = bundle([record(1, "alpha", "beta"), record(2, "alpha", "delta"),
                       record(3, "beta", "gamma"), record(4, "delta", "gamma")])
        out = self.values(data, [{"from": "alpha", "to": "gamma", "limit": 1}, {"from": "alpha", "to": "alpha"}])
        self.assertEqual((out[0]["total_paths"], out[0]["returned_paths"], out[0]["omitted_paths"]), (2, 1, 1))
        self.assertEqual(out[1]["paths"], [{"nodes": ["alpha"], "hops": []}])
        self.assertEqual((out[1]["hops"], out[1]["total_paths"]), (0, 1))

    def test_neighbour_subgraph_declares_all_omissions(self):
        data = bundle([record(1, "alpha", "beta"), record(2, "alpha", "delta"),
                       record(3, "beta", "gamma"), record(4, "delta", "gamma")])
        out = self.values(data, [{"from": "alpha", "limit": 1}])[0]
        self.assertEqual((out["total_nodes"], out["returned_nodes"], out["omitted_nodes"]), (3, 1, 2))
        self.assertEqual(out["nodes"], [{"id": "beta", "hops": 1}])
        self.assertEqual((out["total_records"], out["returned_records"], out["omitted_records"]), (4, 1, 3))
        self.assertEqual(out["records"][0]["id"], "relation:1")

    def test_missing_graph_is_unavailable_and_work_budget_is_explicit(self):
        out = self.values({"repos": []}, [{"from": "alpha"}])[0]
        self.assertEqual(out["status"], "unavailable")
        self.assertIn("not a substitute", out["reason"])
        huge = bundle([], [f"node:{i}" for i in range(10001)])
        limited = self.values(huge, [{"from": "node:1"}])[0]
        self.assertEqual(limited["status"], "unavailable")
        self.assertEqual(limited["graph_nodes"], 10001)

    def test_unknown_names_predicates_and_invalid_arguments_are_refused(self):
        data = bundle([record(1, "alpha", "beta")])
        queries = [{"from": "guess"}, {"from": "alpha", "predicates": "runtime_dependency"},
                   {"from": "alpha", "grades": "Z"}, {"from": "alpha", "max_hops": 7},
                   {"from": "alpha", "max_hops": 0}, {"from": "alpha", "limit": 51},
                   {"from": "alpha", "limit": "1"}, {"from": "alpha", "direction": "reversed"},
                   {"from": "alpha", "pretend": True}, {"from": "alpha", "predicates": ["declares_use"]}]
        out = self.query(data, queries)
        self.assertTrue(all("error" in r for r in out), out)

    def test_large_evidence_is_withheld_atomically_and_count_remains_exact(self):
        records = [record(1, "alpha", "beta", text="漢" * 12000)]
        out = self.values(bundle(records), [{"from": "alpha", "to": "beta"}])[0]
        self.assertLessEqual(len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()), 30000)
        self.assertEqual((out["total_paths"], out["returned_paths"], out["omitted_paths"]), (1, 0, 1))
        self.assertEqual(out["paths"], [])
        self.assertIn("Whole path", out["presentation_limited"])

    def test_large_neighbour_names_keep_budget_and_counts_consistent(self):
        names = [f"{i:03}-" + "漢" * 230 for i in range(50)]
        data = bundle([record(i, "source", n) for i, n in enumerate(names)])
        out = self.values(data, [{"from": "source", "limit": 50}])[0]
        self.assertLessEqual(len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()), 14000)
        self.assertEqual(out["returned_nodes"], len(out["nodes"]))
        self.assertEqual(out["omitted_nodes"], 50 - len(out["nodes"]))
        self.assertEqual(out["returned_records"], len(out["records"]))
        self.assertEqual(out["omitted_records"], 50 - len(out["records"]))
        self.assertNotIn("_result_limit", out)

    def test_branching_count_does_not_require_exponential_path_enumeration(self):
        layers = [["source"]] + [[f"L{i}-{j}" for j in range(10)] for i in range(5)] + [["target"]]
        records = []
        for left, right in zip(layers, layers[1:]):
            for a in left:
                for b in right:
                    records.append(record(len(records), a, b))
        out = self.values(bundle(records), [{"from": "source", "to": "target", "max_hops": 6, "limit": 3}])[0]
        self.assertEqual(out["total_paths"], 100000)
        self.assertEqual(out["returned_paths"], 3)
        self.assertEqual(out["omitted_paths"], 99997)
        self.assertLessEqual(out["work"]["examined_arcs"], 420)

    def test_small_random_graphs_match_independent_path_enumeration(self):
        rng = random.Random(204)
        for sample in range(8):
            names = [f"n{i}" for i in range(6)]
            records = []
            for a in names:
                for b in names:
                    if a != b and rng.random() < .17:
                        symmetric = rng.random() < .25
                        records.append(record(len(records), a, b, "shares_blob" if symmetric else "declares_use", not symmetric))
            data = bundle(records, names)
            queries = [{"from": a, "to": b, "direction": direction, "max_hops": 6, "limit": 50}
                       for a, b in [("n0", "n4"), ("n5", "n1")] for direction in ("outgoing", "incoming", "association")]
            values = self.values(data, queries)
            for query, value in zip(queries, values):
                with self.subTest(sample=sample, query=query):
                    expected = oracle(data["relations"], query)
                    self.assertEqual(value["total_paths"], len(expected))
                    self.assertEqual(sorted(p["nodes"] for p in value["paths"]), expected)
                    self.assertEqual(value["hops"], len(expected[0]) - 1 if expected else None)

    def test_python_and_javascript_match_real_projection_and_manual_paths(self):
        from x3dray import relations
        ds = {"family": "fixture", "repos": [{"name": n, "visibility": "public"} for n in ("alpha", "beta", "gamma")]}
        ties = {"pairs": [
            {"from": "alpha", "to": "beta", "kinds": {"uses": 1}, "evidence": [{"kind": "uses", "text": "Fixture configuration declares a use."}]},
            {"from": "beta", "to": "gamma", "kinds": {"shared_file": 1, "mention:docs": 1}, "evidence": [
                {"kind": "shared_file", "path": "src/a.py", "other_path": "copy/a.py", "bytes": 200},
                {"kind": "mention:docs", "path": "README.md", "line": 4, "text": "The fixture discusses gamma."}]}]}
        real_graph = relations.build_relations(ds, ties)
        manual = bundle([record(1, "alpha", "beta"), record(2, "alpha", "delta"), record(3, "beta", "gamma"),
                         record(4, "delta", "gamma"), record(5, "beta", "gamma")])["relations"]
        for graph in (real_graph, manual):
            data = bundle([], [n["id"] for n in graph["nodes"]])
            data["relations"] = graph
            queries = [{"from": "alpha", "to": "gamma", "direction": d, "limit": 1} for d in ("outgoing", "incoming", "association")]
            queries += [{"from": "alpha", "limit": 1}, {"from": "gamma", "direction": "incoming", "limit": 2}]
            values = self.values(data, queries)
            for query, actual in zip(queries, values):
                expected = relations.traverse(graph, query["from"], query.get("to"), direction=query.get("direction", "outgoing"), limit=query.get("limit", 12))
                with self.subTest(query=query, projection=graph is real_graph):
                    if "to" in query:
                        for key in ("hops", "total_paths", "returned_paths", "omitted_paths", "paths"):
                            self.assertEqual(actual[key], expected[key])
                    else:
                        for key in ("nodes", "total_nodes", "returned_nodes", "omitted_nodes", "records", "total_records", "returned_records", "omitted_records"):
                            self.assertEqual(actual[key], expected[key])

    def test_unicode_canonical_order_matches_python_without_browser_locale(self):
        from x3dray import relations
        names = ["z", "A", "ä", "\ue000", "😀"]
        data = bundle([record(i, "source", n) for i, n in enumerate(names)])
        actual = self.values(data, [{"from": "source", "limit": 50}])[0]
        expected = relations.traverse(data["relations"], "source", limit=50)
        self.assertEqual(actual["nodes"], expected["nodes"])
        self.assertEqual([n["id"] for n in actual["nodes"]], sorted(names))

    def test_legacy_traverse_remains_symmetric_but_labels_association_scope(self):
        data = bundle([record(1, "alpha", "beta")])
        data["ties"]["machine"] = [{"from": "alpha", "to": "beta", "grade": "C", "reason": "synthetic", "kinds": {"uses": 1}}]
        script = """
          import fs from 'node:fs';const {makeTools}=await import(process.argv[1]);
          const tools=Object.fromEntries(makeTools(JSON.parse(fs.readFileSync(0,'utf8'))).map(t=>[t.name,t]));
          console.log(JSON.stringify({legacy:tools.traverse.execute({from:'beta',to:'alpha'}),typed:tools.architecture.execute({from:'beta',to:'alpha'}),description:tools.traverse.description}));
        """
        process = subprocess.run(["node", "--input-type=module", "-e", script, (WEB / "tools.js").as_uri()], input=json.dumps(data), capture_output=True, text=True, check=True, timeout=20)
        out = json.loads(process.stdout)
        self.assertEqual(out["legacy"]["hops"], 1)
        self.assertIsNone(out["typed"]["hops"])
        self.assertIn("Undirected", out["legacy"]["semantic_scope"])
        self.assertNotIn("what depends", out["description"])

    def test_mcp_exposes_and_executes_the_same_architecture_tool(self):
        data = bundle([record(1, "alpha", "beta")])
        data["meta"] = {"title": "Synthetic"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            path.write_text(json.dumps(data))
            messages = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "architecture", "arguments": {"from": "alpha", "to": "beta"}}}]
            process = subprocess.run(["node", str(WEB / "mcp.mjs"), "--bundle", str(path)],
                                     input="\n".join(map(json.dumps, messages)) + "\n", capture_output=True, text=True, check=True, timeout=20)
        listed, called = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertIn("architecture", [t["name"] for t in listed["result"]["tools"]])
        answer = json.loads(called["result"]["content"][0]["text"])
        self.assertEqual(answer["paths"][0]["nodes"], ["alpha", "beta"])

    def test_mcp_double_json_encoding_does_not_invalidate_path_counts(self):
        data = bundle([record(i, "alpha", "beta", text='"\\' * 900) for i in range(6)])
        data["meta"] = {"title": "Synthetic"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            path.write_text(json.dumps(data))
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "architecture", "arguments": {"from": "alpha", "to": "beta"}}}
            process = subprocess.run(["node", str(WEB / "mcp.mjs"), "--bundle", str(path)],
                                     input=json.dumps(request) + "\n", capture_output=True, text=True, check=True, timeout=20)
        self.assertLessEqual(len(process.stdout.rstrip().encode()), 32000)
        response = json.loads(process.stdout)
        answer = json.loads(response["result"]["content"][0]["text"])
        self.assertNotIn("_result_limit", answer)
        self.assertEqual(answer["returned_paths"], len(answer["paths"]))
        self.assertEqual(answer["omitted_paths"], answer["total_paths"] - len(answer["paths"]))


if __name__ == "__main__":
    unittest.main()
