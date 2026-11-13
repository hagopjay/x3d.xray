"""The eda stage: rows, audit, profile, rhythm, concentration, flags, run and verify.

Every section is checked against a slow, independent definition (pair loops, plain filtering, trying every start), and the
whole file is checked for privacy, determinism, planted faults, empty input and size. In-memory datasets only."""

import ast
import collections
import copy
import hashlib
import inspect
import itertools
import json
import os
import random
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eda_support import (HARVEST_EPOCH, bundle_commit_rows, minimal_bundle, synthetic_dataset)  # noqa: E402
from x3dray import eda, gitio, page, stats, structure  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def cm(t, add=1, dele=0, files=1, who="Alice", cls="you", merge=False, default=True):
    return {"t": t, "who": who, "cls": cls, "files": files, "add": add, "del": dele, "merge": merge,
            "ai_trailer": cls == "ai", "sessions": [], "default": default, "upstream": False}


def make_ds(repos: dict, private=(), **extra):
    """repos maps a name to a list of commit dicts; public commits get a sha and oid from their position."""
    out = []
    for name, commits in repos.items():
        pv = name in private
        cs = []
        for k, c in enumerate(commits):
            c = dict(c)
            if not pv:
                oid = hashlib.sha1(f"{name}|{k}".encode()).hexdigest()
                c.update(sha=oid[:12], oid=oid, subject=f"s{k}", kinds={}, paths=[f"p{j}" for j in range(c["files"])])
            cs.append(c)
        out.append({"name": name, "present": True, "visibility": "private" if pv else "public", "kind": "own",
                    "tree_measured": not pv, "tree": {}, "files": [], "commits": cs})
    ds = {"family": "t", "title": "t", "harvested_at": "2026-02-01T00:00:00Z", "lineage": [],
          "identities": [{"label": "Alice", "class": "you"}, {"label": "Bot-AI", "class": "ai"}], "repos": out}
    ds.update(extra)
    return ds


