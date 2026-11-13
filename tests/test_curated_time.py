"""Explicit curated time survives ingestion without inventing lifetimes."""
import json
from pathlib import Path
import tempfile
import unittest

from x3dray.curated import import_masterclass
from x3dray.relations import build_relations, traverse


class CuratedTimeTests(unittest.TestCase):
    def imported(self, edge, exclude=()):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "data"
            folder.mkdir()
            (folder / "ecosystem.json").write_text(json.dumps({"edges": [{"id": "C1", "from": "a", "to": "b", "grade": "S", "kind": "assertion", "evidence": ["Controlled curator claim"], **edge}]}))
            return import_masterclass(temp, exclude=exclude)

    def test_explicit_interval_survives_import_and_drives_half_open_query(self):
        cur = self.imported({"observed_at": "2026-01-02T07:00:00-05:00", "valid_time": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-03T00:00:00Z"}})
        graph = build_relations({"repos": [{"name": "a"}, {"name": "b"}]}, {"pairs": []}, cur)
        self.assertEqual(graph["records"][0]["observed_at"], "2026-01-02T12:00:00Z")
        self.assertEqual(traverse(graph, "a", "b", at="2026-01-02T00:00:00Z", time_mode="valid_at")["total_paths"], 1)
        self.assertEqual(traverse(graph, "a", "b", at="2026-01-03T00:00:00Z", time_mode="valid_at")["total_paths"], 0)

    def test_first_date_never_becomes_validity(self):
        cur = self.imported({"first_date": "2026-01-01"})
        graph = build_relations({"repos": [{"name": "a"}, {"name": "b"}], "harvested_at": "2026-01-03T00:00:00Z"}, {"pairs": []}, cur)
        self.assertIsNone(graph["records"][0]["valid_time"])
        self.assertEqual(traverse(graph, "a", "b", at="2026-01-02T00:00:00Z", time_mode="valid_at")["total_paths"], 0)

    def test_invalid_public_interval_is_refused_before_publication(self):
        with self.assertRaises(ValueError):
            self.imported({"valid_time": {"start": "2026-01-03T00:00:00Z", "end": "2026-01-01T00:00:00Z"}})

    def test_excluded_private_interval_does_not_affect_import(self):
        self.assertEqual(self.imported({"valid_time": {"private": "unmeasured"}}, exclude=("b",))["ties"], [])
