"""The receipt: one sha256 over everything a build read, ran and wrote, so a page can be traced to its inputs and code.

files lists each input (the data folder), each module of x3d.xray (its Python, the page's scripts, the family config and the spec)
and each registered output this build serves, with its sha256 and size. Every vendored asset is recorded individually. gates
records the verdict of every gate. digest is the sha256 of the sorted "path sha256" lines plus the gate verdicts. The page shows the
digest's first twelve characters; how_built returns the whole receipt.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os

from . import __version__
from .page import write_publish_map, privacy_scan
from . import publication

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
        # Logical locators remain valid when a fresh staging folder is promoted.
        # Machine-specific/staging directory names are not input identities.
        add("input", p, data_dir)
    code = sorted(glob.glob(os.path.join(HERE, "*.py")) + glob.glob(os.path.join(HERE, "web", "*.js")) + glob.glob(os.path.join(HERE, "web", "*.mjs"))
                  + glob.glob(os.path.join(HERE, "web", "*.cjs")) + glob.glob(os.path.join(HERE, "web", "*.css"))
                  + glob.glob(os.path.join(REPO, "families", "*.json")) + glob.glob(os.path.join(REPO, "specs", "*.json")))
    for p in code:
        add("code", p, REPO)
    # The publication registry is the sole authority for served bytes. Every
    # selected asset is listed individually, including vendor components.
    selected = publication.assets(out_dir)
    for asset in selected:
        if asset["path"] != "receipt.json":
            add("output" if asset["role"] == "owned" else "vendored", str(publication.safe_path(out_dir, asset["path"])), out_dir)
    for name in ("layout.json", publication.REGISTRY):
        p = publication.safe_path(out_dir, name)
        if p.is_file():
            add("build-control", str(p), out_dir)
    gp = os.path.join(out_dir, "gates.json")
    gates = {}
    if os.path.exists(gp):
        with open(gp, encoding="utf-8") as fh:
            g = json.load(fh)
        gates = {"ok": g["ok"], "release_ready": g.get("release_ready", False), "ran_at": g["ran_at"], **{k: v["status"] for k, v in g["gates"].items()}}
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: (x["role"], x["path"])):
        h.update(f"{f['role']} {f['path']} {f['sha256']}\n".encode())
    h.update(json.dumps(gates, sort_keys=True).encode())
    rec = {"tool": "x3d.xray", "version": __version__, "made_at": stamp, "digest": h.hexdigest(), "gates": gates,
           "how": "sha256 over sorted 'role path sha256' lines of inputs, code, registered served assets and build controls, then gate verdicts; receipt.json is detached and excludes its own bytes",
           "publication": {"assets": [a["path"] for a in selected if a["path"] != "receipt.json"], "detached": "receipt.json"},
           "files": files}
    if privacy_scan(json.dumps(rec, ensure_ascii=False)):
        raise ValueError("receipt: generated receipt failed the publication privacy scan")
    target = publication.safe_path(out_dir, "receipt.json")
    temporary = publication.safe_path(out_dir, ".receipt.json.tmp")
    with temporary.open("w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    temporary.replace(target)
    # The report itself joins the same served set. Check that final set, without
    # mutating gates.json after its bytes have been attested.
    from .gates import gate_privacy
    if gate_privacy(out_dir)["status"] != "pass":
        os.remove(os.path.join(out_dir, "receipt.json"))
        write_publish_map(out_dir)
        raise ValueError("receipt: final registered publication set failed privacy validation")
    write_publish_map(out_dir)
    return rec
