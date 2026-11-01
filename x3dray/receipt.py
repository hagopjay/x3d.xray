"""The receipt: one sha256 over everything a build read, ran and wrote, so a page can be traced to its inputs and code.

files lists each input (the data folder), each module of x3d.xray (its Python, the page's scripts, the family config and the spec)
and each output this build wrote, with its sha256 and size. Vendored X_ITE and the label font count as one entry each. gates
records the verdict of every gate. digest is the sha256 of the sorted "path sha256" lines plus the gate verdicts. The page shows the
digest's first twelve characters; how_built returns the whole receipt.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os

from . import __version__
from .page import write_publish_map

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_sha(paths: list[str], root: str) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(f"{os.path.relpath(p, root)} {_sha(p)}\n".encode())
    return h.hexdigest()


def write(data_dir: str, out_dir: str, stamp: str) -> dict:
    files = []

    def add(role: str, path: str, root: str):
        files.append({"role": role, "path": os.path.relpath(path, root).replace(os.sep, "/"), "sha256": _sha(path), "bytes": os.path.getsize(path)})

    for p in sorted(glob.glob(os.path.join(data_dir, "*.json"))):
        add("input", p, os.path.dirname(data_dir.rstrip("/")))
    code = sorted(glob.glob(os.path.join(HERE, "*.py")) + glob.glob(os.path.join(HERE, "web", "*.js")) + glob.glob(os.path.join(HERE, "web", "*.mjs"))
                  + glob.glob(os.path.join(HERE, "web", "*.cjs")) + glob.glob(os.path.join(HERE, "web", "*.css"))
                  + glob.glob(os.path.join(REPO, "families", "*.json")) + glob.glob(os.path.join(REPO, "specs", "*.json")))
    for p in code:
        add("code", p, REPO)
    for name in ("index.html", "bundle.json", "app.js", "tools.js", "xray.x3d", "layout.json", "gates.json"):
        p = os.path.join(out_dir, name)
        if os.path.exists(p):
            add("output", p, out_dir)
    xite = [p for p in glob.glob(os.path.join(out_dir, "x_ite", "**", "*"), recursive=True) if os.path.isfile(p)]
    if xite:
        files.append({"role": "vendored", "path": "x_ite/", "sha256": _tree_sha(xite, out_dir), "files": len(xite), "bytes": sum(os.path.getsize(p) for p in xite)})
    fonts = [p for p in glob.glob(os.path.join(out_dir, "fonts", "*")) if os.path.isfile(p)]
    if fonts:
        files.append({"role": "vendored", "path": "fonts/", "sha256": _tree_sha(fonts, out_dir), "files": len(fonts), "bytes": sum(os.path.getsize(p) for p in fonts)})
    gp = os.path.join(out_dir, "gates.json")
    gates = {}
    if os.path.exists(gp):
        g = json.load(open(gp, encoding="utf-8"))
        gates = {"ok": g["ok"], "ran_at": g["ran_at"], **{k: v["status"] for k, v in g["gates"].items()}}
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: (x["role"], x["path"])):
        h.update(f"{f['role']} {f['path']} {f['sha256']}\n".encode())
    h.update(json.dumps(gates, sort_keys=True).encode())
    rec = {"tool": "x3d.xray", "version": __version__, "made_at": stamp, "digest": h.hexdigest(), "gates": gates,
           "how": "sha256 over the sorted 'role path sha256' lines of every input, module and output, then the gate verdicts",
           "files": files}
    with open(os.path.join(out_dir, "receipt.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    write_publish_map(out_dir)
    return rec
