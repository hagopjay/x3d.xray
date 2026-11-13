"""Controlled XML mutations for a static-scene verification laboratory.

These are deliberate fixture changes, never source repairs. The actual checker
is called by audit_cases; this module does not implement verification itself.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET


def _layer(root: ET.Element, role: str) -> ET.Element:
    return next(e for e in root.iter() if e.get("DEF") == "XraySemantic" + role)


def _first_entity(root: ET.Element) -> ET.Element:
    layer = _layer(root, "Entities")
    return next(e for e in layer.iter("Transform") if e.find("MetadataString[@name='xray.entity']") is not None)


def _first_edge(root: ET.Element) -> ET.Element:
    layer = _layer(root, "Relations")
    return next(e for e in layer.iter("Shape") if e.find("MetadataString[@name='xray.relation']") is not None)


def _floats(value: str) -> list[float]:
    return [float(s) for s in re.split(r"[\s,]+", value.strip()) if s]


def _set_points(node: ET.Element, points: list[float]) -> None:
    node.set("point", " ".join(format(n, ".12g") for n in points))


def move_entity(root):
    e = _first_entity(root)
    t = _floats(e.get("translation", "0 0 0")); t[0] += 1.25
    e.set("translation", " ".join(map(str, t)))


def change_primitive(root):
    e = _first_entity(root)
    sphere = e.find(".//Sphere")
    if sphere is not None:
        sphere.set("radius", str(float(sphere.get("radius", "1")) * 1.7))
    else:
        box = e.find(".//Box")
        size = _floats(box.get("size", "2 2 2")); size[0] *= 1.7
        box.set("size", " ".join(map(str, size)))


def move_edge_endpoint(root):
    coordinate = _first_edge(root).find(".//Coordinate")
    points = _floats(coordinate.get("point")); points[-3] += 1.25
    _set_points(coordinate, points)


def reverse_edge_indices(root):
    line = _first_edge(root).find("IndexedLineSet")
    ids = [int(v) for v in line.get("coordIndex").split() if int(v) != -1]
    line.set("coordIndex", " ".join(map(str, reversed(ids))) + " -1")


def change_grade_colour(root):
    material = _first_edge(root).find(".//Material")
    material.set("emissiveColor", "1 0 1")


def change_metadata_endpoint(root):
    meta = _first_edge(root).find("MetadataString[@name='xray.relation']")
    value = json.loads(json.loads(meta.get("value")))
    value["to_entity"] = "node:not-in-the-source"
    meta.set("value", json.dumps(json.dumps(value, sort_keys=True)))


def duplicate_def(root):
    e = _first_entity(root)
    clone = ET.fromstring(ET.tostring(e, encoding="unicode"))
    _layer(root, "Entities").append(clone)


def _unlabelled_line() -> ET.Element:
    return ET.fromstring("<Shape DEF='AuditUnlabelledLine'><Appearance><Material emissiveColor='1 0 0'/></Appearance>"
                         "<IndexedLineSet coordIndex='0 1 -1'><Coordinate point='0 0 0, 1 0 0'/></IndexedLineSet></Shape>")


def inject_unlabelled_edge(root):
    _layer(root, "Relations").append(_unlabelled_line())


def use_missing_coordinate(root):
    coordinate = _first_edge(root).find(".//Coordinate")
    coordinate.attrib.clear()
    coordinate.set("USE", "AuditMissingCoordinate")


def hide_semantic_layer(root):
    layer = _layer(root, "Relations")
    parent = next(e for e in root.iter() if layer in list(e))
    parent.remove(layer)
    hidden = ET.SubElement(parent, "Switch", {"whichChoice": "-1"})
    hidden.append(layer)


def equivalent_nested_transforms(root):
    # (T * R * S) * (S^-1 * R^-1 * T^-1) is the identity. The inner
    # Transform's translation is transformed too; simply negating T is wrong.
    for role in ("Entities", "Relations"):
        layer = _layer(root, role)
        parent = next(e for e in root.iter() if layer in list(e))
        parent.remove(layer)
        outer = ET.SubElement(parent, "Transform", {"translation": "3 -2 1", "rotation": "0 0 1 1.5707963267948966", "scale": "2 2 2"})
        inner = ET.SubElement(outer, "Transform", {"translation": "1 1.5 -0.5", "rotation": "0 0 1 -1.5707963267948966", "scale": "0.5 0.5 0.5"})
        inner.append(layer)


def out_of_scope_line(root):
    # The static contract deliberately excludes unrelated decoration layers.
    scene = root.find("Scene")
    scene.append(_unlabelled_line())


MUTATIONS = (
    ("moved_entity", move_entity, "reject", "Glyph translation changed while its source record and plan remain unchanged."),
    ("primitive_size", change_primitive, "reject", "Glyph radius/size changed."),
    ("wrong_edge_endpoint", move_edge_endpoint, "reject", "Actual used polyline endpoint changed."),
    ("reversed_coord_index", reverse_edge_indices, "reject", "Coordinate-index traversal changed without editing coordinates."),
    ("wrong_grade_colour", change_grade_colour, "reject", "Actual material colour no longer encodes the source grade."),
    ("metadata_wrong_endpoint", change_metadata_endpoint, "reject", "Relation metadata names a nonexistent endpoint."),
    ("duplicate_def", duplicate_def, "reject", "Cloned glyph duplicates XML and semantic identities."),
    ("unlabelled_semantic_edge", inject_unlabelled_edge, "reject", "An unregistered edge is added inside the declared relation layer."),
    ("missing_coordinate_use", use_missing_coordinate, "reject", "Coordinate USE cannot resolve."),
    ("hidden_semantic_layer", hide_semantic_layer, "reject", "The relation layer is hidden under a static Switch."),
    ("equivalent_nested_transforms", equivalent_nested_transforms, "accept", "Nested translation/rotation/scale compose to the unchanged world geometry."),
    ("unlabelled_outside_layer", out_of_scope_line, "scope_probe", "Geometry outside the declared semantic scope is observed, not counted as a rejection guarantee."),
)



def mutate_cases(xml: str) -> list[dict]:
    """Return original and deliberately altered XML, with stated expectations.

    A None expectation is a scope probe, not a promise of rejection. All cases
    retain the caller's authoritative plan and bundle unchanged.
    """
    cases = [{"id": "baseline", "label": "Original scene", "description": "Unmodified compiler output.",
              "xml": xml, "expected_ok": True, "scope_probe": False}]
    for name, mutate, expected, note in MUTATIONS:
        root = ET.fromstring(xml)
        mutate(root)
        cases.append({"id": name, "label": name.replace("_", " ").capitalize(), "description": note,
                      "xml": ET.tostring(root, encoding="unicode"),
                      "expected_ok": expected == "accept" if expected != "scope_probe" else None,
                      "scope_probe": expected == "scope_probe"})
    return cases


def audit_cases(xml: str, plan: dict, bundle: dict) -> list[dict]:
    """Add the independent checker's actual static result to each case."""
    from .scenecheck import verify_scene
    return [{**case, "verification": verify_scene(case["xml"], plan, bundle)} for case in mutate_cases(xml)]
