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


def bind_payload(xml, plan, payload):
    """Bind an explicit golden payload without using a producer hash helper."""
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True,
                                      separators=(",", ":"), allow_nan=False).encode("ascii")).hexdigest()
    plan["source_binding_schema"] = 1
    plan["relations"][0]["source_digest"] = digest
    root = ET.fromstring(xml)
    meta = root.find(".//Shape[@DEF='Edge']/MetadataString")
    record = json.loads(json.loads(meta.get("value")))
    record["source_digest"] = digest
    meta.set("value", json.dumps(json.dumps(record, sort_keys=True)))
    return ET.tostring(root, encoding="unicode")


def modern_fixture(private=False):
    xml, plan, bundle = fixture()
    bundle["ties"]["machine"][0]["kinds"] = {"uses": 1}
    payload = {"schema_version": 1, "source": "machine", "from": "a", "to": "b", "grade": "C",
               "kinds": {"uses": 1}, "visibility": "aggregate_only" if private else "public", "evidence_count": 1,
               "evidence": [{"count": 1}] if private else [{"kind": "uses", "path": "config.json", "text": "uses b"}]}
    if private:
        bundle["repos"][0]["visibility"] = "private"
    bundle["scene_sources"] = copy.deepcopy(bundle["ties"])
    return bind_payload(xml, plan, payload), plan, bundle, payload


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

    def test_source_witness_must_be_substantive_and_correctly_typed(self):
        for evidence in ([{}], [{"path": "  ", "text": "\t"}], "not-a-list", {"path": "source.py"},
                         ["machine evidence is not free prose"], [{"line": 10}], [{"path": 1}], None):
            with self.subTest(evidence=evidence):
                b = copy.deepcopy(self.bundle)
                b["ties"]["machine"][0]["evidence"] = evidence
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
                           ("<Group visible='false'>", "</Group>"), ("<CADLayer visible='false true'>", "</CADLayer>"),
                           ("<GeoLocation geoCoords='1 2 3'>", "</GeoLocation>"),
                           ("<HAnimJoint translation='1 2 3'>", "</HAnimJoint>")):
            xml = self.xml.replace("<Group DEF='Entities'>", start+"<Group DEF='Entities'>").replace("</Group><Group DEF='Relations'>", "</Group>"+end+"<Group DEF='Relations'>", 1)
            self.rejects(xml, code="unsupported_semantic_ancestor")

    def test_unknown_transform_inside_entity_glyph_is_refused(self):
        def mutate(root):
            owner = root.find(".//Transform[@DEF='A']")
            shape = owner.find("Shape")
            owner.remove(shape)
            ET.SubElement(owner, "HAnimJoint", translation="1 2 3").append(shape)
        self.rejects(self.mutate(mutate), code="unsupported_semantic_ancestor")

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

    def test_deep_json_metadata_returns_a_controlled_finding(self):
        root = ET.fromstring(self.xml)
        root.find(".//Shape[@DEF='Edge']/MetadataString").set("value", json.dumps("["*1500+"0"+"]"*1500))
        self.rejects(ET.tostring(root, encoding="unicode"), code="invalid_semantic_metadata")

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