def walk(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from walk(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from walk(v)
    else:
        yield x


def checks_by_id(a):
    return {c["id"]: c for c in a["checks"]}


# ------------------------------------------------------------------ 1 rows
class RowTests(unittest.TestCase):
    def test_rows_equal_the_bundle_projection_with_equal_t_across_repos(self):
        ds = synthetic_dataset(seed=4, repos=3, per_repo=30)
        # equal t across repos and twice inside one repo: the (t, name) sort must order them like make_bundle
        for r in ds["repos"][1:]:
            for k in range(5):
                r["commits"][k]["t"] = ds["repos"][0]["commits"][k]["t"]
        ds["repos"][0]["commits"][7]["t"] = ds["repos"][0]["commits"][6]["t"]
        ds["repos"][0]["commits"][7]["add"] = 777
        ds["repos"][2]["commits"].append({**ds["repos"][2]["commits"][0], "upstream": True})
        rows, bundle = eda.indexed_rows(ds), bundle_commit_rows(ds)
        self.assertEqual(len(rows), len(bundle))
        for row, b in zip(rows, bundle):
            self.assertEqual({k: row[k] for k in ("i", "r", "t", "c", "w", "a", "d", "f", "m", "b", "tr")},
                             {k: b[k] for k in ("i", "r", "t", "c", "w", "a", "d", "f", "m", "b", "tr")})
        self.assertEqual([r["i"] for r in rows], list(range(len(rows))))
        self.assertTrue(any(rows[k]["t"] == rows[k + 1]["t"] and rows[k]["r"] != rows[k + 1]["r"] for k in range(len(rows) - 1)))

    def test_the_helper_copies_the_real_construction(self):
        src = inspect.getsource(page.make_bundle)
        self.assertIn('key=lambda rc: (rc[1]["t"], rc[0])', src)
        self.assertIn('if not c.get("upstream")', src)
        self.assertIn('"b": 1 if c.get("default", True) else 0', src)

    def test_private_rows_carry_no_identifier(self):
        ds = synthetic_dataset(seed=2, repos=3, per_repo=20, private=("r1",))
        for c in ds["repos"][1]["commits"]:           # a dataset that leaked them: the rows must still drop them
            c.update(sha="a" * 12, oid="b" * 40, paths=["secret/x"], kinds={"code": 1}, subject="secret")
        for row in eda.indexed_rows(ds):
            self.assertEqual(row["pv"], row["r"] == "r1")
            if row["pv"]:
                self.assertEqual((row["sha"], row["oid"], row["paths"], row["kinds"]), (None, None, None, None))
            else:
                self.assertTrue(row["sha"] and row["oid"] and row["paths"] is not None)

    def test_class_labels(self):
        ds = {"identities": [{"label": "Zed", "class": "you"}, {"label": "Other", "class": "you"}, {"label": "Bot", "class": "ai"}]}
        self.assertEqual(eda.class_labels(ds), {"you": "Zed", "ai": "Bot", "bot": "bot", "other": "other", "upstream": "upstream"})
        self.assertEqual(eda.class_labels({})["you"], "you")

    def test_thresholds_are_the_declared_constants(self):
        t = eda.THRESHOLDS
        for k, v in dict(MIN_PROFILE_N=10, MIN_SIDE=20, MIN_HALF=8, MIN_QUIET_N=120, MIN_BURST_N=20, MIN_FLAG_STRATUM=20,
                         MIN_CONC_FILES=10, MASS_FILES=15, FLAG_CAP=200, SIZE_BIN_CAP=16, GAP_BIN_CAP=19, TS_MIN=1112832000,
                         CLASS_RESIDUAL_PERMILLE=250, SAME_SECOND_RUN=5).items():
            self.assertEqual(t[k], v, k)
        self.assertEqual(tuple(t["BURST_GAPS"]), (3600, 7200, 14400))
        self.assertEqual(t["GAP_BIN_CAP"], 19)
        self.assertEqual(t["GAP_BIN_UNIT"], 60)
        self.assertEqual(eda.TS_MIN, 1112832000)


# ------------------------------------------------------------------ 2 audit
def pair_oracle(ds):
    """dup_oid, dup_fingerprint and same_second by O(n^2) pair loops over the raw dataset, no hashing and no sorting."""
    items = []
    for repo in ds["repos"]:
        for c in repo["commits"]:
            if not c.get("upstream"):
                pub = repo.get("visibility") != "private"
                items.append({"repo": repo["name"], "pub": pub, "c": c, "oid": c.get("oid") if pub else None,
                              "sha": (c.get("sha") or (c.get("oid") or "")[:12])[:12] if pub else ""})
    n = len(items)
    dup = set()
    for x in range(n):
        for y in range(n):
            if x == y or not (items[x]["pub"] and items[y]["pub"]):
                continue
            a, b = items[x], items[y]
            if (a["oid"] and a["oid"] == b["oid"] and a["repo"] != b["repo"]) or (
                    a["repo"] == b["repo"] and a["sha"] and a["sha"] == b["sha"]):
                dup.add(x)

    def key(it):
        c = it["c"]
        return (c["t"], c["cls"], c["who"], c["add"], c["del"], c["files"], bool(c.get("merge")))

    cand = [x for x in range(n) if x not in dup and not items[x]["c"].get("merge")
            and not (items[x]["c"]["files"] == 0 and items[x]["c"]["add"] + items[x]["c"]["del"] == 0)]
    fp, likely = set(), False
    for x in cand:
        same = [y for y in cand if key(items[y]) == key(items[x])]
        if any(items[y]["repo"] != items[x]["repo"] for y in same):
            fp.add(x)
            if all(items[y]["pub"] and items[y]["oid"] for y in same) and all(
                    items[a]["oid"] != items[b]["oid"] for a, b in itertools.combinations(same, 2)):
                likely = True
    mult = {x: sum(1 for y in range(n) if items[y]["repo"] == items[x]["repo"] and items[y]["c"]["t"] == items[x]["c"]["t"])
            for x in range(n)}
    ss = {x for x in range(n) if mult[x] >= 2} if max(mult.values(), default=0) >= 5 else set()

    def wit(ix):
        return sorted({items[x]["sha"] for x in ix if items[x]["pub"] and items[x]["sha"]})[:5]
    return {"dup_oid": (len(dup), wit(dup)), "dup_fingerprint": (len(fp), wit(fp), "L" if likely else "S"),
            "same_second": (len(ss), wit(ss))}


def mutate(rng, ds):
    """Plant random duplicates: shared oids, repeated shas, copied fingerprints, same-second runs."""
    flat = [(r, c) for r in ds["repos"] for c in r["commits"]]
    for _ in range(rng.randint(0, 4)):
        (_, x), (_, y) = rng.choice(flat), rng.choice(flat)
        if "oid" in x and "oid" in y:
            y["oid"], y["sha"] = x["oid"], x["sha"]
    for _ in range(rng.randint(0, 3)):
        (_, x), (_, y) = rng.choice(flat), rng.choice(flat)
        if "sha" in x and "sha" in y:
            y["sha"] = x["sha"]
    for _ in range(rng.randint(0, 4)):
        (rx, x), (ry, y) = rng.choice(flat), rng.choice(flat)
        if rx is not ry:
            for k in ("t", "cls", "who", "add", "del", "files", "merge"):
                y[k] = x[k]
    if rng.random() < 0.6:
        r = rng.choice(ds["repos"])
        k = min(len(r["commits"]), rng.randint(2, 7))
        for c in r["commits"][:k]:
            c["t"] = r["commits"][0]["t"]


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.ds = synthetic_dataset(seed=1, repos=3, per_repo=60, plant="defects")
        self.rows = eda.indexed_rows(self.ds)
        self.audit = eda.audit(self.rows, self.ds)
        self.checks = checks_by_id(self.audit)

    def test_planted_defects_have_exact_counts_and_grades(self):
        want = {"ts_range": (2, "C"), "dup_oid": (2, "C"), "dup_fingerprint": (2, "L"), "same_second": (7, "S"),
                "empty_commit": (1, "S"), "merge_contract": (1, "C")}
        for cid, (count, grade) in want.items():
            c = self.checks[cid]
            self.assertEqual((c["status"], c["count"], c["grade"]), ("flag", count, grade), cid)
            self.assertEqual(sum(e["count"] for e in c["by_repo"]), count)
        for cid in ("stat_free_change", "class_residual"):
            self.assertEqual((self.checks[cid]["status"], self.checks[cid]["count"], self.checks[cid]["grade"]), ("pass", 0, None))
        self.assertEqual(list(self.checks), ["ts_range", "dup_oid", "dup_fingerprint", "same_second", "empty_commit",
                                             "stat_free_change", "merge_contract", "class_residual"])

    def test_clean_datasets_flag_nothing_at_c(self):
        for seed in range(1, 9):
            ds = synthetic_dataset(seed=seed, repos=3, per_repo=50, private=("r2",) if seed % 2 else ())
            for c in eda.audit(eda.indexed_rows(ds), ds)["checks"]:
                self.assertNotEqual(c["grade"], "C", (seed, c["id"]))
                self.assertEqual(c["status"], "pass", (seed, c["id"]))

    def test_oracle_matches_on_100_random_datasets(self):
        flagged_somewhere = collections.Counter()
        for seed in range(100):
            rng = random.Random(seed)
            repos = rng.randint(2, 4)
            names = [f"r{k}" for k in range(repos)]
            ds = synthetic_dataset(seed=seed, repos=repos, per_repo=rng.randint(6, 16),
                                   private=tuple(n for n in names if rng.random() < 0.3))
            for r in ds["repos"]:                       # a private repo that leaked ids must still count as counts only
                if r["visibility"] == "private":
                    for c in r["commits"]:
                        oid = hashlib.sha1(f"leak{seed}{c['t']}".encode()).hexdigest()
                        c.update(oid=oid, sha=oid[:12])
            mutate(rng, ds)
            got = checks_by_id(eda.audit(eda.indexed_rows(ds), ds))
            want = pair_oracle(ds)
            for cid in ("dup_oid", "dup_fingerprint", "same_second"):
                self.assertEqual(got[cid]["count"], want[cid][0], (seed, cid))
                self.assertEqual(got[cid]["witnesses"], want[cid][1], (seed, cid))
                self.assertEqual(got[cid]["status"], "flag" if want[cid][0] else "pass", (seed, cid))
                if want[cid][0]:
                    flagged_somewhere[cid] += 1
            if want["dup_fingerprint"][0]:
                self.assertEqual(got["dup_fingerprint"]["grade"], want["dup_fingerprint"][2], seed)
        for cid in ("dup_oid", "dup_fingerprint", "same_second"):
            self.assertGreater(flagged_somewhere[cid], 10, cid)

    def test_fingerprint_grade_s_when_a_private_row_is_involved(self):
        a = [cm(1600000000 + 100 * k, add=3 + k) for k in range(12)]
        b = [dict(c) for c in a]
        ds = make_ds({"pub": a, "priv": b}, private=("priv",))
        chk = checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["dup_fingerprint"]
        self.assertEqual((chk["count"], chk["grade"]), (24, "S"))
        self.assertEqual(chk["witnesses"], sorted(c["sha"] for c in ds["repos"][0]["commits"])[:5])

    def test_fingerprint_ignores_merges_empty_commits_and_rows_already_counted(self):
        base = 1600000000
        pub1 = [cm(base + k, add=2 + k) for k in range(10)]
        pub2 = [dict(c) for c in pub1]
        pub1.append(cm(base + 50, merge=True, add=0, dele=0, files=0))
        pub2.append(cm(base + 50, merge=True, add=0, dele=0, files=0))
        pub1.append(cm(base + 60, add=0, dele=0, files=0))
        pub2.append(cm(base + 60, add=0, dele=0, files=0))
        ds = make_ds({"x": pub1, "y": pub2})
        for k in range(10):                                  # the same oid in both repos: dup_oid takes the rows
            ds["repos"][1]["commits"][k].update(oid=ds["repos"][0]["commits"][k]["oid"], sha=ds["repos"][0]["commits"][k]["sha"])
        got = checks_by_id(eda.audit(eda.indexed_rows(ds), ds))
        self.assertEqual(got["dup_oid"]["count"], 20)
        self.assertEqual(got["dup_fingerprint"]["count"], 0)

    def test_same_second_needs_a_run_of_five(self):
        base = 1600000000
        for run, flagged in ((4, 0), (5, 5)):
            cs = [cm(base + 100 * k, add=k + 1) for k in range(12)]
            for c in cs[:run]:
                c["t"] = base - 5
            ds = make_ds({"x": cs})
            chk = checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["same_second"]
            self.assertEqual(chk["count"], flagged)
            self.assertIn(f"longest run {run}", chk["note"])
        # two pairs in two repos are not a run
        ds = make_ds({"x": [cm(base, add=1), cm(base, add=2)] + [cm(base + 10 * k, add=k + 3) for k in range(1, 10)],
                      "y": [cm(base, add=5), cm(base, add=6)] + [cm(base + 10 * k, add=k + 20) for k in range(1, 10)]})
        self.assertEqual(checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["same_second"]["count"], 0)

    def test_stat_free_and_empty_commits_are_told_apart(self):
        base = 1600000000
        ds = make_ds({"x": [cm(base + k) for k in range(10)] + [cm(base + 99, add=0, dele=0, files=2), cm(base + 98, add=0, dele=0, files=0)]})
        got = checks_by_id(eda.audit(eda.indexed_rows(ds), ds))
        self.assertEqual((got["stat_free_change"]["count"], got["empty_commit"]["count"]), (1, 1))

    def test_ts_range_boundaries(self):
        ok = [cm(eda.TS_MIN + k) for k in range(8)]
        ds = make_ds({"x": ok + [cm(eda.TS_MIN - 1, add=9), cm(HARVEST_EPOCH, add=10), cm(HARVEST_EPOCH + 1, add=11)]})
        chk = checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["ts_range"]
        self.assertEqual(chk["count"], 2)
        ds["harvested_at"] = "not a date"
        chk = checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["ts_range"]
        self.assertEqual(chk["count"], 1)
        self.assertIn("unparsable", chk["note"])

    def test_ts_range_without_a_harvest_time_is_unavailable_not_a_pass(self):
        # Every commit dated 2100: only the "after the harvest" half could say so, and it cannot run without a harvest time.
        future = 4102444800
        for stamp in (None, "", "2026-01-04", "not a date", "2026-01-04T00-00-00"):
            with self.subTest(harvested_at=stamp):
                ds = make_ds({"x": [cm(future + k, add=k + 1) for k in range(12)]})
                ds["harvested_at"] = stamp
                chk = checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["ts_range"]
                self.assertEqual((chk["status"], chk["count"], chk["grade"]), ("unavailable", 0, None))
                self.assertIn("skipped", chk["note"])
                out, problems = eda.run(ds)
                self.assertEqual(problems, [])
                self.assertEqual(checks_by_id(out["audit"])["ts_range"]["status"], "unavailable")
        ds = make_ds({"x": [cm(future + k, add=k + 1) for k in range(12)]})
        self.assertEqual(checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["ts_range"]["status"], "flag")      # with a harvest time it is caught
        # the half that can run still flags without a harvest time
        ds = make_ds({"x": [cm(eda.TS_MIN - 1 - k, add=k + 1) for k in range(3)]})
        ds["harvested_at"] = None
        chk = checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["ts_range"]
        self.assertEqual((chk["status"], chk["count"]), ("flag", 3))
        # a clean history with a harvest time passes
        ds = make_ds({"x": [cm(1600000000 + k, add=k + 1) for k in range(12)]})
        self.assertEqual(checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["ts_range"]["status"], "pass")
        # verify re-derives the status: a stage that said "pass" would be reported
        ds = make_ds({"x": [cm(future + k, add=k + 1) for k in range(12)]})
        ds["harvested_at"] = None
        out, _ = eda.run(ds)
        for chk in out["audit"]["checks"]:
            if chk["id"] == "ts_range":
                chk["status"] = "pass"
        self.assertIn("audit ts_range: status differs", eda.verify(out, ds))

    def test_class_residual_flags_and_lists_small_repos_as_unavailable(self):
        base = 1600000000
        mix = [cm(base + k, add=k + 1, cls="other", who="Zed") for k in range(3)] + [cm(base + 10 + k, add=k + 20) for k in range(9)]
        small = [cm(base + k, add=k + 1, cls="other", who="Zed") for k in range(3)]
        ds = make_ds({"mix": mix, "small": small})
        chk = checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["class_residual"]
        self.assertEqual((chk["status"], chk["count"], chk["grade"]), ("flag", 3, "S"))
        self.assertEqual(chk["by_repo"], [{"repo": "mix", "count": 3}])
        self.assertIn("1 repos with fewer than 10 commits unavailable", chk["note"])
        ds = make_ds({"small": small})
        chk = checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["class_residual"]
        self.assertEqual((chk["status"], chk["count"]), ("unavailable", 0))
        mix2 = [cm(base + k, add=k + 1, cls="other", who="Zed") for k in range(2)] + [cm(base + 10 + k, add=k + 20) for k in range(10)]
        ds = make_ds({"mix": mix2})                    # 2 of 12 = 167 permille: below the line
        self.assertEqual(checks_by_id(eda.audit(eda.indexed_rows(ds), ds))["class_residual"]["status"], "pass")

    def test_witnesses_are_public_sha12_only(self):
        ds = synthetic_dataset(seed=3, repos=3, per_repo=40, private=("r2",), plant="defects")
        for c in ds["repos"][2]["commits"]:
            c.update(sha="deadbeef0000", oid="deadbeef0000" + "0" * 28)
        a = eda.audit(eda.indexed_rows(ds), ds)
        public = {c["sha"] for r in ds["repos"][:2] for c in r["commits"]}
        for chk in a["checks"]:
            self.assertLessEqual(len(chk["witnesses"]), 5)
            for w in chk["witnesses"]:
                self.assertIn(w, public)
                self.assertEqual(len(w), 12)
        self.assertNotIn("deadbeef0000", json.dumps(a))

    def test_coverage_matrix(self):
        ds = synthetic_dataset(seed=2, repos=3, per_repo=130, private=("r2",))
        ds["repos"][1]["tree_measured"] = False
        ds["repos"][0]["history_scope"] = "all refs"
        a = eda.audit(eda.indexed_rows(ds), ds)
        cov = {e["repo"]: e for e in a["coverage"]}
        self.assertEqual([e["n"] for e in a["coverage"]], sorted((e["n"] for e in a["coverage"]), reverse=True))
        self.assertEqual(cov["r2"]["visibility"], "private")
        self.assertEqual((cov["r2"]["n_with_paths"], cov["r2"]["n_usable"], cov["r2"]["tree"]), (None, None, "counts only (private)"))
        self.assertEqual((cov["r0"]["tree"], cov["r1"]["tree"]), ("measured", "not harvested"))
        self.assertEqual(cov["r0"]["history_scope"], "all refs")
        self.assertIsNone(cov["r1"]["history_scope"])
        for name in ("r0", "r1"):
            commits = [c for c in ds["repos"][int(name[1])]["commits"]]
            nm = [c for c in commits if not c["merge"]]
            self.assertEqual(cov[name]["n"], len(commits))
            self.assertEqual(cov[name]["n_nonmerge"], len(nm))
            self.assertEqual(cov[name]["n_default"], sum(1 for c in nm if c["default"]))
            self.assertEqual(cov[name]["n_with_paths"], sum(1 for c in nm if c["paths"]))
            self.assertEqual(cov[name]["n_usable"], sum(1 for c in nm if c["paths"] and c["files"] <= structure.MAX_FILES))
            self.assertEqual(cov[name]["eligible"], {"profile": True, "rhythm": len(nm) >= 120, "bursts": True, "contrast": True})

    def test_limitations_are_derived_from_the_source(self):
        ds = synthetic_dataset(seed=1, repos=2, per_repo=20)
        ds["repos"][0]["commits"][0]["upstream"] = True
        a = eda.audit(eda.indexed_rows(ds), ds)
        ids = [x["id"] for x in a["limitations"]]
        self.assertEqual(ids, ["author_time_utc", "renames_not_followed", "upstream_excluded", "private_counts_only", "exchangeable"])
        text = {x["id"]: x["text"] for x in a["limitations"]}
        self.assertIn("1 commits flagged as upstream", text["upstream_excluded"])
        self.assertEqual(text["exchangeable"], "Contrasts treat commits as exchangeable under the null; commits of one session "
                         "or day are correlated, so permutation scores are anti-conservative.")
        self.assertEqual(text["author_time_utc"], "Times are author times in UTC; the harvest records no timezone offset, "
                         "so local hours are unknown.")
        with mock.patch.object(gitio, "LOG_FORMAT_NUL", gitio.LOG_FORMAT_NUL + "%x00%ai"):
            a2 = eda.audit(eda.indexed_rows(ds), ds)
        self.assertNotIn("author_time_utc", [x["id"] for x in a2["limitations"]])
        self.assertNotIn("could not be derived", a2["scope"])
        with mock.patch.object(gitio, "log_with_numstat", lambda *a, **k: None):
            a3 = eda.audit(eda.indexed_rows(ds), ds)
        self.assertNotIn("renames_not_followed", [x["id"] for x in a3["limitations"]])
        with mock.patch.object(inspect, "getsource", side_effect=OSError("no source")):
            a4 = eda.audit(eda.indexed_rows(ds), ds)
        self.assertNotIn("renames_not_followed", [x["id"] for x in a4["limitations"]])
        self.assertIn("limitation renames_not_followed could not be derived", a4["scope"])


# ------------------------------------------------------------------ 3 profile
FIXTURE = [  # (repo, t, add, del, files, merge, default, cls)
    ("a", 0, 3, 0, 1, False, True, "you"),
    ("b", 60, 10, 6, 2, False, True, "you"),
    ("a", 180, 1, 0, 1, False, True, "you"),
    ("b", 240, 100, 28, 20, False, True, "ai"),
    ("a", 600, 7, 1, 3, False, True, "you"),
    ("b", 660, 0, 0, 0, True, True, "you"),
    ("a", 1200, 2, 2, 2, False, True, "you"),
    ("b", 1260, 60, 4, 5, False, False, "you"),
    ("a", 3600, 12, 20, 4, False, True, "you"),
    ("b", 3660, 1, 1, 1, False, True, "you"),
    ("a", 7200, 50, 50, 9, False, True, "ai"),
    ("b", 7260, 6, 0, 1, False, True, "you"),
]


def fixture_ds():
    repos = {"a": [], "b": []}
    for name, t, a, d, f, m, df, cls in FIXTURE:
        repos[name].append(cm(t, a, d, f, who="Alice" if cls == "you" else "Bot-AI", cls=cls, merge=m, default=df))
    return make_ds(repos)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.ds = fixture_ds()
        self.rows = eda.indexed_rows(self.ds)
        self.prof = eda.profile(self.rows, self.ds)
        self.rec = {(r["slice"], r["fork"], r["measure"]): r for r in self.prof["records"]}

    def test_family_lines_known_answers(self):
        r = self.rec[("family", "all", "lines")]
        self.assertEqual((r["available"], r["members"], r["n"], r["sum"], r["min"], r["max"]), (True, 12, 12, 364, 0, 128))
        self.assertEqual([r[k] for k in ("p10", "p25", "p50", "p75", "p90", "p99")], [1, 2, 6, 32, 100, 128])
        self.assertEqual(r["mad"], 5)
        self.assertEqual(r["bins"], [1, 1, 2, 2, 1, 1, 1, 2, 1] + [0] * 8)
        self.assertEqual(r["bin_rule"], {"unit": 1, "cap": 16})
        self.assertEqual((r["gini_permille"], r["top_decile_permille"]), (668, 626))
        self.assertEqual(self.prof["measure_defs"], {"lines": "add+del", "files": "files changed", "gap_s": "seconds since the previous member of the "
                         "slice, ordered by (t,i); the first member has none"})

    def test_gaps_exclude_the_first_member_and_interleave_repos(self):
        r = self.rec[("family", "all", "gap_s")]
        self.assertEqual((r["members"], r["n"], r["sum"]), (12, 11, 7260))
        self.assertEqual([r[k] for k in ("min", "p10", "p25", "p50", "p75", "p90", "p99", "max")], [60, 60, 60, 60, 540, 2340, 3540, 3540])
        self.assertEqual(r["bins"][:8], [0, 6, 1, 1, 1, 0, 2, 0])
        self.assertEqual(r["bin_rule"], {"unit": 60, "cap": 19})
        self.assertEqual(len(r["bins"]), 20)
        nm = self.rec[("family", "no_merge", "gap_s")]
        self.assertEqual((nm["members"], nm["n"], nm["sum"]), (11, 10, 7260))

    def test_merge_lands_in_bin_zero_under_all_and_is_absent_under_no_merge(self):
        self.assertEqual(self.rec[("family", "all", "lines")]["bins"][0], 1)
        self.assertEqual(self.rec[("family", "all", "files")]["bins"][0], 1)
        nm = self.rec[("family", "no_merge", "lines")]
        self.assertEqual((nm["members"], nm["bins"][0], nm["sum"]), (11, 0, 364))

    def test_fork_memberships(self):
        got = {f: self.rec[("family", f, "lines")]["members"] for f in stats.FORKS}
        self.assertEqual(got, {"all": 12, "no_merge": 11, "default_branch": 10, "no_mass": 10})
        self.assertEqual(self.rec[("family", "no_mass", "lines")]["max"], 100)       # the 20-file commit is out
        self.assertEqual(self.rec[("family", "default_branch", "lines")]["max"], 128)  # the non-default 64-line one is out

    def test_small_slices_are_unavailable_never_zero(self):
        for key, r in self.rec.items():
            if r["members"] < 10:
                self.assertFalse(r["available"], key)
                self.assertEqual(r["reason"], "fewer than 10 commits in this slice and fork")
                self.assertNotIn("n", r)
                self.assertNotIn("bins", r)
            else:
                self.assertTrue(r["available"], key)
                self.assertIsNone(r["reason"])
        self.assertEqual(self.rec[("class:ai", "all", "lines")]["members"], 2)
        self.assertEqual(self.rec[("class:you", "all", "lines")]["members"], 10)
        self.assertTrue(self.rec[("class:you", "all", "lines")]["available"])
        self.assertFalse(self.rec[("class:you", "no_merge", "lines")]["available"])      # 9 outside merges
        self.assertEqual(self.rec[("repo:a", "no_merge", "gap_s")]["members"], 6)

    def test_slice_and_fork_layout(self):
        slices = [r["slice"] for r in self.prof["records"]]
        order = list(dict.fromkeys(slices))
        self.assertEqual(order, ["family", "class:you", "class:ai", "repo:a", "repo:b"])
        for r in self.prof["records"]:
            if r["slice"].startswith("repo:"):
                self.assertEqual(r["fork"], "no_merge")
        self.assertEqual(len(self.prof["records"]), (1 + 2) * 4 * 3 + 2 * 1 * 3)
        self.assertEqual([r["measure"] for r in self.prof["records"][:3]], ["lines", "files", "gap_s"])
        self.assertEqual([r["fork"] for r in self.prof["records"][:12:3]], list(stats.FORKS))

    def test_repo_slices_sorted_by_commits_then_name_and_private_included(self):
        ds = make_ds({"zz": [cm(1600000000 + k, add=k + 1) for k in range(15)], "aa": [cm(1600000100 + k, add=k + 1) for k in range(12)],
                      "bb": [cm(1600000200 + k, add=k + 1) for k in range(12)]}, private=("zz",))
        recs = eda.profile(eda.indexed_rows(ds), ds)["records"]
        self.assertEqual(list(dict.fromkeys(r["slice"] for r in recs if r["slice"].startswith("repo:"))),
                         ["repo:zz", "repo:aa", "repo:bb"])
        zz = next(r for r in recs if r["slice"] == "repo:zz" and r["measure"] == "lines")
        self.assertTrue(zz["available"])

    def test_values_match_stats_summarize_on_random_datasets(self):
        for seed in range(6):
            ds = synthetic_dataset(seed=seed, repos=3, per_repo=70)
            rows = eda.indexed_rows(ds)
            recs = {(r["slice"], r["fork"], r["measure"]): r for r in eda.profile(rows, ds)["records"]}
            for fork in stats.FORKS:
                mem = [r for r in rows if stats.in_fork(r, fork)]
                want = stats.summarize([r["a"] + r["d"] for r in mem], 16)
                got = {k: v for k, v in recs[("family", fork, "lines")].items() if k in want}
                self.assertEqual(got, want)
                gaps = [g for _, g in stats.gap_pairs(mem)]
                self.assertEqual({k: v for k, v in recs[("family", fork, "gap_s")].items() if k in want}, stats.summarize(gaps, 19, 60))


# ------------------------------------------------------------------ 4 rhythm
def hour_rows(hours, n, base_day=19000):
    """n no-merge commits whose UTC hours cycle through `hours`, a day apart (so gaps are long)."""
    return [cm(base_day * 86400 + k * 86400 + hours[k % len(hours)] * 3600 + (k * 97) % 3600, add=k + 1) for k in range(n)]


def rows_of(commits):
    return eda.indexed_rows(make_ds({"x": commits}))


class RhythmTests(unittest.TestCase):
    def fam(self, commits):
        return eda.rhythm(rows_of(commits))["slices"][0]

    def test_uniform_hours_have_no_quiet_window(self):
        s = self.fam(hour_rows(list(range(24)), 240))
        self.assertEqual(s["hours"], [10] * 24)
        self.assertTrue(s["quiet"]["available"])
        self.assertLess(s["quiet"]["run"], 4)
        self.assertEqual(s["quiet"]["quiet_hours"], [])

    def test_planted_night_gap_is_found(self):
        s = self.fam(hour_rows(list(range(8, 23)), 240))
        q = s["quiet"]
        self.assertGreaterEqual(q["run"], 8)
        self.assertIn(q["start_hour"], (22, 23, 0))
        self.assertEqual((q["run"], q["start_hour"], q["quiet_hours"]), (9, 23, [0, 1, 2, 3, 4, 5, 6, 7, 23]))
        self.assertIn("UTC", q["threshold"])

    def test_quiet_needs_120_commits(self):
        s = self.fam(hour_rows(list(range(8, 23)), 119))
        self.assertFalse(s["quiet"]["available"])
        self.assertIn("120", s["quiet"]["reason"])
        self.assertIsNone(s["quiet"]["run"])
        self.assertTrue(self.fam(hour_rows(list(range(8, 23)), 120))["quiet"]["available"])

    def test_circular_run_matches_trying_all_24_starts(self):
        rng = random.Random(7)
        for case in range(500):
            kind = case % 4
            if kind == 0:
                hist = [rng.randint(0, 40) for _ in range(24)]
            elif kind == 1:
                hist = [rng.choice([0, 0, 0, 30]) for _ in range(24)]
            elif kind == 2:
                hist = [0 if rng.random() < 0.7 else rng.randint(1, 3) for _ in range(24)]
            else:
                hist = [rng.choice([0, 1]) * 1000 for _ in range(24)]
            n = sum(hist) or 1
            quiet = [96 * h <= n for h in hist]
            best, start = 0, None
            for s in range(24):
                length = 0
                while length < 24 and quiet[(s + length) % 24]:
                    length += 1
                if length > best:
                    best, start = length, s
            got = eda._quiet(hist, n)
            self.assertEqual((got["run"], got["start_hour"]), (best, start), hist)
            self.assertEqual(got["quiet_hours"], [x for x in range(24) if quiet[x]])

    def test_whole_circle_quiet_is_a_run_of_24(self):
        got = eda._quiet([0] * 24, 5)
        self.assertEqual((got["run"], got["start_hour"]), (24, 0))

    def clusters(self, sizes, gap_between, step=600):
        out, t = [], 1600000000
        for si, size in enumerate(sizes):
            for k in range(size):
                out.append(cm(t, add=len(out) + 1))
                t += step
            t += gap_between - step
        return out

    def test_three_clusters_give_three_bursts_and_stable(self):
        b = self.fam(self.clusters([7, 7, 7], 50000))["bursts"]
        self.assertTrue(b["available"])
        self.assertEqual({k: v["n_bursts"] for k, v in b["by_gap"].items()}, {"3600": 3, "7200": 3, "14400": 3})
        v = b["by_gap"]["7200"]
        self.assertEqual((v["median_commits"], v["largest_commits"], v["largest_duration_s"], v["single_commit_permille"]), (7, 7, 3600, 0))
        self.assertTrue(b["stable"])

    def test_burst_numbers_for_uneven_clusters(self):
        commits = self.clusters([9, 6, 1, 5], 50000)
        b = self.fam(commits)["bursts"]["by_gap"]["3600"]
        self.assertEqual((b["n_bursts"], b["median_commits"], b["largest_commits"], b["largest_duration_s"], b["single_commit_permille"]),
                         (4, 5, 9, 4800, 250))

    def test_boundary_gaps_flip_the_counts_and_stability(self):
        # n_bursts is non-increasing in theta, so stability (2*max <= 3*min) depends on the 3600 and 14400 counts only:
        # a gap of exactly 7200 changes the 7200 count but cannot change `stable`; the flips sit at 3600 and 14400.
        def run(gap):
            return self.fam(self.clusters([10, 10], gap))["bursts"]

        for gap, counts, stable in ((3600, (1, 1, 1), True), (3601, (2, 1, 1), False),
                                    (7200, (2, 1, 1), False), (7201, (2, 2, 1), False),
                                    (14400, (2, 2, 1), False), (14401, (2, 2, 2), True)):
            b = run(gap)
            self.assertEqual(tuple(b["by_gap"][k]["n_bursts"] for k in ("3600", "7200", "14400")), counts, gap)
            self.assertEqual(b["stable"], stable, gap)

    def test_bursts_need_20_commits_and_repo_slices_have_no_hour_histogram(self):
        s = self.fam(self.clusters([7, 6], 50000))
        self.assertFalse(s["bursts"]["available"])
        self.assertEqual(s["bursts"]["by_gap"], {})
        self.assertIsNone(s["bursts"]["stable"])
        out = eda.rhythm(rows_of(self.clusters([10, 10, 10], 50000)))["slices"]
        self.assertEqual([x["slice"] for x in out], ["family", "class:you", "repo:x"])
        self.assertIsNone(out[2]["hours"])
        self.assertEqual(len(out[0]["hours"]), 24)
        self.assertEqual(sum(out[0]["hours"]), 30)

    def test_merges_are_left_out(self):
        commits = self.clusters([7, 7, 7], 50000)
        commits.append(cm(1600000000 + 1, merge=True, add=0, dele=0, files=0))
        self.assertEqual(self.fam(commits)["n"], 21)

    def test_no_dates_anywhere(self):
        ds = synthetic_dataset(seed=5, repos=3, per_repo=130)
        text = json.dumps(eda.rhythm(eda.indexed_rows(ds)))
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", text))
        self.assertIsNone(re.search(r"\b1[5-9]\d{8}\b", text))        # no epoch second of the harvest either


# ------------------------------------------------------------------ 5 concentration
class ConcentrationTests(unittest.TestCase):
    def ds(self, churns, **kw):
        ds = make_ds({"x": [cm(1600000000 + k) for k in range(3)]})
        ds["repos"][0]["files"] = [{"path": f"f{k}", "churn": c} for k, c in enumerate(churns)]
        ds["repos"][0].update(kw)
        return ds

    def test_handcrafted_gini_and_top_decile(self):
        rec = eda.concentration(self.ds([1] * 9 + [91]))["repos"][0]
        self.assertEqual((rec["available"], rec["unknown"], rec["reason"]), (True, False, None))
        self.assertEqual((rec["n_files"], rec["total_churn"], rec["gini_permille"], rec["top_decile_permille"], rec["half_k"]),
                         (10, 100, 810, 910, 1))

    def test_equal_churn_has_zero_gini_and_zero_churn_files_do_not_count(self):
        rec = eda.concentration(self.ds([5] * 12 + [0, 0, 0]))["repos"][0]
        self.assertEqual((rec["n_files"], rec["gini_permille"], rec["half_k"]), (12, 0, 6))
        self.assertEqual(rec["top_decile_permille"], 167)

    def test_private_repo_is_unknown_counts_only(self):
        ds = make_ds({"p": [cm(1600000000)]}, private=("p",))
        ds["repos"][0]["files"] = [{"path": "secret", "churn": 99}] * 12
        ds["repos"][0]["tree_measured"] = True
        rec = eda.concentration(ds)["repos"][0]
        self.assertEqual((rec["available"], rec["unknown"], rec["reason"]), (False, True, "counts only (private)"))
        self.assertIsNone(rec["n_files"])
        self.assertIsNone(rec["gini_permille"])
        self.assertNotIn("secret", json.dumps(rec))

    def test_unmeasured_tree_is_unknown(self):
        for kw in ({"tree_measured": False}, {"files": []}):
            rec = eda.concentration(self.ds([1] * 12, **kw))["repos"][0]
            self.assertEqual((rec["available"], rec["unknown"], rec["reason"]), (False, True, "tree not harvested"))

    def test_fewer_than_ten_files_is_not_available(self):
        rec = eda.concentration(self.ds([4] * 9))["repos"][0]
        self.assertEqual((rec["available"], rec["unknown"]), (False, False))
        self.assertIn("fewer than 10", rec["reason"])
        self.assertIsNone(rec["gini_permille"])
        self.assertTrue(eda.concentration(self.ds([4] * 10))["repos"][0]["available"])

    def test_repos_sorted_by_name_and_scope_text(self):
        ds = make_ds({"b": [cm(1600000000)], "a": [cm(1600000001)]})
        out = eda.concentration(ds)
        self.assertEqual([r["repo"] for r in out["repos"]], ["a", "b"])
        self.assertEqual(out["scope"], "all files at the pinned tip with default-branch non-merge history; renames not followed; "
                         "vendored and generated files count in full")


# ------------------------------------------------------------------ 6 flags
FORKS4 = ("all", "no_merge", "default_branch", "no_mass")


def oracle_flags(rows):
    """{rule: {i: (m, k)}} by plain filtering: every stratum is rebuilt from the full row list for every commit and fork."""
    def member(r, fork):
        return {"all": True, "no_merge": not r["m"], "default_branch": not r["m"] and r["b"] == 1,
                "no_mass": not r["m"] and r["f"] <= 15}[fork]

    def lbin(x, unit):
        return min(16 if unit == 1 else 19, (x // unit).bit_length())

    def lmed(xs):
        return sorted(xs)[(len(xs) - 1) // 2]

    # a fork that holds exactly the commits of an earlier fork repeats its verdict, so it is not a separate vote
    distinct, seen = [], set()
    for fork in FORKS4:
        ids = frozenset(r["i"] for r in rows if member(r, fork))
        if ids not in seen:
            seen.add(ids)
            distinct.append(fork)
    out = {"size_high": {}, "gap_long": {}}
    for r in rows:
        for fork in distinct:
            if not member(r, fork):
                continue
            stratum = [x for x in rows if member(x, fork) and x["r"] == r["r"] and x["c"] == r["c"]]
            if len(stratum) >= 20:
                bs = [lbin(x["a"] + x["d"], 1) for x in stratum]
                med = lmed(bs)
                md = lmed([abs(b - med) for b in bs])
                m, k = out["size_high"].get(r["i"], (0, 0))
                out["size_high"][r["i"]] = (m + 1, k + int(lbin(r["a"] + r["d"], 1) - med >= max(3, 3 * md) and r["a"] + r["d"] >= 64))
            repo = sorted((x for x in rows if member(x, fork) and x["r"] == r["r"]), key=lambda x: (x["t"], x["i"]))
            pos = [x["i"] for x in repo].index(r["i"])
            if pos == 0 or len(repo) - 1 < 20:
                continue
            gaps = [repo[j]["t"] - repo[j - 1]["t"] for j in range(1, len(repo))]
            bs = [lbin(g, 60) for g in gaps]
            med = lmed(bs)
            md = lmed([abs(b - med) for b in bs])
            m, k = out["gap_long"].get(r["i"], (0, 0))
            out["gap_long"][r["i"]] = (m + 1, k + int(bs[pos - 1] - med >= max(4, 3 * md) and gaps[pos - 1] >= 604800))
    return out


GRADE_TABLE = {(1, 1): "S", (2, 1): "S", (2, 2): "L", (3, 1): "S", (3, 2): "L", (3, 3): "C",
               (4, 1): "S", (4, 2): "S", (4, 3): "L", (4, 4): "C"}


def stress_dataset(seed):
    """A synthetic dataset with class strata of exactly 19, 20 or 21, some mass edits, off-default commits, merges and giants."""
    rng = random.Random(1000 + seed)
    ds = synthetic_dataset(seed=seed, repos=rng.randint(1, 3), per_repo=rng.randint(30, 90))
    for r in ds["repos"]:
        cs = r["commits"]
        want_ai = rng.choice([19, 20, 21])
        order = list(range(len(cs)))
        rng.shuffle(order)
        for pos, k in enumerate(order):
            ai = pos < want_ai
            cs[k].update(cls="ai" if ai else "you", who="Bot-AI" if ai else "Alice")
        for c in rng.sample(cs, k=len(cs) // 8):
            c["add"] = rng.choice([40, 90, 300, 5000])
        for c in rng.sample(cs, k=len(cs) // 8):
            c["files"] = rng.choice([16, 20, 30])
        for c in rng.sample(cs, k=len(cs) // 6):
            c["default"] = False
        for c in rng.sample(cs, k=len(cs) // 12):
            c["merge"] = True
            c["add"] = rng.choice([0, 0, 200])
    return ds


class FlagTests(unittest.TestCase):
    def listed(self, flags_, rule):
        body = flags_["by_rule"][rule]
        return {e[0]: (e[1], e[2], g) for g in "CLS" for e in body[g]}

    def test_oracle_on_40_seeded_datasets(self):
        total = collections.Counter()
        for seed in range(40):
            ds = stress_dataset(seed)
            rows = eda.indexed_rows(ds)
            want = oracle_flags(rows)
            got = eda.flags(rows)
            for rule in ("size_high", "gap_long"):
                expect = {i: (m, k) for i, (m, k) in want[rule].items() if k >= 1}
                lst = self.listed(got, rule)
                self.assertEqual({i: (k, m) for i, (k, m, _) in lst.items()}, {i: (k, m) for i, (m, k) in expect.items()}, (seed, rule))
                for i, (m, k) in expect.items():
                    self.assertEqual(lst[i][2], GRADE_TABLE[(m, k)], (seed, rule, i))
                self.assertEqual(got["by_rule"][rule]["counts"]["evaluable"], len(want[rule]), (seed, rule))
                total[rule] += len(expect)
                for g in "CLS":
                    self.assertEqual(got["by_rule"][rule][g], sorted(got["by_rule"][rule][g]))
        self.assertGreater(total["size_high"], 50)
        self.assertGreater(total["gap_long"], 5)

    @staticmethod
    def collapsed(seed, how):
        """stress_dataset with the variation between forks removed step by step: no merges; also all on the default
        branch; also no commit over 15 files (then all four forks are one set)."""
        ds = stress_dataset(seed)
        for r in ds["repos"]:
            for c in r["commits"]:
                if how >= 1:
                    c["merge"] = False
                    if c["files"] == 0:
                        c["files"], c["add"] = 1, max(c["add"], 1)
                if how >= 2:
                    c["default"] = True
                if how >= 3:
                    c["files"] = min(c["files"], 15)
        return ds

    def test_forks_holding_the_same_commits_vote_once(self):
        for how, distinct in ((1, 3), (2, 2), (3, 1)):
            top_m = 0
            for seed in range(12):
                ds = self.collapsed(seed, how)
                rows = eda.indexed_rows(ds)
                got = eda.flags(rows)
                want = oracle_flags(rows)
                for rule in ("size_high", "gap_long"):
                    lst = self.listed(got, rule)
                    expect = {i: (m, k) for i, (m, k) in want[rule].items() if k >= 1}
                    self.assertEqual({i: (k, m) for i, (k, m, _) in lst.items()}, {i: (k, m) for i, (m, k) in expect.items()}, (how, seed, rule))
                    for i, (m, k) in expect.items():
                        self.assertEqual(lst[i][2], GRADE_TABLE[(m, k)], (how, seed, rule, i))
                    top_m = max([top_m] + [m for m, _ in want[rule].values()])
                self.assertEqual(eda.verify(eda._plain(eda.build(ds)), ds), [], (how, seed))
            self.assertLessEqual(top_m, distinct, how)
            if how == 3:
                self.assertEqual(top_m, 1)
        # one distinct fork cannot be Confirmed or Likely, however large the commit
        ds = self.collapsed(3, 3)
        grades = {g: eda.flags(eda.indexed_rows(ds))["by_rule"][r]["counts"][g] for r in ("size_high", "gap_long") for g in "CL"}
        self.assertEqual(sum(grades.values()), 0, grades)

    def test_coverage_says_which_forks_coincide(self):
        ds = self.collapsed(3, 3)
        cov = eda.build(ds)["coverage"]
        self.assertEqual(cov["distinct_forks"], 1)
        self.assertEqual(cov["fork_same_as"], {"all": None, "no_merge": "all", "default_branch": "all", "no_mass": "all"})
        self.assertEqual(len(set(cov["by_fork"].values())), 1)
        ds = stress_dataset(2)
        cov = eda.build(ds)["coverage"]
        self.assertEqual(cov["distinct_forks"], 4)
        self.assertEqual(set(cov["fork_same_as"].values()), {None})
        # equal counts with different members are different forks: a counts-only comparison would merge these two
        ds = self.collapsed(3, 3)
        c0 = ds["repos"][0]["commits"]
        c0[0]["default"] = False
        c0[1]["files"] = 20
        cov = eda.build(ds)["coverage"]
        self.assertEqual(cov["by_fork"]["default_branch"], cov["by_fork"]["no_mass"])
        self.assertEqual(cov["fork_same_as"], {"all": None, "no_merge": "all", "default_branch": None, "no_mass": None})
        self.assertEqual(cov["distinct_forks"], 3)

    def test_grade_truth_table(self):
        for m in range(0, 5):
            for k in range(0, m + 1):
                if k >= 1:
                    self.assertEqual(eda.flag_grade(m, k), GRADE_TABLE[(m, k)], (m, k))

    def size_ds(self, baseline_lines, target_lines, n=20, target_files=1):
        base = 1600000000
        cs = [cm(base + 600 * k, add=baseline_lines) for k in range(n - 1)]
        cs.append(cm(base + 600 * n, add=target_lines, files=target_files))
        return make_ds({"x": cs})

    def fired(self, ds, rule="size_high"):
        rows = eda.indexed_rows(ds)
        fl = eda.flags(rows)
        return self.listed(fl, rule), fl["by_rule"][rule]["counts"]

    def test_size_threshold_boundaries(self):
        # baseline lines 8..15 sit in bin 4; a target in bin 7 (64..127) is exactly 3 bins up
        # every fork holds the same commits here, so there is one distinct fork and the best grade is S
        lst, counts = self.fired(self.size_ds(8, 64))
        self.assertEqual(lst, {19: (1, 1, "S")})
        self.assertEqual(counts["evaluable"], 20)
        lst, _ = self.fired(self.size_ds(16, 64))                  # median bin 5: only 2 up
        self.assertEqual(lst, {})
        lst, _ = self.fired(self.size_ds(4, 63))                   # median bin 3, target bin 6: 3 up but under 64 lines
        self.assertEqual(lst, {})
        lst, _ = self.fired(self.size_ds(4, 64))                   # median bin 3, target bin 7: 4 up and 64 lines
        self.assertEqual(lst, {19: (1, 1, "S")})

    def test_stratum_of_19_is_not_evaluable_and_20_is(self):
        lst, counts = self.fired(self.size_ds(8, 5000, n=19))
        self.assertEqual((lst, counts["evaluable"]), ({}, 0))
        lst, counts = self.fired(self.size_ds(8, 5000, n=20))
        self.assertEqual((len(lst), counts["evaluable"]), (1, 20))

    def test_a_mad_above_zero_raises_the_bar(self):
        base = 1600000000
        lines = [1, 1, 1, 1, 1, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16]     # bins 1 and 5
        cs = [cm(base + 600 * k, add=v) for k, v in enumerate(lines)]
        cs.append(cm(base + 99999, add=40000))                                              # bin 16
        lst, _ = self.fired(make_ds({"x": cs}))
        med = stats.lower_median([stats.bit_bin(v, 16) for v in lines + [40000]])
        md = stats.mad([stats.bit_bin(v, 16) for v in lines + [40000]])
        self.assertEqual((med, md), (5, 0))
        self.assertIn(19, lst)
        # widen the spread so that 3*MAD exceeds the distance of the target
        lines2 = [1] * 7 + [16] * 6 + [4096] * 7
        bs = [stats.bit_bin(v, 16) for v in lines2]
        self.assertGreaterEqual(3 * stats.mad(bs), 9)
        cs = [cm(base + 600 * k, add=v) for k, v in enumerate(lines2)]
        lst, _ = self.fired(make_ds({"x": cs}))
        self.assertEqual({i for i in lst}, set())

    def gap_ds(self, base_gap, target_gap, n=21):
        t, cs = 1600000000, []
        for k in range(n - 1):
            cs.append(cm(t, add=k + 1))
            t += base_gap
        cs.append(cm(t - base_gap + target_gap, add=99))
        return make_ds({"x": cs})

    def test_gap_threshold_boundaries(self):
        lst, counts = self.fired(self.gap_ds(40000, 604800), "gap_long")       # bin 10 vs bin 14: 4 up and 7 days
        self.assertEqual(lst, {20: (1, 1, "S")})
        self.assertEqual(counts["evaluable"], 20)
        lst, _ = self.fired(self.gap_ds(40000, 604799), "gap_long")
        self.assertEqual(lst, {})
        lst, _ = self.fired(self.gap_ds(80000, 604800), "gap_long")            # median bin 11: only 3 up
        self.assertEqual(lst, {})
        lst, counts = self.fired(self.gap_ds(40000, 604800, n=20), "gap_long")  # 19 gaps: not evaluable
        self.assertEqual((lst, counts["evaluable"]), ({}, 0))

    def test_planted_outlier_is_flagged_and_graded_c(self):
        ds = synthetic_dataset(seed=3, repos=3, per_repo=100, plant="outlier")
        rows = eda.indexed_rows(ds)
        big = max(rows, key=lambda r: r["a"])
        fl = eda.flags(rows)
        lst = self.listed(fl, "size_high")
        # the generator never exceeds 12 files, so no_merge and no_mass hold the same commits: three distinct forks
        self.assertEqual(lst[big["i"]], (3, 3, "C"))
        ds2 = copy.deepcopy(ds)
        for c in ds2["repos"][0]["commits"]:
            if c["add"] == big["a"]:
                c["files"] = 20
        rows2 = eda.indexed_rows(ds2)
        big2 = max(rows2, key=lambda r: r["a"])
        self.assertEqual(self.listed(eda.flags(rows2), "size_high")[big2["i"]], (3, 3, "C"))

    def test_merges_are_never_judged_outside_the_all_fork(self):
        ds = synthetic_dataset(seed=3, repos=2, per_repo=100)
        ds["repos"][0]["commits"][40].update(merge=True, add=5000, files=0)
        rows = eda.indexed_rows(ds)
        mi = next(r["i"] for r in rows if r["m"] and r["a"] == 5000)
        mk = eda._flag_counts(rows)["size_high"][mi]
        self.assertEqual(mk[0], 1)
        self.assertEqual(self.listed(eda.flags(rows), "size_high")[mi][2], "S")

    def test_cap_arithmetic(self):
        base, cs = 1600000000, {"c": [], "l": [], "s": []}
        t = base
        for name in cs:
            for k in range(501):
                cs[name].append(cm(t, add=1))
                t += 600
            for k in range(500):
                if name == "c":
                    cs[name].append(cm(t, add=100))
                elif name == "l":
                    cs[name].append(cm(t, add=100, files=16, default=False))
                else:
                    cs[name].append(cm(t, add=100, files=0, merge=True))
                t += 600
        rows = eda.indexed_rows(make_ds(cs))
        body = eda.flags(rows)["by_rule"]["size_high"]
        for g in "CLS":
            self.assertEqual(len(body[g]), 200, g)
            self.assertEqual(body["counts"][g], 500, g)
            self.assertEqual(body["counts"][g] - len(body[g]), body["omitted"][g], g)
            self.assertEqual([e[0] for e in body[g]], sorted(e[0] for e in body[g]))
        self.assertEqual(body["cap"], 200)
        # no_mass and default_branch hold the same commits here, so the family has three distinct forks, not four
        self.assertEqual({tuple(e[1:]) for e in body["C"]}, {(3, 3)})
        self.assertEqual({tuple(e[1:]) for e in body["L"]}, {(2, 2)})
        self.assertEqual({tuple(e[1:]) for e in body["S"]}, {(1, 1)})

    def test_flags_carry_indices_only(self):
        ds = synthetic_dataset(seed=3, repos=3, per_repo=100, plant="outlier", private=("r2",))
        text = json.dumps(eda.flags(eda.indexed_rows(ds)))
        for e in ds["repos"][0]["commits"][:5]:
            self.assertNotIn(e["sha"], text)
        self.assertNotIn("r0", text)
        self.assertEqual(set(eda.flags(eda.indexed_rows(ds))["rules"]), {"size_high", "gap_long"})


# ------------------------------------------------------------------ 7 privacy
class PrivacyTests(unittest.TestCase):
    NAMES = ("author", "repo", "session")

    def dataset(self):
        ds = synthetic_dataset(seed=6, repos=5, per_repo=45, private=("r2", "r3", "r4"), plant="outlier")
        for n, r in enumerate(ds["repos"]):
            r["name"] = ("pub0", "pub1", "author", "repo", "session")[n]
        for r in ds["repos"]:
            if r["visibility"] == "private":
                r["files"] = [{"path": f"{r['name']}/secret.py", "churn": 50 + k, "kind": "code"} for k in range(15)]
                r["tree_measured"] = True
                for k, c in enumerate(r["commits"]):
                    c.update(sha=f"{k:012x}", oid=f"{k:040x}", subject=f"PRIVSUBJECT{k}", paths=[f"{r['name']}/secret{k}.py"],
                             kinds={"code": 1})
        return ds

    def test_no_private_identifier_reaches_the_json(self):
        ds = self.dataset()
        out, problems = eda.run(ds)
        self.assertEqual(problems, [])
        text = json.dumps(out)
        for r in ds["repos"]:
            if r["visibility"] == "private":
                for c in r["commits"]:
                    for needle in (c["sha"], c["oid"], c["subject"], c["paths"][0]):
                        self.assertNotIn(needle, text)
        self.assertNotIn("secret", text)
        self.assertNotIn("<private>:", text)
        self.assertNotIn("<private>/", text)

    def test_itemised_finds_nothing(self):
        ds = self.dataset()
        out, _ = eda.run(ds)
        priv = set(self.NAMES)
        nodes = structure.node_ids([r["name"] for r in ds["repos"]], [i["label"] for i in ds["identities"]], [])
        self.assertEqual(structure.itemised(out, priv, "eda", nodes), [])
        for s in walk(out):
            if isinstance(s, str):
                for p in priv:
                    if s.startswith(p + ":") or s.startswith(p + "/"):
                        self.assertIn(s, nodes)
                self.assertFalse(s.startswith("<private>"))

    def test_private_repos_enter_as_counts_only(self):
        ds = self.dataset()
        out, _ = eda.run(ds)
        for e in out["audit"]["coverage"]:
            if e["repo"] in self.NAMES:
                self.assertEqual((e["n_with_paths"], e["n_usable"], e["tree"]), (None, None, "counts only (private)"))
        for e in out["concentration"]["repos"]:
            if e["repo"] in self.NAMES:
                self.assertTrue(e["unknown"])
                self.assertEqual(e["reason"], "counts only (private)")
        self.assertEqual(out["coverage"]["private_repos"], 3)
        self.assertEqual(out["coverage"]["private_commits"], 135)
        for c in out["audit"]["checks"]:
            public = {c2["sha"] for r in ds["repos"] if r["visibility"] != "private" for c2 in r["commits"]}
            self.assertTrue(set(c["witnesses"]) <= public)


# ------------------------------------------------------------------ 8 determinism
class DeterminismTests(unittest.TestCase):
    SCRIPT = (
        "import sys, json, hashlib\n"
        "sys.path.insert(0, 'tests')\n"
        "from eda_support import synthetic_dataset\n"
        "from x3dray import eda\n"
        "ds = synthetic_dataset(seed=2, repos=3, per_repo=40, private=('r2',), plant='defects')\n"
        "out, probs = eda.run(ds)\n"
        "assert probs == [], probs\n"
        "print(hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest())\n"
    )

    def test_two_runs_and_shuffled_repos_give_identical_bytes(self):
        ds = synthetic_dataset(seed=2, repos=3, per_repo=40, private=("r2",), plant="defects")
        a, pa = eda.run(copy.deepcopy(ds))
        b, pb = eda.run(copy.deepcopy(ds))
        self.assertEqual((pa, pb), ([], []))
        self.assertEqual(json.dumps(a), json.dumps(b))
        for order in ([2, 1, 0], [1, 2, 0]):
            sh = copy.deepcopy(ds)
            sh["repos"] = [sh["repos"][k] for k in order]
            c, pc = eda.run(sh)
            self.assertEqual(pc, [])
            self.assertEqual(json.dumps(c), json.dumps(a))

    def test_identical_under_hash_seeds(self):
        outs = []
        for seed in ("0", "1", "2"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE="1")
            env["PATH"] = os.environ["PATH"]
            run = subprocess.run([sys.executable, "-c", self.SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            outs.append(run.stdout)
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], outs[2])

    def test_no_float_anywhere_in_the_output(self):
        ds = synthetic_dataset(seed=2, repos=3, per_repo=60, plant=("defects", "outlier"))
        out, _ = eda.run(ds)
        for v in walk(out):
            self.assertNotIsInstance(v, float)
        self.assertEqual(json.loads(json.dumps(out)), out)


class HygieneTests(unittest.TestCase):
    def test_source_has_no_float_random_clock_or_round(self):
        tree = ast.parse((ROOT / "x3dray" / "eda.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                self.assertNotIsInstance(node.value, float)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                self.assertFalse(any(n in ("random", "time", "math") for n in names), names)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("round", "float"))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, ("now", "utcnow", "today", "time"))
            if isinstance(node, ast.BinOp):
                self.assertNotIsInstance(node.op, ast.Div)


# ------------------------------------------------------------------ 9 verify
class VerifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = synthetic_dataset(seed=3, repos=4, per_repo=80, private=("r3",), plant=("outlier", "defects"))
        cls.ds["repos"][3]["name"] = "secretrepo"
        cls.ds["repos"][3]["visibility"] = "private"
        cls.eda, cls.problems = eda.run(cls.ds)

    def fault(self, mutate):
        bad = copy.deepcopy(self.eda)
        mutate(bad)
        return eda.verify(bad, self.ds)

    def test_clean_output_verifies(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(eda.verify(self.eda, self.ds), [])
        self.assertEqual(eda.verify(json.loads(json.dumps(self.eda)), self.ds), [])

    def test_planted_faults_are_reported(self):
        def rec(e, slice_, fork, measure):
            return next(r for r in e["profile"]["records"] if (r["slice"], r["fork"], r["measure"]) == (slice_, fork, measure))

        def flag_index(e):
            body = e["flags"]["by_rule"]["size_high"]
            g = next(g for g in "CLS" if body[g])
            body[g][0][0] += 1

        def bump(d, k, by=1):
            d[k] += by

        faults = {
            "bin": lambda e: bump(rec(e, "family", "all", "lines")["bins"], 3),
            "quantile": lambda e: bump(rec(e, "family", "no_merge", "files"), "p50"),
            "flag index": flag_index,
            "audit count": lambda e: bump(e["audit"]["checks"][0], "count"),
            "concentration": lambda e: bump(e["concentration"]["repos"][0], "gini_permille"),
            "coverage count": lambda e: bump(e["coverage"], "commits"),
            "rhythm hours": lambda e: bump(e["rhythm"]["slices"][0]["hours"], 5),
            "burst": lambda e: bump(e["rhythm"]["slices"][0]["bursts"]["by_gap"]["3600"], "n_bursts"),
            "flag grade list": lambda e: e["flags"]["by_rule"]["gap_long"]["S"].append([0, 1, 1]),
            "witness": lambda e: e["audit"]["checks"][1]["witnesses"].append("000000000000"),
            "thresholds": lambda e: bump(e["method"]["thresholds"], "MIN_SIDE"),
            "code hash": lambda e: e["method"]["code_sha256"].update({"eda.py": "0" * 16}),
        }
        for name, f in faults.items():
            msgs = self.fault(f)
            self.assertTrue(msgs, name)

    def test_messages_never_echo_a_private_identifier(self):
        priv_sha = [c.get("sha") for c in self.ds["repos"][3]["commits"] if c.get("sha")]
        self.assertEqual(priv_sha, [])

        def hit_private_records(e):
            for r in e["profile"]["records"]:
                if r["slice"] == "repo:secretrepo":
                    r["members"] += 1
                    if r["available"]:
                        r["bins"][0] += 1
                        r["p50"] += 1
            for s in e["rhythm"]["slices"]:
                if s["slice"] == "repo:secretrepo":
                    s["n"] += 1
            for c in e["audit"]["coverage"]:
                if c["repo"] == "secretrepo":
                    c["n"] += 1
            for c in e["concentration"]["repos"]:
                if c["repo"] == "secretrepo":
                    c["unknown"] = False
            for c in e["audit"]["checks"]:
                c["by_repo"] = [{"repo": "secretrepo", "count": 99}]

        msgs = self.fault(hit_private_records)
        self.assertTrue(msgs)
        for m in msgs:
            self.assertNotIn("secretrepo", m)
            self.assertIsNone(re.search(r"secretrepo[:/]", m))

    def test_malformed_input_is_reported_not_raised(self):
        msgs = eda.verify({"stage": "eda"}, self.ds)
        self.assertTrue(msgs)
        msgs = eda.verify({}, self.ds)
        self.assertTrue(msgs)

    def test_structural_invariants(self):
        msgs = self.fault(lambda e: e["profile"]["records"][0]["bins"].__setitem__(0, e["profile"]["records"][0]["bins"][0] + 5))
        self.assertTrue(any("bins" in m for m in msgs))
        msgs = self.fault(lambda e: e["flags"]["by_rule"]["size_high"]["omitted"].update({"C": 7}))
        self.assertTrue(any("omitted" in m or "count" in m for m in msgs))
        msgs = self.fault(lambda e: e["audit"]["checks"][0].update({"grade": "Z", "status": "flag", "count": e["audit"]["checks"][0]["count"]}))
        self.assertTrue(any("grade" in m for m in msgs))

    def test_looks_are_delegated(self):
        with mock.patch("x3dray.looks.verify", return_value=["looks: planted"]) as v:
            msgs = eda.verify(self.eda, self.ds)
        self.assertIn("looks: planted", msgs)
        self.assertEqual(v.call_args[0][0], self.eda["looks"])
        self.assertEqual(v.call_args[0][1], eda.indexed_rows(self.ds))

    def test_pairwise_gini_agrees_with_the_weighted_formula(self):
        rng = random.Random(11)
        for _ in range(300):
            xs = [rng.choice([0, 1, 1, 2, 5, 9, 40, 1000]) for _ in range(rng.randint(10, 60))]
            if sum(xs) == 0:
                continue
            want = stats.permille(*stats.gini_ratio(xs))
            self.assertEqual(eda._naive_gini_permille(xs), want, xs)
            self.assertEqual(eda._naive_top_permille(xs), stats.permille(*stats.top_share_ratio(xs)))
        for xs in ([1] * 9, [0] * 12, []):
            self.assertIsNone(eda._naive_gini_permille(xs))

    def test_naive_quantile_counts_elements(self):
        rng = random.Random(5)
        for _ in range(300):
            xs = sorted(rng.randint(0, 9) for _ in range(rng.randint(1, 40)))
            for num, den in stats.PCTS.values():
                self.assertEqual(eda._naive_quantile(xs, num, den), stats.nearest_rank(xs, num, den))


# ------------------------------------------------------------------ 10 empty and single repo
class EmptyTests(unittest.TestCase):
    def test_empty_dataset(self):
        out, problems = eda.run({"family": "empty", "repos": [], "lineage": []})
        self.assertEqual(problems, [])
        self.assertEqual(list(out), ["stage", "schema_version", "method", "classes", "coverage", "audit", "profile", "rhythm",
                                     "concentration", "flags", "looks"])
        self.assertEqual(out["coverage"], {"commits": 0, "merges": 0, "private_commits": 0, "repos": [], "private_repos": 0,
                                           "by_fork": dict.fromkeys(stats.FORKS, 0), "distinct_forks": 1,
                                           "fork_same_as": {"all": None, "no_merge": "all", "default_branch": "all", "no_mass": "all"}})
        # no harvested_at, so the future half of the timestamp check could not run: unavailable, not a pass
        self.assertEqual([c["status"] for c in out["audit"]["checks"]], ["unavailable"] + ["pass"] * 6 + ["unavailable"])
        self.assertEqual(out["audit"]["coverage"], [])
        self.assertTrue(all(not r["available"] for r in out["profile"]["records"]))
        self.assertEqual({r["slice"] for r in out["profile"]["records"]}, {"family"})
        fam = out["rhythm"]["slices"][0]
        self.assertEqual((len(out["rhythm"]["slices"]), fam["n"], fam["quiet"]["available"], fam["bursts"]["available"]), (1, 0, False, False))
        self.assertEqual(out["concentration"]["repos"], [])
        for rule, body in out["flags"]["by_rule"].items():
            self.assertEqual(body["counts"], {"C": 0, "L": 0, "S": 0, "evaluable": 0})
        self.assertTrue(out["looks"]["findings"])
        self.assertTrue(all(f["status"] == "not_run" for f in out["looks"]["findings"]))
        self.assertIsNone(out["classes"]["pair"])

    def test_single_repo_dataset(self):
        ds = synthetic_dataset(seed=1, repos=1, per_repo=40)
        out, problems = eda.run(ds)
        self.assertEqual(problems, [])
        self.assertEqual(out["coverage"]["repos"], ["r0"])
        self.assertEqual(out["coverage"]["commits"], 40)
        self.assertTrue(any(r["available"] for r in out["profile"]["records"]))
        self.assertEqual(out["concentration"]["repos"][0]["repo"], "r0")

    def test_repo_with_no_commits_is_present_but_empty(self):
        ds = make_ds({"x": [cm(1600000000 + k, add=k + 1) for k in range(12)], "idle": []})
        out, problems = eda.run(ds)
        self.assertEqual(problems, [])
        self.assertEqual(out["coverage"]["repos"], ["idle", "x"])
        idle = next(e for e in out["audit"]["coverage"] if e["repo"] == "idle")
        self.assertEqual((idle["n"], idle["eligible"]["profile"]), (0, False))
        self.assertEqual([e["repo"] for e in out["audit"]["coverage"]], ["x", "idle"])


# ------------------------------------------------------------------ 11 shape and size
class ShapeTests(unittest.TestCase):
    def test_method_and_classes(self):
        ds = synthetic_dataset(seed=1, repos=2, per_repo=30)
        out, _ = eda.run(ds)
        m = out["method"]
        self.assertEqual(list(m), ["kernel", "rounding", "quantile", "thresholds", "forks", "seed_label", "code_sha256"])
        self.assertEqual(m["seed_label"], "x3dray.eda.v1|<look id>")
        self.assertEqual(set(m["forks"]), set(stats.FORKS))
        self.assertEqual(sorted(m["code_sha256"]), ["eda.py", "looks.py", "stats.py"])
        for name, digest in m["code_sha256"].items():
            self.assertEqual(digest, hashlib.sha256((ROOT / "x3dray" / name).read_bytes()).hexdigest()[:16])
        self.assertEqual(m["thresholds"]["BURST_GAPS"], [3600, 7200, 14400])
        self.assertEqual(out["classes"]["order"], ["you", "ai", "bot", "other", "upstream"])
        self.assertEqual(out["classes"]["labels"]["you"], "Alice")
        self.assertEqual(out["classes"]["labels"]["bot"], "bot")
        self.assertEqual(out["schema_version"], 1)
        self.assertEqual(out["coverage"]["by_fork"]["all"], 60)

    def test_minimal_bundle_helper(self):
        ds = synthetic_dataset(seed=1, repos=2, per_repo=10, private=("r1",))
        b = minimal_bundle(ds, {"stage": "eda"})
        self.assertEqual(b["repos"], [{"name": "r0", "visibility": "public"}, {"name": "r1", "visibility": "private"}])
        self.assertEqual(len(b["commits"]), 20)
        self.assertEqual(b["eda"], {"stage": "eda"})

    def test_size_budget_for_5000_commits_over_12_repos(self):
        ds = synthetic_dataset(seed=9, repos=12, per_repo=417, private=("r3", "r7"))
        out = eda.build(ds)
        out = eda._plain(out)
        size = len(json.dumps(out, separators=(",", ":")).encode())
        self.assertLessEqual(size, 150 * 1024)
        self.assertEqual(out["coverage"]["commits"], 5004)
        for section in ("audit", "profile", "rhythm", "concentration", "flags"):
            self.assertLess(len(json.dumps(out[section], separators=(",", ":"))), 60000, section)
        self.assertEqual(eda.verify(out, ds), [])


if __name__ == "__main__":
    unittest.main()
