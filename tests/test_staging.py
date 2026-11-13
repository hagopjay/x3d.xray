"""Fresh-stage and rollback contracts, using ordinary temporary directories."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from x3dray import staging
from x3dray.page import write_publish_map


class StagedBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data, self.out = self.root / "data", self.root / "out"
        self.data.mkdir()
        self.out.mkdir()
        (self.data / "curated.json").write_text('{"old":true}')
        (self.out / "index.html").write_text("old page")
        self.messages = io.StringIO()

    def run_stage(self, runner):
        with redirect_stdout(self.messages):
            return staging.run_staged(str(self.data), str(self.out), runner)

    @staticmethod
    def successful(data, out):
        data, out = Path(data), Path(out)
        (data / "dataset.json").write_text('{"fresh":true}')
        (out / "index.html").write_text("new page")
        (out / "bundle.json").write_text("{}")
        write_publish_map(str(out))
        return 0

    def test_runner_failure_preserves_destinations_and_retains_diagnostics(self):
        def fail(data, out):
            self.assertTrue(Path(data).is_absolute())
            self.assertTrue(Path(out).is_absolute())
            (Path(data) / "partial.json").write_text("{}")
            return 7
        self.assertEqual(self.run_stage(fail), 7)
        self.assertEqual((self.data / "curated.json").read_text(), '{"old":true}')
        self.assertEqual((self.out / "index.html").read_text(), "old page")
        stages = list(self.root.glob(".data.stage-*"))
        self.assertEqual(len(stages), 1)
        self.assertTrue((stages[0] / "partial.json").is_file())
        self.assertIn(str(stages[0]), self.messages.getvalue())
        self.assertFalse(list(self.root.glob("*.backup-*")))

    def test_fresh_build_cannot_read_stale_optional_artifacts(self):
        def build(data, out):
            self.assertFalse((Path(data) / "curated.json").exists())
            self.assertEqual(list(Path(out).iterdir()), [])
            return self.successful(data, out)
        self.assertEqual(self.run_stage(build), 0)
        self.assertFalse((self.data / "curated.json").exists())
        self.assertTrue((self.data / "dataset.json").exists())

    def test_success_keeps_named_backups_and_rebases_publication_sources(self):
        self.assertEqual(self.run_stage(self.successful), 0)
        data_backups, out_backups = list(self.root.glob("data.backup-*")), list(self.root.glob("out.backup-*"))
        self.assertEqual(len(data_backups), 1)
        self.assertEqual(len(out_backups), 1)
        self.assertTrue((data_backups[0] / "curated.json").exists())
        self.assertEqual((out_backups[0] / "index.html").read_text(), "old page")
        manifest = json.loads((self.out / "publish.json").read_text())
        self.assertEqual(manifest["bundle.json"]["from"], str(self.out / "bundle.json"))
        self.assertNotIn(".stage-", json.dumps(manifest))

    def test_second_promotion_rename_failure_rolls_back_both_destinations(self):
        original = Path.rename
        def rename(source, target):
            if source.name.startswith(".out.stage-") and Path(target) == self.out:
                raise OSError("synthetic promotion failure")
            return original(source, target)
        with mock.patch.object(Path, "rename", rename):
            self.assertEqual(self.run_stage(self.successful), 1)
        self.assertEqual((self.data / "curated.json").read_text(), '{"old":true}')
        self.assertEqual((self.out / "index.html").read_text(), "old page")
        self.assertTrue((next(self.root.glob(".data.stage-*")) / "dataset.json").exists())
        self.assertIn("rollback completed", self.messages.getvalue())

    def test_final_manifest_failure_rolls_back_both_destinations(self):
        with mock.patch.object(staging, "write_publish_map", side_effect=ValueError("synthetic map failure")):
            self.assertEqual(self.run_stage(self.successful), 1)
        self.assertTrue((self.data / "curated.json").exists())
        self.assertEqual((self.out / "index.html").read_text(), "old page")
        self.assertIn("rollback completed", self.messages.getvalue())

    def test_runner_exception_is_reraised_with_destinations_unchanged(self):
        def crash(data, out):
            raise RuntimeError("synthetic runner crash")
        with self.assertRaisesRegex(RuntimeError, "runner crash"):
            self.run_stage(crash)
        self.assertTrue((self.data / "curated.json").exists())
        self.assertEqual((self.out / "index.html").read_text(), "old page")
        self.assertIn("diagnostic output retained", self.messages.getvalue())

    def test_unsafe_targets_are_rejected_before_runner(self):
        runner = mock.Mock(return_value=0)
        nested = self.data / "nested"
        for a, b in ((self.data, self.data), (self.data, nested), (nested, self.data), (Path("/"), self.out)):
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                staging.run_staged(str(a), str(b), runner)
        (self.data / ".git").write_text("synthetic worktree marker")
        with self.assertRaises(ValueError):
            staging.run_staged(str(self.data), str(self.out), runner)
        link = self.root / "link"
        link.symlink_to(self.out, target_is_directory=True)
        with self.assertRaises(ValueError):
            staging.run_staged(str(self.data.parent / "other"), str(link), runner)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
