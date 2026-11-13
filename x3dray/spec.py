"""The spec: what the page shows and says. An AI writes its prose; code fills every number.

A spec is JSON with views (id, label, viewpoint, caption), a tour (stops: view, caption, optional action) and
suggested questions. Captions hold no numbers of their own. Each number is a slot that code fills:

  {busiest_day.commits}            signal busiest_day, values.commits
  {busiest_day.day|date}           ... formatted as a date ("25 Oct 2026")
  {who_commits.shares.ai|pct}      ... as a percentage
  {repo:x3d_mcp/tests|n}           profile.per_repo["x3d_mcp"]["tests"], with thousands separators
  {self:claude_in_the_data.commits}  self.json, by dotted path

The fact gate (check) fails a caption with a digit outside a slot, a slot that does not resolve to a number or
a short string, a view the scene lacks, or a tour action the page does not know. Names that contain digits
(x3d_mcp, X3D, S07, E09) are allowed. `python -m x3dray spec --brief` prints what an AI author needs: the
rules, the schema and every slot with its current value.
"""

from __future__ import annotations

import datetime as dt
import copy
import json
import math
import os
import re

SLOT = re.compile(r"\{([^{}|]+)(?:\|(\w+))?\}")
NAMEISH = re.compile(r"(?<![\w.])[A-Za-z_][\w\-]*(?:\.[A-Za-z_][\w\-]*)*\d[\w.\-]*|(?<![\w.])[A-Za-z_][\w\-]*\d[\w\-]*")
FORMATS = {"n", "pct", "date", "hour", "raw", "k"}
ACTIONS = {"highlight", "color_by", "xray", "clear"}
VIEWS_KNOWN = {"overview", "helix", "burst", "peak", "rhythm", "ties", "fork", "city", "hotspots", "self", "claude", "concepts", "bridges"}
MAX_CAPTION = 420
MAX_CLAIM = 200
# A view's claim is one sentence the view backs (a Viewpoint named as a claim, not a position). basis says who is speaking:
# git (counted from git by code), tool (x3d.xray's description of itself) or reading (Claude's reading of the data).
BASES = {"git": "git says", "tool": "the tool says", "reading": "Claude reads"}
HOW_BUILT_TOPICS = {"pipeline", "stage", "signal_code", "module", "gates", "receipt", "self_in_data", "claude", "limits", "spec", "identity"}

# Claims remain optional for backwards-compatible exhibits. Their presence does
# not prove their prose: this module checks structure, references and formatting.
_HIGHLIGHT_FIELDS = {k: {"type": "string"} for k in ("repo", "who", "from", "to", "day", "text", "sha", "session")}
_HIGHLIGHT_FIELDS.update({k: {"type": "boolean"} for k in ("merges", "trailer", "xray", "clear")})
ACTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "highlight": {"type": "object", "additionalProperties": False, "properties": _HIGHLIGHT_FIELDS},
        "color_by": {"type": "object", "required": ["mode"], "additionalProperties": False,
                     "properties": {"mode": {"type": "string", "enum": ["who", "repo", "size", "branch", "age"]}}},
        "xray": {"type": "object", "required": ["on"], "additionalProperties": False,
                 "properties": {"on": {"type": "boolean"}}},
        "clear": {"type": "object", "additionalProperties": False, "properties": {}},
    },
}

