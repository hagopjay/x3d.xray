#!/usr/bin/env python3
"""Build the published Explorer demo: the real build stages on temporary synthetic Git repositories, with a
two-view narrative (Overview and the Looks gallery), then keep the served page in --keep.

Example:
  python3 experiments/build_pages_explorer.py --xite /path/to/x_ite/dist --font /path/to/a-font.ttf --keep /tmp/explorer

No gate result is mocked. XSD, Loom, family-specific tools, and live rendering
are explicitly unrun. All repositories, harvested records and build assets are
temporary; only an optional summary survives. The current exhibition requires
the fixture names x3d_mcp/x3d.xray. They contain no actual project history.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from x3dray import compiler, eda, gates, harvest, page, profile, publication, receipt, selfdesc, spec, structure, ties


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=1) + "\n", encoding="utf-8")


def git(repo: Path, *args, env=None):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
        text=True, env={**os.environ, **(env or {})}, check=True)
    return result.stdout.strip()


def fixture_repo(path: Path, offset: int):
    import random
    rng = random.Random(100 + offset)
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "synthetic@example.invalid")
    stamp = dt.datetime(2026, 1, 2, 0, tzinfo=dt.timezone.utc)
    t = stamp
    for i in range(80):
        ai = rng.random() < 0.5
        git(path, "config", "user.name", "Claude" if ai else "Pat")
        (path / "src").mkdir(exist_ok=True)
        n = rng.randint(2, 8) if ai else rng.randint(20, 90)
        (path / "src" / f"module{i % 5}.py").write_text("\n".join(f"# synthetic line {j} of revision {i}" for j in range(n)) + "\n")
        (path / "README.md").write_text(f"Synthetic integration fixture only. Revision {i}\n")
        git(path, "add", "--all")
        t = t.replace(hour=0, minute=0) + dt.timedelta(days=rng.choice([0, 1, 1, 2]), hours=(rng.randint(20, 23) if ai else rng.randint(8, 17)), minutes=rng.randint(0, 59))
        at = t.isoformat()
        message = f"Synthetic fixture change {i}"
        if ai:
            message += "\n\nClaude-Session: https://claude.ai/code/session_syntheticshared"
        git(path, "commit", "-q", "-m", message, env={"GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at})


KEEP = None


def run(xite: Path, font: Path):
    global KEEP
    result = {"synthetic_only": True, "ok": False, "release_ready": False,
        "stages": [], "checks": {}, "not_run": ["XSD schema validation", "Loom validation",
        "family-specific tools self-test", "live X_ITE/browser rendering", "hardware GPU measurement"],
        "scope": "Real Git ingestion and deterministic build functions; three actual local gates; publication byte coverage. No browser-rendering claim."}
    current = None

    def stage(name, fn):
        nonlocal current
        current = name
        start = time.perf_counter()
        value = fn()
        result["stages"].append({"stage": name, "status": "pass", "seconds": round(time.perf_counter() - start, 6)})
        return value

    try:
        with tempfile.TemporaryDirectory(prefix="xray-synthetic-pipeline-") as temporary:
            root = Path(temporary)
            source, data, out = root / "sources", root / "data", root / "out"
            source.mkdir()
            data.mkdir()
            out.mkdir()
            cfg = {"family": "synthetic-pipeline", "title": "Synthetic integration fixture", "owner": "synthetic",
                "identities": [{"label": "Pat", "class": "you", "match_names": ["Pat"]},
                               {"label": "Claude", "class": "ai", "match_names": ["Claude"]}],
                "default_identity": {"label": "unmapped", "class": "upstream"},
                "repos": [{"name": name, "dir": name, "kind": "own", "visibility": "public",
                           "aliases": [name], "role": "synthetic fixture"} for name in ("x3d_mcp", "x3d.xray")]}
            cfg["repos"][1].update({"observer": True, "uses": {"x3d_mcp": "Synthetic declared validator use"}})
            stage("real_git_fixtures", lambda: [fixture_repo(source / name, k) for k, name in enumerate(("x3d_mcp", "x3d.xray"))])
            internal = stage("harvest", lambda: harvest.harvest_family(cfg, str(source)))
            relationships = stage("tie_detection", lambda: ties.find_ties(cfg, internal))
            dataset = harvest.strip_internal(internal)
            dataset["harvested_at"] = "2026-01-05T00:00:00Z"
            write_json(data / "dataset.json", dataset)
            write_json(data / "ties.json", relationships)
            con, bri, problems = stage("structure", lambda: structure.run(dataset))
            if problems:
                raise AssertionError("structure verification findings: " + "; ".join(problems[:3]))
            write_json(data / "concepts.json", con)
            write_json(data / "bridges.json", bri)
            eda_out, eda_problems = stage("eda", lambda: eda.run(dataset))
            if eda_problems:
                raise AssertionError("eda verification findings: " + "; ".join(eda_problems[:3]))
            (data / "eda.json").write_text(json.dumps(eda_out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
            prof = stage("profile", lambda: profile.profile_family(dataset, relationships, None, con, bri, eda=eda_out))
            write_json(data / "profile.json", prof)
            description = stage("self_description", lambda: selfdesc.describe(dataset, prof, "2026-01-05T00:00:00Z", cfg))
            write_json(data / "self.json", description)
            narrative = {"family": cfg["family"], "title": "Synthetic integration fixture",
                "views": [{"id": "overview", "label": "Overview",
                    "caption": "{family_span.commits} own commits in the synthetic fixture.",
                    "claim": {"text": "{family_span.commits} own commits were observed.",
                              "backs": ["family_span"], "basis": "git"}},
                    {"id": "gallery", "label": "Looks",
                    "caption": "Each panel is one of the {eda_findings.declared} declared looks, in order. Bars: effect, share of label shuffles at least as extreme, and q, all on one scale. Cubes: fork, leave-one-repo-out, repo and halves checks, hollow if one could not run. {eda_findings.confirmed} Confirmed, {eda_findings.likely} Likely, {eda_findings.suspected} Suspected, {eda_findings.without_grade} ungraded, {eda_findings.not_run} not run.",
                    "claim": {"text": "{eda_findings.declared} looks were declared before any were run, and {eda_findings.confirmed} of them are Confirmed.", "backs": ["eda_findings"], "basis": "git"}}],
                "tour": [{"view": "overview", "title": "Synthetic overview", "caption": "{family_span.commits} commits measured from temporary Git repositories."}],
                "questions": ["Which recorded sessions connect these repositories?"]}
            rendered = stage("spec_render", lambda: spec.render_spec(narrative, prof, description))
            if rendered["problems"]:
                raise AssertionError("spec rendering findings: " + "; ".join(rendered["problems"]))
            write_json(data / "spec.rendered.json", rendered)
            scene_report = stage("x3d_compile", lambda: compiler.build(str(data), str(out)))
            stage("xml_well_formedness", lambda: ET.parse(out / "xray.x3d"))
            page_report = stage("page_export", lambda: page.build_page(str(data), str(out), str(xite), str(font), cfg, standalone=True))
            verdicts = {}
            for name, fn in (("fact", lambda: gates.gate_fact(str(data))),
                             ("privacy", lambda: gates.gate_privacy(str(out))),
                             ("witness", lambda: gates.gate_witness(str(out), str(data)))):
                verdicts[name] = stage(name + "_gate", fn)
                if verdicts[name]["status"] != "pass":
                    raise AssertionError(name + " gate: " + verdicts[name]["summary"])
            for name in ("xsd", "loom", "tools", "live"):
                verdicts[name] = {"status": "skip", "summary": "Not invoked by this synthetic pipeline smoke experiment"}
            write_json(out / "gates.json", {"ok": True, "release_ready": False,
                "ran_at": "2026-01-05T00:00:00Z", "skipped": ["xsd", "loom", "tools", "live"], "gates": verdicts})
            sealed = stage("receipt", lambda: receipt.write(str(data), str(out), "2026-01-05T00:00:00Z"))
            current = "publication_hash_and_extra_asset_check"
            selected = publication.assets(str(out))
            published = json.loads((out / "publish.json").read_text())
            served_paths = set(published) | {"index.html"}
            selected_paths = {a["path"] for a in selected}
            hashes = {f["path"]: f["sha256"] for f in sealed["files"] if f["role"] in {"output", "vendored"}}
            unattested = sorted(served_paths - {"receipt.json"} - set(hashes))
            mismatched = sorted(rel for rel in served_paths - {"receipt.json"}
                if hashes.get(rel) != hashlib.sha256((out / rel).read_bytes()).hexdigest())
            if served_paths != selected_paths or unattested or mismatched:
                raise AssertionError("publication selected set / receipt hashes do not agree")
            # This file never receives registration; neither the manifest nor
            # receipt should grant it permission merely because it exists.
            (out / "unexpected.json").write_text('{"synthetic_extra": true}')
            page.write_publish_map(str(out))
            after = json.loads((out / "publish.json").read_text())
            if "unexpected.json" in after:
                raise AssertionError("unregistered extra file was published")
            if gates.gate_privacy(str(out))["status"] != "pass":
                raise AssertionError("final registered set failed privacy gate")
            result["stages"].append({"stage": "publication_hash_and_extra_asset_check", "status": "pass"})
            result["checks"] = {"fixture_repositories": 2,
                "source_git_head": description["tool"]["git"]["head"],
                "source_has_uncommitted_changes": description["tool"]["git"]["uncommitted_changes"],
                "source_code_digest": description["tool"]["code_digest"],
                "harvested_own_commits": sum(r["own_commits"] for r in dataset["repos"]),
                "expected_own_commits": 16, "x_ite_version": json.loads((out / "bundle.json").read_text())["meta"]["x_ite"],
                "gates": {k: v["status"] for k, v in verdicts.items()},
                "scene": {k: v for k, v in scene_report.items() if k != "scene"},
                "page": page_report, "served_assets": len(served_paths),
                "receipt_covered_served_assets": len(served_paths - {"receipt.json"}),
                "detached_receipt_exception": "receipt.json deliberately excludes its own byte hash",
                "unattested": unattested, "mismatched": mismatched,
                "unregistered_extra_published": False,
                "temporary_fixture_deleted_on_exit": True}
            if False:
                raise AssertionError("wrong synthetic commit population")
            import shutil
            shutil.copytree(out, KEEP, dirs_exist_ok=True)
            result["ok"] = True
    except Exception as exc:
        result["failed_stage"] = current
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xite", required=True, type=Path)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--keep", required=True, type=Path)
    args = parser.parse_args()
    global KEEP
    KEEP = args.keep.resolve()
    summary = run(args.xite.resolve(), args.font.resolve())
    if args.output_json:
        write_json(args.output_json.resolve(), summary)
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
