"""The release gate reads emitted geometry, independently of stale layout."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from x3dray import gates
from x3dray.scene_mutations import mutate_cases
from test_scene_plan import region, machine


class SceneGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pair = machine()
        self.xml, self.layout = region(pairs=[self.pair])
        self.bundle = {"repos": [{"name": "alpha"}, {"name": "beta"}], "layout": self.layout,
                       "ties": {"machine": [self.pair], "curated": []}}
        self.write()

    def write(self):
        (self.root / "xray.x3d").write_text(self.xml)
        (self.root / "layout.json").write_text(json.dumps(self.layout))
        (self.root / "bundle.json").write_text(json.dumps(self.bundle))

    def test_actual_geometry_mutation_fails_with_unchanged_control_files(self):
        self.assertEqual(gates.gate_scene(str(self.root))["status"], "pass")
        controls = [(self.root / p).read_bytes() for p in ("layout.json", "bundle.json")]
        altered = next(c for c in mutate_cases(self.xml) if c["id"] == "wrong_edge_endpoint")
        (self.root / "xray.x3d").write_text(altered["xml"])
        result = gates.gate_scene(str(self.root))
        self.assertEqual(result["status"], "fail")
        self.assertIn("relation_geometry_mismatch", [f["code"] for f in result["findings"]])
        self.assertEqual(controls, [(self.root / p).read_bytes() for p in ("layout.json", "bundle.json")])

    def test_served_plan_must_match_build_control_plan(self):
        self.bundle = copy.deepcopy(self.bundle)
        self.bundle["layout"]["scene_plan"]["entities"][0]["position"][0] += 1
        (self.root / "bundle.json").write_text(json.dumps(self.bundle))
        self.assertEqual(gates.gate_scene(str(self.root))["status"], "fail")

    def test_legacy_export_is_unverified_and_scene_is_required_for_release(self):
        self.layout = {}
        self.bundle["layout"] = {}
        self.write()
        self.assertEqual(gates.gate_scene(str(self.root))["status"], "skip")
        self.assertIn("scene", gates.REQUIRED_RELEASE_GATES)