SCHEMA = {
    "type": "object", "required": ["family", "title", "views", "tour", "questions"],
    "properties": {
        "family": {"type": "string"}, "title": {"type": "string"}, "subtitle": {"type": "string"},
        "author": {"type": "object"},
        "views": {"type": "array", "items": {"type": "object", "required": ["id", "label", "caption"],
                                             "properties": {"id": {"enum": sorted(VIEWS_KNOWN)}, "label": {"type": "string"},
                                                            "caption": {"type": "string"},
                                                            "claim": {"type": "object", "required": ["text", "backs", "basis"],
                                                                      "properties": {"text": {"type": "string"}, "backs": {"type": "array", "items": {"type": "string"}},
                                                                                     "basis": {"enum": sorted(BASES)}}}}}},
        "tour": {"type": "array", "items": {"type": "object", "required": ["view", "caption"],
                                            "properties": {"view": {"type": "string"}, "title": {"type": "string"},
                                                           "caption": {"type": "string"},
                                                           "action": ACTION_SCHEMA}}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
}


def validate_shape(value, schema: dict = SCHEMA, path: str = "spec") -> list[str]:
    """Validate the deliberately small JSON-Schema subset used above, stdlib only.

    This is not a general JSON Schema implementation. Keep the exported schema
    and this walk aligned when adding constraints. Unknown top-level metadata is
    allowed; action names and their argument keys are deliberately closed.
    """
    problems = []
    kind = schema.get("type")
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
               "string": isinstance(value, str), "boolean": isinstance(value, bool)}
    if kind and not matches[kind]:
        return [f"{path}: expected {kind}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: value is not one of the supported options")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{path}: missing required field {name}")
        props = schema.get("properties", {})
        for name, item in value.items():
            if name in props:
                problems += validate_shape(item, props[name], f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                problems.append(f"{path}: unknown field {name}")
    elif isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            problems += validate_shape(item, schema["items"], f"{path}[{i}]")
    return problems


def _human_date(s: str) -> str:
    d = dt.date.fromisoformat(str(s)[:10])
    return f"{d.day} {d.strftime('%b')} {d.year}"


def _get(obj, path: list[str]):
    for p in path:
        if isinstance(obj, dict) and p in obj:
            obj = obj[p]
        elif isinstance(obj, list) and p.isdigit() and int(p) < len(obj):
            obj = obj[int(p)]
        else:
            raise KeyError(".".join(path))
    return obj


def resolve(ref: str, profile: dict, selfd: dict | None = None):
    ref = ref.strip()
    if ref.startswith("repo:"):
        name, _, field = ref[5:].partition("/")
        return _get(profile["per_repo"][name], field.split(".")) if field else name
    if ref.startswith("self:"):
        return _get(selfd or {}, ref[5:].split("."))
    sid, _, path = ref.partition(".")
    sig = next((s for s in profile["signals"] if s["id"] == sid), None)
    if sig is None:
        raise KeyError(sid)
    return _get(sig["values"], path.split(".")) if path else sig["text"]


def fmt(v, f: str | None) -> str:
    if f == "date":
        return _human_date(v)
    if f == "hour":
        return f"{int(v):02d}:00 UTC"
    if f == "pct":
        return f"{v}%"
    if f == "k":
        return f"{v / 1000:.1f}k" if isinstance(v, (int, float)) and v >= 1000 else str(v)
    if f == "raw":
        return str(v)
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.1f}"
    return str(v)


def fill(text: str, profile: dict, selfd: dict | None = None) -> tuple[str, list[dict], list[str]]:
    slots, problems = [], []
    if not isinstance(text, str):
        return "?", [], ["text must be a string"]

    def sub(m):
        ref, f = m.group(1), m.group(2)
        if f and f not in FORMATS:
            problems.append(f"unknown format |{f} in {{{ref}}}")
            return "?"
        try:
            v = resolve(ref, profile, selfd)
        except (KeyError, TypeError, IndexError):
            problems.append(f"slot {{{ref}}} does not resolve")
            return "?"
        if not isinstance(v, (str, int, float, bool)) or (isinstance(v, str) and len(v) > 80):
            problems.append(f"slot {{{ref}}} is not a number or a short string")
            return "?"
        if isinstance(v, float) and not math.isfinite(v):
            problems.append(f"slot {{{ref}}} is not finite")
            return "?"
        if f in {"n", "pct", "k", "hour"} and (isinstance(v, bool) or not isinstance(v, (int, float))):
            problems.append(f"slot {{{ref}}} needs a number for |{f}")
            return "?"
        if f == "hour" and (not 0 <= v < 24 or int(v) != v):
            problems.append(f"slot {{{ref}}} needs an integer UTC hour from zero to twenty-three")
            return "?"
        if f == "date" and not isinstance(v, str):
            problems.append(f"slot {{{ref}}} needs an ISO date string for |date")
            return "?"
        try:
            s = fmt(v, f)
        except (ValueError, TypeError, OverflowError):
            problems.append(f"slot {{{ref}}} cannot use format |{f or 'default'}")
            return "?"
        slots.append({"ref": ref, "format": f, "value": v, "text": s})
        return s

    out = SLOT.sub(sub, text)
    bare = SLOT.sub(" ", text)
    if "{" in bare or "}" in bare:
        problems.append("malformed slot syntax")
    bare = NAMEISH.sub(" ", bare)
    digits = re.findall(r"\d[\d,.:%]*", bare)
    if digits:
        problems.append("digits outside a slot: " + ", ".join(sorted(set(digits))))
    return out, slots, problems


def render_spec(spec: dict, profile: dict, selfd: dict | None = None) -> dict:
    problems = validate_shape(spec)
    # Build metadata is not source prose. A deep copy preserves the source
    # templates even if a caller later mutates its input or the rendered views.
    source = copy.deepcopy({k: v for k, v in spec.items() if k not in {"source_spec", "source", "problems"}}) if isinstance(spec, dict) else None
    if problems:
        return {"family": source.get("family") if source else None, "title": source.get("title", "") if source else "",
                "views": [], "tour": [], "questions": [], "source_spec": source, "problems": problems}
    spec = source
    if profile.get("family") is not None and profile["family"] != spec["family"]:
        problems.append("spec family does not match profile family")
    out = {k: v for k, v in spec.items() if k not in ("views", "tour", "questions", "subtitle")}
    out["source_spec"] = copy.deepcopy(source)
    if "subtitle" in spec:
        out["subtitle"], _, p = fill(spec["subtitle"], profile, selfd)
        problems += [f"subtitle: {x}" for x in p]
    view_ids = []
    out["views"] = []
    for v in spec.get("views", []):
        if v["id"] not in VIEWS_KNOWN:
            problems.append(f"view {v['id']}: the scene has no such view")
        view_ids.append(v["id"])
        cap, slots, p = fill(v.get("caption", ""), profile, selfd)
        problems += [f"view {v['id']}: {x}" for x in p]
        if len(cap) > MAX_CAPTION:
            problems.append(f"view {v['id']}: caption is {len(cap)} characters, over {MAX_CAPTION}")
        claim = None
        if v.get("claim"):
            c = v["claim"]
            ctext, cslots, cp = fill(c.get("text", ""), profile, selfd)
            problems += [f"view {v['id']} claim: {x}" for x in cp]
            basis = c.get("basis")
            if basis not in BASES:
                problems.append(f"view {v['id']} claim: basis {basis!r} is not one of {', '.join(sorted(BASES))}")
            if basis == "git" and not cslots:
                problems.append(f"view {v['id']} claim: a git claim must carry at least one slot")
            if len(ctext) > MAX_CLAIM:
                problems.append(f"view {v['id']} claim: {len(ctext)} characters, over {MAX_CLAIM}")
            sig_ids = {s_["id"] for s_ in profile["signals"]}
            for b in c.get("backs", []):
                if b.startswith("how_built:"):
                    if b[10:] not in HOW_BUILT_TOPICS:
                        problems.append(f"view {v['id']} claim: backs {b}, which is no how_built topic")
                elif b not in sig_ids:
                    problems.append(f"view {v['id']} claim: backs {b}, which is no signal")
            if not c.get("backs"):
                problems.append(f"view {v['id']} claim: backs nothing")
            claim = {"text": ctext, "template": c.get("text", ""), "backs": c.get("backs", []), "basis": basis,
                     "says": BASES.get(basis, basis), "slots": cslots}
        out["views"].append({**v, "caption": cap, "slots": slots, "template": v.get("caption", ""), "claim": claim})
    if len(view_ids) != len(set(view_ids)):
        problems.append("views: duplicate view ids")
    out["tour"] = []
    for i, st in enumerate(spec.get("tour", [])):
        if st["view"] not in view_ids:
            problems.append(f"tour {i + 1}: view {st['view']} is not in views")
        act = st.get("action") or {}
        filled_act = {}
        for k, args in act.items():
            if k not in ACTIONS:
                problems.append(f"tour {i + 1}: unknown action {k}")
            if isinstance(args, dict):
                fa = {}
                for kk, vv in args.items():
                    if isinstance(vv, str) and "{" in vv:
                        vv, _, p2 = fill(vv, profile, selfd)
                        problems += [f"tour {i + 1} action {k}.{kk}: {x}" for x in p2]
                    fa[kk] = vv
                filled_act[k] = fa
            else:
                filled_act[k] = args
        cap, slots, p = fill(st.get("caption", ""), profile, selfd)
        problems += [f"tour {i + 1}: {x}" for x in p]
        title, tslots, tp = fill(st.get("title", ""), profile, selfd)
        problems += [f"tour {i + 1} title: {x}" for x in tp]
        if len(cap) > MAX_CAPTION:
            problems.append(f"tour {i + 1}: caption is {len(cap)} characters, over {MAX_CAPTION}")
        cites = sorted({s["ref"].split(".")[0] for s in slots + tslots if not s["ref"].startswith(("repo:", "self:"))})
        out["tour"].append({**st, "action": filled_act, "title": title, "caption": cap, "slots": slots + tslots, "cites": cites,
                            "template": st.get("caption", "")})
    out["questions"] = []
    for q in spec.get("questions", []):
        qq, _, p = fill(q, profile, selfd)
        problems += [f"question {q[:30]!r}: {x}" for x in p]
        out["questions"].append(qq)
    out["problems"] = problems
    return out


def verify_rendered_spec(data_dir: str) -> list[str]:
    """Re-render saved source templates against current profile/self data.

    Detects stale or edited filled output, including an edited `problems` list.
    Saved source provenance is not independently authenticated; this does not
    establish natural-language entailment or independently verify profile data.
    Old artifacts without source_spec must be regenerated by the spec stage.
    """
    def read(name, optional=False):
        path = os.path.join(data_dir, name)
        if optional and not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)

    try:
        saved, profile, selfd = read("spec.rendered.json"), read("profile.json"), read("self.json", optional=True)
    except (OSError, ValueError):
        return ["fact inputs: missing or invalid JSON"]
    if not isinstance(saved, dict) or not isinstance(saved.get("source_spec"), dict):
        return ["rendered spec lacks source_spec; rerun the spec stage"]
    if not isinstance(profile, dict) or not isinstance(profile.get("signals"), list) or not isinstance(profile.get("per_repo"), dict):
        return ["profile: invalid shape for spec verification"]
    if not isinstance(selfd, dict):
        return ["self: invalid shape for spec verification"]
    try:
        expected = render_spec(saved["source_spec"], profile, selfd)
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        return ["fact inputs: invalid referenced data"]
    problems = list(expected["problems"])
    actual = {k: v for k, v in saved.items() if k != "source"}
    if actual != expected:
        problems.append("rendered spec differs from its source templates and current profile/self data")
    return problems


