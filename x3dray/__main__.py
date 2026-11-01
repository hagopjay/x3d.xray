"""Command line: python -m x3dray <step> ...

Steps, in pipeline order (all but `spec --brief` and `mcp` write into --data or --out):
  harvest  --family F --root DIR --data DIR    git clones -> dataset.json + ties.json (no model)
  curated  --masterclass DIR --data DIR        the fork masterclass's graded ties and series -> curated.json
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
    return json.load(open(p, encoding="utf-8"))


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_harvest(a) -> int:
    from . import harvest, ties

    cfg = json.load(open(a.family, encoding="utf-8"))
    t0 = time.time()
    ds = harvest.harvest_family(cfg, a.root)
    t1 = time.time()
    tie = ties.find_ties(cfg, ds)
    t2 = time.time()
    out = harvest.strip_internal(ds)
    out["harvested_at"] = a.stamp or _now()
    out["timing_s"] = {"harvest": round(t1 - t0, 1), "ties": round(t2 - t1, 1)}
    _write_json(os.path.join(a.data, "dataset.json"), out)
    _write_json(os.path.join(a.data, "ties.json"), tie)
    n_commits = sum(len(r.get("commits", [])) for r in out["repos"])
    print(f"harvest: {len(out['repos'])} repos, {n_commits} commits, {len(tie['pairs'])} tie pairs in {t2 - t0:.1f}s -> {a.data}")
    return 0


def cmd_curated(a) -> int:
    from . import curated

    cfg = json.load(open(a.family, encoding="utf-8")) if a.family else {}
    exclude = tuple(a.exclude or [])
    cur = curated.import_masterclass(a.masterclass, exclude=exclude, notes=cfg.get("curated_notes"))
    _write_json(os.path.join(a.data, "curated.json"), cur)
    print(f"curated: {len(cur['ties'])} graded ties, {len(cur['series'])} series -> {a.data}/curated.json")
    return 0


def cmd_profile(a) -> int:
    from . import profile

    prof = profile.profile_family(_read(a.data, "dataset.json"), _read(a.data, "ties.json"), _read(a.data, "curated.json", {}) or None)
    _write_json(os.path.join(a.data, "profile.json"), prof)
    print(f"profile: {len(prof['signals'])} signals -> {a.data}/profile.json")
    return 0


def cmd_self(a) -> int:
    from . import selfdesc

    cfg = json.load(open(a.family, encoding="utf-8")) if a.family else None
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
    rendered = spec.render_spec(json.load(open(a.spec, encoding="utf-8")), prof, selfd)
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
    print("gate: " + ("pass" if res["ok"] else "FAIL"))
    return 0 if res["ok"] else 1


def cmd_receipt(a) -> int:
    from . import receipt

    rec = receipt.write(a.data, a.out, a.stamp or _now())
    print(f"receipt: {rec['digest']} over {len(rec['files'])} files -> {a.out}/receipt.json")
    return 0


def cmd_all(a) -> int:
    steps = [
        (cmd_harvest, {}), (cmd_curated, {}), (cmd_profile, {}), (cmd_self, {}), (cmd_spec, {"brief": False}),
        (cmd_self, {}), (cmd_build, {}), (cmd_page, {}), (cmd_gate, {}), (cmd_receipt, {}),
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

    h = sp("harvest", cmd_harvest, data, stamp)
    h.add_argument("--family", required=True)
    h.add_argument("--root", required=True, help="the folder the repos are cloned under")
    c = sp("curated", cmd_curated, data, fam)
    c.add_argument("--masterclass", required=True)
    c.add_argument("--exclude", nargs="*", help="repos to leave out (for a public build)")
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
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
