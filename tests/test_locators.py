"""Real-Git immutable witnesses: byte identity, pinned trees and safe failure."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from x3dray import gitio, harvest, locators


class LocatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")

    def git(self, *args, repo=None, data=None):
        env = gitio.read_environment()
        env.update(GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")
        result = subprocess.run(["git", "-C", str(repo or self.repo), *args], input=data,
                                capture_output=True, env=env, check=True)
        return result.stdout.decode("utf-8").strip()

    def commit(self, files, message="fixture", repo=None):
        root = repo or self.repo
        for path, content in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.git("add", "--all", repo=root)
        self.git("commit", "-qm", message, repo=root)
        return self.git("rev-parse", "HEAD", repo=root)

    def locator(self, oid, path="src/a.py", **kwargs):
        return locators.make_locator(str(self.repo), "repo:a", oid, path,
                                     detector_id="mention:code", **kwargs)

    def verify(self, value):
        return locators.verify_locator(str(self.repo), value, repo_id="repo:a")

    def test_utf8_crlf_span_hashes_exact_bytes_and_all_git_objects(self):
        content = "α\r\nimport targetlib\r\nlast\n".encode()
        oid = self.commit({"src/a.py": content})
        value = self.locator(oid, line_start=2, line_end=2)
        self.assertEqual(value["revision"], {"oid": oid, "object_format": "sha1"})
        self.assertEqual(value["blob"]["oid"], self.git("rev-parse", oid + ":src/a.py"))
        self.assertEqual(value["blob"]["content_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual((value["span"]["byte_start"], value["span"]["byte_end"]), (4, 22))
        self.assertEqual(value["span"]["content_sha256"], hashlib.sha256(b"import targetlib\r\n").hexdigest())
        result = self.verify(value)
        self.assertTrue(result["ok"])
        self.assertIn("commit_and_tree_object_hashes", result["checks"])
        self.assertIn("no remote authentication or detector truth claim", result["scope"])

    def test_snapshot_survives_head_worktree_and_branch_changes(self):
        oid = self.commit({"src/a.py": b"original\n"})
        value = self.locator(oid, line_start=1)
        self.commit({"src/a.py": b"new tip\n"}, "changed")
        (self.repo / "src/a.py").write_bytes(b"uncommitted\n")
        self.assertTrue(self.verify(value)["ok"])
        branch = self.git("symbolic-ref", "HEAD")
        self.git("update-ref", "-d", branch)
        self.assertTrue(self.verify(value)["ok"])

    def test_literal_paths_unicode_controls_and_shell_metacharacters(self):
        paths = ["dir/α file.py", "dir/tab\tline\nname.py", ":(glob)magic.py",
                 "dir/back\\slash.py", "$(touch marker)`touch marker`.py", "-option.py"]
        oid = self.commit({p: b"content\n" for p in paths})
        for path in paths:
            with self.subTest(path=path):
                value = self.locator(oid, path, line_start=1)
                self.assertEqual(value["path"], path)
                self.assertTrue(self.verify(value)["ok"])
        self.assertFalse((self.repo / "marker").exists())

    def test_binary_invalid_utf8_and_empty_blobs_have_exact_byte_scopes(self):
        oid = self.commit({"binary.dat": b"\x00\xffabc\n", "bad.txt": b"\xfftarget\n", "empty": b""})
        for path, start, end in (("binary.dat", 1, 5), ("bad.txt", 0, 8), ("empty", 0, 0)):
            with self.subTest(path=path):
                value = self.locator(oid, path, byte_start=start, byte_end=end)
                self.assertEqual(value["span"]["encoding"], "bytes")
                self.assertTrue(self.verify(value)["ok"])
        for path in ("binary.dat", "bad.txt", "empty"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.locator(oid, path, line_start=1)

    def test_executable_and_symlink_are_blob_modes_not_followed_worktree_paths(self):
        target = self.repo / "script.sh"
        target.write_bytes(b"echo fixture\n")
        target.chmod(0o755)
        (self.repo / "link").symlink_to("outside-secret")
        self.git("add", "--all")
        self.git("commit", "-qm", "modes")
        oid = self.git("rev-parse", "HEAD")
        executable = self.locator(oid, "script.sh")
        link = self.locator(oid, "link")
        self.assertEqual(executable["blob"]["mode"], "100755")
        self.assertEqual(link["blob"]["mode"], "120000")
        self.assertEqual(link["blob"]["size_bytes"], len(b"outside-secret"))
        self.assertTrue(self.verify(link)["ok"])

    def test_full_revision_type_and_span_bounds_are_required(self):
        oid = self.commit({"src/a.py": b"one\ntwo\n"})
        blob = self.git("rev-parse", oid + ":src/a.py")
        for revision in ("HEAD", oid[:12], blob):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                self.locator(revision)
        for kwargs in ({"line_start": 0}, {"line_start": 3}, {"line_start": 2, "line_end": 1},
                       {"byte_start": -1}, {"byte_end": 9}, {"byte_start": True},
                       {"line_start": 1, "byte_start": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.locator(oid, **kwargs)

    def test_changed_oid_path_size_mode_content_or_span_cannot_verify(self):
        oid = self.commit({"src/a.py": b"one\ntwo\n", "other.py": b"other\n"})
        value = self.locator(oid, line_start=2)
        newer = self.commit({"src/a.py": b"changed\n"}, "new")
        edits = [("revision", "oid", newer), ("blob", "oid", "0" * 40),
                 ("blob", "content_sha256", "0" * 64), ("blob", "size_bytes", 7),
                 ("blob", "mode", "100755"), ("span", "start", 1),
                 ("span", "byte_start", 0), ("span", "content_sha256", "0" * 64)]
        for section, key, replacement in edits:
            changed = deepcopy(value)
            changed[section][key] = replacement
            with self.subTest(section=section, key=key):
                self.assertFalse(self.verify(changed)["ok"])
        changed = deepcopy(value)
        changed["path"] = "other.py"
        self.assertFalse(self.verify(changed)["ok"])

    def test_identity_binding_is_explicit_and_no_error_echoes_source(self):
        oid = self.commit({"src/a.py": b"private-canary\n"})
        value = self.locator(oid)
        for expected in (None, "repo:elsewhere"):
            result = locators.verify_locator(str(self.repo), value, repo_id=expected)
            self.assertEqual(result["findings"], ["repository_identity_unverified"])
        changed = deepcopy(value)
        changed["path"] = "unknown-source-path"
        result = self.verify(changed)
        encoded = json.dumps(result)
        for secret in ("private-canary", "unknown-source-path", oid, str(self.repo)):
            self.assertNotIn(secret, encoded)

    def test_private_unsupported_and_extra_fields_fail_closed_or_are_whitelisted(self):
        oid = self.commit({"src/a.py": b"one\n"})
        value = self.locator(oid)
        for key, replacement in (("visibility", "private"), ("schema_version", True),
                                 ("path", "../a.py"), ("path", "a\udcff.py"),
                                 ("repo_id", "repo:\ud800"),
                                 ("repo_id", "repo:canary@example.invalid")):
            changed = deepcopy(value)
            changed[key] = replacement
            self.assertIsNone(locators.normalize_source_locator(changed))
        changed = deepcopy(value)
        changed["secret"] = "untrusted payload"
        changed["blob"]["extra"] = "untrusted payload"
        self.assertEqual(locators.normalize_source_locator(changed), value)
        for field in ("id", "version"):
            changed = deepcopy(value)
            changed["detector"][field] = "label\ud800"
            self.assertIsNone(locators.normalize_source_locator(changed))
        with self.assertRaises(ValueError):
            locators.locator_from_blob("repo:a", oid, "sha1", value["blob"]["oid"], "src/a.py", b"one\n",
                                       detector_id="test", visibility="private")

    def test_source_bounds_reject_large_objects_before_blob_read(self):
        oid = self.commit({"src/a.py": b"0123456789"})
        with patch.object(locators, "MAX_BYTES", 4), self.assertRaises(ValueError):
            self.locator(oid)

    def test_missing_local_objects_and_unsupported_git_capability_are_unavailable(self):
        oid = self.commit({"src/a.py": b"one\n"})
        value = self.locator(oid)
        with patch.object(gitio, "local_git_bytes", side_effect=RuntimeError("secret-error-canary")):
            result = self.verify(value)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["ok"])
        self.assertNotIn("secret-error-canary", json.dumps(result))
        blob = value["blob"]["oid"]
        obj = self.repo / ".git" / "objects" / blob[:2] / blob[2:]
        obj.unlink()
        self.assertEqual(self.verify(value)["status"], "unavailable")

    def test_detector_label_is_provenance_and_not_git_authenticated_semantics(self):
        oid = self.commit({"src/a.py": b"one\n"})
        value = self.locator(oid)
        value["detector"] = {"id": "different-detector", "version": "2"}
        result = self.verify(value)
        self.assertTrue(result["ok"])
        self.assertIn("no remote authentication or detector truth claim", result["scope"])

    def test_replace_refs_and_ambient_git_dir_do_not_redirect_verification(self):
        oid = self.commit({"src/a.py": b"original\n"})
        value = self.locator(oid)
        newer = self.commit({"src/a.py": b"replacement\n"}, "changed")
        self.git("replace", oid, newer)
        with patch.dict(os.environ, {"GIT_DIR": "/absent-canary", "GIT_WORK_TREE": "/absent-canary"}):
            self.assertTrue(self.verify(value)["ok"])

    def test_rehashed_commit_tree_and_blob_detect_corrupt_local_objects(self):
        oid = self.commit({"src/a.py": b"original\n"})
        value = self.locator(oid)
        tree = self.git("rev-parse", oid + "^{tree}")
        blob = value["blob"]["oid"]
        for object_oid in (oid, tree, blob):
            obj = self.repo / ".git" / "objects" / object_oid[:2] / object_oid[2:]
            saved = obj.read_bytes()
            obj.chmod(0o600)
            raw = zlib.decompress(saved)
            # Keep the object header/size valid while changing its bytes. Git
            # can inflate such an object; the verifier must rehash the chain.
            modified = raw[:-1] + bytes([raw[-1] ^ 1])
            obj.write_bytes(zlib.compress(modified))
            try:
                self.assertFalse(self.verify(value)["ok"], object_oid)
            finally:
                obj.write_bytes(saved)
        self.assertTrue(self.verify(value)["ok"])

    def test_local_read_disables_network_fetch_and_replacements_without_shell(self):
        completed = subprocess.CompletedProcess([], 0, b"value", b"")
        with patch.object(gitio.subprocess, "run", return_value=completed) as run:
            self.assertEqual(gitio.local_git_bytes("/literal checkout", "cat-file", "blob", "a" * 40), b"value")
        args, kwargs = run.call_args
        self.assertEqual(args[0][:5], ["git", "--no-lazy-fetch", "--no-replace-objects", "--literal-pathspecs", "-C"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(kwargs["env"]["GIT_NO_REPLACE_OBJECTS"], "1")

    def test_sha256_git_format_retains_independent_content_hash(self):
        other = Path(self.tmp.name) / "sha256"
        other.mkdir()
        try:
            self.git("init", "-q", "--object-format=sha256", repo=other)
        except subprocess.CalledProcessError:
            self.skipTest("Git does not support SHA-256 repositories")
        self.git("config", "user.name", "Fixture", repo=other)
        self.git("config", "user.email", "fixture@example.invalid", repo=other)
        oid = self.commit({"a.txt": b"same content\n"}, repo=other)
        value = locators.make_locator(str(other), "repo:sha256", oid, "a.txt", detector_id="test")
        self.assertEqual(value["blob"]["object_format"], "sha256")
        self.assertEqual(len(value["blob"]["oid"]), 64)
        self.assertNotEqual(value["blob"]["oid"], value["blob"]["content_sha256"])
        self.assertTrue(locators.verify_locator(str(other), value, repo_id="repo:sha256")["ok"])

    def test_public_export_retains_immutable_ids_private_export_does_not(self):
        oid = self.commit({"src/a.py": b"one\n"})
        cfg = {"name": "a", "dir": "repo", "kind": "own"}
        book = harvest.IdentityBook({})
        public = harvest.strip_internal(harvest.harvest_repo(cfg, self.tmp.name, book))
        self.assertEqual(public["head"], oid)
        self.assertEqual(public["commits"][0]["oid"], oid)
        self.assertEqual(public["repo_id"], "repo:a")
        self.assertEqual(public["object_format"], "sha1")
        private = harvest.strip_internal(harvest.harvest_repo({**cfg, "visibility": "private"}, self.tmp.name, book))
        for field in ("repo_id", "object_format", "files"):
            self.assertNotIn(field, private)
        for secret in (oid, "src/a.py", "_blob_modes"):
            self.assertNotIn(secret, json.dumps(private))


if __name__ == "__main__":
    unittest.main()
