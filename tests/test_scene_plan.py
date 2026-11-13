"""Producer contract tests for the instrumented static constellation.

The independent scene reader and mutation suite are separate from these tests.
These checks pin the compiler's source identity, coverage and serialization.
"""
import copy
import json
import math
import unittest
import xml.etree.ElementTree as ET

from x3dray import compiler, profile


def region(names=("alpha", "beta"), pairs=None, curated=None):
    ds = {"family": "generic", "lineage": [], "repos": [
        {"name": name, "kind": "own", "visibility": "public"} for name in names]}
    prof = {"per_repo": {name: {"own_commits": 3, "by_class": {"you": 2, "ai": 1}, "tests": 0, "kind": "own"} for name in names}}
    scene = compiler.Scene()
    layout = {"nodes": {}, "ties": {"machine": [], "curated": []}, "regions": {}}
    compiler.constellation_region(scene, layout, ds, {"pairs": pairs or []}, curated, prof)
    xml = "<X3D profile='Immersive' version='4.0'><Scene>" + "".join(scene.out) + "</Scene></X3D>"
    return xml, layout


def machine(a="alpha", b="beta", grade="C"):
    return {"from": a, "to": b, "grade": grade, "grade_reason": "declared use", "kinds": {"uses": 1},
            "evidence": [{"kind": "uses", "path": "family.json", "text": "declared fixture use"}]}


def metadata(element, name):
    return json.loads(json.loads(element.find(f"MetadataString[@name='{name}']").get("value")))


