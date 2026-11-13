"""Independent geometric checks and adversarial mutation tests.

Fixtures are hand-authored X3D and source records, not compiler output. The
integration mutation audit separately exercises the real compiler.
"""
import copy
import hashlib
import json
import math
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from x3dray.scenecheck import MAX_XML_BYTES, verify_scene


def metadata(name, value):
    element = ET.Element("MetadataString", name=name, value=json.dumps(json.dumps(value, sort_keys=True)))
    return ET.tostring(element, encoding="unicode")


def fixture():
    identifier = "machine:"+hashlib.sha256(b"a\0b").hexdigest()[:20]
    relation = {"id": identifier, "def": "Edge", "source": "machine", "from": "a", "to": "b",
                "from_entity": "node:a", "to_entity": "node:b", "grade": "C",
                "points": [[1.12, 0, 0], [4.88, 0, 0]], "color": [0.361, 0.761, 0.541],
                "attachment_offsets": [1.12, 1.12]}
    entities = [{"id": "node:"+name, "def": name.upper(), "source_name": name,
                 "position": [x, 0, 0], "geometry": {"type": "Sphere", "radius": 1}, "attachment_radius": 1}
                for name, x in (("a", 0), ("b", 6))]
    plan = {"schema_version": 1, "scope": "constellation", "entity_layer": "Entities", "relation_layer": "Relations",
            "entities": entities, "relations": [relation]}
    bundle = {"repos": [{"name": "a"}, {"name": "b"}], "ties": {"machine": [
        {"from": "a", "to": "b", "grade": "C", "evidence": [{"kind": "uses", "path": "config.json", "text": "uses b"}]}], "curated": []}}
    entity_xml = []
    for name, x in (("a", 0), ("b", 6)):
        material = "<Material DEF='SharedMaterial' diffuseColor='0.6 0.7 0.8'/>" if name == "a" else "<Material USE='SharedMaterial'/>"
        primitive = "<Sphere DEF='SharedSphere' radius='1'/>" if name == "a" else "<Sphere USE='SharedSphere'/>"
        entity_xml.append(f"<Transform DEF='{name.upper()}' translation='{x} 0 0'>"+
                          metadata("xray.entity", {"id": "node:"+name})+
                          f"<Shape><Appearance>{material}</Appearance>{primitive}</Shape></Transform>")
    meta = {k: relation[k] for k in ("id", "from_entity", "to_entity", "grade", "source")}
    xml = ("<X3D profile='Immersive' version='4.0'><Scene><Group DEF='Entities'>"+"".join(entity_xml)+
           "</Group><Group DEF='Relations'><Shape DEF='Edge'>"+metadata("xray.relation", meta)+
           "<Appearance><Material emissiveColor='0.361 0.761 0.541'/><LineProperties linewidthScaleFactor='2'/></Appearance>"
           "<IndexedLineSet coordIndex='0 1 -1'><Coordinate DEF='LinePoints' point='1.12 0 0, 4.88 0 0'/></IndexedLineSet>"
           "</Shape></Group></Scene></X3D>")
    return xml, plan, bundle


