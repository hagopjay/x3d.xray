"""The bounded source binding is explicit about visible and withheld evidence."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from x3dray import page
from x3dray.locators import locator_from_blob, normalize_source_locator
from x3dray.witness_contract import evidence_count, project_evidence, scene_sources, source_digest, source_payload


def machine(a="alpha", b="beta", n=6):
    return {"from": a, "to": b, "grade": "C", "grade_reason": "Declared use", "kinds": {"uses": n},
            "evidence": [{"kind": "uses", "path": f"src/{i}.py", "line": i + 1, "text": f"Witness {i}"} for i in range(n)]}


def curated(a="alpha", b="beta"):
    return {"id": "E1", "from": a, "to": b, "grade": "L", "kind": "lineage", "evidence": ["Attributed observation"]}


def locator():
    content = b"print('fixture')\n"
    oid = hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest()
    return locator_from_blob("repo:alpha", "a" * 40, "sha1", oid, "src/a.py", content,
                             line_start=1, line_end=1, detector_id="mention:code")


def make_page_bundle(dataset, ties, cur=None):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        documents = {"dataset.json": dataset, "ties.json": {"pairs": ties},
                     "profile.json": {"per_repo": {r["name"]: {"visibility": r.get("visibility", "public")} for r in dataset["repos"]}, "signals": []},
                     "spec.rendered.json": {}, "self.json": {"modules": [], "tool": {"git": {}}},
                     "curated.json": cur or {}, "layout.json": {}}
        for name, data in documents.items():
            (root / name).write_text(json.dumps(data), encoding="utf-8")
        return page.make_bundle(str(root), str(root))


class BoundedWitnessTests(unittest.TestCase):
    def test_public_projection_takes_first_four_substantive_witnesses(self):
        row = machine()
        row["evidence"][:0] = [None, "machine ignores prose", {}, {"text": "  "}]
        row["evidence"][4].update(text="汉" * 200, path="p" * 200, extra="not projected")
        sample = project_evidence(row, "machine")
        self.assertEqual(len(sample), 4)
        self.assertEqual(sample[0]["text"], "汉" * 160)
        self.assertEqual(sample[0]["path"], "p" * 160)
        self.assertEqual([e["line"] for e in sample], [1, 2, 3, 4])
        self.assertNotIn("extra", sample[0])

    def test_curated_prose_is_bounded_and_empty_records_do_not_take_slots(self):
        row = {"evidence": ["", "  ", {}, "a" * 200, {"text": "source", "other_path": "b" * 200}, "third", "fourth", "fifth"]}
        self.assertEqual(project_evidence(row, "curated"), ["a" * 160, {"text": "source", "other_path": "b" * 160}, "third", "fourth"])

    def test_locator_fields_are_normalized_before_digesting(self):
        raw = locator()
        raw["revision"]["oid"] = raw["revision"]["oid"].upper()
        raw["blob"]["oid"] = raw["blob"]["oid"].upper()
        raw["unapproved"] = "ignored extension"
        invalid = {**raw, "visibility": "private"}
        row = machine(n=1)
        row["evidence"][0].update(locator=raw, locators=[invalid, raw])
        projected = project_evidence(row, "machine")
        expected = normalize_source_locator(raw)
        self.assertIsNotNone(expected)
        self.assertEqual(projected[0]["locator"], expected)
        self.assertEqual(projected[0]["locators"], [expected])
        canonical = copy.deepcopy(row)
        canonical["evidence"] = projected
        self.assertEqual(source_digest(row, "machine"), source_digest(canonical, "machine"))
        self.assertEqual(raw["unapproved"], "ignored extension")

    def test_invalid_locator_fields_are_omitted(self):
        row = machine(n=1)
        row["evidence"][0].update(locator={"kind": "untrusted"}, locators=[None, {"visibility": "private"}])
        self.assertNotIn("locator", project_evidence(row, "machine")[0])
        self.assertNotIn("locators", project_evidence(row, "machine")[0])

    def test_digest_is_canonical_ascii_sha256_of_the_versioned_payload(self):
        row = machine(n=1)
        row["evidence"][0]["text"] = "汉字"
        expected = {"schema_version": 1, "source": "machine", "from": "alpha", "to": "beta", "grade": "C",
                    "kinds": {"uses": 1}, "visibility": "public", "evidence_count": 1, "evidence": row["evidence"]}
        self.assertEqual(source_payload(row, "machine"), expected)
        encoded = json.dumps(expected, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
        self.assertEqual(source_digest(row, "machine"), hashlib.sha256(encoded).hexdigest())

    def test_count_total_is_never_below_raw_input_and_rejects_nonintegers(self):
        for declared, expected in [(0, 6), (2, 6), (9, 9), (-1, 6), (True, 6), (1.5, 6), ("9", 6), (None, 6)]:
            with self.subTest(declared=declared):
                row = machine()
                row["evidence_records"] = {"total": declared}
                self.assertEqual(evidence_count(row), expected)
                for private in ((), ("beta",)):
                    self.assertEqual(source_payload(row, "machine", private)["evidence_count"], expected)
        self.assertEqual(evidence_count({"evidence": None, "evidence_records": []}), 0)

    def test_public_digest_binds_visible_sample_and_total_not_hidden_fifth_text(self):
        row = machine()
        baseline = source_digest(row, "machine")
        row["evidence"][5]["text"] = "Outside the bounded sample"
        self.assertEqual(source_digest(row, "machine"), baseline)
        row["evidence"][0]["text"] = "Changed visible observation"
        self.assertNotEqual(source_digest(row, "machine"), baseline)
        row = machine()
        row["evidence_records"] = {"total": 20}
        self.assertNotEqual(source_digest(row, "machine"), baseline)

    def test_projection_is_idempotent_when_page_preserves_total(self):
        for source, row in (("machine", machine()), ("curated", {**curated(), "evidence": [f"note {i}" for i in range(7)]})):
            original = copy.deepcopy(row)
            reduced = {**row, "evidence": project_evidence(row, source), "evidence_records": {"total": evidence_count(row)}}
            self.assertEqual(source_digest(row, source), source_digest(reduced, source))
            self.assertEqual(project_evidence(reduced, source), reduced["evidence"])
            self.assertEqual(row, original)

    def test_either_private_endpoint_binds_counts_without_hidden_content(self):
        for source in ("machine", "curated"):
            for endpoints in (("private", "beta"), ("alpha", "private")):
                with self.subTest(source=source, endpoints=endpoints):
                    row = machine(*endpoints) if source == "machine" else curated(*endpoints)
                    row["evidence_records"] = {"total": 10}
                    before = source_digest(row, source, {"private"})
                    payload = source_payload(row, source, {"private"})
                    self.assertEqual(payload["visibility"], "aggregate_only")
                    self.assertEqual(payload["evidence"], [{"count": 10}])
                    row["evidence"] = [{"text": "hidden source text", "path": "hidden.py", "locator": locator()}]
                    self.assertEqual(source_digest(row, source, {"private"}), before)
                    self.assertNotIn("hidden", json.dumps(source_payload(row, source, {"private"})))
                    row["evidence_records"]["total"] = 11
                    self.assertNotEqual(source_digest(row, source, {"private"}), before)

    def test_private_freeform_kinds_do_not_become_a_hidden_hash_channel(self):
        row = machine("alpha", "private", 1)
        row["kinds"] = {"uses": 2, "private:secret.py": 3}
        before = source_digest(row, "machine", {"private"})
        self.assertEqual(source_payload(row, "machine", {"private"})["kinds"], {"uses": 2, "other": 3})
        row["kinds"] = {"uses": 2, "different-private-prose": 3}
        self.assertEqual(source_digest(row, "machine", {"private"}), before)
        row = curated("private", "beta")
        before = source_digest(row, "curated", {"private"})
        row["kind"] = "private free prose changed"
        self.assertEqual(source_digest(row, "curated", {"private"}), before)

    def test_complete_source_ledger_keeps_ineligible_inputs_for_omission_checks(self):
        ds = {"repos": [{"name": "alpha"}, {"name": "beta"}]}
        pairs = [machine(), {**machine("beta", "outside"), "evidence": []}, machine("alpha", "alpha")]
        cur = {"ties": [{**curated(), "evidence": []}, {**curated("beta", "alpha"), "id": "E2", "grade": "?"}]}
        ledger = scene_sources(ds, {"pairs": pairs}, cur)
        self.assertEqual(len(ledger["machine"]), len(pairs))
        self.assertEqual(len(ledger["curated"]), len(cur["ties"]))
        self.assertEqual(ledger["machine"][1]["evidence"], [])
        self.assertEqual(ledger["curated"][0]["evidence"], [])
        self.assertEqual(ledger["curated"][1]["grade"], "?")
        for source, rows in (("machine", pairs), ("curated", cur["ties"])):
            for raw, displayed in zip(rows, ledger[source]):
                self.assertEqual(source_digest(raw, source), source_digest(displayed, source))

    def test_private_ledger_projection_is_idempotent_and_hides_all_source_detail(self):
        ds = {"repos": [{"name": "alpha"}, {"name": "private", "visibility": "private"}]}
        pairs = [machine("alpha", "private"), machine("private", "alpha")]
        cur = {"ties": [curated("alpha", "private"), {**curated("private", "alpha"), "id": "E2"}]}
        ledger = scene_sources(ds, {"pairs": pairs}, cur)
        for source, rows in (("machine", pairs), ("curated", cur["ties"])):
            for raw, displayed in zip(rows, ledger[source]):
                self.assertEqual(source_digest(raw, source, {"private"}), source_digest(displayed, source, {"private"}))
        repeated = scene_sources(ds, {"pairs": ledger["machine"]}, {"ties": ledger["curated"]})
        self.assertEqual(ledger, repeated)
        self.assertNotIn("Witness", json.dumps(ledger))
        self.assertNotIn("Attributed", json.dumps(ledger))

    def test_page_public_legacy_projection_preserves_digest_after_truncation(self):
        ds = {"family": "fixture", "repos": [{"name": "alpha"}, {"name": "beta"}]}
        row = machine()
        row["evidence_records"] = {"total": 1}
        bundle = make_page_bundle(ds, [row])
        displayed = bundle["ties"]["machine"][0]
        self.assertEqual(len(displayed["evidence"]), 4)
        self.assertEqual(displayed["evidence_records"]["total"], 6)
        self.assertEqual(source_digest(row, "machine"), source_digest(displayed, "machine"))
        self.assertEqual(source_digest(row, "machine"), source_digest(bundle["scene_sources"]["machine"][0], "machine"))

    def test_page_private_legacy_exports_aggregate_placeholders_in_both_directions(self):
        ds = {"family": "fixture", "repos": [{"name": "alpha"}, {"name": "private", "visibility": "private"}]}
        pairs = [machine("alpha", "private", 1), machine("private", "alpha", 1)]
        for row in pairs:
            row.update(grade_reason="sensitive reason", pin_targets={"sensitive-target": 1})
            row["evidence"][0].update(path="sensitive-private.py", text="sensitive witness")
            row["kinds"] = {"uses": 1, "sensitive-detector": 2}
        cur = {"ties": [curated("alpha", "private"), {**curated("private", "alpha"), "id": "E2"}]}
        for row in cur["ties"]:
            row.update(kind="sensitive kind", what="sensitive interpretation", extra="sensitive extra", evidence=["sensitive source"])
        bundle = make_page_bundle(ds, pairs, cur)
        for row in bundle["ties"]["machine"]:
            self.assertEqual(row["evidence"], [{"path": "[private repo]"}])
            self.assertEqual(row["kinds"], {"uses": 1, "other": 2})
            self.assertIsNone(row["pin_targets"])
        for row in bundle["ties"]["curated"]:
            self.assertEqual(row["evidence"], ["[private repo]"])
            self.assertEqual(row["kind"], "aggregate")
            self.assertNotIn("extra", row)
        self.assertNotIn("sensitive", json.dumps(bundle["ties"]))
        self.assertNotIn("sensitive", json.dumps(bundle["scene_sources"]))


if __name__ == "__main__":
    unittest.main()
