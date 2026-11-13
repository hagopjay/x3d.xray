"""CLI integration for release policy and fresh full-pipeline staging."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from x3dray import __main__ as cli, gates


def verdict(ready=False, ok=True):
    return {"ok": ok, "release_ready": ready,
            "gates": {"xsd": {"status": "pass" if ready else "skip", "summary": "synthetic dependency state"}},
            "skipped": [] if ready else ["xsd"]}


class ReleasePolicyCLI(unittest.TestCase):
    def test_release_flag_rejects_skipped_checks_while_development_can_continue(self):
        args = ["gate", "--data", "unused-data", "--out", "unused-out", "--no-live"]
        with mock.patch.object(gates, "run_all", return_value=verdict()) as run, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(args), 0)
            self.assertEqual(cli.main(args + ["--release"]), 1)
        self.assertFalse(run.call_args.kwargs["live"])
        self.assertIn("release incomplete", output.getvalue())

    def test_failed_checks_fail_development_and_ready_checks_pass_release(self):
        args = ["gate", "--data", "unused-data", "--out", "unused-out"]
        with mock.patch.object(gates, "run_all", return_value=verdict(ok=False)), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args), 1)
        with mock.patch.object(gates, "run_all", return_value=verdict(ready=True)), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args + ["--release"]), 0)


class FullPipelineStagingCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / "source"
        source.mkdir()
        self.git(source, "init", "-q")
        self.git(source, "config", "user.name", "Synthetic Author")
        self.git(source, "config", "user.email", "author@example.invalid")
        (source / "normal.py").write_text("print('synthetic')\n")
        self.git(source, "add", ".")
        self.git(source, "commit", "-qm", "synthetic initial")
        self.family = self.root / "family.json"
        self.family.write_text(json.dumps({"family": "synthetic-cli", "title": "Synthetic fixture",
            "identities": [{"label": "Fixture Author", "class": "you", "match_names": ["Synthetic Author"]}],
            "repos": [{"name": "x3d_mcp", "dir": "source", "kind": "own", "visibility": "public"}]}))
        self.spec = self.root / "spec.json"
        self.spec.write_text(json.dumps({"family": "synthetic-cli", "title": "Synthetic fixture",
            "views": [{"id": "overview", "label": "Overview", "caption": "{family_span.commits} commits"}],
            "tour": [], "questions": []}))
        self.xite = self.root / "dist"
        for rel in ["x_ite.min.js", "x_ite.css", "LICENSE.md", "assets/linetype/linetypes.png",
                    "assets/lib/mikktspace_bg.wasm", "assets/images/logo.svg"]:
            p = self.xite / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"/* X_ITE v16.7.0 synthetic build fixture, not a runtime */")
        self.font = self.root / "font.ttf"
        self.font.write_bytes(b"synthetic font fixture, not a rendering font")
        self.data, self.out = self.root / "data", self.root / "out"
        self.data.mkdir()
        self.out.mkdir()
        (self.data / "curated.json").write_text('{"sentinel":"existing data"}')
        (self.out / "index.html").write_text("existing output")
        self.args = ["all", "--family", str(self.family), "--root", str(self.root),
                     "--data", str(self.data), "--out", str(self.out), "--spec", str(self.spec),
                     "--xite", str(self.xite), "--font", str(self.font), "--no-live", "--stamp", "2026-11-12T00:00:00Z"]

    @staticmethod
    def git(path, *args):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    def test_failed_release_full_pipeline_leaves_existing_destinations_untouched(self):
        # Ingestion, structure, profiling, self-description, spec, compiler and
        # page generation plus fact/privacy/witness gates run for real. External
        # validators/runtimes are stubbed; unavailable XSD must stop promotion.
        with self.external_checks(xsd_status="skip"), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(self.args + ["--release"]), 1)
        self.assertEqual((self.data / "curated.json").read_text(), '{"sentinel":"existing data"}')
        self.assertEqual((self.out / "index.html").read_text(), "existing output")
        stage_data = next(self.root.glob(".data.stage-*"))
        stage_out = next(self.root.glob(".out.stage-*"))
        self.assertFalse((stage_data / "curated.json").exists())
        self.assertTrue((stage_data / "dataset.json").is_file())
        self.assertTrue((stage_out / "xray.x3d").is_file())
        self.assertFalse(list(self.root.glob("*.backup-*")))
        self.assertIn("all: stopped at gate", output.getvalue())

    def test_successful_full_pipeline_promotes_and_preserves_receipt_paths(self):
        with self.external_checks(), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([a for a in self.args if a != "--no-live"] + ["--release"]), 0)
        self.assertFalse((self.data / "curated.json").exists())
        rendered = json.loads((self.data / "spec.rendered.json").read_text())
        self.assertEqual(rendered["views"][0]["caption"], "1 commits")
        self_description = json.loads((self.data / "self.json").read_text())
        self.assertEqual(self_description["tool"]["built_at"], "2026-11-12T00:00:00Z")
        receipt = json.loads((self.out / "receipt.json").read_text())
        self.assertEqual(receipt["made_at"], "2026-11-12T00:00:00Z")
        self.assertTrue(receipt["gates"]["release_ready"])
        self.assertTrue(all(".stage-" not in f["path"] for f in receipt["files"]))
        self.assertIn("dataset.json", [f["path"] for f in receipt["files"] if f["role"] == "input"])
        manifest = json.loads((self.out / "publish.json").read_text())
        self.assertEqual(manifest["bundle.json"]["from"], str(self.out / "bundle.json"))
        self.assertEqual(len(list(self.root.glob("data.backup-*"))), 1)
        self.assertEqual(len(list(self.root.glob("out.backup-*"))), 1)

    @staticmethod
    def external_checks(xsd_status="pass"):
        stack = ExitStack()
        for name in ("gate_xsd", "gate_loom", "gate_tools", "gate_live"):
            status = xsd_status if name == "gate_xsd" else "pass"
            stack.enter_context(mock.patch.object(gates, name, return_value={"status": status, "summary": "synthetic external gate"}))
        return stack


if __name__ == "__main__":
    unittest.main()
