"""Compile one X3D 4.0 scene from the data and the rendered spec. No model.

xray.x3d holds six regions side by side:
- the time helix (centre): the family's own commits, one turn per active day; angle is the hour (UTC), radius the
  repo, colour who committed, size lines changed; Claude-session threads, day, month, gap, series and repo labels,
  the busiest-day ring, and a clock face at the end with per-hour bars;
- the tie constellation (left): repos and other nodes in a plane, a ring around each repo showing who wrote it,
  machine-found ties as straight lines and the masterclass's hand-graded ties as arcs with arrowheads;
- the code city (right): one district per repo at its tip, one building per file, a hot roof on heavy rewrites;
- the concept lattices (above, left): files by the kinds of author that touched them, and repos by the kinds of
  author they have seen, as Hasse diagrams (x3dray/structure.py, formal concept analysis);
- the bridges (above, right): the Claude-session graph with its passage points ringed in gold, and each repo's
  bridge files with and without the high-churn files (betweenness);
- the tool's own pipeline (front): one block per stage, coral where a model takes part, and lines from the
  tool's row to x3d.xray's own place in the other three views.
Every pickable thing has a DEF name and a record in layout.json, which the page uses to pick, fly and highlight.
"""

from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import math
import os

from . import __version__

# ---- palette (scene is X-ray film: a blue-black base, luminous marks)
SKY = "#0b141c"
CLS_COL = {"you": "#f0b443", "ai": "#e8795d", "bot": "#a4bf7a", "upstream": "#7d8b97", "other": "#a68fdc"}
GRADE_COL = {"C": "#5cc28a", "L": "#e6c75a", "S": "#e8875a"}
KIND_COL = {"code": "#6fb7e0", "tests": "#5cc28a", "docs": "#d9d2c3", "data": "#8b98d6", "x3d": "#e0a3d8",
            "config": "#8a9aa6", "web": "#f0c674", "notebook": "#f29e6d", "media": "#b28ad6", "log": "#6e7b85",
            "ci": "#7fd1c7", "other": "#9aa5ad"}
NODE_COL = {"own": "#dce8ef", "fork-active": "#5ac8e0", "fork-idle": "#5d6f7d", "private": "#a58bd6", "external": "#9aa9b5",
            "stub": "#4c5a66"}
LABEL = "#dce8ef"
DIM = "#8fa3b1"
CORAL = CLS_COL["ai"]
BONE = "#eef5f8"
HEAT = "#ff9a52"
FOG = "#7f93a3"     # unmeasured: drawn as fog, never as zero

FONT = '"fonts/UbuntuMono-R.ttf" "TYPEWRITER"'
CLASSES = ["you", "ai", "bot", "other", "upstream"]

HELIX_LEN = 30.0
R0, DR = 1.35, 0.33
CT = (-47.0, 0.0, 0.0)     # constellation centre
CC = (44.0, -4.0, -2.0)    # city origin (left-back corner of the first row)
SELF_Y, SELF_Z = -9.5, 13.0
CONC = (-26.0, 28.0, -6.0)   # the concept lattices (above, left): high enough to stay out of the ties and helix views
BRID = (30.0, 28.0, -6.0)    # the bridges (above, right): high enough to stay out of the rhythm and city views
WHO_NAME = {"you": "HagopJay", "ai": "Claude", "bot": "bots", "other": "other", "upstream": "unmapped"}
GOLD = "#ffd36b"             # a passage point: on every path between two repos in the session graph


# ---- small helpers
def f(x: float, p: int = 3) -> str:
    s = f"{x:.{p}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def v3(p) -> str:
    return " ".join(f(c) for c in p)


def hexcol(h: str, k: float = 1.0) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(min(1.0, int(h[i:i + 2], 16) / 255 * k) for i in (0, 2, 4))


def col(h: str, k: float = 1.0) -> str:
    return " ".join(f(c) for c in hexcol(h, k))


def esc_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;")


def mfstring(lines: list[str]) -> str:
    return " ".join('"' + esc_attr(x).replace("\\", "\\\\").replace('"', '\\"') + '"' for x in lines)


