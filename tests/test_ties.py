"""Source-witness regressions: sampling, pin identity and curated exclusion."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from x3dray import curated, gitio, harvest, locators, ties


class TieWitnessTests(unittest.TestCase):
    def detected(self, blobs, commits=(), other_repos=(), source_commits=()):
        cfg = {"owner": "fixture", "repos": [{"name": "a"}, {"name": "b", "aliases": ["targetlib"]}]}
        cfg["repos"].extend({"name": r["name"]} for r in other_repos)
        dataset = {"repos": [{"name": "a", "_path": "fixture", "commits": list(source_commits),
                              "_blobs": [(key, len(text), path) for key, path, text in blobs]},
                              {"name": "b", "commits": list(commits)}, *other_repos]}
        texts = {key: text.encode() for key, _, text in blobs}
        with patch.object(ties.gitio, "cat_blob", side_effect=lambda _repo, blob: texts[blob]):
            return ties.find_ties(cfg, dataset)

    @staticmethod
    def ab(result):
        return next(p for p in result["pairs"] if (p["from"], p["to"]) == ("a", "b"))

    def test_manifest_grade_and_witness_survive_many_earlier_docs(self):
        for count in (47, 48, 200):
            blobs = [(f"doc{i}", f"docs/{i:03}.md", "targetlib") for i in range(count)]
            blobs.append(("manifest", "package.json", '{"dependencies": {"targetlib": "*"}}'))
            result = self.ab(self.detected(blobs))
            self.assertEqual(result["grade"], "C")
            self.assertEqual(result["kinds"]["mention:dependency"], 1)
            self.assertTrue(any(e["path"] == "package.json" for e in result["evidence"]))
            ledger = result["evidence_records"]
            self.assertEqual(ledger["total"], count + 1)
            self.assertEqual(ledger["returned"] + ledger["omitted"], ledger["total"])
            self.assertLessEqual(ledger["returned"], ledger["limit"])

    def test_evidence_order_does_not_change_grade_or_display_sample(self):
        blobs = [(f"doc{i}", f"docs/{i:03}.md", "targetlib") for i in range(60)]
        blobs += [("manifest", "package.json", "targetlib"), ("code", "src/a.py", "import targetlib")]
        forward = self.ab(self.detected(blobs))
        reverse = self.ab(self.detected(list(reversed(blobs))))
        self.assertEqual(forward, reverse)

    def test_grade_uses_complete_summary_even_with_no_display_records(self):
        self.assertEqual(ties.machine_grade({"kinds": {"mention:dependency": 1}, "evidence": []})[0], "C")
        self.assertEqual(ties.machine_grade({"kinds": {"mention:docs": 8}, "evidence": []})[0], "L")

    def test_late_import_in_a_file_is_not_hidden_by_hit_sampling(self):
        text = "targetlib is discussed here\n" * 70 + "import targetlib\n"
        result = self.ab(self.detected([("docs", "README.md", text)]))
        self.assertEqual(result["grade"], "C")
        self.assertEqual(result["mention_lines"], {"docs": 70, "dependency": 1})
        witness = next(e for e in result["evidence"] if e["kind"] == "mention:dependency")
        self.assertEqual(witness["line"], 71)

    def test_fabricated_suffix_cannot_be_validated_by_legacy_prefix(self):
        result = self.detected([("doc", "README.md", "pin abcdef123456" + "9" * 28)], [{"sha": "abcdef123456"}])
        self.assertFalse(any(p["kinds"].get("pin") for p in result["pairs"]))
        self.assertEqual(result["pin_rejections"]["unresolved_or_longer_than_recorded_id"], 1)

    def test_full_internal_oid_validates_exact_pin(self):
        oid = "abcdef123456" + "a" * 28
        result = self.ab(self.detected([("doc", "README.md", "pin " + oid)], [{"sha": oid[:12], "_oid": oid}]))
        self.assertEqual(result["grade"], "C")
        self.assertEqual(result["pin_targets"], {oid: 1})

    def test_wrong_full_oid_suffix_is_rejected(self):
        oid = "abcdef123456" + "a" * 28
        result = self.detected([("doc", "README.md", "pin " + oid[:12] + "b" * 28)], [{"sha": oid[:12], "_oid": oid}])
        self.assertFalse(any(p["kinds"].get("pin") for p in result["pairs"]))

    def test_legacy_short_pin_remains_supported_when_unambiguous(self):
        result = self.ab(self.detected([("doc", "README.md", "pin abcdef123456")], [{"sha": "abcdef123456"}]))
        self.assertEqual(result["kinds"]["pin"], 1)

    def test_ambiguous_short_token_is_not_a_pin(self):
        commits = [{"sha": "abcdef123456", "_oid": "abcdef123456" + "a" * 28},
                   {"sha": "abcdef123456", "_oid": "abcdef123456" + "b" * 28}]
        result = self.detected([("doc", "README.md", "pin abcdef123456")], commits)
        self.assertFalse(any(p["kinds"].get("pin") for p in result["pairs"]))
        self.assertEqual(result["pin_rejections"]["ambiguous_abbreviation"], 1)

    def test_same_exact_commit_in_two_repos_is_not_ambiguous(self):
        oid = "abcdef123456" + "a" * 28
        extra = {"name": "fork", "commits": [{"sha": oid[:12], "_oid": oid}]}
        result = self.detected([("doc", "README.md", "pin " + oid)], [{"sha": oid[:12], "_oid": oid}], [extra])
        self.assertEqual({p["to"] for p in result["pairs"] if p["kinds"].get("pin")}, {"b", "fork"})

    def test_ambiguity_with_the_source_repo_is_also_rejected(self):
        result = self.detected([("doc", "README.md", "pin abcdef123456")],
            [{"sha": "abcdef123456", "_oid": "abcdef123456" + "a" * 28}],
            source_commits=[{"sha": "abcdef123456", "_oid": "abcdef123456" + "b" * 28}])
        self.assertFalse(any(p["kinds"].get("pin") for p in result["pairs"]))
        self.assertEqual(result["pin_rejections"]["ambiguous_abbreviation"], 1)

    def test_sha256_oid_and_uppercase_token(self):
        oid = "abcdef123456" + "a" * 52
        result = self.ab(self.detected([("doc", "README.md", "pin " + oid.upper())], [{"sha": oid[:12], "_oid": oid}]))
        self.assertEqual(result["pin_targets"], {oid: 1})

    def test_legacy_witness_has_explicit_unknown_locator_and_observation_time(self):
        result = self.ab(self.detected([("doc", "README.md", "targetlib")]))
        witness = result["evidence"][0]
        self.assertEqual(witness["locator_status"], "unavailable")
        self.assertEqual(witness["locator_unavailable_reason"], "no_public_pinned_revision")
        self.assertIsNone(witness["observed_at"])
        self.assertIsNone(witness["valid_time"])


class ImmutableTieTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = {"family": "fixture", "owner": "fixture", "repos": [
            {"name": "a", "dir": "a", "kind": "own", "aliases": ["sourcelib"]},
            {"name": "b", "dir": "b", "kind": "own", "aliases": ["targetlib"]}]}
        for name in ("a", "b"):
            (self.root / name).mkdir()
            self.git(name, "init", "-q")
            self.git(name, "config", "user.name", "Fixture")
            self.git(name, "config", "user.email", "fixture@example.invalid")

    def git(self, name, *args):
        return subprocess.check_output(["git", "-C", str(self.root / name), *args],
                                       env=gitio.read_environment()).decode().strip()

    def commit(self, name, files, message="fixture"):
        for path, data in files.items():
            dest = self.root / name / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        self.git(name, "add", "--all")
        self.git(name, "commit", "-qm", message)
        return self.git(name, "rev-parse", "HEAD")

    def harvested(self):
        data = harvest.harvest_family(self.cfg, str(self.root))
        data["harvested_at"] = "2026-11-12T11:30:00+01:00"
        return data

    def populate(self):
        shared = b"\x00\xff" + bytes(range(256)) * 5
        target_oid = self.commit("b", {"target.py": b"target fixture\n", "same.bin": shared})
        self.commit("a", {"src/main.py": "α\r\nimport targetlib\r\n".encode() + b"pin " + target_oid.encode() + b"\r\n",
                          "same.bin": shared})

    def test_public_file_and_shared_witnesses_verify_against_full_pinned_revisions(self):
        self.populate()
        dataset = self.harvested()
        result = ties.find_ties(self.cfg, dataset)
        pair = TieWitnessTests.ab(result)
        witnesses = {e["kind"]: e for e in pair["evidence"]}
        self.assertTrue({"mention:dependency", "pin", "shared_file"} <= witnesses.keys())
        by_id = {r["repo_id"]: r for r in dataset["repos"]}
        for evidence in witnesses.values():
            self.assertEqual(evidence["locator_status"], "recorded")
            self.assertEqual(evidence["observed_at"], "2026-11-12T10:30:00Z")
            self.assertIsNone(evidence["valid_time"])
            sources = evidence.get("locators", [evidence.get("locator")])
            for source in sources:
                record = by_id[source["repo_id"]]
                self.assertEqual(source["revision"]["oid"], record["head"])
                self.assertEqual(source["detector"], {"id": evidence["kind"], "version": "1"})
                self.assertTrue(locators.verify_locator(record["_path"], source, repo_id=record["repo_id"])["ok"])
        self.assertEqual(witnesses["mention:dependency"]["locator"]["span"]["start"], 2)
        self.assertEqual(len(witnesses["shared_file"]["locators"]), 2)
        self.assertTrue(all(s["span"]["kind"] == "bytes" for s in witnesses["shared_file"]["locators"]))

    def test_later_head_and_worktree_changes_do_not_retarget_source(self):
        self.populate()
        dataset = self.harvested()
        original = dataset["repos"][0]["head"]
        self.commit("a", {"src/main.py": b"unrelated changed tip\n"}, "new tip")
        result = ties.find_ties(self.cfg, dataset)
        witness = next(e for e in TieWitnessTests.ab(result)["evidence"] if e["kind"] == "mention:dependency")
        self.assertEqual(witness["locator"]["revision"]["oid"], original)
        self.assertTrue(locators.verify_locator(str(self.root / "a"), witness["locator"], repo_id="github:fixture/a")["ok"])

    def test_private_endpoint_withholds_both_shared_content_locators_and_hashes(self):
        self.populate()
        self.cfg["repos"][1]["visibility"] = "private"
        dataset = self.harvested()
        private_oid = self.git("b", "rev-parse", "HEAD")
        shared_oid = self.git("b", "rev-parse", "HEAD:same.bin")
        result = ties.find_ties(self.cfg, dataset)
        for pair in result["pairs"]:
            if "b" not in (pair["from"], pair["to"]):
                continue
            for witness in pair["evidence"]:
                self.assertEqual(witness["locator_status"], "withheld_private")
                self.assertNotIn("locator", witness)
                self.assertNotIn("locators", witness)
        self.assertNotIn(shared_oid, json.dumps(result))
        exported = harvest.strip_internal(dataset)
        self.assertNotIn(private_oid, json.dumps(exported))
        self.assertNotIn("repo_id", exported["repos"][1])
        self.assertNotIn("oid", json.dumps(exported["repos"][1]["commits"]))

    def test_configuration_assertions_have_no_invented_git_source_or_lifetime(self):
        self.populate()
        self.cfg["repos"][0].update(observer=True, uses={"b": "configured declaration"})
        self.cfg.update(lineage=["a", "b"], lineage_source="owner statement")
        dataset = self.harvested()
        result = ties.find_ties(self.cfg, dataset)
        for witness in TieWitnessTests.ab(result)["evidence"]:
            if witness["kind"] in {"uses", "lineage"}:
                self.assertEqual(witness["locator_status"], "unavailable")
                self.assertEqual(witness["locator_unavailable_reason"], "non_file_provenance")
                self.assertNotIn("locator", witness)
                self.assertIsNone(witness["valid_time"])
                self.assertEqual(witness["observed_at"], "2026-11-12T10:30:00Z")

    def test_invalid_utf8_source_uses_exact_raw_line_bytes(self):
        self.commit("b", {"a.py": b"target\n"})
        self.commit("a", {"src/main.py": b"\xff\nimport targetlib\n"})
        dataset = self.harvested()
        witness = TieWitnessTests.ab(ties.find_ties(self.cfg, dataset))["evidence"][0]
        self.assertEqual(witness["line"], 2)
        self.assertEqual(witness["locator"]["span"]["kind"], "bytes")
        self.assertEqual(witness["locator"]["span"]["byte_start"], 2)
        self.assertTrue(locators.verify_locator(str(self.root / "a"), witness["locator"], repo_id="github:fixture/a")["ok"])


class CuratedExclusionTests(unittest.TestCase):
    def test_exclusion_covers_nodes_edges_and_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            (data / "ecosystem.json").write_text(json.dumps({"nodes": [{"id": "private"}, {"id": "public"}],
                "edges": [{"id": "E1", "from": "private", "to": "public"}]}))
            (data / "series.json").write_text(json.dumps({"series": [{"id": "S1", "commits": [
                {"sha": "abcdef123456", "date": "2026-01-01"}]}]}))
            result = curated.import_masterclass(tmp, exclude=("private",), series_repo="private")
            self.assertEqual(result["series"], [])
            self.assertEqual(result["ties"], [])
            self.assertEqual([n["id"] for n in result["nodes"]], ["public"])
            self.assertFalse(any(s["file"] == "data/series.json" for s in result["source"]["files"]))

    def test_nonexcluded_series_still_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            (data / "ecosystem.json").write_text('{"nodes": [], "edges": []}')
            (data / "series.json").write_text(json.dumps({"series": [{"id": "S1", "commits": [
                {"sha": "abcdef123456", "date": "2026-01-01"}]}]}))
            result = curated.import_masterclass(tmp, exclude=("other",), series_repo="public")
            self.assertEqual(result["series"][0]["repo"], "public")
            self.assertEqual(result["series"][0]["shas"], ["abcdef1"])


if __name__ == "__main__":
    unittest.main()