class SceneSourceBindingTests(unittest.TestCase):
    def setUp(self):
        self.xml, self.plan, self.bundle, self.payload = modern_fixture()

    def report(self):
        return verify_scene(self.xml, self.plan, self.bundle)

    def rejects(self, code):
        report = self.report()
        self.assertFalse(report["ok"], report)
        self.assertIn(code, {f["code"] for f in report["findings"]}, report)
        return report

    def rebind(self):
        self.xml = bind_payload(self.xml, self.plan, self.payload)

    def test_explicit_golden_binding_and_legacy_scope(self):
        report = self.report()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["source_binding"]["mode"], "public_sample_or_private_aggregate")
        self.assertEqual(report["source_binding"]["source_authentication"], "not verified")
        self.assertEqual(report["checks"]["eligible_source_relations"], 1)
        xml, plan, bundle = fixture()
        self.assertEqual(verify_scene(xml, plan, bundle)["source_binding"]["mode"], "legacy_declared_subset")

    def test_ledger_source_witness_and_detector_changes_break_binding(self):
        for key, value in (("evidence", [{"path": "different.py", "text": "changed witness"}]), ("kinds", {"uses": 2})):
            with self.subTest(key=key):
                original = copy.deepcopy(self.bundle)
                self.bundle["scene_sources"]["machine"][0][key] = value
                report = self.rejects("relation_source_digest_mismatch")
                self.assertNotIn("different.py", json.dumps(report))
                self.bundle = original

    def test_display_and_ledger_are_both_bound(self):
        self.bundle["ties"]["machine"][0]["evidence"][0]["text"] = "changed served witness"
        self.rejects("display_source_digest_mismatch")

    def test_recursive_source_fields_return_a_controlled_finding(self):
        nested = {}
        nested["start"] = nested
        self.bundle["ties"]["machine"][0]["evidence"][0]["valid_time"] = nested
        self.rejects("invalid_source_binding")

    def test_drawn_relations_require_display_witness_record(self):
        self.bundle["ties"]["machine"] = []
        self.rejects("drawn_relation_missing_from_display")

    def test_binding_also_works_with_original_source_without_ledger(self):
        del self.bundle["scene_sources"]
        self.assertTrue(self.report()["ok"])

    def test_metadata_digest_tampering_fails(self):
        root = ET.fromstring(self.xml)
        meta = root.find(".//Shape[@DEF='Edge']/MetadataString")
        record = json.loads(json.loads(meta.get("value")))
        record["source_digest"] = "0"*64
        meta.set("value", json.dumps(json.dumps(record)))
        self.xml = ET.tostring(root, encoding="unicode")
        self.rejects("relation_metadata_mismatch")

    def test_complete_source_membership_cannot_be_deleted_from_plan_and_xml(self):
        self.plan["relations"] = []
        root = ET.fromstring(self.xml)
        root.find(".//Group[@DEF='Relations']").clear()
        root.find(".//Scene/Group[2]").set("DEF", "Relations")
        self.xml = ET.tostring(root, encoding="unicode")
        self.rejects("source_relation_coverage_mismatch")

    def test_isolated_source_repositories_cannot_be_silently_omitted(self):
        self.bundle["repos"].append({"name": "isolated"})
        self.rejects("source_entity_coverage_mismatch")

    def test_omissions_have_independently_checked_reasons_and_no_phantom_nodes(self):
        cases = [("witnessless", [], "C", "no_witness"), ("a", [{"path": "self.py"}], "C", "self_relation"),
                 ("invalid", [{"path": "bad.py"}], "?", "invalid_grade"),
                 ("missing-grade", [{"path": "bad.py"}], None, "invalid_grade")]
        self.plan["omitted"] = []
        for to, evidence, grade, reason in cases:
            record = {"from": "a", "to": to, "grade": grade, "evidence": evidence}
            self.bundle["scene_sources"]["machine"].append(record)
            rid = "machine:"+hashlib.sha256(("a\0"+to).encode()).hexdigest()[:20]
            self.plan["omitted"].append({"id": rid, "source": "machine", "from": "a", "to": to, "reason": reason})
        self.assertTrue(self.report()["ok"], self.report())
        self.plan["omitted"][0]["reason"] = "display_cap"
        self.rejects("invalid_source_omission")

    def test_omission_ledger_must_cover_exactly_ineligible_sources(self):
        self.plan["omitted"] = [{"id": self.plan["relations"][0]["id"], "source": "machine", "from": "a", "to": "b", "reason": "no_witness"}]
        self.rejects("invalid_source_omission")
        self.plan["omitted"][0]["id"] = "machine:unknown"
        self.rejects("source_omission_unverifiable")

    def test_evidence_count_binds_population_beyond_the_four_witness_sample(self):
        evidence = [{"path": f"{i}.py"} for i in range(5)]
        for section in ("ties", "scene_sources"):
            self.bundle[section]["machine"][0]["evidence"] = copy.deepcopy(evidence)
        self.payload["evidence"] = evidence[:4]
        self.payload["evidence_count"] = 5
        self.rebind()
        self.assertTrue(self.report()["ok"], self.report())
        # Contents after the bounded sample are explicitly outside the binding.
        self.bundle["ties"]["machine"][0]["evidence"][4]["path"] = "outside-sample.py"
        self.assertTrue(self.report()["ok"])
        self.bundle["ties"]["machine"][0]["evidence"].append({"path": "six.py"})
        self.rejects("display_source_digest_mismatch")

    def test_count_is_maximum_of_total_and_raw_list_length(self):
        for value in (0, -1, True, "1", None):
            with self.subTest(value=value):
                for section in ("ties", "scene_sources"):
                    self.bundle[section]["machine"][0]["evidence_records"] = {"total": value}
                self.assertTrue(self.report()["ok"], self.report())
        self.bundle["ties"]["machine"][0]["evidence_records"] = {"total": 9}
        self.rejects("display_source_digest_mismatch")

    def test_full_locator_and_observation_time_are_bound_without_truncation(self):
        locator = {"schema_version": 1, "kind": "git_blob", "visibility": "public", "repo_id": "repo:a",
                   "revision": {"oid": "a"*40, "object_format": "sha1"},
                   "blob": {"oid": "b"*40, "object_format": "sha1", "mode": "100644", "size_bytes": 20, "content_sha256": "c"*64},
                   "path": "source.py", "span": {"kind": "lines", "encoding": "utf-8", "start": 1, "end": 2,
                   "byte_start": 0, "byte_end": 20, "content_sha256": "d"*64}, "detector": {"id": "uses", "version": "1"}}
        evidence = {"path": "source.py", "locator": locator, "locators": [locator],
                    "observed_at": "2026-11-12T00:00:00Z", "valid_time": {"start": "2026-11-01T00:00:00Z", "end": None}}
        self.payload["evidence"] = [evidence]
        for section in ("ties", "scene_sources"):
            self.bundle[section]["machine"][0]["evidence"] = [copy.deepcopy(evidence)]
        self.rebind()
        self.assertTrue(self.report()["ok"], self.report())
        # Normalization strips unknown fields and canonicalizes object IDs.
        display = self.bundle["ties"]["machine"][0]["evidence"][0]
        display["locator"]["unknown"] = "not part of locator schema"
        display["locator"]["revision"]["oid"] = "A"*40
        self.assertTrue(self.report()["ok"], self.report())
        for change in (lambda e: e["locator"]["revision"].update(oid="e"*40),
                       lambda e: e["locators"][0]["span"].update(content_sha256="f"*64),
                       lambda e: e.update(observed_at="2026-11-13T00:00:00Z"),
                       lambda e: e["valid_time"].update(end="2026-11-14T00:00:00Z")):
            with self.subTest(change=change):
                original = copy.deepcopy(display)
                change(display)
                self.rejects("display_source_digest_mismatch")
                display.clear()
                display.update(original)

    def test_producer_projection_matches_independent_golden_public_and_private_binding(self):
        # A compatibility check supplements (rather than supplies) the golden
        # payload above. The checker imports no producer projection/hash code.
        from x3dray.witness_contract import source_digest
        for private in (False, True):
            xml, plan, bundle, _ = modern_fixture(private=private)
            digest = source_digest(bundle["ties"]["machine"][0], "machine", {"a"} if private else set())
            self.assertEqual(plan["relations"][0]["source_digest"], digest)
            self.assertTrue(verify_scene(xml, plan, bundle)["ok"])

    def test_private_binding_ignores_hidden_content_but_preserves_aggregate_counts(self):
        self.xml, self.plan, self.bundle, self.payload = modern_fixture(private=True)
        for section in ("ties", "scene_sources"):
            record = self.bundle[section]["machine"][0]
            record["evidence"][0].update(path="private/hidden.py", text="hidden source text", locator={"secret": "private"})
            record["kinds"] = {"uses": 1, "private detector identity": 3}
        self.payload["kinds"]["other"] = 3
        self.rebind()
        self.assertTrue(self.report()["ok"], self.report())
        self.bundle["ties"]["machine"][0]["kinds"] = {"uses": 1, "another hidden detector identity": 3}
        self.assertTrue(self.report()["ok"])
        self.bundle["ties"]["machine"][0]["evidence_records"] = {"total": 2}
        report = self.rejects("display_source_digest_mismatch")
        self.assertNotIn("hidden source", json.dumps(report))
        self.assertNotIn("private/hidden", json.dumps(report))

    def test_private_curated_kind_is_aggregate_and_never_free_prose(self):
        self.xml, self.plan, self.bundle, self.payload = modern_fixture(private=True)
        self.plan["relations"][0].update(id="curated:manual", source="curated")
        self.payload.update(source="curated", kinds={"curated_kind": "aggregate"})
        for section in ("ties", "scene_sources"):
            record = self.bundle[section]["machine"].pop()
            record.update(id="manual", kind="private curator prose", evidence=["private witness"])
            self.bundle[section]["curated"].append(record)
        root = ET.fromstring(self.xml)
        meta = root.find(".//Shape[@DEF='Edge']/MetadataString")
        record = json.loads(json.loads(meta.get("value")))
        record.update(id="curated:manual", source="curated")
        meta.set("value", json.dumps(json.dumps(record)))
        self.xml = ET.tostring(root, encoding="unicode")
        self.rebind()
        self.assertTrue(self.report()["ok"], self.report())
        self.bundle["ties"]["curated"][0]["kind"] = "changed hidden prose"
        self.assertTrue(self.report()["ok"])


if __name__ == "__main__":
    unittest.main()
