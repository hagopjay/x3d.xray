"""Integration contracts for the synthetic Architecture Observatory export.

These tests exercise the real Git specimen and independent checker. Browser
layout, WebGL rendering and full-family release readiness are separate checks.
"""
import copy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from x3dray import investigation, prooflab, relations, scenecheck


class DataScript(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.matches = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == "proof-data":
            self.active = True
            self.matches += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, text):
        if self.active:
            self.parts.append(text)


class ObservatoryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = prooflab.build_bundle()

    def test_real_git_specimen_has_four_repositories_and_twenty_four_observations(self):
        b = self.bundle
        self.assertTrue(b["synthetic"])
        repos = b["tool_bundle"]["repos"]
        self.assertEqual({r["name"] for r in repos}, {"ingest", "evidence", "render", "review"})
        observations = []
        for repo in repos:
            self.assertTrue(repo["present"])
            self.assertEqual(repo["default_branch"], "HEAD")
            self.assertEqual(len(repo["commits"]), 6)
            self.assertEqual(repo["own_commits"], 6)
            self.assertEqual(repo["visibility"], "public")
            for commit in repo["commits"]:
                self.assertRegex(commit["sha"], r"^[0-9a-f]{12}$")
                self.assertTrue(commit["subject"].startswith("Synthetic "))
                self.assertNotIn("_oid", commit)
                observations.append(commit["sha"])
        self.assertEqual(len(observations), 24)
        self.assertEqual(len(set(observations)), 24)
        serialized = json.dumps(b)
        self.assertNotIn("fixture@example.invalid", serialized)
        self.assertNotIn("claude.ai/code/session_", serialized)

    def test_plan_identities_and_typed_records_share_the_source_population(self):
        b = self.bundle
        names = {r["name"] for r in b["tool_bundle"]["repos"]}
        entities = b["plan"]["entities"]
        self.assertEqual({e["source_name"] for e in entities}, names)
        self.assertEqual({e["id"] for e in entities}, {"node:" + n for n in names})
        graph = b["tool_bundle"]["relations"]
        self.assertEqual({n["id"] for n in graph["nodes"]}, names)
        ids = set()
        for record in graph["records"]:
            self.assertNotIn(record["id"], ids)
            ids.add(record["id"])
            self.assertIn(record["from"], names)
            self.assertIn(record["to"], names)
            self.assertTrue(record["evidence"])
            self.assertEqual(record["directed"], graph["predicates"][record["predicate"]]["directed"])
            raw = record.get("source_endpoints", record)
            self.assertTrue(any(p["source"] == record["source"] and p["from"] == raw["from"] and p["to"] == raw["to"]
                                for p in b["plan"]["relations"]))
        self.assertEqual(set(b["catalog"]["entities"]), {e["id"] for e in entities})
        self.assertEqual(set(b["catalog"]["relations"]), ids)

    def test_dependency_reference_chain_has_direction_without_runtime_inference(self):
        graph = self.bundle["tool_bundle"]["relations"]
        filters = ["declares_use", "dependency_reference"]
        forward = relations.traverse(graph, "review", "ingest", predicates=filters, direction="outgoing")
        backward = relations.traverse(graph, "ingest", "review", predicates=filters, direction="outgoing")
        incoming = relations.traverse(graph, "ingest", "review", predicates=filters, direction="incoming")
        self.assertEqual(forward["paths"][0]["nodes"], ["review", "render", "evidence", "ingest"])
        self.assertEqual(forward["hops"], 3)
        self.assertEqual(backward["total_paths"], 0)
        self.assertEqual(incoming["paths"][0]["nodes"], ["ingest", "evidence", "render", "review"])
        self.assertIn("runtime", forward["scope"])
        self.assertTrue(all(not r["directed"] for r in graph["records"] if r["source"] == "curated"))

    def test_actual_xml_cases_obey_their_declared_accept_reject_scope(self):
        b = self.bundle
        before = copy.deepcopy((b["plan"], b["tool_bundle"]))
        for case in b["cases"]:
            result = scenecheck.verify_scene(case["xml"], b["plan"], b["tool_bundle"])
            self.assertEqual(result, case["verification"])
            if case["scope_probe"]:
                self.assertIsNone(case["expected_ok"])
                self.assertTrue(result["ok"])
            else:
                self.assertEqual(result["ok"], case["expected_ok"], case["id"])
            if case["expected_ok"] is False:
                self.assertGreater(result["finding_count"], 0, case["id"])
        self.assertEqual((b["plan"], b["tool_bundle"]), before)
        cases = {c["id"]: c for c in b["cases"]}
        self.assertTrue(cases["baseline"]["verification"]["ok"])
        self.assertTrue(cases["equivalent_nested_transforms"]["verification"]["ok"])
        self.assertFalse(cases["wrong_edge_endpoint"]["verification"]["ok"])

    def test_constellation_excerpt_has_resolvable_shared_definitions_only(self):
        root = ET.fromstring(self.bundle["cases"][0]["xml"])
        defs = [e.get("DEF") for e in root.iter() if e.get("DEF")]
        self.assertEqual(len(defs), len(set(defs)))
        self.assertTrue(all(e.get("USE") in defs for e in root.iter() if e.get("USE")))
        self.assertIsNotNone(root.find(".//Group[@DEF='Ties']"))
        self.assertIsNotNone(root.find(".//Group[@DEF='XraySemanticEntities']"))
        self.assertIsNotNone(root.find(".//Group[@DEF='XraySemanticRelations']"))
        self.assertIsNone(root.find(".//Group[@DEF='Helix']"))
        self.assertIsNone(root.find(".//Group[@DEF='City']"))

    def test_repeated_real_fixture_build_is_deterministic_and_context_bound(self):
        again = prooflab.build_bundle()
        self.assertEqual(self.bundle, again)
        for key in ("fixture_digest", "analysis_digest"):
            self.assertRegex(again[key], r"^[0-9a-f]{64}$")
        self.assertNotEqual(again["fixture_digest"], again["analysis_digest"])
        self.assertTrue(again["implementation"])
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in again["implementation"].values()))

    def test_example_history_replays_against_exact_catalogue_and_context(self):
        b = self.bundle
        state = investigation.replay(b["example_log"], b["catalog"], b["fixture_digest"], b["analysis_digest"])
        self.assertEqual(state, b["example_state"])
        self.assertEqual(state["query"], {"from_entity": "node:review", "to_entity": "node:ingest"})
        self.assertEqual(state["requested_mutation"], "baseline")
        self.assertEqual(state["selected_entity"], "node:render")
        with self.assertRaises(investigation.InvestigationError):
            investigation.replay(b["example_log"], b["catalog"], b["fixture_digest"], "0" * 64)


class ObservatoryExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = prooflab.build_bundle()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def build(self, out, **kwargs):
        with patch.object(prooflab, "build_bundle", return_value=copy.deepcopy(self.bundle)):
            return prooflab.build(str(out), **kwargs)

    def test_export_embeds_source_exactly_and_registers_its_own_outputs(self):
        out = self.root / "out"
        out.mkdir()
        (out / "stale.json").write_text('{"synthetic_canary":true}')
        result = self.build(out)
        html = (out / "index.html").read_text()
        script = DataScript()
        script.feed(html)
        self.assertEqual(script.matches, 1)
        self.assertEqual(json.loads("".join(script.parts)), self.bundle)
        self.assertEqual(json.loads((out / "observatory-data.json").read_text()), self.bundle)
        self.assertEqual((out / "baseline.x3d").read_text(), self.bundle["cases"][0]["xml"])
        receipt = json.loads((out / "lab-receipt.json").read_text())
        self.assertEqual({a["path"] for a in receipt["assets"]}, {"index.html", "observatory-data.json", "baseline.x3d"})
        self.assertEqual({a["path"] for a in receipt["controls"]}, {"start_lab.py"})
        for item in receipt["assets"] + receipt["controls"]:
            path = Path(item["path"])
            self.assertFalse(path.is_absolute())
            self.assertNotIn("..", path.parts)
            self.assertEqual(item["sha256"], hashlib.sha256((out / path).read_bytes()).hexdigest())
        self.assertEqual(receipt["analysis_digest"], self.bundle["analysis_digest"])
        self.assertEqual(result["entities"], 4)
        self.assertEqual(result["cases"], len(self.bundle["cases"]))

    def test_script_delimiters_and_template_tokens_round_trip_as_data(self):
        modified = copy.deepcopy(self.bundle)
        payload = "</script><script>throw new Error('synthetic')</script> /*__APP__*/ /*__SHARED_TOOLS__*/ /*__EVENT_ENGINE__*/"
        modified["source_tips"]["review"]["canary.txt"] = payload
        out = self.root / "out"
        with patch.object(prooflab, "build_bundle", return_value=modified):
            prooflab.build(str(out))
        html = (out / "index.html").read_text()
        script = DataScript()
        script.feed(html)
        self.assertEqual(script.matches, 1)
        self.assertEqual(json.loads("".join(script.parts)), modified)
        self.assertNotIn("</script><script>throw new Error('synthetic')", html)

    def test_invalid_renderer_or_font_input_preserves_existing_entrypoint(self):
        out = self.root / "out"
        out.mkdir()
        entry = out / "index.html"
        entry.write_text("keep original")
        for kwargs in ({"xite": str(self.root / "missing")}, {"font": str(self.root / "missing.ttf")},
                       {"font_license": str(self.root / "missing-license.txt")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.build(out, **kwargs)
            self.assertEqual(entry.read_text(), "keep original")

    def test_provided_font_and_licence_are_copied_and_attested(self):
        font = self.root / "synthetic.ttf"
        license_file = self.root / "synthetic-license.txt"
        font.write_bytes(b"synthetic font copy contract, not a rendering fixture")
        license_file.write_text("Synthetic licence copy fixture")
        out = self.root / "out"
        self.build(out, font=str(font), font_license=str(license_file))
        receipt = json.loads((out / "lab-receipt.json").read_text())
        files = {a["path"] for a in receipt["assets"]}
        self.assertIn("fonts/UbuntuMono-R.ttf", files)
        self.assertIn("fonts/LICENSE.txt", files)
        self.assertEqual((out / "fonts/UbuntuMono-R.ttf").read_bytes(), font.read_bytes())
        self.assertEqual((out / "fonts/LICENSE.txt").read_text(), license_file.read_text())


if __name__ == "__main__":
    unittest.main()
