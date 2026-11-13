"""Publication boundaries use synthetic assets; no family harvest is needed."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from x3dray import gates, page, publication, receipt


class PublicationBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "out"
        self.out.mkdir()
        self.data = self.root / "data"
        self.data.mkdir()
        (self.out / "index.html").write_text("<title>Fixture</title>")
        (self.out / "bundle.json").write_text(json.dumps({"repos": [], "commits": [], "files": {}}))
        (self.out / "xray.x3d").write_text("<X3D version='4.0'><Scene/></X3D>")

    def register(self, extra=()):
        publication.register(str(self.out), [publication.entry(p) for p in ("index.html", "bundle.json", "xray.x3d", *extra)])

    def test_unregistered_json_and_nested_asset_are_never_published(self):
        (self.out / "unintended.json").write_text('{"canary":"synthetic@example.invalid"}')
        (self.out / "nested").mkdir()
        (self.out / "nested/extra.js").write_text("unregistered")
        self.register()
        page.write_publish_map(str(self.out))
        published = json.loads((self.out / "publish.json").read_text())
        self.assertEqual(set(published), {"bundle.json", "xray.x3d"})
        self.assertEqual(gates.gate_privacy(str(self.out))["status"], "pass")

    def test_legacy_stage_fallback_never_discovers_vendor_or_extra_files(self):
        (self.out / "extra.json").write_text("{}")
        (self.out / "x_ite").mkdir()
        (self.out / "x_ite/extra.js").write_text("unregistered")
        page.write_publish_map(str(self.out))
        published = json.loads((self.out / "publish.json").read_text())
        self.assertEqual(set(published), {"bundle.json", "xray.x3d"})

    def test_registered_owned_assets_are_scanned_and_attested(self):
        (self.out / "notes.json").write_text('{"synthetic":"clean"}')
        self.register(("notes.json",))
        before = receipt.write(str(self.data), str(self.out), "2026-11-12T00:00:00Z")
        self.assertIn("notes.json", gates.gate_privacy(str(self.out))["scanned"])
        entries = {f["path"]: f for f in before["files"] if f["role"] == "output"}
        self.assertIn("notes.json", entries)
        self.assertNotIn("receipt.json", entries)
        (self.out / "notes.json").write_text('{"synthetic":"changed"}')
        after = receipt.write(str(self.data), str(self.out), "2026-11-12T00:00:00Z")
        self.assertNotEqual(before["digest"], after["digest"])
        self.assertEqual(after["publication"]["detached"], "receipt.json")
        self.assertEqual(set(json.loads((self.out / "publish.json").read_text())),
                         {"bundle.json", "xray.x3d", "notes.json", "receipt.json"})

    def test_registered_canary_fails_without_echoing_secret(self):
        (self.out / "notes.json").write_text('{"canary":"synthetic@example.invalid"}')
        self.register(("notes.json",))
        result = gates.gate_privacy(str(self.out))
        self.assertEqual(result["status"], "fail")
        self.assertNotIn("synthetic@example.invalid", json.dumps(result))

    def test_path_escape_and_symlink_are_refused(self):
        for rel in ("../outside.json", "/absolute.json", "a/../b.json", "a\\b.json", "C:drive.json", "%2e%2e/file.json", "file.json?other"):
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                publication.safe_path(self.out, rel)
        (self.root / "outside.json").write_text("{}")
        (self.out / "linked.json").symlink_to(self.root / "outside.json")
        with self.assertRaises(ValueError):
            self.register(("linked.json",))

    def test_registered_missing_asset_and_changed_vendor_are_refused(self):
        self.register()
        (self.out / "xray.x3d").unlink()
        with self.assertRaises(ValueError):
            page.write_publish_map(str(self.out))
        (self.out / "vendor.js").write_text("initial")
        publication.register(str(self.out), [publication.entry("index.html"), publication.entry("vendor.js", "vendored")])
        (self.out / "vendor.js").write_text("tampered")
        self.assertEqual(gates.gate_privacy(str(self.out))["status"], "fail")

    def test_missing_xite_dependency_fails_before_output_mutation(self):
        xite = self.root / "x_ite"
        xite.mkdir()
        (xite / "x_ite.min.js").write_text("/* X_ITE v16.7.0 */")
        before = {p.name for p in self.out.iterdir()}
        with self.assertRaisesRegex(ValueError, "required X_ITE asset"):
            page.build_page(str(self.data), str(self.out), str(xite), str(self.root / "missing.ttf"), {})
        self.assertEqual(before, {p.name for p in self.out.iterdir()})

    def test_page_registers_exact_dependencies_and_normalizes_font_url(self):
        xite = self.root / "dist"
        required = ["x_ite.min.js", "x_ite.css", "LICENSE.md", "assets/linetype/linetypes.png",
                    "assets/lib/mikktspace_bg.wasm", "assets/images/logo.svg", "assets/components/Text.min.js"]
        for rel in required:
            target = xite / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"/* X_ITE v16.7.0 synthetic fixture */")
        font = self.root / "arbitrary-name.ttf"
        font.write_bytes(b"synthetic font bytes")
        (self.out / "fonts").mkdir()
        (self.out / "fonts/stale.ttf").write_bytes(b"unregistered font")
        with mock.patch.object(page, "make_bundle", return_value={"meta": {}}):
            page.build_page(str(self.data), str(self.out), str(xite), str(font), {})
        selected = {a["path"] for a in publication.assets(str(self.out))}
        self.assertEqual(selected, set(publication.GENERATED) | {"x_ite/" + p for p in required} | {"fonts/UbuntuMono-R.ttf"})
        self.assertEqual((self.out / "fonts/UbuntuMono-R.ttf").read_bytes(), font.read_bytes())
        self.assertNotIn("fonts/stale.ttf", selected)
        self.assertEqual(json.loads((self.out / "bundle.json").read_text())["meta"]["x_ite"], "16.7.0")

    def test_skipped_checks_are_development_ok_but_not_release_ready(self):
        gate_names = ("gate_xsd", "gate_loom", "gate_fact", "gate_privacy", "gate_witness", "gate_scene", "gate_tools", "gate_live")
        with ExitStack() as stack:
            for name in gate_names:
                stack.enter_context(mock.patch.object(gates, name, return_value={"status": "pass", "summary": "synthetic check"}))
            stack.enter_context(mock.patch.object(gates, "gate_xsd", return_value={"status": "skip", "summary": "synthetic unavailable tool"}))
            result = gates.run_all(str(self.data), str(self.out))
        self.assertTrue(result["ok"])
        self.assertFalse(result["release_ready"])
        self.assertEqual(result["skipped"], ["xsd"])
        self.assertIn("xsd", result["required_release_gates"])

    def test_witness_gate_requires_actual_scene(self):
        (self.out / "xray.x3d").unlink()
        self.assertEqual(gates.gate_witness(str(self.out))["status"], "fail")


if __name__ == "__main__":
    unittest.main()
