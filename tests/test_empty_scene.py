"""An empty observed population is valid and must not fabricate activity."""
import unittest
import xml.etree.ElementTree as ET
from x3dray import compiler, profile


class EmptySceneTests(unittest.TestCase):
    def test_no_observed_activity_produces_explicit_scene(self):
        ds = {"family": "empty", "repos": [], "lineage": []}
        prof = profile.profile_family(ds, {"pairs": []})
        xml, layout = compiler.build_scene(ds, {"pairs": []}, None, prof, {"views": []}, {})
        scene = ET.fromstring(xml)
        self.assertEqual(scene.tag, "X3D")
        self.assertEqual(layout["counts"]["commits"], 0)
        self.assertEqual(layout["days"], [])
        self.assertIn("No observed own commits", xml)
        self.assertNotIn("busiest", xml)
        self.assertIsNotNone(scene.find(".//Viewpoint[@DEF='VP_overview']"))