def _flatten(prefix: str, v, out: list, depth: int = 0):
    if isinstance(v, dict) and depth < 3:
        for k, x in v.items():
            _flatten(f"{prefix}.{k}", x, out, depth + 1)
    elif isinstance(v, (int, float, str, bool)) and not (isinstance(v, str) and len(v) > 80):
        out.append((prefix, v))


def brief(profile: dict, selfd: dict | None = None) -> str:
    """Everything an AI needs to write a spec for this family: rules, schema, and every slot with its value."""
    rows = []
    for s in profile["signals"]:
        rows.append(f"\n# {s['id']} ({s['kind']}): {s['text'][:300]}")
        flat = []
        _flatten(s["id"], s["values"], flat)
        rows += [f"  {{{k}}} = {v!r}" for k, v in flat[:40]]
    rows.append("\n# per repo: {repo:<name>/<field>}")
    for name, r in profile["per_repo"].items():
        rows.append(f"  {name}: " + ", ".join(f"{k}={v}" for k, v in r.items() if isinstance(v, (int, str)) and k != "role"))
    if selfd:
        flat = []
        for key in ("tool", "in_its_own_data", "claude_in_the_data"):
            _flatten(f"self:{key}", selfd.get(key, {}), flat)
        rows.append("\n# the tool itself: {self:<path>}")
        rows += [f"  {{{k}}} = {v!r}" for k, v in flat]
    return "\n".join([
        "Write a spec for x3d-ray as one JSON object. The rules:",
        "- Prose only. Every number must be a slot from the list below; the fact gate fails any other digit.",
        "- Names with digits (x3d_mcp, X3D, S07, E09) are fine. Dates are slots with |date.",
        f"- Views come from: {', '.join(sorted(VIEWS_KNOWN))}. Tour actions: {', '.join(sorted(ACTIONS))}.",
        f"- Captions stay under {MAX_CAPTION} characters once filled. Say what the viewer sees, then what it means.",
        f"- Each view also makes one claim (under {MAX_CLAIM} characters): text, backs (signal ids or how_built:<topic>) and basis "
        f"({', '.join(f'{k}: {v}' for k, v in BASES.items())}). A git claim carries a slot.",
        "- Formats: |n thousands, |pct percent, |date day, |hour hour of day, |k thousands as 1.2k, |raw as is.",
        "", "Schema:", json.dumps(SCHEMA, indent=1), "", "Slots and their current values:", *rows,
    ])
