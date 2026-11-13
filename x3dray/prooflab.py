"""Build the synthetic Architecture Observatory with real static-scene audits.

The specimen is harvested from temporary real Git repositories. XML mutations
are checked by the independent verifier at build time. The browser inspects
those recorded results and uses the production shared architecture query tool.
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
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


def fixture_inputs(source_dir=None):
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
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        if stamp:
            env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        return subprocess.run(["git", "-c", "core.hooksPath="+os.devnull, "-c", "init.templateDir=", "-C", str(path), *args], env=env, check=True, capture_output=True, text=True).stdout.strip()
    context = tempfile.TemporaryDirectory(prefix="xray-observatory-") if source_dir is None else nullcontext(str(Path(source_dir).resolve()))
    with context as temp:
        root = Path(temp)
        if source_dir is not None:
            root.mkdir(parents=True, exist_ok=True)
            if any(root.iterdir()):
                raise ValueError("synthetic source output must be an empty directory")
        for k, (name, files) in enumerate(sources.items()):
            path = root / name
            path.mkdir()
            git(path, "init", "-q", "-b", "main")
            git(path, "config", "commit.gpgsign", "false")
            git(path, "config", "core.autocrlf", "false")
            git(path, "config", "user.email", "fixture@example.invalid")
            for i in range(6):
                git(path, "config", "user.name", "Fixture Author" if i % 2 == 0 else "Fixture Assistant")
                for rel, text in files.items():
                    (path / rel).write_text(text, encoding="utf-8", newline="\n")
                (path / "revision.txt").write_text(f"Synthetic {name} revision {i}\n", encoding="utf-8", newline="\n")
                git(path, "add", "--all")
                when = dt.datetime(2026, 1, 3, 8 + i, k, tzinfo=dt.timezone.utc).isoformat()
                message = f"Synthetic {name} observation {i}"
                if i % 2 and name in {"evidence", "render"}:
                    message += "\n\nClaude-Session: https://claude.ai/code/session_syntheticproof"
                git(path, "commit", "-qm", message, stamp=when)
        internal = harvest.harvest_family(cfg, str(root))
        internal["harvested_at"] = "2026-01-04T00:00:00Z"
        discovered = ties.find_ties(cfg, internal)
        from .locators import verify_locator
        by_identity = {r.get("repo_id"): r for r in internal["repos"] if r.get("repo_id")}
        checked = []
        for pair in discovered["pairs"]:
            for witness in pair["evidence"]:
                for locator in ([witness["locator"]] if witness.get("locator") else witness.get("locators", [])):
                    source = by_identity.get(locator["repo_id"])
                    if source:
                        result = verify_locator(source["_path"], locator, repo_id=source["repo_id"])
                        checked.append({"locator": locator, "verification": result})
                        if not result["ok"]:
                            raise ValueError("synthetic immutable source verification failed")
        discovered["locator_audit"] = {"checked": len(checked), "records": checked,
                                       "scope": "Actual local Git object checks before temporary source repositories are discarded; not remote ownership authentication."}
        if checked:
            altered = copy.deepcopy(checked[0]["locator"])
            digest = altered["span"]["content_sha256"]
            altered["span"]["content_sha256"] = ("0" if digest[0] != "0" else "1") + digest[1:]
            source = by_identity[altered["repo_id"]]
            discovered["locator_audit"]["tamper_probe"] = verify_locator(source["_path"], altered, repo_id=source["repo_id"])
            if discovered["locator_audit"]["tamper_probe"]["ok"]:
                raise ValueError("synthetic immutable witness tamper probe was not rejected")
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


def temporal_fixture():
    """Disclosed interval assertions; never inferred from commit timestamps."""
    repos = [{"name": name, "visibility": "public"} for name in ("alpha", "beta", "gamma", "delta")]
    windows = [
        ("EARLY", "alpha", "beta", "2026-01-02T00:00:00Z", {"start": "2026-01-01T00:00:00Z", "end": "2026-01-03T00:00:00Z"}),
        ("LATE", "beta", "gamma", "2026-01-05T00:00:00Z", {"start": "2026-01-03T00:00:00Z", "end": "2026-01-05T00:00:00Z"}),
        ("KNOWN", "alpha", "delta", "2026-01-02T00:00:00Z", {"start": "2026-01-01T00:00:00Z", "end": "2026-01-05T00:00:00Z"}),
        ("UNKNOWN", "delta", "gamma", "2026-01-02T00:00:00Z", None),
    ]
    records = [{"id": key, "from": a, "to": b, "kind": "synthetic interval assertion", "grade": "S", "observed_at": observed, "valid_time": interval,
                "evidence": ["Controlled synthetic interval assertion for a temporal counterexample; not inferred from Git author dates."]}
               for key, a, b, observed, interval in windows]
    graph = relations.build_relations({"family": "synthetic-time-counterexample", "harvested_at": "2026-01-06T00:00:00Z", "repos": repos}, {"pairs": []}, {"ties": records})
    return {"relations": graph, "repos": repos, "vocab": {r["name"]: r["name"] for r in repos}, "synthetic": True,
            "description": "The early and late intervals touch at an exclusive boundary and never overlap. The alternative route contains unknown validity. Observation dates tell when claims were recorded, not when they became true."}


def build_bundle(scale_results=None, scale_previews=None) -> dict:
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
    from .witness_contract import scene_sources
    bundle["scene_sources"] = scene_sources(dataset, found, curated)
    plan = layout["scene_plan"]
    cases = audit_cases(xml, plan, bundle)
    if not cases[0]["verification"]["ok"]:
        raise ValueError("baseline synthetic scene failed independent geometry verification")
    fingerprint = canonical_digest({"dataset": dataset, "ties": found, "curated": curated})
    time_fixture = temporal_fixture()
    implementation = {path.relative_to(WEB.parent).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in sorted(WEB.parent.rglob("*")) if path.is_file() and not path.is_symlink()
                      and path.suffix in {".py", ".js", ".mjs", ".cjs", ".html", ".css"} and "__pycache__" not in path.parts}
    analysis = canonical_digest({"implementation": implementation, "plan": plan, "relations": graph, "temporal_fixture": time_fixture, "scale_results": scale_results, "scale_previews": scale_previews,
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
            "source_tips": sources, "source_verification": found["locator_audit"], "temporal_fixture": time_fixture, "scale_results": scale_results, "scale_previews": scale_previews,
            "example_log": log, "example_state": replay(log, catalog, fingerprint, analysis),
            "scope": cases[0]["verification"]["scope"],
            "verification_mode": "The independent Python verifier ran at build time for every embedded XML case. The browser inspects those recorded results; it does not execute the Python verifier."}


def build(out_dir: str, xite: str | None = None, font: str | None = None, font_license: str | None = None, benchmark: str | None = None) -> dict:
    source = Path(xite) if xite else None
    if source and any(not (source / f).is_file() for f in ("x_ite.min.js", "x_ite.css", "LICENSE.md")):
        raise ValueError("--xite must name a complete renderer distribution")
    if font and not Path(font).is_file():
        raise ValueError("--font must name a readable TTF font")
    if font_license and (not font or not Path(font_license).is_file()):
        raise ValueError("--font-license requires a font and a readable license file")
    scale_results = None
    scale_previews, scale_assets = [], []
    if benchmark:
        if Path(benchmark).stat().st_size > 8 * 1024 * 1024:
            raise ValueError("benchmark report exceeds the bounded input size")
        scale_results = json.loads(Path(benchmark).read_text())
        if not isinstance(scale_results, dict) or not isinstance(scale_results.get("rows"), list) or len(scale_results["rows"]) > 100:
            raise ValueError("benchmark must contain a bounded rows array")
        directory = scale_results.get("artifact_directory")
        if not isinstance(directory, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", directory) or directory in {".", ".."}:
            raise ValueError("benchmark artifact directory must be a local child name")
        artifacts = Path(benchmark).resolve().parent / directory
        if artifacts.is_symlink() or artifacts.resolve().parent != Path(benchmark).resolve().parent:
            raise ValueError("benchmark artifact directory must remain beside its report")
        total_bytes = 0
        for row in scale_results["rows"]:
            for key in ("fixture", "scene"):
                name = row["artifacts"][key]
                path = artifacts / name
                if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) or path.is_symlink() or not path.is_file() or path.resolve().parent != artifacts.resolve():
                    raise ValueError("benchmark artifact must be a regular local file")
                total_bytes += path.stat().st_size
                if total_bytes > 128 * 1024 * 1024:
                    raise ValueError("benchmark artifact byte budget exceeded")
                scale_assets.append((path, "scale/" + name))
            if row["topology"] == "modular":
                fixture = json.loads((artifacts / row["artifacts"]["fixture"]).read_text())
                preview = {"size": row["size"], "topology": row["topology"], "entities": fixture["plan"]["entities"],
                           "relations": [{k: e[k] for k in ("id", "from", "to", "points", "grade")} for e in fixture["plan"]["relations"]],
                           "scene": "scale/" + row["artifacts"]["scene"]}
                scale_previews.append(preview)
    bundle = build_bundle(scale_results, scale_previews)
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
    for source_path, relative in scale_assets:
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
        written.add(relative)
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
