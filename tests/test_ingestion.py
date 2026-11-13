"""Integration fixtures for Git fidelity and sparse analytical populations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from x3dray import gitio, harvest, profile


class GitFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xray-ingest-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.identity("Pat")
        self.book = harvest.IdentityBook({"identities": [{"label": "Pat", "class": "you", "match_names": ["Pat"]}]})

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.PIPE, text=True).strip()

    def identity(self, name):
        self.git("config", "user.name", name)
        self.git("config", "user.email", "synthetic@example.invalid")

    def commit(self, files, message="Synthetic commit"):
        for path, text in files.items():
            p = self.repo / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text if isinstance(text, bytes) else text.encode())
        self.git("add", "--all")
        self.git("commit", "-q", "-m", message)

    def record(self, **extra):
        cfg = {"name": "fixture", "dir": "repo", "kind": "own", "visibility": "public", **extra}
        return harvest.harvest_repo(cfg, str(self.root), self.book)

    def test_paths_with_unicode_tabs_newlines_quotes_and_backslashes_join(self):
        paths = ["normal.py", "中文.py", "tab\tname.py", "new\nline.py", 'quote"name.py', "back\\slash.py", "old\x1eseparator.py"]
        self.commit({p: "one\n" for p in paths})
        history = list(gitio.log_with_numstat(str(self.repo), ["--all"]))
        self.assertEqual({p for _, _, p in history[0]["numstat"]}, set(paths))
        tree = {p for _, _, p in gitio.ls_tree(str(self.repo), "HEAD")}
        self.assertEqual(tree, set(paths))
        rec = self.record()
        self.assertEqual({f["path"]: f["churn"] for f in rec["files"]}, dict.fromkeys(paths, 1))
        # Standard Unicode and control paths remain valid serialized JSON strings.
        json.loads(json.dumps(harvest.strip_internal({"repos": [rec]}), ensure_ascii=False))

    def test_metadata_separators_binary_and_empty_numstat_are_preserved(self):
        subject = "Metadata \x1e and \x1f separators"
        message = subject + "\n\nCo-Authored-By: Synthetic <coauthor@example.invalid>\nClaude-Session: https://claude.ai/code/session_synthetic"
        self.commit({"binary.dat": b"\0\x01", "text.py": "x\n"}, message)
        self.git("commit", "--allow-empty", "-q", "-m", "Empty change")
        records = list(gitio.log_with_numstat(str(self.repo), ["--all"]))
        first = next(r for r in records if r["subject"] == subject)
        self.assertEqual(first["coauthors"], ["Synthetic <coauthor@example.invalid>"])
        self.assertEqual(first["sessions"], ["https://claude.ai/code/session_synthetic"])
        self.assertIn((None, None, "binary.dat"), first["numstat"])
        self.assertEqual(next(r for r in records if r["subject"] == "Empty change")["numstat"], [])

    @unittest.skipIf(os.name == "nt", "raw non-UTF-8 filenames require a POSIX filesystem")
    def test_non_utf8_paths_keep_a_reversible_shared_internal_key(self):
        raw = os.fsencode(self.repo) + b"/nonutf8-\xff.py"
        with open(raw, "wb") as stream:
            stream.write(b"x\n")
        self.git("add", "--all")
        self.git("commit", "-q", "-m", "Non UTF-8 fixture")
        history_path = list(gitio.log_with_numstat(str(self.repo), ["--all"]))[0]["numstat"][0][2]
        tree_path = list(gitio.ls_tree(str(self.repo), "HEAD"))[0][2]
        self.assertEqual(history_path, tree_path)
        self.assertEqual(os.fsencode(history_path), b"nonutf8-\xff.py")
        self.assertEqual(self.record()["files"][0]["churn"], 1)

    def test_linked_worktree_is_discovered_as_checkout(self):
        self.commit({"main.py": "x\n"})
        linked = self.root / "linked"
        self.git("worktree", "add", "-q", "-b", "linked", str(linked))
        rec = harvest.harvest_repo({"name": "linked", "dir": "linked", "kind": "own"}, str(self.root), self.book)
        self.assertTrue((linked / ".git").is_file())
        self.assertTrue(rec["present"])
        self.assertEqual(rec["own_commits"], 1)
        self.assertFalse(gitio.is_repository(str(linked / "missing")))
        (linked / "subdir").mkdir()
        self.assertFalse(gitio.is_repository(str(linked / "subdir")))

    def test_fork_fallback_uses_configured_identity_and_measures_touched_tip(self):
        self.identity("Unmapped upstream")
        self.commit({"untouched.py": "upstream\n", "main.py": "before\n"})
        self.identity("Pat")
        self.commit({"main.py": "after\n"})
        rec = self.record(kind="fork-active", upstream={"name": "upstream", "dir": "missing"})
        self.assertEqual(rec["own_commits"], 1)
        self.assertEqual(rec["commits"][0]["who"], "Pat")
        self.assertEqual(rec["fork_touched_paths"], ["main.py"])
        self.assertEqual(rec["tree"]["files"], 1)
        self.assertEqual([f["path"] for f in rec["files"]], ["main.py"])
        p = profile.profile_family({"repos": [rec]}, {"pairs": []})
        self.assertIn("configured identities only", p["per_repo"]["fixture"]["coverage"]["history"])
        self.assertIn("touched files", p["per_repo"]["fixture"]["coverage"]["tree_scope"])

    def test_fork_fallback_honours_hash_rule_and_private_counts(self):
        self.book = harvest.IdentityBook({"identities": [{"label": "Configured", "class": "you",
            "match_email_sha256": [harvest.ident_hash("synthetic@example.invalid")]}]})
        self.commit({"private-note.py": "private fixture\n"})
        rec = self.record(kind="fork-active", visibility="private", upstream={"name": "upstream", "dir": "missing"})
        self.assertEqual(rec["own_commits"], 1)
        self.assertEqual(rec["tree"]["files"], 1)
        self.assertEqual(rec["fork_touched_paths"], 1)
        exported = harvest.strip_internal({"repos": [rec]})
        self.assertNotIn("private-note.py", json.dumps(exported))
        for c in exported["repos"][0]["commits"]:
            self.assertFalse(set(c) & {"sha", "subject", "paths", "_oid"})

    def test_full_oid_is_internal_and_recursive_export_stripping_removes_it(self):
        self.commit({"main.py": "x\n"})
        rec = self.record()
        self.assertEqual(rec["commits"][0]["_oid"], self.git("rev-parse", "HEAD"))
        self.assertEqual(len(rec["commits"][0]["sha"]), 12)
        exported = harvest.strip_internal({"repos": [rec]})
        self.assertNotIn("_oid", json.dumps(exported))
        self.assertIn("sha", exported["repos"][0]["commits"][0])

    def test_sparse_profiles_have_explicit_availability_at_threshold(self):
        empty = profile.profile_family({"repos": []}, {"pairs": []})
        signals = {s["id"]: s["values"] for s in empty["signals"]}
        self.assertEqual(signals["family_span"]["commits"], 0)
        self.assertIsNone(signals["family_span"]["first"])
        self.assertFalse(signals["family_span"]["available"])
        self.assertIsNone(signals["who_commits"]["shares"]["ai"])
        self.assertIsNone(signals["rhythm"]["ai_peak"])
        self.assertIsNone(signals["rhythm"]["you_peak"])
        for count in range(1, 6):
            self.commit({"main.py": str(count) + "\n"}, "Commit " + str(count))
            if count not in {1, 4, 5}:
                continue
            prof = profile.profile_family({"repos": [self.record()]}, {"pairs": []})
            sig = {s["id"]: s["values"] for s in prof["signals"]}
            self.assertEqual(sig["family_span"]["commits"], count)
            self.assertEqual(sig["ai_share_by_repo"]["available"], count >= 5)


if __name__ == "__main__":
    unittest.main()
