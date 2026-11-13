"""The look gallery (x3dray/gallery.py) against its independent reader (x3dray/gallerycheck.py).

The reader derives every expectation from bundle.eda.looks by its own code, so a mutation that changes the XML, or one that changes only the stored
numbers, has to be rejected with the right finding. The cases here are the gallery's own mutation suite (the constellation has its own)."""
import copy
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from eda_support import synthetic_dataset  # noqa: E402
from x3dray import eda, gallery, gallerycheck  # noqa: E402

FONT = '"fonts/UbuntuMono-R.ttf" "TYPEWRITER"'
KW = dict(seed=7, repos=4, per_repo=150, plant=("ai_hours", "ai_smaller"))


def wrap(lines):
    return "<?xml version='1.0'?><X3D><Scene>" + "\n".join(lines) + "</Scene></X3D>"


def build(**kw):
    e, problems = eda.run(synthetic_dataset(**(kw or KW)))
    assert problems == [], problems
    g = gallery.region(e["looks"], FONT)
    return e, g, wrap(g["xml"])


def codes(r):
    return {f["code"] for f in r["findings"]}


class Baseline(unittest.TestCase):
    def test_accepts_what_the_producer_draws(self):
        for kw in (KW, dict(seed=3, repos=1, per_repo=14), dict(seed=41, repos=3, per_repo=120, private=("r2",), plant=("ai_hours", "outlier"))):
            e, g, xml = build(**kw)
            r = gallerycheck.verify_gallery(xml, g["plan"], e)
            self.assertTrue(r["ok"], r["findings"][:3])
            self.assertEqual(r["checks"]["panels"], 12)
            self.assertEqual(r["checks"]["texts"], 15)

    def test_eight_seeds_with_and_without_planted_effects(self):
        for seed in range(100, 108):
            e, g, xml = build(seed=seed, repos=3, per_repo=60, plant=("ai_hours",) if seed % 2 else None)
            r = gallerycheck.verify_gallery(xml, g["plan"], e)
            self.assertTrue(r["ok"], (seed, r["findings"][:3]))

    def test_the_producer_is_deterministic_and_pure(self):
        e, g1, _ = build()
        g2 = gallery.region(copy.deepcopy(e["looks"]), FONT)
        self.assertEqual(g1["xml"], g2["xml"])

    def test_a_look_that_did_not_run_is_a_hollow_slab_and_no_bars(self):
        e, g, xml = build(seed=3, repos=1, per_repo=14)
        self.assertEqual(xml.count("xray.panel"), 12)
        self.assertNotIn("\"role\\\":\\\"effect", xml.replace("&quot;", "\""))
        self.assertEqual(xml.count("transparency='0.65'"), 12 * 5)       # slab plus four cubes, each hollow
        self.assertIn("not run", xml)

    def test_the_scale_rule_is_exact_and_resolves_one_permille(self):
        e, g, xml = build()
        m = re.search(r"<Box size='1\.2 (\d+(?:\.\d+)?) 1\.2'/>", xml)
        f0 = e["looks"]["findings"][0]
        self.assertEqual(float(m.group(1)), f0["effect"]["permille"] * 0.006)
        self.assertGreater(0.006, 2 * float(gallerycheck.TOL))             # one permille is far more than the tolerance

    def test_a_negative_cliff_effect_is_a_bar_of_its_magnitude_in_the_other_colour(self):
        e, g, xml = build()
        f = next(x for x in e["looks"]["findings"] if x["id"] == "contrast:files")
        self.assertLess(f["effect"]["permille"], 0)
        panel = xml[xml.index(gallery.panel_def("contrast:files")):]
        panel = panel[:panel.index("</Transform>\n</Transform>")]
        self.assertIn(f"<Box size='1.2 {abs(f['effect']['permille']) * 0.006:.3f} 1.2'/>".replace(".000", ""), panel)
        self.assertIn(f"diffuseColor='{gallery._hex(gallery.NEG_HEX)}'", panel)
        self.assertIn(f"effect {f['effect']['permille']}/1000", panel)

    def test_a_distance_is_labelled_a_distance(self):
        e, g, xml = build()
        f = next(x for x in e["looks"]["findings"] if x["id"] == "contrast:hour4")
        self.assertEqual(f["effect"]["kind"], "tv")
        self.assertIn(f"distance {f['effect']['permille']}/1000", xml)

    def test_labels_are_registered_for_the_live_gate(self):
        e, g, xml = build()
        self.assertEqual(len(g["labels"]), 15)
        self.assertTrue(all(l["text"] and l["view"] == "gallery" for l in g["labels"]))
        self.assertEqual(len(g["records"]), 12)


