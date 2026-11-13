"""Deterministic, synthetic bridge laboratory powered by x3dray's graph engine.

Build: python -m x3dray lab --out out/bridge-lab [--xite /path/to/x_ite/dist]
The exported HTML works offline in 2D. Local vendored X_ITE enables the 3D view.
This fixture teaches graph interpretations; it is not a measurement of real code.
"""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil

from . import centrality

WEB = Path(__file__).with_name("lab_web")
PRUNE_SHARE = Fraction(65, 100)
FILES = [
    ("ingest.py", "ingest", [190, 240]), ("gitio.py", "ingest", [115, 330]),
    ("snapshot.py", "ingest", [290, 340]), ("identity.py", "ingest", [215, 430]),
    ("evidence.py", "bridge", [500, 325]), ("query.py", "bridge", [510, 460]),
    ("scene.py", "render", [705, 250]), ("layout.py", "render", [805, 330]),
    ("ui.js", "render", [700, 425]), ("pick.js", "render", [845, 455]),
    ("README.md", "hub", [500, 120]), ("test_ingest.py", "tests", [90, 490]),
    ("test_scene.py", "tests", [900, 245]), ("guide.md", "docs", [390, 80]),
    ("generated/catalog.json", "generated", [650, 75]), ("vendor/schema.xsd", "generated", [925, 120]),
]


def fixture() -> list[dict]:
    """A known-answer graph: two communities, a hub and a cross-community cut.

    The sole 16-file generated sweep creates the deliberate 15/16 cutoff cliff.
    Fixture IDs identify synthetic observations, never real Git objects.
    """
    commits = []
    def add(paths, subject, who="you", kind="authored"):
        i = len(commits)
        commits.append({"id": f"lab-{i + 1:03}", "paths": sorted(set(paths)), "subject": subject,
                        "who": who, "kind": kind, "t": 1793491200 + i * 3600})
    left = ["ingest.py", "gitio.py", "snapshot.py", "identity.py"]
    right = ["scene.py", "layout.py", "ui.js", "pick.js"]
    for i in range(15):
        add([left[i % 4], left[(i + 1) % 4], "README.md"], "Explain an ingestion boundary", "you" if i % 3 else "ai")
        add([right[i % 4], right[(i + 1) % 4], "README.md"], "Connect a rendering responsibility", "ai" if i % 3 else "you")
    for _ in range(7):
        add(["snapshot.py", "evidence.py", "README.md"], "Bind snapshot identity to evidence")
        add(["evidence.py", "scene.py", "README.md"], "Carry evidence identity into the scene", "ai")
    for i in range(5):
        add(["evidence.py", "query.py", "README.md"], "Expose a witnessed query", "ai")
    for _ in range(3):
        add(["gitio.py", "test_ingest.py", "README.md"], "Exercise the byte-safe ingestion boundary")
        add(["scene.py", "test_scene.py", "README.md"], "Check a scene identity", "ai")
    add(["guide.md", "README.md"], "Document interpretation limits")
    add([p for p, _, _ in FILES], "Regenerate the complete catalog and schema", "bot", "generated_sweep")
    return commits


def analyse(commits: list[dict], max_files=15, distance="count", prune=False, removed=None) -> dict:
    """One fully specified analytical lens, with complete source witnesses.

    A graph-node removal is a topology experiment: source observations remain
    unchanged, while edges incident on the removed node are withheld.
    """
    if max_files not in (5, 15, 30) or distance not in ("count", "jaccard"):
        raise ValueError("unsupported laboratory lens")
    known = {p for p, _, _ in FILES}
    ids = [c["id"] for c in commits]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate synthetic observation ID")
    if any(len(c["paths"]) != len(set(c["paths"])) or not set(c["paths"]) <= known for c in commits):
        raise ValueError("fixture paths must be unique and present in the laboratory vocabulary")
    all_paths = {p for c in commits for p in c["paths"]}
    if removed is not None and removed not in all_paths:
        raise ValueError("removed node is not in the fixture")
    used = [c for c in commits if len(c["paths"]) <= max_files]
    omitted = [c["id"] for c in commits if len(c["paths"]) > max_files]
    node_witnesses = defaultdict(set)
    for c in used:
        for path in c["paths"]:
            node_witnesses[path].add(c["id"])
    high = sorted(p for p, ids in node_witnesses.items() if len(ids) >= 5 and Fraction(len(ids), len(used)) >= PRUNE_SHARE)
    withheld = set(high if prune else []) | ({removed} if removed else set())
    edges = defaultdict(list)
    for c in used:
        paths = [p for p in c["paths"] if p not in withheld]
        for i, a in enumerate(paths):
            for b in paths[i + 1:]:
                edges[(a, b)].append(c["id"])
    adj = centrality.make_graph([(a, b, len(ids)) for (a, b), ids in edges.items()])
    for p in sorted(node_witnesses):
        if p not in withheld:
            adj.setdefault(p, {})
    dist = {}
    for a, nb in adj.items():
        dist[a] = {}
        for b, weight in nb.items():
            dist[a][b] = Fraction(1, weight) if distance == "count" else Fraction(len(node_witnesses[a] | node_witnesses[b]), weight)
    bc = centrality.betweenness(dist, weighted=True)
    cuts = centrality.articulation_points(adj)
    components = centrality.components(adj)
    component_size = {n: len(group) for group in components for n in group}
    nodes = []
    for path, group, pos in FILES:
        if path not in adj:
            continue
        n = component_size[path]
        pairs = (n - 1) * (n - 2) / 2
        nodes.append({"id": path, "group": group, "pos": list(pos), "degree": len(adj[path]), "cut": path in cuts,
                      "betweenness": round(bc[path], 8), "share": round(bc[path] / pairs, 8) if pairs else 0,
                      "component": n, "witnesses": sorted(node_witnesses[path])})
    nodes.sort(key=lambda n: (-n["betweenness"], n["id"]))
    out_edges = []
    for (a, b), ids in sorted(edges.items()):
        d = dist[a][b]
        out_edges.append({"a": a, "b": b, "count": len(ids), "distance": f"{d.numerator}/{d.denominator}", "witnesses": ids})
    return {"parameters": {"max_files": max_files, "distance": distance, "prune": prune, "removed": removed,
                           "prune_min_commits": 5, "prune_share": float(PRUNE_SHARE)},
            "nodes": nodes, "edges": out_edges, "components": [sorted(c) for c in components],
            "source_commits": len(commits), "included_commits": len(used), "excluded_commits": omitted,
            "pruned_nodes": high if prune else [], "prune_candidates": high,
            "scope": "Synthetic file co-change graph. Node removal does not predict runtime failure."}