class ConstellationPlanTests(unittest.TestCase):
    def test_generic_family_has_only_declared_repository_nodes(self):
        xml, layout = region()
        self.assertEqual(set(layout["nodes"]), {"alpha", "beta"})
        self.assertNotIn("x3d_mcp", layout["nodes"])
        self.assertNotIn("upstream", layout["nodes"])
        root = ET.fromstring(xml)
        self.assertIsNotNone(root.find(".//Group[@DEF='XraySemanticEntities']"))
        self.assertIsNotNone(root.find(".//Group[@DEF='XraySemanticRelations']"))
        self.assertEqual(layout["scene_plan"]["scope"], "constellation")

    def test_external_endpoints_are_canonical_and_require_witness(self):
        good = machine("alpha", "Upstream/project")
        bad = {**machine("alpha", "unwitnessed-external"), "evidence": []}
        _, layout = region(pairs=[good, bad])
        self.assertEqual(set(layout["nodes"]), {"alpha", "beta", "Upstream/project"})
        entities = {e["source_name"]: e for e in layout["scene_plan"]["entities"]}
        self.assertEqual(entities["Upstream/project"]["id"], "node:Upstream/project")
        self.assertEqual(entities["Upstream/project"]["geometry"]["type"], "Box")
        self.assertEqual(layout["scene_plan"]["omitted"][0]["reason"], "no_witness")
        self.assertEqual(layout["scene_plan"]["node_aliases"], {})

    def test_metadata_records_identity_not_geometry(self):
        xml, layout = region(pairs=[machine()])
        root = ET.fromstring(xml)
        for entity in layout["scene_plan"]["entities"]:
            node = root.find(f".//Transform[@DEF='{entity['def']}']")
            self.assertEqual(metadata(node, "xray.entity"), {"id": entity["id"]})
            self.assertEqual([float(v) for v in node.get("translation").split()], entity["position"])
            self.assertEqual(float(node.find(".//Sphere").get("radius")), entity["geometry"]["radius"])
        rel = layout["scene_plan"]["relations"][0]
        node = root.find(f".//Shape[@DEF='{rel['def']}']")
        self.assertEqual(metadata(node, "xray.relation"), {k: rel[k] for k in ("id", "source", "from_entity", "to_entity", "grade")})
        points = [float(v) for v in node.find(".//Coordinate").get("point").replace(",", " ").split()]
        self.assertEqual([points[i:i + 3] for i in range(0, len(points), 3)], rel["points"])

    def test_curated_polyline_has_twenty_one_ordered_points(self):
        cur = {"ties": [{"id": "E01", "from": "beta", "to": "alpha", "grade": "L", "kind": "lineage",
                         "evidence": ["Attributed synthetic account"]}]}
        xml, layout = region(curated=cur)
        rel = layout["scene_plan"]["relations"][0]
        self.assertEqual(rel["id"], "curated:E01")
        self.assertEqual(len(rel["points"]), 21)
        self.assertEqual(rel["from_entity"], "node:beta")
        root = ET.fromstring(xml)
        layer = root.find(".//Group[@DEF='XraySemanticRelations']")
        self.assertEqual([n.tag for n in layer], ["Shape"])
        self.assertEqual(len(layer.findall(".//Cone")), 0)
        # Free-prose curated endpoints do not establish an executable or
        # architectural direction. The source association has no arrowhead.
        self.assertEqual(len(root.findall(".//Cone")), 0)

    def test_quantized_endpoints_attach_to_actual_node_centres(self):
        cur = {"ties": [{"id": "E01", "from": "beta", "to": "alpha", "grade": "L", "evidence": ["source"]}]}
        _, layout = region(pairs=[machine()], curated=cur)
        plan = layout["scene_plan"]
        nodes = {n["id"]: n for n in plan["entities"]}
        for rel in plan["relations"]:
            for endpoint, node_id, radial in ((rel["points"][0], rel["from_entity"], rel["attachment_offsets"][0]),
                                              (rel["points"][-1], rel["to_entity"], rel["attachment_offsets"][1])):
                self.assertLessEqual(abs(math.dist(endpoint, nodes[node_id]["position"]) - radial), plan["tolerance"])

    def test_punctuation_collisions_do_not_collide_defs(self):
        xml, layout = region(names=("a.b", "a_b"), pairs=[machine("a.b", "a_b")])
        self.assertNotEqual(layout["nodes"]["a.b"]["def"], layout["nodes"]["a_b"]["def"])
        defs = [e.get("DEF") for e in ET.fromstring(xml).iter() if e.get("DEF")]
        self.assertEqual(len(defs), len(set(defs)))

    def test_unicode_quotes_and_xml_delimiters_round_trip(self):
        external = 'owner/汉字 & <quoted> "name" apostrophe\''
        xml, layout = region(pairs=[machine("alpha", external)])
        root = ET.fromstring(xml)
        entity = next(e for e in layout["scene_plan"]["entities"] if e["source_name"] == external)
        self.assertEqual(metadata(root.find(f".//Transform[@DEF='{entity['def']}']"), "xray.entity")["id"], "node:" + external)

    def test_relation_identity_is_independent_of_array_position(self):
        first, second = machine(), machine("beta", "extra")
        _, a = region(pairs=[first, second])
        _, b = region(pairs=[second, first])
        by_source = lambda layout: {(r["from"], r["to"]): (r["id"], r["def"]) for r in layout["scene_plan"]["relations"]}
        self.assertEqual(by_source(a), by_source(b))

    def test_self_and_invalid_grade_have_explicit_omissions(self):
        _, layout = region(pairs=[machine("alpha", "alpha"), machine("alpha", "external", "?")])
        plan = layout["scene_plan"]
        self.assertEqual({r["reason"] for r in plan["omitted"]}, {"self_relation", "invalid_grade"})
        self.assertEqual(plan["relations"], [])
        self.assertEqual(plan["coverage"]["source_relations"], 2)

    def test_duplicate_source_identity_is_refused_before_rendering(self):
        with self.assertRaisesRegex(ValueError, "duplicate source"):
            region(pairs=[machine(), copy.deepcopy(machine())])

    def test_blank_witnesses_do_not_authorize_a_drawn_relation(self):
        record = machine()
        record["evidence"] = [{"path": "  ", "text": "\t"}]
        _, layout = region(pairs=[record])
        self.assertEqual(layout["scene_plan"]["relations"], [])
        self.assertEqual(layout["scene_plan"]["omitted"][0]["reason"], "no_witness")

    def test_blank_endpoints_are_refused(self):
        with self.assertRaisesRegex(ValueError, "nonblank"):
            region(pairs=[machine("", "beta")])

    def test_empty_population_carries_empty_layers_and_plan(self):
        ds = {"family": "empty", "repos": [], "lineage": []}
        prof = profile.profile_family(ds, {"pairs": []})
        xml, layout = compiler.build_scene(ds, {"pairs": []}, None, prof, {"views": []}, {})
        self.assertEqual(layout["scene_plan"]["entities"], [])
        self.assertEqual(layout["scene_plan"]["relations"], [])
        root = ET.fromstring(xml)
        self.assertIsNotNone(root.find(".//Group[@DEF='XraySemanticEntities']"))
        self.assertIsNotNone(root.find(".//Group[@DEF='XraySemanticRelations']"))


if __name__ == "__main__":
    unittest.main()
