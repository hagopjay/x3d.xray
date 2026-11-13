"""Build the synthetic Architecture Observatory with real static-scene audits.

The specimen is harvested from temporary real Git repositories. XML mutations
are checked by the independent verifier at build time. The browser inspects
those recorded results and uses the production shared architecture query tool.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from . import compiler, harvest, profile, relations, selfdesc, spec, structure, ties

WEB = Path(__file__).with_name("proof_web")


def canonical_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def fixture_inputs():
    """Return a reproducible harvested specimen and inspectable source tips."""
    sources = {
        "ingest": {"ingest_api.py": "def snapshot():\n    return {'observed': True}\n", "README.md": "Synthetic ingestion boundary.\n"},
        "evidence": {"contract.py": "from ingest_api import snapshot\n\ndef witnessed():\n    return snapshot()\n", "package.json": '{"name":"evidence_core","dependencies":{"ingest_api":"workspace:*"}}\n'},
        "render": {"viewer.py": "from evidence_core import witnessed\n\ndef view():\n    return witnessed()\n", "package.json": '{"name":"render_view","dependencies":{"evidence_core":"workspace:*"}}\n'},
        "review": {"inspector.py": "def inspect_export():\n    return 'synthetic review'\n", "README.md": "The render viewer and ingest boundary may share a conceptual review workflow. This is a written association.\n"},
    }
    aliases = {"ingest": ["ingest_api"], "evidence": ["evidence_core"], "render": ["render_view"], "review": ["review_inspector"]}
    cfg = {"family": "architecture-observatory", "title": "Synthetic Architecture Observatory", "owner": "synthetic",
           "identities": [{"label": "Fixture Author", "class": "you", "match_names": ["Fixture Author"]},
                          {"label": "Fixture Assistant", "class": "ai", "match_names": ["Fixture Assistant"]}],
           "default_identity": {"label": "Unmapped", "class": "upstream"},
           "repos": [{"name": name, "dir": name, "kind": "own", "visibility": "public", "aliases": aliases[name],
                      "role": "Synthetic " + name + " boundary"} for name in sources],
           "lineage": ["ingest", "evidence", "render"],
           "lineage_source": "Synthetic owner statement about conceptual development order, not Git ancestry."}
    cfg["repos"][-1]["uses"] = {"render": "The synthetic family configuration declares the review viewer."}
    cfg["repos"][-1]["observer"] = True
    def git(path, *args, stamp=None):
        env = dict(os.environ)
        if stamp:
            env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        return subprocess.run(["git", "-C", str(path), *args], env=env, check=True, capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="xray-observatory-") as temp:
        root = Path(temp)
        for k, (name, files) in enumerate(sources.items()):
            path = root / name
            path.mkdir()
            git(path, "init", "-q", "-b", "main")
            git(path, "config", "commit.gpgsign", "false")
            git(path, "config", "user.email", "fixture@example.invalid")
            for i in range(6):
                git(path, "config", "user.name", "Fixture Author" if i % 2 == 0 else "Fixture Assistant")
                for rel, text in files.items():
                    (path / rel).write_text(text, encoding="utf-8")
                (path / "revision.txt").write_text(f"Synthetic {name} revision {i}\n")
                git(path, "add", "--all")
                when = dt.datetime(2026, 1, 3, 8 + i, k, tzinfo=dt.timezone.utc).isoformat()
                message = f"Synthetic {name} observation {i}"
                if i % 2 and name in {"evidence", "render"}:
                    message += "\n\nClaude-Session: https://claude.ai/code/session_syntheticproof"
                git(path, "commit", "-qm", message, stamp=when)
        internal = harvest.harvest_family(cfg, str(root))
        discovered = ties.find_ties(cfg, internal)
        dataset = harvest.strip_internal(internal)
    dataset["harvested_at"] = "2026-01-04T00:00:00Z"
    curated = {"ties": [{"id": "SYN-READING", "from": "review", "to": "ingest", "kind": "conceptual reading", "grade": "S",
                        "what": "The synthetic curator proposes an association to investigate.",
                        "direction": "Conceptual association; execution direction was not established.",
                        "evidence": ["Synthetic curator statement; this is an interpretation, not an import trace."]}], "nodes": [], "series": []}
    return cfg, dataset, discovered, curated, sources


def constellation_xml(xml: str) -> str:
    """Keep the actual compiled constellation and materialize its shared assets.

    This is a selection/export transformation, not the independent verifier.
    Resolving missing shared Appearance/FontStyle definitions keeps the excerpt
    valid without importing unrelated helix/city geometry.
    """
    root = ET.fromstring(xml)
    defs = {e.get("DEF"): e for e in root.iter() if e.get("DEF")}
    scene = root.find("Scene")
    for child in list(scene):
        keep = child.tag in {"Background", "NavigationInfo", "DirectionalLight", "WorldInfo"} or child.get("DEF") in {"Ties", "VP_ties"}
        if not keep:
            scene.remove(child)
    # First use gains its original DEF; later uses refer to that definition.
    for _ in range(64):
        retained = {e.get("DEF") for e in root.iter() if e.get("DEF")}
        replacement = None
        for parent in root.iter():
            for index, child in enumerate(parent):
                name = child.get("USE")
                if name and name not in retained:
                    if name not in defs:
                        raise ValueError("compiler excerpt contains unresolved shared content")
                    replacement = parent, index, copy.deepcopy(defs[name])
                    break
            if replacement:
                break
        if not replacement:
            return ET.tostring(root, encoding="unicode")
        parent, index, original = replacement
        parent[index] = original
    raise ValueError("shared-content expansion exceeded its bounded budget")


def build_bundle() -> dict:
    from .scene_mutations import audit_cases
    from .investigation import new_log, append_event, replay
    cfg, dataset, found, curated, sources = fixture_inputs()
    concepts, bridges, problems = structure.run(dataset)
    if problems:
        raise ValueError("synthetic structure failed its independent checks")
    prof = profile.profile_family(dataset, found, curated, concepts, bridges)
    selfd = selfdesc.describe(dataset, prof, "2026-01-04T00:00:00Z", cfg)
    narrative = {"family": cfg["family"], "title": cfg["title"], "views": [{"id": "ties", "label": "Recorded relationships",
                  "caption": "{family_span.commits} synthetic commits provide the observation context."}], "tour": [], "questions": []}
    rendered = spec.render_spec(narrative, prof, selfd)
    if rendered["problems"]:
        raise ValueError("synthetic narrative did not validate")
    full_xml, layout = compiler.build_scene(dataset, found, curated, prof, rendered, selfd, concepts, bridges)
    xml = constellation_xml(full_xml)
    graph = relations.build_relations(dataset, found, curated)
    bundle = {"meta": {"synthetic": True, "title": cfg["title"]}, "repos": dataset["repos"],
              "ties": {"machine": found["pairs"], "curated": curated["ties"]}, "relations": graph,
              "vocab": {alias: r["name"] for r in cfg["repos"] for alias in [r["name"], *r["aliases"]]}, "layout": layout}
    plan = layout["scene_plan"]
    cases = audit_cases(xml, plan, bundle)
    if not cases[0]["verification"]["ok"]:
        raise ValueError("baseline synthetic scene failed independent geometry verification")
    fingerprint = canonical_digest({"dataset": dataset, "ties": found, "curated": curated})
    implementation = {name: hashlib.sha256((WEB.parent / name).read_bytes()).hexdigest() for name in
                      ("compiler.py", "relations.py", "scenecheck.py", "scene_mutations.py", "investigation.py", "prooflab.py", "web/tools.js", "proof_web/investigation.js", "proof_web/proof.js")}
    analysis = canonical_digest({"implementation": implementation, "plan": plan, "relations": graph,
                                 "audits": [{"id": c["id"], "xml": c["xml"], "verification": c["verification"]} for c in cases]})
    findings = sorted({f["code"] for c in cases for f in c["verification"]["findings"]})
    catalog = {"entities": [e["id"] for e in plan["entities"]], "relations": [r["id"] for r in graph["records"]],
               "mutations": [c["id"] for c in cases], "findings": findings, "predicates": sorted(graph["predicates"])}
    log = new_log(fingerprint, analysis)
    for event in [{"action": "set_lens", "lens": {"predicates": ["declares_use", "dependency_reference"], "direction": "outgoing", "max_hops": 3, "grade": "all"}},
                  {"action": "set_query", "query": {"from_entity": "node:review", "to_entity": "node:ingest"}},
                  {"action": "select_entity", "id": "node:render"}, {"action": "run_mutation", "id": cases[0]["id"]}]:
        log = append_event(log, event, catalog)
    return {"schema_version": 1, "synthetic": True, "fixture_digest": fingerprint, "analysis_digest": analysis,
            "tool_bundle": bundle, "plan": plan, "cases": cases, "catalog": catalog, "implementation": implementation,
            "source_tips": sources, "example_log": log, "example_state": replay(log, catalog, fingerprint, analysis),
            "scope": cases[0]["verification"]["scope"],
            "verification_mode": "The independent Python verifier ran at build time for every embedded XML case. The browser inspects those recorded results; it does not execute the Python verifier."}


def build(out_dir: str, xite: str | None = None, font: str | None = None, font_license: str | None = None) -> dict:
    source = Path(xite) if xite else None
    if source and any(not (source / f).is_file() for f in ("x_ite.min.js", "x_ite.css", "LICENSE.md")):
        raise ValueError("--xite must name a complete renderer distribution")
    if font and not Path(font).is_file():
        raise ValueError("--font must name a readable TTF font")
    if font_license and (not font or not Path(font_license).is_file()):
        raise ValueError("--font-license requires a font and a readable license file")
    bundle = build_bundle()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tools_source = (WEB.parent / "web" / "tools.js").read_text()
    tools_source = re.sub(r"^export (?=(?:const|function|async function)\b)", "", tools_source, flags=re.M)
    event_source = (WEB / "investigation.js").read_text()
    event_source = re.sub(r"^export (?=(?:const|class|function|async function)\b)", "", event_source, flags=re.M)
    inline = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    page = (WEB / "index.html").read_text().replace("/*__CSS__*/", (WEB / "proof.css").read_text())
    page = page.replace("/*__SHARED_TOOLS__*/", tools_source).replace("/*__EVENT_ENGINE__*/", event_source).replace("/*__APP__*/", (WEB / "proof.js").read_text())
    page = page.replace("__PROOF_DATA__", inline)
    (out / "index.html").write_text(page)
    (out / "observatory-data.json").write_text(json.dumps(bundle, ensure_ascii=True, indent=1))
    (out / "baseline.x3d").write_text(bundle["cases"][0]["xml"])
    shutil.copyfile(WEB.parent / "lab_web" / "start_lab.py", out / "start_lab.py")
    written = {"index.html", "observatory-data.json", "baseline.x3d"}
    if source:
        for p in sorted(source.rglob("*")):
            if not p.is_file() or p.is_symlink():
                continue
            rel = p.relative_to(source)
            if str(rel) not in {"x_ite.min.js", "x_ite.css", "LICENSE.md"} and not rel.parts[0] == "assets":
                continue
            dest = out / "x_ite" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
            written.add("x_ite/" + rel.as_posix())
    if font:
        target = out / "fonts" / "UbuntuMono-R.ttf"
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(font, target)
        written.add("fonts/UbuntuMono-R.ttf")
        if font_license:
            shutil.copyfile(font_license, target.parent / "LICENSE.txt")
            written.add("fonts/LICENSE.txt")
    assets = [{"path": rel, "sha256": hashlib.sha256((out / rel).read_bytes()).hexdigest(), "bytes": (out / rel).stat().st_size} for rel in sorted(written)]
    receipt = {"synthetic": True, "fixture_digest": bundle["fixture_digest"], "analysis_digest": bundle["analysis_digest"],
               "assets": assets, "controls": [{"path": "start_lab.py", "sha256": hashlib.sha256((out / "start_lab.py").read_bytes()).hexdigest()}],
               "scope": "Detached content receipt for the observatory export; not publisher authentication or full-family release readiness."}
    (out / "lab-receipt.json").write_text(json.dumps(receipt, indent=1))
    return {"html": str(out / "index.html"), "entities": len(bundle["plan"]["entities"]), "relations": len(bundle["plan"]["relations"]),
            "typed_records": len(bundle["tool_bundle"]["relations"]["records"]), "cases": len(bundle["cases"]), "fixture_digest": bundle["fixture_digest"]}
