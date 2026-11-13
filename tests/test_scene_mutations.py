"""Adversarial integration over real synthetic Git and emitted scene bytes."""
from functools import lru_cache
import hashlib
import json
import unittest

from experiments.scene_mutation_audit import build_fixture, run_audit
from x3dray.scene_mutations import audit_cases, mutate_cases


@lru_cache(maxsize=1)
def report():
    return run_audit()


class SceneMutationIntegrationTests(unittest.TestCase):
    def test_real_git_generic_family_compiles_without_phantom_nodes(self):
        r = report()
        self.assertTrue(r["baseline"]["ok"], r["baseline"])
        self.assertEqual(r["fixture"]["repos"], ["alpha", "beta"])
        self.assertEqual(r["fixture"]["commits"], 16)
        self.assertEqual(r["fixture"]["planned_entities"], 2)
        self.assertGreaterEqual(r["fixture"]["planned_relations"], 2)

    def test_all_claimed_xml_mutations_are_rejected(self):
        r = report()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["rejections_tested"], 10)
        self.assertEqual(r["rejections_observed"], 10)
        for row in r["mutations"]:
            if row["expected"] == "reject":
                with self.subTest(mutation=row["id"]):
                    self.assertFalse(row["accepted"])
                    self.assertTrue(row["finding_codes"])
                    self.assertTrue(row["expectation_met"])

    def test_equivalent_world_geometry_is_not_rejected_for_xml_difference(self):
        r = report()
        row = next(x for x in r["mutations"] if x["id"] == "equivalent_nested_transforms")
        self.assertTrue(row["accepted"], row)
        self.assertNotEqual(row["scene_sha256"], r["fixture"]["scene_sha256"])
        self.assertEqual(row["finding_codes"], [])

    def test_plan_and_bundle_are_unchanged_during_mutations(self):
        self.assertTrue(report()["source_inputs_unchanged"])
        self.assertEqual(len(report()["fixture"]["plan_sha256"]), 64)
        self.assertEqual(len(report()["fixture"]["bundle_sha256"]), 64)

    def test_scope_probe_is_not_counted_as_a_promised_rejection(self):
        r = report()
        probes = [row for row in r["mutations"] if row["expected"] == "scope_probe"]
        self.assertEqual([p["id"] for p in probes], ["unlabelled_outside_layer"])
        self.assertTrue(all(row["expectation_met"] is None for row in probes))
        self.assertIn("static", r["scope"])
        self.assertIn("GPU", r["scope"])

    def test_generic_case_api_preserves_expected_original_and_input_identity(self):
        xml, plan, bundle = build_fixture()
        before = json.dumps([plan, bundle], sort_keys=True)
        cases = mutate_cases(xml)
        self.assertEqual(cases[0]["id"], "baseline")
        self.assertEqual(cases[0]["xml"], xml)
        self.assertTrue(cases[0]["expected_ok"])
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        actual = audit_cases(xml, plan, bundle)
        self.assertEqual([c["id"] for c in actual], [c["id"] for c in cases])
        for case in actual:
            if case["expected_ok"] is not None:
                with self.subTest(case=case["id"]):
                    self.assertEqual(case["verification"]["ok"], case["expected_ok"])
        self.assertEqual(json.dumps([plan, bundle], sort_keys=True), before)

    def test_actual_scene_mutations_change_bytes_not_only_report_labels(self):
        xml, _, _ = build_fixture()
        baseline = hashlib.sha256(xml.encode()).hexdigest()
        for case in mutate_cases(xml)[1:]:
            with self.subTest(case=case["id"]):
                self.assertNotEqual(hashlib.sha256(case["xml"].encode()).hexdigest(), baseline)


if __name__ == "__main__":
    unittest.main()