class SceneGeometryTests(unittest.TestCase):
    def setUp(self):
        self.xml, self.plan, self.bundle = fixture()

    def report(self, xml=None, plan=None, bundle=None):
        return verify_scene(self.xml if xml is None else xml,
                            self.plan if plan is None else plan, self.bundle if bundle is None else bundle)

    def rejects(self, xml=None, plan=None, bundle=None, code=None):
        result = self.report(xml, plan, bundle)
        self.assertFalse(result["ok"], result)
        if code:
            self.assertIn(code, {f["code"] for f in result["findings"]}, result)
        return result

    def mutate(self, fn):
        root = ET.fromstring(self.xml)
        fn(root)
        return ET.tostring(root, encoding="unicode")

    def test_hand_authored_geometry_and_shared_primitives_pass(self):
        result = self.report()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["checks"]["entities"], 2)
        self.assertEqual(result["checks"]["relations"], 1)
        self.assertEqual(result["observed"]["entities"][1]["position"], [6., 0., 0.])

    def test_moving_real_glyph_fails_despite_unchanged_metadata(self):
        xml = self.mutate(lambda r: r.find(".//Transform[@DEF='B']").set("translation", "9 0 0"))
        report = self.rejects(xml, code="entity_position_mismatch")
        self.assertEqual(report["observed"]["entities"][1]["position"], [9., 0., 0.])

    def test_changed_primitive_size_fails(self):
        self.rejects(self.mutate(lambda r: r.find(".//Sphere[@DEF='SharedSphere']").set("radius", "2")), code="entity_size_mismatch")

    def test_changed_line_endpoint_fails_with_source_and_metadata_unchanged(self):
        self.rejects(self.mutate(lambda r: r.find(".//Coordinate").set("point", "1.12 0 0, 8 0 0")), code="relation_geometry_mismatch")

    def test_line_order_comes_from_coordindex_not_coordinate_storage(self):
        self.rejects(self.mutate(lambda r: r.find(".//IndexedLineSet").set("coordIndex", "1 0 -1")), code="relation_geometry_mismatch")
        root = ET.fromstring(self.xml)
        root.find(".//Coordinate").set("point", "4.88 0 0, 1.12 0 0, 99 99 99")
        root.find(".//IndexedLineSet").set("coordIndex", "1 0 -1")
        self.assertTrue(self.report(ET.tostring(root, encoding="unicode"))["ok"])

    def test_invalid_and_multiple_polyline_indices_are_refused(self):
        for indices in ("0 5 -1", "0 1 -1 0 1 -1", "0 1.5 -1", "0", ""):
            with self.subTest(indices=indices):
                self.rejects(self.mutate(lambda r: r.find(".//IndexedLineSet").set("coordIndex", indices)), code="invalid_coordinate_index")

    def test_changing_rendered_colour_fails(self):
        self.rejects(self.mutate(lambda r: r.find(".//Shape[@DEF='Edge']/Appearance/Material").set("emissiveColor", "1 0 0")), code="relation_color_mismatch")

    def test_changing_plan_and_render_colour_cannot_override_grade_encoding(self):
        plan = copy.deepcopy(self.plan)
        plan["relations"][0]["color"] = [1, 0, 0]
        xml = self.mutate(lambda r: r.find(".//Shape[@DEF='Edge']/Appearance/Material").set("emissiveColor", "1 0 0"))
        self.rejects(xml, plan=plan, code="relation_color_mismatch")

    def test_containerfield_cannot_move_geometry_out_of_render_fields(self):
        for path in (".//Transform[@DEF='A']", ".//Sphere", ".//IndexedLineSet", ".//Coordinate", ".//Shape[@DEF='Edge']/Appearance", ".//Shape[@DEF='Edge']/Appearance/Material"):
            with self.subTest(path=path):
                self.rejects(self.mutate(lambda r: r.find(path).set("containerField", "metadata")), code="invalid_semantic_container_field")

    def test_metadata_grade_tampering_fails(self):
        def mutate(root):
            meta = root.find(".//Shape[@DEF='Edge']/MetadataString")
            data = json.loads(json.loads(meta.get("value")))
            data["grade"] = "S"
            meta.set("value", json.dumps(json.dumps(data)))
        self.rejects(self.mutate(mutate), code="relation_metadata_mismatch")

    def test_changing_plan_and_metadata_grade_cannot_override_source(self):
        plan = copy.deepcopy(self.plan)
        plan["relations"][0]["grade"] = "S"
        def mutate(root):
            meta = root.find(".//Shape[@DEF='Edge']/MetadataString")
            data = json.loads(json.loads(meta.get("value")))
            data["grade"] = "S"
            meta.set("value", json.dumps(json.dumps(data)))
        self.rejects(self.mutate(mutate), plan=plan, code="relation_source_mismatch")

    def test_actual_attachment_must_match_source_glyph_even_if_plan_points_change(self):
        plan = copy.deepcopy(self.plan)
        plan["relations"][0]["points"][-1] = [8, 0, 0]
        xml = self.mutate(lambda r: r.find(".//Coordinate").set("point", "1.12 0 0, 8 0 0"))
        self.rejects(xml, plan=plan, code="relation_attachment_mismatch")

    def test_metadata_relabel_does_not_reassign_source_identity(self):
        def mutate(root):
            meta = root.find(".//Transform[@DEF='B']/MetadataString")
            meta.set("value", json.dumps(json.dumps({"id": "node:unwitnessed"})))
        result = self.rejects(self.mutate(mutate), code="extra_scene_entities")
        self.assertNotIn("unwitnessed", json.dumps(result))

    def test_added_unannotated_line_in_relation_layer_is_rejected(self):
        def mutate(root):
            layer = root.find(".//Group[@DEF='Relations']")
            layer.append(ET.fromstring("<Shape><Appearance><Material/></Appearance><IndexedLineSet coordIndex='0 1 -1'><Coordinate point='0 0 0 1 1 1'/></IndexedLineSet></Shape>"))
        self.rejects(self.mutate(mutate), code="unregistered_relation_geometry")

    def test_added_unannotated_glyph_in_entity_layer_is_rejected(self):
        def mutate(root):
            root.find(".//Group[@DEF='Entities']").append(ET.fromstring("<Transform><Shape><Appearance><Material/></Appearance><Sphere/></Shape></Transform>"))
        self.rejects(self.mutate(mutate), code="unregistered_entity_geometry")

    def test_source_witness_is_required(self):
        b = copy.deepcopy(self.bundle)
        b["ties"]["machine"][0]["evidence"] = []
        self.rejects(bundle=b, code="relation_has_no_source_witness")

    def test_source_endpoints_cannot_be_swapped_by_changing_metadata(self):
        p = copy.deepcopy(self.plan)
        p["relations"][0]["from_entity"] = "node:b"
        self.rejects(plan=p, code="relation_source_endpoint_mismatch")

    def test_def_uniqueness_missing_refs_cycles_and_wrong_types_fail(self):
        variants = [self.xml.replace("DEF='B'", "DEF='A'"), self.xml.replace("USE='SharedSphere'", "USE='missing'"),
                    self.xml.replace("USE='SharedSphere'", "USE='SharedMaterial'"),
                    self.xml.replace("<Sphere DEF='SharedSphere' radius='1'/>", "<Sphere DEF='SharedSphere' USE='SharedSphere'/>"),
                    self.xml.replace("<Sphere USE='SharedSphere'/>", "<Sphere USE='SharedSphere' radius='3'/>")]
        for xml in variants:
            with self.subTest(xml=xml[-70:]):
                self.rejects(xml)

    def test_hidden_or_runtime_dependent_semantic_ancestors_are_refused(self):
        for start, end in (("<Switch whichChoice='-1'>", "</Switch>"), ("<Billboard>", "</Billboard>"),
                           ("<Group visible='false'>", "</Group>")):
            xml = self.xml.replace("<Group DEF='Entities'>", start+"<Group DEF='Entities'>").replace("</Group><Group DEF='Relations'>", "</Group>"+end+"<Group DEF='Relations'>", 1)
            self.rejects(xml, code="unsupported_semantic_ancestor")

    def test_transparency_and_shader_overrides_are_refused(self):
        xml = self.mutate(lambda r: r.find(".//Shape[@DEF='Edge']/Appearance/Material").set("transparency", "1"))
        self.rejects(xml, code="invisible_semantic_geometry")
        xml = self.mutate(lambda r: r.find(".//Shape[@DEF='Edge']/Appearance").append(ET.Element("ComposedShader")))
        self.rejects(xml, code="unsupported_semantic_appearance")

    def test_equivalent_nested_transforms_preserve_world_geometry(self):
        root = ET.fromstring(self.xml)
        scene = root.find("Scene")
        for layer in list(scene):
            scene.remove(layer)
            outer = ET.SubElement(scene, "Transform", translation="2 3 4", rotation="0 0 1 1.5707963267948966", scale="2 2 2")
            inverse_scale = ET.SubElement(outer, "Transform", scale="0.5 0.5 0.5")
            inverse_rotation = ET.SubElement(inverse_scale, "Transform", rotation="0 0 1 -1.5707963267948966")
            inverse_translate = ET.SubElement(inverse_rotation, "Transform", translation="-2 -3 -4")
            inverse_translate.append(layer)
        self.assertTrue(self.report(ET.tostring(root, encoding="unicode"))["ok"])

    def test_center_scale_orientation_and_rotation_use_x3d_order(self):
        plan = copy.deepcopy(self.plan)
        plan["entities"] = [{"id": "node:a", "def": "A", "source_name": "a", "position": [3, 2, 4],
                             "geometry": {"type": "Box", "size": [2, 4, 2]}}]
        plan["relations"] = []
        xml = ("<X3D><Scene><Group DEF='Entities'><Transform DEF='A' translation='2 3 4' center='1 0 0' "
               f"rotation='0 0 1 {math.pi/2}' scaleOrientation='0 0 1 {math.pi/2}' scale='2 1 1'>"+
               metadata("xray.entity", {"id": "node:a"})+
               "<Shape><Appearance><Material/></Appearance><Box size='2 2 2'/></Shape></Transform></Group>"
               "<Group DEF='Relations'/></Scene></X3D>")
        self.assertTrue(self.report(xml, plan=plan)["ok"])

    def test_nonuniform_scaled_sphere_is_out_of_supported_subset(self):
        self.rejects(self.mutate(lambda r: r.find(".//Transform[@DEF='A']").set("scale", "2 1 1")), code="unsupported_ellipsoid_geometry")

    def test_dtd_entities_malformed_namespaces_and_nonfinite_data_fail_safely(self):
        for xml in ("", "<X3D>", "<!DOCTYPE X3D [<!ENTITY leak SYSTEM 'file:///private'>]>"+self.xml,
                    self.xml.replace("<X3D ", "<X3D xmlns='urn:unknown' "),
                    self.xml.replace("translation='6 0 0'", "translation='NaN 0 0'")):
            with self.subTest(kind=xml[:25]):
                report = self.rejects(xml)
                self.assertNotIn("/private", json.dumps(report))
        self.rejects(" "*(MAX_XML_BYTES+1)+self.xml, code="scene_xml_too_large")

    def test_empty_plan_with_empty_layers_is_a_valid_explicit_population(self):
        p = {"schema_version": 1, "scope": "constellation", "entity_layer": "Entities", "relation_layer": "Relations", "entities": [], "relations": []}
        self.assertTrue(self.report("<X3D><Scene><Group DEF='Entities'/><Group DEF='Relations'/></Scene></X3D>", plan=p)["ok"])

    def test_malformed_plan_fields_return_findings_not_raw_exceptions(self):
        for patch in ({"entities": None}, {"relations": {}}, {"schema_version": "1"}):
            p = {**self.plan, **patch}
            self.rejects(plan=p)
        for patch in ({"position": [0, float('nan'), 0]}, {"geometry": {"type": "Sphere", "radius": None}}):
            p = copy.deepcopy(self.plan)
            p["entities"][0].update(patch)
            self.rejects(plan=p)
        for section, patch in (("entities", {"id": []}), ("entities", {"source_name": []}),
                               ("relations", {"id": []}), ("relations", {"from_entity": []})):
            p = copy.deepcopy(self.plan)
            p[section][0].update(patch)
            self.rejects(plan=p)
        self.rejects(bundle={"repos": None, "ties": []})

    def test_inspection_never_returns_unknown_xml_ids(self):
        def mutate(root):
            shape = copy.deepcopy(root.find(".//Shape[@DEF='Edge']"))
            shape.set("DEF", "Extra")
            meta = shape.find("MetadataString")
            meta.set("value", json.dumps(json.dumps({"id": "secret/path/value"})))
            root.find(".//Group[@DEF='Relations']").append(shape)
        report = self.rejects(self.mutate(mutate))
        self.assertNotIn("secret/path/value", json.dumps(report))

    def test_compact_use_dag_is_bounded_by_global_expansion_allowance(self):
        # Each definition doubles the previous subgraph, despite linear XML
        # size. The graph remains outside semantic layers: it cannot evade the
        # verification resource budget merely by being called decoration.
        definitions = ["<Group DEF='D0'/>" ]
        for i in range(1, 18):
            definitions.append(f"<Group DEF='D{i}'><Group USE='D{i-1}'/><Group USE='D{i-1}'/></Group>")
        xml = self.xml.replace("<Scene>", "<Scene>"+"".join(definitions), 1)
        with patch("x3dray.scenecheck.MAX_EXPANDED_VISITS", 512):
            report = self.rejects(xml, code="scene_expansion_limit")
        self.assertEqual(report["checks"]["expanded_visits"], 512)
        self.assertEqual(report["limits"]["expanded_visits"], 512)
        self.assertNotIn("observed", report)

    def test_glyph_subwalk_shares_the_same_global_budget(self):
        root = ET.fromstring(self.xml)
        owner = root.find(".//Transform[@DEF='A']")
        # A binary empty-group DAG inside a registered glyph consumes work in
        # glyphs() before the outer walk resumes. The original Sphere is kept
        # so budget exhaustion, rather than a missing primitive, is observed.
        owner.insert(1, ET.fromstring("<Group DEF='G0'/>") )
        for i in range(1, 15):
            owner.insert(i+1, ET.fromstring(f"<Group DEF='G{i}'><Group USE='G{i-1}'/><Group USE='G{i-1}'/></Group>"))
        with patch("x3dray.scenecheck.MAX_EXPANDED_VISITS", 512):
            report = self.rejects(ET.tostring(root, encoding="unicode"), code="scene_expansion_limit")
        self.assertEqual(report["checks"]["expanded_visits"], 512)

    def test_nonexpanding_shared_geometry_still_passes_with_moderate_budget(self):
        with patch("x3dray.scenecheck.MAX_EXPANDED_VISITS", 512):
            report = self.report()
        self.assertTrue(report["ok"], report)
        self.assertLess(report["checks"]["expanded_visits"], 512)


if __name__ == "__main__":
    unittest.main()
