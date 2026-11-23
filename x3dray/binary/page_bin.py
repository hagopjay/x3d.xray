"""Pass B page: capability lattice + hypothesis gallery + gates, one standalone
HTML file (no scripts, no network), written next to xray.x3d.

Everything on the page is read from the witnessed records; the page computes
nothing new. Layout: verdict strip, "the debugger lies" panel, hypothesis
gallery (one card per ledger row), capability lattice (Hasse diagram from
x3dray.fca), call graph by betweenness, gates, claims, method credit.
"""
from __future__ import annotations

import html
import json
from fractions import Fraction

ARTICLE = "https://www.slowchronicle.com/posts/llm-assisted-reverse-engineering"
E = html.escape

CSS = """
:root{--bg:#f6f7f9;--fg:#141a20;--mut:#5b6875;--card:#fff;--line:#d9dee4;--ok:#1e8a52;--bad:#c2413b;--warn:#b26b00;--gold:#a87b00;--acc:#2a6fb0}
@media (prefers-color-scheme:dark){:root{--bg:#0b141c;--fg:#dce8ef;--mut:#8ea0ae;--card:#111d27;--line:#22323f;--ok:#5cc28a;--bad:#e0605a;--warn:#ff9a52;--gold:#ffd36b;--acc:#6fb7e0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 60px}h1{font-size:26px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px;border-bottom:1px solid var(--line);padding-bottom:4px}
.mut{color:var(--mut)}code,.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:14px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}.tile b{display:block;font-size:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;border-left:5px solid var(--line)}
.card.CONFIRMED{border-left-color:var(--ok)}.card.REFUTED{border-left-color:var(--bad)}.card.UNTESTED{border-left-color:var(--warn)}
.pill{display:inline-block;border-radius:99px;padding:1px 8px;font-size:11.5px;border:1px solid var(--line);margin-right:4px}
.pill.CONFIRMED{color:var(--ok);border-color:var(--ok)}.pill.REFUTED{color:var(--bad);border-color:var(--bad)}.pill.UNTESTED{color:var(--warn);border-color:var(--warn)}
table{border-collapse:collapse;width:100%;background:var(--card)}td,th{border:1px solid var(--line);padding:4px 7px;text-align:left;vertical-align:top}th{font-weight:600}
.pass{color:var(--ok);font-weight:600}.fail{color:var(--bad);font-weight:600}
.lies{display:grid;grid-template-columns:1fr 1fr;gap:12px}.lies div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.big{font-size:22px;font-family:ui-monospace,monospace}
svg text{fill:var(--fg);font:11px system-ui}svg .edge{stroke:var(--mut);stroke-width:1.2}svg .node{fill:var(--card);stroke:var(--acc);stroke-width:1.5}svg .hot{stroke:var(--warn);stroke-width:2.5}
.scroll{overflow-x:auto}
@media (max-width:640px){.lies{grid-template-columns:1fr}}
"""


def _lattice_svg(cap: dict) -> str:
    lat = (cap or {}).get("lattice")
    if not lat:
        return "<p class='mut'>no capabilities found</p>"
    cs = lat["concepts"]
    levels = max(c["level"] for c in cs) + 1
    W, H = 760, 120 + 110 * levels
    pos = {}
    for i, c in enumerate(cs):
        pos[i] = (60 + c["x"] * (W - 120), H - 70 - c["level"] * 110)
    out = [f"<svg viewBox='0 0 {W} {H}' width='100%' role='img' aria-label='capability lattice'>"]
    for a, b in lat["covers"]:
        (x1, y1), (x2, y2) = pos[a], pos[b]
        out.append(f"<line class='edge' x1='{x1:.0f}' y1='{y1:.0f}' x2='{x2:.0f}' y2='{y2:.0f}'/>")
    for i, c in enumerate(cs):
        x, y = pos[i]
        hot = " hot" if "anti-debug" in c["intent"] else ""
        out.append(f"<circle class='node{hot}' cx='{x:.0f}' cy='{y:.0f}' r='{8 + 3 * c['support']}'/>")
        intent = ", ".join(c["intent"]) or "(top: every function)"
        out.append(f"<text x='{x:.0f}' y='{y - 18 - 3 * c['support']:.0f}' text-anchor='middle'>{E(intent)}</text>")
        out.append(f"<text x='{x:.0f}' y='{y + 24 + 3 * c['support']:.0f}' text-anchor='middle' class='mut'>"
                   f"{E(', '.join(c['extent']))}</text>")
    out.append("</svg>")
    return "".join(out)


def _card(r: dict) -> str:
    kinds = sorted({w.get("kind", "?") for w in r["witnesses"]})
    obs = "".join(f"<tr><th>{E(k)}</th><td class='mono'>{E(json.dumps(v) if not isinstance(v, str) else v)}</td></tr>"
                  for k, v in r["observed"].items())
    sup = "".join(f"<li class='mono'>{E(p['addr'])}: {E(p['insn'])} <span class='mut'>({E(p['constant'])})</span></li>"
                  for p in r["static_support"])
    imp = r["imported_verdict"] or "none (new here)"
    agree = "agrees" if r["agrees_with_import"] else "DISAGREES"
    return (f"<div class='card {r['verdict']}'><div><b>{E(r['id'])}</b> "
            f"<span class='pill {r['verdict']}'>{r['verdict']}</span><span class='pill'>{E(r['phase'])}</span>"
            f"<span class='pill'>grade: {E(r['grade'])}</span></div>"
            f"<p>{E(r['hypothesis'])}</p>"
            f"<p class='mut'>experiment: <code>{E(r['experiment']['kind'])}</code> · witnesses: {E(', '.join(kinds))}"
            f" · imported: {E(imp)} ({agree})</p>"
            f"<table><tr><th>predicted</th><td class='mono'>{E(json.dumps(r['prediction']))}</td></tr>{obs}</table>"
            + (f"<p class='mut'>static support in the witnessed disasm:</p><ul>{sup}</ul>" if sup else "")
            + "</div>")


