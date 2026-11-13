"""The gates: every check a build must pass before it is published. No model takes part.

  xsd       the scene validates against the X3D 4.0 schema (xmllint)
  loom      the Loom gate (x3d.onto's gobbler-demiurge/tools/loom_gate.py --scene), with the x3d_mcp fork's validator (--fork)
  fact      the spec renders with no problem: no digit outside a slot, every slot resolves, every claim is backed
  privacy   no address, session URL or secret-shaped string in any file this build wrote (vendored X_ITE is not ours to scan), and no
            private repo itemised: no sha, subject, file, path or area of one, in the commits or the concepts, bridges and eda
            sections, and every sha the eda audit cites is a public commit's
  witness   every tie the page or the scene can show carries evidence, and the scene draws no tie the bundle lacks; the concepts and
            bridges the page serves match the data folder, which must match a fresh run of the stage on dataset.json, and pass the
            stage's own check again (every cited sha is a commit that did what it is cited for, every count re-derived from the
            commits), and the scene draws no concept, session or bridge file they lack; the eda section is held to the same
            standard (bundle, data folder and a fresh run agree, the independent check is clean, and the bundle's commit rows are
            the rows the stage indexed its flags by)
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
from . import publication

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
REQUIRED_RELEASE_GATES = ("xsd", "loom", "fact", "privacy", "witness", "scene", "tools", "live")


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
    tail = (err or out).replace(scene, os.path.basename(scene)).strip().splitlines()[-3:]    # the scene by name, not this build's path
    return {"status": "pass" if rc == 0 else "fail", "summary": tail[-1] if tail else "", "xsd": os.path.basename(xsd)}


def gate_loom(scene: str, onto: str | None, fork: str | None) -> dict:
    tool = onto and os.path.join(onto, "gobbler-demiurge", "tools", "loom_gate.py")
    if not tool or not os.path.exists(tool):
        return {"status": "skip", "summary": "no x3d.onto clone with gobbler-demiurge/tools/loom_gate.py"}
    cmd = ["python3", "-I", tool, scene, "--scene"] + (["--fork"] if fork else [])
    rc, out, err = _run(cmd, env={"X3D_MCP": fork} if fork else None)
    lines = [x for x in (out + err).replace(scene, os.path.basename(scene)).strip().splitlines() if x.strip()]
    m = re.search(r"(\d+) errors?, (\d+) warnings?", " ".join(lines[-3:]))
    errors, warnings = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    ok = rc == 0 and errors == 0
    return {"status": "pass" if ok else "fail", "summary": lines[-1] if lines else "", "errors": errors, "warnings": warnings,
            "fork_validator": bool(fork), "notes": lines[-4:]}


def gate_fact(data_dir: str) -> dict:
    sp = os.path.join(data_dir, "spec.rendered.json")
    if not os.path.exists(sp):
        return {"status": "fail", "summary": "no rendered spec"}
    from .spec import verify_rendered_spec
    probs = verify_rendered_spec(data_dir)
    # Counts are a readout only; the verdict comes from re-rendering the saved
    # source spec against current profile/self inputs, not a cached problem list.
    try:
        with open(sp, encoding="utf-8") as fh:
            r = json.load(fh)
        views = [v for v in r.get("views", []) if isinstance(v, dict)] if isinstance(r, dict) else []
        tour = [t for t in r.get("tour", []) if isinstance(t, dict)] if isinstance(r, dict) else []
        slots = sum(len(v.get("slots") or []) + len((v.get("claim") or {}).get("slots") or []) for v in views)
        slots += sum(len(t.get("slots") or []) for t in tour)
        claims = sum(1 for v in views if v.get("claim"))
    except (ValueError, TypeError, AttributeError):
        slots, claims = 0, 0
    return {"status": "pass" if not probs else "fail", "summary": f"{slots} slots, {claims} evidence-linked claims, {len(probs)} consistency problems",
            "problems": probs[:20], "scope": "Schema, slot/reference validity and current-input re-rendering; natural-language entailment is not proved."}


def gate_privacy(out_dir: str) -> dict:
    hits = []
    try:
        selected = publication.assets(out_dir)
    except (ValueError, OSError, json.JSONDecodeError):
        return {"status": "fail", "summary": "invalid publication asset registry", "findings": ["publication asset validation failed"], "scanned": []}
    scanned = [a["path"] for a in selected if a["role"] == "owned"]
    for name in scanned:
        p = publication.safe_path(out_dir, name)
        with open(p, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for rx, what in ((EMAIL, "address"), (SESSION_URL, "session URL"), (SECRET, "secret-shaped string")):
            for m in rx.findall(text):
                hits.append(f"{name}: {what} {m[:3]}…")
    with open(os.path.join(out_dir, "bundle.json"), encoding="utf-8") as fh:
        b = json.load(fh)
    priv = {r["name"] for r in b["repos"] if r.get("visibility") == "private"}
    leaked = [c for c in b["commits"] if c["r"] in priv and (c.get("h") or c.get("sub"))]
    if leaked:
        hits.append(f"bundle.json: {len(leaked)} commits of a private repo carry a sha or subject")
    if any(f for r, fs in b["files"].items() if r in priv for f in fs):
        hits.append("bundle.json: a private repo's file list")
    from . import structure
    nodes = structure.node_ids({c["r"] for c in b["commits"]}, {c["w"] for c in b["commits"] if c.get("w")},
                               {s for c in b["commits"] for s in (c.get("s") or [])})       # the graphs' nodes, from the commits
    for sec in ("concepts", "bridges", "eda"):
        hits += [f"bundle.json: {t}" for t in _itemised(b.get(sec) or {}, priv, sec, nodes)][:10]
    if b.get("eda"):
        hits += _eda_privacy(b, priv)
    return {"status": "pass" if not hits else "fail", "summary": f"{len(hits)} findings in {len(scanned)} registered owned assets",
            "findings": hits[:20], "scanned": scanned,
            "vendored_attested": [a["path"] for a in selected if a["role"] == "vendored"]}


def _eda_privacy(b: dict, priv: set) -> list[str]:
    """The eda section names commits by index and by public sha only: an audit witness must be the sha of a public commit
    in the bundle and a flag must point at a commit row that exists. A malformed section is a finding, not a crash."""
    eda = b["eda"]
    hits = []
    public = {c["h"] for c in b["commits"] if c["r"] not in priv and c.get("h")}
    try:
        for chk in eda["audit"]["checks"]:
            if any(w not in public for w in chk.get("witnesses") or []):
                hits.append("bundle.json: eda audit names a commit hash that is not a public commit")
                break
        n = len(b["commits"])
        for rule in eda["flags"]["by_rule"].values():
            if any(not (isinstance(row, list) and row and type(row[0]) is int and 0 <= row[0] < n) for g in ("C", "L", "S") for row in rule[g]):
                hits.append("bundle.json: eda flags refer to a commit index outside the bundle's commits")
                break
    except (KeyError, TypeError, AttributeError):
        hits.append("bundle.json: the eda section could not be read by the privacy gate")
    if _marker_prefixed(eda):
        hits.append("bundle.json: eda has a key or string that starts with the private-repo marker")
    return hits


def _marker_prefixed(x) -> bool:
    """The stage withholds a private repo's name behind a marker; a key or string that starts with the marker and a path
    separator is a path that was withheld badly."""
    if isinstance(x, dict):
        return any(_marker_prefixed(k) or _marker_prefixed(v) for k, v in x.items())
    if isinstance(x, list):
        return any(_marker_prefixed(v) for v in x)
    return isinstance(x, str) and x.startswith(("<private>:", "<private>/"))


def _itemised(x, priv: set, trail: str, nodes=frozenset()) -> list[str]:
    """Where a section names a private repo's file, path or area (a key or a string that starts with repo:path or
    repo/area, as every id does, and is not one of the graphs' node ids), or lists a private record's shas. The stage's own
    check uses the same function."""
    from . import structure
    return structure.itemised(x, priv, trail, nodes)


FIXED_TEXT = ("checks", "csp", "chromium", "caveat", "scanned", "vendored_attested")   # the gates' own words: check names, the page's CSP, versions


def _guard(g: dict, priv: set) -> list[str]:
    """gates.json is written after the privacy gate ran, so its own text is checked here before it is written. On a hit, each
    gate keeps its status and a summary, the summary withheld too if it carries the hit, and the privacy gate fails. The
    fields that hold only the gates' own words are not scanned: a private repo named like a word in them (data, tools,
    files) would fail every build, and no data reaches them."""
    from . import structure
    rx = structure.private_rx(priv)

    def text_of(v):
        if isinstance(v, dict):
            return {k: text_of(w) for k, w in v.items() if k not in FIXED_TEXT}
        return [text_of(w) for w in v] if isinstance(v, list) else v

    def hits(text: str) -> list[str]:
        out = [f"gates: {what}" for r_, what in ((EMAIL, "address"), (SESSION_URL, "session URL"), (SECRET, "secret-shaped string")) if r_.search(text)]
        return out + (["gates: a private repo's file or area"] if rx and rx.search(text) else [])
    view = text_of(g)
    own = _itemised(view, priv, "gates") + hits(json.dumps(view, ensure_ascii=False))
    if own:
        for k in list(g):
            summ = str(g[k].get("summary", ""))                # tested whole, then cut: a cut could split what it carries
            seen = hits(summ) or hits(json.dumps(summ, ensure_ascii=False))       # as written, and as the scan above read it
            g[k] = {"status": g[k]["status"], "summary": "(withheld: it carried what the privacy gate looks for)" if seen else summ[:200]}
        g["privacy"] = {"status": "fail", "summary": f"{len(own)} findings in the gate results themselves (details dropped)",
                        "findings": sorted(set(own))[:10]}
    return own


def _structure_witness(b: dict, L: dict, data_dir: str | None) -> tuple[list[str], str]:
    con, bri = b.get("concepts"), b.get("bridges")
    if not con or not bri:
        return [], "no concepts or bridges in this build"
    from . import structure
    probs = []
    if data_dir and os.path.exists(os.path.join(data_dir, "dataset.json")):
        with open(os.path.join(data_dir, "dataset.json"), encoding="utf-8") as fh:
            rows = structure.commit_rows(json.load(fh))
        try:
            probs += structure.verify(con, bri, rows)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as e:
            probs.append(f"the stage's check could not read the concepts or bridges ({type(e).__name__})")
        fresh = json.loads(json.dumps({"concepts.json": structure.concepts(rows), "bridges.json": structure.bridges(rows)}))
        for name, sec in (("concepts.json", con), ("bridges.json", bri)):
            p = os.path.join(data_dir, name)
            disk = None
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    disk = json.load(fh)
            if disk != sec:
                probs.append(f"the bundle's {name[:-5]} differ from {name} in the data folder")
            elif fresh[name] != sec:
                probs.append(f"{name} is stale: a fresh run of the stage on dataset.json gives something else")
    else:
        probs.append("no dataset.json to check the concepts and bridges against")
    F, S = con["files_by_author_class"], bri["sessions"]
    lc, lb = L.get("concepts") or {}, L.get("bridges") or {}
    n_fc, n_rc = len(F["concepts"]), len(con["repos_by_author_class"]["concepts"])
    probs += [f"scene draws file concept {c['i']}, which the bundle lacks" for c in lc.get("files", []) if not 0 <= c["i"] < n_fc]
    probs += [f"scene draws repo concept {c['i']}, which the bundle lacks" for c in lc.get("repos", []) if not 0 <= c["i"] < n_rc]
    probs += [f"scene draws session {sid}, which the bundle lacks" for sid in lb.get("sessions", {}) if sid not in S["sessions"]]
    in_sess = {r for rec in S["sessions"].values() for r in rec["repos"]}
    probs += [f"scene draws {r} in the session graph without a session" for r in lb.get("repos", {}) if r not in in_sess]
    V = bri["files"]["variants"]
    probs += [f"scene draws bridge file {f['repo']}:{f['file']} ({f['variant']}), which the bundle lacks" for f in lb.get("files", [])
              if f"{f['repo']}:{f['file']}" not in V["count" if f["variant"] == "hub" else "jaccard"]["per_file"]]
    drawn = len(lc.get("files", [])) + len(lc.get("repos", [])) + len(lb.get("sessions", {})) + len(lb.get("files", []))
    return probs, f"{drawn} concepts, sessions and bridge files drawn, each in the bundle; structure check {'clean' if not probs else 'FAILED'}"


def _eda_witness(b: dict, data_dir: str | None) -> tuple[list[str], str]:
    """The eda section the page serves, the eda.json in the data folder and a fresh run of the stage on dataset.json must be
    one and the same, the stage's independent check must be clean, and the bundle's commit rows must be the rows the stage
    indexed its flags by (a flag is a row index)."""
    sec = b.get("eda")
    path = os.path.join(data_dir, "eda.json") if data_dir else None
    disk = None
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            disk = json.load(fh)
    if not sec and not disk:
        return [], "no eda in this build"
    if not sec or not disk:
        return ["eda is in the " + ("bundle but not in the data folder" if sec else "data folder but not in the bundle")], "eda present on one side only; check FAILED"
    from . import eda
    probs = []
    # Compared as canonical text, not as Python objects: 400 == 400.0 and 0 == False there.
    def same(a, b) -> bool:
        try:
            return eda.canonical_json(a) == eda.canonical_json(b)
        except (TypeError, ValueError):     # NaN, infinity or a key that cannot be ordered: not the file the stage writes
            return False

    if not same(disk, sec):
        probs.append("the bundle's eda differs from eda.json in the data folder")
    ds_path = os.path.join(data_dir, "dataset.json")
    findings = flagged = None
    if not os.path.exists(ds_path):
        probs.append("no dataset.json to check the eda section against")
    else:
        with open(ds_path, encoding="utf-8") as fh:
            ds = json.load(fh)
        try:
            fresh, vprobs = eda.run(ds)
        except eda.DatasetError as exc:
            probs.append(f"the eda stage cannot run on dataset.json: {exc}")
        else:
            probs += vprobs
            if not same(json.loads(json.dumps(fresh)), sec):
                probs.append("eda.json is stale: a fresh run of the stage on dataset.json gives something else")
            cols = ("i", "r", "t", "c", "a", "d", "f", "m", "b")
            try:
                if [tuple(r[k] for k in cols) for r in b["commits"]] != [tuple(x[k] for k in cols) for x in eda.indexed_rows(ds)]:
                    probs.append("the bundle's commits are not the rows the eda stage indexed its flags by")
            except (KeyError, TypeError):
                probs.append("the bundle's commits could not be compared with the eda stage's rows")
    try:
        findings = len(sec["looks"]["findings"])
        flagged = sum(r["counts"][g] for r in sec["flags"]["by_rule"].values() for g in "CLS")
    except (KeyError, TypeError):
        probs.append("the eda section is malformed")
    return probs, f"eda recomputed: {findings} findings, {flagged} flagged commits; check {'clean' if not probs else 'FAILED'}"


def gate_witness(out_dir: str, data_dir: str | None = None) -> dict:
    if not os.path.isfile(os.path.join(out_dir, "xray.x3d")):
        return {"status": "fail", "summary": "xray.x3d is missing", "without_witness": [], "drawn_without_record": [], "structure": [],
                "scope": "Requires an emitted scene; semantic correspondence below is checked against layout metadata."}
    with open(os.path.join(out_dir, "bundle.json"), encoding="utf-8") as fh:
        b = json.load(fh)
    with open(os.path.join(out_dir, "layout.json"), encoding="utf-8") as fh:
        L = json.load(fh)
    bad = [f"{t['from']} -> {t['to']}" for t in b["ties"]["machine"] if not t.get("evidence")]
    bad += [t["id"] for t in b["ties"]["curated"] if not t.get("evidence")]
    have = {(t["from"], t["to"]) for t in b["ties"]["machine"]}
    drawn = [f"{t['from']} -> {t['to']}" for t in L["ties"]["machine"] if (t["from"], t["to"]) not in have]
    cur = {t["id"] for t in b["ties"]["curated"]}
    drawn += [t["id"] for t in L["ties"]["curated"] if t["id"] not in cur]
    n = len(b["ties"]["machine"]) + len(b["ties"]["curated"])
    sprobs, ssum = _structure_witness(b, L, data_dir)
    eprobs, esum = _eda_witness(b, data_dir)
    sprobs, ssum = sprobs + eprobs, f"{ssum}; {esum}"
    ok = not bad and not drawn and not sprobs
    return {"status": "pass" if ok else "fail",
            "summary": f"{n} ties, each with a witness; {b['ties'].get('dropped_without_witness', 0)} dropped for want of one; {len(drawn)} drawn without a record; {ssum}",
            "without_witness": bad[:10], "drawn_without_record": drawn[:10], "structure": sprobs[:20],
            "scope": "Scene presence and layout/bundle/source witnesses; independent scene geometry correspondence is not yet checked."}


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


def gate_scene(out_dir: str) -> dict:
    """Independently read the actual static constellation primitives.

    Legacy exports can still be inspected in development, but absence of this
    contract never constitutes release-ready geometry evidence.
    """
    from .scenecheck import MAX_XML_BYTES, verify_scene
    try:
        with open(os.path.join(out_dir, "bundle.json"), encoding="utf-8") as fh:
            bundle = json.load(fh)
        with open(os.path.join(out_dir, "layout.json"), encoding="utf-8") as fh:
            layout = json.load(fh)
        plan = layout.get("scene_plan")
        if plan is None:
            return {"status": "skip", "summary": "No static scene plan; regenerate with the current compiler", "scope": "No geometry verification performed"}
        if (bundle.get("layout") or {}).get("scene_plan") != plan:
            return {"status": "fail", "summary": "Served and build-control scene plans differ"}
        if not isinstance(plan, dict):
            return {"status": "fail", "summary": "Static scene plan is malformed"}
        if type(plan.get("source_binding_schema")) is not int or plan["source_binding_schema"] != 1:
            return {"status": "skip", "summary": "Legacy scene plan lacks the current source-binding contract; regenerate before release",
                    "scope": "Legacy geometry inspection remains available through verify-scene; modern source coverage was not established"}
        path = os.path.join(out_dir, "xray.x3d")
        if os.path.getsize(path) > MAX_XML_BYTES:
            return {"status": "fail", "summary": "Scene exceeds the bounded static verifier input size"}
        with open(path, encoding="utf-8") as fh:
            result = verify_scene(fh.read(), plan, bundle)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"status": "fail", "summary": "Static scene contract could not be read", "error_type": type(exc).__name__}
    return {"status": "pass" if result["ok"] else "fail", "summary": "Static constellation geometry " + ("matches source plan" if result["ok"] else "failed correspondence checks"),
            "scope": result["scope"], "checks": result.get("checks", {}), "findings": result.get("findings", []),
            "counts": result.get("checks", {}), "findings_omitted": result.get("omitted_findings", 0)}


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
    g["witness"] = gate_witness(out_dir, data_dir)
    g["scene"] = gate_scene(out_dir)
    g["tools"] = gate_tools(out_dir)
    g["live"] = gate_live(out_dir) if live else {"status": "skip", "summary": "skipped with --no-live"}
    with open(os.path.join(out_dir, "bundle.json"), encoding="utf-8") as fh:
        b = json.load(fh)
    _guard(g, {r["name"] for r in b["repos"] if r.get("visibility") == "private"})
    ok = all(v["status"] != "fail" for v in g.values())
    ready = all(g.get(k, {}).get("status") == "pass" for k in REQUIRED_RELEASE_GATES)
    res = {"ok": ok, "release_ready": ready, "required_release_gates": list(REQUIRED_RELEASE_GATES),
           "ran_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "skipped": [k for k, v in g.items() if v["status"] == "skip"], "gates": g}
    with open(os.path.join(out_dir, "gates.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    write_publish_map(out_dir)
    return res
