"""The look gallery: the twelve declared looks as small multiples in the X3D scene, one panel each, in declared order.

Producer side. Every number drawn is a stored integer (or stored string) of `bundle.eda.looks`, put through the scale rules below with
exact integer arithmetic, so a bar's height is never a float product. `x3dray/gallerycheck.py` is the independent reader: it derives the
same expectations from the bundle by its own code (it imports nothing from here, from the compiler, `eda`, `looks` or `stats`) and
compares them with the XML.

A panel, in panel-local units (the panel sits at the grid position of its index):
  slab   8 x 0.2 x 5.4, top at y = 0, coloured by grade (Confirmed, Likely, Suspected, none); a look that did not run is a hollow slab
  bar 1  effect size, 6/1000 unit per permille of the stored effect, blue when the first group is larger, coral otherwise
  bar 2  the share of label shuffles at least as extreme, floor(1000 * k_ge / M) permille, same scale
  bar 3  q, floor(1000 * q) permille, same scale
  cubes  forks, leave-one-repo-out, repo strata, interleaved halves: pass, fail, or hollow when the check could not run
  text   the id, the effect, the shuffle count with q, and the grade, or "not run" with the stored reason
No bar is drawn for zero: absence is the zero, and a hollow slab is the only mark of "not run", never a flat bar.

What it does not show: why a grade was given (the stored reason, in the ledger and the Ask tools), anything about private repos (the panels
name looks, never repos), or that the numbers are right (the data audit and the recompute check those).
"""
from __future__ import annotations

import hashlib
import json

COLS = 6
PITCH_X, PITCH_Z = 9.0, 15.0
ORIGIN = (0.0, -48.0, 40.0)
LAYER_DEF, TOUCH_DEF = "XrayGallery", "TS_gallery"
SLAB = (8.0, 0.2, 5.4)
BAR = 1.2
BAR_X = (-2.6, 0.0, 2.6)
BAR_Z = 0.6
MILLI_PER_PERMILLE = 6            # 1000 permille is 6 units, so one permille is 0.006
CUBE, CUBE_X, CUBE_Y, CUBE_Z = 0.6, (-1.8, -0.6, 0.6, 1.8), 0.3, -2.0
LABEL_Y = 7.4
LABEL_SIZE = 0.46
GRADE_HEX = {"C": "#5cc28a", "L": "#e6c75a", "S": "#e8875a", None: "#6f8796"}
GRADE_WORD = {"C": "Confirmed", "L": "Likely", "S": "Suspected", None: "no grade"}
POS_HEX, NEG_HEX, NULL_HEX, Q_HEX = "#5ac8e0", "#e8795d", "#a68fdc", "#dce8ef"
PASS_HEX, FAIL_HEX, NA_HEX = "#5cc28a", "#e8875a", "#6f8796"
TEXT_HEX, DIM_HEX = "#dce8ef", "#8fa3b1"
HOLLOW = 0.65
GLOW = 0.35                      # solid glyphs glow at this share of their colour, as the rest of the scene does, so a headlight does not wash them out
REASON_CHARS = 44
CHECKS = ("forks", "loro", "strata", "halves")


def _fmt(x: int) -> str:
    """Thousandths as an exact decimal string with trailing zeros stripped; x is an integer count of thousandths."""
    sign = "-" if x < 0 else ""
    x = abs(x)
    s = f"{x // 1000}.{x % 1000:03d}".rstrip("0").rstrip(".")
    return sign + ("0" if s == "" else s)


def _hex(h: str, k: float = 1.0) -> str:
    h = h.lstrip("#")
    return " ".join(_fmt(round(int(h[i:i + 2], 16) / 255 * 1000 * k)) for i in (0, 2, 4))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;")


def _meta(name: str, record: dict) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"<MetadataString containerField='metadata' name='{name}' value='{_esc(json.dumps(payload, ensure_ascii=True))}'/>"


def _mf(lines: list[str]) -> str:
    return " ".join('"' + _esc(x).replace("\\", "\\\\").replace('"', '\\"') + '"' for x in lines)


def panel_def(finding_id: str) -> str:
    return "gp" + hashlib.sha256(json.dumps([finding_id], ensure_ascii=True).encode()).hexdigest()[:20]