def build_page(b: dict, g: dict, cap: dict, L: dict, gates: dict, claims: list) -> str:
    rows = L["rows"] if L else []
    byid = {r["id"]: r for r in rows}
    t = b["triage"]
    n_ok = sum(r["verdict"] == "CONFIRMED" for r in rows)
    gate_ok = sum(gg["status"] == "pass" for gg in gates["gates"].values())
    h5 = byid.get("H5", {}).get("observed", {})
    h3 = byid.get("H3", {}).get("observed", {})
    h2 = byid.get("H2", {}).get("observed", {})
    tiles = [("hypotheses re-run", f"{n_ok}/{len(rows)}", "CONFIRMED here"),
             ("keygen vs binary", h5.get("accepted", "-"), "native oracle"),
             ("mutants rejected", h5.get("mutants_rejected", "-"), "negative control"),
             ("gatekeeper", g.get("gatekeeper") or "-", "max betweenness"),
             ("functions / edges", f"{len(b['functions'])} / {len(g['edges'])}", "objdump"),
             ("RE gates", f"{gate_ok}/{len(gates['gates'])}", "pass")]
    strip = "".join(f"<div class='tile'><span class='mut'>{E(a)}</span><b>{E(str(v))}</b><span class='mut'>{E(c)}</span></div>"
                    for a, v, c in tiles)
    lies = (f"<div><span class='mut'>under gdb (traced)</span><p class='big'>salt {E(h2.get('gdb-salt-at-main', '?'))}</p>"
            f"<p>serial for alice: <b class='mono'>{E(h3.get('poisoned_serial', '?'))}</b> → "
            f"<b>{E(h3.get('under_gdb', '?'))}</b></p></div>"
            f"<div><span class='mut'>native (no debugger)</span><p class='big'>salt 0x00000000</p>"
            f"<p>{E(h3.get('poisoned_serial', '?'))} → <b>{E(h3.get('native', '?'))}</b>; true serial "
            f"<b class='mono'>{E(h5.get('alice', '?'))}</b> → accepted</p></div>")
    bw = sorted(((k, float(Fraction(v))) for k, v in g["betweenness"].items()),
                key=lambda kv: -kv[1])[:10]
    bwt = "".join(f"<tr><td class='mono'>{E(k)}</td><td>{v:.3f}</td><td><div style='background:var(--gold);height:8px;width:{int(v * 300)}px'></div></td></tr>"
                  for k, v in bw)
    gt = "".join(f"<tr><td class='mono'>{E(k)}</td><td class='{ 'pass' if gg['status'] == 'pass' else 'fail'}'>{E(gg['status'])}</td>"
                 f"<td>{E(gg['summary'])}</td><td class='mut'>{E(gg.get('scope', ''))}</td></tr>"
                 for k, gg in gates["gates"].items())
    ct = "".join(f"<tr><td>{E(c['view'])}</td><td class='mut'>{E(c['speaker'])}</td><td>{E(c['text'])}</td></tr>" for c in claims)
    wt = "".join(f"<tr><td class='mono'>{E(k)}</td><td class='mono'>{E(' '.join(w['argv']))}</td><td class='mono'>{E(w['output_sha256'][:16])}</td></tr>"
                 for k, w in (b.get("witnesses") or {}).items() if w)
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Binary X-Ray: {E(L.get('subject') or 'binary') if L else 'binary'}</title><style>{CSS}</style></head><body><main>
<h1>Binary X-Ray · {E(L.get('subject', '') if L else '')}</h1>
<p class='mut'>x3dray.binary Pass B · {E(t['description'][:120])}</p>
<div class='strip'>{strip}</div>
<h2>The debugger lies</h2><div class='lies'>{lies}</div>
<p class='mut'>Both sides were run on this machine (gdb + native argv run); the poisoned serial is not read from a dump but recomputed from the H5 model with the H2 salt, then judged by the binary.</p>
<h2>Hypothesis gallery</h2><div class='grid'>{''.join(_card(r) for r in rows)}</div>
<h2>Capability lattice</h2><p class='mut'>Formal concept analysis (x3dray.fca) over functions × imported capabilities. Orange ring = the anti-debug concept.</p>
<div class='scroll'>{_lattice_svg(cap)}</div>
<h2>Call graph by betweenness</h2><table><tr><th>function</th><th>betweenness</th><th></th></tr>{bwt}</table>
<h2>Gates</h2><table><tr><th>gate</th><th>status</th><th>summary</th><th>scope</th></tr>{gt}</table>
<h2>Claims (every number from a slot)</h2><table>{ct}</table>
<h2>Static witnesses</h2><div class='scroll'><table><tr><th>record</th><th>argv</th><th>sha256</th></tr>{wt}</table></div>
<h2>Method</h2><p>The ledger follows the hypothesis-debugging loop described by ioma8 in
<a href='{ARTICLE}'>“LLM assisted reverse engineering”</a> (Slow Chronicle, 18 Nov 2026): one hypothesis, one definitive experiment, one recorded verdict, kept on disk.
Here each row is code, each verdict is re-earned on this machine, and imported verdicts are display-only.</p>
</main></body></html>
"""
