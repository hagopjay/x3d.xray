"""Independent verification of the compiler's static constellation geometry.

This module deliberately imports no compiler, layout or producer math. It reads
the emitted XML, resolves DEF/USE, composes the X3D Transform matrix and compares
the actual semantic primitives with a declared plan and witnessed source ties.
It does not execute scripts, render pixels, verify decorations/arrowheads, or
authenticate the supplied plan/bundle. Other scene regions remain out of scope.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET

TOLERANCE = 0.003
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_ELEMENTS = 200_000
MAX_DEPTH = 256
MAX_FINDINGS = 80
MAX_EXPANDED_VISITS = 250_000
GRADE_COLOURS = {"C": (0.361, 0.761, 0.541), "L": (0.902, 0.780, 0.353), "S": (0.910, 0.529, 0.353)}
CONTAINERS = {"Transform": "children", "Group": "children", "Shape": "children", "TouchSensor": "children",
              "Sphere": "geometry", "Box": "geometry", "IndexedLineSet": "geometry", "Coordinate": "coord",
              "Appearance": "appearance", "Material": "material", "LineProperties": "lineProperties",
              "MetadataString": "metadata", "MetadataSet": "metadata"}
SCOPE = ("Static constellation entity glyphs and relation polylines in declared semantic layers; "
         "world transforms, source witnesses and grade colour checked. Decorations, arrowheads, "
         "other regions, runtime events, pixels and source authenticity are not verified.")


class _Invalid(Exception):
    def __init__(self, code):
        self.code = code


class _ExpansionLimit(Exception):
    """Stop the complete verification, not merely one local graph branch."""


def _identity():
    return ((1., 0., 0., 0.), (0., 1., 0., 0.), (0., 0., 1., 0.), (0., 0., 0., 1.))


def _multiply(a, b):
    value = tuple(tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)) for i in range(4))
    if any(not math.isfinite(v) or abs(v) > 1e15 for row in value for v in row):
        raise _Invalid("unbounded_world_transform")
    return value


def _point(matrix, p):
    value = tuple(sum(matrix[i][j] * p[j] for j in range(3)) + matrix[i][3] for i in range(3))
    if any(not math.isfinite(v) or abs(v) > 1e15 for v in value):
        raise _Invalid("unbounded_world_geometry")
    return value


def _translation(p):
    m = [list(row) for row in _identity()]
    for i in range(3):
        m[i][3] = p[i]
    return tuple(tuple(row) for row in m)


def _scale(s):
    return ((s[0], 0., 0., 0.), (0., s[1], 0., 0.), (0., 0., s[2], 0.), (0., 0., 0., 1.))


def _numbers(text, count=None):
    try:
        vals = tuple(float(v) for v in re.split(r"[\s,]+", text.strip()) if v)
    except (TypeError, ValueError, AttributeError):
        raise _Invalid("invalid_number") from None
    if count is not None and len(vals) != count:
        raise _Invalid("invalid_number_count")
    if any(not math.isfinite(v) or abs(v) > 1e12 for v in vals):
        raise _Invalid("nonfinite_or_unbounded_number")
    return vals


def _vector(value, count=3):
    if not isinstance(value, (list, tuple)) or len(value) != count or any(isinstance(v, bool) for v in value):
        raise _Invalid("invalid_plan_vector")
    return _numbers(" ".join(str(v) for v in value), count)


def _rotation(v):
    x, y, z, angle = v
    length = math.sqrt(x*x + y*y + z*z)
    if length == 0:
        if abs(angle) > 1e-12:
            raise _Invalid("invalid_rotation_axis")
        return _identity()
    x, y, z = x/length, y/length, z/length
    c, s, t = math.cos(angle), math.sin(angle), 1-math.cos(angle)
    return ((t*x*x+c, t*x*y-s*z, t*x*z+s*y, 0.),
            (t*x*y+s*z, t*y*y+c, t*y*z-s*x, 0.),
            (t*x*z-s*y, t*y*z+s*x, t*z*z+c, 0.), (0., 0., 0., 1.))


def _transform(node):
    t = _numbers(node.get("translation", "0 0 0"), 3)
    c = _numbers(node.get("center", "0 0 0"), 3)
    r = _numbers(node.get("rotation", "0 0 1 0"), 4)
    sr = _numbers(node.get("scaleOrientation", "0 0 1 0"), 4)
    s = _numbers(node.get("scale", "1 1 1"), 3)
    # X3D: T * C * R * SR * S * inverse(SR) * inverse(C).
    m = _identity()
    for part in (_translation(t), _translation(c), _rotation(r), _rotation(sr),
                 _scale(s), _rotation((*sr[:3], -sr[3])), _translation(tuple(-v for v in c))):
        m = _multiply(m, part)
    return m


def _close(a, b, tolerance=TOLERANCE):
    return len(a) == len(b) and all(abs(x-y) <= tolerance for x, y in zip(a, b))


def _basis_scales(m):
    columns = [tuple(m[i][j] for i in range(3)) for j in range(3)]
    scales = [math.sqrt(sum(x*x for x in col)) for col in columns]
    if any(s <= 1e-12 for s in scales):
        raise _Invalid("collapsed_semantic_geometry")
    for i in range(3):
        for j in range(i+1, 3):
            dot = sum(a*b for a, b in zip(columns[i], columns[j])) / (scales[i] * scales[j])
            if abs(dot) > 1e-6:
                raise _Invalid("unsupported_sheared_geometry")
    return scales


def _metadata(node, name):
    records = [c for c in node if c.tag == "MetadataString" and c.get("name") == name]
    if not records:
        return None
    if len(records) != 1:
        raise _Invalid("duplicate_semantic_metadata")
    try:
        # A single MFString whose text is itself a JSON object. Decode each
        # layer separately; no producer serialization helper is reused.
        text = json.loads(records[0].get("value", ""))
        data = json.loads(text)
        if not isinstance(text, str) or not isinstance(data, dict) or not isinstance(data.get("id"), str):
            raise ValueError
        return data
    except (ValueError, TypeError, RecursionError):
        raise _Invalid("invalid_semantic_metadata") from None


def _source_witness(record, source):
    """Recognize the admitted source record, independently of its container's truthiness."""
    evidence = record.get("evidence")
    if not isinstance(evidence, list):
        return False
    for item in evidence:
        if source == "curated" and isinstance(item, str) and item.strip():
            return True
        if isinstance(item, dict) and any(isinstance(item.get(k), str) and item[k].strip() for k in ("path", "text")):
            return True
    return False


