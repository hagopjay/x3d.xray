"""Synthetic scale fixtures exercise real compiler/checker/query code paths."""
import copy
import itertools
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from x3dray import relations, scaling, scenecheck


class ScaleFixtures(unittest.TestCase):
    def test_declared_entities_sources_and_scene_are_complete(self):
        for topology in scaling.TOPOLOGIES:
            fixture = scaling.build_fixture(10, topology)
            self.assertEqual(len(fixture["dataset"]["repos"]), 10)
            self.assertEqual(len(fixture["graph"]["nodes"]), 10)
            self.assertEqual(len(fixture["plan"]["entities"]), 10)
            self.assertIn("Pure synthetic", fixture["metadata"]["disclosure"])
            self.assertEqual(fixture["plan"]["coverage"]["source_relations"], len(fixture["ties"]["pairs"]) + len(fixture["curated"]["ties"]))
            checked = scenecheck.verify_scene(fixture["xml"], fixture["plan"], fixture["bundle"])
            self.assertTrue(checked["ok"], checked["findings"])
            self.assertEqual(checked["checks"]["entities"], 10)

    def test_build_is_deterministic_and_does_not_require_git(self):
        first, second = scaling.build_fixture(100, "modular"), scaling.build_fixture(100, "modular")
        self.assertEqual(first, second)
        self.assertGreater(first["metadata"]["max_identifier_characters"], 100)
        self.assertLessEqual(first["metadata"]["max_identifier_characters"], 256)

    def test_adaptive_thousand_entity_layout_has_no_glyph_overlap(self):
        fixture = scaling.build_fixture(1000)
        self.assertEqual(fixture["geometry"]["glyph_overlap_pairs"], 0)
        self.assertGreater(fixture["geometry"]["closest_pair_surface_gap"], 0.3)
        self.assertEqual(fixture["plan"]["layout_policy"]["id"], "adaptive-concentric-v1")

    def test_large_glyph_bounds_expand_rings_without_overlap(self):
        fixture = scaling.build_fixture(120)
        profile = copy.deepcopy(fixture["profile"])
        for index, row in enumerate(profile["per_repo"].values()):
            row["own_commits"] = 10 ** (index % 13)
        built = scaling.compile_constellation(fixture["dataset"], fixture["ties"], fixture["curated"], profile)
        metrics = scaling.geometry_metrics(built["plan"])
        self.assertEqual(metrics["glyph_overlap_pairs"], 0)
        self.assertGreater(metrics["closest_pair_surface_gap"], 0.3)

    def test_extracted_scale_scene_has_camera_for_dynamic_region(self):
        fixture = scaling.build_fixture(1000)
        root = ET.fromstring(fixture["xml"])
        self.assertIsNotNone(root.find(".//Background"))
        self.assertIsNotNone(root.find(".//NavigationInfo"))
        camera = root.find(".//Viewpoint[@DEF='VP_scale']")
        position = [float(x) for x in camera.get("position").split()]
        center = [float(x) for x in camera.get("centerOfRotation").split()]
        radius = fixture["layout"]["regions"]["ties"]["r"]
        self.assertGreater(position[2] - center[2], radius / math.tan(0.4))

    def test_parallel_observations_do_not_multiply_node_paths(self):
        fixture = scaling.build_fixture(10, "modular")
        self.assertEqual(fixture["oracle"]["forward"]["total_paths"], 2)
        self.assertEqual(fixture["oracle"]["forward"]["hops"], 3)
        for query in fixture["queries"]:
            result = scaling._run_python(fixture["graph"], query["args"])
            scaling._assert_query(result, fixture["oracle"][query["id"]])
            for path in result["paths"]:
                self.assertTrue(all(hop["total_relations"] == 2 for hop in path["hops"]))
        self.assertEqual(fixture["oracle"]["disconnected"]["total_paths"], 0)

    def test_oracle_rejects_fabricated_query_answer(self):
        fixture = scaling.build_fixture(10)
        result = scaling._run_python(fixture["graph"], fixture["queries"][0]["args"])
        result["total_paths"] = 999
        with self.assertRaisesRegex(AssertionError, "oracle"):
            scaling._assert_query(result, fixture["oracle"]["forward"])

    def test_geometry_metrics_match_independent_pair_enumeration(self):
        rnd = random.Random(142)
        entities = [{"id": str(i), "position": [rnd.uniform(-3, 3) for _ in range(3)],
                     "attachment_radius": rnd.uniform(0.1, 0.8)} for i in range(80)]
        pairs = list(itertools.combinations(entities, 2))
        want_overlaps = sum(math.dist(a["position"], b["position"]) < a["attachment_radius"] + b["attachment_radius"] for a, b in pairs)
        want_distance = min(math.dist(a["position"], b["position"]) for a, b in pairs)
        got = scaling.geometry_metrics({"entities": entities})
        self.assertEqual(got["glyph_overlap_pairs"], want_overlaps)
        self.assertAlmostEqual(got["closest_center_distance"], want_distance)

    def test_fixture_and_measurement_bounds_are_explicit(self):
        for size in (0, 9, 10001, True):
            with self.assertRaises(ValueError):
                scaling.build_fixture(size)
        with self.assertRaises(ValueError):
            scaling.build_fixture(10, "unknown")
        with self.assertRaisesRegex(ValueError, "five repetitions"):
            scaling.benchmark(repeats=1)

    def test_percentiles_retain_raw_samples_and_method(self):
        result = scaling.statistics_ms([8, 1, 3, 2, 5], warmups=2)
        self.assertEqual(result["p50_ms"], 3)
        self.assertEqual(result["p95_ms"], 8)
        self.assertEqual(result["samples_ms"], [8, 1, 3, 2, 5])
        self.assertEqual(result["repeats"], 5)
        self.assertEqual(result["warmups"], 2)

    def test_default_scene_expansion_budget_is_a_refusal(self):
        result = scaling._expansion_budget_probe()
        self.assertFalse(result["accepted"])
        self.assertEqual(result["expanded_visits"], 250000)
        self.assertIn("scene_expansion_limit", result["finding_codes"])

    def test_actual_python_graph_budget_guard_refuses_both_limits(self):
        result = scaling._graph_budget_probes()
        self.assertTrue(result["nodes"]["refused"])
        self.assertTrue(result["records"]["refused"])
        self.assertEqual(result["nodes"]["nodes"], 10001)
        self.assertEqual(result["records"]["records"], 50001)

    @unittest.skipUnless(shutil.which("node"), "Node needed for shared production tool measurement")
    def test_shared_javascript_harness_matches_same_graph_oracle(self):
        fixture = scaling.build_fixture(10, "modular")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.json"
            path.write_text(json.dumps({k: v for k, v in fixture.items() if k != "xml"}))
            process = subprocess.run(["node", str(Path(scaling.__file__).with_name("web") / "scale_benchmark.cjs"),
                                      "--fixture", str(path), "--tools", str(scaling.ROOT / "x3dray/web/tools.js"),
                                      "--repeats", "5", "--warmups", "1"], capture_output=True, text=True, check=True, timeout=30)
        result = json.loads(process.stdout)
        self.assertTrue(all(q["oracle_equal"] for q in result["queries"].values()))
        self.assertEqual(result["queries"]["forward"]["total_paths"], 2)
        self.assertTrue(all(p["refused"] for p in result["budget_probes"].values()))

    @unittest.skipUnless(shutil.which("node"), "Node needed for installed-package path regression")
    def test_benchmark_runs_from_package_only_without_experiments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(scaling.__file__).parent, root / "x3dray", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            self.assertFalse((root / "experiments").exists())
            script = ("import json; from x3dray.scaling import benchmark; "
                      "r=benchmark(sizes=(10,),topologies=('chain',),repeats=5,warmups=1,output_dir='out'); "
                      "print(json.dumps({'rows':len(r['rows']),'hashes':list(r['source_sha256']),"
                      "'absent':r['unavailable_project_harnesses'],'js':r['rows'][0]['javascript']['queries']}))")
            process = subprocess.run([sys.executable, "-c", script], cwd=root,
                                     env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True, text=True, check=True, timeout=90)
        result = json.loads(process.stdout)
        self.assertEqual(result["rows"], 1)
        self.assertIn("x3dray/web/scale_benchmark.cjs", result["hashes"])
        self.assertEqual(set(result["absent"]), {"experiments/benchmark_architecture_scale.py", "experiments/benchmark_architecture_scale.cjs"})
        self.assertTrue(all(item["oracle_equal"] for item in result["js"].values()))


if __name__ == "__main__":
    unittest.main()
