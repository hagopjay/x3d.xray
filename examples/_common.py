"""Shared by the EDA examples: the repo on the path, the synthetic data of the tests, and a plain table printer.

The data is synthetic (tests/eda_support.py: seeded, no clock, no network); nothing here reads a real history, and no example makes a claim about one."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from eda_support import minimal_bundle, synthetic_dataset  # noqa: E402,F401
from x3dray import eda  # noqa: E402,F401


def table(rows: list[list], head: list[str]) -> str:
    cells = [[str(c) for c in r] for r in [head] + rows]
    w = [max(len(r[i]) for r in cells) for i in range(len(head))]
    line = lambda r: "  ".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip()
    return "\n".join([line(cells[0]), line(["-" * n for n in w])] + [line(r) for r in cells[1:]])


def node_available() -> bool:
    return shutil.which("node") is not None


def run_node(script: str, bundle: dict, *args: str, timeout: int = 120) -> dict:
    """Run an ES-module script in Node with the page's own tools.js imported as `T` and the bundle parsed as `B`; the script prints one JSON line."""
    with tempfile.TemporaryDirectory() as tmp:
        b, s = Path(tmp) / "bundle.json", Path(tmp) / "run.mjs"
        b.write_text(json.dumps(bundle))
        s.write_text("import fs from 'node:fs';\nimport {pathToFileURL} from 'node:url';\n"
                     f"const T = await import(pathToFileURL({json.dumps(str(ROOT / 'x3dray' / 'web' / 'tools.js'))}).href);\n"
                     f"const B = JSON.parse(fs.readFileSync({json.dumps(str(b))}, 'utf8'));\n" + script)
        r = subprocess.run(["node", str(s), *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("node failed: " + r.stderr[-1500:])
    return json.loads(r.stdout.strip().splitlines()[-1])