def _source_binding(record, source, private_names):
    """Independent v1 public-sample/aggregate binding, not source authentication.

    Locator shape normalization is shared with the locator reader, but source
    projection and hash construction do not import producer helpers. The first
    four qualifying display witnesses are bound; detector counts bind the full
    declared detector population. Hidden private contents never enter the hash.
    """
    evidence = record.get("evidence", [])
    if not isinstance(evidence, list):
        raise _Invalid("invalid_source_binding")
    if source == "machine":
        kinds = record.get("kinds", {})
        if not isinstance(kinds, dict):
            raise _Invalid("invalid_source_binding")
        kinds = {k: v for k, v in kinds.items() if isinstance(k, str) and type(v) is int and v > 0}
    else:
        kinds = {"curated_kind": record.get("kind", "")}
    private = record.get("from") in private_names or record.get("to") in private_names
    ledger = record.get("evidence_records")
    total = ledger.get("total") if isinstance(ledger, dict) else None
    if type(total) is not int or total < 0:
        total = 0
    total = max(total, len(evidence))
    projected = []
    if private:
        if source == "curated":
            kinds = {"curated_kind": "aggregate"}
        else:
            file_kinds = {"code", "tests", "docs", "web", "config", "notebook", "data", "x3d", "log", "other", "media", "ci"}
            allowed = {"pin", "pin-weak", "uses", "fork_of", "shared_file", "session", "lineage", "second_account"}
            allowed |= {"mention:"+kind for kind in file_kinds} | {"mention:url-"+kind for kind in file_kinds}
            other = sum(value for key, value in kinds.items() if key not in allowed)
            kinds = {key: value for key, value in kinds.items() if key in allowed}
            if other:
                kinds["other"] = other
        projected = [{"count": total}]
    else:
        keys = ("kind", "path", "line", "text", "other_path", "locator", "locators", "observed_at", "valid_time")
        for item in evidence:
            if source == "curated" and isinstance(item, str) and item.strip():
                projected.append(item[:160])
            elif isinstance(item, dict) and any(isinstance(item.get(k), str) and item[k].strip() for k in ("path", "text")):
                projected_item = {}
                for key in keys:
                    value = item.get(key)
                    if value is None:
                        continue
                    if key == "locator":
                        from .locators import normalize_source_locator
                        value = normalize_source_locator(value)
                        if value is None:
                            continue
                    elif key == "locators":
                        from .locators import normalize_source_locator
                        value = [v for v in (normalize_source_locator(v) for v in value) if v is not None] if isinstance(value, list) else []
                        if not value:
                            continue
                    projected_item[key] = value[:160] if isinstance(value, str) else value
                projected.append(projected_item)
            if len(projected) == 4:
                break
    payload = {"schema_version": 1, "source": source, "from": record.get("from"), "to": record.get("to"),
               "grade": record.get("grade"), "kinds": kinds,
               "visibility": "aggregate_only" if private else "public", "evidence_count": total, "evidence": projected}
    try:
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _Invalid("invalid_source_binding") from None


