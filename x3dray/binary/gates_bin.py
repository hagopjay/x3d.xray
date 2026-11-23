"""Pass B gates for the binary lens: the repo's fact and witness gates, extended
to the reverse-engineering witness kinds.

  gate_re_fact     every claim renders from slots; a digit outside a slot fails
                   (the binary analogue of gates.gate_fact)
  gate_re_witness  re-runs every witness in binary.json and ledger.json: tool
                   hashes, strace/gdb observations, the seeded native oracle.
                   Fails closed on any mismatch (the analogue of gates.gate_witness)
  gate_re_scene    every hypothesis and function the scene draws has a record,
                   and every record is drawn (no ghost buildings, no lost rows)
  gate_re_verdict  no CONFIRMED without a re-run witness; an imported verdict
                   that disagrees with the re-run is reported, never hidden

Every gate returns {status, summary, problems, scope}, like x3dray.gates.
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET

from . import witnesses, hypotheses


def _load(out_dir, name):
    with open(os.path.join(out_dir, name), encoding="utf-8") as fh:
        return json.load(fh)


# ----------------------------------------------------------------- slot values
def slot_values(out_dir: str) -> dict:
    b = _load(out_dir, "binary.json")
    g = _load(out_dir, "callgraph.json")
    cap = _load(out_dir, "capability.json")
    L = _load(out_dir, "ledger.json") if os.path.exists(os.path.join(out_dir, "ledger.json")) else None
    vals = {
        "callgraph.gatekeeper": g.get("gatekeeper") or "",
        "binary.anti_debug_count": str(sum(1 for fn in b["functions"] if fn.get("anti_debug"))),
        "capability.concept_count": str(len((cap.get("lattice") or {}).get("concepts", []))),
    }
    pp = g.get("passage") or {}
    vals["callgraph.passage_target"] = pp.get("target", "")
    vals["callgraph.passage_points"] = ", ".join(pp.get("passage_points", []))
    ext = [c["extent"] for c in (cap.get("lattice") or {}).get("concepts", []) if c["intent"] == ["anti-debug"]]
    vals["capability.anti_debug_extent"] = " and ".join(ext[0]) if ext else ""
    if L:
        pats = {p["constant"]: p["value"] for p in L.get("static_patterns", [])}
        vals["pattern.int3_byte"] = pats.get("int3_byte", "")
        vals["ledger.confirmed"] = str(L["tally"]["CONFIRMED"])
        vals["ledger.total"] = str(len(L["rows"]))
        for r in L["rows"]:
            for k, v in r["observed"].items():
                if isinstance(v, str):
                    vals[f"ledger.{r['id']}.{k}"] = v.split("/")[0] if k in ("accepted", "mutants_rejected") else v
    return vals


def _untested_rows(out_dir: str) -> set:
    p = os.path.join(out_dir, "ledger.json")
    if not os.path.exists(p):
        return set()
    return {r["id"] for r in _load(out_dir, "ledger.json")["rows"] if r["verdict"] == "UNTESTED"}


def _withheld_by(c: dict, untested: set) -> list:
    """Ledger rows this claim depends on that could not be run here."""
    return sorted({k.split(".")[1] for k in (c.get("slots") or {}).values()
                   if k.startswith("ledger.") and k.split(".")[1] in untested})


def render_claims(spec: dict, vals: dict, out_dir: str | None = None) -> list[dict]:
    untested = _untested_rows(out_dir) if out_dir else set()
    out = []
    for c in spec.get("claims", []):
        held = _withheld_by(c, untested)
        if held:
            out.append({"view": c["view"], "speaker": c["speaker"], "withheld": held,
                        "text": f"(withheld: {', '.join(held)} could not be run on this machine)",
                        "template": c["text"], "slots": c.get("slots") or {}, "missing": []})
            continue
        text, missing = c["text"], []
        for slot, key in (c.get("slots") or {}).items():
            if key not in vals or vals[key] == "":
                missing.append(key)
            text = text.replace("{" + slot + "}", vals.get(key, "?"))
        out.append({"view": c["view"], "speaker": c["speaker"], "text": text,
                    "template": c["text"], "slots": c.get("slots") or {}, "missing": missing})
    return out


def gate_re_fact(out_dir: str, spec: dict) -> dict:
    vals = slot_values(out_dir)
    untested = _untested_rows(out_dir)
    probs, withheld = [], []
    for c in spec.get("claims", []):
        if _withheld_by(c, untested):
            # a claim resting on an experiment this machine could not run is withheld,
            # not filled and not failed: the page says so instead of printing a number
            withheld.append(c["view"])
            continue
        bare = re.sub(r"\{[a-z0-9_]+\}", "", c["text"])
        stray = re.findall(r"\d", bare)
        if stray:
            probs.append(f"{c['view']}: {len(stray)} digit(s) outside a slot in the template")
        for slot, key in (c.get("slots") or {}).items():
            if "{" + slot + "}" not in c["text"]:
                probs.append(f"{c['view']}: slot {slot} declared but unused")
            if not vals.get(key):
                probs.append(f"{c['view']}: slot {slot} -> {key} has no witnessed value")
        for slot in re.findall(r"\{([a-z0-9_]+)\}", c["text"]):
            if slot not in (c.get("slots") or {}):
                probs.append(f"{c['view']}: placeholder {{{slot}}} has no slot")
    n = sum(len(c.get("slots") or {}) for c in spec.get("claims", []))
    return {"status": "pass" if not probs else "fail",
            "summary": f"{len(spec.get('claims', []))} claims, {n} slots, {len(probs)} problems, "
                       f"{len(withheld)} withheld (untested rows)",
            "problems": probs, "withheld": withheld,
            "scope": "Every number in claim prose comes from a witnessed record; entailment of the prose is not proved."}


def gate_re_witness(out_dir: str, binp: str, spec: dict) -> dict:
    b = _load(out_dir, "binary.json")
    results = []
    for name, w in (b.get("witnesses") or {}).items():
        if w:
            results.append({"id": f"harvest.{name}", "kind": "static", **witnesses.verify(w)})
    if os.path.exists(os.path.join(out_dir, "ledger.json")):
        L = _load(out_dir, "ledger.json")
        if L["binary_sha256"] != hypotheses.file_sha(binp):
            results.append({"id": "ledger", "kind": "binary", "status": "mismatch",
                            "reason": "binary differs from the one the ledger was run on"})
        else:
            results += hypotheses.verify_ledger(L, binp, spec)["results"]
    bad = [r for r in results if r["status"] == "mismatch"]
    unavailable = [r for r in results if r["status"] == "unavailable"]
    kinds = sorted({r["kind"] for r in results})
    return {"status": "pass" if not bad else "fail",
            "summary": f"{len(results)} witnesses re-run ({', '.join(kinds)}): "
                       f"{len(results) - len(bad) - len(unavailable)} match, {len(bad)} mismatch, "
                       f"{len(unavailable)} unavailable",
            "problems": [f"{r['id']} ({r['kind']}): {r.get('reason', 'output changed')}" for r in bad],
            "results": results,
            "scope": "Static tool output hashes, strace/gdb extracted observations and the seeded native oracle transcript are reproduced on this machine."}


def gate_re_verdict(out_dir: str) -> dict:
    L = _load(out_dir, "ledger.json")
    probs, disagree = [], []
    for r in L["rows"]:
        rerun = [w for w in r["witnesses"] if w.get("status") not in ("unavailable",)]
        if r["verdict"] == "CONFIRMED" and not rerun:
            probs.append(f"{r['id']}: CONFIRMED without a re-run witness")
        if not r["agrees_with_import"]:
            disagree.append(f"{r['id']}: imported {r['imported_verdict']} vs re-run {r['verdict']}")
    return {"status": "pass" if not probs else "fail",
            "summary": f"{len(L['rows'])} verdicts; {len(probs)} unsupported; {len(disagree)} disagree with the imported ledger",
            "problems": probs, "disagreements": disagree,
            "scope": "A verdict is only as good as the experiment re-run here; imported verdicts are display-only."}


def gate_re_scene(out_dir: str, scene_name: str = "xray.x3d") -> dict:
    path = os.path.join(out_dir, scene_name)
    if not os.path.exists(path):
        return {"status": "fail", "summary": f"{scene_name} missing", "problems": [scene_name], "scope": ""}
    root = ET.parse(path).getroot()
    defs = {e.get("DEF") for e in root.iter() if e.get("DEF")}
    b = _load(out_dir, "binary.json")
    want_fn = {"FN_" + _safe(fn["name"]) for fn in b["functions"]}
    have_fn = {d for d in defs if d.startswith("FN_")}
    probs = [f"function {d[3:]} has a record but is not drawn" for d in sorted(want_fn - have_fn)]
    probs += [f"building {d} is drawn without a record" for d in sorted(have_fn - want_fn)]
    if os.path.exists(os.path.join(out_dir, "ledger.json")):
        L = _load(out_dir, "ledger.json")
        want_h = {"HYP_" + r["id"] for r in L["rows"]}
        have_h = {d for d in defs if d.startswith("HYP_")}
        probs += [f"hypothesis {d} not on the helix" for d in sorted(want_h - have_h)]
        probs += [f"helix rung {d} has no ledger row" for d in sorted(have_h - want_h)]
    return {"status": "pass" if not probs else "fail",
            "summary": f"{len(have_fn)} buildings, {len([d for d in defs if d.startswith('HYP_')])} helix rungs; {len(probs)} problems",
            "problems": probs,
            "scope": "DEF-level correspondence between records and drawn shapes; geometry values are not re-measured."}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def run_all(out_dir: str, binp: str, claims_spec: dict, ledger_spec: dict) -> dict:
    gates = {"re_fact": gate_re_fact(out_dir, claims_spec),
             "re_witness": gate_re_witness(out_dir, binp, ledger_spec),
             "re_verdict": gate_re_verdict(out_dir) if os.path.exists(os.path.join(out_dir, "ledger.json"))
             else {"status": "skip", "summary": "no ledger"},
             "re_scene": gate_re_scene(out_dir)}
    ok = all(g["status"] in ("pass", "skip") for g in gates.values())
    return {"ok": ok, "gates": gates}
