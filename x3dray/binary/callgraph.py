"""Call graph + gatekeepers, reusing x3dray.centrality unchanged.

Edges are graded by call evidence, mirroring the repo's predicate ladder:
  direct   call rel32 to a known function   (strong)
  plt      call through the PLT to an import (medium)
  indirect computed/register/tail call      (weak, annotated)

The gatekeeper is the betweenness choke point; passage_points(entry, target)
returns the functions every path from entry to a chosen function must cross --
the single most useful question in practical RE.
"""
from __future__ import annotations

import re

from .. import centrality

_CALL = re.compile(r"\b(call|callq|bl)\s+(?:0x)?([0-9a-fA-F]+)\s*<([^>+]+)(?:\+0x[0-9a-fA-F]+)?>")
_CALL_BARE = re.compile(r"\b(call|callq|bl)\s+(?:0x)?([0-9a-fA-F]+)\b")
_INDIRECT = re.compile(r"\b(call|callq|bl)\s+(\*|r[a-z0-9]+|x[0-9]+|\[)")
# objdump rip-relative annotation, e.g.  lea ...  # 1140 <main>
_LEA_NAMED = re.compile(r"\blea\b.*#\s*[0-9a-fA-F]+\s*<([^>+]+)>")
_LIBC_START = re.compile(r"\bcall\b.*<__libc_start_main")


def build(binary: dict, disasm: str) -> dict:
    """binary = harvest() output; disasm = the objdump text it was built from."""
    by_addr = {fn["addr"]: fn["name"] for fn in binary["functions"]}
    starts = sorted(by_addr)
    import bisect

    def owner(addr):
        i = bisect.bisect_right(starts, addr) - 1
        return by_addr[starts[i]] if i >= 0 else None

    edges = []
    cur = None
    last_lea_name = None  # for the _start -> main(__libc_start_main arg) pattern
    for line in disasm.splitlines():
        m = re.match(r"^([0-9a-fA-F]+)\s+<([^>]+)>:", line)
        if m:
            cur = m.group(2)
            last_lea_name = None
            continue
        mi = re.match(r"^\s*([0-9a-fA-F]+):", line)
        if mi and owner(int(mi.group(1), 16)):
            cur = owner(int(mi.group(1), 16))
        if cur is None:
            continue
        ml = _LEA_NAMED.search(line)
        if ml:
            last_lea_name = ml.group(1)
        if _LIBC_START.search(line) and last_lea_name and last_lea_name != cur:
            # runtime-dispatched entry: __libc_start_main(main). Witnessed by the
            # lea that loads main's address immediately before the call.
            edges.append({"from": cur, "to": last_lea_name, "kind": "runtime"})
        mc = _CALL.search(line)
        if mc:
            callee = mc.group(3).replace("@plt", "")
            kind = "plt" if "@plt" in mc.group(0) else "direct"
            if callee != cur:
                edges.append({"from": cur, "to": callee, "kind": kind})
            continue
        mb = _CALL_BARE.search(line)
        if mb:
            tgt = int(mb.group(2), 16)
            callee = owner(tgt) or f"fn_{tgt:x}"
            if callee != cur:
                edges.append({"from": cur, "to": callee, "kind": "direct"})
            continue
        if _INDIRECT.search(line):
            edges.append({"from": cur, "to": "(indirect)", "kind": "indirect"})

    uniq = _dedupe(edges)
    nodes = sorted({e["from"] for e in uniq} | {e["to"] for e in uniq})
    adj = centrality.make_graph([(e["from"], e["to"]) for e in uniq if e["to"] != "(indirect)"],
                                directed=False)
    bw = centrality.betweenness(adj, directed=False, normalized=True) if adj else {}
    ranked = sorted(bw.items(), key=lambda kv: (-kv[1], kv[0]))
    gatekeeper = ranked[0][0] if ranked and ranked[0][1] > 0 else None
    return {
        "schema_version": 1,
        "nodes": nodes,
        "edges": uniq,
        "betweenness": {k: str(v) for k, v in bw.items()},  # exact Fraction as text
        "gatekeeper": gatekeeper,
        "entry": _guess_entry(nodes),
    }


def passage(graph: dict, target: str) -> dict:
    """Functions every path from entry to `target` must cross (Callon passage points)."""
    entry = graph.get("entry")
    adj = centrality.make_graph([(e["from"], e["to"]) for e in graph["edges"]
                                 if e["to"] != "(indirect)"], directed=False)
    if not entry or entry not in adj or target not in adj:
        return {"entry": entry, "target": target, "passage_points": []}
    pp = centrality.passage_points(adj, {entry}, {target})
    return {"entry": entry, "target": target, "passage_points": sorted(pp)}


def _dedupe(edges):
    seen, out = set(), []
    order = {"direct": 0, "plt": 1, "indirect": 2}
    for e in sorted(edges, key=lambda x: order.get(x["kind"], 3)):
        key = (e["from"], e["to"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _guess_entry(nodes):
    for cand in ("_start", "main", "entry0"):
        if cand in nodes:
            return cand
    return nodes[0] if nodes else None