def _source_id(record, source):
    if source == "machine":
        try:
            return "machine:"+hashlib.sha256((record["from"]+"\0"+record["to"]).encode()).hexdigest()[:20]
        except (KeyError, TypeError, UnicodeError):
            raise _Invalid("invalid_source_tie") from None
    if not isinstance(record.get("id"), str):
        raise _Invalid("invalid_source_tie")
    return "curated:"+record["id"]


def verify_scene(xml: str, plan: dict, bundle: dict) -> dict:
    """Return bounded, non-echoing findings for an emitted XML scene.

    `plan` is layout.scene_plan; `bundle` contains repos and the original
    machine/curated ties. A successful result is deliberately scoped, not a
    complete semantic proof of arbitrary X3D or of runtime appearance.
    """
    findings, counts = [], {"entities": 0, "relations": 0, "source_relations": 0, "expanded_visits": 0}
    total_findings = 0

    def fail(code, record=None):
        nonlocal total_findings
        total_findings += 1
        if len(findings) < MAX_FINDINGS:
            item = {"code": code, "message": code.replace("_", " ")}
            if record is not None:
                item["record"] = record
            findings.append(item)

    def result():
        return {"ok": not total_findings, "scope": SCOPE, "checks": counts,
                "findings": findings, "finding_count": total_findings,
                "omitted_findings": max(0, total_findings-len(findings)),
                "limits": {"expanded_visits": MAX_EXPANDED_VISITS}}

    def visit():
        # A small XML DAG may expand into exponentially many USE instances.
        # One shared allowance covers the global walk, glyph subwalks and
        # reference resolution; per-branch depth and XML-size limits alone do
        # not bound that work. Exhaustion terminates verification immediately.
        if counts["expanded_visits"] >= MAX_EXPANDED_VISITS:
            raise _ExpansionLimit
        counts["expanded_visits"] += 1

    if not isinstance(xml, str) or not xml.strip():
        fail("missing_scene_xml")
        return result()
    try:
        if len(xml.encode("utf-8")) > MAX_XML_BYTES:
            fail("scene_xml_too_large")
            return result()
    except UnicodeError:
        fail("invalid_xml_encoding")
        return result()
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, flags=re.I):
        fail("xml_declaration_forbidden")
        return result()
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, ValueError):
        fail("invalid_scene_xml")
        return result()
    if root.tag != "X3D":
        fail("unsupported_scene_root")
        return result()
    elements = list(root.iter())
    if len(elements) > MAX_ELEMENTS:
        fail("scene_element_limit")
        return result()
    if any(not isinstance(e.tag, str) or e.tag.startswith("{") for e in elements):
        fail("unsupported_xml_namespace")
        return result()
    if not isinstance(plan, dict) or plan.get("schema_version") != 1 or plan.get("scope") != "constellation":
        fail("missing_or_unsupported_scene_plan")
        return result()
    if not isinstance(bundle, dict):
        fail("invalid_source_bundle")
        return result()
    entity_layer = plan.get("entity_layer")
    relation_layer = plan.get("relation_layer")
    if not isinstance(entity_layer, str) or not isinstance(relation_layer, str) or entity_layer == relation_layer:
        fail("invalid_semantic_layers")
        return result()
    entities, relations = plan.get("entities"), plan.get("relations")
    if not isinstance(entities, list) or not isinstance(relations, list) or len(entities)+len(relations) > 100_000:
        fail("invalid_plan_records")
        return result()
    binding_schema = plan.get("source_binding_schema")
    modern = binding_schema is not None
    if modern and (type(binding_schema) is not int or binding_schema != 1):
        fail("unsupported_source_binding_schema")
        return result()
    expected_entities, expected_relations = {}, {}
    for category, records, target in (("entity", entities, expected_entities), ("relation", relations, expected_relations)):
        for i, record in enumerate(records):
            if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
                fail("invalid_plan_record", i)
            elif record["id"] in target:
                fail("duplicate_plan_"+category, i)
            else:
                target[record["id"]] = record

    defs = {}
    for element in elements:
        name = element.get("DEF")
        if name:
            if name in defs:
                fail("duplicate_def")
            else:
                defs[name] = element
        if element.get("USE") and element.get("DEF"):
            fail("def_and_use_together")
    if entity_layer not in defs or relation_layer not in defs:
        fail("missing_semantic_layer")
        return result()
    if defs[entity_layer].tag != "Group" or defs[relation_layer].tag != "Group":
        fail("invalid_semantic_layer_type")
        return result()

    def resolve(element):
        seen = set()
        while True:
            visit()
            name = element.get("USE")
            if not name:
                return element
            if name in seen or name not in defs:
                raise _Invalid("unresolved_or_cyclic_use")
            seen.add(name)
            if set(element.attrib)-{"USE", "containerField"}:
                raise _Invalid("ambiguous_use_attributes")
            target = defs[name]
            if target.tag != element.tag:
                raise _Invalid("use_type_mismatch")
            element = target

    # Validate all references, including appearance/coordinate references that
    # might otherwise be skipped by a semantic-only traversal.
    for element in elements:
        if element.get("USE"):
            try:
                resolve(element)
            except _ExpansionLimit:
                fail("scene_expansion_limit")
                return result()
            except _Invalid as exc:
                fail(exc.code)

    source_records = {}
    repos = bundle.get("repos", [])
    if not isinstance(repos, list):
        fail("invalid_source_repositories")
        repos = []
    source_nodes = {r["name"] for r in repos if isinstance(r, dict) and isinstance(r.get("name"), str)}
    repo_names = set(source_nodes)
    private_names = {r["name"] for r in repos if isinstance(r, dict) and isinstance(r.get("name"), str) and r.get("visibility") == "private"}
    display_sections = bundle.get("ties") or {}
    tie_sections = bundle.get("scene_sources", display_sections) if modern else display_sections
    if not isinstance(tie_sections, dict):
        fail("invalid_source_ties")
        tie_sections = {}
    for source, records in (("machine", tie_sections.get("machine", [])), ("curated", tie_sections.get("curated", []))):
        if not isinstance(records, list):
            fail("invalid_source_ties")
            continue
        for record in records:
            if (not isinstance(record, dict) or
                    not all(isinstance(record.get(k), str) and record[k].strip() for k in ("from", "to")) or
                    (not modern and not isinstance(record.get("grade"), str))):
                fail("invalid_source_tie")
                continue
            try:
                identifier = _source_id(record, source)
            except _Invalid as exc:
                fail(exc.code)
                continue
            if identifier in source_records:
                fail("duplicate_source_relation")
            source_records[identifier] = (source, record)
            if _source_witness(record, source):
                source_nodes.update((record["from"], record["to"]))
    counts["source_relations"] = len(source_records)
    display_records = {}
    if modern:
        if not isinstance(display_sections, dict):
            fail("invalid_display_ties")
        else:
            for source in ("machine", "curated"):
                records = display_sections.get(source, [])
                if not isinstance(records, list):
                    fail("invalid_display_ties")
                    continue
                for record in records:
                    if not isinstance(record, dict):
                        fail("invalid_display_tie")
                        continue
                    try:
                        identifier = _source_id(record, source)
                        if identifier in display_records:
                            fail("duplicate_display_relation")
                        display_records[identifier] = (source, record)
                    except _Invalid as exc:
                        fail(exc.code)

        eligible, legitimately_omitted = set(), {}
        for identifier, (source, record) in source_records.items():
            reason = ("no_witness" if not _source_witness(record, source) else
                      "self_relation" if record["from"] == record["to"] else
                      "invalid_grade" if not isinstance(record.get("grade"), str) or record["grade"] not in GRADE_COLOURS else None)
            if reason:
                legitimately_omitted[identifier] = reason
            else:
                eligible.add(identifier)
        if set(expected_relations) != eligible:
            fail("source_relation_coverage_mismatch")
        required_nodes = repo_names | {source_records[rid][1][k] for rid in eligible for k in ("from", "to")}
        if set(expected_entities) != {"node:"+name for name in required_nodes}:
            fail("source_entity_coverage_mismatch")
        omissions = plan.get("omitted", [])
        seen_omissions = set()
        if not isinstance(omissions, list):
            fail("invalid_omission_ledger")
        else:
            for omission in omissions:
                if not isinstance(omission, dict) or not isinstance(omission.get("id"), str):
                    fail("invalid_omission_record")
                    continue
                identifier = omission["id"]
                if identifier in seen_omissions:
                    fail("duplicate_omission_record")
                seen_omissions.add(identifier)
                if identifier not in source_records:
                    fail("source_omission_unverifiable")
                else:
                    source, record = source_records[identifier]
                    if (omission.get("reason") != legitimately_omitted.get(identifier) or
                            any(omission.get(k) != record.get(k) for k in ("from", "to")) or omission.get("source") != source):
                        fail("invalid_source_omission")
        if seen_omissions != set(legitimately_omitted):
            fail("source_omission_coverage_mismatch")
        counts["eligible_source_relations"] = len(eligible)
        counts["eligible_source_entities"] = len(required_nodes)

    actual_entities, actual_relations = {}, {}
    layer_occurrences = {entity_layer: 0, relation_layer: 0}
    unsupported = {"Billboard", "Switch", "LOD", "MatrixTransform", "GeoTransform", "ProtoInstance", "Inline", "StaticGroup"}
    supported_scene_nodes = set(CONTAINERS) | {"X3D", "Scene"}

    def children(node, tag):
        matched = []
        for child in node:
            if child.tag != tag:
                continue
            if child.get("containerField", CONTAINERS.get(tag)) != CONTAINERS.get(tag):
                raise _Invalid("invalid_semantic_container_field")
            matched.append(resolve(child))
        return matched

    def appearance(shape):
        aps = children(shape, "Appearance")
        if len(aps) != 1:
            raise _Invalid("missing_or_ambiguous_appearance")
        app = aps[0]
        if any(c.tag not in {"MetadataString", "MetadataSet", "Material", "LineProperties"} for c in app):
            raise _Invalid("unsupported_semantic_appearance")
        mats = children(app, "Material")
        if len(mats) != 1:
            raise _Invalid("missing_or_ambiguous_material")
        material = mats[0]
        tr = _numbers(material.get("transparency", "0"), 1)[0]
        if not 0 <= tr < 1:
            raise _Invalid("invisible_semantic_geometry")
        return material

    def glyphs(owner, world):
        found = []
        stack = [(owner, world, 0)]
        while stack:
            raw, matrix, depth = stack.pop()
            visit()
            if depth > MAX_DEPTH:
                raise _Invalid("scene_depth_limit")
            node = resolve(raw)
            # The owner's matrix already includes its transform.
            if depth and node.tag == "Transform":
                matrix = _multiply(matrix, _transform(node))
            if node.get("visible", "true").lower() == "false":
                raise _Invalid("invisible_semantic_geometry")
            if depth and node.tag not in {"Transform", "Group", "Shape", "TouchSensor"}:
                raise _Invalid("unsupported_semantic_ancestor")
            if node.tag == "Shape":
                shapes = [resolve(c) for c in node if c.tag in {"Sphere", "Box"}]
                other = [c for c in node if c.tag not in {"MetadataString", "MetadataSet", "Appearance", "Sphere", "Box"}]
                if len(shapes) != 1 or other:
                    raise _Invalid("unsupported_entity_geometry")
                appearance(node)
                found.append((shapes[0], matrix))
                continue
            for child in reversed(list(node)):
                if child.tag not in {"MetadataString", "MetadataSet", "Appearance", "TouchSensor"}:
                    stack.append((child, matrix, depth+1))
        if len(found) != 1:
            raise _Invalid("ambiguous_entity_primitive")
        return found[0]

    stack = [(root, _identity(), None, (), (), 0, False)]
    while stack:
        raw, parent_world, layer, bad_ancestors, chain, depth, entity_owner = stack.pop()
        try:
            visit()
            if depth > MAX_DEPTH:
                raise _Invalid("scene_depth_limit")
            node = resolve(raw)
            if id(node) in chain:
                raise _Invalid("cyclic_scene_graph")
            world = _multiply(parent_world, _transform(node)) if node.tag == "Transform" else parent_world
            # Unknown grouping nodes may hide children or introduce their own
            # coordinate system (CADLayer, HAnimJoint, GeoLocation, ...). A
            # denylist cannot establish world geometry for arbitrary X3D.
            bad = bad_ancestors + ((node.tag,) if node.tag in unsupported or node.tag not in supported_scene_nodes or node.get("visible", "true").lower() == "false" else ())
            if node.get("DEF") in layer_occurrences:
                layer = node.get("DEF")
                layer_occurrences[layer] += 1
            if layer and raw.tag in CONTAINERS and raw.get("containerField", CONTAINERS[raw.tag]) != CONTAINERS[raw.tag]:
                raise _Invalid("invalid_semantic_container_field")
            em, rm = _metadata(node, "xray.entity"), _metadata(node, "xray.relation")
            if em is not None:
                if layer != entity_layer or node.tag != "Transform":
                    raise _Invalid("entity_outside_semantic_layer")
                if bad:
                    raise _Invalid("unsupported_semantic_ancestor")
                ident = em["id"]
                if ident in actual_entities:
                    raise _Invalid("duplicate_scene_entity")
                primitive, matrix = glyphs(node, world)
                actual_entities[ident] = {"def": node.get("DEF"), "position": _point(matrix, (0., 0., 0.)),
                                          "primitive": primitive, "world": matrix, "metadata": em}
                entity_owner = True
            if rm is not None:
                if layer != relation_layer or node.tag != "Shape":
                    raise _Invalid("relation_outside_semantic_layer")
                if bad:
                    raise _Invalid("unsupported_semantic_ancestor")
                ident = rm["id"]
                if ident in actual_relations:
                    raise _Invalid("duplicate_scene_relation")
                geometries = children(node, "IndexedLineSet")
                if len(geometries) != 1 or any(c.tag not in {"MetadataString", "MetadataSet", "Appearance", "IndexedLineSet"} for c in node):
                    raise _Invalid("unsupported_relation_geometry")
                geometry = geometries[0]
                if any(c.tag not in {"MetadataString", "MetadataSet", "Coordinate"} for c in geometry):
                    raise _Invalid("unsupported_relation_geometry")
                coordinates = children(geometry, "Coordinate")
                if len(coordinates) != 1:
                    raise _Invalid("ambiguous_relation_coordinates")
                flat = _numbers(coordinates[0].get("point", ""))
                if len(flat) % 3:
                    raise _Invalid("invalid_coordinate_count")
                points = [flat[i:i+3] for i in range(0, len(flat), 3)]
                try:
                    indices = [int(v) for v in re.split(r"[\s,]+", geometry.get("coordIndex", "").strip()) if v]
                except ValueError:
                    raise _Invalid("invalid_coordinate_index") from None
                if len(indices) > 1_000_000:
                    raise _Invalid("coordinate_index_limit")
                if indices and indices[-1] == -1:
                    indices.pop()
                if len(indices) < 2 or any(i < 0 or i >= len(points) for i in indices):
                    raise _Invalid("invalid_coordinate_index")
                material = appearance(node)
                actual_relations[ident] = {"def": node.get("DEF"), "metadata": rm,
                    "points": [_point(world, points[i]) for i in indices],
                    "color": _numbers(material.get("emissiveColor", "0 0 0"), 3)}
            if layer == relation_layer and node.tag == "Shape" and rm is None:
                raise _Invalid("unregistered_relation_geometry")
            if layer == entity_layer and node.tag == "Transform" and em is None:
                # Grouping transforms are permitted; their Shape descendants
                # must still belong to one registered metadata owner below.
                pass
            # Every bare Shape in the entity layer must have a registered
            # entity owner in its ancestor chain (not a free decorative glyph).
            if layer == entity_layer and node.tag == "Shape":
                if not entity_owner:
                    raise _Invalid("unregistered_entity_geometry")
            for child in reversed(list(node)):
                # Metadata/appearance children cannot contain semantic geometry
                # in the accepted subset; references were checked above.
                if child.tag not in {"MetadataString", "MetadataSet", "Appearance"}:
                    stack.append((child, world, layer, bad, (*chain, id(node)), depth+1, entity_owner))
        except _ExpansionLimit:
            fail("scene_expansion_limit")
            return result()
        except _Invalid as exc:
            fail(exc.code)

    for name, n in layer_occurrences.items():
        if n != 1:
            fail("ambiguous_semantic_layer")
    if set(actual_entities)-set(expected_entities):
        fail("extra_scene_entities")
    if set(expected_entities)-set(actual_entities):
        fail("missing_scene_entities")
    if set(actual_relations)-set(expected_relations):
        fail("extra_scene_relations")
    if set(expected_relations)-set(actual_relations):
        fail("missing_scene_relations")

    for i, record in enumerate(entities):
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or record["id"] not in actual_entities:
            continue
        actual = actual_entities[record["id"]]
        try:
            source_name = record.get("source_name")
            if not isinstance(source_name, str) or source_name not in source_nodes or record["id"] != "node:"+source_name:
                raise _Invalid("entity_has_no_source_identity")
            if actual["def"] != record.get("def"):
                fail("entity_def_mismatch", i)
            if not _close(actual["position"], _vector(record.get("position"))):
                fail("entity_position_mismatch", i)
            geometry = record.get("geometry")
            if not isinstance(geometry, dict) or geometry.get("type") != actual["primitive"].tag:
                raise _Invalid("entity_primitive_mismatch")
            scales = _basis_scales(actual["world"])
            if geometry["type"] == "Sphere":
                if max(scales)-min(scales) > 1e-6:
                    raise _Invalid("unsupported_ellipsoid_geometry")
                radius = _numbers(actual["primitive"].get("radius", "1"), 1)[0] * scales[0]
                expected = _vector([geometry.get("radius")], 1)[0]
                if radius <= 0 or expected <= 0 or abs(radius-expected) > TOLERANCE:
                    fail("entity_size_mismatch", i)
            else:
                local = _numbers(actual["primitive"].get("size", "2 2 2"), 3)
                sizes = tuple(v*s for v, s in zip(local, scales))
                if any(v <= 0 for v in sizes) or not _close(sizes, _vector(geometry.get("size"))):
                    fail("entity_size_mismatch", i)
            counts["entities"] += 1
        except _Invalid as exc:
            fail(exc.code, i)

    for i, record in enumerate(relations):
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or record["id"] not in actual_relations:
            continue
        actual = actual_relations[record["id"]]
        try:
            if any(not isinstance(record.get(k), str) for k in ("from_entity", "to_entity", "from", "to", "source", "grade")):
                raise _Invalid("invalid_plan_relation_identity")
            source = source_records.get(record["id"])
            if not source:
                raise _Invalid("relation_has_no_source_record")
            source_type, source_record = source
            if not _source_witness(source_record, source_type):
                raise _Invalid("relation_has_no_source_witness")
            if any(record.get(k) != source_record.get(k) for k in ("from", "to", "grade")) or record.get("source") != source_type:
                fail("relation_source_mismatch", i)
            if record.get("from_entity") != "node:"+source_record["from"] or record.get("to_entity") != "node:"+source_record["to"]:
                fail("relation_source_endpoint_mismatch", i)
            keys = ("id", "from_entity", "to_entity", "grade", "source", "source_digest") if modern else ("id", "from_entity", "to_entity", "grade", "source")
            expected_meta = {k: record.get(k) for k in keys}
            if actual["metadata"] != expected_meta:
                fail("relation_metadata_mismatch", i)
            if modern:
                digest = _source_binding(source_record, source_type, private_names)
                if record.get("source_digest") != digest:
                    fail("relation_source_digest_mismatch", i)
                display = display_records.get(record["id"])
                if display is None:
                    fail("drawn_relation_missing_from_display", i)
                elif display[0] != source_type or _source_binding(display[1], display[0], private_names) != digest:
                    fail("display_source_digest_mismatch", i)
            if actual["def"] != record.get("def"):
                fail("relation_def_mismatch", i)
            points = record.get("points")
            if not isinstance(points, list) or len(points) < 2:
                raise _Invalid("invalid_plan_polyline")
            expected_points = [_vector(p) for p in points]
            if len(actual["points"]) != len(expected_points) or any(not _close(a, b) for a, b in zip(actual["points"], expected_points)):
                fail("relation_geometry_mismatch", i)
            grade_colour = GRADE_COLOURS.get(source_record["grade"])
            if grade_colour is None:
                raise _Invalid("invalid_source_grade")
            if not _close(_vector(record.get("color")), grade_colour):
                fail("plan_grade_color_mismatch", i)
            if not _close(actual["color"], grade_colour):
                fail("relation_color_mismatch", i)
            offsets = _vector(record.get("attachment_offsets"), 2)
            for endpoint, entity_key, offset in ((actual["points"][0], record.get("from_entity"), offsets[0]),
                                                  (actual["points"][-1], record.get("to_entity"), offsets[1])):
                if entity_key not in actual_entities:
                    raise _Invalid("relation_endpoint_entity_missing")
                if offset < 0 or abs(math.dist(endpoint, actual_entities[entity_key]["position"])-offset) > TOLERANCE:
                    fail("relation_attachment_mismatch", i)
            counts["relations"] += 1
        except _Invalid as exc:
            fail(exc.code, i)
    report = result()
    report["source_binding"] = {"schema_version": 1 if modern else None,
                                "mode": "public_sample_or_private_aggregate" if modern else "legacy_declared_subset",
                                "source_authentication": "not verified"}
    # Only matching declared IDs are returned. Unknown XML metadata never gets
    # copied into diagnostics or overlays. Omission counts make this a bounded
    # inspection view rather than an implicit complete scene export.
    observed_entities, observed_relations = [], []
    inspect_entities = {ident for ident in set(actual_entities) & set(expected_entities)
                        if isinstance(expected_entities[ident].get("source_name"), str) and
                        expected_entities[ident]["source_name"] in source_nodes and
                        ident == "node:"+expected_entities[ident]["source_name"]}
    inspect_relations = set(actual_relations) & set(expected_relations) & set(source_records)
    for ident in sorted(inspect_entities)[:200]:
        actual = actual_entities[ident]
        primitive = actual["primitive"]
        item = {"id": ident[:512], "position": list(actual["position"]), "geometry": {"type": primitive.tag}}
        try:
            scales = _basis_scales(actual["world"])
            if primitive.tag == "Sphere":
                item["geometry"]["radius"] = _numbers(primitive.get("radius", "1"), 1)[0] * scales[0]
            else:
                item["geometry"]["size"] = [v*s for v, s in zip(_numbers(primitive.get("size", "2 2 2"), 3), scales)]
        except _Invalid:
            item["geometry"]["unsupported"] = True
        observed_entities.append(item)
    for ident in sorted(inspect_relations)[:200]:
        actual = actual_relations[ident]
        observed_relations.append({"id": ident[:512], "points": [list(p) for p in actual["points"][:256]],
                                   "points_omitted": max(0, len(actual["points"])-256), "color": list(actual["color"])})
    report["observed"] = {"entities": observed_entities, "relations": observed_relations,
                          "entities_omitted": max(0, len(inspect_entities)-200),
                          "relations_omitted": max(0, len(inspect_relations)-200)}
    return report
