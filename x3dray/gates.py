"""The gates: every check a build must pass before it is published. No model takes part.

  xsd       the scene validates against the X3D 4.0 schema (xmllint)
  loom      the Loom gate (x3d.onto's gobbler-demiurge/tools/loom_gate.py --scene), with the x3d_mcp fork's validator (--fork)
  fact      the spec renders with no problem: no digit outside a slot, every slot resolves, every claim is backed
  privacy   no address, session URL or secret-shaped string in any file this build wrote (vendored X_ITE is not ours to scan)
  witness   every tie the page or the scene can show carries evidence, and the scene draws no tie the bundle lacks
  tools     the page's tools run against the bundle and fit the sample limits, aliases resolve, and the MCP server answers over stdio
  live      Chromium under a strict CSP: the scene loads and draws, the scene tools work, a click opens a card, and Ask runs end to end
            against a stand-in for the sample runtime (the real one only runs inside Claude)

Each gate is pass, fail or skip (a tool it needs is missing). gates.json records them all; the page shows the verdict and how_built
returns it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess

from .page import EMAIL, SECRET, SESSION_URL, write_publish_map

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
OURS = ("index.html", "bundle.json", "app.js", "tools.js", "xray.x3d", "gates.json", "receipt.json")


def _run(cmd: list[str], env: dict | None = None, timeout: int = 900) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"


def _default(path: str | None, *candidates: str) -> str | None:
    if path:
        return path
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def gate_xsd(scene: str, xsd: str | None) -> dict:
    if not xsd or not shutil.which("xmllint"):
        return {"status": "skip", "summary": "xmllint or the X3D 4.0 XSD is missing"}
    rc, out, err = _run(["xmllint", "--noout", "--schema", xsd, scene])
    tail = (err or out).strip().splitlines()[-3:]
    return {"status": "pass" if rc == 0 else "fail", "summary": tail[-1] if tail else "", "xsd": os.path.basename(xsd)}


def gate_loom(scene: str, onto: str | None, fork: str | None) -> dict:
    tool = onto and os.path.join(onto, "gobbler-demiurge", "tools", "loom_gate.py")
    if not tool or not os.path.exists(tool):
        return {"status": "skip", "summary": "no x3d.onto clone with gobbler-demiurge/tools/loom_gate.py"}
    cmd = ["python3", "-I", tool, scene, "--scene"] + (["--fork"] if fork else [])
    rc, out, err = _run(cmd, env={"X3D_MCP": fork} if fork else None)
    lines = [x for x in (out + err).strip().splitlines() if x.strip()]
    m = re.search(r"(\d+) errors?, (\d+) warnings?", " ".join(lines[-3:]))
    errors, warnings = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    ok = rc == 0 and errors == 0
    return {"status": "pass" if ok else "fail", "summary": lines[-1] if lines else "", "errors": errors, "warnings": warnings,
            "fork_validator": bool(fork), "notes": lines[-4:]}


def gate_fact(data_dir: str) -> dict:
    sp = os.path.join(data_dir, "spec.rendered.json")
    if not os.path.exists(sp):
        return {"status": "fail", "summary": "no rendered spec"}
    r = json.load(open(sp, encoding="utf-8"))
    probs = r.get("problems", [])
    slots = sum(len(v.get("slots", [])) + len((v.get("claim") or {}).get("slots", [])) for v in r["views"]) + sum(len(t.get("slots", [])) for t in r["tour"])
    claims = sum(1 for v in r["views"] if v.get("claim"))
    return {"status": "pass" if not probs else "fail", "summary": f"{slots} slots filled by code, {claims} claims backed, {len(probs)} problems",
            "problems": probs[:20]}


def gate_privacy(out_dir: str) -> dict:
    hits = []
    for name in OURS:
        p = os.path.join(out_dir, name)
        if not os.path.exists(p):
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        for rx, what in ((EMAIL, "address"), (SESSION_URL, "session URL"), (SECRET, "secret-shaped string")):
            for m in rx.findall(text):
                hits.append(f"{name}: {what} {m[:3]}…")
    b = json.load(open(os.path.join(out_dir, "bundle.json"), encoding="utf-8"))
    priv = {r["name"] for r in b["repos"] if r.get("visibility") == "private"}
    leaked = [c for c in b["commits"] if c["r"] in priv and (c.get("h") or c.get("sub"))]
    if leaked:
        hits.append(f"bundle.json: {len(leaked)} commits of a private repo carry a sha or subject")
    if any(f for r, fs in b["files"].items() if r in priv for f in fs):
        hits.append("bundle.json: a private repo's file list")
    return {"status": "pass" if not hits else "fail", "summary": f"{len(hits)} findings in {len([n for n in OURS if os.path.exists(os.path.join(out_dir, n))])} files we wrote",
            "findings": hits[:20], "scanned": [n for n in OURS if os.path.exists(os.path.join(out_dir, n))]}


def gate_witness(out_dir: str) -> dict:
    b = json.load(open(os.path.join(out_dir, "bundle.json"), encoding="utf-8"))
    L = json.load(open(os.path.join(out_dir, "layout.json"), encoding="utf-8"))
    bad = [f"{t['from']} -> {t['to']}" for t in b["ties"]["machine"] if not t.get("evidence")]
    bad += [t["id"] for t in b["ties"]["curated"] if not t.get("evidence")]
    have = {(t["from"], t["to"]) for t in b["ties"]["machine"]}
    drawn = [f"{t['from']} -> {t['to']}" for t in L["ties"]["machine"] if (t["from"], t["to"]) not in have]
    cur = {t["id"] for t in b["ties"]["curated"]}
    drawn += [t["id"] for t in L["ties"]["curated"] if t["id"] not in cur]
    n = len(b["ties"]["machine"]) + len(b["ties"]["curated"])
    ok = not bad and not drawn
    return {"status": "pass" if ok else "fail",
            "summary": f"{n} ties, each with a witness; {b['ties'].get('dropped_without_witness', 0)} dropped for want of one; {len(drawn)} drawn without a record",
            "without_witness": bad[:10], "drawn_without_record": drawn[:10]}


def gate_tools(out_dir: str) -> dict:
    node = shutil.which("node")
    if not node:
        return {"status": "skip", "summary": "node is missing"}
    rc, out, err = _run([node, os.path.join(WEB, "selftest.mjs"), "--bundle", os.path.join(out_dir, "bundle.json")], timeout=120)
    try:
        r = json.loads(out)
    except json.JSONDecodeError:
        return {"status": "fail", "summary": "the self-test printed no result", "stderr": err[-600:]}
    return {"status": "pass" if r["ok"] else "fail", "summary": f"{r['checks'] - len(r['failed'])} of {r['checks']} checks pass",
            "failed": r["failed"], "checks": [c["name"] for c in r["all"]]}


def gate_live(out_dir: str) -> dict:
    node = shutil.which("node")
    if not node:
        return {"status": "skip", "summary": "node is missing"}
    env = {"NODE_PATH": os.pathsep.join(x for x in ["/opt/node-tools/node_modules", os.environ.get("NODE_PATH", "")] if x)}
    rc, out, err = _run([node, os.path.join(WEB, "live_gate.cjs"), "--site", out_dir, "--frames", os.path.join(out_dir, "frames")], env=env, timeout=1500)
    line = (out.strip().splitlines() or ["{}"])[-1]
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        r = {"ok": False, "error": (err or out)[-800:]}
    if "runs" not in r:
        if "Cannot find module 'playwright'" in (r.get("error") or ""):
            return {"status": "skip", "summary": "playwright is missing"}
        return {"status": "fail", "summary": "the live run did not finish", "error": r.get("error", "")[-800:]}
    runs = {x["name"]: {"ok": x["ok"], "failed": [k for k, v in x["checks"].items() if not v], "scene_ms": x.get("boot_ms"),
                        "labels": x.get("labels"), "frames": len(x.get("frames", []))} for x in r["runs"]}
    stub = next((x for x in r["runs"] if x.get("ask")), None)
    return {"status": "pass" if r["ok"] else "fail",
            "summary": ", ".join(f"{k} {'pass' if v['ok'] else 'FAIL: ' + ' '.join(v['failed'])}" for k, v in runs.items()),
            "runs": runs, "csp": r["csp"], "chromium": r.get("chromium"),
            "ask_stand_in": {"tools_called": [x["name"] for x in stub["ask"]["results"]], "prompt_bytes": stub["ask"]["prompt_bytes"],
                             "problems": stub["ask"]["problems"]} if stub else None,
            "caveat": "The host's real CSP is not visible from here, so a pass is Likely on claude.ai, not Confirmed. Ask ran against a stand-in, not Claude."}


def run_all(data_dir: str, out_dir: str, onto: str | None = None, fork: str | None = None, xsd: str | None = None, live: bool = True) -> dict:
    home = os.path.expanduser("~")
    onto = _default(onto, os.path.join(home, "hagopjay", "x3d.onto"), "/home/user/hagopjay/x3d.onto")
    fork = _default(fork, os.path.join(home, "hagopjay", "x3d_mcp"), "/home/user/hagopjay/x3d_mcp")
    xsd = _default(xsd, fork and os.path.join(fork, "src", "validation", "schemas", "x3d-4.0.xsd"))
    scene = os.path.join(out_dir, "xray.x3d")
    for stale in ("gates.json", "receipt.json"):   # a verdict from an earlier run must not be served while this one runs
        if os.path.exists(os.path.join(out_dir, stale)):
            os.remove(os.path.join(out_dir, stale))
    write_publish_map(out_dir)
    g = {}
    g["xsd"] = gate_xsd(scene, xsd)
    g["loom"] = gate_loom(scene, onto, fork)
    g["fact"] = gate_fact(data_dir)
    g["privacy"] = gate_privacy(out_dir)
    g["witness"] = gate_witness(out_dir)
    g["tools"] = gate_tools(out_dir)
    g["live"] = gate_live(out_dir) if live else {"status": "skip", "summary": "skipped with --no-live"}
    ok = all(v["status"] != "fail" for v in g.values())
    res = {"ok": ok, "ran_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "skipped": [k for k, v in g.items() if v["status"] == "skip"], "gates": g}
    with open(os.path.join(out_dir, "gates.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    write_publish_map(out_dir)
    return res