def build_bundle() -> dict:
    commits = fixture()
    digest = hashlib.sha256(json.dumps(commits, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    scenarios = {}
    for cutoff in (5, 15, 30):
        for distance in ("count", "jaccard"):
            for prune in (False, True):
                key = f"{cutoff}:{distance}:{int(prune)}"
                baseline = analyse(commits, cutoff, distance, prune)
                # Small precomputed experiments keep the browser artifact offline,
                # deterministic and tied to the same Python reference engine.
                scenarios[key] = {"baseline": baseline, "removals": {
                    n["id"]: analyse(commits, cutoff, distance, prune, n["id"]) for n in baseline["nodes"]}}
    analysis_digest = hashlib.sha256(json.dumps(scenarios, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema_version": "1", "fixture_digest": digest, "analysis_digest": analysis_digest, "synthetic": True,
            "engine": "x3dray.centrality exact rational weighted distances",
            "policy_note": "Laboratory pruning uses 65% to isolate the README hub in this small fixture; production structure.py has its own declared policy.",
            "commits": commits, "scenarios": scenarios}


def build(out_dir: str, xite: str | None = None) -> dict:
    root = Path(xite) if xite else None
    if root and (not (root / "x_ite.min.js").is_file() or not (root / "x_ite.css").is_file()):
        raise ValueError("--xite must name a complete X_ITE dist directory")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bundle = build_bundle()
    embedded = json.dumps(bundle, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")
    template = (WEB / "index.html").read_text(encoding="utf-8")
    page = template.replace("/*__CSS__*/", (WEB / "lab.css").read_text()).replace("/*__JS__*/", (WEB / "lab.js").read_text()).replace("__LAB_DATA__", embedded)
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "lab-data.json").write_text(json.dumps(bundle, indent=1), encoding="utf-8")
    shutil.copyfile(WEB / "start_lab.py", out / "start_lab.py")
    written = {"index.html", "lab-data.json"}
    vendor = False
    if root:
        target = out / "x_ite"
        target.mkdir(exist_ok=True)
        for name in ("x_ite.min.js", "x_ite.css", "LICENSE.md"):
            if (root / name).exists():
                shutil.copyfile(root / name, target / name)
                written.add("x_ite/" + name)
        if (root / "assets").exists():
            for source in sorted((root / "assets").rglob("*")):
                if source.is_file() and not source.is_symlink():
                    rel = source.relative_to(root)
                    destination = target / rel
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    written.add("x_ite/" + rel.as_posix())
        vendor = True
    assets = []
    for rel in sorted(written):
        p = out / rel
        assets.append({"path": rel, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size})
    receipt = {"fixture_digest": bundle["fixture_digest"], "analysis_digest": bundle["analysis_digest"], "synthetic": True, "xite_vendored": vendor,
               "controls": [{"path": "start_lab.py", "sha256": hashlib.sha256((out / "start_lab.py").read_bytes()).hexdigest()}],
               "xite_expected_version": "16.7.0" if vendor else None, "assets": assets,
               "scope": "Detached content manifest for this laboratory export; not a production family-gate verdict."}
    (out / "lab-receipt.json").write_text(json.dumps(receipt, indent=1))
    return {"html": str(out / "index.html"), "scenarios": len(bundle["scenarios"]), "commits": len(bundle["commits"]),
            "fixture_digest": bundle["fixture_digest"], "vendored_3d": vendor}
