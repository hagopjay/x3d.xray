"""The `eda` tool of x3dray/web/tools.js: topics, limits, privacy, wire size and the page/MCP surface.

Node runs the real tools over bundles built from tests/eda_support.py datasets and the real eda stage, so the tool reads the
same JSON shape it reads in a build. Nothing here needs a git checkout or the network.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "x3dray" / "web"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eda_support import minimal_bundle, synthetic_dataset  # noqa: E402
from x3dray import eda  # noqa: E402

TOOLS_URI = (WEB / "tools.js").as_uri()
HAS_NODE = shutil.which("node")
SCENE = "{ show() {}, highlight(q) { return { lit: 0 }; }, note() {}, colorBy() {} }"
ROUTES = ("lookup", "compute")
TOPICS = ["ledger", "finding", "audit", "profile", "flags", "rhythm", "concentration", "slice", "method", "recompute", "compare"]


def run_tools(bundle, calls, scene=False, timeout=60):
    """Execute [name, input] calls against makeTools(bundle); returns ({value}|{error}) per call and whether the bundle changed."""
    script = f"""
      const T = await import(process.argv[1]);
      const input = JSON.parse(await new Promise(r => {{ let s = ''; process.stdin.on('data', d => {{ s += d; }}); process.stdin.on('end', () => r(s)); }}));
      const B = input.bundle, before = JSON.stringify(B);
      const tools = Object.fromEntries(T.makeTools(B, {SCENE if scene else 'null'}).map(t => [t.name, t]));
      const results = input.calls.map(([name, args]) => {{
        try {{ return {{ value: tools[name].execute(args) }}; }} catch (e) {{ return {{ error: e.message }}; }}
      }});
      console.log(JSON.stringify({{ results, unchanged: JSON.stringify(B) === before }}));"""
    run = subprocess.run(["node", "--input-type=module", "-e", script, TOOLS_URI], input=json.dumps({"bundle": bundle, "calls": calls}),
                         capture_output=True, text=True, timeout=timeout)
    if run.returncode != 0:
        raise AssertionError(run.stderr[-2000:])
    out = json.loads(run.stdout)
    return out["results"], out["unchanged"]


def nbytes(v) -> int:
    return len(json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def check_counters(tc: unittest.TestCase, result: dict):
    """Every X_total has a list X at top level whose length plus X_omitted is the total; the bulk keys never appear."""
    tc.assertNotIn("truncated", result)
    tc.assertNotIn("_result_limit", result)
    for key, total in result.items():
        if key.endswith("_total") and isinstance(total, int) and isinstance(result.get(key[:-6]), list):
            name = key[:-6]
            tc.assertEqual(len(result[name]) + result.get(name + "_omitted", 0), total, name)


@unittest.skipUnless(HAS_NODE, "Node is required for the eda tool tests")
class Fixtures(unittest.TestCase):
    _cache: dict = {}

    @classmethod
    def small(cls):
        if "small" not in cls._cache:
            ds = synthetic_dataset(seed=5, repos=3, per_repo=60, private=("r2",), plant=("outlier", "ai_hours"))
            e, problems = eda.run(ds)
            assert problems == [], problems
            cls._cache["small"] = (ds, e)
        ds, e = cls._cache["small"]
        return ds, copy.deepcopy(e)

    @classmethod
    def big(cls):
        """40 repos, two of them private, and 500 flagged commits in the flag lists (the lists are rewritten, not recomputed)."""
        if "big" not in cls._cache:
            ds = synthetic_dataset(seed=9, repos=40, per_repo=25, private=("r3", "r4"))
            e, problems = eda.run(ds)
            assert problems == [], problems
            n = sum(len(r["commits"]) for r in ds["repos"])
            by = e["flags"]["by_rule"]
            by["size_high"].update(C=[[i, 4, 4] for i in range(0, 20)], L=[[i, 3, 4] for i in range(20, 120)], S=[[i, 2, 4] for i in range(120, 320)])
            by["gap_long"].update(C=[], L=[[i, 3, 4] for i in range(320, 420)], S=[[i, 2, 4] for i in range(420, 500)])
            for rule in by.values():
                rule["counts"].update({g: len(rule[g]) for g in "CLS"})
            assert n > 500
            cls._cache["big"] = (ds, e)
        ds, e = cls._cache["big"]
        return ds, copy.deepcopy(e)

    def bundle(self):
        ds, e = self.small()
        return minimal_bundle(ds, e)


@unittest.skipUnless(HAS_NODE, "Node is required for the eda tool tests")
class Topics(Fixtures):
    def test_construction_reads_nothing_and_every_topic_answers(self):
        res, unchanged = run_tools({}, [])
        self.assertTrue(unchanged)
        b = self.bundle()
        calls = [["eda", {"topic": "ledger"}], ["eda", {"topic": "finding", "id": "contrast:lines"}], ["eda", {"topic": "audit"}],
                 ["eda", {"topic": "audit", "grade": "L"}], ["eda", {"topic": "profile"}], ["eda", {"topic": "profile", "repo": "r0", "measure": "gap_s"}],
                 ["eda", {"topic": "profile", "who": "ai", "fork": "all"}], ["eda", {"topic": "flags"}],
                 ["eda", {"topic": "flags", "rule": "size_high", "grade": "S"}], ["eda", {"topic": "rhythm"}], ["eda", {"topic": "rhythm", "repo": "r1"}],
                 ["eda", {"topic": "concentration"}], ["eda", {"topic": "concentration", "repo": "r2"}], ["eda", {"topic": "method"}],
                 ["eda", {"topic": "slice"}], ["eda", {"topic": "slice", "who": "you", "measure": "gap_s", "fork": "all"}],
                 ["eda", {"topic": "recompute"}], ["eda", {"topic": "compare", "a": {"who": "ai"}, "b": {"who": "you"}}]]
        res, unchanged = run_tools(b, calls)
        self.assertTrue(unchanged, "a tool changed the bundle")
        for call, r in zip(calls, res):
            self.assertIn("value", r, (call, r))
            v = r["value"]
            self.assertIn(v["route"], ROUTES, call)
            self.assertIsInstance(v["summary"], str, call)
            self.assertTrue(v["summary"], call)
            self.assertLessEqual(nbytes(v), 30000, call)
            check_counters(self, v)
        self.assertEqual({c[1]["topic"] for c in calls}, set(TOPICS))

    def test_ledger_has_twelve_rows_the_wording_and_no_stray_numbers(self):
        b = self.bundle()
        (r,), _ = run_tools(b, [["eda", {"topic": "ledger"}]])
        v = r["value"]
        self.assertEqual(len(v["findings"]), 12)
        self.assertEqual(v["findings_total"], 12)
        self.assertEqual(v["wording"], "a grade here is robustness over declared checks, never a probability")
        self.assertEqual(v["ledger"], b["eda"]["looks"]["ledger"])
        keys = ["id", "kind", "measure", "grade", "grade_reason", "effect_permille", "direction", "n_a", "n_b", "p_text", "q_text", "text"]
        for row, look in zip(v["findings"], b["eda"]["looks"]["findings"]):
            self.assertEqual(list(row), keys)
            self.assertEqual(row["id"], look["id"])
            self.assertEqual(row["grade"], look["grade"])
            self.assertEqual(row["effect_permille"], look["effect"]["permille"])
        labels = b["eda"]["classes"]["labels"]
        self.assertEqual(v["pair"], [labels[c] for c in b["eda"]["classes"]["pair"]])

    def test_ledger_with_a_look_that_did_not_run(self):
        b = self.bundle()
        look = b["eda"]["looks"]["findings"][0]
        look.update(status="not_run", reason="too few", grade=None, grade_reason="not_run", effect=None, null_model=None, q=None, q_text=None,
                    checks=None, say_at_most=None)
        (r,), _ = run_tools(b, [["eda", {"topic": "ledger"}]])
        row = r["value"]["findings"][0]
        self.assertIsNone(row["effect_permille"])
        self.assertIsNone(row["p_text"])
        self.assertEqual(row["grade_reason"], "not_run")

    def test_finding_returns_the_stored_look_and_refuses_an_unknown_id(self):
        b = self.bundle()
        look = b["eda"]["looks"]["findings"][3]
        res, _ = run_tools(b, [["eda", {"topic": "finding", "id": look["id"]}], ["eda", {"topic": "finding", "id": "contrast:nothing"}],
                               ["eda", {"topic": "finding"}]])
        self.assertEqual(res[0]["value"]["finding"], look)
        self.assertEqual(res[0]["value"]["grade_rule"], b["eda"]["looks"]["grade_rule"])
        for bad in res[1:]:
            self.assertRegex(bad["error"], r"known: contrast:lines, contrast:files")
            self.assertIn("drift:second:weekday", bad["error"])

    def test_audit_lists_checks_limitations_and_private_coverage_as_counts(self):
        b = self.bundle()
        # A stored row for a private repo that holds more than counts must still leave as counts.
        for row in b["eda"]["audit"]["coverage"]:
            if row["visibility"] == "private":
                row.update(n_with_paths=7, n_usable=7, history_scope="planted-scope-secret")
        res, _ = run_tools(b, [["eda", {"topic": "audit"}], ["eda", {"topic": "audit", "grade": "C"}], ["eda", {"topic": "audit", "grade": "S"}]])
        v = res[0]["value"]
        self.assertEqual([c["id"] for c in v["checks"]], [c["id"] for c in b["eda"]["audit"]["checks"]])
        self.assertEqual(v["limitations"], b["eda"]["audit"]["limitations"])
        priv = [r for r in v["coverage"] if r["visibility"] == "private"]
        self.assertTrue(priv)
        for r in priv:
            self.assertNotIn("n_with_paths", r)
            self.assertNotIn("n_usable", r)
            self.assertNotIn("history_scope", r)
            self.assertIn("unknown", r)
        self.assertNotIn("planted-scope-secret", json.dumps(res[0]))
        rank = {"C": 3, "L": 2, "S": 1}
        for r, least in ((res[1], 3), (res[2], 1)):
            for c in r["value"]["checks"]:
                self.assertEqual(c["status"], "flag")
                self.assertGreaterEqual(rank[c["grade"]], least)
        self.assertLessEqual(len(res[1]["value"]["checks"]), len(res[2]["value"]["checks"]))

    def test_profile_filters_by_repo_who_fork_and_measure(self):
        b = self.bundle()
        recs = b["eda"]["profile"]["records"]
        res, _ = run_tools(b, [["eda", {"topic": "profile", "repo": "r0", "limit": 40}], ["eda", {"topic": "profile", "who": "you", "limit": 40}],
                               ["eda", {"topic": "profile", "fork": "no_mass", "measure": "files", "limit": 40}],
                               ["eda", {"topic": "profile", "repo": "r0", "who": "you"}], ["eda", {"topic": "profile", "who": "you,ai"}],
                               ["eda", {"topic": "profile", "repo": "nowhere"}]])
        self.assertEqual(res[0]["value"]["records"], [r for r in recs if r["slice"] == "repo:r0"])
        self.assertEqual(res[1]["value"]["records"], [r for r in recs if r["slice"] == "class:you"])
        self.assertEqual(res[2]["value"]["records"], [r for r in recs if r["fork"] == "no_mass" and r["measure"] == "files"])
        self.assertRegex(res[3]["error"], r"repo or who, not both")
        self.assertRegex(res[4]["error"], r"one author class")
        self.assertRegex(res[5]["error"], r"^not in vocabulary")
        self.assertIn("measure_defs", res[0]["value"])

    def test_unknown_repos_are_refused_by_every_topic_that_takes_one(self):
        b = self.bundle()
        topics = ["profile", "rhythm", "concentration", "slice"]
        res, _ = run_tools(b, [["eda", {"topic": t, "repo": "x3d.nothing"}] for t in topics])
        for r in res:
            self.assertRegex(r["error"], r"^not in vocabulary")
            self.assertIn("Known repos: r0, r1, r2", r["error"])

    def test_flags_are_ordered_by_grade_then_index_and_filtered(self):
        ds, e = self.big()
        b = minimal_bundle(ds, e)
        res, unchanged = run_tools(b, [["eda", {"topic": "flags", "limit": 40}], ["eda", {"topic": "flags", "rule": "gap_long", "limit": 40}],
                                       ["eda", {"topic": "flags", "grade": "C", "limit": 40}], ["eda", {"topic": "flags", "rule": "nope"}],
                                       ["eda", {"topic": "flags", "rule": "size_high", "grade": "L", "limit": 1}]])
        self.assertTrue(unchanged)
        allv, gap, top, bad, one = (r.get("value") for r in res)
        self.assertEqual(allv["flags_total"], 500)
        self.assertEqual(len(allv["flags"]) + allv["flags_omitted"], 500)
        self.assertEqual(len(allv["flags"]), 40)
        rank = {"C": 3, "L": 2, "S": 1}
        keys = [(-rank[r["grade"]], r["index"]) for r in allv["flags"]]
        self.assertEqual(keys, sorted(keys))
        self.assertTrue(all(r["rule"] == "size_high" and r["grade"] == "C" for r in allv["flags"][:20]))
        self.assertEqual(gap["flags_total"], 180)
        self.assertTrue(all(r["rule"] == "gap_long" for r in gap["flags"]))
        self.assertEqual(top["flags_total"], 20)
        self.assertEqual(len(top["flags"]), 20)
        self.assertEqual(one["flags_total"], 120)
        self.assertEqual(len(one["flags"]), 1)
        self.assertIn("must be one of", res[3]["error"])
        self.assertIn("at most 200 per rule and grade", allv["note"])
        self.assertEqual(allv["rules"]["size_high"]["counts"], e["flags"]["by_rule"]["size_high"]["counts"])
        self.assertEqual(allv["rules"]["size_high"]["cap"], 200)
        row = allv["flags"][0]
        commit = b["commits"][row["index"]]
        self.assertEqual((row["repo"], row["lines"], row["fired_in"], row["of"]), (commit["r"], commit["a"] + commit["d"], 4, 4))

    def test_flag_rows_never_carry_private_identifiers(self):
        ds, e = self.small()
        b = minimal_bundle(ds, e)
        private_idx = [c["i"] for c in b["commits"] if c["r"] == "r2"][:3]
        public_idx = [c["i"] for c in b["commits"] if c["r"] == "r0"][:2]
        for i in private_idx:
            b["commits"][i].update(h="deadbeefcafe", sub="PRIVATE SUBJECT", s=["s-private0001"])
        rule = b["eda"]["flags"]["by_rule"]["size_high"]
        rule["C"], rule["L"] = [], []
        rule["S"] = [[i, 2, 4] for i in sorted(private_idx + public_idx)]
        (r,), _ = run_tools(b, [["eda", {"topic": "flags", "rule": "size_high", "limit": 40}]])
        text = json.dumps(r["value"])
        for secret in ("deadbeefcafe", "PRIVATE SUBJECT", "s-private0001"):
            self.assertNotIn(secret, text)
        rows = {x["index"]: x for x in r["value"]["flags"]}
        self.assertEqual(sorted(rows), sorted(private_idx + public_idx))
        self.assertTrue(all(rows[i].get("private") is True for i in private_idx))
        self.assertTrue(all(not rows[i].get("private") for i in public_idx))
        self.assertTrue(all("sha" in rows[i] for i in public_idx))

    def test_rhythm_and_concentration_pass_the_stored_rows_and_keep_unknown(self):
        b = self.bundle()
        res, _ = run_tools(b, [["eda", {"topic": "rhythm", "who": "ai"}], ["eda", {"topic": "rhythm", "repo": "r1"}],
                               ["eda", {"topic": "concentration"}], ["eda", {"topic": "concentration", "repo": "r2"}]])
        self.assertEqual(res[0]["value"]["slices"], [s for s in b["eda"]["rhythm"]["slices"] if s["slice"] == "class:ai"])
        self.assertEqual(res[1]["value"]["slices"], [s for s in b["eda"]["rhythm"]["slices"] if s["slice"] == "repo:r1"])
        self.assertEqual(res[2]["value"]["repos"], b["eda"]["concentration"]["repos"])
        (private,) = res[3]["value"]["repos"]
        self.assertTrue(private["unknown"])
        self.assertIsNone(private["gini_permille"])
        self.assertIn("never read it as zero", res[3]["value"]["note"])

    def test_method_names_thresholds_and_code_hashes(self):
        b = self.bundle()
        (r,), _ = run_tools(b, [["eda", {"topic": "method"}]])
        v = r["value"]
        self.assertEqual(v["thresholds"], b["eda"]["method"]["thresholds"])
        self.assertEqual(v["code_sha256"], b["eda"]["method"]["code_sha256"])
        self.assertEqual(v["forks"], b["eda"]["method"]["forks"])
        self.assertEqual(v["grade_rule"], b["eda"]["looks"]["grade_rule"])
        self.assertEqual([x["id"] for x in v["limitations"]], [x["id"] for x in b["eda"]["audit"]["limitations"]])


@unittest.skipUnless(HAS_NODE, "Node is required for the eda tool tests")
class Slices(Fixtures):
    def test_slices_are_ungraded_and_the_ask_counter_counts_distinct_requests(self):
        b = self.bundle()
        script_calls = [["eda", {"topic": "slice"}], ["eda", {"topic": "slice"}], ["eda", {"topic": "slice", "repo": "r0"}],
                        ["eda", {"topic": "slice", "repo": " R0 "}], ["eda", {"topic": "slice", "who": "ai"}],
                        ["eda", {"topic": "slice", "who": "ai", "measure": "files"}], ["eda", {"topic": "slice", "who": "claude"}],
                        ["eda", {"topic": "profile"}], ["eda", {"topic": "slice", "fork": "nope"}]]
        res, _ = run_tools(b, script_calls)
        asked = [r["value"]["looks_asked_this_session"] for r in res[:7]]
        self.assertEqual(asked, [1, 1, 2, 2, 3, 4, 4])      # ' R0 ' is r0 and 'claude' is ai: the same request again
        for r in res[:7]:
            v = r["value"]
            self.assertIsNone(v["grade"])
            self.assertEqual(v["declared_looks"], 12)
            self.assertIn("ungraded", v["grade_note"])
            self.assertEqual(v["computed_by"], "edaSummarize (twin of x3dray/stats.py summarize)")
            self.assertEqual(v["route"], "compute")
        self.assertIn("must be one of", res[8]["error"])      # the schema refuses it before the tool does
        # A new tool set starts again from one.
        (again,), _ = run_tools(b, [["eda", {"topic": "slice", "who": "ai"}]])
        self.assertEqual(again["value"]["looks_asked_this_session"], 1)

    def test_a_malformed_date_is_refused_never_read_as_an_empty_slice(self):
        b = self.bundle()
        bad = ["garbage", "2020-13-45", "2020-1-1", "2017-02-30", "2017-07-14T00:00", "0050-01-01", "20170714", "2017/07/14"]
        calls = []
        for key in ("from", "to", "day"):
            for v in bad:
                calls += [["eda", {"topic": "slice", key: v}], ["query_commits", {key: v}], ["activity", {key: v}]]
        calls += [["eda", {"topic": "slice", "who": "ai"}]]
        res, _ = run_tools(b, calls)
        for call, r in zip(calls[:-1], res[:-1]):
            msg = r.get("error", "")
            self.assertRegex(msg, r"^(from|to|day) must be a calendar date written YYYY-MM-DD \(UTC\)$", call)
        # a refused request is not a look: the next slice is the first one asked
        self.assertEqual(res[-1]["value"]["looks_asked_this_session"], 1)
        ok, _ = run_tools(b, [["eda", {"topic": "slice", "from": "2017-07-01", "to": "2017-12-31"}],
                              ["query_commits", {"day": "2017-07-14"}], ["activity", {"from": "2016-02-29"}]])
        self.assertTrue(all("value" in r for r in ok), ok)
        self.assertGreater(ok[0]["value"]["matched"], 0)

    def test_filtered_row_tools_join_the_looks_asked_counter(self):
        b = self.bundle()
        calls = [
            ["activity", {"hour": 3, "min_lines": 10}], ["query_commits", {"hour": 3, "weekday": "mon"}],
            ["query_commits", {"weekday": 0, "hour": 3}],              # the same look in another spelling
            ["highlight", {"hour": 5}], ["highlight", {"clear": True}],
            ["activity", {"repo": "r0"}], ["query_commits", {"day": "2017-07-14"}],      # plain lookups are not looks
            ["query_commits", {"flag": "nope"}], ["query_commits", {"hour": 99}],        # refused requests are not looks
            ["eda", {"topic": "ledger"}],
            ["eda", {"topic": "slice", "hour": 3}], ["eda", {"topic": "ledger"}],
        ]
        res, _ = run_tools(b, calls, scene=True)
        self.assertIn("error", res[7])
        self.assertIn("error", res[8])
        self.assertEqual(res[9]["value"]["looks_asked_this_session"], 3)
        self.assertIn("query_commits", res[9]["value"]["looks_asked_note"])
        self.assertEqual(res[10]["value"]["looks_asked_this_session"], 4)
        self.assertEqual(res[11]["value"]["looks_asked_this_session"], 4)
        self.assertEqual(res[9]["value"]["ledger"]["declared"], 12)

    def test_a_slice_matches_a_python_recomputation(self):
        ds, e = self.small()
        b = minimal_bundle(ds, e)
        from x3dray import stats
        (r,), _ = run_tools(b, [["eda", {"topic": "slice", "who": "you", "measure": "lines", "fork": "no_merge", "from": "2017-08-01"}]])
        v = r["value"]
        rows = [c for c in b["commits"] if c["c"] == "you" and not c["m"] and __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime(c["t"])) >= "2017-08-01"]
        self.assertEqual(v["members"], len(rows))
        self.assertEqual(v["statistics"], json.loads(json.dumps(stats.summarize([c["a"] + c["d"] for c in rows], 16))))
        self.assertEqual(v["filter"], {"who": ["you"], "from": "2017-08-01"})

    def test_a_slice_over_a_flag_keeps_the_flagged_commits(self):
        ds, e = self.small()
        b = minimal_bundle(ds, e)
        listed = {i for g in "CLS" for i, _k, _m in e["flags"]["by_rule"]["size_high"][g]}
        (r, bad), _ = run_tools(b, [["eda", {"topic": "slice", "rule": "size_high", "fork": "all"}], ["eda", {"topic": "slice", "grade": "C"}]])
        self.assertEqual(r["value"]["matched"], len(listed))
        self.assertIn("grade needs a rule", bad["error"])

    def test_a_slice_takes_the_hour_weekday_and_size_filters_and_counts_a_spelling_once(self):
        import time
        ds, e = self.small()
        b = minimal_bundle(ds, e)
        rows = [c for c in b["commits"] if not c["m"]]
        wd = lambda c: time.gmtime(c["t"]).tm_wday      # noqa: E731
        hr = lambda c: time.gmtime(c["t"]).tm_hour      # noqa: E731
        big = [c for c in rows if c["a"] + c["d"] >= 30]
        calls = [["eda", {"topic": "slice", "hour": 10}], ["eda", {"topic": "slice", "weekday": "mon"}], ["eda", {"topic": "slice", "weekday": "0"}],
                 ["eda", {"topic": "slice", "min_lines": 30}], ["eda", {"topic": "slice", "min_lines": 10, "max_lines": 30}],
                 ["eda", {"topic": "slice", "hour": 99}], ["eda", {"topic": "slice", "weekday": "someday"}]]
        res, _ = run_tools(b, calls)
        h, mon, zero, mn, both = (r["value"] for r in res[:5])
        self.assertEqual(h["matched"], sum(1 for c in b["commits"] if hr(c) == 10))
        self.assertEqual(h["filter"], {"hour": 10})
        self.assertEqual(mon["matched"], sum(1 for c in b["commits"] if wd(c) == 0))
        self.assertEqual(mon["filter"], {"weekday": 0})
        self.assertEqual(zero["filter"], {"weekday": 0})
        self.assertEqual(zero["looks_asked_this_session"], mon["looks_asked_this_session"], "two spellings of one slice count once")
        self.assertEqual(mn["matched"], sum(1 for c in b["commits"] if c["a"] + c["d"] >= 30))
        self.assertEqual(mn["members"], len(big))
        self.assertEqual(both["matched"], sum(1 for c in b["commits"] if 10 <= c["a"] + c["d"] <= 30))
        self.assertEqual(both["filter"], {"min_lines": 10, "max_lines": 30})
        self.assertIn("hour must be", res[5]["error"])
        self.assertIn("weekday must be", res[6]["error"])

    def test_a_flag_slice_or_filter_over_a_capped_list_is_refused_not_undercounted(self):
        from unittest import mock
        ds = synthetic_dataset(seed=5, repos=3, per_repo=300, plant=("outlier",))
        e, problems = eda.run(ds)
        self.assertEqual(problems, [])
        rows = eda.indexed_rows(ds)
        with mock.patch.object(eda, "FLAG_CAP", 2):
            e["flags"] = json.loads(json.dumps(eda.flags(rows)))
        by = e["flags"]["by_rule"]["size_high"]
        self.assertGreater(sum(by["omitted"].values()), 0, "the fixture must have a flag list the cap cuts")
        b = minimal_bundle(ds, e)
        calls = [["query_commits", {"flag": "size_high"}], ["activity", {"flag": "size_high"}],
                 ["eda", {"topic": "slice", "rule": "size_high"}], ["eda", {"topic": "slice", "rule": "size_high", "grade": "S"}]]
        res, _ = run_tools(b, calls)
        for call, r in zip(calls, res):
            self.assertIn("capped", r.get("error", ""), (call, r))
            self.assertRegex(r["error"], r"would miss \d+ of \d+ grade")
        # a grade whose list is complete still filters, and its count is the stored one
        whole = [g for g in "CLS" if all(by["omitted"][h] == 0 for h in "CLS" if "CLS".index(h) <= "CLS".index(g))]
        for g in whole:
            (r,), _ = run_tools(b, [["query_commits", {"flag": "size_high", "flag_grade": g}]])
            want = sum(by["counts"][h] for h in "CLS" if "CLS".index(h) <= "CLS".index(g))
            self.assertEqual(r["value"]["matched"], want, g)
        self.assertTrue(whole, "the fixture must leave at least one grade listed in full")

    def test_a_topic_refuses_arguments_it_does_not_use(self):
        b = self.bundle()
        cases = [("profile", {"repo": "r0", "from": "2099-01-01"}, "from"), ("profile", {"day": "2000-01-01", "rule": "size_high", "grade": "C"}, "day, rule, grade"),
                 ("ledger", {"repo": "r0"}, "repo"), ("concentration", {"who": "ai", "fork": "all"}, "who, fork"),
                 ("rhythm", {"repo": "r0", "fork": "all", "measure": "gap_s"}, "fork, measure"), ("flags", {"repo": "r0"}, "repo"),
                 ("audit", {"who": "ai"}, "who"), ("method", {"rule": "gap_long"}, "rule"), ("finding", {"id": "contrast:lines", "grade": "C"}, "grade")]
        res, _ = run_tools(b, [["eda", {"topic": t, **a}] for t, a, _w in cases])
        for (t, a, want), r in zip(cases, res):
            self.assertIn(f"topic {t} does not use {want}", r.get("error", ""), (t, a, r))
        # what a topic does take, an empty value, and the page size still go through
        ok, _ = run_tools(b, [["eda", {"topic": "profile", "repo": "r0", "fork": "all", "measure": "lines", "limit": 5}],
                              ["eda", {"topic": "profile", "repo": "r0", "from": ""}], ["eda", {"topic": "method", "limit": 3}],
                              ["eda", {"topic": "slice", "who": "ai", "from": "2017-01-01", "day": "2017-08-01", "session": "", "rule": "size_high", "fork": "all"}]])
        for r in ok:
            self.assertIn("value", r, r)

    def test_private_only_slices_below_twenty_are_counts_only(self):
        ds = synthetic_dataset(seed=3, repos=3, per_repo=15, private=("r2",))
        e, problems = eda.run(ds)
        self.assertEqual(problems, [])
        b = minimal_bundle(ds, e)
        # Plant identifiers the data layer would never keep, to show no path of the tool prints them.
        for c in b["commits"]:
            if c["r"] == "r2":
                c.update(h="feedfacec0de", sub="SECRET SUBJECT PATH src/secret.py")
        res, _ = run_tools(b, [["eda", {"topic": "slice", "repo": "r2"}], ["eda", {"topic": "slice", "repo": "r2", "fork": "all"}],
                               ["eda", {"topic": "slice", "repo": "r0"}], ["eda", {"topic": "slice", "repo": "r0,r2", "fork": "all"}],
                               ["eda", {"topic": "slice", "repo": "r2", "from": "2099-01-01"}]])
        counts, counts_all, public, mixed, empty = (r["value"] for r in res)
        for v in (counts, counts_all):
            self.assertEqual(v["summary"], "private: counts only")
            self.assertIsNone(v["statistics"])
            self.assertGreater(v["members"], 0)
            self.assertLess(v["members"], 20)
        self.assertIsNotNone(public["statistics"])
        self.assertEqual(public["statistics"]["n"], public["members"])
        self.assertIsNotNone(mixed["statistics"], "a slice that includes public commits is described")
        self.assertEqual(empty["matched"], 0)
        self.assertEqual(empty["statistics"], {"n": 0})
        text = json.dumps(res)
        for secret in ("feedfacec0de", "SECRET SUBJECT", "src/secret.py"):
            self.assertNotIn(secret, text)

    def test_private_slices_with_twenty_or_more_members_are_described(self):
        ds, e = self.small()
        b = minimal_bundle(ds, e)
        (r,), _ = run_tools(b, [["eda", {"topic": "slice", "repo": "r2"}]])
        self.assertIsNotNone(r["value"]["statistics"])
        self.assertGreaterEqual(r["value"]["members"], 20)

    def test_a_slice_over_fifty_thousand_commits_is_refused(self):
        row = {"i": 0, "r": "r0", "t": 1500000000, "c": "you", "w": "A", "a": 1, "d": 0, "f": 1, "m": 0, "b": 1, "tr": 0}
        script = """
          const T = await import(process.argv[1]);
          const rows = Array.from({ length: 50001 }, (_, i) => ({ ...JSON.parse(process.argv[2]), i, t: 1500000000 + i }));
          const B = { repos: [{ name: 'r0', visibility: 'public' }], vocab: {}, commits: rows, eda: { looks: { ledger: { declared: 12 } } } };
          const tool = T.makeTools(B).find(t => t.name === 'eda');
          const out = {};
          try { tool.execute({ topic: 'slice' }); out.refused = false; } catch (e) { out.message = e.message; out.refused = true; }
          rows.length = 50000;
          out.ok = tool.execute({ topic: 'slice' }).members;
          console.log(JSON.stringify(out));"""
        run = subprocess.run(["node", "--input-type=module", "-e", script, TOOLS_URI, json.dumps(row)], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertTrue(out["refused"])
        self.assertIn("at most 50000", out["message"])
        self.assertEqual(out["ok"], 50000)


@unittest.skipUnless(HAS_NODE, "Node is required for the eda tool tests")
class Limits(Fixtures):
    def test_results_stay_under_the_cap_with_counters_that_add_up(self):
        ds, e = self.big()
        b = minimal_bundle(ds, e)
        calls = [["eda", {"topic": t, "limit": 40}] for t in ("ledger", "audit", "profile", "flags", "rhythm", "concentration", "method")]
        calls += [["eda", {"topic": "slice", "measure": m, "fork": f}] for m in ("lines", "files", "gap_s") for f in ("all", "no_mass")]
        calls += [["eda", {"topic": "profile", "limit": n}] for n in (1, 5, 40, 400)] + [["eda", {"topic": "rhythm", "limit": 0}]]
        res, unchanged = run_tools(b, calls)
        self.assertTrue(unchanged)
        for call, r in zip(calls, res):
            self.assertIn("value", r, call)
            self.assertLessEqual(nbytes(r["value"]), 30000, call)
            check_counters(self, r["value"])
        by_call = {json.dumps(c[1], sort_keys=True): r["value"] for c, r in zip(calls, res)}
        profile = by_call[json.dumps({"topic": "profile", "limit": 40}, sort_keys=True)]
        self.assertGreater(profile["records_total"], 40)
        self.assertLessEqual(len(profile["records"]), 40)
        self.assertEqual(len(by_call[json.dumps({"topic": "profile", "limit": 1}, sort_keys=True)]["records"]), 1)
        self.assertLessEqual(len(by_call[json.dumps({"topic": "profile", "limit": 400}, sort_keys=True)]["records"]), 40)
        self.assertEqual(len(by_call[json.dumps({"topic": "rhythm", "limit": 0}, sort_keys=True)]["slices"]), 1)
        rhythm = by_call[json.dumps({"topic": "rhythm", "limit": 40}, sort_keys=True)]
        self.assertEqual(rhythm["slices_total"], len(e["rhythm"]["slices"]))
        flags = by_call[json.dumps({"topic": "flags", "limit": 40}, sort_keys=True)]
        self.assertEqual((flags["flags_total"], len(flags["flags"]), flags["flags_omitted"]), (500, 40, 460))
        conc = by_call[json.dumps({"topic": "concentration", "limit": 40}, sort_keys=True)]
        self.assertEqual(conc["repos_total"], 40)
        self.assertEqual(len(conc["repos"]), 40)

    def test_a_result_that_would_pass_the_budget_loses_whole_rows_not_counters(self):
        ds, e = self.big()
        b = minimal_bundle(ds, e)
        for rec in b["eda"]["profile"]["records"]:
            rec["reason"] = "x" * 1500
        (r,), _ = run_tools(b, [["eda", {"topic": "profile", "limit": 40}]])
        v = r["value"]
        self.assertLessEqual(nbytes(v), 30000)
        self.assertLess(len(v["records"]), 40)
        self.assertEqual(len(v["records"]) + v["records_omitted"], v["records_total"])
        self.assertNotIn("truncated", v)

    def test_every_page_tool_fits_the_sample_runtime_definition_limits(self):
        script = f"""
          const T = await import(process.argv[1]);
          const tools = T.makeTools({{}}, {SCENE});
          console.log(JSON.stringify(tools.map(t => [t.name, new TextEncoder().encode(t.description).length, new TextEncoder().encode(JSON.stringify(t.inputSchema)).length, t.inputSchema])));"""
        run = subprocess.run(["node", "--input-type=module", "-e", script, TOOLS_URI], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        rows = json.loads(run.stdout)
        names = [r[0] for r in rows]
        self.assertEqual(len(names), 15)
        self.assertLessEqual(len(names), 16)
        self.assertIn("eda", names)
        self.assertLess(names.index("eda"), names.index("show"), "eda must be a shared tool, registered above the scene tools")
        for name, d, s, _schema in rows:
            self.assertLessEqual(d, 1024, name)
            self.assertLessEqual(s, 4096, name)
        schema = next(r[3] for r in rows if r[0] == "eda")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["required"], ["topic"])
        self.assertEqual(schema["properties"]["topic"]["enum"], TOPICS)
        for tool in ("query_commits", "activity", "highlight"):
            props = next(r[3] for r in rows if r[0] == tool)["properties"]
            for key in ("min_lines", "max_lines", "hour", "weekday", "flag", "flag_grade", "side_branch"):
                self.assertIn(key, props, (tool, key))

    def test_validation_rejects_stray_keys_and_bad_values(self):
        b = self.bundle()
        bad = [{"topic": "ledger", "extra": 1}, {"topic": "nope"}, {}, {"topic": "slice", "fork": "sideways"}, {"topic": "slice", "measure": "bytes"},
               {"topic": "flags", "rule": "size_low"}, {"topic": "audit", "grade": "X"}, {"topic": "profile", "limit": "5"},
               {"topic": "profile", "limit": 1.5}, {"topic": 3}, {"topic": "profile", "repo": 7}]
        res, _ = run_tools(b, [["eda", x] for x in bad])
        for x, r in zip(bad, res):
            self.assertIn("error", r, x)
        self.assertIn("is not allowed", res[0]["error"])
        self.assertIn("must be one of", res[1]["error"])
        self.assertIn("is required", res[2]["error"])
        self.assertIn("must be integer", res[7]["error"])

    def test_without_eda_data_every_topic_says_unavailable(self):
        ds, _ = self.small()
        b = minimal_bundle(ds, None)
        res, unchanged = run_tools(b, [["eda", {"topic": t}] for t in TOPICS])
        self.assertTrue(unchanged)
        for r in res:
            v = r["value"]
            self.assertEqual((v["route"], v["status"]), ("lookup", "unavailable"))
            self.assertEqual(v["reason"], "eda was not computed for this build")
        no_key = {k: v for k, v in b.items() if k != "eda"}
        res, _ = run_tools(no_key, [["eda", {"topic": "ledger"}]])
        self.assertEqual(res[0]["value"]["status"], "unavailable")


def stub_bundle(ds, e):
    """What digest() reads, around the minimal bundle."""
    b = minimal_bundle(ds, e)
    b.update(meta={"title": "Synthetic", "family": "synthetic", "harvested_at": "2026-02-01", "built_at": "2026-02-01", "version": "t",
                   "head": None, "privacy": "private repos are counts only"},
             signals=[], ties={"machine": [], "curated": [], "series": [], "grades": {}}, spec={"views": [], "tour": []},
             self={"tool": {"version": "t", "modules": 1, "lines_of_code": 1, "code_digest": "x", "git": {}}, "pipeline": [], "limits": [],
                   "in_its_own_data": {"note": "n", "commits_in_data": 0}, "claude_in_the_data": {"commits": 0, "of": 0, "share_pct": 0, "sessions": 0,
                   "multi_repo_sessions": 0, "paired_commits": 0, "note": ""}, "signal_code": {}, "modules": []}, files={})
    b["repos"] = [{"name": r["name"], "visibility": r["visibility"], "kind": "own", "own_commits": 1, "by_class": {}, "coverage": {"tree": "measured"},
                   "first": "", "last": "", "files": 0, "lines_at_tip": 0, "tests": 0, "x3d_files": 0, "branches": 1, "role": ""} for r in b["repos"]]
    return b


@unittest.skipUnless(HAS_NODE, "Node is required for the eda tool tests")
class Digest(Fixtures):
    def digests(self):
        ds, e = self.small()
        with_eda, without = stub_bundle(ds, e), stub_bundle(ds, None)
        script = """
          const T = await import(process.argv[1]);
          const input = JSON.parse(await new Promise(r => { let s = ''; process.stdin.on('data', d => { s += d; }); process.stdin.on('end', () => r(s)); }));
          console.log(JSON.stringify(input.map(b => T.digest(b))));"""
        run = subprocess.run(["node", "--input-type=module", "-e", script, TOOLS_URI], input=json.dumps([with_eda, without]), capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_the_exploration_block_appears_only_with_eda_and_stays_short(self):
        a, b = self.digests()
        self.assertNotIn("EXPLORATION", b)
        self.assertIn("\nEXPLORATION ", a)
        s, v = a.index("\nEXPLORATION "), a.index("\nVIEWS IN THE SCENE")
        block = a[s + 1:v].strip("\n").split("\n")
        self.assertLessEqual(len(block), 6, block)
        self.assertTrue(all(len(line) < 400 for line in block), [len(x) for x in block])
        self.assertIn("never a probability", block[-1])
        self.assertIn("ungraded", block[-1])
        self.assertIn("12 declared looks", block[1])
        self.assertIn("Confirmed", block[1])
        self.assertLess(s, v)
        # Everything but the block is what the digest was before.
        self.assertEqual(a[:s] + a[v:], b)


@unittest.skipUnless(HAS_NODE, "Node is required for the eda tool tests")
class Surface(Fixtures):
    def test_python_tools_command_lists_eda_as_shared(self):
        run = subprocess.run([sys.executable, "-m", "x3dray", "tools"], cwd=ROOT, capture_output=True, text=True, timeout=60,
                             env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(run.returncode, 0, run.stderr)
        groups = json.loads(run.stdout)
        shared = [t["name"] for t in groups["mcp_and_page"]]
        self.assertIn("eda", shared)
        self.assertNotIn("eda", [t["name"] for t in groups["page_only"]])
        self.assertNotIn("eda", [t["name"] for t in groups["mcp_only"]])
        self.assertEqual(len(groups["mcp_and_page"]) + len(groups["page_only"]), 15)

    def test_mcp_server_serves_the_tool_and_every_wire_line_fits(self):
        ds, e = self.small()
        b = minimal_bundle(ds, e)
        b.update(meta={"title": "Synthetic", "harvested_at": "2026-02-01"}, files={}, signals=[], self={"signal_code": {}},
                 ties={"machine": [], "curated": [], "series": [], "grades": {}})
        nasty = '"\\漢' * 5000
        b["eda"]["looks"]["findings"][0]["text"] = nasty
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "eda", "arguments": {"topic": "ledger"}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "eda", "arguments": {"topic": "finding", "id": "contrast:lines"}}},
                    {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "eda", "arguments": {"topic": "bogus"}}},
                    {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "eda", "arguments": {"topic": "slice", "repo": "r1"}}}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            path.write_text(json.dumps(b))
            run = subprocess.run(["node", str(WEB / "mcp.mjs"), "--bundle", str(path)], input="\n".join(json.dumps(m) for m in messages) + "\n",
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        lines = run.stdout.splitlines()
        rows = {json.loads(x)["id"]: json.loads(x) for x in lines}
        for line in lines:
            self.assertLessEqual(len(line.encode("utf-8")), 32000)
        self.assertIn("eda", [t["name"] for t in rows[2]["result"]["tools"]])
        self.assertIn("never a probability", rows[1]["result"]["instructions"])
        ledger = json.loads(rows[3]["result"]["content"][0]["text"])
        self.assertEqual(ledger["findings_total"], 12)
        self.assertEqual(len(ledger["findings"]) + ledger["findings_omitted"], 12)
        self.assertFalse(rows[4]["result"].get("isError", False))
        self.assertTrue(rows[5]["result"]["isError"])
        self.assertEqual(json.loads(rows[6]["result"]["content"][0]["text"])["route"], "compute")

    def test_instructions_and_page_rules_name_the_tool_and_the_wording(self):
        mcp = (WEB / "mcp.mjs").read_text()
        app = (WEB / "app.js").read_text()
        for name, text in (("mcp.mjs", mcp), ("app.js", app)):
            self.assertRegex(text, r"\beda\b", name)
            self.assertIn("a grade here is robustness over declared checks, never a probability", text.lower(), name)
            self.assertIn("slices asked through eda are descriptive and ungraded", text, name)
        rules = app[app.index("const RULES = B =>"):app.index("DIGEST\n${digest(B)}")]
        self.assertIn("eda (", rules)

    def test_node_syntax_checks(self):
        for name in ("tools.js", "mcp.mjs", "app.js", "selftest.mjs", "live_gate.cjs"):
            run = subprocess.run(["node", "--check", str(WEB / name)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, f"{name}: {run.stderr}")

    def test_tools_js_still_inlines_into_the_observatory_page(self):
        source = (WEB / "tools.js").read_text()
        stripped = re.sub(r"^export (?=(?:const|function|async function)\b)", "", source, flags=re.M)
        self.assertEqual([x for x in stripped.splitlines() if x.startswith("export")], [])
        self.assertEqual([x for x in source.splitlines() if re.match(r"export (default|\{|class)", x)], [])
        self.assertEqual([x for x in source.splitlines() if x.startswith("import ")], [])
        exported = set(re.findall(r"^export (?:const|function) (eda\w+|EDA_\w+)", source, flags=re.M))
        self.assertEqual(exported, {"EDA_FORKS", "edaBin", "edaQuantile", "edaSummarize", "edaHourOf", "edaWeekdayOf", "edaInFork", "edaSliceValues", "EDA_INFER"})
        events = re.sub(r"^export (?=(?:const|class|function|async function)\b)", "", (ROOT / "x3dray" / "proof_web" / "investigation.js").read_text(), flags=re.M)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tools.js.txt").write_text(stripped)
            (Path(tmp) / "events.js.txt").write_text(events)
            script = """
              const fs = await import('node:fs'); const vm = await import('node:vm');
              const ctx = vm.createContext({ TextEncoder, console });
              // Two classic scripts in one global scope, as the Observatory page loads them: a repeated top-level name would throw.
              vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
              vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
              console.log(JSON.stringify(vm.runInContext("[typeof makeTools, typeof edaSummarize, edaBin(1000, 16, 1), edaWeekdayOf(0)]", ctx)));"""
            run = subprocess.run(["node", "--input-type=module", "-e", script, str(Path(tmp) / "tools.js.txt"), str(Path(tmp) / "events.js.txt")],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), ["function", "function", 10, 3])

    def test_the_selftest_exploration_block_passes_on_a_synthetic_bundle(self):
        """selftest.mjs is wired to the real family; its `if (B.eda)` block is run here against a synthetic one."""
        text = (WEB / "selftest.mjs").read_text()
        block = text[text.index("// 7b."):text.index("// 8. sample's limits")]
        calls = text[text.index("const EDA_CLASSES"):text.index("const CALLS = [")]
        ds, e = self.small()
        b = stub_bundle(ds, e)
        script = (f"import {{ makeTools, digest, edaSummarize, edaSliceValues, edaInFork, LIMIT_BYTES }} from {json.dumps(TOOLS_URI)};\n"
                  "import fs from 'node:fs';\n"
                  "const B = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));\n"
                  "const tools = Object.fromEntries(makeTools(B).map(t => [t.name, t]));\n"
                  "const checks = []; const check = (name, ok, detail) => checks.push({ name, ok: !!ok, detail });\n"
                  "const size = v => Buffer.byteLength(JSON.stringify(v), 'utf8');\n"
                  "const priv = B.repos.filter(r => r.visibility === 'private').map(r => r.name);\n"
                  + calls + "\nfor (const [name, input] of EDA_CALLS) { try { const r = tools[name].execute(input); check(name + JSON.stringify(input), size(r) <= LIMIT_BYTES); } catch (e) { check(name, false, e.message); } }\n"
                  + block + "\nconsole.log(JSON.stringify(checks));")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            path.write_text(json.dumps(b))
            run = subprocess.run(["node", "--input-type=module", "-e", script, str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        checks = json.loads(run.stdout)
        failed = [c for c in checks if not c["ok"]]
        self.assertEqual(failed, [])
        self.assertGreaterEqual(len(checks), 20)
        names = " ".join(c["name"] for c in checks)
        self.assertIn("recomputes in JavaScript", names)
        self.assertNotRegex(names, r"\br2[:/]")      # a private repo's name is never printed before ':' or '/'


if __name__ == "__main__":
    unittest.main()
