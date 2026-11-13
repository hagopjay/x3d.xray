#!/usr/bin/env python3
"""Adversarial static-scene checks using a real synthetic Git build.

Every mutation changes emitted XML while keeping the scene plan and bundle
unchanged. No real project history, browser rendering or GPU evidence is used.
Run: python3 experiments/scene_mutation_audit.py --output-json results.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.smoke_synthetic_pipeline import fixture_repo, write_json
from x3dray import compiler, harvest, page, profile, selfdesc, spec, structure, ties
from x3dray.scene_mutations import mutate_cases


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_fixture() -> tuple[str, dict, dict]:
    """Use generic repository names to test the actual producer boundary."""
    with tempfile.TemporaryDirectory(prefix="xray-scene-audit-") as temporary:
        root = Path(temporary)
        sources, data, out = (root / n for n in ("sources", "data", "out"))
        for p in (sources, data, out):
            p.mkdir()
        cfg = {"family": "synthetic-scene-audit", "owner": "synthetic", "title": "Synthetic scene audit",
               "identities": [{"label": "Pat", "class": "you", "match_names": ["Pat"]},
                              {"label": "Claude", "class": "ai", "match_names": ["Claude"]}],
               "default_identity": {"label": "unmapped", "class": "upstream"},
               "repos": [{"name": name, "dir": name, "kind": "own", "visibility": "public", "aliases": [name]}
                         for name in ("alpha", "beta")]}
        cfg["repos"][0].update({"observer": True, "uses": {"beta": "Synthetic declared tool use"}})
        for i, name in enumerate(("alpha", "beta")):
            fixture_repo(sources / name, i)
        internal = harvest.harvest_family(cfg, str(sources))
        found = ties.find_ties(cfg, internal)
        dataset = harvest.strip_internal(internal)
        dataset["harvested_at"] = "2026-01-05T00:00:00Z"
        curated = {"ties": [{"id": "AUDIT", "from": "beta", "to": "alpha", "grade": "L", "kind": "association",
                              "what": "Synthetic attributed association", "direction": "unspecified",
                              "evidence": ["Synthetic assertion in the audit fixture"]}], "nodes": [], "series": []}
        con, bri, problems = structure.run(dataset)
        if problems:
            raise AssertionError("Synthetic analytical fixture did not verify")
        prof = profile.profile_family(dataset, found, curated, con, bri)
        description = selfdesc.describe(dataset, prof, "2026-01-05T00:00:00Z", cfg)
        narrative = {"family": cfg["family"], "title": cfg["title"], "views": [{"id": "overview", "label": "Overview",
                     "caption": "{family_span.commits} synthetic commits."}], "tour": [], "questions": []}
        rendered = spec.render_spec(narrative, prof, description)
        if rendered["problems"]:
            raise AssertionError("Synthetic narrative fixture did not verify")
        for name, value in (("dataset.json", dataset), ("ties.json", found), ("curated.json", curated),
                            ("concepts.json", con), ("bridges.json", bri), ("profile.json", prof),
                            ("self.json", description), ("spec.rendered.json", rendered)):
            write_json(data / name, value)
        compiler.build(str(data), str(out))
        bundle = page.make_bundle(str(data), str(out), cfg)
        layout = json.loads((out / "layout.json").read_text())
        return (out / "xray.x3d").read_text(), layout["scene_plan"], bundle



def run_audit() -> dict:
    from x3dray import scenecheck
    xml, plan, bundle = build_fixture()
    plan_digest, bundle_digest = _digest(plan), _digest(bundle)
    baseline = scenecheck.verify_scene(xml, plan, bundle)
    rows = []
    for case in mutate_cases(xml)[1:]:
        started = perf_counter()
        result = scenecheck.verify_scene(case["xml"], plan, bundle)
        observed = bool(result.get("ok"))
        expected = "scope_probe" if case["scope_probe"] else "accept" if case["expected_ok"] else "reject"
        passed = observed == case["expected_ok"] if case["expected_ok"] is not None else None
        rows.append({"id": case["id"], "expected": expected, "accepted": observed, "expectation_met": passed,
                     "finding_codes": sorted({f.get("code", "unknown") for f in result.get("findings", [])}),
                     "findings": result.get("findings", [])[:12], "seconds": round(perf_counter() - started, 6),
                     "scene_sha256": hashlib.sha256(case["xml"].encode()).hexdigest(), "note": case["description"]})
    immutable = _digest(plan) == plan_digest and _digest(bundle) == bundle_digest
    return {"synthetic_only": True, "ok": bool(baseline.get("ok")) and immutable and all(r["expectation_met"] is not False for r in rows),
            "scope": "Independent static constellation geometry/witness checks. No browser, occlusion, runtime-event or GPU claim.",
            "fixture": {"repos": [r["name"] for r in bundle["repos"]], "commits": len(bundle["commits"]),
                        "planned_entities": len(plan["entities"]), "planned_relations": len(plan["relations"]),
                        "plan_sha256": plan_digest, "bundle_sha256": bundle_digest,
                        "scene_sha256": hashlib.sha256(xml.encode()).hexdigest()},
            "baseline": baseline, "source_inputs_unchanged": immutable, "mutations": rows,
            "rejections_tested": sum(r["expected"] == "reject" for r in rows),
            "rejections_observed": sum(r["expected"] == "reject" and not r["accepted"] for r in rows),
            "benign_equivalences_accepted": sum(r["expected"] == "accept" and r["accepted"] for r in rows),
            "scope_probes": [r["id"] for r in rows if r["expected"] == "scope_probe"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    result = run_audit()
    if args.output_json:
        write_json(args.output_json.resolve(), result)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
