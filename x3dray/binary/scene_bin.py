"""Emit an X3D 4.0 scene for a binary: a binary city + a call constellation.

Reuses the repo's own formatting helpers and palette (x3dray.compiler) so the
binary world speaks the same visual dialect as the repo-family world. Geometry
is deliberately simple and well-formed; it validates as X3D 4.0.

  city        : one district per section, one Box per function, height by size,
                a hot roof (HEAT) on functions that touch anti-analysis APIs.
  constellation: functions as Spheres on a ring, call edges as IndexedLineSet,
                the gatekeeper enlarged and GOLD.
"""
from __future__ import annotations

import math

from ..compiler import SKY, HEAT, GOLD, LABEL, FOG, col, f, v3, esc_attr

SECTION_COL = {".text": "#6fb7e0", ".plt": "#5d6f7d", ".plt.sec": "#5d6f7d",
               ".rodata": "#e0a3d8", ".data": "#8b98d6", ".bss": "#7f93a3",
               ".init": "#5cc28a", ".fini": "#5cc28a"}
NODE_COL = "#dce8ef"


def _shape(geom: str, color: str, emissive: float = 0.0) -> str:
    app = (f"<Appearance><Material diffuseColor='{col(color)}' "
           f"emissiveColor='{col(color, emissive)}'/></Appearance>")
    return f"<Shape>{app}{geom}</Shape>"


def _label(text: str, pos, size: float = 0.5, color: str = LABEL, font: str | None = None) -> str:
    fs = (f"<FontStyle family='{esc_attr(font)}' style='PLAIN' size='{f(size)}' justify='\"MIDDLE\" \"MIDDLE\"'/>"
          if font else f"<FontStyle style='PLAIN' size='{f(size)}' justify='\"MIDDLE\" \"MIDDLE\"'/>")
    geom = f"<Text string='\"{esc_attr(text)}\"'>{fs}</Text>"
    return (f"<Transform translation='{v3(pos)}'>"
            f"<Shape><Appearance><Material emissiveColor='{col(color)}'/></Appearance>{geom}</Shape>"
            f"</Transform>")


def _city(binary: dict, anti: set) -> str:
    by_sec: dict[str, list] = {}
    for fn in binary["functions"]:
        by_sec.setdefault(fn["section"], []).append(fn)
    out = ["<Transform DEF='City' translation='0 0 0'><Group>"]
    x0 = 0.0
    maxins = max((fn["insns"] for fn in binary["functions"]), default=1) or 1
    for sec, fns in sorted(by_sec.items()):
        scol = SECTION_COL.get(sec, "#9aa9b5")
        out.append(_label(sec, (x0 + len(fns) * 1.5 / 2, -0.6, 2.2), 0.6, scol))
        for i, fn in enumerate(sorted(fns, key=lambda g: -g["insns"])):
            h = 0.4 + 3.6 * (fn["insns"] / maxins)
            x = x0 + i * 1.5
            box = f"<Box size='1 {f(h)} 1'/>"
            out.append(f"<Transform translation='{v3((x, h / 2, 0))}'>{_shape(box, scol, 0.18)}</Transform>")
            if fn["name"] in anti or fn.get("anti_debug"):
                roof = "<Box size='1.05 0.3 1.05'/>"
                out.append(f"<Transform translation='{v3((x, h + 0.15, 0))}'>{_shape(roof, HEAT, 0.5)}</Transform>")
            out.append(_label(fn["name"][:18], (x, h + 0.6, 0), 0.32, NODE_COL))
        x0 += len(fns) * 1.5 + 2.0
    out.append("</Group></Transform>")
    return "\n".join(out)


def _constellation(graph: dict, anti: set, center=(0.0, 6.0, -14.0), r=7.0) -> str:
    nodes = [n for n in graph["nodes"] if n != "(indirect)"]
    if not nodes:
        return ""
    idx = {n: i for i, n in enumerate(nodes)}
    pos = {}
    for n, i in idx.items():
        a = 2 * math.pi * i / len(nodes)
        pos[n] = (center[0] + r * math.cos(a), center[1] + r * math.sin(a) * 0.6, center[2])
    gk = graph.get("gatekeeper")
    out = ["<Transform DEF='CallGraph'><Group>"]
    # edges
    pts, lines, k = [], [], 0
    for e in graph["edges"]:
        if e["from"] in pos and e["to"] in pos:
            pts.append(pos[e["from"]]); pts.append(pos[e["to"]])
            lines.append(f"{k} {k + 1} -1"); k += 2
    if pts:
        coord = " ".join(v3(p) for p in pts)
        out.append("<Shape><Appearance><Material emissiveColor='%s'/></Appearance>"
                   "<IndexedLineSet coordIndex='%s'><Coordinate point='%s'/></IndexedLineSet></Shape>"
                   % (col(FOG), " ".join(lines), coord))
    # nodes
    for n in nodes:
        is_gk = (n == gk)
        rad = 0.55 if is_gk else 0.3
        ncol = GOLD if is_gk else (HEAT if n in anti else NODE_COL)
        out.append(f"<Transform translation='{v3(pos[n])}'>"
                   f"{_shape(f'<Sphere radius=\"{f(rad)}\"/>', ncol, 0.5 if is_gk else 0.25)}</Transform>")
        out.append(_label(n[:16], (pos[n][0], pos[n][1] + rad + 0.35, pos[n][2]), 0.3, ncol))
    out.append("</Group></Transform>")
    return "\n".join(out)


def build_scene(binary: dict, graph: dict, *, font: str | None = None,
                source_digest: str = "") -> str:
    anti = {fn["name"] for fn in binary["functions"] if fn.get("anti_debug")}
    gk = graph.get("gatekeeper")
    title = esc_attr(binary["target"])
    head = [
        "<?xml version='1.0' encoding='UTF-8'?>",
        "<X3D profile='Immersive' version='4.0' "
        "xmlns:xsd='http://www.w3.org/2001/XMLSchema-instance' "
        "xsd:noNamespaceSchemaLocation='https://www.web3d.org/specifications/x3d-4.0.xsd'>",
        "<head>",
        f"<meta name='title' content='x3d.xray binary lens: {title}'/>",
        "<meta name='generator' content='x3dray.binary (Pass A, stdlib + binutils, no model)'/>",
        f"<meta name='source-digest' content='{esc_attr(source_digest)}'/>",
        "</head>", "<Scene>",
        f"<NavigationInfo type='\"EXAMINE\" \"ANY\"' headlight='true'/>",
        f"<Background skyColor='{col(SKY)}'/>",
        "<Viewpoint DEF='Overview' description='Overview' position='6 5 26'/>",
    ]
    if gk:
        head.append("<Viewpoint DEF='Gatekeeper' description='The gatekeeper: %s' "
                    "position='0 6 2'/>" % esc_attr(gk))
    if anti:
        head.append("<Viewpoint DEF='AntiDebug' description='Anti-analysis functions' position='0 3 10'/>")
    body = _city(binary, anti) + _constellation(graph, anti)
    tail = ["</Scene>", "</X3D>", ""]
    scene_font = f'"{font}"' if font else None
    if scene_font:
        body = body.replace("<FontStyle style=", f"<FontStyle family={scene_font} style=")
    return "\n".join(head) + body + "\n".join(tail)
