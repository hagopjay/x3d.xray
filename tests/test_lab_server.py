"""Loopback HTTP integration for the exported laboratory's manifest server.

These tests use Python sockets and a real launcher subprocess. No browser,
family harvest, external service or third-party runtime is required.
"""
import hashlib
import http.client
import json
from pathlib import Path
from queue import Queue
import re
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
import unittest

from x3dray import lab


class LaboratoryServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.root = self.parent / "export"
        self.root.mkdir()
        self.launcher = self.root / "start_lab.py"
        shutil.copyfile(Path(lab.__file__).parent / "lab_web/start_lab.py", self.launcher)
        (self.root / "index.html").write_text("<title>Synthetic laboratory server</title>")
        (self.root / "nested").mkdir()
        (self.root / "nested/allowed.json").write_text('{"synthetic":true}')
        (self.root / "unknown.json").write_text('{"canary":"unregistered private fixture"}')
        (self.parent / "outside.json").write_text('{"canary":"outside private fixture"}')
        self.receipt = {"assets": [self.record("index.html"), self.record("nested/allowed.json")],
                        "controls": [self.record("start_lab.py")]}
        self.save_receipt()

    def record(self, rel):
        p = self.root / rel
        return {"path": rel, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}

    def save_receipt(self):
        (self.root / "lab-receipt.json").write_text(json.dumps(self.receipt))

    def verify(self):
        return subprocess.run([sys.executable, str(self.launcher), "--verify-only"],
                              cwd=self.parent, capture_output=True, text=True, timeout=8)

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)

    def start(self):
        process = subprocess.Popen([sys.executable, str(self.launcher), "--port", "0"],
                                   cwd=self.parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, bufsize=1)
        self.addCleanup(self.stop, process)
        ready = Queue()
        Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
        line = ready.get(timeout=8)
        match = re.search(r"http://localhost:(\d+)", line)
        if not match:
            self.stop(process)
            self.fail("Server did not print its ready URL: " + repr(line))
        return int(match.group(1))

    @staticmethod
    def get(port, path):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_valid_manifest_verifies_without_binding_a_port(self):
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Verified", result.stdout)
        self.assertNotIn("http://", result.stdout)

    def test_tampered_asset_is_refused_at_startup(self):
        (self.root / "index.html").write_text("changed after export")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed after export", result.stderr)

    def test_tampered_launcher_control_is_refused(self):
        self.launcher.write_text(self.launcher.read_text() + "\n# synthetic modification\n")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed after export", result.stderr)

    def test_real_http_serves_only_registered_assets_and_detached_receipt(self):
        port = self.start()
        for path, expected in (("/", b"Synthetic laboratory server"), ("/index.html", b"Synthetic laboratory server"),
                               ("/nested/allowed.json", b'"synthetic":true'), ("/lab-receipt.json", b'"assets"')):
            with self.subTest(path=path):
                status, body, headers = self.get(port, path)
                self.assertEqual(status, 200)
                self.assertIn(expected, body)
                self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        for path in ("/unknown.json", "/start_lab.py", "/%73tart_lab.py", "/../outside.json",
                     "/%2e%2e/outside.json", "/%2E%2E%2Foutside.json", "/nested/", "/missing.json"):
            with self.subTest(path=path):
                status, body, _ = self.get(port, path)
                self.assertIn(status, (403, 404))
                self.assertNotIn(b"private fixture", body)

    def test_parent_traversal_manifest_is_refused(self):
        self.receipt["assets"].append({"path": "../outside.json",
                                      "sha256": hashlib.sha256((self.parent / "outside.json").read_bytes()).hexdigest()})
        self.save_receipt()
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)

    def test_ambiguous_encoded_manifest_paths_are_refused_before_http_decoding(self):
        # A second unquote by SimpleHTTPRequestHandler must never reinterpret a
        # registered literal directory as '..' and serve an unregistered file.
        rel = "nested/%2e%2e/unknown.json"
        p = self.root / rel
        p.parent.mkdir(parents=True)
        p.write_text('{"synthetic":"registered encoded filename"}')
        self.receipt["assets"].append(self.record(rel))
        self.save_receipt()
        result = self.verify()
        self.assertNotEqual(result.returncode, 0, "Ambiguous URL-encoded manifest paths must be rejected")


if __name__ == "__main__":
    unittest.main()