def norm(a):
    n = math.sqrt(sum(c * c for c in a)) or 1.0
    return tuple(c / n for c in a)


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def mul(a, k):
    return tuple(x * k for x in a)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def look_at(pos, target, up=(0.0, 1.0, 0.0)) -> tuple[float, float, float, float]:
    """Axis-angle orientation for a Viewpoint at pos looking at target (X3D views along -Z with +Y up)."""
    fw = norm(sub(target, pos))
    r = cross(fw, up)
    if math.sqrt(sum(c * c for c in r)) < 1e-6:
        r = (1.0, 0.0, 0.0)
    r = norm(r)
    u = cross(r, fw)
    m = [[r[0], u[0], -fw[0]], [r[1], u[1], -fw[1]], [r[2], u[2], -fw[2]]]
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w, x, y, z = 0.25 * s, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        w, x, y, z = (m[2][1] - m[1][2]) / s, 0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        w, x, y, z = (m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        w, x, y, z = (m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s
    w = max(-1.0, min(1.0, w))
    ang = 2 * math.acos(w)
    sh = math.sqrt(max(0.0, 1 - w * w))
    if sh < 1e-6:
        return (0.0, 0.0, 1.0, 0.0)
    return (x / sh, y / sh, z / sh, ang)


def rot_y_to(t) -> tuple[float, float, float, float]:
    """Rotation taking +Y onto direction t (for cones and bars)."""
    t = norm(t)
    ax = (t[2], 0.0, -t[0])
    n = math.sqrt(ax[0] ** 2 + ax[2] ** 2)
    if n < 1e-6:
        return (1.0, 0.0, 0.0, 0.0 if t[1] > 0 else math.pi)
    return (ax[0] / n, 0.0, ax[2] / n, math.acos(max(-1.0, min(1.0, t[1]))))


class Scene:
    def __init__(self):
        self.out: list[str] = []
        self.mats: dict[str, str] = {}
        self.fonts: dict[tuple, str] = {}
        self.labels = 0
        self.label_texts: list[dict] = []

    def w(self, s: str):
        self.out.append(s)

    def mat(self, key: str, xml_new: str) -> str:
        """An Appearance shared by DEF/USE: the first use defines it."""
        if key in self.mats:
            return f"<Appearance USE='{self.mats[key]}'/>"
        d = "A_" + "".join(ch if ch.isalnum() else "_" for ch in key)
        self.mats[key] = d
        return f"<Appearance DEF='{d}'>{xml_new}</Appearance>"

    def lit(self, key: str, hexc: str, glow: float = 0.35, transparency: float = 0.0) -> str:
        tr = f" transparency='{f(transparency)}'" if transparency else ""
        return self.mat(key, f"<Material diffuseColor='{col(hexc)}' emissiveColor='{col(hexc, glow)}'{tr}/>")

    def unlit(self, key: str, hexc: str, width: float | None = None, transparency: float = 0.0) -> str:
        lp = f"<LineProperties linewidthScaleFactor='{f(width)}'/>" if width else ""
        tr = f" transparency='{f(transparency)}'" if transparency else ""
        return self.mat(key, f"<Material diffuseColor='0 0 0' emissiveColor='{col(hexc)}'{tr}/>{lp}")

    def font(self, size: float, justify: str = "MIDDLE") -> str:
        k = (round(size, 3), justify)
        if k in self.fonts:
            return f"<FontStyle USE='{self.fonts[k]}'/>"
        d = f"FS_{len(self.fonts)}"
        self.fonts[k] = d
        return f"<FontStyle DEF='{d}' family='{FONT}' justify='\"{justify}\" \"MIDDLE\"' size='{f(size)}'/>"

    def label(self, lines: list[str], pos, size: float = 0.3, color: str = LABEL, justify: str = "MIDDLE",
              billboard: bool = True, view: str = "", def_: str | None = None):
        self.labels += 1
        self.label_texts.append({"text": " / ".join(lines), "view": view, "pos": [round(c, 3) for c in pos]})
        app = self.mat("lbl" + color, f"<Material diffuseColor='0 0 0' emissiveColor='{col(color)}'/>")
        dd = f" DEF='{def_}'" if def_ else ""
        txt = f"<Shape>{app}<Text string='{mfstring(lines)}' solid='false'>{self.font(size, justify)}</Text></Shape>"
        if billboard:
            self.w(f"<Transform{dd} translation='{v3(pos)}'><Billboard axisOfRotation='0 0 0'>{txt}</Billboard></Transform>")
        else:
            self.w(f"<Transform{dd} translation='{v3(pos)}'>{txt}</Transform>")

    def lines(self, key: str, color: str, polylines: list[list[tuple]], width: float | None = None, transparency: float = 0.0,
              def_: str | None = None):
        pts, idx = [], []
        for pl in polylines:
            if len(pl) < 2:
                continue
            for p in pl:
                idx.append(str(len(pts)))
                pts.append(v3(p))
            idx.append("-1")
        if not pts:
            return
        dd = f" DEF='{def_}'" if def_ else ""
        self.w(f"<Shape{dd}>{self.unlit(key, color, width, transparency)}<IndexedLineSet coordIndex='{' '.join(idx)}'>"
               f"<Coordinate point='{', '.join(pts)}'/></IndexedLineSet></Shape>")

    def boxes(self, key: str, items: list[tuple], def_: str | None = None, emissive: str = "#141c22"):
        """Many boxes in one IndexedFaceSet: items are (x0, y0, z0, x1, y1, z1, hexcolour); five faces each (no bottom)."""
        if not items:
            return
        pts, idx, cols = [], [], []
        for (x0, y0, z0, x1, y1, z1, c) in items:
            b = len(pts)
            for p in [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]:
                pts.append(v3(p))
            for face in ([4, 5, 6, 7], [1, 0, 3, 2], [5, 1, 2, 6], [0, 4, 7, 3], [7, 6, 2, 3]):
                idx.append(" ".join(str(b + i) for i in face) + " -1")
                cols.append(col(c))
        dd = f" DEF='{def_}'" if def_ else ""
        app = self.mat(key, f"<Material diffuseColor='1 1 1' emissiveColor='{col(emissive)}'/>")
        self.w(f"<Shape{dd}>{app}<IndexedFaceSet solid='false' colorPerVertex='false' coordIndex='{' '.join(idx)}'>"
               f"<Coordinate point='{', '.join(pts)}'/><Color color='{', '.join(cols)}'/></IndexedFaceSet></Shape>")


def _day(t: int) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")


def _short_day(d: str) -> str:
    x = dt.date.fromisoformat(d)
    return f"{x.day} {x.strftime('%b')}"


def _fmt_n(n: int) -> str:
    return f"{n:,}"


def _some(xs: list[str]) -> str:
    """Two names joined, or the first and how many more: a label that stays inside its block."""
    return " + ".join(xs) if len(xs) <= 2 else f"{xs[0]} + {len(xs) - 1} more"


def _k(n: int) -> str:
    return f"{n / 1000:.0f}k" if n >= 10000 else (f"{n / 1000:.1f}k" if n >= 1000 else str(n))


def own_commits(ds: dict) -> list[tuple[str, dict]]:
    rows = [(r["name"], c) for r in ds["repos"] for c in r.get("commits", []) if not c.get("upstream")]
    rows.sort(key=lambda rc: (rc[1]["t"], rc[0]))
    return rows


def ring(S: "Scene", p, r1: float, r2: float, parts: list[tuple[str, float]], z: float = 0.02):
    """A flat ring around p in the XY plane, split into coloured arcs (colour, share), from twelve o'clock clockwise.
    Unlit and coloured per face: the Appearance has no Material, so the Color node's colours show as they are."""
    tot = sum(w for _, w in parts) or 1
    pts, idx, cols = [], [], []
    a = math.pi / 2
    for hexc, w in parts:
        if w <= 0:
            continue
        span = 2 * math.pi * w / tot
        steps = max(2, int(span / 0.12))
        for st in range(steps):
            a0, a1 = a - span * st / steps, a - span * (st + 1) / steps
            b = len(pts)
            for (aa, rr) in ((a0, r1), (a1, r1), (a1, r2), (a0, r2)):
                pts.append(v3((p[0] + rr * math.cos(aa), p[1] + rr * math.sin(aa), p[2] + z)))
            idx.append(f"{b} {b + 1} {b + 2} {b + 3} -1")
            cols.append(col(hexc))
        a -= span
    if pts:
        S.w(f"<Shape>{S.mat('ringc', '')}<IndexedFaceSet solid='false' colorPerVertex='false' coordIndex='{' '.join(idx)}'>"
            f"<Coordinate point='{', '.join(pts)}'/><Color color='{', '.join(cols)}'/></IndexedFaceSet></Shape>")


def intent_name(intent: list[str], empty: str = "every file") -> str:
    return " + ".join(WHO_NAME.get(c, c) for c in intent) if intent else empty


def concepts_region(S: "Scene", L: dict, con: dict) -> dict:
    """Two concept lattices drawn as Hasse diagrams: files by the kinds of author that touched them (left, large) and
    repos by the kinds of author they have seen (right, small). Level is height (the top concept holds everything), x
    comes from barycentre sweeps in x3dray/fca.py; a node's size is how many objects it holds, its ring the kinds."""
    cx, cy, cz = CONC
    fc, rc = con["files_by_author_class"], con["repos_by_author_class"]
    L["concepts"] = {"files": [], "repos": []}
    S.w("<Group DEF='Concepts'>")
    S.w("<TouchSensor DEF='TS_concepts' description='concept'/>")

    def draw(cs, covers, x0, width, top_y, dy, scale, kind, sub):
        pos = {}
        for c in cs:
            pos[c["i"]] = (x0 + (c["x"] - 0.5) * width, top_y - c["level"] * dy, cz)
        S.lines("hasse", "#6f8796", [[pos[lo], pos[up]] for lo, up in covers], width=1.6)
        for c in cs:
            p = pos[c["i"]]
            r = scale(c["support"])
            hexc = CLS_COL.get(c["intent"][0], BONE) if len(c["intent"]) == 1 else BONE
            S.w(f"<Transform translation='{v3(p)}'><Shape>{S.lit('cnode' + hexc, hexc, 0.3)}<Sphere radius='{f(r)}'/></Shape></Transform>")
            if c["intent"]:
                ring(S, p, r + 0.06, r + 0.22, [(CLS_COL.get(k, BONE), 1) for k in c["intent"]])
            S.label([intent_name(c["intent"], "every file" if kind == "files" else "every repo"), sub(c)], (p[0], p[1] + r + 0.5, p[2] + 0.1),
                    0.22 if kind == "files" else 0.19, BONE if c["intent"] else DIM, view="concepts")
            L["concepts"][kind].append({"i": c["i"], "p": [round(v, 3) for v in p], "r": round(r, 3)})

    n_levels = max((c["level"] for c in fc["concepts"]), default=0)
    draw(fc["concepts"], fc["covers"], cx - 2.0, 18.0, cy + 4.2, 8.4 / max(1, n_levels), lambda n: 0.16 + 0.085 * math.log2(1 + n), "files",
         lambda c: f"{_fmt_n(c['support'])} files" + (f" · {_fmt_n(c['exact'])} only these" if c["intent"] else ""))
    r_levels = max((c["level"] for c in rc["concepts"]), default=0)
    draw(rc["concepts"], rc["covers"], cx + 13.4, 7.6, cy + 4.2, 8.4 / max(1, r_levels), lambda n: 0.14 + 0.1 * math.log2(1 + n), "repos",
         lambda c: ", ".join(c["exact"])[:46] if c["exact"] else f"{c['support']} repos")
    ex = con["excluded"]
    aside = ", ".join(f"{v} {k.split(' (')[0]}" for k, v in ex.items())
    S.label(["Who touched which files: a concept lattice", f"{_fmt_n(fc['n_commits'])} commits, {_fmt_n(fc['n_files'])} files · set aside: {aside}"],
            (cx - 11.5, cy + 6.6, cz), 0.3, BONE, justify="BEGIN", billboard=False, view="concepts")
    S.label(["repos by who has committed"], (cx + 13.4, cy + 6.0, cz), 0.24, DIM, billboard=False, view="concepts")
    S.label(["a line joins a concept to the next larger one above it · size: how many it holds · ring: the kinds of author"],
            (cx - 11.5, cy - 5.6, cz), 0.2, DIM, justify="BEGIN", billboard=False, view="concepts")
    S.w("</Group>")
    return {"center": [cx + 2.0, cy, cz], "x0": round(cx - 13.2, 3), "x1": round(cx + 19.4, 3)}


def bridges_region(S: "Scene", L: dict, bri: dict, kinds: dict, top_hubs: dict, top_bridges: dict) -> dict:
    """The Claude-session graph (left): repos on a circle, sessions between the repos they committed to, passage points
    ringed in gold. The bridge files (right): per repo, the file on the most shortest paths between its other files, with
    the high-churn files kept (hub) and set aside (bridge)."""
    bx, by, bz = BRID
    sb = bri["sessions"]
    L["bridges"] = {"repos": {}, "sessions": {}, "files": []}
    S.w("<Group DEF='Bridges'>")
    S.w("<TouchSensor DEF='TS_bridges' description='session, repo or bridge file'/>")
    gc = (bx - 6.5, by - 0.4, bz)
    repos_in = sorted({rep for rec in sb["sessions"].values() for rep in rec["repos"]},
                      key=lambda n: (-sum(1 for rec in sb["sessions"].values() if n in rec["repos"]), n))
    pp_nodes = collections.defaultdict(list)
    for pair, via in sb["passage_points"].items():
        for v in via:
            pp_nodes[v].append(pair.replace(" -> ", " and "))
    rp = {}
    for i, n in enumerate(repos_in):
        ang = math.pi / 2 - 2 * math.pi * i / max(1, len(repos_in))
        rp[n] = (gc[0] + 5.2 * math.cos(ang), gc[1] + 4.6 * math.sin(ang), gc[2])
    # sessions: one that touched several repos sits between them; one that touched one repo orbits it
    orbit = collections.Counter()
    n_one = collections.Counter(next(iter(rec["repos"])) for rec in sb["sessions"].values() if len(rec["repos"]) == 1)
    sp = {}
    same = collections.defaultdict(list)    # sessions with the same set of repos are spread side by side, not stacked
    for sid, rec in sorted(sb["sessions"].items()):
        if len(rec["repos"]) > 1:
            same[tuple(sorted(rec["repos"]))].append(sid)
    for sid, rec in sorted(sb["sessions"].items()):
        rs = sorted(rec["repos"])
        if len(rs) > 1:
            m = [sum(rp[x][k] for x in rs) / len(rs) for k in range(3)]
            grp = same[tuple(rs)]
            k, n = grp.index(sid), len(grp)
            # the first sits near the repos' midpoint, the rest step in toward the centre, so a crowded pair fans inward
            inward = norm(sub(gc, m)) if math.dist(m, gc) > 1e-6 else (0.0, -1.0, 0.0)
            start = 0.88 if n > 1 else 0.7
            m = (gc[0] + (m[0] - gc[0]) * start, gc[1] + (m[1] - gc[1]) * start, gc[2] + 0.3)
            sp[sid] = (m[0] + inward[0] * k * 1.05, m[1] + inward[1] * k * 1.05, m[2])
        else:
            home = rp[rs[0]]
            out = norm(sub(home, gc)) if home != gc else (0.0, 1.0, 0.0)
            k = orbit[rs[0]]
            orbit[rs[0]] += 1
            ang = math.atan2(out[1], out[0]) + (k - (n_one[rs[0]] - 1) / 2) * 0.3
            sp[sid] = (home[0] + 1.25 * math.cos(ang), home[1] + 1.25 * math.sin(ang), home[2])
    lines_one, lines_multi = [], []
    for sid, rec in sb["sessions"].items():
        for rep in rec["repos"]:
            (lines_multi if len(rec["repos"]) > 1 else lines_one).append([sp[sid], rp[rep]])
    S.lines("sess1", "#7c5a50", lines_one, width=1.0)
    S.lines("sessn", CORAL, lines_multi, width=2.2)
    for n, p in rp.items():
        nsess = sum(1 for rec in sb["sessions"].values() if n in rec["repos"])
        kind = kinds.get(n, "own")
        hexc = NODE_COL.get(kind, NODE_COL["own"])
        r = 0.28 + 0.12 * math.log2(1 + nsess)
        S.w(f"<Transform translation='{v3(p)}'><Shape>{S.lit('bnode' + hexc, hexc, 0.3)}<Sphere radius='{f(r)}'/></Shape></Transform>")
        sub_ = f"{nsess} session{'s' if nsess != 1 else ''}" + (" · private" if kind == "private" else "")
        out = norm(sub(p, gc)) if p != gc else (0.0, 1.0, 0.0)
        lift = 1.15 if (out[1] > 0.45 and n_one[n]) else 0.0     # the label clears the fan of one-repo sessions above it
        S.label([n, sub_], (p[0], p[1] + r + 0.55 + lift, p[2] + 0.1), 0.24, BONE, view="bridges")
        if n in pp_nodes:
            ring(S, p, r + 0.14, r + 0.32, [(GOLD, 1)])
        L["bridges"]["repos"][n] = {"p": [round(v, 3) for v in p], "r": round(r, 3)}
    for sid, p in sp.items():
        multi = len(sb["sessions"][sid]["repos"]) > 1
        r = 0.2 if multi else 0.09
        S.w(f"<Transform translation='{v3(p)}'><Shape>{S.lit('snode' + str(multi), CORAL, 0.45)}<Sphere radius='{f(r)}'/></Shape></Transform>")
        if ("session:" + sid) in pp_nodes:
            ring(S, p, r + 0.1, r + 0.24, [(GOLD, 1)])
        if multi:
            S.label([sid, f"{len(sb['sessions'][sid]['repos'])} repos"], (p[0] + r + 0.16, p[1] + 0.1, p[2] + 0.1), 0.19, CORAL, justify="BEGIN", view="bridges")
        L["bridges"]["sessions"][sid] = {"p": [round(v, 3) for v in p], "r": r}
    S.label(["What links the repos: Claude sessions",
             f"{sb['n_sessions']} sessions left a trailer · {sb['n_multi_repo']} committed to more than one repo · gold: on every session path between two repos"],
            (gc[0] - 6.0, by + 7.6, bz), 0.24, BONE, justify="BEGIN", billboard=False, view="bridges")
    S.label(["white: own repo · cyan: active fork · violet: private (counts only) · big coral: a session in several repos · small: in one",
             f"{_fmt_n(sb['commits_without_trailer'])} own commits carry no session trailer, so {len(sb['no_recorded_link'])} of {sb['n_repo_pairs']} repo pairs have no recorded session link,",
             "which is not the same as no link: the ties may still join them"],
            (gc[0] - 6.0, by - 6.4, bz), 0.2, FOG, justify="BEGIN", billboard=False, view="bridges")

    # bridge files, one row per repo: the hub (high-churn files kept) and the bridge (set aside, Jaccard distance)
    x0 = bx + 1.6
    S.label(["Bridge files: on the most shortest paths between files",
             f"hub: every file kept · bridge: {len(bri['files']['high_churn'])} high-churn files set aside · bar: share of paths in its cluster"],
            (x0, by + 7.6, bz), 0.24, BONE, justify="BEGIN", billboard=False, view="bridges")
    order = list(top_bridges) + [r for r in top_hubs if r not in top_bridges]
    bars = []
    for j, repo in enumerate(order[:7]):
        y = by + 4.6 - j * 1.55
        S.label([repo], (x0, y, bz), 0.24, BONE, justify="BEGIN", billboard=False, view="bridges")
        for k, (var, rec) in enumerate((("hub", top_hubs.get(repo)), ("bridge", top_bridges.get(repo)))):
            yy = y - 0.42 - k * 0.5
            if not rec:
                S.label([f"{var}: none in a cluster of ten or more files"], (x0 + 0.2, yy, bz), 0.19, FOG, justify="BEGIN", billboard=False, view="bridges")
                continue
            w = 0.15 + 4.2 * rec["share_pct"] / 100
            bars.append((x0 + 0.2, yy - 0.12, bz - 0.06, x0 + 0.2 + w, yy + 0.12, bz + 0.06, HEAT if var == "hub" else GRADE_COL["C"]))
            name = rec["file"] if len(rec["file"]) <= 34 else "…" + rec["file"][-33:]
            S.label([f"{var}: {name} · {rec['share_pct']}% of {rec['cluster']}" + (" · cut" if rec["cut_vertex"] else "")],
                    (x0 + 0.35 + w, yy, bz), 0.19, LABEL, justify="BEGIN", billboard=False, view="bridges")
            L["bridges"]["files"].append({"repo": repo, "variant": var, "file": rec["file"], "p": [round(x0 + 0.2 + w / 2, 3), round(yy, 3), bz]})
    S.boxes("bbar", bars)
    S.w("</Group>")
    return {"center": [bx + 0.5, by, bz], "x0": round(gc[0] - 6.5, 3), "x1": round(x0 + 10.5, 3)}


def build_scene(ds: dict, ties: dict, curated: dict | None, prof: dict, spec: dict, selfd: dict,
                con: dict | None = None, bri: dict | None = None) -> tuple[str, dict]:
    S = Scene()
    L: dict = {"views": {}, "commits": [], "days": [], "lanes": {}, "nodes": {}, "ties": {"machine": [], "curated": []},
               "city": {"districts": {}, "buildings": []}, "self": {"stages": {}}, "regions": {}}
    repos = {r["name"]: r for r in ds["repos"]}
    per_repo = prof["per_repo"]
    rows = own_commits(ds)

    # ================= time helix
    lanes_order = sorted([n for n, r in per_repo.items() if r["own_commits"] > 0],
                         key=lambda n: (-(per_repo[n]["own_commits"]), n))
    lane = {n: R0 + DR * i for i, n in enumerate(lanes_order)}
    L["lanes"] = {n: round(r, 3) for n, r in lane.items()}
    r_out = R0 + DR * (len(lanes_order) - 1)
    days = sorted({_day(c["t"]) for _, c in rows})
    per_day = collections.Counter(_day(c["t"]) for _, c in rows)
    widths, gaps = [], []
    for i, d in enumerate(days):
        widths.append(0.16 + 0.016 * per_day[d])
        if i:
            gap = (dt.date.fromisoformat(d) - dt.date.fromisoformat(days[i - 1])).days - 1
            gaps.append(gap)
        else:
            gaps.append(0)
    raw = sum(widths) + sum(0.55 for g in gaps if g > 0)
    k = HELIX_LEN / raw
    x = -HELIX_LEN / 2
    day_x = {}
    gap_marks = []
    for i, d in enumerate(days):
        if gaps[i] > 0:
            gap_marks.append((x + 0.275 * k, gaps[i]))
            x += 0.55 * k
        day_x[d] = (x, x + widths[i] * k)
        x += widths[i] * k
    x_end = x
    L["days"] = [{"day": d, "x0": round(day_x[d][0], 3), "x1": round(day_x[d][1], 3), "n": per_day[d]} for d in days]

    def helix_pos(name: str, t: int):
        d = _day(t)
        frac = (t % 86400) / 86400
        x0, x1 = day_x[d]
        th = 2 * math.pi * frac
        r = lane[name]
        return (x0 + (x1 - x0) * frac, r * math.cos(th), -r * math.sin(th))

    S.w("<Group DEF='Helix'>")
    S.w("<TouchSensor DEF='TS_helix' description='commit'/>")
    pos_by_idx = {}
    for i, (name, c) in enumerate(rows):
        p = helix_pos(name, c["t"])
        lines_changed = c["add"] + c["del"]
        s = 0.06 + 0.034 * math.log10(1 + lines_changed)
        hexc = CLS_COL.get(c["cls"], CLS_COL["other"])
        trans = 0.0 if c.get("default", True) else 0.45
        tr = f" transparency='{f(trans)}'" if trans else ""
        geom = f"<Sphere radius='{f(s * 0.62)}'/>" if c.get("merge") else f"<Box size='{f(s)} {f(s)} {f(s)}'/>"
        extra = ""
        if c["cls"] == "you" and c.get("ai_trailer"):
            extra = (f"<Transform translation='0 {f(s * 0.62)} 0'><Shape>{S.lit('pair', CORAL, 0.6)}"
                     f"<Sphere radius='{f(max(0.025, s * 0.26))}'/></Shape></Transform>")
        S.w(f"<Transform DEF='c{i}' translation='{v3(p)}'><Shape><Appearance><Material DEF='m{i}' diffuseColor='{col(hexc)}' "
            f"emissiveColor='{col(hexc, 0.38)}'{tr}/></Appearance>{geom}</Shape>{extra}</Transform>")
        pos_by_idx[i] = p
        L["commits"].append([round(p[0], 3), round(p[1], 3), round(p[2], 3), round(s, 3)])
    S.w("</Group>")

    S.w("<Group DEF='HelixGuides'>")
    # Claude-session threads
    sess = collections.defaultdict(list)
    for i, (name, c) in enumerate(rows):
        for sname in c.get("sessions", []):
            sess[sname].append(i)
    multi = [v for v in sess.values() if len({rows[j][0] for j in v}) > 1]
    single = [v for v in sess.values() if len({rows[j][0] for j in v}) == 1]
    S.lines("thread", CORAL, [[pos_by_idx[j] for j in v] for v in single], width=1.0, transparency=0.55)
    S.lines("thread2", CORAL, [[pos_by_idx[j] for j in v] for v in multi], width=2.0, transparency=0.2)
    # day rings at each day's start, lane rings at the clock face
    ring = lambda xc, r, n=40: [(xc, r * math.cos(2 * math.pi * j / n), -r * math.sin(2 * math.pi * j / n)) for j in range(n + 1)]
    S.lines("dayring", "#2a3b48", [ring(day_x[d][0], r_out + 0.12, 36) for d in days], width=1.0)
    S.lines("axis", "#3a4d5b", [[(-HELIX_LEN / 2 - 1.2, 0, 0), (x_end + 0.8, 0, 0)]], width=1.0)
    # busiest day
    peak = max(per_day.items(), key=lambda kv: (kv[1], kv[0]))[0]
    pxc = sum(day_x[peak]) / 2
    S.lines("peak", BONE, [ring(pxc, r_out + 0.45, 56)], width=3.0)
    S.label([f"busiest day · {per_day[peak]} commits", _short_day(peak) + " " + peak[:4]], (pxc, r_out + 1.25, 0), 0.34, BONE, view="peak")
    # gap markers
    for gx, g in gap_marks:
        S.lines("gap", "#5d6f7d", [[(gx, -0.25, 0), (gx, 0.25, 0)], [(gx - 0.12, -0.25, 0), (gx - 0.12, 0.25, 0)]], width=1.0)
        S.label([f"+{g} d"], (gx, r_out + 0.55, 0), 0.24, DIM, view="helix")
    # day labels below the helix, greedily placed in three rows so none overlaps
    rows_used = [[], [], []]
    size = 0.24
    for d in days:
        xc = sum(day_x[d]) / 2
        txt = [_short_day(d), str(per_day[d])]
        wdt = max(len(t) for t in txt) * size * 0.62
        placed = False
        for ri, used in enumerate(rows_used):
            if all(xc + wdt / 2 + 0.06 < a or xc - wdt / 2 - 0.06 > b for a, b in used):
                used.append((xc - wdt / 2, xc + wdt / 2))
                S.label(txt, (xc, -r_out - 0.75 - ri * 0.72, 0), size, LABEL if per_day[d] >= 10 else DIM, view="helix")
                placed = True
                break
        if not placed:
            S.lines("tick", "#3a4d5b", [[(xc, -r_out - 0.3, 0), (xc, -r_out - 0.5, 0)]])
    # month labels
    seen_m = set()
    for d in days:
        m = d[:7]
        if m in seen_m:
            continue
        seen_m.add(m)
        x0 = day_x[d][0]
        S.lines("month", "#5d6f7d", [[(x0, -r_out - 2.8, 0), (x0, r_out + 0.2, 0)]], width=1.0, transparency=0.5)
        S.label([dt.date.fromisoformat(d).strftime("%b %Y")], (x0 + 0.1, -r_out - 3.1, 0), 0.34, BONE, justify="BEGIN", view="helix")
    # lane key at the start of the helix
    xs = -HELIX_LEN / 2 - 1.0
    S.lines("lanekey", "#2f4150", [ring(xs, lane[n], 36) for n in lanes_order], width=1.0)
    for n in lanes_order:
        S.label([n], (xs - 0.15, lane[n], 0.02), 0.2, DIM, justify="END", billboard=False, view="helix")
    # series of the fork (from the masterclass)
    if curated and curated.get("series"):
        by_sha = {(n, c.get("sha", "")[:7]): i for i, (n, c) in enumerate(rows) if c.get("sha")}
        for s_ in curated["series"]:
            idx = [by_sha[(s_["repo"], h)] for h in s_["shas"] if (s_["repo"], h) in by_sha]
            if not idx:
                continue
            p = pos_by_idx[min(idx)]
            S.label([s_["id"]], (p[0], p[1] + 0.32, p[2]), 0.2, CLS_COL["you"], view="helix")
    # the clock face at the end of the axis
    xf = x_end + 0.9
    S.lines("lanering", "#2f4150", [ring(xf, lane[n], 48) for n in lanes_order], width=1.0)
    spokes = []
    for h in range(24):
        th = 2 * math.pi * h / 24
        spokes.append([(xf, R0 * 0.6 * math.cos(th), -R0 * 0.6 * math.sin(th)), (xf, (r_out + 0.2) * math.cos(th), -(r_out + 0.2) * math.sin(th))])
    S.lines("spoke", "#24343f", spokes, width=1.0)
    hours = prof_signal(prof, "rhythm")["hours"]
    mx = max(max(hours["you"]), max(hours["ai"]), 1)
    bars = []
    for h in range(24):
        th = 2 * math.pi * (h + 0.5) / 24
        for who, r_a, sign, lmax in (("you", r_out + 0.3, 1, 2.6), ("ai", R0 - 0.12, -1, R0 - 0.35)):
            n = hours[who][h]
            if not n:
                continue
            ln = lmax * n / mx
            r_mid = r_a + sign * ln / 2
            cx, cy, cz = xf, r_mid * math.cos(th), -r_mid * math.sin(th)
            bars.append((who, (cx, cy, cz), ln, -th, n))
    for who, (cx, cy, cz), ln, ang, n in bars:
        S.w(f"<Transform translation='{v3((cx, cy, cz))}' rotation='1 0 0 {f(ang, 4)}'><Shape>{S.lit('bar' + who, CLS_COL[who], 0.5)}"
            f"<Box size='0.05 {f(ln)} 0.16'/></Shape></Transform>")
    for h in range(0, 24, 3):
        th = 2 * math.pi * h / 24
        rr = r_out + 3.3
        S.label([f"{h:02d}"], (xf + 0.05, rr * math.cos(th), -rr * math.sin(th)), 0.36, BONE, view="rhythm")
    S.label(["UTC hour", "outside: HagopJay", "inside: Claude"], (xf + 0.05, -(r_out + 4.6), 0), 0.26, DIM, view="rhythm")
    S.w("</Group>")
    L["regions"]["helix"] = {"center": [0, 0, 0], "x0": round(-HELIX_LEN / 2, 3), "x1": round(x_end, 3), "r": round(r_out, 3), "face_x": round(xf, 3)}

    # ================= tie constellation
    ext_ids = {"Web3DConsortium/x3d_mcp": "upstream", "hagsmac (account)": "hagsmac"}
    nodes: dict[str, dict] = {}
    # The owner's lineage order first, then the rest. ".prov" is no repo (the vocabulary reads it as x3d.ML), so it gets no node.
    inner = [n for n in ["x3d-blender-io", "x3d.ML", "x3d_ai", "x3d.onto", "x3d.xray", "x3d.poc", "KeGG", "shellmcp"] if n in repos]
    outer = [n for n in ["graphify", "x3d.jepa-notebooks", "x3dom", "splat-transform", "x3d-coopilot", "blender-mcp"] if n in repos]
    externals = ["hagsmac", "physics-stress", "polyxandria", "genealogy-loom"]
    if curated:
        cur_ids = {t["from"] for t in curated["ties"]} | {t["to"] for t in curated["ties"]}
        externals = [e for e in externals if e in cur_ids or e == "hagsmac"]

    def place(name, ang, rad, kind):
        p = (CT[0] + rad * math.cos(ang), CT[1] + rad * math.sin(ang), CT[2])
        nodes[name] = {"p": p, "kind": kind}

    place("x3d_mcp", 0, 0, "fork-active")
    place("upstream", -math.pi / 2, 3.4, "external")
    for i, n in enumerate(inner):
        kind = "private" if per_repo.get(n, {}).get("visibility") == "private" else repos[n].get("kind", "own")
        place(n, math.pi / 2 - 2 * math.pi * i / len(inner), 7.2, "own" if kind == "own" else kind)
    ring2 = outer + externals
    for i, n in enumerate(ring2):
        kind = repos[n].get("kind") if n in repos else "external"
        place(n, math.pi / 2 - 2 * math.pi * (i + 0.5) / len(ring2), 12.0, kind)
    for n, nd in nodes.items():
        oc = per_repo.get(n, {}).get("own_commits", 0)
        nd["r"] = 0.32 + 0.17 * math.log2(1 + oc) if n in per_repo else 0.3
    S.w("<Group DEF='Ties'>")
    S.w("<TouchSensor DEF='TS_ties' description='repo or tie'/>")
    for n, nd in nodes.items():
        p, r = nd["p"], nd["r"]
        kind = nd["kind"]
        hexc = NODE_COL.get(kind, NODE_COL["external"])
        dn = "n_" + "".join(ch if ch.isalnum() else "_" for ch in n)
        if kind == "external":
            S.w(f"<Transform DEF='{dn}' translation='{v3(p)}' rotation='0 0 1 0.7854'><Shape><Appearance><Material DEF='{dn}_m' diffuseColor='{col(hexc)}' "
                f"emissiveColor='{col(hexc, 0.3)}'/></Appearance><Box size='{f(r * 1.3)} {f(r * 1.3)} {f(r * 1.3)}'/></Shape></Transform>")
        else:
            S.w(f"<Transform DEF='{dn}' translation='{v3(p)}'><Shape><Appearance><Material DEF='{dn}_m' diffuseColor='{col(hexc)}' "
                f"emissiveColor='{col(hexc, 0.3)}'/></Appearance><Sphere radius='{f(r)}'/></Shape></Transform>")
        L["nodes"][n] = {"p": [round(c, 3) for c in p], "r": round(r, 3), "kind": kind, "def": dn}
        # who-wrote-it ring
        bc = per_repo.get(n, {}).get("by_class", {})
        tot = sum(bc.values())
        if tot:
            pts, idx, cols = [], [], []
            a = math.pi / 2
            r1, r2 = r + 0.1, r + 0.34
            for cl in CLASSES:
                cnt = bc.get(cl, 0)
                if not cnt:
                    continue
                span = 2 * math.pi * cnt / tot
                steps = max(2, int(span / 0.12))
                for st in range(steps):
                    a0 = a - span * st / steps
                    a1 = a - span * (st + 1) / steps
                    b = len(pts)
                    for (aa, rr) in ((a0, r1), (a1, r1), (a1, r2), (a0, r2)):
                        pts.append(v3((p[0] + rr * math.cos(aa), p[1] + rr * math.sin(aa), p[2] + 0.02)))
                    idx.append(f"{b} {b + 1} {b + 2} {b + 3} -1")
                    cols.append(col(CLS_COL[cl]))
                a -= span
            S.w(f"<Shape>{S.mat('ringc', '')}<IndexedFaceSet solid='false' colorPerVertex='false' coordIndex='{' '.join(idx)}'>"
                f"<Coordinate point='{', '.join(pts)}'/><Color color='{', '.join(cols)}'/></IndexedFaceSet></Shape>")
        pr = per_repo.get(n)
        sub_ = []
        if pr:
            if pr["own_commits"]:
                sub_.append(f"{pr['own_commits']} own commits")
            if pr.get("tests"):
                sub_.append(f"{_fmt_n(pr['tests'])} tests")
            if not pr["own_commits"]:
                sub_.append("idle fork" if pr.get("kind") == "fork-idle" else "")
        if n == "x3d.xray":
            sub_.append("this tool")
            S.lines("selfring", CORAL, [[(p[0] + (r + 0.55) * math.cos(2 * math.pi * j / 40), p[1] + (r + 0.55) * math.sin(2 * math.pi * j / 40), p[2])
                                         for j in range(41)]], width=2.5)
        if kind == "private":
            sub_.append("private")
        if n == "x3d.ML" and ".prov" in ds.get("lineage", []):
            sub_.append("holds the .prov stage")
        S.label([n] + [x for x in [" · ".join(x for x in sub_ if x)] if x], (p[0], p[1] + r + 0.75, p[2] + 0.1), 0.34 if pr else 0.28,
                BONE if pr and pr["own_commits"] else DIM, view="ties")

    def node_id(n):
        if n in ext_ids:
            return ext_ids[n]
        return n

    # fork stubs for forks whose upstream was not harvested
    for p_ in ties.get("pairs", []):
        if p_["to"].endswith(" (upstream)"):
            fork = p_["from"]
            if fork in nodes:
                a = nodes[fork]["p"]
                d_ = norm(sub(a, CT)) if a != CT else (0, 1, 0)
                b = add(a, mul(d_, nodes[fork]["r"] + 1.1))
                S.lines("stub", "#4c5a66", [[add(a, mul(d_, nodes[fork]["r"])), b]], width=1.0)
                S.w(f"<Transform translation='{v3(b)}'><Shape>{S.lit('stubn', NODE_COL['stub'], 0.3)}<Sphere radius='0.12'/></Shape></Transform>")
    # machine ties: straight lines, one shape each
    for i, p_ in enumerate(ties.get("pairs", [])):
        a_id, b_id = node_id(p_["from"]), node_id(p_["to"])
        if a_id not in nodes or b_id not in nodes:
            continue
        a, b = nodes[a_id]["p"], nodes[b_id]["p"]
        wsum = sum(v for k_, v in p_["kinds"].items())
        wdt = 1.0 + min(3.0, math.log10(1 + wsum) * 1.2)
        d = norm(sub(b, a))
        a2 = add(a, mul(d, nodes[a_id]["r"] + 0.12))
        b2 = sub(b, mul(d, nodes[b_id]["r"] + 0.12))
        dn = f"tm{i}"
        S.w(f"<Shape DEF='{dn}'><Appearance><Material DEF='{dn}_m' diffuseColor='0 0 0' emissiveColor='{col(GRADE_COL[p_['grade']])}' transparency='0.15'/>"
            f"<LineProperties linewidthScaleFactor='{f(wdt)}'/></Appearance><IndexedLineSet coordIndex='0 1 -1'><Coordinate point='{v3(a2)}, {v3(b2)}'/>"
            f"</IndexedLineSet></Shape>")
        mid = mul(add(a2, b2), 0.5)
        L["ties"]["machine"].append({"i": i, "from": p_["from"], "to": p_["to"], "grade": p_["grade"], "p": [round(c, 3) for c in mid], "def": dn})
        pins = p_["kinds"].get("pin", 0)
        if pins >= 10:
            S.label([f"{_fmt_n(pins)} pins"], (mid[0], mid[1], mid[2] + 0.15), 0.22, GRADE_COL[p_["grade"]], view="ties")
    # curated ties: arcs toward the viewer, with arrowheads
    if curated:
        groups = collections.defaultdict(list)
        for t in curated["ties"]:
            groups[tuple(sorted((t["from"], t["to"])))].append(t)
        for key, ts in groups.items():
            for j, t in enumerate(ts):
                if t["from"] not in nodes or t["to"] not in nodes or t["from"] == t["to"]:
                    continue
                a, b = nodes[t["from"]]["p"], nodes[t["to"]]["p"]
                d = norm(sub(b, a))
                perp = (-d[1], d[0], 0.0)
                off = (j - (len(ts) - 1) / 2) * 0.55
                h = 1.6 + 0.45 * (j % 5)
                ctrl = add(add(mul(add(a, b), 0.5), mul(perp, off * 1.6)), (0, 0, h))
                a2 = add(a, mul(norm(sub(ctrl, a)), nodes[t["from"]]["r"] + 0.1))
                b2 = add(b, mul(norm(sub(ctrl, b)), nodes[t["to"]]["r"] + 0.1))
                pts = []
                for s_ in range(21):
                    u = s_ / 20
                    pts.append(add(add(mul(a2, (1 - u) ** 2), mul(ctrl, 2 * u * (1 - u))), mul(b2, u * u)))
                dn = f"ta_{t['id']}"
                gc = GRADE_COL.get(t["grade"], DIM)
                S.w(f"<Shape DEF='{dn}'><Appearance><Material DEF='{dn}_m' diffuseColor='0 0 0' emissiveColor='{col(gc)}'/>"
                    f"<LineProperties linewidthScaleFactor='1.6'/></Appearance><IndexedLineSet coordIndex='{' '.join(str(q) for q in range(len(pts)))} -1'>"
                    f"<Coordinate point='{', '.join(v3(q) for q in pts)}'/></IndexedLineSet></Shape>")
                tip = pts[17]
                tang = sub(pts[18], pts[16])
                rx, ry, rz, ra = rot_y_to(tang)
                S.w(f"<Transform translation='{v3(tip)}' rotation='{f(rx, 4)} {f(ry, 4)} {f(rz, 4)} {f(ra, 4)}'><Shape>{S.lit('cone' + t['grade'], gc, 0.6)}"
                    f"<Cone bottomRadius='0.11' height='0.3'/></Shape></Transform>")
                apex = pts[10]
                S.label([f"{t['id']} {t['kind'] or ''}".strip()], (apex[0], apex[1], apex[2] + 0.12), 0.2, gc, view="ties")
                L["ties"]["curated"].append({"id": t["id"], "from": t["from"], "to": t["to"], "grade": t["grade"], "p": [round(c, 3) for c in apex], "def": dn})
    S.w("</Group>")
    L["regions"]["ties"] = {"center": list(CT), "r": 13.5}

    # ================= code city
    S.w("<Group DEF='City'>")
    S.w("<TouchSensor DEF='TS_city' description='file'/>")
    order = sorted(per_repo, key=lambda n: (-(per_repo[n]["files"] or 0), -(per_repo[n]["own_commits"]), n))
    xcur, zcur, row_depth = CC[0], CC[2], 0.0
    ROW_W = 30.0
    for n in order:
        pr = per_repo[n]
        files = repos[n].get("files") or []
        nfiles = pr.get("files") or 0
        side = 2.0 + 0.9 * math.log2(1 + nfiles / 40) if nfiles else 1.6
        if xcur + side > CC[0] + ROW_W:
            xcur = CC[0]
            zcur += row_depth + 2.2
            row_depth = 0.0
        x0, z0 = xcur, zcur
        cx, cz = x0 + side / 2, z0 + side / 2
        priv = pr.get("visibility") == "private"
        plate = NODE_COL["private"] if priv else ("#1d2a33" if nfiles else "#141d24")
        if n == "x3d.xray":
            plate = "#4a2a22"
        dn = "d_" + "".join(ch if ch.isalnum() else "_" for ch in n)
        S.w(f"<Transform DEF='{dn}' translation='{v3((cx, CC[1] - 0.06, cz))}'><Shape><Appearance><Material DEF='{dn}_m' diffuseColor='{col(plate)}' "
            f"emissiveColor='{col(plate, 0.4)}'/></Appearance><Box size='{f(side + 0.3)} 0.1 {f(side + 0.3)}'/></Shape></Transform>")
        items, roofs = [], []
        if files:
            top = sorted(files, key=lambda q: -(q.get("lines") or (q.get("bytes", 0) / 40)))[:64]
            top.sort(key=lambda q: (q["kind"], -(q.get("lines") or 0)))
            cols_ = max(1, math.ceil(math.sqrt(len(top))))
            cell = side / cols_
            churn_sorted = sorted((q.get("churn", 0) for q in top), reverse=True)
            hot_cut = max(1000, churn_sorted[max(0, len(churn_sorted) // 10 - 1)] if churn_sorted else 1000)
            for j, q in enumerate(top):
                gx, gz = j % cols_, j // cols_
                bx0 = x0 + gx * cell + cell * 0.11
                bz0 = z0 + gz * cell + cell * 0.11
                bw = cell * 0.78
                lines_ = q.get("lines") or (q.get("bytes", 0) / 40)
                hgt = 0.06 + 0.42 * math.log2(1 + lines_ / 25)
                items.append((bx0, CC[1], bz0, bx0 + bw, CC[1] + hgt, bz0 + bw, KIND_COL.get(q["kind"], KIND_COL["other"])))
                ch = q.get("churn", 0)
                if ch >= hot_cut:
                    rh = 0.05 + 0.05 * math.log10(ch)
                    roofs.append((bx0 - 0.01, CC[1] + hgt, bz0 - 0.01, bx0 + bw + 0.01, CC[1] + hgt + rh, bz0 + bw + 0.01, HEAT))
                L["city"]["buildings"].append({"repo": n, "path": q["path"], "kind": q["kind"], "lines": q.get("lines"), "churn": ch,
                                               "p": [round(bx0 + bw / 2, 3), round(CC[1] + hgt, 3), round(bz0 + bw / 2, 3)]})
            S.boxes("bld", items, def_=dn + "_b")
            S.boxes("roof", roofs, emissive="#7a3a12")
            hot = sorted(top, key=lambda q: -q.get("churn", 0))[:2]
            for q in hot:
                b = next(bb for bb in L["city"]["buildings"] if bb["repo"] == n and bb["path"] == q["path"])
                S.label([os.path.basename(q["path"])[:34], f"{_k(q.get('churn', 0))} lines churned"], (b["p"][0], b["p"][1] + 0.55, b["p"][2]),
                        0.2, HEAT, view="city")
        elif priv or nfiles:
            hgt = 0.06 + 0.42 * math.log2(1 + (pr.get("lines_at_tip") or 0) / 25 / max(1, nfiles))
            bh = max(0.6, hgt * 2.5)
            S.boxes("bld", [(cx - 0.5, CC[1], cz - 0.5, cx + 0.5, CC[1] + bh, cz + 0.5, NODE_COL["private"])])
            if priv:
                # H01: private is a role the repo plays, so it is drawn as a translucent shell over the opaque counts block.
                S.w(f"<Transform translation='{v3((cx, CC[1] + bh / 2 + 0.05, cz))}'><Shape><Appearance><Material diffuseColor='{col(NODE_COL['private'])}' "
                    f"emissiveColor='{col(NODE_COL['private'], 0.5)}' transparency='0.72'/></Appearance><Box size='{f(side - 0.1)} {f(bh + 0.5)} {f(side - 0.1)}'/></Shape></Transform>")
        cov = (pr.get("coverage") or {}).get("tree")
        fog = not files and not priv and cov != "measured"
        if fog:
            # H11: what was not measured is drawn as fog, never as an empty lot that reads as zero.
            for k_, (hh, tr) in enumerate(((1.1, 0.8), (1.8, 0.88))):
                S.w(f"<Transform translation='{v3((cx, CC[1] + hh / 2, cz))}'><Shape><Appearance><Material diffuseColor='{col(FOG)}' "
                    f"emissiveColor='{col(FOG, 0.55)}' transparency='{tr}'/></Appearance><Box size='{f(side + 0.1 - 0.3 * k_)} {f(hh)} {f(side + 0.1 - 0.3 * k_)}'/></Shape></Transform>")
            S.label(["?", "unknown"], (cx, CC[1] + 2.35, cz), 0.42, FOG, view="city")
        title = [n + ("  · this tool" if n == "x3d.xray" else "") + ("  · private" if priv else "")]
        bits = []
        if nfiles:
            bits += [f"{_fmt_n(nfiles)} files", f"{_k(pr.get('lines_at_tip') or 0)} lines"]
        if pr.get("x3d_files"):
            bits.append(f"{_fmt_n(pr['x3d_files'])} X3D")
        if pr.get("tests"):
            bits.append(f"{_fmt_n(pr['tests'])} tests")
        if fog:
            bits.append(("idle fork; " if pr.get("kind") == "fork-idle" else "") + "tree not measured: unknown, not zero")
        elif priv:
            bits.append("contents private: counts only")
        S.label(title + [" · ".join(bits)], (cx, CC[1] + 0.12, z0 + side + 0.55), 0.26 if nfiles else 0.2,
                BONE if nfiles else DIM, billboard=False, view="city")
        L["city"]["districts"][n] = {"center": [round(cx, 3), CC[1], round(cz, 3)], "side": round(side, 3), "def": dn,
                                     "coverage": "fog" if fog else ("private" if priv else "measured")}
        xcur += side + 1.6
        row_depth = max(row_depth, side + 0.9)
    # kind legend
    lx, lz = CC[0], CC[2] - 1.6
    leg = []
    for j, (kname, kc) in enumerate(KIND_COL.items()):
        bx = lx + j * 2.4
        leg.append((bx, CC[1], lz, bx + 0.3, CC[1] + 0.3, lz + 0.3, kc))
        S.label([kname], (bx + 0.45, CC[1] + 0.15, lz + 0.15), 0.22, DIM, justify="BEGIN", billboard=False, view="city")
    S.boxes("legend", leg)
    S.label(["hot roof: churn in the district's top tenth, at least a thousand lines"], (lx, CC[1] + 0.15, lz - 0.7), 0.22, HEAT,
            justify="BEGIN", billboard=False, view="city")
    S.w("</Group>")
    city_x1 = max(d["center"][0] + d["side"] / 2 for d in L["city"]["districts"].values())
    city_z1 = max(d["center"][2] + d["side"] / 2 for d in L["city"]["districts"].values())
    L["regions"]["city"] = {"center": [round((CC[0] + city_x1) / 2, 3), CC[1], round((CC[2] + city_z1) / 2, 3)],
                            "x0": CC[0], "x1": round(city_x1, 3), "z0": CC[2], "z1": round(city_z1, 3)}

    # ================= concepts and bridges (x3dray/structure.py)
    if con:
        L["regions"]["concepts"] = concepts_region(S, L, con)
    if bri:
        from .profile import top_bridges as _tb
        kinds = {n: ("private" if pr.get("visibility") == "private" else (repos[n].get("kind") or "own")) for n, pr in per_repo.items()}
        L["regions"]["bridges"] = bridges_region(S, L, bri, kinds, _tb(bri["files"]["variants"]["count"]), _tb(bri["files"]["variants"]["jaccard"]))

    # ================= the tool's own pipeline
    S.w("<Group DEF='Self'>")
    S.w("<TouchSensor DEF='TS_self' description='pipeline stage'/>")
    stages = [s_ for s_ in selfd["pipeline"] if s_["stage"] not in ("ask", "mcp")]
    rt = [s_ for s_ in selfd["pipeline"] if s_["stage"] in ("ask", "mcp")]
    sp = 2.75
    x0 = -sp * (len(stages) - 1) / 2
    prev = None
    for j, st in enumerate(stages):
        p = (x0 + j * sp, SELF_Y, SELF_Z)
        ai = bool(st.get("model"))
        hexc = CORAL if ai else "#5d6f7d"
        dn = "s_" + st["stage"]
        S.w(f"<Transform DEF='{dn}' translation='{v3(p)}'><Shape><Appearance><Material DEF='{dn}_m' diffuseColor='{col(hexc)}' emissiveColor='{col(hexc, 0.35)}'/>"
            f"</Appearance><Box size='2.3 1.15 0.5'/></Shape></Transform>")
        mods = [m_.strip() for m_ in st["module"].split("+")]
        hs = [st.get("sha256", {}).get(m_, "")[:8] for m_ in mods]
        S.label([st["stage"]], (p[0], p[1] + 0.22, p[2] + 0.27), 0.34, BONE, billboard=False, view="self")
        S.label([_some([m_.split("/")[-1] for m_ in mods]), "sha " + _some([h for h in hs if h]),               # every module named, with its own hash
                 f"{_fmt_n(st.get('lines', 0))} lines · " + ("Claude" if ai else "no model")],
                (p[0], p[1] - 0.27, p[2] + 0.27), 0.17, LABEL, billboard=False, view="self")
        L["self"]["stages"][st["stage"]] = [round(c, 3) for c in p]
        if prev:
            S.lines("pipe", "#8fa3b1", [[(prev[0] + 1.17, prev[1], prev[2]), (p[0] - 1.17, p[1], p[2])]], width=2.0)
        prev = p
    page_p = L["self"]["stages"].get("page", prev)
    for j, st in enumerate(rt):
        p = (page_p[0] + (j - 0.5) * sp, SELF_Y - 2.2, SELF_Z)
        dn = "s_" + st["stage"]
        S.w(f"<Transform DEF='{dn}' translation='{v3(p)}'><Shape><Appearance><Material DEF='{dn}_m' diffuseColor='{col(CORAL)}' emissiveColor='{col(CORAL, 0.35)}'/>"
            f"</Appearance><Box size='2.3 1.0 0.5'/></Shape></Transform>")
        S.label([st["stage"] + (" (in the page)" if st["stage"] == "ask" else " (any agent)")], (p[0], p[1] + 0.18, p[2] + 0.27), 0.26, BONE, billboard=False, view="self")
        S.label([_some([m_.strip().split("/")[-1] for m_ in st["module"].split("+")]), "Claude" if st["stage"] == "ask" else "same tools"],
                (p[0], p[1] - 0.24, p[2] + 0.27), 0.17, LABEL, billboard=False, view="self")
        L["self"]["stages"][st["stage"]] = [round(c, 3) for c in p]
        S.lines("pipe", "#8fa3b1", [[(page_p[0], page_p[1] - 0.6, page_p[2]), (p[0], p[1] + 0.5, p[2])]], width=1.5)
    # who wrote the family, and who wrote this tool
    cnt = prof_signal(prof, "who_commits")["counts"]
    tot = sum(cnt.values()) or 1
    bw = sp * (len(stages) - 1) + 2.3
    bx = x0 - 1.15
    items = []
    for cl in CLASSES:
        n_ = cnt.get(cl, 0)
        if not n_:
            continue
        wseg = bw * n_ / tot
        items.append((bx, SELF_Y + 1.1, SELF_Z - 0.1, bx + wseg, SELF_Y + 1.4, SELF_Z + 0.1, CLS_COL[cl]))
        if wseg > 1.2:
            S.label([f"{ {'you': 'HagopJay', 'ai': 'Claude', 'bot': 'bots', 'other': 'other', 'upstream': 'unmapped'}[cl]} {round(100 * n_ / tot)}%"],
                    (bx + wseg / 2, SELF_Y + 1.25, SELF_Z + 0.16), 0.2, "#0b141c", billboard=False, view="self")
        bx += wseg
    S.boxes("whobar", items)
    S.label([f"who wrote the family's {_fmt_n(tot)} commits"], (x0 - 1.15, SELF_Y + 1.75, SELF_Z), 0.26, DIM, justify="BEGIN", billboard=False, view="self")
    tool = selfd["tool"]
    me = selfd["in_its_own_data"]
    S.label([f"x3d.xray {tool['version']} · head {tool['git']['head'] or '?'} · {tool['modules']} modules · {_fmt_n(tool['lines_of_code'])} lines · code {tool['code_digest']}",
             f"in its own data: {me['commits_in_data']} commit(s) at harvest {str(me['harvested_at'])[:16]} UTC"],
            (x0 - 1.15, SELF_Y - 1.0, SELF_Z + 0.27), 0.2, CORAL, justify="BEGIN", billboard=False, view="self")
    # lines from the tool's row to its own place in the other views
    anchor = L["self"]["stages"].get("compile", prev)
    targets = []
    xi = [i for i, (n, _) in enumerate(rows) if n == "x3d.xray"]
    if xi:
        targets.append(pos_by_idx[xi[-1]])
    if "x3d.xray" in nodes:
        targets.append(nodes["x3d.xray"]["p"])
    if "x3d.xray" in L["city"]["districts"]:
        targets.append(tuple(L["city"]["districts"]["x3d.xray"]["center"]))
    S.lines("selfref", CORAL, [[(anchor[0], anchor[1] + 0.6, anchor[2]), t] for t in targets], width=1.5, transparency=0.35)
    S.w("</Group>")
    L["regions"]["self"] = {"center": [0, SELF_Y, SELF_Z]}

    S.w("<Group DEF='Notes'/>")
    S.w("<Group DEF='Hi'/>")

    # ================= viewpoints
    reg = L["regions"]
    hx0, hx1 = reg["helix"]["x0"], reg["helix"]["x1"]
    bm = prof_signal(prof, "busiest_month")
    bx0 = day_x[min(d for d in days if d >= bm["from"])][0]
    bx1 = day_x[max(d for d in days if d <= bm["to"])][1]
    city_c = reg["city"]["center"]
    stage_n = len([s_ for s_ in selfd["pipeline"] if s_["stage"] not in ("ask", "mcp")])
    halfw = {   # how much width each wide view must keep in frame, in scene units either side of its centre
        "overview": max(7 - (CT[0] - 13.5), reg["city"]["x1"] + 1.5 - 7),
        "helix": (hx1 - hx0) / 2 + 1.0,
        "ties": 14.0,
        "city": (reg["city"]["x1"] - reg["city"]["x0"]) / 2 + 1.5,
        "self": 2.75 * (stage_n - 1) / 2 + 1.6,
    }
    for rid in ("concepts", "bridges"):
        if rid in reg:
            halfw[rid] = (reg[rid]["x1"] - reg[rid]["x0"]) / 2 + 0.8
    views = {
        "overview": ((7, 42, 118), (7, 8, 0), 0.74),
        "helix": (((hx0 + hx1) / 2, 3.5, 30), ((hx0 + hx1) / 2, -1.2, 0), 0.9),
        "burst": (((bx0 + bx1) / 2, 4.0, (bx1 - bx0) * 0.95 + 5), ((bx0 + bx1) / 2, -0.8, 0), 0.9),
        "peak": ((pxc + 1.5, 3.0, 10.5), (pxc, 0.2, 0), 0.8),
        "rhythm": ((reg["helix"]["face_x"] + 52, 0, 0), (reg["helix"]["face_x"], 0, 0), 0.34),
        "claude": (((hx0 + hx1) / 2, 9.0, 27), ((hx0 + hx1) / 2, 0, 0), 0.9),
        "ties": ((CT[0], 1.5, 37), (CT[0], 0, 0), 0.85),
        "fork": ((nodes["x3d_mcp"]["p"][0] + 2.5, nodes["x3d_mcp"]["p"][1] + 3, 15), nodes["x3d_mcp"]["p"], 0.85),
        "city": ((city_c[0], 19, city_c[2] + 25), (city_c[0], -4, city_c[2]), 0.85),
        "self": ((0, SELF_Y + 2.5, SELF_Z + 22), (0, SELF_Y, SELF_Z), 0.85),
    }
    for rid in ("concepts", "bridges"):
        if rid in reg:
            c_ = reg[rid]["center"]
            mid = ((reg[rid]["x0"] + reg[rid]["x1"]) / 2, c_[1], c_[2])
            views[rid] = ((mid[0], mid[1] + 1.2, mid[2] + 24), mid, 0.85)
    if "x3d_mcp" in L["city"]["districts"]:
        dc = L["city"]["districts"]["x3d_mcp"]["center"]
        views["hotspots"] = ((dc[0] + 2, dc[1] + 7, dc[2] + 9), (dc[0], dc[1] + 1.5, dc[2]), 0.85)
    # H07: a Viewpoint is named by the claim it backs ("label: claim"), not by where the camera stands.
    labels = {v["id"]: (v["label"] + ": " + v["claim"]["text"]) if v.get("claim") else v["label"] for v in spec.get("views", [])}
    vp_xml = []
    for vid, (pos, tgt, fov) in views.items():
        o = look_at(pos, tgt)
        vp_xml.append(f"<Viewpoint DEF='VP_{vid}' description='{esc_attr(labels.get(vid, vid))}' position='{v3(pos)}' "
                      f"orientation='{f(o[0], 4)} {f(o[1], 4)} {f(o[2], 4)} {f(o[3], 4)}' centerOfRotation='{v3(tgt)}' fieldOfView='{f(fov)}'/>")
        L["views"][vid] = {"def": f"VP_{vid}", "position": [round(c, 3) for c in pos], "orientation": [round(c, 4) for c in o],
                           "center": [round(c, 3) for c in tgt], "fov": fov}
        if vid in halfw:   # the page widens fieldOfView on a narrow stage so this half-width stays in frame
            L["views"][vid]["halfw"] = round(halfw[vid], 2)
    vp_xml.append("<Viewpoint DEF='VP_fly' description='Fly' position='0 30 104' fieldOfView='0.8'/>")
    vp_xml.append("<Viewpoint DEF='VP_fly2' description='Fly' position='0 30 104' fieldOfView='0.8'/>")
    head = [
        "<?xml version='1.0' encoding='UTF-8'?>",
        "<X3D profile='Immersive' version='4.0' xmlns:xsd='http://www.w3.org/2001/XMLSchema-instance' "
        "xsd:noNamespaceSchemaLocation='https://www.web3d.org/specifications/x3d-4.0.xsd'>",
        "<head>",
        f"<meta name='title' content='{esc_attr(spec.get('title', 'x3d-ray'))}'/>",
        f"<meta name='description' content='{esc_attr(spec.get('subtitle', ''))}'/>",
        f"<meta name='generator' content='x3dray {__version__} (x3dray/compiler.py, stdlib Python, no model)'/>",
        f"<meta name='created' content='{esc_attr(str(selfd['tool'].get('built_at') or ''))[:10]}'/>",
        f"<meta name='source-digest' content='{source_digest(ds, ties, curated, prof)}'/>",
        "</head>",
        "<Scene>",
        "<NavigationInfo DEF='Nav' type='\"EXAMINE\" \"ANY\"' headlight='true' transitionType='\"LINEAR\"' transitionTime='1.6'/>",
        f"<Background DEF='Sky' skyColor='{col(SKY)}'/>",
        "<DirectionalLight direction='-0.3 -1 -0.45' intensity='0.5'/>",
        *vp_xml,
    ]
    xml = "\n".join(head + S.out + ["</Scene>", "</X3D>", ""])
    L["labels"] = S.label_texts
    L["counts"] = {"commits": len(rows), "labels": S.labels, "nodes": len(nodes), "machine_ties": len(L["ties"]["machine"]),
                   "curated_ties": len(L["ties"]["curated"]), "buildings": len(L["city"]["buildings"]), "days": len(days)}
    return xml, L


def prof_signal(prof: dict, sid: str) -> dict:
    return next(s for s in prof["signals"] if s["id"] == sid)["values"]


def source_digest(*objs) -> str:
    h = hashlib.sha256()
    for o in objs:
        h.update(json.dumps(o, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:16]


def build(data_dir: str, out_dir: str) -> dict:
    j = lambda n: json.load(open(os.path.join(data_dir, n), encoding="utf-8"))
    ds, ti, prof, spec_r, selfd = j("dataset.json"), j("ties.json"), j("profile.json"), j("spec.rendered.json"), j("self.json")
    opt = lambda n: j(n) if os.path.exists(os.path.join(data_dir, n)) else None
    cur = opt("curated.json")
    xml, layout = build_scene(ds, ti, cur, prof, spec_r, selfd, opt("concepts.json"), opt("bridges.json"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "xray.x3d"), "w", encoding="utf-8") as fh:
        fh.write(xml)
    with open(os.path.join(out_dir, "layout.json"), "w", encoding="utf-8") as fh:
        json.dump(layout, fh, ensure_ascii=False, separators=(",", ":"))
    return {"scene": os.path.join(out_dir, "xray.x3d"), "bytes": len(xml.encode("utf-8")), **layout["counts"]}
