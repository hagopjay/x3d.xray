"""Independent reader for the look gallery in xray.x3d.

It checks what `x3dray/gallery.py` draws against `bundle.eda.looks`, using its own scale rules, its own grid, its own colours and its own
tolerance. It imports nothing from the compiler, the gallery producer, `eda`, `looks`, `stats` or the witness code, and it does not read the
plan for anything but the origin, the layer names and the font (the plan declares where the gallery sits; it cannot say what a number is).

What it establishes: the gallery layer holds exactly one panel per declared look, in declared order, at the grid position of its index; each
panel holds exactly the glyphs its stored values call for (a slab, a bar for each non-zero effect, shuffle share and q, four check cubes, one
label) with the size, position, colour and text those values give; nothing else is inside the layer; no DEF/USE sharing is used inside it.

What it does not establish: that the stored numbers are right (the data audit and the recompute check that), anything outside the layer
(the rest of the scene has its own checks or none; a gallery-looking marker found outside the layer is a finding, but ordinary geometry
outside is not read), the camera, text size as rendered, or pixels.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from decimal import Decimal as D

SCOPE = ("The look gallery: one panel per declared look in the gallery layer, with each bar, cube, slab and text matching the stored look. "
         "Not the numbers' correctness, the rest of the scene, the camera or pixels.")
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_FINDINGS = 80
TOL = D("0.0011")                        # exact thousandths are written; one permille of a bar is 0.006
COLS, PITCH_X, PITCH_Z = 6, D(9), D(15)
SLAB_SIZE = (D(8), D("0.2"), D("5.4"))
BAR_FOOT, BAR_X, BAR_Z, UNIT_PER_PERMILLE = D("1.2"), {"effect": D("-2.6"), "null": D(0), "q": D("2.6")}, D("0.6"), D("0.006")
CUBE, CUBE_Y, CUBE_Z = D("0.6"), D("0.3"), D(-2)
CUBE_X = {"forks": D("-1.8"), "loro": D("-0.6"), "strata": D("0.6"), "halves": D("1.8")}
LABEL_Y, LABEL_SIZE, HOLLOW = D("7.4"), D("0.46"), D("0.65")
REGION_TEXT = {"title": (D("0.9"), D(0)), "subtitle": (D("0.5"), D("-1.5")), "legend": (D("0.34"), D("-2.4"))}
REGION_Y, REGION_Z = D("10.4"), D(-6)
HEX = {"C": "5cc28a", "L": "e6c75a", "S": "e8875a", "none": "6f8796", "pos": "5ac8e0", "neg": "e8795d", "null": "a68fdc", "q": "dce8ef",
       "pass": "5cc28a", "fail": "e8875a", "na": "6f8796", "text": "dce8ef", "dim": "8fa3b1"}
WORDS = {"C": "Confirmed", "L": "Likely", "S": "Suspected"}
ALLOWED = {"Transform", "MetadataString", "Shape", "Appearance", "Material", "Box", "Billboard", "Text", "FontStyle", "TouchSensor"}
REASON_CHARS = 44
GLOW = D("0.35")


def _rgb(h: str) -> tuple[D, D, D]:
    return tuple(D(int(h[i:i + 2], 16)) / D(255) for i in (0, 2, 4))


def _nums(s: str | None, n: int) -> list[D] | None:
    try:
        v = [D(x) for x in (s or "").replace(",", " ").split()]
    except Exception:
        return None
    return v if len(v) == n else None


def _near(a: list[D] | None, b: tuple, tol: D = TOL) -> bool:
    return a is not None and len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def _mfstrings(s: str | None) -> list[str] | None:
    """An MFString attribute: zero or more double-quoted strings with backslash escapes. None if anything else is in it."""
    if s is None:
        return None
    out, pos = [], 0
    for m in re.finditer(r'\s*"((?:[^"\\]|\\.)*)"', s):
        if m.start() != pos:
            return None
        out.append(re.sub(r'\\(.)', r'\1', m.group(1)))
        pos = m.end()
    return out if s[pos:].strip() == "" else None


def _record(el: ET.Element) -> tuple[str, dict] | None:
    ms = [c for c in el if c.tag == "MetadataString"]
    if len(ms) != 1:
        return None
    v = _mfstrings(ms[0].get("value"))
    if v is None or len(v) != 1:
        return None
    try:
        rec = json.loads(v[0])
    except ValueError:
        return None
    return (ms[0].get("name") or ""), rec if isinstance(rec, dict) else {}


class _Findings:
    def __init__(self):
        self.items: list[dict] = []
        self.count = 0

    def add(self, code: str, message: str, record: dict | None = None):
        self.count += 1
        if len(self.items) < MAX_FINDINGS:
            f = {"code": code, "message": message}
            if record:
                f["record"] = record
            self.items.append(f)


def _expected_panel(f: dict) -> dict:
    """The panel's glyphs as this reader derives them from one stored look. Everything is Decimal arithmetic on stored integers."""
    run = f.get("status") == "evaluated"
    out: dict[str, dict] = {}
    grade = f.get("grade") if run and f.get("grade") in ("C", "L", "S") else "none"
    out["slab"] = {"pos": (D(0), -SLAB_SIZE[1] / 2, D(0)), "size": SLAB_SIZE, "hex": HEX[grade], "tr": HOLLOW if not run else D(0)}
    c = f.get("checks") or {}
    states = {}
    if run:
        fk, lo, st, hv = (c.get(k) or {} for k in ("forks", "loro", "strata", "halves"))
        states["forks"] = "na" if not fk.get("evaluable") else "pass" if fk.get("agree") == fk.get("evaluable") else "fail"
        states["loro"] = "na" if not lo.get("total") else "pass" if lo.get("agree") == lo.get("total") else "fail"
        states["strata"] = "na" if not st.get("n") else "fail" if st.get("opposite") else "pass"
        states["halves"] = "na" if not hv.get("evaluable") else "pass" if hv.get("replicates") else "fail"
    else:
        states = {k: "na" for k in ("forks", "loro", "strata", "halves")}
    for k, s in states.items():
        out["check_" + k] = {"pos": (CUBE_X[k], CUBE_Y, CUBE_Z), "size": (CUBE, CUBE, CUBE), "hex": HEX[s], "tr": HOLLOW if s == "na" else D(0)}
    if run:
        e, nm, q = f.get("effect") or {}, f.get("null_model") or {}, f.get("q")
        heights = {"effect": (abs(int(e.get("permille") or 0)), HEX["pos"] if (e.get("direction") or 0) >= 0 else HEX["neg"])}
        heights["null"] = ((1000 * int(nm["k_ge"])) // int(nm["M"]) if nm.get("M") else 0, HEX["null"])
        heights["q"] = (min(1000, (1000 * int(q[0])) // int(q[1])) if q else 0, HEX["q"])
        for role, (pm, hx) in heights.items():
            if pm > 0:
                h = D(pm) * UNIT_PER_PERMILLE
                out[role] = {"pos": (BAR_X[role], h / 2, BAR_Z), "size": (BAR_FOOT, h, BAR_FOOT), "hex": hx, "tr": D(0)}
        tv = e.get("kind") == "tv"
        lines = [f["id"], f"distance {int(e['permille'])}/1000" if tv else f"effect {int(e['permille']):+d}/1000", f"{nm['k_ge']} of {nm['M']} shuffles, q {f['q_text']}", WORDS.get(f.get("grade"), "no grade")]
    else:
        lines = [f["id"], "not run", str(f.get("reason") or "")[:REASON_CHARS]]
    out["label"] = {"pos": (D(0), LABEL_Y, D(0)), "lines": lines, "hex": HEX["text"] if run else HEX["dim"]}
    return out


def _grid(i: int, origin: list[D]) -> tuple[D, D, D]:
    return origin[0] + (D(i % COLS) - D(COLS - 1) / 2) * PITCH_X, origin[1], origin[2] + D(i // COLS) * PITCH_Z


def _region_lines(fs: list[dict]) -> dict[str, list[str]]:
    ev = [f for f in fs if f.get("status") == "evaluated"]
    g = {k: sum(1 for f in ev if f.get("grade") == k) for k in "CLS"}
    none = sum(1 for f in ev if f.get("grade") not in ("C", "L", "S"))
    return {"title": [f"The {len(fs)} declared looks, one panel each"],
            "subtitle": [f"{len(ev)} evaluated, {len(fs) - len(ev)} not run · Confirmed {g['C']} · Likely {g['L']} · Suspected {g['S']} · no grade {none}"],
            "legend": ["bars: effect, share of shuffles at least as extreme, q (each out of 1000) · cubes: forks, leave-one-repo-out, repos, halves"]}


def _tr(el: ET.Element) -> list[D] | None:
    return _nums(el.get("translation") or "0 0 0", 3)


def _appearance(shape: ET.Element) -> ET.Element | None:
    ap = [c for c in shape if c.tag == "Appearance"]
    mats = [m for a in ap for m in a if m.tag == "Material"] if len(ap) == 1 else []
    return mats[0] if len(mats) == 1 and len(ap[0]) == 1 else None


def verify_gallery(xml: str, plan: dict, eda: dict | None) -> dict:
    F = _Findings()
    checks = {"panels": 0, "glyphs": 0, "texts": 0, "values": 0}

    def out(extra=None):
        r = {"ok": F.count == 0, "scope": SCOPE, "checks": checks, "findings": F.items, "finding_count": F.count,
             "omitted_findings": max(0, F.count - len(F.items))}
        if extra:
            r.update(extra)
        return r

    if not isinstance(plan, dict) or plan.get("scope") != "gallery" or plan.get("schema_version") != 1:
        F.add("plan_invalid", "the gallery plan is missing or not schema 1")
        return out()
    looks = (eda or {}).get("looks") if isinstance(eda, dict) else None
    if not isinstance(looks, dict) or not isinstance(looks.get("findings"), list):
        F.add("source_missing", "bundle.eda.looks.findings is missing, so there is nothing to check the gallery against")
        return out()
    if len(xml.encode("utf-8", "replace")) > MAX_XML_BYTES:
        F.add("xml_too_large", "the scene is larger than the reader's limit")
        return out()
    if re.search(r"<!DOCTYPE|<!ENTITY", xml):
        F.add("xml_refused", "the scene declares a DOCTYPE or an entity")
        return out()
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        F.add("xml_parse", f"the scene does not parse: {e}")
        return out()
    try:
        origin = [D(str(v)) for v in plan["origin"]]
        layer_def, touch_def, font = str(plan["layer_def"]), str(plan["touch_def"]), str(plan["font"])
        assert len(origin) == 3
    except Exception:
        F.add("plan_invalid", "the gallery plan has no usable origin, layer, touch sensor or font")
        return out()
    layers = [el for el in root.iter("Group") if el.get("DEF") == layer_def]
    if len(layers) != 1:
        F.add("layer_count", f"expected exactly one gallery layer, found {len(layers)}")
        return out()
    layer = layers[0]
    inside = {id(e) for e in layer.iter()}
    for parent in root.iter():
        for c in parent:
            if c.tag == "MetadataString" and id(c) not in inside and (c.get("name") in ("xray.panel", "xray.gallery_text")):
                F.add("marker_outside_layer", "a gallery marker sits outside the gallery layer")
    fs = [f for f in looks["findings"] if isinstance(f, dict)]
    if len(fs) != len(looks["findings"]):
        F.add("source_invalid", "a stored look is not an object")
        return out()

    # -- the layer's direct children: one touch sensor, three region texts, one Transform per panel
    touches = [c for c in layer if c.tag == "TouchSensor"]
    if len(touches) != 1 or touches[0].get("DEF") != touch_def:
        F.add("touch_sensor", "the gallery layer must hold exactly one TouchSensor with the planned DEF")
    panels, texts = [], {}
    for c in layer:
        if c.tag == "TouchSensor":
            continue
        if c.tag != "Transform":
            F.add("unsupported_node", f"a {c.tag} is a direct child of the gallery layer")
            continue
        rec = _record(c)
        if rec and rec[0] == "xray.panel":
            panels.append((c, rec[1]))
        elif rec and rec[0] == "xray.gallery_text":
            role = rec[1].get("role")
            if role in texts or role not in REGION_TEXT:
                F.add("region_text", "a region text is duplicated or has an unknown role")
            else:
                texts[role] = c
        else:
            F.add("unregistered_child", "a child of the gallery layer is neither a panel nor a region text")
    for el in layer.iter():
        if el.get("USE") is not None:
            F.add("use_in_gallery", "the gallery uses DEF/USE sharing, which this reader does not follow")
        if el is not layer and el.tag not in ALLOWED:
            F.add("unsupported_node", f"a {el.tag} is inside the gallery layer")
        if el.get("DEF") is not None and not (el is layer or el in touches or el.tag == "Transform"):
            F.add("def_in_gallery", "only the layer, its touch sensor and the panels may carry a DEF")

    # -- region texts
    for role, (size, dy) in REGION_TEXT.items():
        el = texts.get(role)
        if el is None:
            F.add("region_text_missing", f"the {role} text is missing")
            continue
        _check_text(F, checks, el, (origin[0], origin[1] + REGION_Y + dy, origin[2] + REGION_Z), _region_lines(fs)[role],
                    HEX["text"] if role == "title" else HEX["dim"], size, font, False, f"region {role}")

    # -- panels
    if len(panels) != len(fs):
        F.add("panel_count", f"expected {len(fs)} panels, found {len(panels)}")
    seen = set()
    for i, f in enumerate(fs):
        pid = f.get("id")
        match = [p for p in panels if p[1].get("id") == pid]
        if len(match) != 1:
            F.add("panel_missing" if not match else "panel_duplicate", f"panel {pid}: found {len(match)}")
            continue
        el, rec = match[0]
        seen.add(id(el))
        checks["panels"] += 1
        if rec.get("index") != i:
            F.add("panel_order", f"panel {pid} is not at its declared index {i}")
        if panels.index(match[0]) != i:
            F.add("panel_order", f"panel {pid} is not in declared document order")
        if not _near(_tr(el), _grid(i, origin)):
            F.add("panel_position", f"panel {pid} is not at the grid position of index {i}", {"id": pid})
        try:
            exp = _expected_panel(f)
        except (KeyError, TypeError, ValueError, ZeroDivisionError, AttributeError):
            F.add("source_invalid", f"panel {pid}: the stored look is missing a value the panel needs", {"id": pid})
            continue
        _check_panel(F, checks, el, pid, exp, font)
    for el, rec in panels:
        if id(el) not in seen:
            F.add("panel_extra", "a panel does not correspond to a declared look")
    return out()


def _check_text(F, checks, tr_el, pos, lines, hexc, size, font, billboard, who):
    checks["texts"] += 1
    if not _near(_tr(tr_el), pos):
        F.add("text_position", f"{who}: text is not where the scale rule puts it")
    inner = [c for c in tr_el if c.tag != "MetadataString"]
    node = inner[0] if len(inner) == 1 else None
    if billboard:
        if node is None or node.tag != "Billboard":
            F.add("text_billboard", f"{who}: text is not in a Billboard")
            return
        sh = [c for c in node if c.tag == "Shape"]
    else:
        sh = [node] if node is not None and node.tag == "Shape" else []
    if len(sh) != 1:
        F.add("text_shape", f"{who}: expected one Shape holding the text")
        return
    txt = [c for c in sh[0] if c.tag == "Text"]
    if len(txt) != 1:
        F.add("text_node", f"{who}: expected exactly one Text")
        return
    got = _mfstrings(txt[0].get("string"))
    checks["values"] += len(lines)
    if got != lines:
        F.add("text_mismatch", f"{who}: the drawn text differs from the text the stored values give")
    fs_ = [c for c in txt[0] if c.tag == "FontStyle"]
    if len(fs_) != 1 or fs_[0].get("family") != font or abs(D(fs_[0].get("size") or "0") - size) > TOL:
        F.add("font", f"{who}: font family or size is not the planned one")
    m = _appearance(sh[0])
    if m is None or not _near(_nums(m.get("emissiveColor"), 3), _rgb(hexc)):
        F.add("text_colour", f"{who}: text colour differs")


def _check_panel(F, checks, el, pid, exp, font):
    glyphs: dict[str, list[ET.Element]] = {}
    for c in el:
        if c.tag == "MetadataString":
            continue
        if c.tag != "Transform":
            F.add("unsupported_node", f"panel {pid}: a {c.tag} is a direct child")
            continue
        rec = _record(c)
        if not rec or rec[0] != "xray.glyph" or rec[1].get("panel") != pid or not isinstance(rec[1].get("role"), str):
            F.add("glyph_unregistered", f"panel {pid}: a child is not a registered glyph of this panel")
            continue
        glyphs.setdefault(rec[1]["role"], []).append(c)
    for role in glyphs:
        if role not in exp:
            F.add("glyph_extra", f"panel {pid}: an unexpected {role} glyph", {"id": pid, "role": role})
    for role, spec in exp.items():
        found = glyphs.get(role, [])
        if len(found) != 1:
            F.add("glyph_missing" if not found else "glyph_duplicate", f"panel {pid}: {len(found)} {role} glyphs, expected 1", {"id": pid, "role": role})
            continue
        g = found[0]
        checks["glyphs"] += 1
        if role == "label":
            _check_text(F, checks, g, spec["pos"], spec["lines"], spec["hex"], LABEL_SIZE, font, True, f"panel {pid} label")
            continue
        if not _near(_tr(g), spec["pos"]):
            F.add("glyph_position", f"panel {pid}: {role} is not where its stored value puts it", {"id": pid, "role": role})
        inner = [c for c in g if c.tag != "MetadataString"]
        shape = inner[0] if len(inner) == 1 and inner[0].tag == "Shape" else None
        boxes = [c for c in shape if c.tag == "Box"] if shape is not None else []
        if len(boxes) != 1:
            F.add("glyph_shape", f"panel {pid}: {role} must be one Shape holding one Box", {"id": pid, "role": role})
            continue
        checks["values"] += 1
        if not _near(_nums(boxes[0].get("size"), 3), spec["size"]):
            F.add("glyph_size", f"panel {pid}: {role} has the wrong size for its stored value", {"id": pid, "role": role})
        m = _appearance(shape)
        want = _rgb(spec["hex"])
        glow = tuple(c * GLOW for c in want)
        if m is None or not _near(_nums(m.get("diffuseColor"), 3), want) or not _near(_nums(m.get("emissiveColor"), 3), glow):
            F.add("glyph_colour", f"panel {pid}: {role} has the wrong colour for its stored value", {"id": pid, "role": role})
        elif abs(D(m.get("transparency") or "0") - spec["tr"]) > TOL:
            F.add("glyph_hollow", f"panel {pid}: {role} is hollow or solid contrary to its stored value", {"id": pid, "role": role})
