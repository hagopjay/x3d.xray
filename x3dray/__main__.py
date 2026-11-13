"""Command line: python -m x3dray <step> ...

Steps, in pipeline order (all but `spec --brief` and `mcp` write into --data or --out):
  harvest  --family F --root DIR --data DIR    git clones -> dataset.json + ties.json (no model)
  curated  --masterclass DIR --data DIR        the fork masterclass's graded ties and series -> curated.json
  structure --data DIR                         concepts (formal concept analysis) and bridges (betweenness) -> concepts.json, bridges.json (no model)
  eda      --data DIR                          data audit, robust profiles, flags and 12 declared looks (contrasts and drifts) with graded robustness -> eda.json (no model)
  profile  --data DIR                          -> profile.json: signals, each with its numbers (no model)
  spec     --data DIR --spec S                 fill the spec's slots from the profile -> spec.rendered.json; fails on a stray digit
  spec     --data DIR --brief                  print what an AI needs to write a spec: rules, schema, every slot and its value
  self     --data DIR                          the tool's description of itself -> self.json
  build    --data DIR --out DIR                -> xray.x3d + layout.json (no model)
  page     --data DIR --out DIR --xite D --font F   -> the Artifact folder: index.html, bundle.json, page code, X_ITE, font
  gate     --data DIR --out DIR [--onto D --fork D]  XSD, Loom gate, fork validator, fact, privacy, tools, live run -> gates.json
  receipt  --data DIR --out DIR                -> receipt.json: sha256 of inputs, code, outputs and gate results
  all      --family F --root DIR --masterclass DIR --data DIR --out DIR --spec S --xite D --font F [--onto D --fork D]
  tools                                        print the tools the page's Claude and the MCP server share
  mcp      --out DIR                           serve the data tools over MCP (stdio) from DIR/bundle.json
  lab      --out DIR [--xite D]                 synthetic, offline bridge investigation laboratory
  observatory --out DIR [--xite D --font F]     geometry audit, typed traversal and replayable investigations
  verify-scene --scene X --plan P --bundle B   independent static constellation verification
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def _write_json(path: str, obj, compact: bool = False) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def _read(d: str, name: str, default=None):
    p = os.path.join(d, name)
    if not os.path.exists(p):
        if default is not None:
            return default
        raise SystemExit(f"{p} is missing: run the step that writes it first")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_harvest(a) -> int:
    from . import harvest, ties

    with open(a.family, encoding="utf-8") as fh:
        cfg = json.load(fh)
    t0 = time.time()
    ds = harvest.harvest_family(cfg, a.root)
    ds["harvested_at"] = a.stamp or _now()
    t1 = time.time()
    tie = ties.find_ties(cfg, ds)
    t2 = time.time()
    out = harvest.strip_internal(ds)
    out["harvested_at"] = ds["harvested_at"]
    out["timing_s"] = {"harvest": round(t1 - t0, 1), "ties": round(t2 - t1, 1)}
    _write_json(os.path.join(a.data, "dataset.json"), out)
    _write_json(os.path.join(a.data, "ties.json"), tie)
    n_commits = sum(len(r.get("commits", [])) for r in out["repos"])
    print(f"harvest: {len(out['repos'])} repos, {n_commits} commits, {len(tie['pairs'])} tie pairs in {t2 - t0:.1f}s -> {a.data}")
    return 0


def cmd_curated(a) -> int:
    from . import curated

    cfg = {}
    if a.family:
        with open(a.family, encoding="utf-8") as fh:
            cfg = json.load(fh)
    exclude = tuple(a.exclude or [])
    cur = curated.import_masterclass(a.masterclass, exclude=exclude, notes=cfg.get("curated_notes"))
    _write_json(os.path.join(a.data, "curated.json"), cur)
    print(f"curated: {len(cur['ties'])} graded ties, {len(cur['series'])} series -> {a.data}/curated.json")
    return 0


def cmd_structure(a) -> int:
    from . import structure

    t0 = time.time()
    con, bri, problems = structure.run(_read(a.data, "dataset.json"))
    _write_json(os.path.join(a.data, "concepts.json"), con, compact=True)
    _write_json(os.path.join(a.data, "bridges.json"), bri, compact=True)
    f, s = con["files_by_author_class"], bri["sessions"]
    print(f"structure: {f['n_concepts']} concepts over {f['n_files']} files, {len(con['areas']['rules'])} area rules, "
          f"{s['n_sessions']} sessions ({s['n_multi_repo']} in more than one repo), {len(s['passage_points'])} repo pairs with a passage point "
          f"in {time.time() - t0:.1f}s -> {a.data}")
    for p in problems[:20]:
        print("  " + p)
    return 1 if problems else 0


def cmd_eda(a) -> int:
    from . import eda

    t0 = time.time()
    try:
        out, problems = eda.run(_read(a.data, "dataset.json"))
    except eda.DatasetError as exc:
        print(f"eda: {exc}")
        return 1
    _write_json(os.path.join(a.data, "eda.json"), out, compact=True)
    ledger, cov = out["looks"]["ledger"], out["coverage"]
    by = ledger["by_grade"]
    flagged = len({row[0] for rule in out["flags"]["by_rule"].values() for g in ("C", "L", "S") for row in rule[g]})
    print(f"eda: {cov['commits']} commits, {ledger['declared']} declared looks ({by['C']} Confirmed, {by['L']} Likely, "
          f"{by['S']} Suspected, {by['none'] - ledger['not_run']} without a grade, {ledger['not_run']} not run), {flagged} flagged commits "
          f"in {time.time() - t0:.1f}s -> {a.data}/eda.json")
    for p in problems[:20]:
        print("  " + p)
    return 1 if problems else 0


def cmd_profile(a) -> int:
    from . import profile

    prof = profile.profile_family(_read(a.data, "dataset.json"), _read(a.data, "ties.json"), _read(a.data, "curated.json", {}) or None,
                                  _read(a.data, "concepts.json", {}) or None, _read(a.data, "bridges.json", {}) or None,
                                  eda=_read(a.data, "eda.json", {}) or None)
    _write_json(os.path.join(a.data, "profile.json"), prof)
    print(f"profile: {len(prof['signals'])} signals -> {a.data}/profile.json")
    return 0


def cmd_self(a) -> int:
    from . import selfdesc

    cfg = None
    if a.family:
        with open(a.family, encoding="utf-8") as fh:
            cfg = json.load(fh)
    sd = selfdesc.describe(_read(a.data, "dataset.json"), _read(a.data, "profile.json"), a.stamp or _now(), cfg)
    _write_json(os.path.join(a.data, "self.json"), sd)
    print(f"self: {sd['tool']['modules']} modules, code {sd['tool']['code_digest']}, head {sd['tool']['git']['head']} -> {a.data}/self.json")
    return 0


def cmd_spec(a) -> int:
    from . import spec

    prof = _read(a.data, "profile.json")
    selfd = _read(a.data, "self.json", {})
    if a.brief:
        print(spec.brief(prof, selfd))
        return 0
    with open(a.spec, encoding="utf-8") as fh:
        rendered = spec.render_spec(json.load(fh), prof, selfd)
    rendered["source"] = os.path.relpath(a.spec)
    _write_json(os.path.join(a.data, "spec.rendered.json"), rendered)
    n = len(rendered["problems"])
    print(f"spec: {len(rendered['views'])} views, {len(rendered['tour'])} tour stops, {n} problem(s) -> {a.data}/spec.rendered.json")
    for p in rendered["problems"]:
        print("  " + p)
    return 1 if n else 0


def cmd_build(a) -> int:
    from . import compiler

    report = compiler.build(a.data, a.out)
    print("build: " + json.dumps(report))
    return 0


def cmd_page(a) -> int:
    from . import page

    rep = page.build_page(a.data, a.out, xite=a.xite, font=a.font)
    print(f"page: {rep['files']} files, {rep['bytes']:,} bytes -> {a.out}")
    return 0


def cmd_gate(a) -> int:
    from . import gates

    res = gates.run_all(a.data, a.out, onto=a.onto, fork=a.fork, xsd=a.xsd, live=not a.no_live)
    for k, v in res["gates"].items():
        print(f"  {k:10} {v['status']:5} {v.get('summary', '')}")
    print("gate: development " + ("pass" if res["ok"] else "FAIL") + "; release " + ("ready" if res["release_ready"] else "incomplete"))
    return 0 if (res["release_ready"] if getattr(a, "release", False) else res["ok"]) else 1


def cmd_receipt(a) -> int:
    from . import receipt

    rec = receipt.write(a.data, a.out, a.stamp or _now())
    print(f"receipt: {rec['digest']} over {len(rec['files'])} files -> {a.out}/receipt.json")
    return 0


def cmd_all(a) -> int:
    from .staging import run_staged

    # Freeze run time and self-description before rendering narrative slots.
    # Every run starts with fresh data, so omitted optional stages cannot reuse
    # artifacts from the previous family harvest.
    a.stamp = a.stamp or _now()
    def runner(stage_data, stage_out):
        staged = SimpleNamespace(**vars(a))
        staged.data, staged.out = stage_data, stage_out
        return _all_steps(staged)
    return run_staged(a.data, a.out, runner)


def _all_steps(a) -> int:
    steps = [
        (cmd_harvest, {}), (cmd_curated, {}), (cmd_structure, {}), (cmd_eda, {}), (cmd_profile, {}), (cmd_self, {}), (cmd_spec, {"brief": False}),
        (cmd_build, {}), (cmd_page, {}), (cmd_gate, {}), (cmd_receipt, {}),
    ]
    if not a.masterclass:
        steps = [s for s in steps if s[0] is not cmd_curated]
    for fn, extra in steps:
        for k, v in extra.items():
            setattr(a, k, v)
        rc = fn(a)
        if rc:
            print(f"all: stopped at {fn.__name__[4:]}")
            return rc
    return 0


def cmd_lab(a) -> int:
    from . import lab
    print("lab: " + json.dumps(lab.build(a.out, a.xite)))
    return 0


def cmd_observatory(a) -> int:
    from . import prooflab
    print("observatory: " + json.dumps(prooflab.build(a.out, a.xite, a.font, a.font_license, a.benchmark)))
    return 0


def cmd_fixture_sources(a) -> int:
    from . import prooflab
    cfg, dataset, found, _, _ = prooflab.fixture_inputs(a.out)
    _write_json(os.path.join(a.out, "family.json"), cfg)
    _write_json(os.path.join(a.out, "dataset.json"), dataset)
    audit = found["locator_audit"]
    _write_json(os.path.join(a.out, "source-audit.json"), audit)
    for i, record in enumerate(audit["records"]):
        _write_json(os.path.join(a.out, "witnesses", f"{i:04d}.json"), record["locator"])
    print(json.dumps({"synthetic": True, "sources": a.out, "public_locators_checked": audit["checked"],
                      "scope": "Four deterministic synthetic local Git repositories; no remote repositories are contacted."}))
    return 0


def cmd_verify_witness(a) -> int:
    from .locators import MAX_BYTES, verify_locator
    try:
        if os.path.getsize(a.locator) > MAX_BYTES:
            raise ValueError("locator input limit")
        with open(a.locator, encoding="utf-8") as fh:
            locator = json.load(fh)
        result = verify_locator(a.repo, locator, repo_id=a.repo_id)
    except (OSError, ValueError, TypeError) as exc:
        result = {"ok": False, "error_type": type(exc).__name__, "scope": "Locator inputs could not be read"}
    print(json.dumps(result, ensure_ascii=True, indent=1))
    return 0 if result["ok"] else 1


def cmd_scale(a) -> int:
    from .scaling import benchmark
    result = benchmark(sizes=tuple(a.sizes), topologies=tuple(a.topologies), repeats=a.repeats, warmups=a.warmups,
                       output_dir=os.path.join(a.out, "scale-fixtures"), jsdom_modules=a.jsdom_modules)
    path = os.path.join(a.out, "scale-benchmark.json")
    _write_json(path, result)
    print(json.dumps({"benchmark": path, "rows": len(result["rows"]), "disclosure": result["disclosure"]}))
    return 0


def cmd_verify_scene(a) -> int:
    from .scenecheck import MAX_XML_BYTES, verify_scene
    try:
        if any(os.path.getsize(p) > MAX_XML_BYTES for p in (a.scene, a.plan, a.bundle)):
            raise ValueError("bounded input size exceeded")
        with open(a.plan, encoding="utf-8") as fh:
            plan = json.load(fh)
        with open(a.bundle, encoding="utf-8") as fh:
            bundle = json.load(fh)
        with open(a.scene, encoding="utf-8") as fh:
            result = verify_scene(fh.read(), plan.get("scene_plan", plan), bundle)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result = {"ok": False, "error_type": type(exc).__name__, "scope": "Inputs could not be read as the static scene contract"}
    print(json.dumps(result, ensure_ascii=True, indent=1))
    return 0 if result["ok"] else 1


def _node() -> str:
    n = shutil.which("node")
    if not n:
        raise SystemExit("node is needed for the tools and the MCP server (they are the page's own tools.js)")
    return n


def cmd_tools(a) -> int:
    return subprocess.call([_node(), os.path.join(WEB, "mcp.mjs"), "--list"])


def cmd_mcp(a) -> int:
    return subprocess.call([_node(), os.path.join(WEB, "mcp.mjs"), "--bundle", os.path.join(a.out, "bundle.json")])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="x3dray", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def sp(name, fn, *args):
        q = sub.add_parser(name)
        for a_ in args:
            a_(q)
        q.set_defaults(fn=fn)
        return q

    data = lambda q: q.add_argument("--data", required=True, help="the data folder (dataset.json, ties.json, ...)")
    out = lambda q: q.add_argument("--out", required=True, help="the scene and page folder")
    stamp = lambda q: q.add_argument("--stamp", default=None, help="ISO time to record instead of now")
    fam = lambda q: q.add_argument("--family", default=None, help="the family config")
    xite = lambda q: q.add_argument("--xite", required=True, help="X_ITE dist folder to vendor")
    font = lambda q: q.add_argument("--font", required=True, help="TTF font for the scene's labels")

    def gate_args(q):
        q.add_argument("--onto", default=None, help="an x3d.onto clone, for gobbler-demiurge/tools/loom_gate.py")
        q.add_argument("--fork", default=None, help="an x3d_mcp fork clone, for its validator")
        q.add_argument("--xsd", default=None, help="the X3D 4.0 XSD (default: the fork's copy)")
        q.add_argument("--no-live", action="store_true", help="skip the live X_ITE run")
        q.add_argument("--release", action="store_true", help="require every mandatory gate to pass; skipped checks are incomplete")

    h = sp("harvest", cmd_harvest, data, stamp)
    h.add_argument("--family", required=True)
    h.add_argument("--root", required=True, help="the folder the repos are cloned under")
    c = sp("curated", cmd_curated, data, fam)
    c.add_argument("--masterclass", required=True)
    c.add_argument("--exclude", nargs="*", help="repos to leave out (for a public build)")
    sp("structure", cmd_structure, data)
    sp("eda", cmd_eda, data)
    sp("profile", cmd_profile, data)
    sp("self", cmd_self, data, stamp, fam)
    s = sp("spec", cmd_spec, data)
    s.add_argument("--spec", default=None)
    s.add_argument("--brief", action="store_true")
    sp("build", cmd_build, data, out)
    sp("page", cmd_page, data, out, xite, font)
    sp("gate", cmd_gate, data, out, gate_args)
    sp("receipt", cmd_receipt, data, out, stamp)
    al = sp("all", cmd_all, data, out, stamp, xite, font, gate_args)
    al.add_argument("--family", required=True)
    al.add_argument("--root", required=True)
    al.add_argument("--masterclass", default=None)
    al.add_argument("--exclude", nargs="*")
    al.add_argument("--spec", required=True)
    sp("tools", cmd_tools)
    sp("mcp", cmd_mcp, out)
    lab_p = sp("lab", cmd_lab, out)
    lab_p.add_argument("--xite", default=None, help="optional pinned X_ITE dist folder to vendor for offline 3D")
    observatory = sp("observatory", cmd_observatory, out)
    observatory.add_argument("--xite", default=None, help="optional local X_ITE distribution for offline 3D")
    observatory.add_argument("--font", default=None, help="optional local TTF for X3D text labels")
    observatory.add_argument("--font-license", default=None, help="font redistribution notice to include alongside a supplied font")
    observatory.add_argument("--benchmark", default=None, help="measured scale-benchmark JSON to embed and copy associated fixtures")
    sp("fixture-sources", cmd_fixture_sources, out)
    witness = sp("verify-witness", cmd_verify_witness)
    witness.add_argument("--repo", required=True, help="caller-bound local Git checkout")
    witness.add_argument("--repo-id", required=True, help="expected canonical repository ID from a trusted checkout map")
    witness.add_argument("--locator", required=True, help="immutable Git blob locator JSON")
    scale = sp("scale", cmd_scale, out)
    scale.add_argument("--sizes", nargs="+", type=int, default=[10, 100, 1000])
    scale.add_argument("--topologies", nargs="+", choices=["chain", "modular", "branching"], default=["chain", "modular", "branching"])
    scale.add_argument("--repeats", type=int, default=7)
    scale.add_argument("--warmups", type=int, default=2)
    scale.add_argument("--jsdom-modules", default=None, help="node_modules directory containing jsdom for disclosed SVG DOM timings")
    verify = sp("verify-scene", cmd_verify_scene)
    verify.add_argument("--scene", required=True)
    verify.add_argument("--plan", required=True, help="scene-plan JSON or layout.json")
    verify.add_argument("--bundle", required=True)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
