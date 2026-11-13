"""Spec boundary tests: malformed input and stale output must become findings."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from x3dray import spec

PROFILE = {
    "family": "synthetic",
    "signals": [{"id": "span", "text": "A counted signal", "values": {"commits": 2, "day": "2026-11-12", "hour": 8}}],
    "per_repo": {},
}


def source():
    return {"family": "synthetic", "title": "Synthetic exhibit",
            "views": [{"id": "overview", "label": "Overview", "caption": "{span.commits} commits",
                       "claim": {"text": "{span.commits} commits", "backs": ["span"], "basis": "git"}}],
            "tour": [{"view": "overview", "caption": "{span.day|date}", "action": {"color_by": {"mode": "who"}}}],
            "questions": ["What was measured?"]}


class ShapeTests(unittest.TestCase):
    def test_missing_required_structure_is_reported(self):
        result = spec.render_spec({}, PROFILE)
        self.assertEqual(len(result["problems"]), 5)
        self.assertTrue(all("missing required field" in p for p in result["problems"]))

    def test_invalid_json_shapes_do_not_raise(self):
        variants = [None, [], {"family": 1}, source() | {"views": [None]}, source() | {"questions": [False]}]
        for value in variants:
            with self.subTest(value=value):
                self.assertTrue(spec.render_spec(value, PROFILE)["problems"])

    def test_duplicate_view_ids_are_refused(self):
        s = source()
        s["views"].append(copy.deepcopy(s["views"][0]))
        self.assertIn("views: duplicate view ids", spec.render_spec(s, PROFILE)["problems"])

    def test_unknown_action_argument_is_refused(self):
        s = source()
        s["tour"][0]["action"] = {"highlight": {"invented_filter": "yes"}}
        self.assertTrue(any("unknown field invented_filter" in p for p in spec.render_spec(s, PROFILE)["problems"]))

    def test_wrong_action_types_are_refused(self):
        for action in ({"xray": {"on": "true"}}, {"clear": {"repo": "alpha"}}, {"color_by": {"mode": "rainbow"}}, {"highlight": []}):
            s = source()
            s["tour"][0]["action"] = action
            with self.subTest(action=action):
                self.assertTrue(spec.render_spec(s, PROFILE)["problems"])

    def test_cross_family_input_is_refused(self):
        s = source() | {"family": "another-family"}
        self.assertIn("spec family does not match profile family", spec.render_spec(s, PROFILE)["problems"])

    def test_claimless_view_remains_supported_for_legacy_exhibits(self):
        s = source()
        del s["views"][0]["claim"]
        self.assertEqual(spec.render_spec(s, PROFILE)["problems"], [])


class FormattingTests(unittest.TestCase):
    def test_invalid_dates_become_findings(self):
        p = copy.deepcopy(PROFILE)
        p["signals"][0]["values"]["day"] = "2026-02-31"
        text, _, problems = spec.fill("{span.day|date}", p)
        self.assertEqual(text, "?")
        self.assertTrue(any("cannot use format" in p for p in problems))

    def test_malformed_slot_and_unknown_format_are_findings(self):
        for text in ("{span.commits|}", "{span.commits", "span.commits}", "{span.commits|invented}"):
            with self.subTest(text=text):
                self.assertTrue(spec.fill(text, PROFILE)[2])

    def test_numeric_formats_reject_wrong_types_and_nonfinite_values(self):
        for value, form in (("many", "n"), (None, "raw"), (math.inf, "pct"), (math.nan, "raw"), (True, "hour"), (24, "hour"), (8.5, "hour")):
            p = copy.deepcopy(PROFILE)
            p["signals"][0]["values"]["commits"] = value
            with self.subTest(value=value, form=form):
                self.assertTrue(spec.fill("{span.commits|" + form + "}", p)[2])

    def test_saved_source_is_independent_of_rendered_views(self):
        s = source()
        result = spec.render_spec(s, PROFILE)
        s["views"][0]["caption"] = "changed caller"
        result["views"][0]["caption"] = "changed output"
        self.assertEqual(result["source_spec"]["views"][0]["caption"], "{span.commits} commits")


class RecomputeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.saved = spec.render_spec(source(), PROFILE)
        self.write("profile.json", PROFILE)
        self.write("spec.rendered.json", self.saved)

    def write(self, name, value):
        (self.folder / name).write_text(json.dumps(value), encoding="utf-8")

    def test_current_rendering_passes_without_optional_self(self):
        self.assertEqual(spec.verify_rendered_spec(str(self.folder)), [])

    def test_changed_filled_caption_fails_even_with_empty_problem_list(self):
        self.saved["views"][0]["caption"] = "999999 invented commits"
        self.saved["problems"] = []
        self.write("spec.rendered.json", self.saved)
        self.assertTrue(any("differs" in p for p in spec.verify_rendered_spec(str(self.folder))))

    def test_changed_profile_makes_saved_rendering_stale(self):
        p = copy.deepcopy(PROFILE)
        p["signals"][0]["values"]["commits"] = 3
        self.write("profile.json", p)
        self.assertTrue(spec.verify_rendered_spec(str(self.folder)))

    def test_legacy_artifact_requires_regeneration(self):
        del self.saved["source_spec"]
        self.write("spec.rendered.json", self.saved)
        self.assertTrue(any("rerun the spec stage" in p for p in spec.verify_rendered_spec(str(self.folder))))

    def test_source_problem_cannot_be_hidden_by_editing_saved_problems(self):
        self.saved["source_spec"]["views"][0]["caption"] = "999999 invented commits"
        self.saved["problems"] = []
        self.write("spec.rendered.json", self.saved)
        self.assertTrue(any("digits outside a slot" in p for p in spec.verify_rendered_spec(str(self.folder))))

    def test_cli_source_path_is_build_metadata_not_rendered_content(self):
        self.saved["source"] = "specs/synthetic.json"
        self.write("spec.rendered.json", self.saved)
        self.assertEqual(spec.verify_rendered_spec(str(self.folder)), [])

    def test_malformed_inputs_become_findings(self):
        self.write("profile.json", [])
        self.assertTrue(spec.verify_rendered_spec(str(self.folder)))


if __name__ == "__main__":
    unittest.main()
