"""Source, time and scale-export integration over disclosed synthetic inputs."""
import copy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from x3dray import prooflab, relations, scaling
from x3dray.locators import verify_locator


ROOT = Path(__file__).resolve().parents[1]


class EmbeddedData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "proof-data":
            self.active = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


class ContinuumSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.sources = Path(cls.temporary.name) / "sources"
        cls.cfg, cls.dataset, cls.found, _, _ = prooflab.fixture_inputs(cls.sources)

    def test_source_audit_checks_four_real_locators_and_rejects_actual_tampering(self):
        audit = self.found["locator_audit"]
        self.assertEqual(audit["checked"], 4)
        self.assertEqual(len(audit["records"]), 4)
        self.assertTrue(all(r["verification"]["ok"] for r in audit["records"]))
        self.assertFalse(audit["tamper_probe"]["ok"])
        self.assertTrue(audit["tamper_probe"]["findings"])
        for row in audit["records"]:
            self.assertIn("commit_and_tree_object_hashes", row["verification"]["checks"])
            self.assertIn("span_bounds_and_sha256", row["verification"]["checks"])

    def test_exported_sources_remain_four_inspectable_gits_with_exact_offline_witnesses(self):
        repos = self.dataset["repos"]
        self.assertEqual({r["name"] for r in repos}, {"ingest", "evidence", "render", "review"})
        self.assertEqual(sum(len(r["commits"]) for r in repos), 24)
        by_id = {r["repo_id"]: self.sources / r["name"] for r in repos}
        for repo in repos:
            checkout = self.sources / repo["name"]
            self.assertTrue((checkout / ".git").is_dir())
            counted = subprocess.run(["git", "-C", str(checkout), "rev-list", "--count", "HEAD"], check=True, capture_output=True, text=True)
            self.assertEqual(counted.stdout.strip(), "6")
            remotes = subprocess.run(["git", "-C", str(checkout), "remote"], check=True, capture_output=True, text=True)
            self.assertEqual(remotes.stdout.strip(), "")
        for record in self.found["locator_audit"]["records"]:
            locator = record["locator"]
            self.assertTrue(verify_locator(str(by_id[locator["repo_id"]]), locator, repo_id=locator["repo_id"])["ok"])

    def test_source_export_refuses_nonempty_destination_without_changing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "existing.txt"
            marker.write_text("existing work")
            with self.assertRaisesRegex(ValueError, "empty directory"):
                prooflab.fixture_inputs(root)
            self.assertEqual(marker.read_text(), "existing work")
            self.assertEqual(list(root.iterdir()), [marker])

    def test_cli_exports_sources_and_verifies_then_rejects_a_tampered_witness(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "sources"
            result = subprocess.run([sys.executable, "-m", "x3dray", "fixture-sources", "--out", str(out)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["public_locators_checked"], 4)
            audit = json.loads((out / "source-audit.json").read_text())
            dataset = json.loads((out / "dataset.json").read_text())
            witness = out / "witnesses" / "0000.json"
            locator = json.loads(witness.read_text())
            repo = next(r for r in dataset["repos"] if r["repo_id"] == locator["repo_id"])
            command = [sys.executable, "-m", "x3dray", "verify-witness", "--repo", str(out / repo["name"]), "--repo-id", locator["repo_id"], "--locator", str(witness)]
            passed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertTrue(json.loads(passed.stdout)["ok"])
            self.assertEqual(audit["checked"], 4)
            locator["span"]["content_sha256"] = "0" * 64
            witness.write_text(json.dumps(locator))
            failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 1, failed.stderr)
            self.assertFalse(json.loads(failed.stdout)["ok"])


class ContinuumTemporalTests(unittest.TestCase):
    def test_disjoint_intervals_never_establish_a_definite_path_at_one_instant(self):
        fixture = prooflab.temporal_fixture()
        self.assertTrue(fixture["synthetic"])
        graph = fixture["relations"]
        for day in (1, 2, 3, 4, 5, 6):
            with self.subTest(day=day):
                result = relations.traverse(graph, "alpha", "gamma", at=f"2026-01-{day:02d}T00:00:00Z", time_mode="valid_at")
                self.assertEqual(result["total_paths"], 0)
        result = relations.traverse(graph, "alpha", "gamma", at="2026-01-03T00:00:00Z", time_mode="valid_at", unknown_time="include")
        self.assertEqual(result["total_paths"], 1)
        self.assertEqual(result["paths"][0]["nodes"], ["alpha", "delta", "gamma"])
        self.assertEqual(result["paths"][0]["time_status"], "provisional_unknown_time")
        observed = relations.traverse(graph, "alpha", "gamma", at="2026-01-05T00:00:00Z", time_mode="observed_by")
        self.assertEqual(observed["total_paths"], 2)
        self.assertTrue(all(p["time_status"] == "known_time" for p in observed["paths"]))
        self.assertEqual({tuple(p["nodes"]) for p in observed["paths"]}, {("alpha", "beta", "gamma"), ("alpha", "delta", "gamma")})


class ContinuumBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = prooflab.build_bundle()

    def test_analysis_identity_changes_with_temporal_claims_and_implementation_bytes(self):
        changed_time = copy.deepcopy(self.bundle["temporal_fixture"])
        changed_time["relations"]["records"][0]["valid_time"]["end"] = "2026-01-04T00:00:00Z"
        with patch.object(prooflab, "temporal_fixture", return_value=changed_time):
            changed = prooflab.build_bundle()
        self.assertEqual(changed["fixture_digest"], self.bundle["fixture_digest"])
        self.assertNotEqual(changed["analysis_digest"], self.bundle["analysis_digest"])
        source = prooflab.WEB / "proof.js"
        original = Path.read_bytes
        def changed_bytes(path):
            value = original(path)
            return value + b"\n// synthetic implementation identity probe\n" if path == source else value
        with patch.object(Path, "read_bytes", changed_bytes):
            changed = prooflab.build_bundle()
        self.assertNotEqual(changed["implementation"]["proof_web/proof.js"], self.bundle["implementation"]["proof_web/proof.js"])
        self.assertNotEqual(changed["analysis_digest"], self.bundle["analysis_digest"])
        self.assertEqual(changed["fixture_digest"], self.bundle["fixture_digest"])

    def benchmark_fixture(self, root):
        fixture = scaling.build_fixture(10, "modular")
        artifacts = root / "artifacts"
        artifacts.mkdir()
        (artifacts / "fixture.json").write_text(json.dumps({"plan": fixture["plan"]}))
        (artifacts / "scene.x3d").write_text(fixture["xml"])
        report = {"synthetic": True, "disclosure": "Test fixture only; timing is not measured.", "artifact_directory": "artifacts",
                  "rows": [{"size": 10, "topology": "modular", "artifacts": {"fixture": "fixture.json", "scene": "scene.x3d"}}]}
        path = root / "benchmark.json"
        path.write_text(json.dumps(report))
        return fixture, report, path

    def test_benchmark_artifact_failures_preserve_an_existing_entrypoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, report, path = self.benchmark_fixture(root)
            out = root / "out"
            out.mkdir()
            entry = out / "index.html"
            entry.write_text("Existing entrypoint")
            for name in ("missing.x3d", "../outside.x3d"):
                broken = copy.deepcopy(report)
                broken["rows"][0]["artifacts"]["scene"] = name
                path.write_text(json.dumps(broken))
                with self.subTest(name=name), self.assertRaises((ValueError, OSError)):
                    prooflab.build(str(out), benchmark=str(path))
                self.assertEqual(entry.read_text(), "Existing entrypoint")
            (root / "outside.x3d").write_text("Outside artifact")
            (root / "artifacts" / "link.x3d").symlink_to(root / "outside.x3d")
            report["rows"][0]["artifacts"]["scene"] = "link.x3d"
            path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                prooflab.build(str(out), benchmark=str(path))
            self.assertEqual(entry.read_text(), "Existing entrypoint")

    def test_small_scale_preview_and_copied_artifacts_are_embedded_and_attested(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, report, path = self.benchmark_fixture(root)
            out = root / "out"
            prooflab.build(str(out), benchmark=str(path))
            bundle = json.loads((out / "observatory-data.json").read_text())
            self.assertEqual(bundle["scale_results"], report)
            self.assertIn("not measured", bundle["scale_results"]["disclosure"])
            preview = bundle["scale_previews"][0]
            self.assertEqual(preview["size"], 10)
            self.assertEqual(preview["entities"], fixture["plan"]["entities"])
            self.assertEqual(preview["scene"], "scale/scene.x3d")
            self.assertEqual((out / preview["scene"]).read_text(), fixture["xml"])
            parser = EmbeddedData()
            parser.feed((out / "index.html").read_text())
            self.assertEqual(json.loads("".join(parser.parts)), bundle)
            receipt = json.loads((out / "lab-receipt.json").read_text())
            assets = {row["path"]: row for row in receipt["assets"]}
            for name in ("scale/fixture.json", "scale/scene.x3d"):
                self.assertIn(name, assets)
                self.assertEqual(assets[name]["sha256"], hashlib.sha256((out / name).read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
