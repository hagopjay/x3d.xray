"""Independent laboratory invariants and known-answer topology experiments.

The existing Floyd-Warshall oracle verifies weighted numeric results; these
tests also follow every exported witness back to an actual source observation.
"""
import copy
import hashlib
import json
import tempfile
import unittest
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

from x3dray import lab
from test_centrality import brute_betweenness


@lru_cache(maxsize=1)
def shared_bundle():
    return lab.build_bundle()


def adjacency(result):
    graph = {node["id"]: {} for node in result["nodes"]}
    for edge in result["edges"]:
        graph[edge["a"]][edge["b"]] = Fraction(edge["distance"])
        graph[edge["b"]][edge["a"]] = Fraction(edge["distance"])
    return graph


def components(graph, removed=None):
    """Independent simple reachability helper, not the production graph module."""
    unseen = set(graph) - ({removed} if removed else set())
    found = []
    while unseen:
        frontier = [unseen.pop()]
        group = set(frontier)
        while frontier:
            for neighbour in graph[frontier.pop()]:
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    group.add(neighbour)
                    frontier.append(neighbour)
        found.append(frozenset(group))
    return set(found)


class LaboratoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = shared_bundle()
        cls.source = cls.bundle["commits"]
        cls.by_id = {commit["id"]: commit for commit in cls.source}

    def test_source_fixture_has_unique_observation_ids_and_one_sweep(self):
        self.assertTrue(self.bundle["synthetic"])
        self.assertEqual(len(self.by_id), len(self.source))
        known = {path for path, _, _ in lab.FILES}
        sweeps = [c for c in self.source if c["kind"] == "generated_sweep"]
        self.assertEqual(len(sweeps), 1)
        self.assertEqual(set(sweeps[0]["paths"]), known)
        self.assertEqual(len(sweeps[0]["paths"]), 16)
        for commit in self.source:
            self.assertEqual(commit["paths"], sorted(set(commit["paths"])))
            self.assertTrue(set(commit["paths"]) <= known)

    def test_bundle_and_canonical_fixture_digest_are_deterministic(self):
        again = lab.build_bundle()
        self.assertEqual(self.bundle, again)
        source_bytes = json.dumps(self.source, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(self.bundle["fixture_digest"], hashlib.sha256(source_bytes).hexdigest())
        self.assertEqual(len(self.bundle["scenarios"]), 12)

    def test_every_exported_witness_is_a_real_included_observation(self):
        for key, scenario in self.bundle["scenarios"].items():
            for result in [scenario["baseline"], *scenario["removals"].values()]:
                with self.subTest(lens=key, removed=result["parameters"]["removed"]):
                    excluded = set(result["excluded_commits"])
                    included = [commit for commit in self.source if commit["id"] not in excluded]
                    nodes = {n["id"]: n for n in result["nodes"]}
                    for node in nodes.values():
                        self.assertEqual(node["witnesses"], sorted(set(node["witnesses"])))
                        self.assertEqual(node["witnesses"], sorted(c["id"] for c in included if node["id"] in c["paths"]))
                        for wid in node["witnesses"]:
                            self.assertIn(node["id"], self.by_id[wid]["paths"])
                            self.assertNotIn(wid, excluded)
                    for edge in result["edges"]:
                        self.assertIn(edge["a"], nodes)
                        self.assertIn(edge["b"], nodes)
                        self.assertNotEqual(edge["a"], edge["b"])
                        self.assertEqual(edge["count"], len(set(edge["witnesses"])))
                        expected_ids = [c["id"] for c in included if {edge["a"], edge["b"]} <= set(c["paths"])]
                        self.assertEqual(edge["witnesses"], expected_ids)
                        union = sum(edge["a"] in c["paths"] or edge["b"] in c["paths"] for c in included)
                        numerator = 1 if result["parameters"]["distance"] == "count" else union
                        self.assertEqual(Fraction(edge["distance"]), Fraction(numerator, len(expected_ids)))
                        for wid in edge["witnesses"]:
                            self.assertTrue({edge["a"], edge["b"]} <= set(self.by_id[wid]["paths"]))
                            self.assertNotIn(wid, excluded)
                    self.assertEqual(result["included_commits"] + len(excluded), len(self.source))

    def test_weighted_values_match_existing_independent_oracle(self):
        for key, scenario in self.bundle["scenarios"].items():
            baseline = scenario["baseline"]
            graph = adjacency(baseline)
            expected = brute_betweenness(graph, dist_of=lambda value: value)
            for node in baseline["nodes"]:
                with self.subTest(lens=key, node=node["id"]):
                    self.assertAlmostEqual(node["betweenness"], expected[node["id"]], places=7)
                    denominator = (node["component"] - 1) * (node["component"] - 2) / 2
                    self.assertAlmostEqual(node["share"], expected[node["id"]] / denominator if denominator else 0, places=7)

    def test_components_degrees_and_cuts_match_actual_topology(self):
        for key, scenario in self.bundle["scenarios"].items():
            baseline = scenario["baseline"]
            graph = adjacency(baseline)
            groups = components(graph)
            self.assertEqual({frozenset(group) for group in baseline["components"]}, groups)
            for node in baseline["nodes"]:
                with self.subTest(lens=key, node=node["id"]):
                    group = next(c for c in groups if node["id"] in c)
                    self.assertEqual(node["component"], len(group))
                    self.assertEqual(node["degree"], len(graph[node["id"]]))
                    # Removing an isolated vertex lowers component count; it is
                    # correctly never a cut. A cut increases the total count.
                    self.assertEqual(node["cut"], len(components(graph, node["id"])) > len(groups))

    def test_cutoff_crosses_the_deliberate_sixteen_file_cliff(self):
        small = self.bundle["scenarios"]["5:count:0"]["baseline"]
        default = self.bundle["scenarios"]["15:count:0"]["baseline"]
        broad = self.bundle["scenarios"]["30:count:0"]["baseline"]
        sweep = next(c for c in self.source if c["kind"] == "generated_sweep")
        self.assertEqual(small["edges"], default["edges"])
        self.assertEqual(default["excluded_commits"], [sweep["id"]])
        self.assertEqual(broad["excluded_commits"], [])
        self.assertEqual(broad["included_commits"], default["included_commits"] + 1)
        self.assertEqual(len(broad["edges"]), len(sweep["paths"]) * (len(sweep["paths"]) - 1) // 2)
        self.assertEqual(sum(e["count"] for e in broad["edges"]) - sum(e["count"] for e in default["edges"]), 120)
        self.assertTrue(all(sweep["id"] in e["witnesses"] for e in broad["edges"]))

    def test_hub_pruning_exposes_cross_community_articulation(self):
        all_files = self.bundle["scenarios"]["15:count:0"]["baseline"]
        pruned = self.bundle["scenarios"]["15:count:1"]["baseline"]
        self.assertEqual(pruned["pruned_nodes"], ["README.md"])
        self.assertNotIn("README.md", {n["id"] for n in pruned["nodes"]})
        self.assertFalse(any(n["cut"] for n in all_files["nodes"] if n["id"] == "evidence.py"))
        self.assertTrue(next(n["cut"] for n in pruned["nodes"] if n["id"] == "evidence.py"))
        removed = self.bundle["scenarios"]["15:count:1"]["removals"]["evidence.py"]
        parts = [set(group) for group in removed["components"]]
        self.assertFalse(any({"ingest.py", "ui.js"} <= group for group in parts))

    def test_generated_sweep_admission_eliminates_obligatory_cuts(self):
        for prune in (0, 1):
            result = self.bundle["scenarios"][f"30:jaccard:{prune}"]["baseline"]
            self.assertFalse(any(n["cut"] for n in result["nodes"]))
            self.assertEqual(len(result["components"]), 1)

    def test_node_removal_only_withholds_incident_edges_and_keeps_source(self):
        before = copy.deepcopy(self.source)
        for key, scenario in self.bundle["scenarios"].items():
            baseline = scenario["baseline"]
            for removed, result in scenario["removals"].items():
                with self.subTest(lens=key, removed=removed):
                    expected_nodes = {n["id"] for n in baseline["nodes"]} - {removed}
                    self.assertEqual({n["id"] for n in result["nodes"]}, expected_nodes)
                    self.assertEqual(result["edges"], [e for e in baseline["edges"] if removed not in (e["a"], e["b"])])
                    self.assertEqual(result["source_commits"], baseline["source_commits"])
                    self.assertEqual(result["included_commits"], baseline["included_commits"])
                    self.assertEqual(result["excluded_commits"], baseline["excluded_commits"])
                    self.assertEqual(components(adjacency(result)), components(adjacency(baseline), removed))
        self.assertEqual(self.source, before)

    def test_returned_layout_does_not_mutate_catalogue(self):
        original = copy.deepcopy(lab.FILES)
        try:
            result = lab.analyse(self.source)
            result["nodes"][0]["pos"][0] = -999
            self.assertEqual(lab.FILES, original)
        finally:
            lab.FILES[:] = original

    def test_invalid_lenses_and_unknown_removal_fail_explicitly(self):
        for kwargs in ({"max_files": 16}, {"distance": "invented"}, {"removed": "not-in-fixture.py"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    lab.analyse(self.source, **kwargs)


class LaboratoryExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text('<style>/*__CSS__*/</style><script>const B=__LAB_DATA__;/*__JS__*/</script>')
        (self.web / "lab.css").write_text("body{color:black}")
        (self.web / "lab.js").write_text("void B;")
        (self.web / "start_lab.py").write_text("# synthetic launcher template\n")

    def build(self, site, xite=None, bundle=None):
        with patch.object(lab, "WEB", self.web), patch.object(lab, "build_bundle", return_value=bundle if bundle is not None else shared_bundle()):
            return lab.build(str(site), str(xite) if xite else None)

    def test_embedded_source_round_trips_script_delimiters_without_injection(self):
        bundle = {"fixture_digest": "synthetic-test", "analysis_digest": "synthetic-analysis", "scenarios": {}, "commits": [
            {"subject": "</script><script>throw new Error('canary')</script>"}]}
        site = self.root / "script-text"
        self.build(site, bundle=bundle)
        html = (site / "index.html").read_text()
        encoded = html.partition("const B=")[2].rpartition(";void B;")[0]
        self.assertEqual(json.loads(encoded), bundle)
        self.assertNotIn(bundle["commits"][0]["subject"], html)

    def test_detached_export_manifest_matches_bytes_and_safe_relative_paths(self):
        site = self.root / "out"
        rep = self.build(site)
        receipt = json.loads((site / "lab-receipt.json").read_text())
        self.assertEqual(receipt["fixture_digest"], rep["fixture_digest"])
        self.assertFalse(receipt["xite_vendored"])
        self.assertEqual({a["path"] for a in receipt["assets"]}, {"index.html", "lab-data.json"})
        for asset in receipt["assets"]:
            path = Path(asset["path"])
            self.assertFalse(path.is_absolute())
            self.assertNotIn("..", path.parts)
            data = (site / path).read_bytes()
            self.assertEqual(asset["bytes"], len(data))
            self.assertEqual(asset["sha256"], hashlib.sha256(data).hexdigest())
        index = (site / "index.html").read_text()
        self.assertNotIn("__LAB_DATA__", index)
        self.assertNotIn("/*__CSS__*/", index)
        self.assertNotIn("/*__JS__*/", index)

    def test_unknown_stale_file_is_never_silently_added_to_manifest(self):
        site = self.root / "out"
        site.mkdir()
        (site / "stale-private.json").write_text('{"synthetic":true}')
        try:
            self.build(site)
        except (ValueError, FileExistsError):
            return
        receipt = json.loads((site / "lab-receipt.json").read_text())
        self.assertNotIn("stale-private.json", {a["path"] for a in receipt["assets"]})

    def test_invalid_vendor_input_does_not_overwrite_existing_entrypoint(self):
        site = self.root / "out"
        site.mkdir()
        entry = site / "index.html"
        entry.write_text("keep existing output")
        bad_vendor = self.root / "bad-vendor"
        bad_vendor.mkdir()
        with self.assertRaises(ValueError):
            self.build(site, bad_vendor)
        self.assertEqual(entry.read_text(), "keep existing output")


if __name__ == "__main__":
    unittest.main()