def tamper(xml, fn):
    out = fn(xml)
    assert out != xml, "mutation changed nothing"
    return out


def first(pattern, repl, count=1):
    return lambda x: re.sub(pattern, repl, x, count=count, flags=re.S)


class Mutations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e, cls.g, cls.xml = build()
        cls.plan = cls.g["plan"]

    def verify(self, xml=None, e=None):
        return gallerycheck.verify_gallery(xml if xml is not None else self.xml, self.plan, e if e is not None else self.e)

    def reject(self, xml, code, e=None):
        r = self.verify(xml, e)
        self.assertFalse(r["ok"])
        self.assertIn(code, codes(r), r["findings"][:4])

    def test_effect_bar_one_permille_too_tall(self):
        pm = self.e["looks"]["findings"][0]["effect"]["permille"]
        self.reject(tamper(self.xml, first(rf"size='1\.2 {pm * 0.006:.3f}".rstrip("0") + r"[^ ]* 1\.2", lambda m: f"size='1.2 {(pm + 1) * 0.006:.3f} 1.2")), "glyph_size")

    def test_bar_moved(self):
        self.reject(tamper(self.xml, first(r"(<Transform translation=')-2\.6 ", r"\g<1>-2.5 ")), "glyph_position")

    def test_slab_grade_colour_swapped(self):
        c, l = gallery._hex(gallery.GRADE_HEX["C"]), gallery._hex(gallery.GRADE_HEX["L"])
        self.reject(tamper(self.xml, lambda x: x.replace(f"diffuseColor='{c}'", f"diffuseColor='{l}'", 1)), "glyph_colour")

    def test_a_glyph_that_loses_its_glow_is_refused(self):
        c = gallery._hex(gallery.GRADE_HEX["C"])
        self.reject(tamper(self.xml, lambda x: x.replace(f"emissiveColor='{gallery._hex(gallery.GRADE_HEX['C'], gallery.GLOW)}'", f"emissiveColor='{c}'", 1)), "glyph_colour")

    def test_label_digit_changed(self):
        self.reject(tamper(self.xml, first(r'effect \+647/1000', "effect +674/1000")), "text_mismatch")

    def test_subtitle_count_changed(self):
        self.reject(tamper(self.xml, first(r"Confirmed 2", "Confirmed 3")), "text_mismatch")

    def test_panel_deleted(self):
        self.reject(tamper(self.xml, first(r"<Transform DEF='gp[0-9a-f]{20}' translation='22\.5 .*?</Transform>\n</Transform>", "")), "panel_count")

    def test_panel_duplicated(self):
        def dup(x):
            m = re.search(r"(<Transform DEF='gp[0-9a-f]{20}' translation='-22\.5 .*?\n</Transform>)\n", x, re.S)
            return x.replace(m.group(1), m.group(1) + "\n" + m.group(1).replace(re.search(r"DEF='(gp[0-9a-f]{20})'", m.group(1)).group(0), "DEF='gpduplicate'"), 1)
        r = self.verify(tamper(self.xml, dup))
        self.assertFalse(r["ok"])
        self.assertTrue(codes(r) & {"panel_duplicate", "panel_count"})

    def test_two_panels_swapped_in_order(self):
        root = ET.fromstring(self.xml)
        layer = next(g for g in root.iter("Group") if g.get("DEF") == "XrayGallery")
        ps = [c for c in layer if c.tag == "Transform" and c.find("MetadataString") is not None and c.find("MetadataString").get("name") == "xray.panel"]
        i, j = list(layer).index(ps[0]), list(layer).index(ps[1])
        layer[i], layer[j] = ps[1], ps[0]
        self.reject(ET.tostring(root, encoding="unicode"), "panel_order")

    def test_an_unregistered_bar_is_refused(self):
        extra = "<Transform translation='0 1 0'><Shape><Appearance><Material diffuseColor='1 0 0' emissiveColor='1 0 0'/></Appearance><Box size='1 1 1'/></Shape></Transform>"
        self.reject(tamper(self.xml, first(r"(</Transform>\n)(</Group>)", r"\g<1>" + extra + r"\g<2>")), "unregistered_child")

    def test_an_extra_registered_glyph_is_refused(self):
        def add(x):
            m = re.search(r"(<MetadataString name='xray.glyph' value='\"\{\\\"panel\\\":\\\"contrast:lines\\\",\\\"role\\\":\\\"slab\\\"\}\"'/>)", x)
            dup = "<Transform translation='0 0 0'>" + m.group(1).replace("slab", "bar9") + "<Shape><Appearance><Material/></Appearance><Box size='1 1 1'/></Shape></Transform>"
            return x.replace("</Transform>\n<Transform translation='-2.6", dup + "</Transform>\n<Transform translation='-2.6", 1) if False else x.replace("<Transform translation='-2.6", dup + "<Transform translation='-2.6", 1)
        self.reject(tamper(self.xml, add), "glyph_extra")

    def test_a_glyph_hidden_under_a_switch_is_refused(self):
        r = self.verify(tamper(self.xml, first(r"(<Shape><Appearance><Material diffuseColor='0\.353 0\.784 0\.878'.*?</Shape>)", r"<Switch whichChoice='-1'>\g<1></Switch>")))
        self.assertFalse(r["ok"])
        self.assertTrue(codes(r) & {"unsupported_node", "glyph_shape"}, r["findings"][:3])

    def test_sharing_by_use_is_refused(self):
        self.reject(tamper(self.xml, first(r"<Box size='0\.6 0\.6 0\.6'/>", "<Box USE='x'/>")), "use_in_gallery")

    def test_a_second_layer_is_refused(self):
        self.reject(tamper(self.xml, lambda x: x.replace("</Scene>", "<Group DEF='XrayGallery'/></Scene>")), "layer_count")

    def test_a_marker_outside_the_layer_is_refused(self):
        self.reject(tamper(self.xml, lambda x: x.replace("</Scene>", "<Transform><MetadataString name='xray.panel' value='\"{}\"'/></Transform></Scene>")), "marker_outside_layer")

    def test_a_text_removed(self):
        self.reject(tamper(self.xml, first(r"<Text string='\"The 12 declared looks[^>]*>.*?</Text>", "")), "text_node")

    def test_a_denominator_text_cannot_say_a_different_twelve(self):
        self.reject(tamper(self.xml, first(r"The 12 declared", "The 11 declared")), "text_mismatch")

    # stored numbers changed and the XML left alone: the reader's own rules notice
    def test_bundle_tamper_effect_permille(self):
        e = copy.deepcopy(self.e)
        e["looks"]["findings"][0]["effect"]["permille"] += 1
        self.reject(self.xml, "glyph_size", e)

    def test_bundle_tamper_grade(self):
        e = copy.deepcopy(self.e)
        e["looks"]["findings"][0]["grade"] = "S"
        self.reject(self.xml, "glyph_colour", e)

    def test_bundle_tamper_a_check(self):
        e = copy.deepcopy(self.e)
        e["looks"]["findings"][0]["checks"]["loro"]["agree"] = 0
        self.reject(self.xml, "glyph_colour", e)

    def test_bundle_tamper_a_look_reordered(self):
        e = copy.deepcopy(self.e)
        fs = e["looks"]["findings"]
        fs[0], fs[1] = fs[1], fs[0]
        self.reject(self.xml, "panel_position", e)

    def test_bundle_tamper_a_look_dropped(self):
        e = copy.deepcopy(self.e)
        del e["looks"]["findings"][3]
        self.reject(self.xml, "panel_count", e)

    def test_bundle_tamper_not_run_made_solid(self):
        e, g, xml = build(seed=3, repos=1, per_repo=14)
        r = gallerycheck.verify_gallery(xml, g["plan"], e)
        self.assertTrue(r["ok"])
        e2 = copy.deepcopy(e)
        f = e2["looks"]["findings"][0]
        f["status"], f["reason"] = "evaluated", None
        r2 = gallerycheck.verify_gallery(xml, g["plan"], e2)
        self.assertFalse(r2["ok"])

    # equivalent spellings are accepted; what the reader does not read is said so
    def test_equivalent_serialisation_is_accepted(self):
        root = ET.fromstring(self.xml)
        r = self.verify(ET.tostring(root, encoding="unicode").replace("'", '"') if False else ET.tostring(root, encoding="unicode"))
        self.assertTrue(r["ok"], r["findings"][:3])
        self.assertTrue(self.verify(self.xml.replace("size='1.2 ", "size='1.200 ", 1)).get("ok"))

    def test_scope_probe_decoration_outside_the_layer_is_not_read(self):
        r = self.verify(self.xml.replace("</Scene>", "<Transform><Shape><Box size='9 9 9'/></Shape></Transform></Scene>"))
        self.assertTrue(r["ok"])
        self.assertIn("the rest of the scene", gallerycheck.SCOPE)

    def test_a_refusal_never_echoes_scene_text(self):
        r = self.verify(tamper(self.xml, first(r"effect \+647/1000", "effect +SECRET-private-name/1000")))
        self.assertNotIn("SECRET", repr(r))

    def test_no_independence_leaks(self):
        src = (ROOT / "x3dray" / "gallerycheck.py").read_text()
        for mod in ("compiler", "gallery", "eda", "looks", "stats", "witness_contract"):
            self.assertIsNone(re.search(rf"^\s*(from|import)\s+(x3dray\.|\.)?{mod}\b", src, re.M), mod)


