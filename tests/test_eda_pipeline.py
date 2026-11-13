"""The eda stage inside the pipeline: the CLI step, the profile signals, the bundle, the witness and privacy gates and the
self-description. Datasets come from tests/eda_support; nothing here needs git 2.44, node, X_ITE or a font."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eda_support import bundle_commit_rows, synthetic_dataset  # noqa: E402

from x3dray import __main__ as cli, eda, gates, page, profile, publication, selfdesc, spec, structure  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDA_IDS = ("eda_audit", "eda_profile", "eda_findings")
PRIVATE = ("r2",)
_CACHE: dict = {}


def dataset(**kw) -> dict:
    """A fresh copy of a cached synthetic dataset; the per-file commit counts are what profile_family reads from the tree."""
    key = json.dumps(kw, sort_keys=True)
    if key not in _CACHE:
        ds = synthetic_dataset(**kw)
        for r in ds["repos"]:
            for f in r["files"]:
                f["commits"] = 3
        _CACHE[key] = ds
    return copy.deepcopy(_CACHE[key])


def standard() -> dict:
    return dataset(seed=3, repos=3, per_repo=60, private=PRIVATE, plant="defects")


def eda_of(ds: dict) -> dict:
    key = "eda:" + json.dumps(ds, sort_keys=True)
    if key not in _CACHE:
        out, problems = eda.run(ds)
        assert not problems, problems
        _CACHE[key] = out
    return copy.deepcopy(_CACHE[key])


def put(path: str, obj, compact: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, **({"separators": (",", ":")} if compact else {}))
        fh.write("\n")


def quiet(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args)
    return rc, buf.getvalue()


class CliStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = os.path.join(self.tmp.name, "data")
        put(os.path.join(self.data, "dataset.json"), standard())

    def raw(self) -> bytes:
        with open(os.path.join(self.data, "eda.json"), "rb") as fh:
            return fh.read()

    def test_writes_compact_deterministic_json_and_one_summary_line(self):
        rc, text = quiet(cli.cmd_eda, SimpleNamespace(data=self.data))
        self.assertEqual(rc, 0)
        first = self.raw()
        obj = json.loads(first)
        self.assertEqual(obj["stage"], "eda")
        self.assertEqual(first.decode("utf-8"), json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
        lines = text.strip().splitlines()
        self.assertEqual(len(lines), 1)
        by, led = obj["looks"]["ledger"]["by_grade"], obj["looks"]["ledger"]
        self.assertRegex(lines[0], r"^eda: 180 commits, 12 declared looks \(\d+ Confirmed, \d+ Likely, \d+ Suspected, \d+ without a grade, "
                                   r"\d+ not run\), \d+ flagged commits in \d+\.\ds -> ")
        self.assertIn(f"({by['C']} Confirmed, {by['L']} Likely, {by['S']} Suspected, {by['none'] - led['not_run']} without a grade, "
                      f"{led['not_run']} not run)", lines[0])
        quiet(cli.cmd_eda, SimpleNamespace(data=self.data))
        self.assertEqual(self.raw(), first)

    def test_a_commit_the_stage_cannot_count_stops_with_one_named_line_not_a_traceback(self):
        bad = {
            "line count past 2**53": ("add", 2 ** 60), "sum past 2**53": ("add", 2 ** 53 - 1), "float line count": ("add", 3.5),
            "negative line count": ("add", -5), "null line count": ("add", None), "numeric string": ("del", "12"),
            "bool line count": ("files", True), "missing field": ("files", KeyError),
            # a time is bounded too: a gap of 2**53 seconds would reach stats.summarize, and the page's Date holds 8.64e12
            "time past the page's dates": ("t", 10 ** 13), "time of 10**16": ("t", 10 ** 16), "time of 2**54": ("t", 2 ** 54),
            "negative time past the page's dates": ("t", -(10 ** 16)), "float time": ("t", 1.5e9),
        }
        for name, (key, value) in bad.items():
            with self.subTest(name):
                ds = standard()
                c = ds["repos"][0]["commits"][3]
                c["del"] = max(c["del"], 1)
                if value is KeyError:
                    del c[key]
                else:
                    c[key] = value
                put(os.path.join(self.data, "dataset.json"), ds)
                if os.path.exists(os.path.join(self.data, "eda.json")):
                    os.remove(os.path.join(self.data, "eda.json"))
                rc, text = quiet(cli.cmd_eda, SimpleNamespace(data=self.data))
                self.assertEqual(rc, 1)
                self.assertRegex(text.strip(), r"^eda: dataset repo #0 commit #3: \w+ ")
                self.assertEqual(len(text.strip().splitlines()), 1)
                self.assertFalse(os.path.exists(os.path.join(self.data, "eda.json")))
                with self.assertRaises(eda.DatasetError):
                    eda.run(ds)

    def test_the_edges_of_the_time_range_still_run(self):
        for t in (eda.T_LIMIT, -eda.T_LIMIT, 0, -5):
            with self.subTest(t=t):
                ds = standard()
                ds["repos"][0]["commits"][3]["t"] = t
                out, problems = eda.run(ds)
                self.assertEqual(problems, [])
        ds = standard()
        ds["repos"][0]["commits"][3]["t"] = eda.T_LIMIT + 1
        with self.assertRaises(eda.DatasetError):
            eda.run(ds)

    def test_a_dataset_not_shaped_like_the_harvest_stops_with_one_named_line_not_a_traceback(self):
        def churn(v):
            return lambda ds: ds["repos"][0]["files"][2].__setitem__("churn", v)

        def drop(key):
            return lambda ds: ds["repos"][0].pop(key)

        def setkey(key, v):
            return lambda ds: ds["repos"][0].__setitem__(key, v)
        bad = {
            "negative churn": (churn(-5000), r"repo #0 file #2: churn"), "small negative churn": (churn(-5), r"repo #0 file #2: churn"),
            "float churn": (churn(12.5), r"repo #0 file #2: churn"), "string churn": (churn("7"), r"repo #0 file #2: churn"),
            "bool churn": (churn(True), r"repo #0 file #2: churn"), "churn past 2**53": (churn(2 ** 60), r"repo #0 file #2: churn"),
            "file that is not an object": (lambda ds: ds["repos"][0]["files"].__setitem__(2, 7), r"repo #0 file #2 is not an object"),
            "repo without a name": (drop("name"), r"repo #0: name"), "repo with a number for a name": (setkey("name", 5), r"repo #0: name"),
            "commits null": (setkey("commits", None), r"repo #0: commits must be a list"),
            "files null": (setkey("files", None), r"repo #0: files must be a list"),
            "repos null": (lambda ds: ds.__setitem__("repos", None), r"repos must be a list"),
            "repo that is not an object": (lambda ds: ds["repos"].__setitem__(1, "x"), r"repo #1 is not an object"),
            "identities null": (lambda ds: ds.__setitem__("identities", None), r"identities must be a list"),
        }
        for name, (mutate, where) in bad.items():
            with self.subTest(name):
                ds = standard()
                mutate(ds)
                put(os.path.join(self.data, "dataset.json"), ds)
                if os.path.exists(os.path.join(self.data, "eda.json")):
                    os.remove(os.path.join(self.data, "eda.json"))
                rc, text = quiet(cli.cmd_eda, SimpleNamespace(data=self.data))
                self.assertEqual(rc, 1)
                self.assertRegex(text.strip(), r"^eda: dataset " + where)
                self.assertEqual(len(text.strip().splitlines()), 1)
                self.assertFalse(os.path.exists(os.path.join(self.data, "eda.json")))
                with self.assertRaises(eda.DatasetError):
                    eda.run(ds)

    def test_the_stage_never_writes_a_float_for_a_valid_dataset_with_zero_or_missing_churn(self):
        ds = standard()
        for f in ds["repos"][0]["files"][:3]:
            f["churn"] = 0
        del ds["repos"][0]["files"][3]["churn"]
        ds["repos"][0]["files"][4]["churn"] = None
        out, problems = eda.run(ds)
        self.assertEqual(problems, [])

    def test_the_largest_exact_counts_still_run(self):
        ds = standard()
        c = ds["repos"][0]["commits"][3]
        c["add"], c["del"] = 2 ** 53 - 2, 1
        out, problems = eda.run(ds)
        self.assertEqual(problems, [])

    def test_the_subparser_runs_the_stage(self):
        rc, text = quiet(cli.main, ["eda", "--data", self.data])
        self.assertEqual((rc, text.startswith("eda: ")), (0, True))
        self.assertTrue(os.path.exists(os.path.join(self.data, "eda.json")))

    def test_a_missing_dataset_stops_with_the_usual_message(self):
        with self.assertRaises(SystemExit) as cm:
            cli.cmd_eda(SimpleNamespace(data=os.path.join(self.tmp.name, "nowhere")))
        self.assertIn("dataset.json is missing", str(cm.exception))

    def test_a_verify_problem_returns_one_and_all_stops_at_eda(self):
        with mock.patch.object(eda, "verify", return_value=["audit ts_range: count differs"]):
            rc, text = quiet(cli.cmd_eda, SimpleNamespace(data=self.data))
            self.assertEqual(rc, 1)
            self.assertIn("  audit ts_range: count differs", text)
            called = []

            def step(name):
                def fn(a):
                    called.append(name)
                    return 0
                fn.__name__ = "cmd_" + name
                return fn
            fakes = {n: step(n) for n in ("harvest", "structure")}
            late = {n: step(n) for n in ("profile", "self", "spec", "build", "page", "gate", "receipt")}
            a = SimpleNamespace(data=self.data, masterclass=None)
            with mock.patch.multiple(cli, **{"cmd_" + n: f for n, f in {**fakes, **late}.items()}):
                rc, text = quiet(cli._all_steps, a)
            self.assertEqual(rc, 1)
            self.assertIn("all: stopped at eda", text)
            self.assertEqual(called, ["harvest", "structure"])

    def test_at_most_twenty_problems_are_printed(self):
        with mock.patch.object(eda, "verify", return_value=[f"problem {i}" for i in range(30)]):
            rc, text = quiet(cli.cmd_eda, SimpleNamespace(data=self.data))
        self.assertEqual(rc, 1)
        self.assertEqual(sum(1 for ln in text.splitlines() if ln.startswith("  problem")), 20)

    def test_all_runs_eda_between_structure_and_profile(self):
        order = []

        def step(name):
            def fn(a):
                order.append(name)
                return 0
            fn.__name__ = "cmd_" + name
            return fn
        names = ("harvest", "curated", "structure", "eda", "profile", "self", "spec", "build", "page", "gate", "receipt")
        with mock.patch.multiple(cli, **{"cmd_" + n: step(n) for n in names}):
            self.assertEqual(cli._all_steps(SimpleNamespace(masterclass="mc")), 0)
            self.assertEqual(order, list(names))
            order.clear()
            cli._all_steps(SimpleNamespace(masterclass=None))
            self.assertEqual(order, [n for n in names if n != "curated"])

    def test_profile_reads_eda_json_only_when_it_exists(self):
        seen = []

        def fake(ds, ties, curated, concepts, bridges, eda=None):
            seen.append(eda)
            return {"signals": []}
        for name, doc in (("ties.json", {"pairs": []}),):
            put(os.path.join(self.data, name), doc)
        with mock.patch.object(profile, "profile_family", fake):
            quiet(cli.cmd_profile, SimpleNamespace(data=self.data))
            put(os.path.join(self.data, "eda.json"), {"stage": "eda"})
            quiet(cli.cmd_profile, SimpleNamespace(data=self.data))
        self.assertEqual(seen, [None, {"stage": "eda"}])

    def test_the_module_docstring_lists_the_step(self):
        self.assertRegex(cli.__doc__, r"\n  eda +--data DIR")


class ProfileSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = standard()
        cls.eda = eda_of(cls.ds)
        cls.con, cls.bri, problems = structure.run(cls.ds)
        assert not problems

    def family(self, eda_doc, ds=None):
        return profile.profile_family(ds or self.ds, {"pairs": []}, None, self.con, self.bri, eda=eda_doc)

    def sigs(self, prof):
        return {s["id"]: s for s in prof["signals"]}

    def test_signals_exist_only_when_eda_is_passed(self):
        without = self.family(None)
        self.assertEqual(profile.profile_family(self.ds, {"pairs": []}, None, self.con, self.bri), without)
        self.assertFalse(set(EDA_IDS) & set(self.sigs(without)))
        self.assertEqual(self.family({}), without)
        with_eda = self.family(self.eda)
        self.assertEqual([s["id"] for s in with_eda["signals"] if s["id"] not in EDA_IDS], [s["id"] for s in without["signals"]])
        self.assertEqual([s for s in with_eda["signals"] if s["id"] not in EDA_IDS], without["signals"])
        ids = [s["id"] for s in with_eda["signals"]]
        self.assertEqual(ids.index("eda_audit") + 1, ids.index("eda_profile"))
        self.assertEqual(ids.index("eda_profile") + 1, ids.index("eda_findings"))
        self.assertLess(ids.index("eda_findings"), ids.index("idle_forks"))
        for sid in ("family_span", "who_commits", "busiest_month", "rhythm"):
            self.assertIn(sid, ids)

    def test_kinds_values_and_texts(self):
        s = self.sigs(self.family(self.eda))
        self.assertEqual([s[i]["kind"] for i in EDA_IDS], ["quality", "outlier", "identity"])
        for i in EDA_IDS:
            self.assertEqual(s[i]["values"]["from_stage"], "eda")
            self.assertLess(len(s[i]["text"]), 300, i)
            for k, v in s[i]["values"].items():
                self.assertIn(type(v), (int, str, bool), (i, k))
        checks = self.eda["audit"]["checks"]
        v = s["eda_audit"]["values"]
        grades = [c["grade"] for c in checks if c["status"] == "flag"]
        self.assertEqual((v["commits"], v["checks"]), (self.eda["coverage"]["commits"], len(checks)))
        self.assertEqual((v["confirmed"], v["likely"], v["suspected"]), tuple(grades.count(g) for g in "CLS"))
        self.assertEqual(v["passed"], sum(c["status"] == "pass" for c in checks))
        self.assertEqual(v["unavailable"], sum(c["status"] == "unavailable" for c in checks))
        self.assertEqual(s["eda_audit"]["text"],
                         f"Data audit of {v['commits']} own commits: {v['confirmed']} of {v['checks']} checks flag a Confirmed violation, "
                         f"{v['likely']} Likely, {v['suspected']} Suspected; {v['passed']} pass and {v['unavailable']} could not run. "
                         "Times are UTC author times; renames are not followed.")
        # the planted defects are visible in the signal
        self.assertEqual((v["confirmed"], v["likely"], v["suspected"]), (3, 1, 2))

    def test_the_size_signal_is_its_own_values(self):
        s = self.sigs(self.family(self.eda))["eda_profile"]
        v = s["values"]
        rec = next(r for r in self.eda["profile"]["records"] if (r["slice"], r["fork"], r["measure"]) == ("family", "no_merge", "lines"))
        self.assertEqual((v["n"], v["p50"], v["p90"], v["p99"], v["max"], v["gini_permille"]),
                         (rec["n"], rec["p50"], rec["p90"], rec["p99"], rec["max"], rec["gini_permille"]))
        rules = self.eda["flags"]["by_rule"].values()
        for key, g in (("confirmed", "C"), ("likely", "L"), ("suspected", "S")):
            self.assertEqual(v[key], sum(r["counts"][g] for r in rules))
        self.assertEqual(v["flagged"], v["confirmed"] + v["likely"] + v["suspected"])
        gini = f"{v['gini_permille'] // 1000}.{v['gini_permille'] % 1000:03d}"
        self.assertEqual(s["text"], f"Commit size outside merges: median {v['p50']} lines changed, 90th percentile {v['p90']}, "
                                    f"largest {v['max']} (Gini {gini}); {v['flagged']} flags on commits for being unusually large or following "
                                    f"a long quiet spell: {v['confirmed']} Confirmed, {v['likely']} Likely, {v['suspected']} Suspected.")
        self.assertTrue(v["available"])

    def test_the_size_signal_is_unavailable_below_ten_commits(self):
        tiny = dataset(seed=4, repos=1, per_repo=6)
        con, bri, _ = structure.run(tiny)
        doc = eda_of(tiny)
        prof = profile.profile_family(tiny, {"pairs": []}, None, con, bri, eda=doc)
        s = self.sigs(prof)["eda_profile"]
        self.assertEqual(s["text"], "Commit size could not be profiled: fewer than 10 commits outside merges.")
        self.assertIs(s["values"]["available"], False)
        self.assertTrue(s["values"]["reason"])
        self.assertEqual(s["kind"], "outlier")

    def test_findings_signal_counts_and_wording(self):
        doc = self.eda
        s = self.sigs(self.family(doc))["eda_findings"]
        v, led = s["values"], doc["looks"]["ledger"]
        lab = doc["classes"]["labels"]
        a, b = doc["looks"]["pair"]
        self.assertEqual((v["declared"], v["forks"], v["not_run"]), (12, 4, led["not_run"]))
        self.assertEqual(v["confirmed"] + v["likely"] + v["suspected"] + v["without_grade"] + v["not_run"], v["declared"])
        d = doc["coverage"]["distinct_forks"]
        self.assertEqual((v["contrasts"], v["drifts"], v["distinct_forks"]), (6, 6, d))
        # six of the twelve compare one class with itself, so the sentence must not call all of them contrasts between a and b
        self.assertEqual(s["text"], f"12 declared looks (6 contrasts between {lab[a]} and {lab[b]}, 6 drifts within one class): "
                                    f"{v['confirmed']} Confirmed, {v['likely']} Likely, "
                                    f"{v['suspected']} Suspected, {v['without_grade']} without a grade, {v['not_run']} not run. "
                                    f"Grades are robustness checks over 4 declared forks ({d} distinct here), not probabilities.")
        self.assertNotIn("declared contrasts", s["text"])
        self.assertNotIn("top_id", v)                          # this noise dataset grades nothing

    def test_a_planted_difference_reaches_the_signal_with_its_best_finding(self):
        planted = dataset(seed=5, repos=3, per_repo=120, plant=("ai_smaller", "ai_hours"))
        con, bri, _ = structure.run(planted)
        doc = eda_of(planted)
        s = self.sigs(profile.profile_family(planted, {"pairs": []}, None, con, bri, eda=doc))["eda_findings"]
        graded = [f for f in doc["looks"]["findings"] if f["grade"]]
        self.assertTrue(graded)
        self.assertEqual(s["values"]["top_id"], profile.eda_top_finding(doc["looks"]["findings"]))
        self.assertIn(s["values"]["top_id"], {f["id"] for f in graded})
        self.assertEqual(s["values"]["confirmed"] + s["values"]["likely"] + s["values"]["suspected"], len(graded))

    def test_top_finding_orders_by_grade_then_q_then_id(self):
        def f(i, g, q=(1, 2)):
            return {"id": i, "grade": g, "q": list(q)}
        self.assertIsNone(profile.eda_top_finding([f("a", None), {"id": "b", "grade": None, "q": None}]))
        self.assertEqual(profile.eda_top_finding([f("a", "S", (1, 100)), f("b", "L", (9, 10)), f("c", "C", (99, 100))]), "c")
        self.assertEqual(profile.eda_top_finding([f("a", "L", (1, 3)), f("b", "L", (2, 7))]), "b")          # 2/7 < 1/3 as fractions
        self.assertEqual(profile.eda_top_finding([f("b", "L", (2, 4)), f("a", "L", (1, 2))]), "a")          # equal q: the smaller id
        self.assertEqual(profile.eda_top_finding([f("z", "S", (1, 20)), f("y", "S", (1, 20))]), "y")

    def test_no_pair_gives_an_unavailable_signal_with_the_reason(self):
        doc = copy.deepcopy(self.eda)
        doc["looks"]["pair"], doc["looks"]["pair_reason"] = None, "no two author classes have 20 commits"
        s = self.sigs(self.family(doc))["eda_findings"]
        self.assertEqual(s["text"], "No look was run: no two author classes have 20 commits.")
        self.assertIs(s["values"]["available"], False)
        self.assertEqual(s["values"]["reason"], "no two author classes have 20 commits")
        self.assertNotIn("declared", s["values"])

    def test_the_planted_repo_confound_is_never_confirmed(self):
        # The class follows the repo here, so nothing is compared inside a repo; four repos is enough for
        # leave-one-repo-out to run, which leaves the strata check as the only guard.
        for seed in (1, 2, 3):
            doc = eda_of(dataset(seed=seed, repos=4, per_repo=60, plant="confound"))
            lines = {f["id"]: f for f in doc["looks"]["findings"]}["contrast:lines"]
            self.assertEqual(lines["checks"]["strata"]["n"], 0, seed)
            self.assertNotEqual(lines["grade"], "C", seed)

    def test_the_real_pair_none_case_end_to_end(self):
        solo = dataset(seed=6, repos=1, per_repo=30, plant="confound")        # one class only: no contrast can be run
        con, bri, _ = structure.run(solo)
        doc = eda_of(solo)
        self.assertIsNone(doc["looks"]["pair"])
        s = self.sigs(profile.profile_family(solo, {"pairs": []}, None, con, bri, eda=doc))["eda_findings"]
        self.assertIs(s["values"]["available"], False)
        self.assertEqual(s["text"], f"No look was run: {doc['looks']['pair_reason']}.")

    def test_signal_code_finds_the_new_ids_and_slots_resolve(self):
        with open(os.path.join(REPO, "x3dray", "profile.py"), encoding="utf-8") as fh:
            code = selfdesc.signal_code(fh.read())
        for sid in EDA_IDS:
            self.assertIn(sid, code)
            self.assertLess(code[sid]["start"], code[sid]["end"])
        self.assertIn("flagged_by", code["eda_audit"]["code"])
        # how_built shows the computation behind a signal: for these two that is the branch with the numbers, not the early exit
        self.assertIn("fcount", code["eda_profile"]["code"])
        self.assertIn("gini", code["eda_profile"]["code"])
        self.assertIn("p90", code["eda_profile"]["code"])
        self.assertIn("top = eda_top_finding", code["eda_findings"]["code"])
        self.assertIn("ungraded", code["eda_findings"]["code"])
        prof = self.family(self.eda)
        text, slots, problems = spec.fill("{eda_findings.declared} looks; {eda_audit.checks} checks; {eda_profile.p50} lines", prof, None)
        self.assertEqual(problems, [])
        self.assertEqual(text, f"12 looks; {len(self.eda['audit']['checks'])} checks; "
                               f"{self.sigs(prof)['eda_profile']['values']['p50']} lines")
        self.assertEqual(len(slots), 3)

    def test_every_signal_id_is_emitted_by_exactly_one_sig_call(self):
        # signal_code keeps the first call per id, so a second call would hide the code that computes the signal.
        with open(os.path.join(REPO, "x3dray", "profile.py"), encoding="utf-8") as fh:
            ids = [m.group(1) for m in selfdesc.SIG_CALL.finditer(fh.read())]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(dup, [])

    def test_the_profile_is_json_and_deterministic(self):
        a, b = json.dumps(self.family(self.eda), sort_keys=True), json.dumps(self.family(copy.deepcopy(self.eda)), sort_keys=True)
        self.assertEqual(a, b)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.ds = standard()
        self.eda = eda_of(self.ds)
        docs = {"dataset.json": self.ds, "ties.json": {"pairs": []},
                "profile.json": {"per_repo": {r["name"]: {"visibility": r.get("visibility", "public")} for r in self.ds["repos"]}, "signals": []},
                "spec.rendered.json": {}, "self.json": {"modules": [], "tool": {"git": {}}}, "curated.json": {}, "layout.json": {}}
        for name, doc in docs.items():
            put(os.path.join(self.root, name), doc)

    def test_the_bundle_carries_eda_json_verbatim(self):
        put(os.path.join(self.root, "eda.json"), self.eda, compact=True)
        b = page.make_bundle(self.root, self.root)
        self.assertEqual(b["eda"], self.eda)
        self.assertEqual(json.loads(json.dumps(b))["eda"], self.eda)

    def test_the_key_is_omitted_not_null_when_the_stage_did_not_run(self):
        b = page.make_bundle(self.root, self.root)
        self.assertNotIn("eda", b)
        put(os.path.join(self.root, "eda.json"), {})
        self.assertNotIn("eda", page.make_bundle(self.root, self.root))

    def test_the_rest_of_the_bundle_is_unchanged_by_the_stage(self):
        before = page.make_bundle(self.root, self.root)
        put(os.path.join(self.root, "eda.json"), self.eda, compact=True)
        after = page.make_bundle(self.root, self.root)
        self.assertEqual({k: v for k, v in after.items() if k != "eda"}, before)

    def test_the_commit_rows_are_the_rows_the_stage_indexes(self):
        put(os.path.join(self.root, "eda.json"), self.eda, compact=True)
        b = page.make_bundle(self.root, self.root)
        cols = ("i", "r", "t", "c", "a", "d", "f", "m", "b")
        self.assertEqual([tuple(r[k] for k in cols) for r in b["commits"]], [tuple(x[k] for k in cols) for x in eda.indexed_rows(self.ds)])


class Site:
    """A data folder and a page folder, minimal but real enough for gate_privacy and gate_witness."""

    def __init__(self, root: str, ds: dict, doc: dict | None, bundle_eda: bool = True, disk_eda: bool = True):
        self.data, self.out, self.ds = os.path.join(root, "data"), os.path.join(root, "out"), ds
        self.priv = {r["name"] for r in ds["repos"] if r.get("visibility") == "private"}
        put(os.path.join(self.data, "dataset.json"), ds)
        if doc is not None and disk_eda:
            put(os.path.join(self.data, "eda.json"), doc, compact=True)
        bundle = {"repos": [{"name": r["name"], "visibility": r.get("visibility", "public")} for r in ds["repos"]],
                  "commits": bundle_commit_rows(ds), "files": {}, "ties": {"machine": [], "curated": []}}
        if doc is not None and bundle_eda:
            bundle["eda"] = copy.deepcopy(doc)
        self.write_bundle(bundle)
        put(os.path.join(self.out, "layout.json"), {"ties": {"machine": [], "curated": []}})
        with open(os.path.join(self.out, "index.html"), "w", encoding="utf-8") as fh:
            fh.write("<title>Fixture</title>")
        with open(os.path.join(self.out, "xray.x3d"), "w", encoding="utf-8") as fh:
            fh.write("<X3D version='4.0'><Scene/></X3D>")
        publication.register(self.out, [publication.entry(p) for p in ("index.html", "bundle.json", "xray.x3d")])

    def bundle(self) -> dict:
        with open(os.path.join(self.out, "bundle.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def write_bundle(self, b: dict) -> None:
        put(os.path.join(self.out, "bundle.json"), b)

    def edit_bundle(self, fn) -> None:
        b = self.bundle()
        fn(b)
        self.write_bundle(b)

    def edit_disk(self, fn) -> None:
        p = os.path.join(self.data, "eda.json")
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        fn(doc)
        put(p, doc, compact=True)


class WitnessGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = standard()
        cls.eda = eda_of(cls.ds)

    def site(self, **kw) -> Site:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Site(tmp.name, copy.deepcopy(self.ds), self.eda, **kw)

    def witness(self, s: Site) -> dict:
        return gates.gate_witness(s.out, s.data)

    def test_a_consistent_pair_passes(self):
        s = self.site()
        r = self.witness(s)
        self.assertEqual(r["status"], "pass", r)
        self.assertRegex(r["summary"], r"eda recomputed: 12 findings, \d+ flagged commits; check clean")
        self.assertEqual(r["structure"], [])
        self.assertEqual(sorted(r), ["drawn_without_record", "scope", "status", "structure", "summary", "without_witness"])    # no new key

    def test_a_time_the_stage_cannot_count_is_a_named_problem_not_a_crash(self):
        s = self.site()
        for t in (2 ** 54, 10 ** 13):
            bad = copy.deepcopy(self.ds)
            bad["repos"][0]["commits"][3]["t"] = t
            put(os.path.join(s.data, "dataset.json"), bad)
            probs, _ = gates._eda_witness(s.bundle(), s.data)
            self.assertEqual(len(probs), 1, t)
            self.assertRegex(probs[0], r"^the eda stage cannot run on dataset.json: dataset repo #0 commit #3: t is outside")

    def test_a_build_without_eda_behaves_as_before(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = Site(tmp.name, self.ds, None)
        r = self.witness(s)
        self.assertEqual(r["status"], "pass")
        self.assertIn("no eda in this build", r["summary"])
        self.assertEqual(gates._eda_witness(s.bundle(), s.data), ([], "no eda in this build"))
        self.assertEqual(gates._eda_witness(s.bundle(), None), ([], "no eda in this build"))
        self.assertEqual(gates.gate_privacy(s.out)["status"], "pass")

    def test_an_edited_grade_in_the_bundle_fails(self):
        s = self.site()

        def edit(b):
            f = b["eda"]["looks"]["findings"][0]
            f["grade"] = "C" if f["grade"] != "C" else "S"
        s.edit_bundle(edit)
        r = self.witness(s)
        self.assertEqual(r["status"], "fail")
        self.assertIn("the bundle's eda differs from eda.json in the data folder", r["structure"])

    def test_an_edited_eda_json_on_disk_fails(self):
        s = self.site()
        s.edit_disk(lambda d: d["audit"]["checks"][0].update(count=d["audit"]["checks"][0]["count"] + 1))
        r = self.witness(s)
        self.assertEqual(r["status"], "fail")
        self.assertIn("the bundle's eda differs from eda.json in the data folder", r["structure"])

    def test_both_edited_alike_is_caught_by_the_fresh_run(self):
        s = self.site()

        def edit(d):
            d["audit"]["checks"][0]["count"] += 1
        s.edit_bundle(lambda b: edit(b["eda"]))
        s.edit_disk(edit)
        r = self.witness(s)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["structure"], ["eda.json is stale: a fresh run of the stage on dataset.json gives something else"])

    def test_a_count_edited_alike_to_a_float_or_a_bool_is_caught(self):
        # Python says 180 == 180.0 and 0 == False, so a plain object comparison lets a retyped count through.
        edits = {
            "float count": lambda d: d["coverage"].update(commits=float(d["coverage"]["commits"])),
            "bool count": lambda d: d["audit"]["checks"][0].update(count=bool(d["audit"]["checks"][0]["count"])),
            "float count of zero": lambda d: d["audit"]["checks"][0].update(count=float(d["audit"]["checks"][0]["count"])),
            "float member count": lambda d: d["profile"]["records"][0].update(members=float(d["profile"]["records"][0]["members"])),
        }
        for name, edit in edits.items():
            with self.subTest(name):
                s = self.site()
                s.edit_bundle(lambda b: edit(b["eda"]))
                s.edit_disk(edit)
                r = self.witness(s)
                self.assertEqual(r["status"], "fail", name)
                self.assertTrue(r["structure"], name)

    def test_a_retyped_value_on_one_side_fails(self):
        s = self.site()
        s.edit_disk(lambda d: d["coverage"].update(commits=float(d["coverage"]["commits"])))
        r = self.witness(s)
        self.assertEqual(r["status"], "fail")
        self.assertIn("the bundle's eda differs from eda.json in the data folder", r["structure"])

    def test_verify_rejects_floats_and_bools_where_integers_belong(self):
        def fl(d):
            d["coverage"]["commits"] = float(d["coverage"]["commits"])

        def bl(d):
            d["audit"]["checks"][0]["count"] = bool(d["audit"]["checks"][0]["count"])

        def members(d):
            d["profile"]["records"][0]["members"] = float(d["profile"]["records"][0]["members"])

        def omitted(d):
            body = next(iter(d["flags"]["by_rule"].values()))
            body["omitted"]["C"] = bool(body["omitted"]["C"])

        def version(d):
            d["schema_version"] = True
        self.assertEqual(eda.verify(copy.deepcopy(self.eda), self.ds), [])
        for edit in (fl, bl, members, omitted, version):
            with self.subTest(edit.__name__):
                doc = copy.deepcopy(self.eda)
                edit(doc)
                self.assertTrue(eda.verify(doc, self.ds), edit.__name__)

    def test_canonical_json_tells_types_apart(self):
        for a, b in ((1, 1.0), (0, False), (1, True), ([0], [False]), ({"k": 2}, {"k": 2.0})):
            self.assertEqual(a, b)
            self.assertNotEqual(eda.canonical_json(a), eda.canonical_json(b))
        self.assertEqual(eda.canonical_json({"b": [1, 2], "a": None}), eda.canonical_json({"a": None, "b": [1, 2]}))

    def test_a_dataset_the_stage_cannot_count_fails_the_gate_with_a_reason(self):
        s = self.site()
        ds = copy.deepcopy(self.ds)
        ds["repos"][0]["commits"][0]["add"] = 2 ** 60
        with open(os.path.join(s.data, "dataset.json"), "w", encoding="utf-8") as fh:
            json.dump(ds, fh)
        r = self.witness(s)
        self.assertEqual(r["status"], "fail")
        self.assertTrue(any(p.startswith("the eda stage cannot run on dataset.json: dataset repo #0 commit #0") for p in r["structure"]), r["structure"])

    def test_eda_on_one_side_only_fails(self):
        r = self.witness(self.site(disk_eda=False))
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["structure"], ["eda is in the bundle but not in the data folder"])
        r = self.witness(self.site(bundle_eda=False))
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["structure"], ["eda is in the data folder but not in the bundle"])
        s = self.site()
        self.assertEqual(gates._eda_witness(s.bundle(), None)[0], ["eda is in the bundle but not in the data folder"])

    def test_a_stale_eda_json_fails(self):
        s = self.site()
        ds = copy.deepcopy(self.ds)
        ds["repos"][0]["tree_measured"] = not ds["repos"][0]["tree_measured"]       # eda reads it; the bundle rows do not
        put(os.path.join(s.data, "dataset.json"), ds)
        r = self.witness(s)
        self.assertEqual(r["status"], "fail")
        self.assertIn("eda.json is stale: a fresh run of the stage on dataset.json gives something else", r["structure"])
        self.assertFalse([p for p in r["structure"] if "indexed its flags" in p])

    def test_a_missing_dataset_fails(self):
        s = self.site()
        os.remove(os.path.join(s.data, "dataset.json"))
        r = self.witness(s)
        self.assertEqual(r["status"], "fail")
        self.assertIn("no dataset.json to check the eda section against", r["structure"])

    def test_a_different_commit_order_in_the_bundle_fails(self):
        s = self.site()

        def swap(b):
            c = b["commits"]
            c[0], c[1] = c[1], c[0]
        s.edit_bundle(swap)
        r = self.witness(s)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["structure"], ["the bundle's commits are not the rows the eda stage indexed its flags by"])

        s = self.site()

        def resize(b):
            b["commits"][5]["a"] += 1
        s.edit_bundle(resize)
        self.assertEqual(self.witness(s)["structure"], ["the bundle's commits are not the rows the eda stage indexed its flags by"])

    def test_a_problem_found_by_the_stages_own_check_is_reported(self):
        s = self.site()
        with mock.patch.object(eda, "verify", return_value=["flags: size_high differs"]):
            r = self.witness(s)
        self.assertEqual(r["status"], "fail")
        self.assertIn("flags: size_high differs", r["structure"])
        self.assertRegex(r["summary"], r"check FAILED")

    def test_a_malformed_section_does_not_crash_the_gate(self):
        s = self.site()
        s.edit_bundle(lambda b: b["eda"].pop("looks"))
        s.edit_disk(lambda d: d.pop("looks"))
        r = self.witness(s)
        self.assertEqual(r["status"], "fail")

    def test_the_result_text_passes_the_result_guard(self):
        priv = {"r2"}
        for s in (self.site(), self.site(disk_eda=False)):
            r = self.witness(s)
            g = {"witness": r}
            self.assertEqual(gates._guard(g, priv), [], r["summary"])
            self.assertFalse(structure.private_rx(priv).search(json.dumps(r)), r)

    def test_problems_never_echo_a_private_repo_name_with_a_path_marker(self):
        s = self.site()
        s.edit_disk(lambda d: d["concentration"]["repos"][0].update(repo="r2"))
        r = self.witness(s)
        self.assertEqual(r["status"], "fail")
        self.assertFalse(structure.private_rx({"r2"}).search(json.dumps(r)), r)


class PrivacyGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = standard()
        cls.eda = eda_of(cls.ds)

    def site(self) -> Site:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Site(tmp.name, copy.deepcopy(self.ds), self.eda)

    def check_clean_messages(self, r):
        self.assertFalse(structure.private_rx({"r2"}).search(json.dumps(r)), r)

    def test_a_clean_build_passes(self):
        s = self.site()
        r = gates.gate_privacy(s.out)
        self.assertEqual((r["status"], r["findings"]), ("pass", []))
        self.assertEqual(structure.itemised(s.bundle()["eda"], {"r2"}, "eda", structure.node_ids(
            {c["r"] for c in s.bundle()["commits"]}, {c["w"] for c in s.bundle()["commits"]}, set())), [])

    def test_the_audit_witnesses_are_public_commits(self):
        s = self.site()
        b = s.bundle()
        self.assertTrue(any(c["witnesses"] for c in b["eda"]["audit"]["checks"]))
        public = {c["h"] for c in b["commits"] if c["r"] not in s.priv and c.get("h")}
        self.assertTrue(all(w in public for c in b["eda"]["audit"]["checks"] for w in c["witnesses"]))

    def test_a_witness_that_is_not_a_public_commit_fails(self):
        s = self.site()
        s.edit_bundle(lambda b: next(c for c in b["eda"]["audit"]["checks"] if c["witnesses"])["witnesses"].append("0" * 12))
        r = gates.gate_privacy(s.out)
        self.assertEqual(r["status"], "fail")
        self.assertIn("bundle.json: eda audit names a commit hash that is not a public commit", r["findings"])
        self.check_clean_messages(r)

    def test_the_hash_of_a_private_commit_fails(self):
        s = self.site()

        def leak(b):
            row = next(c for c in b["commits"] if c["r"] in s.priv)
            row["h"] = "abcdef012345"
            next(c for c in b["eda"]["audit"]["checks"] if c["witnesses"])["witnesses"].append(row["h"])
        s.edit_bundle(leak)
        r = gates.gate_privacy(s.out)
        self.assertEqual(r["status"], "fail")
        self.assertIn("bundle.json: eda audit names a commit hash that is not a public commit", r["findings"])
        self.check_clean_messages(r)

    def test_a_flag_index_outside_the_commits_fails(self):
        for bad in (10 ** 6, -1):
            s = self.site()
            s.edit_bundle(lambda b: b["eda"]["flags"]["by_rule"]["size_high"]["S"].append([bad, 3, 4]))
            r = gates.gate_privacy(s.out)
            self.assertEqual(r["status"], "fail", bad)
            self.assertIn("bundle.json: eda flags refer to a commit index outside the bundle's commits", r["findings"])

    def test_a_private_path_planted_in_a_string_fails(self):
        for planted in ("r2:src/secret.py", "r2/src", 'r2:"src/na\\303\\257ve.md"'):
            s = self.site()
            s.edit_bundle(lambda b: b["eda"]["audit"]["checks"][0].update(note=planted))
            r = gates.gate_privacy(s.out)
            self.assertEqual(r["status"], "fail", planted)
            self.assertTrue([f for f in r["findings"] if "eda" in f and "private repo's file or area" in f], r["findings"])
            self.check_clean_messages(r)

    def test_a_key_that_starts_with_a_private_repo_fails(self):
        for key in ("r2/area", "r2:file.py"):
            s = self.site()
            s.edit_bundle(lambda b: b["eda"]["coverage"]["by_fork"].update({key: 1}))
            r = gates.gate_privacy(s.out)
            self.assertEqual(r["status"], "fail", key)
            self.assertTrue([f for f in r["findings"] if "has a key naming a private repo's file or area" in f], r["findings"])
            self.check_clean_messages(r)

    def test_the_literal_private_marker_as_a_prefix_fails(self):
        for planted in ("<private>/src", "<private>:file.py"):
            s = self.site()
            s.edit_bundle(lambda b: b["eda"]["profile"].update({"extra": [planted]}))
            self.assertEqual(gates.gate_privacy(s.out)["status"], "fail", planted)
            s = self.site()
            s.edit_bundle(lambda b: b["eda"]["profile"].update({planted: 1}))
            self.assertEqual(gates.gate_privacy(s.out)["status"], "fail", planted)

    def test_private_shas_listed_next_to_a_private_repo_fail(self):
        s = self.site()
        s.edit_bundle(lambda b: b["eda"]["concentration"]["repos"][0].update(repo="r2", shas=["abc"]))
        r = gates.gate_privacy(s.out)
        self.assertEqual(r["status"], "fail")
        self.check_clean_messages(r)

    def test_a_private_repo_is_a_node_id_in_the_slices_and_that_passes(self):
        s = self.site()
        slices = {r["slice"] for r in s.bundle()["eda"]["profile"]["records"]}
        self.assertIn("repo:r2", slices)             # an id of the graph's own kind, told apart from a private file by node_ids
        self.assertEqual(gates.gate_privacy(s.out)["status"], "pass")

    def test_a_malformed_section_fails_instead_of_crashing(self):
        s = self.site()
        s.edit_bundle(lambda b: b["eda"].pop("audit"))
        r = gates.gate_privacy(s.out)
        self.assertEqual(r["status"], "fail")
        self.assertIn("bundle.json: the eda section could not be read by the privacy gate", r["findings"])

    def test_private_repos_carry_no_identifying_data_in_the_section(self):
        b = self.site().bundle()
        text = json.dumps(b["eda"])
        for c in self.ds["repos"][2]["commits"][:10]:
            self.assertEqual(c.get("sha"), None)
        self.assertNotIn("r2:", text)
        self.assertNotIn("r2/", text)
        self.assertNotIn("<private>:", text)
        self.assertNotIn("<private>/", text)


class SelfDescriptionTests(unittest.TestCase):
    def test_the_pipeline_has_the_eda_stage_before_profile(self):
        stages = [p["stage"] for p in selfdesc.PIPELINE]
        self.assertEqual(stages.index("eda") + 1, stages.index("profile"))
        st = selfdesc.PIPELINE[stages.index("eda")]
        self.assertEqual((st["entry"], st["inputs"], st["outputs"], st["model"]), ("run", ["dataset.json"], ["eda.json"], None))
        paths = [p.strip() for p in st["module"].split("+")]
        self.assertEqual(paths, ["x3dray/eda.py", "x3dray/looks.py", "x3dray/stats.py"])
        for p in paths:
            self.assertTrue(os.path.exists(os.path.join(REPO, p)), p)
        self.assertIn("12 looks", st["does"])
        self.assertNotIn("12 contrasts", st["does"])
        self.assertIn("eda.json", selfdesc.PIPELINE[stages.index("profile")]["inputs"])

    def test_the_gate_stage_says_the_witness_gate_recomputes_eda(self):
        gate = next(p for p in selfdesc.PIPELINE if p["stage"] == "gates")
        self.assertRegex(gate["does"], r"eda")

    def test_the_new_limits_are_present(self):
        text = "\n".join(selfdesc.LIMITS)
        for needle in ("not probabilities; commits are treated as exchangeable", "UTC author times with no recorded offset",
                       "Slice profiles asked through the eda tool are descriptive and ungraded"):
            self.assertEqual(text.count(needle), 1, needle)

    def test_the_modules_list_includes_the_new_files(self):
        paths = {m["path"] for m in selfdesc.modules()}
        self.assertTrue({"x3dray/eda.py", "x3dray/looks.py", "x3dray/stats.py"} <= paths)

    def test_describe_hashes_the_stage_modules(self):
        ds = standard()
        prof = profile.profile_family(ds, {"pairs": []}, None, None, None, eda=eda_of(ds))
        d = selfdesc.describe(ds, prof, "2026-01-01T00:00:00Z")
        st = next(p for p in d["pipeline"] if p["stage"] == "eda")
        self.assertEqual(set(st["sha256"]), {"x3dray/eda.py", "x3dray/looks.py", "x3dray/stats.py"})
        self.assertGreater(st["lines"], 1000)
        for sid in EDA_IDS:
            self.assertIn(sid, d["signal_code"])


class DataChainTests(unittest.TestCase):
    """The whole data chain without git, node or a font: dataset -> structure -> eda -> profile -> self -> spec -> bundle."""

    def test_the_chain_accepts_eda_end_to_end(self):
        ds = standard()
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            put(os.path.join(data, "dataset.json"), ds)
            put(os.path.join(data, "ties.json"), {"pairs": []})
            con, bri, problems = structure.run(ds)
            self.assertEqual(problems, [])
            put(os.path.join(data, "concepts.json"), con, compact=True)
            put(os.path.join(data, "bridges.json"), bri, compact=True)
            doc, vprobs = eda.run(ds)
            self.assertEqual(vprobs, [])
            put(os.path.join(data, "eda.json"), doc, compact=True)
            prof = profile.profile_family(ds, {"pairs": []}, None, con, bri, eda=doc)
            put(os.path.join(data, "profile.json"), prof)
            description = selfdesc.describe(ds, prof, "2026-01-01T00:00:00Z")
            put(os.path.join(data, "self.json"), description)
            narrative = {"family": prof["family"], "title": "t",
                         "views": [{"id": "overview", "label": "Overview",
                                    "caption": "{eda_findings.declared} declared looks; {eda_audit.checks} audit checks.",
                                    "claim": {"text": "{eda_audit.commits} own commits were audited.", "backs": ["eda_audit"], "basis": "git"}}],
                         "tour": [{"view": "overview", "title": "Overview", "caption": "{eda_profile.p50} lines at the median."}],
                         "questions": ["Which commits are unusually large?"]}
            rendered = spec.render_spec(narrative, prof, description)
            self.assertEqual(rendered["problems"], [])
            self.assertEqual(rendered["views"][0]["caption"], f"12 declared looks; {len(doc['audit']['checks'])} audit checks.")
            put(os.path.join(data, "spec.rendered.json"), rendered)
            put(os.path.join(tmp, "layout.json"), {"ties": {"machine": [], "curated": []}})
            bundle = page.make_bundle(data, tmp)
            self.assertEqual(bundle["eda"], doc)
            ids = [s["id"] for s in bundle["signals"]]
            self.assertTrue(set(EDA_IDS) <= set(ids))
            self.assertEqual(gates.gate_fact(data)["status"], "pass")
            out = os.path.join(tmp, "out")
            put(os.path.join(out, "bundle.json"), bundle)
            put(os.path.join(out, "layout.json"), {"ties": {"machine": [], "curated": []}})
            for name, text in (("index.html", "<title>x</title>"), ("xray.x3d", "<X3D version='4.0'><Scene/></X3D>")):
                with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
                    fh.write(text)
            publication.register(out, [publication.entry(p) for p in ("index.html", "bundle.json", "xray.x3d")])
            self.assertEqual(gates.gate_privacy(out)["status"], "pass")
            w = gates.gate_witness(out, data)
            self.assertEqual(w["status"], "pass", w)
            self.assertIn("check clean", w["summary"])

    def test_the_chain_without_eda_is_the_chain_as_before(self):
        ds = standard()
        con, bri, _ = structure.run(ds)
        prof = profile.profile_family(ds, {"pairs": []}, None, con, bri)
        self.assertFalse(set(EDA_IDS) & {s["id"] for s in prof["signals"]})


class SmokeScriptTests(unittest.TestCase):
    def test_the_smoke_script_runs_the_stage_between_structure_and_profile(self):
        with open(os.path.join(REPO, "experiments", "smoke_synthetic_pipeline.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertRegex(src, r"from x3dray import [^\n]*\beda\b")
        i_struct, i_eda, i_prof = (src.index(m) for m in ('stage("structure"', 'stage("eda"', 'stage("profile"'))
        self.assertLess(i_struct, i_eda)
        self.assertLess(i_eda, i_prof)
        self.assertIn("eda=eda_out", src)
        self.assertRegex(src, r'data / "eda\.json"')

    def test_the_eda_step_of_the_smoke_script_on_a_two_repo_dataset(self):
        """The two fixture repos of the smoke script have 8 commits each: the stage must still run clean on so small a family."""
        ds = dataset(seed=7, repos=2, per_repo=8)
        out, problems = eda.run(ds)
        self.assertEqual(problems, [])
        con, bri, _ = structure.run(ds)
        prof = profile.profile_family(ds, {"pairs": []}, None, con, bri, eda=out)
        self.assertTrue(set(EDA_IDS) <= {s["id"] for s in prof["signals"]})

    @unittest.skipUnless(os.environ.get("X3DRAY_XITE") and os.environ.get("X3DRAY_FONT"), "set X3DRAY_XITE and X3DRAY_FONT to run the real smoke script")
    def test_the_smoke_script_end_to_end(self):
        sys.path.insert(0, os.path.join(REPO, "experiments"))
        import smoke_synthetic_pipeline as smoke
        from pathlib import Path
        result = smoke.run(Path(os.environ["X3DRAY_XITE"]), Path(os.environ["X3DRAY_FONT"]))
        self.assertTrue(result["ok"], result.get("error"))
        self.assertIn("eda", [s["stage"] for s in result["stages"]])


class ProseTests(unittest.TestCase):
    def test_new_text_avoids_the_forbidden_words(self):
        ds = standard()
        prof = profile.profile_family(ds, {"pairs": []}, None, None, None, eda=eda_of(ds))
        for s in prof["signals"]:
            if s["id"] in EDA_IDS:
                self.assertIsNone(re.search(r"significan|probability|p-value|because|caused|due to", s["text"], re.I), s["text"])
        for line in selfdesc.LIMITS[-3:]:
            self.assertIsNone(re.search(r"significan|probability|p-value|because|caused|due to", line, re.I), line)


if __name__ == "__main__":
    unittest.main()