def grid(index: int) -> tuple[str, str, str]:
    """The panel's translation as exact thousandth strings: column and row from the declared index, centred on the origin's x."""
    col, row = index % COLS, index // COLS
    x = round(ORIGIN[0] * 1000) + round((col - (COLS - 1) / 2) * PITCH_X * 1000)
    return _fmt(x), _fmt(round(ORIGIN[1] * 1000)), _fmt(round(ORIGIN[2] * 1000) + round(row * PITCH_Z * 1000))


def check_states(f: dict) -> list[str]:
    c = f.get("checks") or {}
    out = []
    fk = c.get("forks") or {}
    out.append("na" if not fk.get("evaluable") else ("pass" if fk.get("agree") == fk.get("evaluable") else "fail"))
    lo = c.get("loro") or {}
    out.append("na" if not lo.get("total") else ("pass" if lo.get("agree") == lo.get("total") else "fail"))
    st = c.get("strata") or {}
    out.append("na" if not st.get("n") else ("pass" if not st.get("opposite") else "fail"))
    hv = c.get("halves") or {}
    out.append("na" if not hv.get("evaluable") else ("pass" if hv.get("replicates") else "fail"))
    return out


def bars(f: dict) -> list[tuple[str, int, str]]:
    """(role, permille, hex) for each bar the stored values call for; a bar of zero is not drawn."""
    e, nm, q = f.get("effect") or {}, f.get("null_model") or {}, f.get("q")
    out = []
    pe = abs(int(e.get("permille") or 0))       # a Cliff's delta is signed, a total-variation distance is not; the bar is the magnitude
    if pe > 0:
        out.append(("effect", pe, POS_HEX if (e.get("direction") or 0) >= 0 else NEG_HEX))
    if nm.get("M"):
        pn = (1000 * int(nm["k_ge"])) // int(nm["M"])
        if pn > 0:
            out.append(("null", pn, NULL_HEX))
    if q:
        pq = min(1000, (1000 * int(q[0])) // int(q[1]))
        if pq > 0:
            out.append(("q", pq, Q_HEX))
    return out


def label_lines(f: dict) -> list[str]:
    if f.get("status") != "evaluated":
        return [f["id"], "not run", str(f.get("reason") or "")[:REASON_CHARS]]
    e, nm = f["effect"], f["null_model"]
    word = "distance" if e.get("kind") == "tv" else "effect"
    value = f"{int(e['permille'])}" if e.get("kind") == "tv" else f"{int(e['permille']):+d}"
    return [f["id"], f"{word} {value}/1000", f"{nm['k_ge']} of {nm['M']} shuffles, q {f['q_text']}", GRADE_WORD[f.get("grade")]]


def region_lines(looks: dict) -> dict[str, list[str]]:
    fs = looks["findings"]
    ev = [f for f in fs if f.get("status") == "evaluated"]
    n = {g: sum(1 for f in ev if f.get("grade") == g) for g in ("C", "L", "S")}
    none = sum(1 for f in ev if f.get("grade") not in ("C", "L", "S"))
    return {"title": [f"The {len(fs)} declared looks, one panel each"],
            "subtitle": [f"{len(ev)} evaluated, {len(fs) - len(ev)} not run · Confirmed {n['C']} · Likely {n['L']} · Suspected {n['S']} · no grade {none}"],
            "legend": ["bars: effect, share of shuffles at least as extreme, q (each out of 1000) · cubes: forks, leave-one-repo-out, repos, halves"]}


def _glyph(panel_id: str, role: str, pos: tuple[str, str, str], hexc: str, size: tuple[str, str, str], hollow: bool = False) -> str:
    tr = f" transparency='{_fmt(round(HOLLOW * 1000))}'" if hollow else ""
    return (f"<Transform translation='{' '.join(pos)}'>{_meta('xray.glyph', {'panel': panel_id, 'role': role})}<Shape><Appearance>"
            f"<Material diffuseColor='{_hex(hexc)}' emissiveColor='{_hex(hexc, GLOW)}'{tr}/></Appearance><Box size='{' '.join(size)}'/></Shape></Transform>")


def _text(meta: dict, pos: tuple[str, str, str], lines: list[str], hexc: str, size: float, font: str, justify: str, billboard: bool, name: str) -> str:
    shape = (f"<Shape><Appearance><Material diffuseColor='0 0 0' emissiveColor='{_hex(hexc)}'/></Appearance><Text string='{_mf(lines)}' solid='false'>"
             f"<FontStyle family='{font}' justify='\"{justify}\" \"MIDDLE\"' size='{_fmt(round(size * 1000))}'/></Text></Shape>")
    body = f"<Billboard axisOfRotation='0 0 0'>{shape}</Billboard>" if billboard else shape
    return f"<Transform translation='{' '.join(pos)}'>{_meta(name, meta)}{body}</Transform>"


def region(looks: dict, font: str) -> dict:
    """The gallery's XML lines, label records, pick records, region extent and plan. Pure: the same looks give the same bytes."""
    fs = looks["findings"]
    xml = [f"<Group DEF='{LAYER_DEF}'>", f"<TouchSensor DEF='{TOUCH_DEF}' description='look'/>"]
    labels, records = [], []
    ox, oy, oz = (round(v * 1000) for v in ORIGIN)
    # region text sits above the first row, centred on the origin's x
    top = (ox, oy + round((LABEL_Y + 3.0) * 1000), oz - round(6.0 * 1000))
    for k, (name, size, dy) in enumerate((("title", 0.9, 0), ("subtitle", 0.5, -1500), ("legend", 0.34, -2400))):
        pos = (_fmt(top[0]), _fmt(top[1] + dy), _fmt(top[2]))
        lines = region_lines(looks)[name]
        xml.append(_text({"role": name}, pos, lines, TEXT_HEX if k == 0 else DIM_HEX, size, font, "MIDDLE", False, "xray.gallery_text"))
        labels.append({"text": " / ".join(lines), "view": "gallery", "pos": [float(p) for p in pos]})
    for i, f in enumerate(fs):
        pid, run = f["id"], f.get("status") == "evaluated"
        px, py, pz = grid(i)
        xml.append(f"<Transform DEF='{panel_def(pid)}' translation='{px} {py} {pz}'>{_meta('xray.panel', {'id': pid, 'index': i})}")
        hollow = not run
        xml.append(_glyph(pid, "slab", ("0", _fmt(-round(SLAB[1] * 500)), "0"), GRADE_HEX.get(f.get("grade") if run else None, GRADE_HEX[None]),
                          (_fmt(round(SLAB[0] * 1000)), _fmt(round(SLAB[1] * 1000)), _fmt(round(SLAB[2] * 1000))), hollow))
        for role, permille, hexc in bars(f) if run else []:
            h = permille * MILLI_PER_PERMILLE
            x = BAR_X[("effect", "null", "q").index(role)]
            xml.append(_glyph(pid, role, (_fmt(round(x * 1000)), _fmt(h // 2), _fmt(round(BAR_Z * 1000))), hexc,
                              (_fmt(round(BAR * 1000)), _fmt(h), _fmt(round(BAR * 1000)))))
        for k, state in enumerate(check_states(f) if run else ["na"] * 4):
            xml.append(_glyph(pid, "check_" + CHECKS[k], (_fmt(round(CUBE_X[k] * 1000)), _fmt(round(CUBE_Y * 1000)), _fmt(round(CUBE_Z * 1000))),
                              {"pass": PASS_HEX, "fail": FAIL_HEX, "na": NA_HEX}[state], (_fmt(round(CUBE * 1000)),) * 3, state == "na"))
        lines = label_lines(f)
        xml.append(_text({"panel": pid, "role": "label"}, ("0", _fmt(round(LABEL_Y * 1000)), "0"), lines, TEXT_HEX if run else DIM_HEX, LABEL_SIZE, font, "MIDDLE", True,
                         "xray.glyph"))
        xml.append("</Transform>")
        labels.append({"text": " / ".join(lines), "view": "gallery", "pos": [float(px), float(py) + LABEL_Y, float(pz)]})
        records.append({"id": pid, "i": i, "p": [float(px), float(py), float(pz)]})
    xml.append("</Group>")
    half = (COLS - 1) / 2 * PITCH_X + PITCH_X / 2 + 4.0      # the end labels are wider than their slabs
    rows = (len(fs) + COLS - 1) // COLS
    centre = [ORIGIN[0], ORIGIN[1] + 3.0, ORIGIN[2] + (rows - 1) * PITCH_Z / 2]
    plan = {"schema_version": 1, "scope": "gallery", "layer_def": LAYER_DEF, "touch_def": TOUCH_DEF, "origin": list(ORIGIN), "font": font,
            "unverified": ["text size and font", "the camera", "rendered pixels", "why a grade was given"]}
    return {"xml": xml, "labels": labels, "records": records, "region": {"center": centre, "x0": round(ORIGIN[0] - half, 3), "x1": round(ORIGIN[0] + half, 3)},
            "plan": plan}