if __name__ == "__main__":
    unittest.main()


import json  # noqa: E402
import tempfile  # noqa: E402

sys.path.insert(0, str(ROOT))
from experiments.scene_mutation_audit import build_fixture_dir  # noqa: E402
from x3dray import gates  # noqa: E402


def _write(out: Path, bundle: dict) -> None:
    (out / "bundle.json").write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")))


class EndToEnd(unittest.TestCase):
    """The real compile path (git fixtures, harvest, structure, eda, profile, spec, compiler, bundle) and the real gate."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out, cls.bundle = build_fixture_dir(Path(cls.tmp.name), with_eda=True)
        _write(cls.out, cls.bundle)
        cls.layout = json.loads((cls.out / "layout.json").read_text())
        cls.xml = (cls.out / "xray.x3d").read_text()
        cls.plain_tmp = tempfile.TemporaryDirectory()
        cls.plain, cls.plain_bundle = build_fixture_dir(Path(cls.plain_tmp.name), with_eda=False)
        _write(cls.plain, cls.plain_bundle)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        cls.plain_tmp.cleanup()

    def test_the_scene_carries_the_gallery_and_its_plan_travels_with_the_bundle(self):
        self.assertEqual(len(self.layout["gallery"]["panels"]), 12)
        self.assertEqual(self.layout["gallery_plan"], self.bundle["layout"]["gallery_plan"])
        self.assertIn("gallery", self.layout["views"])
        self.assertIn("halfw", self.layout["views"]["gallery"])
        self.assertEqual(self.layout["counts"]["labels"], len(self.layout["labels"]))
        self.assertEqual(sum(1 for l in self.layout["labels"] if l["view"] == "gallery"), 15)

    def test_the_gate_reads_the_gallery_from_the_stored_looks(self):
        g = gates.gate_scene(str(self.out))
        self.assertEqual(g["status"], "pass", g.get("findings"))
        self.assertIn("look gallery matches the stored looks", g["summary"])
        self.assertEqual(g["gallery_checks"]["panels"], 12)

    def test_a_scene_without_stored_looks_keeps_the_old_summary(self):
        g = gates.gate_scene(str(self.plain))
        self.assertEqual(g["status"], "pass", g.get("findings"))
        self.assertEqual(g["summary"], "Static constellation geometry matches source plan")
        self.assertNotIn("gallery_checks", g)

    def test_a_changed_scene_fails_the_gate_with_a_gallery_finding(self):
        (self.out / "xray.x3d").write_text(self.xml.replace("declared looks, one panel each", "declared looks, one panel apiece", 1))
        try:
            g = gates.gate_scene(str(self.out))
        finally:
            (self.out / "xray.x3d").write_text(self.xml)
        self.assertEqual(g["status"], "fail")
        self.assertIn("gallery:text_mismatch", {f["code"] for f in g["findings"]})

    def test_changed_stored_looks_fail_the_gate_while_the_scene_stays_put(self):
        b = copy.deepcopy(self.bundle)
        f = b["eda"]["looks"]["findings"][0]
        f["status"], f["reason"] = ("evaluated" if f["status"] != "evaluated" else "not_run"), "tampered"
        _write(self.out, b)
        try:
            g = gates.gate_scene(str(self.out))
        finally:
            _write(self.out, self.bundle)
        self.assertEqual(g["status"], "fail")

    def test_served_and_build_control_plans_must_agree(self):
        b = copy.deepcopy(self.bundle)
        b["layout"]["gallery_plan"]["origin"][1] += 1
        _write(self.out, b)
        try:
            g = gates.gate_scene(str(self.out))
        finally:
            _write(self.out, self.bundle)
        self.assertEqual(g["status"], "fail")
        self.assertIn("gallery plans differ", g["summary"])

    def test_a_missing_plan_beside_stored_looks_and_commits_fails(self):
        lay = copy.deepcopy(self.layout)
        del lay["gallery_plan"]
        b = copy.deepcopy(self.bundle)
        del b["layout"]["gallery_plan"]
        (self.out / "layout.json").write_text(json.dumps(lay))
        _write(self.out, b)
        try:
            g = gates.gate_scene(str(self.out))
        finally:
            (self.out / "layout.json").write_text(json.dumps(self.layout))
            _write(self.out, self.bundle)
        self.assertEqual(g["status"], "fail")
        self.assertIn("look gallery missing", g["summary"])
