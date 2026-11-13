"""Independent real-Git source-locator attacks and byte-level oracle checks.

All repositories, helper processes and canaries are temporary synthetic data.
The partial-clone test positively proves its helper would run without the
offline boundary, then verifies the locator reader never invokes that helper.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from x3dray import locators

_DEFAULT_LOCATOR = object()


def clean_environment():
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(repo, *args, data=None, check=True, env=None):
    result = subprocess.run(["git", "-C", str(repo), "-c", "user.name=Locator fixture",
                             "-c", "user.email=locator-fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
                            input=data, capture_output=True, env=env or clean_environment(), timeout=10)
    if check and result.returncode:
        raise AssertionError("Synthetic Git fixture command failed: " + args[0])
    return result


@unittest.skipUnless(shutil.which("git"), "Git required")
class LocatorAdversarialTests(unittest.TestCase):
    def setUp(self):
        capability = subprocess.run(["git", "--no-lazy-fetch", "--version"], capture_output=True,
                                    env=clean_environment(), timeout=10)
        if capability.returncode:
            self.skipTest("Git lacks the explicit no-lazy-fetch capability required by local verification")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "checkout"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        self.content = b"first\r\n\xce\xb2eta\r\nlast"
        self.files = {"src/text.txt": self.content, "binary.bin": b"\x00\xffA\n\x80", "empty.txt": b"",
                      "bad-utf8.txt": b"bad\xff\n", "executable.sh": b"echo synthetic\n"}
        for name in ["中文.py", "quote\"and'apostrophe.py", "tab\tname.py", "line\nname.py", "back\\slash.py",
                     ":(literal)marker.py", "e\u0301.py", "é.py"]:
            self.files[name] = ("source for " + name + "\n").encode()
        for name, content in self.files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.repo / "executable.sh").chmod(0o755)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "Synthetic immutable source")
        self.rev = git(self.repo, "rev-parse", "HEAD").stdout.decode().strip()
        self.locator = self.make("src/text.txt", line_start=1)

    def make(self, path, **span):
        return locators.make_locator(str(self.repo), "repo:fixture", self.rev, path,
                                     detector_id="adversarial:fixture", **span)

    def verify(self, locator=_DEFAULT_LOCATOR, repo=None, repo_id="repo:fixture"):
        return locators.verify_locator(str(repo or self.repo), self.locator if locator is _DEFAULT_LOCATOR else locator, repo_id=repo_id)

    def commit_tree(self, entries):
        raw = b"".join(mode.encode() + b" " + name + b"\0" + bytes.fromhex(oid) for mode, name, oid in entries)
        tree = git(self.repo, "hash-object", "-w", "--literally", "-t", "tree", "--stdin", data=raw).stdout.decode().strip()
        raw_commit = (f"tree {tree}\nauthor Fixture <locator-fixture@example.invalid> 1 +0000\n"
                      "committer Fixture <locator-fixture@example.invalid> 1 +0000\n\nSynthetic tree\n").encode()
        return git(self.repo, "hash-object", "-w", "-t", "commit", "--stdin", data=raw_commit).stdout.decode().strip()

    def test_crlf_utf8_line_offsets_and_hashes_match_raw_bytes(self):
        value = self.make("src/text.txt", line_start=2, line_end=2)
        expected = b"\xce\xb2eta\r\n"
        self.assertEqual(value["span"]["byte_start"], len(b"first\r\n"))
        self.assertEqual(value["span"]["byte_end"], len(b"first\r\n") + len(expected))
        self.assertEqual(value["span"]["content_sha256"], hashlib.sha256(expected).hexdigest())
        self.assertEqual(value["blob"]["content_sha256"], hashlib.sha256(self.content).hexdigest())
        object_bytes = b"blob " + str(len(self.content)).encode() + b"\0" + self.content
        self.assertEqual(value["blob"]["oid"], hashlib.sha1(object_bytes).hexdigest())
        self.assertTrue(self.verify(value)["ok"])

    def test_binary_empty_and_mid_codepoint_byte_spans_are_exact(self):
        for path, start, end, expected in [("binary.bin", 1, 4, b"\xffA\n"), ("empty.txt", 0, 0, b""),
                                           ("src/text.txt", 7, 8, b"\xce")]:
            with self.subTest(path=path, start=start):
                value = self.make(path, byte_start=start, byte_end=end)
                self.assertEqual(value["span"]["content_sha256"], hashlib.sha256(expected).hexdigest())
                self.assertEqual((value["span"]["start"], value["span"]["end"]), (start, end))
                self.assertTrue(self.verify(value)["ok"])
        for path in ["binary.bin", "bad-utf8.txt", "empty.txt"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.make(path, line_start=1)

    def test_unicode_quotes_controls_and_pathspec_shaped_names_are_literal(self):
        for path in [p for p in self.files if p.endswith(".py")]:
            with self.subTest(path=path):
                value = self.make(path, byte_start=0, byte_end=len(self.files[path]))
                self.assertEqual(value["path"], path)
                self.assertEqual(value["blob"]["content_sha256"], hashlib.sha256(self.files[path]).hexdigest())
                self.assertTrue(self.verify(value)["ok"])
        self.assertNotEqual(self.make("e\u0301.py")["blob"]["oid"], self.make("é.py")["blob"]["oid"])

    def test_working_tree_edits_do_not_change_immutable_source(self):
        (self.repo / "src/text.txt").write_text("uncommitted replacement")
        self.assertTrue(self.verify()["ok"])

    def test_file_mode_is_part_of_the_verified_binding(self):
        value = self.make("executable.sh")
        self.assertEqual(value["blob"]["mode"], "100755")
        self.assertTrue(self.verify(value)["ok"])
        bad = copy.deepcopy(value); bad["blob"]["mode"] = "100644"
        self.assertEqual(self.verify(bad)["findings"], ["blob_content_or_span_mismatch"])

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlink fixture unavailable")
    def test_symlink_is_its_blob_target_text_and_ancestors_are_not_followed(self):
        outside = self.root / "outside.txt"
        outside.write_bytes(b"outside synthetic canary")
        os.symlink("../outside.txt", self.repo / "link")
        git(self.repo, "add", "link"); git(self.repo, "commit", "-qm", "Record synthetic link")
        self.rev = git(self.repo, "rev-parse", "HEAD").stdout.decode().strip()
        value = self.make("link")
        self.assertEqual(value["blob"]["mode"], "120000")
        self.assertEqual(value["blob"]["content_sha256"], hashlib.sha256(b"../outside.txt").hexdigest())
        self.assertNotEqual(value["blob"]["content_sha256"], hashlib.sha256(outside.read_bytes()).hexdigest())
        self.assertTrue(self.verify(value)["ok"])
        with self.assertRaises(ValueError):
            self.make("link/child")

    def test_directory_gitlink_and_wrong_object_types_are_not_file_witnesses(self):
        blob = self.locator["blob"]["oid"]
        cases = [("160000", b"module", self.rev), ("100644", b"object", self.rev)]
        for mode, name, oid in cases:
            with self.subTest(mode=mode):
                revision = self.commit_tree([(mode, name, oid)])
                bad = copy.deepcopy(self.locator); bad["revision"]["oid"] = revision; bad["path"] = name.decode()
                self.assertFalse(self.verify(bad)["ok"])
        tree_oid = git(self.repo, "rev-parse", "HEAD^{tree}").stdout.decode().strip()
        bad = copy.deepcopy(self.locator); bad["revision"]["oid"] = tree_oid
        self.assertFalse(self.verify(bad)["ok"])
        with self.assertRaises(ValueError):
            self.make("src")
        self.assertEqual(len(blob), 40)

    def test_duplicate_names_and_noncanonical_tree_order_are_rejected(self):
        blob = self.locator["blob"]["oid"]
        for entries in [[("100644", b"same", blob), ("100644", b"same", blob)],
                        [("100644", b"z.txt", blob), ("100644", b"a.txt", blob)]]:
            with self.subTest(entries=entries):
                revision = self.commit_tree(entries)
                bad = copy.deepcopy(self.locator); bad["revision"]["oid"] = revision
                bad["path"] = "same" if entries[0][1] == b"same" else "a.txt"
                # Ordinary Git cannot resolve the latter path in an unsorted
                # tree; matching object hashes alone must not authorize it.
                ordinary = git(self.repo, "show", revision + ":" + bad["path"], check=False)
                if bad["path"] == "a.txt":
                    self.assertNotEqual(ordinary.returncode, 0)
                self.assertFalse(self.verify(bad)["ok"])

    def test_selected_tree_entry_cannot_be_overridden_by_ambient_git_paths(self):
        other = self.root / "other"
        other.mkdir(); git(other, "init", "-q")
        overrides = {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other),
                     "GIT_COMMON_DIR": str(other / ".git"), "GIT_OBJECT_DIRECTORY": str(other / ".git/objects"),
                     "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(other / ".git/objects"),
                     "GIT_INDEX_FILE": str(other / "alternate-index"), "GIT_NO_REPLACE_OBJECTS": "0", "GIT_NO_LAZY_FETCH": "0"}
        with patch.dict(os.environ, overrides):
            self.assertTrue(self.verify()["ok"])

    def test_git_replacement_objects_do_not_change_the_verified_bytes(self):
        replacement = git(self.repo, "hash-object", "-w", "--stdin", data=b"replacement object canary\n").stdout.decode().strip()
        oid = self.locator["blob"]["oid"]
        git(self.repo, "replace", oid, replacement)
        self.assertEqual(git(self.repo, "cat-file", "blob", oid).stdout, b"replacement object canary\n")
        self.assertTrue(self.verify()["ok"])

    @unittest.skipUnless(os.name == "posix", "Local helper positive control uses a POSIX executable")
    def test_missing_promisor_object_never_runs_remote_helper(self):
        marker = self.root / "helper-invoked"
        helper = self.root / "remote-sentinel"
        helper.write_text('#!/bin/sh\nprintf called > "' + str(marker) + '"\nexit 1\n')
        helper.chmod(0o755)
        for key, value in [("remote.sentinel.url", "ext::" + str(helper)), ("remote.sentinel.promisor", "true"),
                           ("extensions.partialClone", "sentinel"), ("protocol.ext.allow", "always")]:
            git(self.repo, "config", key, value)
        oid = self.locator["blob"]["oid"]
        (self.repo / ".git/objects" / oid[:2] / oid[2:]).unlink()
        control = git(self.repo, "cat-file", "-t", oid, check=False)
        self.assertNotEqual(control.returncode, 0)
        self.assertTrue(marker.exists(), "Promisor positive control did not attempt the local synthetic helper")
        marker.unlink()
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(marker.exists(), "Offline verification attempted its configured remote helper")

    def test_invalid_shapes_booleans_and_large_inputs_fail_before_git(self):
        bad = [None, [], [None] * 50000]
        for field, value in [("schema_version", True), ("schema_version", 1.0), ("path", "a" * 4097)]:
            altered = copy.deepcopy(self.locator); altered[field] = value; bad.append(altered)
        for section, field, value in [("blob", "size_bytes", True), ("blob", "size_bytes", locators.MAX_BYTES + 1),
                                      ("blob", "mode", []), ("revision", "object_format", []), ("span", "start", True),
                                      ("span", "byte_end", -1), ("span", "end", 1.0)]:
            altered = copy.deepcopy(self.locator); altered[section][field] = value; bad.append(altered)
        with patch.object(locators.gitio, "local_git_bytes", side_effect=AssertionError("Malformed input reached Git")):
            for value in bad:
                with self.subTest(kind=type(value).__name__):
                    result = self.verify(value)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["findings"], ["invalid_locator"])

    def test_line_indices_are_bounded_by_available_source_bytes(self):
        for huge in [10**5000, 2**63, self.locator["blob"]["size_bytes"] + 1]:
            altered = copy.deepcopy(self.locator)
            altered["span"]["start"] = altered["span"]["end"] = huge
            with patch.object(locators.gitio, "local_git_bytes", side_effect=AssertionError("Unbounded span reached Git")):
                self.assertIsNone(locators.normalize_source_locator(altered))
                self.assertEqual(self.verify(altered)["findings"], ["invalid_locator"])

    def test_unsafe_surrogate_identifiers_and_detector_names_are_not_exported(self):
        for section, field, value in [(None, "repo_id", "repo:\ud800"), ("detector", "id", "detector:\ud800"),
                                      ("detector", "version", "v\ud800")]:
            altered = copy.deepcopy(self.locator)
            (altered if section is None else altered[section])[field] = value
            self.assertIsNone(locators.normalize_source_locator(altered))

    def test_failure_reports_do_not_echo_locator_paths_hashes_or_source(self):
        for edit in [lambda x: x.update(path="synthetic-private-canary\npath"),
                     lambda x: x["span"].update(content_sha256="f" * 64),
                     lambda x: x["revision"].update(oid="d" * 40)]:
            altered = copy.deepcopy(self.locator); edit(altered)
            report = self.verify(altered)
            self.assertFalse(report["ok"])
            serialized = json.dumps(report)
            self.assertNotIn("synthetic-private-canary", serialized)
            self.assertNotIn("f" * 64, serialized)
            self.assertNotIn("d" * 40, serialized)
            self.assertNotIn(self.locator["path"], serialized)
        self.assertEqual(self.verify(repo_id=None)["findings"], ["repository_identity_unverified"])

    def test_repository_mapping_is_a_caller_assertion_not_remote_authentication(self):
        copied = self.root / "copied-checkout"
        shutil.copytree(self.repo, copied)
        # A copy sharing the same immutable objects cannot authenticate an owner.
        result = self.verify(repo=copied)
        self.assertTrue(result["ok"])
        self.assertIn("no remote authentication", result["scope"])
        relabeled = copy.deepcopy(self.locator); relabeled["repo_id"] = "repo:caller-asserted-copy"
        self.assertTrue(self.verify(relabeled, repo=copied, repo_id=relabeled["repo_id"])["ok"])

    def test_sha256_object_format_is_independent_of_raw_content_sha256(self):
        target = self.root / "sha256-checkout"
        target.mkdir()
        if git(target, "init", "-q", "--object-format=sha256", check=False).returncode:
            self.skipTest("Git SHA-256 object repositories unsupported")
        content = b"sha256 object format\r\n"
        (target / "source.txt").write_bytes(content)
        git(target, "add", "."); git(target, "commit", "-qm", "Synthetic SHA256 source")
        revision = git(target, "rev-parse", "HEAD").stdout.decode().strip()
        value = locators.make_locator(str(target), "repo:sha256", revision, "source.txt", line_start=1, detector_id="adversarial")
        self.assertEqual(value["revision"]["object_format"], "sha256")
        self.assertEqual(len(value["blob"]["oid"]), 64)
        self.assertNotEqual(value["blob"]["oid"], value["blob"]["content_sha256"])
        self.assertTrue(self.verify(value, repo=target, repo_id="repo:sha256")["ok"])


if __name__ == "__main__":
    unittest.main()
