"""Two independent clocks: recorded observation and explicit validity claims."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import unittest

from x3dray.locators import locator_from_blob
from x3dray.relations import build_relations, traverse
from x3dray.temporal import instant_micros, normalize_instant, normalize_time_filter, normalize_valid_time

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "x3dray" / "web" / "tools.js"


def instant(day):
    return f"2026-01-{day:02d}T00:00:00Z"


def edge(i, a, b, start=None, end=None, observed=None, *, directed=True, predicate="declares_use"):
    return {"id": f"relation:{i}", "from": a, "to": b, "predicate": predicate, "directed": directed,
            "grade": "C", "observed_at": observed, "valid_time": None if start is None else {"start": start, "end": end},
            "evidence": [{"id": f"evidence:{i}", "text": "Synthetic temporal assertion"}]}


def bundle(records, names=("alpha", "beta", "gamma", "delta")):
    return {"repos": [{"name": n} for n in names], "vocab": {}, "ties": {"machine": [], "curated": []},
            "relations": {"schema_version": 1, "snapshot_digest": "synthetic-time-fixture", "nodes": [{"id": n} for n in names],
                "records": records, "predicates": {r["predicate"]: {"directed": r["directed"]} for r in records}}}


def temporal_fixture():
    return bundle([edge(1, "alpha", "beta", instant(1), instant(3), instant(2)),
                   edge(2, "beta", "gamma", instant(3), instant(5), instant(5)),
                   edge(3, "alpha", "delta", instant(1), instant(5), instant(2)),
                   edge(4, "delta", "gamma", observed=instant(2))])


def small_oracle(graph, query):
    """Independent exhaustive simple paths + stdlib datetime on tiny fixtures."""
    neighbours = {n["id"]: set() for n in graph["nodes"]}
    point = datetime.fromisoformat(query["at"].replace("Z", "+00:00")) if "at" in query else None
    predicates = set(filter(None, query.get("predicates", "").split(",")))
    for row in graph["records"]:
        if predicates and row["predicate"] not in predicates:
            continue
        if point:
            known = row.get("observed_at") if query["time_mode"] == "observed_by" else row.get("valid_time")
            if known is None:
                if query.get("unknown_time", "exclude") != "include":
                    continue
            elif query["time_mode"] == "observed_by":
                if datetime.fromisoformat(known.replace("Z", "+00:00")) > point:
                    continue
            elif not (datetime.fromisoformat(known["start"].replace("Z", "+00:00")) <= point < datetime.fromisoformat(known["end"].replace("Z", "+00:00"))):
                continue
        a, b = row["from"], row["to"]
        if not row["directed"] or query.get("direction") == "association":
            neighbours[a].add(b)
            neighbours[b].add(a)
        elif query.get("direction") == "incoming":
            neighbours[b].add(a)
        else:
            neighbours[a].add(b)
    paths, pending = [], [[query["from"]]]
    while pending:
        path = pending.pop()
        if path[-1] == query["to"]:
            paths.append(path)
        elif len(path) - 1 < query.get("max_hops", 3):
            pending.extend(path + [n] for n in neighbours[path[-1]] if n not in path)
    depth = min(map(len, paths), default=0)
    return sorted(p for p in paths if len(p) == depth)


class TemporalNormalizationTests(unittest.TestCase):
    def test_offsets_fraction_and_epoch_are_exact(self):
        cases = {"2026-01-01T02:00:00.100000+02:00": "2026-01-01T00:00:00.1Z",
                 "2024-02-29T23:59:59.000001-00:30": "2024-03-01T00:29:59.000001Z",
                 "0001-01-01T00:00:00Z": "0001-01-01T00:00:00Z",
                 "9999-12-31T23:59:59.999999Z": "9999-12-31T23:59:59.999999Z"}
        for raw, expected in cases.items():
            self.assertEqual(normalize_instant(raw), expected)
        self.assertEqual(instant_micros("1969-12-31T23:59:59.999999Z"), -1)
        self.assertEqual(instant_micros("1970-01-01T00:00:00.000001Z"), 1)

    def test_calendar_timezone_and_shape_fail_closed(self):
        for raw in [None, 3, True, [], "2026-01-01", "2026-01-01T00:00:00", "2026-02-29T00:00:00Z",
                    "2026-01-01T24:00:00Z", "2026-01-01T00:00:60Z", "2026-01-01T00:00:00.0000001Z",
                    "2026-01-01T00:00:00+14:01", "2026-01-01T00:00:00+01:60", "2026-01-01T00:00:00+15:00",
                    "0000-01-01T00:00:00Z", "0001-01-01T00:00:00+00:01", "9999-12-31T23:59:59-00:01",
                    "٢٠٢٦-01-01T00:00:00Z", "2026-01-01T00:00:00Z\n"]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_instant(raw)

    def test_filter_and_interval_are_strict_and_canonical(self):
        self.assertIsNone(normalize_time_filter(None))
        self.assertIsNone(normalize_valid_time(None))
        expected = {"at": instant(1), "time_mode": "valid_at", "unknown_time": "exclude"}
        self.assertEqual(normalize_time_filter({"at": "2026-01-01T01:00:00+01:00", "time_mode": "valid_at"}), expected)
        for value in [{}, {"at": instant(1)}, {"time_mode": "valid_at"}, {"unknown_time": "include"},
                      expected | {"extra": "ignored?"}, expected | {"time_mode": []}, expected | {"unknown_time": None}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_time_filter(value)
        for value in [{}, {"start": instant(1), "end": None}, {"start": instant(3), "end": instant(2)},
                      {"start": instant(1), "end": instant(1)}, {"start": instant(1), "end": instant(2), "claim": "extra"}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_valid_time(value)


class TemporalProjectionTests(unittest.TestCase):
    def dataset(self, private=False):
        return {"family": "fixture", "harvested_at": instant(6), "repos": [
            {"name": "alpha", "repo_id": "github:owner/alpha", "visibility": "public", "commits": [{"t": 10}]},
            {"name": "beta", "visibility": "private" if private else "public", "commits": [{"t": 20}]}]}

    def assertion(self):
        return {"id": "E1", "from": "alpha", "to": "beta", "grade": "C", "evidence": ["A recorded curated assertion."]}

    def project(self, item=None, ds=None):
        return build_relations(ds or self.dataset(), {"pairs": []}, {"ties": [item or self.assertion()]})

    def test_snapshot_observation_does_not_infer_validity_from_commits(self):
        projected = self.project()
        row = projected["records"][0]
        self.assertEqual(row["observed_at"], instant(6))
        self.assertIsNone(row["valid_time"])
        self.assertEqual(row["evidence"][0]["observed_at"], instant(6))
        self.assertEqual(projected["coverage"]["valid_time_known_records"], 0)
        self.assertEqual(projected["nodes"][0]["repo_id"], "github:owner/alpha")
        ds = self.dataset()
        del ds["harvested_at"]
        self.assertIsNone(self.project(ds=ds)["records"][0]["observed_at"])

    def test_explicit_public_times_are_normalized_and_change_safe_digest(self):
        item = self.assertion() | {"observed_at": "2026-01-02T01:00:00+01:00", "valid_time": {"start": instant(1), "end": instant(3)}}
        projected = self.project(item)
        self.assertEqual(projected["records"][0]["observed_at"], instant(2))
        self.assertEqual(projected["records"][0]["valid_time"], item["valid_time"])
        self.assertNotEqual(self.project()["snapshot_digest"], projected["snapshot_digest"])
        self.assertEqual(self.project(item)["snapshot_digest"], projected["snapshot_digest"])
        self.assertIsNone(self.project(self.assertion() | {"observed_at": None})["records"][0]["observed_at"])

    def test_machine_assertions_honor_only_explicit_source_temporal_claims(self):
        pair = {"from": "alpha", "to": "beta", "kinds": {"uses": 1}, "observed_at": instant(2),
                "valid_time": {"start": instant(1), "end": instant(3)},
                "evidence": [{"kind": "uses", "text": "Configured use", "observed_at": instant(4), "valid_time": {"start": instant(4), "end": instant(5)}}]}
        row = build_relations(self.dataset(), {"pairs": [pair]})["records"][0]
        self.assertEqual(row["observed_at"], instant(2))
        self.assertEqual(row["valid_time"], pair["valid_time"])
        self.assertEqual(row["evidence"][0]["observed_at"], instant(2))

    def test_private_source_temporal_details_do_not_affect_projection_or_digest(self):
        ds = self.dataset(private=True)
        a = self.project(self.assertion() | {"observed_at": instant(2), "valid_time": {"start": instant(1), "end": instant(3)}}, ds)
        b = self.project(self.assertion() | {"observed_at": "withheld malformed private timestamp", "valid_time": {"secret": "canary"}}, ds)
        self.assertEqual(a, b)
        self.assertEqual(a["records"][0]["observed_at"], instant(6))
        self.assertIsNone(a["records"][0]["valid_time"])
        self.assertEqual(a["records"][0]["evidence"][0]["locator_status"], "withheld_private")

    def test_malformed_public_time_metadata_fails(self):
        for item in [self.assertion() | {"observed_at": "yesterday"}, self.assertion() | {"valid_time": {"start": instant(2), "end": instant(1)}}]:
            with self.assertRaises(ValueError):
                self.project(item)
        ds = self.dataset() | {"harvested_at": "unknown"}
        with self.assertRaises(ValueError):
            self.project(ds=ds)

    def test_safe_source_locator_roundtrip_and_private_withholding(self):
        content = b"beta declaration\n"
        oid = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        locator = locator_from_blob("github:owner/alpha", "a" * 40, "sha1", oid, "README.md", content,
                                    line_start=1, detector_id="curated")
        item = self.assertion() | {"evidence": [{"path": "README.md", "line": 1, "text": "beta declaration", "locator": locator,
                                                "locator_status": "verified", "untrusted": "ignored"}]}
        row = self.project(item)["records"][0]["evidence"][0]
        self.assertEqual(row["locator"], locator)
        self.assertEqual(row["locator_status"], "recorded")
        self.assertNotIn("untrusted", row)
        item["evidence"][0]["locator"]["repo_id"] = "github:unrelated/repo"
        self.assertEqual(self.project(item)["records"], [])
        private = self.project(item, self.dataset(private=True))
        self.assertNotIn("content_sha256", json.dumps(private))
        self.assertNotIn("README.md", json.dumps(private))

    def test_well_shaped_unrelated_locators_cannot_support_a_witness(self):
        content = b"beta declaration\nsecond line\n"
        oid = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        locator = locator_from_blob("github:owner/alpha", "a" * 40, "sha1", oid, "README.md", content,
                                    line_start=1, detector_id="curated")
        for replacement in [locator | {"path": "unrelated.md"}, locator | {"detector": {"id": "pin", "version": "1"}},
                            locator | {"repo_id": "repo:beta"},
                            locator_from_blob("github:owner/alpha", "a" * 40, "sha1", oid, "README.md", content, line_start=2, detector_id="curated")]:
            item = self.assertion() | {"evidence": [{"path": "README.md", "line": 1, "text": "beta declaration", "locator": replacement}]}
            self.assertEqual(self.project(item)["records"], [])


@unittest.skipUnless(shutil.which("node"), "Node is required for independent shared-tool parity")
class TemporalTraversalTests(unittest.TestCase):
    def js(self, data, queries):
        script = """
          import fs from 'node:fs';
          const {makeTools}=await import(process.argv[1]);
          const input=JSON.parse(fs.readFileSync(0,'utf8')), tool=makeTools(input.bundle,null).find(t=>t.name==='architecture');
          const before=JSON.stringify(input.bundle);
          const results=input.queries.map(q=>{try{return {value:tool.execute(q)}}catch(e){return {error:e.message}}});
          console.log(JSON.stringify({results,unchanged:before===JSON.stringify(input.bundle)}));
        """
        process = subprocess.run(["node", "--input-type=module", "-e", script, TOOLS.as_uri()],
                                 input=json.dumps({"bundle": data, "queries": queries}), capture_output=True, text=True, check=True, timeout=30)
        output = json.loads(process.stdout)
        self.assertTrue(output["unchanged"])
        return output["results"]

    def parity(self, data, queries):
        values = self.js(data, queries)
        for query, wrapped in zip(queries, values):
            self.assertNotIn("error", wrapped)
            actual = wrapped["value"]
            args = {k: v for k, v in query.items() if k not in {"from", "to"}}
            expected = traverse(data["relations"], query["from"], query.get("to"), **args)
            keys = ["source", "target", "direction", "predicates", "grades", "max_hops", "time_filter", "time_coverage"]
            keys += ["hops", "total_paths", "returned_paths", "omitted_paths", "paths"] if "to" in query else ["nodes", "total_nodes", "returned_nodes", "omitted_nodes", "records", "total_records", "returned_records", "omitted_records"]
            for key in keys:
                self.assertEqual(actual.get(key), expected.get(key), (query, key))
        return [row["value"] for row in values]

    def test_disjoint_intervals_cannot_form_covalid_path(self):
        data = temporal_fixture()
        queries = [{"from": "alpha", "to": "gamma"}] + [
            {"from": "alpha", "to": "gamma", "at": instant(d), "time_mode": "valid_at", "unknown_time": policy}
            for d in (1, 2, 3, 4, 5) for policy in ("exclude", "include")]
        output = self.parity(data, queries)
        self.assertEqual(output[0]["total_paths"], 2)
        self.assertNotIn("time_filter", output[0])
        for query, result in zip(queries[1:], output[1:]):
            expected = 1 if query["unknown_time"] == "include" and query["at"] < instant(5) else 0
            self.assertEqual(result["total_paths"], expected)
            if expected:
                self.assertEqual(result["paths"][0]["nodes"], ["alpha", "delta", "gamma"])
                self.assertEqual(result["paths"][0]["time_status"], "provisional_unknown_time")
        ledger = output[1]["time_coverage"]
        self.assertEqual((ledger["candidate_records"], ledger["eligible_records"], ledger["known_records"], ledger["unknown_records"]), (4, 2, 3, 1))
        self.assertEqual((ledger["excluded_known_records"], ledger["excluded_unknown_records"]), (1, 1))

    def test_observation_visibility_is_not_validity_or_knowledge_cutoff(self):
        data = temporal_fixture()
        queries = [{"from": "alpha", "to": "gamma", "at": instant(d), "time_mode": "observed_by"} for d in (1, 2, 5)]
        output = self.parity(data, queries)
        self.assertEqual([o["total_paths"] for o in output], [0, 1, 2])
        self.assertEqual(output[1]["paths"][0]["time_status"], "known_time")
        # A claim learned later may describe an earlier valid interval. valid_at
        # alone is deliberately not an "as known then" bitemporal query.
        data = bundle([edge(1, "alpha", "beta", instant(1), instant(3), instant(5))])
        output = self.parity(data, [{"from": "alpha", "to": "beta", "at": instant(2), "time_mode": mode} for mode in ("observed_by", "valid_at")])
        self.assertEqual([o["total_paths"] for o in output], [0, 1])
        self.assertIn("does not impose", output[1]["time_coverage"]["scope"])

    def test_microsecond_bounds_offsets_and_negative_epoch_match(self):
        data = bundle([edge(1, "alpha", "beta", "1969-12-31T23:59:59.999999Z", "1970-01-01T00:00:00.000001Z", "1969-12-31T23:59:59.999999Z")])
        raw = ["1970-01-01T00:59:59.999998+01:00", "1969-12-31T23:59:59.999999Z", "1970-01-01T00:00:00Z", "1970-01-01T00:00:00.000001Z"]
        output = self.parity(data, [{"from": "alpha", "to": "beta", "at": at, "time_mode": "valid_at"} for at in raw])
        self.assertEqual([o["total_paths"] for o in output], [0, 1, 1, 0])

    def test_parallel_unknown_witnesses_do_not_hide_known_support(self):
        records = [edge(i, "alpha", "beta") for i in range(8)] + [edge("known", "alpha", "beta", instant(1), instant(3), instant(2))]
        data = bundle(records)
        output = self.parity(data, [{"from": "alpha", "to": "beta", "at": instant(2), "time_mode": "valid_at", "unknown_time": "include"}])[0]
        hop = output["paths"][0]["hops"][0]
        self.assertEqual(output["total_paths"], 1)
        self.assertEqual(hop["time_status"], "known_time")
        self.assertEqual((hop["known_time_relations"], hop["unknown_time_relations"], hop["omitted_relations"]), (1, 8, 5))
        self.assertEqual(hop["relations"][0]["id"], "relation:known")

    def test_neighbour_scope_and_unknown_observation_ledger_match(self):
        data = temporal_fixture()
        data["relations"]["records"][0]["observed_at"] = None
        queries = [{"from": "alpha", "at": instant(2), "time_mode": "observed_by", "unknown_time": p, "limit": 2} for p in ("exclude", "include")]
        output = self.parity(data, queries)
        self.assertEqual(output[0]["time_coverage"]["excluded_unknown_records"], 1)
        self.assertEqual(output[1]["time_coverage"]["included_unknown_records"], 1)

    def test_temporal_result_budget_preserves_query_and_omission_counts(self):
        names = ["alpha", "gamma"] + [f"via{i}" for i in range(12)]
        rows = []
        for i, middle in enumerate(names[2:]):
            rows.extend([edge(2 * i, "alpha", middle, instant(1), instant(5), instant(2)),
                         edge(2 * i + 1, middle, "gamma", instant(1), instant(5), instant(2))])
        for row in rows:
            row["evidence"][0]["text"] = 'A synthetic witness 🙂 "quoted" ' * 100
        query = {"from": "alpha", "to": "gamma", "at": instant(2), "time_mode": "valid_at", "limit": 50}
        result = self.js(bundle(rows, names), [query])[0]["value"]
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()), 14000)
        self.assertEqual(result["total_paths"], 12)
        self.assertEqual(result["returned_paths"], len(result["paths"]))
        self.assertEqual(result["omitted_paths"], 12 - len(result["paths"]))
        self.assertLess(result["returned_paths"], 12)
        self.assertEqual(result["time_filter"]["at"], instant(2))
        self.assertEqual(result["time_coverage"]["eligible_records"], 24)

    def test_malformed_metadata_fails_even_when_filtered_out(self):
        data = temporal_fixture()
        data["relations"]["records"][0]["valid_time"] = {"start": instant(3), "end": instant(1)}
        for query in [{"from": "alpha", "to": "gamma"}, {"from": "alpha", "to": "gamma", "grades": "S"}]:
            self.assertIn("error", self.js(data, [query])[0])
            with self.assertRaises(ValueError):
                traverse(data["relations"], "alpha", "gamma", grades=query.get("grades"))

    def test_incomplete_or_unsupported_temporal_arguments_fail(self):
        bad = [{"at": instant(2)}, {"time_mode": "valid_at"}, {"unknown_time": "include"},
               {"at": "2026-02-30T00:00:00Z", "time_mode": "valid_at"},
               {"at": instant(2), "time_mode": "historical"}, {"at": instant(2), "time_mode": "valid_at", "unknown_time": "guess"}]
        rows = self.js(temporal_fixture(), [{"from": "alpha", **q} for q in bad])
        self.assertTrue(all("error" in r for r in rows))
        for query in bad:
            with self.assertRaises(ValueError):
                traverse(temporal_fixture()["relations"], "alpha", **query)

    def test_shared_normalizer_matches_python_profile(self):
        values = [{"at": raw, "time_mode": "valid_at"} for raw in [
            "0001-01-01T00:00:00Z", "9999-12-31T23:59:59.999999Z", "2024-02-29T23:59:59.123456-00:30",
            "1969-12-31T23:59:59.999999Z", "2026-01-01T00:00:00+14:00", "2026-01-01T00:00:00-14:00",
            "2026-02-29T00:00:00Z", "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-00:01",
            "2026-01-01T00:00:00Z\n", "2026-01-01T00:00:00.0000001Z"]]
        script = """import fs from 'node:fs'; const {normalizeTimeFilter}=await import(process.argv[1]);
          console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(v=>{try{return {value:normalizeTimeFilter(v)}}catch(e){return {error:true}}})));"""
        proc = subprocess.run(["node", "--input-type=module", "-e", script, TOOLS.as_uri()], input=json.dumps(values), capture_output=True, text=True, check=True, timeout=20)
        for value, actual in zip(values, json.loads(proc.stdout)):
            try:
                expected = normalize_time_filter(value)
            except ValueError:
                self.assertIn("error", actual)
            else:
                self.assertEqual(actual, {"value": expected})

    def test_small_random_graphs_match_independent_temporal_path_oracle(self):
        rng = random.Random(771)
        names = ("alpha", "beta", "gamma", "delta")
        for _ in range(7):
            rows = []
            for a in names:
                for b in names:
                    if a == b or rng.random() < .55:
                        continue
                    start = rng.choice((None, instant(1), instant(2), instant(3)))
                    rows.append(edge(len(rows), a, b, start, instant(5) if start else None,
                                     rng.choice((None, instant(1), instant(4)))))
            if not rows:
                rows.append(edge(0, "alpha", "beta"))
            data = bundle(rows)
            queries = [{"from": "alpha", "to": "gamma", "at": instant(day), "time_mode": mode,
                        "unknown_time": policy, "direction": direction, "max_hops": 3, "limit": 50}
                       for day in (2, 4) for mode in ("valid_at", "observed_by") for policy in ("exclude", "include")
                       for direction in ("outgoing", "incoming", "association")]
            output = self.parity(data, queries)
            for query, actual in zip(queries, output):
                expected = small_oracle(data["relations"], query)
                self.assertEqual([p["nodes"] for p in actual["paths"]], expected)
                self.assertEqual(actual["total_paths"], len(expected))


if __name__ == "__main__":
    unittest.main()
