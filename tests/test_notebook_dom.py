"""The notebook panel (x3dray/web/notebook.js) in jsdom, on copies of the files the page build ships; skipped when Node or jsdom is missing.

CI installs jsdom and sets REQUIRE_JSDOM=1, so there the test fails instead of skipping."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from eda_support import minimal_bundle, synthetic_dataset  # noqa: E402
from x3dray import eda  # noqa: E402

SCRIPT = ROOT / "experiments" / "test_notebook_dom.cjs"


def _jsdom_available() -> bool:
    if not shutil.which("node"):
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, text=True)
    return r.returncode == 0


@unittest.skipUnless(os.environ.get("REQUIRE_JSDOM") or (shutil.which("node") and _jsdom_available()), "Node with jsdom is required (set NODE_PATH)")
class NotebookPanel(unittest.TestCase):
    def test_the_panel_in_jsdom(self):
        ds = synthetic_dataset(seed=41, repos=3, per_repo=120, private=("r2",), plant=("ai_hours", "outlier"))
        e, problems = eda.run(ds)
        self.assertEqual(problems, [])
        b = minimal_bundle(ds, e)
        text = json.dumps(b)
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle.json"
            bundle.write_text(text)
            r = subprocess.run(["node", str(SCRIPT), str(ROOT), str(bundle)], capture_output=True, text=True, timeout=240)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:] + r.stdout[-1000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(len(out["checks"]), 12)


if __name__ == "__main__":
    unittest.main()
