"""CLI for the binary x-ray lens. Additive: `python3 -m x3dray.binary <cmd>`.

  harvest   <bin> --out DIR      triage/sections/functions/imports/strings + witnesses
  callgraph <bin> --out DIR      call edges graded + betweenness gatekeeper
  capability<bin> --out DIR      functions x capabilities -> FCA lattice
  passage   <bin> --to NAME --out DIR   passage points from entry to a function
  scene     <bin> --out DIR [--font TTF]   emit xray.x3d
  verify    --out DIR            re-run every witness, check hashes
  all       <bin> --out DIR [--font TTF]   the whole chain + a summary
            [--to NAME] [--ledger SPEC] [--claims SPEC] [--shim SRC] [--oracle-n N]
            Pass B: with --ledger, re-runs every hypothesis as an experiment
            (static-decode, strace/gdb trace, native oracle), draws the
            investigation helix, runs the RE gates and writes page.html
  ledger    <bin> --ledger SPEC --out DIR [--shim SRC] [--oracle-n N]
  gates     <bin> --out DIR --ledger SPEC --claims SPEC   re-run every RE gate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from . import harvest_bin, callgraph, capability, scene_bin, witnesses, hypotheses, gates_bin, page_bin


def _write(out_dir: str, name: str, obj) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        if name.endswith(".json"):
            json.dump(obj, fh, indent=1, sort_keys=True)
        else:
            fh.write(obj)
    return path


def _load(out_dir: str, name: str):
    with open(os.path.join(out_dir, name), encoding="utf-8") as fh:
        return json.load(fh)


def _digest(*objs) -> str:
    h = hashlib.sha256()
    for o in objs:
        h.update(json.dumps(o, sort_keys=True, ensure_ascii=True).encode())
    return h.hexdigest()[:16]


def _harvest(binp):
    disasm, _ = harvest_bin._objdump_disasm(binp)
    return harvest_bin.harvest(binp), disasm


def cmd_harvest(a):
    b, _ = _harvest(a.binary)
    print(_write(a.out, "binary.json", b))
    _summary_line(b)
    return 0


def cmd_callgraph(a):
    b, disasm = _harvest(a.binary)
    _write(a.out, "binary.json", b)
    g = callgraph.build(b, disasm)
    print(_write(a.out, "callgraph.json", g))
    print(f"  gatekeeper (max betweenness): {g['gatekeeper']}")
    return 0


def cmd_capability(a):
    b, _ = _harvest(a.binary)
    cap = capability.analyse(b)
    print(_write(a.out, "capability.json", cap))
    if cap["lattice"]:
        caps = sorted({c for v in cap["context"].values() for c in v})
        print(f"  capabilities found: {', '.join(caps)}")
        print(f"  concepts: {len(cap['lattice']['concepts'])}, "
              f"implications: {len(cap['lattice']['implications'])}")
    return 0


def cmd_passage(a):
    b, disasm = _harvest(a.binary)
    g = callgraph.build(b, disasm)
    res = callgraph.passage(g, a.to)
    print(json.dumps(res, indent=1))
    return 0


def cmd_scene(a):
    b, disasm = _harvest(a.binary)
    g = callgraph.build(b, disasm)
    xml = scene_bin.build_scene(b, g, font=a.font, source_digest=_digest(b, g))
    print(_write(a.out, "xray.x3d", xml))
    return 0


def cmd_verify(a):
    b = _load(a.out, "binary.json")
    results = []
    for name, w in (b.get("witnesses") or {}).items():
        if not w:
            continue
        results.append({"name": name, **witnesses.verify(w)})
    ok = all(r["status"] == "match" for r in results if r["status"] != "unavailable")
    report = {"ok": ok, "results": results}
    print(_write(a.out, "verify.json", report))
    for r in results:
        print(f"  {r['name']:10} {r['status']}")
    return 0 if ok else 1


def _spec(path):
    if not path:
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def cmd_ledger(a):
    _b, disasm = _harvest(a.binary)
    spec = _spec(a.ledger)
    L = hypotheses.run_ledger(a.binary, spec, out_dir=a.out, disasm=disasm,
                              shim_src=a.shim, oracle_n=a.oracle_n)
    _write(a.out, "ledger.json", L)
    hypotheses.write_csv(L, os.path.join(a.out, "hypotheses.csv"))
    for r in L["rows"]:
        imp = r["imported_verdict"] or "-"
        print(f"  {r['id']:3} {r['verdict']:10} (imported {imp:9}) {json.dumps(r['observed'])[:96]}")
    print(f"  tally: {L['tally']}")
    return 0 if not L["tally"]["REFUTED"] else 1


def cmd_gates(a):
    res = gates_bin.run_all(a.out, a.binary, _spec(a.claims), _spec(a.ledger))
    _write(a.out, "gates.json", res)
    for k, g in res["gates"].items():
        print(f"  {k:11} {g['status']:5} {g['summary']}")
        for p in g.get("problems", [])[:5]:
            print(f"      - {p}")
    return 0 if res["ok"] else 1


def cmd_all(a):
    b, disasm = _harvest(a.binary)
    g = callgraph.build(b, disasm)
    if a.to:
        g["passage"] = callgraph.passage(g, a.to)
    cap = capability.analyse(b)
    ledger_spec = _spec(a.ledger)
    L = None
    if ledger_spec:
        L = hypotheses.run_ledger(a.binary, ledger_spec, out_dir=a.out, disasm=disasm,
                                  shim_src=a.shim, oracle_n=a.oracle_n)
        _write(a.out, "ledger.json", L)
        hypotheses.write_csv(L, os.path.join(a.out, "hypotheses.csv"))
    xml = scene_bin.build_scene(b, g, font=a.font, source_digest=_digest(b, g, cap["context"]),
                                ledger=L)
    _write(a.out, "binary.json", b)
    _write(a.out, "callgraph.json", g)
    _write(a.out, "capability.json", cap)
    _write(a.out, "xray.x3d", xml)
    receipt = {"target": b["target"], "source_digest": _digest(b, g, cap["context"]),
               "functions": len(b["functions"]), "edges": len(g["edges"]),
               "gatekeeper": g["gatekeeper"],
               "capabilities": sorted({c for v in cap["context"].values() for c in v}),
               "witness_digests": {k: (w or {}).get("output_sha256") for k, w in (b["witnesses"] or {}).items()}}
    _write(a.out, "receipt.json", receipt)
    print(f"binary x-ray complete -> {a.out}")
    _summary_line(b)
    print(f"  gatekeeper: {g['gatekeeper']}   edges: {len(g['edges'])}   "
          f"capabilities: {', '.join(receipt['capabilities'])}")
    print(f"  scene: {os.path.join(a.out, 'xray.x3d')}   receipt: {receipt['source_digest']}")
    rc = cmd_verify(argparse.Namespace(out=a.out))
    if L:
        print(f"  ledger: {L['tally']}")
        claims = _spec(a.claims)
        res = gates_bin.run_all(a.out, a.binary, claims or {"claims": []}, ledger_spec)
        _write(a.out, "gates.json", res)
        for k, gg in res["gates"].items():
            print(f"  gate {k:11} {gg['status']:5} {gg['summary']}")
        rendered = gates_bin.render_claims(claims or {"claims": []}, gates_bin.slot_values(a.out), a.out)
        _write(a.out, "claims.json", rendered)
        html = page_bin.build_page(b, g, cap, L, res, rendered)
        print(f"  page: {_write(a.out, 'page.html', html)}")
        rc = rc or (0 if res["ok"] else 1)
    return rc


def _summary_line(b):
    t = b["triage"]
    print(f"  {t['arch']}, {'stripped' if t['stripped'] else 'symbols'}, "
          f"{'PIE' if t['pie'] else 'no-PIE'}; {len(b['functions'])} functions, "
          f"{len(b['imports'])} imports")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="x3dray.binary", description="binary x-ray lens (Pass A)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("harvest", "callgraph", "capability", "scene", "all"):
        q = sub.add_parser(name)
        q.add_argument("binary")
        q.add_argument("--out", default="out/bin-xray")
        if name in ("scene", "all"):
            q.add_argument("--font", default=None)
        if name == "all":
            q.add_argument("--to", default=None)
            q.add_argument("--ledger", default=None)
            q.add_argument("--claims", default=None)
            q.add_argument("--shim", default=None)
            q.add_argument("--oracle-n", type=int, default=None)
    q = sub.add_parser("passage"); q.add_argument("binary"); q.add_argument("--to", required=True); q.add_argument("--out", default="out/bin-xray")
    q = sub.add_parser("verify"); q.add_argument("--out", default="out/bin-xray")
    q = sub.add_parser("ledger"); q.add_argument("binary"); q.add_argument("--ledger", required=True)
    q.add_argument("--out", default="out/bin-xray"); q.add_argument("--shim", default=None)
    q.add_argument("--oracle-n", type=int, default=None)
    q = sub.add_parser("gates"); q.add_argument("binary"); q.add_argument("--out", default="out/bin-xray")
    q.add_argument("--ledger", required=True); q.add_argument("--claims", required=True)
    a = p.parse_args(argv)
    fn = {"harvest": cmd_harvest, "callgraph": cmd_callgraph, "capability": cmd_capability,
          "passage": cmd_passage, "scene": cmd_scene, "verify": cmd_verify, "all": cmd_all,
          "ledger": cmd_ledger, "gates": cmd_gates}[a.cmd]
    try:
        return fn(a)
    except witnesses.ToolMissing as e:
        print(f"x3dray.binary: required tool not found: {e}. Install GNU binutils "
              f"(objdump, nm) and 'file'.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
