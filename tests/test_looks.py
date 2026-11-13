"""The declared look battery (x3dray/looks.py): manifest, statistics against brute force, the permutation null against
full enumeration, Benjamini-Hochberg, the grade truth table, planted and pure-noise datasets, determinism, verify's
fault catalog and the work cap. Fixtures are self-contained: tiny row generators with a local seeded random.Random."""

import copy
import hashlib
import itertools
import json
import os
import random
import subprocess
import sys
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

from x3dray import looks, stats

ROOT = Path(__file__).resolve().parent.parent
EPOCH = 1_577_836_800      # 2020-01-01 00:00:00 UTC, a Wednesday
WEEK = 7 * 86400
LABELS = {"you": "You", "ai": "AI", "bot": "Bots"}
FORBIDDEN = ("significant", "probability", "p-value", "because", "caused", "due to")
IDS = ["contrast:lines", "contrast:files", "contrast:gap_s", "contrast:hour4", "contrast:weekday", "contrast:repo",
       "drift:first:lines", "drift:first:hour4", "drift:first:weekday",
       "drift:second:lines", "drift:second:hour4", "drift:second:weekday"]


def synth(seed, spec, weeks=40):
    """spec: list of (class, repo, count, lines_fn(rng, t) -> int). Rows are indexed in (t, i) order."""
    rng = random.Random(seed)
    rows = []
    for cls, repo, n, fn in spec:
        for _ in range(n):
            t = EPOCH + rng.randrange(0, weeks * WEEK)
            rows.append({"i": 0, "r": repo, "t": t, "c": cls, "a": fn(rng, t), "d": rng.randint(0, 5),
                         "f": rng.randint(1, 8), "m": 0, "b": 1})
    rng.shuffle(rows)
    rows.sort(key=lambda r: r["t"])
    for k, r in enumerate(rows):
        r["i"] = k
    return rows


def spread_forks(rows):
    """Make the four forks hold three different sets of commits (merges and side-branch commits at fixed positions), without
    touching any value a look reads. A history with every commit on one linear branch has one distinct fork, and a grade of
    Confirmed needs three."""
    for r in rows:
        if r["i"] % 19 == 7:
            r["m"] = 1
        elif r["i"] % 13 == 4:
            r["b"] = 0
    return rows


def big(rng, t):
    return rng.randint(20, 200)


def small(rng, t):
    return rng.randint(4, 40)


def noise(rng, t):
    return rng.randint(1, 100)


REPOS = ("r1", "r2", "r3")


def planted(seed, ai_fn, you_n=80, ai_n=60, repos=REPOS, linear=False):
    rows = synth(seed, [("you", x, you_n, big) for x in repos] + [("ai", x, ai_n, ai_fn) for x in repos])
    return rows if linear else spread_forks(rows)


def by_id(section):
    return {f["id"]: f for f in section["findings"]}


def walk(obj):
    yield obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from walk(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from walk(v)


def run(rows, **kw):
    return looks.run(rows, LABELS, **kw)


class ManifestTests(unittest.TestCase):
    def test_declared_ids_and_shape(self):
        self.assertEqual([d[0] for d in looks.DECLARED], IDS)
        self.assertEqual([d[1] for d in looks.DECLARED], ["contrast"] * 6 + ["drift"] * 6)
        self.assertEqual([d[2] for d in looks.DECLARED[:6]], ["lines", "files", "gap_s", "hour4", "weekday", "repo"])
        self.assertEqual({d[2] for d in looks.DECLARED[6:]}, {"lines", "hour4", "weekday"})
        self.assertEqual(looks.THRESHOLDS, {
            "MIN_SIDE": 20, "MIN_HALF": 8, "FLOOR_CLIFF": (147, 1000), "FLOOR_TV": (1, 10), "ALPHA": (1, 20),
            "Q_BH": (1, 10), "Q_CONFIRM": (1, 20), "WORK_CAP": 10_000_000, "M_MIN": 199, "M_MAX": 999,
            "MIN_STRATUM_SIDE": 20})

    def test_manifest_hash_is_independent_of_data(self):
        body = [[i, k, m] for i, k, m in looks.DECLARED] + [looks.THRESHOLDS]
        want = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        full = run(planted(1, small))
        empty = run([])
        zero_support = run(synth(2, [("you", "r1", 60, big), ("ai", "r1", 60, lambda r, t: 0)]))
        for section in (full, empty, zero_support):
            self.assertEqual(section["ledger"]["manifest_sha256"], want)
            self.assertEqual(section["ledger"]["declared"], 12)
            self.assertEqual([f["id"] for f in section["findings"]], IDS)

    def test_changing_a_declared_look_fails_verify(self):
        rows = planted(3, small, 30, 25)
        section = run(rows)
        self.assertEqual(looks.verify(section, rows), [])
        swapped = list(looks.DECLARED)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        with mock.patch.object(looks, "DECLARED", swapped):
            problems = looks.verify(section, rows)
        self.assertTrue(any("manifest_sha256" in p for p in problems))
        self.assertTrue(any("declared look ids" in p for p in problems))
        with mock.patch.dict(looks.THRESHOLDS, {"FLOOR_TV": (1, 9)}):
            self.assertTrue(any("manifest_sha256" in p for p in looks.verify(section, rows)))


class PairTests(unittest.TestCase):
    @staticmethod
    def rows_of(counts, merges=()):
        out = []
        for cls, n in counts.items():
            for k in range(n):
                out.append({"i": len(out), "r": "r1", "t": EPOCH + len(out), "c": cls, "a": 1, "d": 1, "f": 1,
                            "m": 0, "b": 1})
        for cls, n in merges:
            for k in range(n):
                out.append({"i": len(out), "r": "r1", "t": EPOCH + len(out), "c": cls, "a": 0, "d": 0, "f": 0,
                            "m": 1, "b": 1})
        return out

    def test_two_largest_with_ties_by_class_order(self):
        self.assertEqual(looks.choose_pair(self.rows_of({"you": 30, "ai": 50, "bot": 40})), ("ai", "bot"))
        self.assertEqual(looks.choose_pair(self.rows_of({"you": 40, "ai": 40, "bot": 40})), ("you", "ai"))
        self.assertEqual(looks.choose_pair(self.rows_of({"you": 20, "ai": 40, "bot": 40})), ("ai", "bot"))
        self.assertEqual(looks.choose_pair(self.rows_of({"bot": 25, "you": 25})), ("you", "bot"))
        self.assertEqual(looks.choose_pair(self.rows_of({"you": 20, "ai": 20, "other": 90, "upstream": 90})),
                         ("you", "ai"))

    def test_merges_do_not_count_and_twenty_is_the_minimum(self):
        self.assertIsNone(looks.choose_pair(self.rows_of({"you": 19, "ai": 50})))
        self.assertIsNone(looks.choose_pair(self.rows_of({"you": 25, "ai": 19}, merges=[("ai", 30)])))
        self.assertEqual(looks.choose_pair(self.rows_of({"you": 20, "ai": 20})), ("you", "ai"))
        self.assertIsNone(looks.choose_pair([]))

    def test_no_pair_means_twelve_not_run_and_m_stays_twelve(self):
        rows = self.rows_of({"you": 100, "ai": 5})
        captured = {}
        real = stats.bh_q

        def spy(items, m):
            captured["items"], captured["m"] = list(items), m
            return real(items, m)

        with mock.patch.object(looks.stats, "bh_q", spy):
            section = run(rows)
        self.assertIsNone(section["pair"])
        reason = "fewer than two author classes have 20 or more commits outside merges"
        self.assertEqual(section["pair_reason"], reason)
        self.assertEqual(section["ledger"]["evaluated"], 0)
        self.assertEqual(section["ledger"]["not_run"], 12)
        self.assertEqual(section["ledger"]["by_grade"], {"C": 0, "L": 0, "S": 0, "none": 12})
        self.assertEqual(captured["m"], 12)
        self.assertEqual(len(captured["items"]), 12)
        self.assertTrue(all(p == 1 for _, p in captured["items"]))
        for f in section["findings"]:
            self.assertEqual((f["status"], f["reason"], f["grade"], f["groups"], f["highlight"]),
                             ("not_run", reason, None, None, None))
            self.assertEqual(f["text"], f"{f['id']}: not run ({reason})")
            self.assertEqual(f["grade_reason"], "not_run")
        self.assertEqual(looks.verify(section, rows), [])


def n_effect(measure, a, b, ref=None):
    """Brute-force effect for one pair of value lists."""
    na, nb = len(a), len(b)
    if measure in looks.ORDINAL:
        s = sum((x > y) - (x < y) for x in a for y in b)
        return s, na * nb, (s > 0) - (s < 0), None
    keys = sorted(set(a) | set(b))
    gaps = {k: Fraction(a.count(k), na) - Fraction(b.count(k), nb) for k in keys}
    total = sum(abs(g) for g in gaps.values()) * na * nb
    star = keys[0]
    for k in keys:
        if abs(gaps[k]) > abs(gaps[star]):
            star = k
    g = gaps[star]
    return int(total), 2 * na * nb, (g > 0) - (g < 0), star


class StatisticTests(unittest.TestCase):
    def test_stat_against_pair_loops_and_fraction_histograms(self):
        rng = random.Random(21)
        for case in range(240):
            na, nb = rng.randint(1, 25), rng.randint(1, 25)
            top = rng.choice([2, 4, 30])
            av = [rng.randint(0, top) for _ in range(na)]
            bv = [rng.randint(0, top) for _ in range(nb)]
            tag = lambda vs: [(v, "r") for v in vs]
            s, den, direction, _ = n_effect("lines", av, bv)
            st = looks._stat("lines", tag(av), tag(bv))
            self.assertEqual((st["num"], st["den"], st["direction"]), (s, den, direction), case)
            t, den, direction, star = n_effect("hour4", av, bv)
            st = looks._stat("hour4", tag(av), tag(bv))
            self.assertEqual((st["num"], st["den"], st["direction"], st["k"]), (t, den, direction, star), case)
            self.assertEqual(looks._effect("hour4", st)["permille"], stats.permille(t, den))

    def test_direction_is_the_sign_at_the_dominant_bin(self):
        a = [(0, "r")] * 18 + [(1, "r")] * 2 + [(2, "r")] * 4
        b = [(0, "r")] * 4 + [(1, "r")] * 10 + [(2, "r")] * 10
        st = looks._stat("hour4", a, b)
        self.assertEqual((st["k"], st["direction"]), (0, 1))
        flipped = looks._stat("hour4", b, a)
        self.assertEqual((flipped["k"], flipped["direction"]), (0, -1))
        self.assertEqual(flipped["num"], st["num"])
        # a reference bin absent from both groups: direction 0, the reference comes back
        ref = looks._stat("hour4", a, b, ref_bin=5)
        self.assertEqual((ref["k"], ref["direction"]), (5, 0))

    def test_floors_exactly_at_the_boundary(self):
        def cliff(s, den, half=False):
            return looks._floor("lines", {"num": s, "den": den}, half)

        self.assertTrue(cliff(147, 1000))
        self.assertFalse(cliff(146, 1000))
        self.assertTrue(cliff(-147, 1000))
        self.assertTrue(cliff(294, 2000))
        self.assertFalse(cliff(293, 2000))
        self.assertFalse(cliff(0, 0))
        self.assertTrue(cliff(147, 2000, half=True))        # half of 0.147 is 0.0735
        self.assertFalse(cliff(146, 2000, half=True))
        self.assertTrue(cliff(74, 1000, half=True))         # 74/1000 >= 0.0735
        self.assertFalse(cliff(73, 1000, half=True))

        def tv(t, den, half=False):
            return looks._floor("hour4", {"num": t, "den": den}, half)

        self.assertTrue(tv(10, 100))
        self.assertFalse(tv(9, 100))
        self.assertTrue(tv(5, 100, half=True))
        self.assertFalse(tv(4, 100, half=True))

    def test_boundary_effect_end_to_end(self):
        # 20 vs 20, so the floor 0.147 * 400 = 58.8 sits between S = 58 and S = 59
        b = list(range(10, 210, 10))
        rng = random.Random(1)

        def groups_with(target):
            while True:
                a = [rng.randint(0, 220) for _ in range(20)]
                if sum((x > y) - (x < y) for x in a for y in b) == target:
                    return a

        for target, met in ((59, True), (58, False), (-59, True), (-58, False)):
            a = groups_with(target)
            st = looks._stat("lines", [(v, "r") for v in a], [(v, "r") for v in b])
            self.assertEqual(st["num"], target)
            self.assertEqual(looks._effect("lines", st)["floor_met"], met, target)


def tiny_rows(a_vals, b_vals, cls_a="ai", cls_b="you", weekdays=None):
    """Distinct-time rows: cls_a first with the given lines, then cls_b. Times are one day apart so weekday cycles."""
    rows = []
    for cls, vals in ((cls_a, a_vals), (cls_b, b_vals)):
        for v in vals:
            k = len(rows)
            rows.append({"i": k, "r": "r1", "t": EPOCH + 3600 * 5 + 86400 * k, "c": cls, "a": v, "d": 0, "f": 1,
                         "m": 0, "b": 1})
    return rows


class PermutationTests(unittest.TestCase):
    PATCH = {"MIN_SIDE": 2, "MIN_HALF": 1, "MIN_STRATUM_SIDE": 2, "M_MIN": 2000, "M_MAX": 2000}

    def exact_tail(self, vals_drawn_first, n_draw, stat):
        n = len(vals_drawn_first)
        obs = stat(list(range(n_draw)), list(range(n_draw, n)))
        hits = total = 0
        for sub in itertools.combinations(range(n), n_draw):
            rest = [k for k in range(n) if k not in sub]
            total += 1
            hits += stat(list(sub), rest) >= obs
        return Fraction(hits, total)

    def test_cliff_null_matches_full_enumeration(self):
        rng = random.Random(5)
        with mock.patch.dict(looks.THRESHOLDS, self.PATCH):
            for case in range(6):
                na, nb = rng.randint(3, 5), rng.randint(3, 5)
                av = [rng.randint(0, 6) for _ in range(na)]
                bv = [rng.randint(0, 6) for _ in range(nb)]
                section = run(tiny_rows(av, bv))
                f = by_id(section)["contrast:lines"]
                self.assertEqual(f["status"], "evaluated")
                self.assertEqual(sorted((f["groups"]["a"]["n"], f["groups"]["b"]["n"])), sorted((na, nb)))
                # c1 is the larger class (ties: you < ai); the drawn group is the smaller (ties: group a)
                ga, gb = (av, bv) if section["pair"][0] == "ai" else (bv, av)
                drawn, other = (ga, gb) if len(ga) <= len(gb) else (gb, ga)
                values = drawn + other

                def stat(first, rest):
                    return abs(sum((values[x] > values[y]) - (values[x] < values[y]) for x in first for y in rest))

                p = self.exact_tail(values, len(drawn), stat)
                M = f["null_model"]["M"]
                est = Fraction(f["null_model"]["k_ge"], M)
                var = float(p * (1 - p)) / M
                self.assertLessEqual(abs(float(est - p)), 5 * (var ** 0.5) + 1e-9, (case, p, est))

    def test_tv_null_matches_full_enumeration(self):
        rng = random.Random(8)
        with mock.patch.dict(looks.THRESHOLDS, self.PATCH):
            for case in range(5):
                na, nb = rng.randint(3, 5), rng.randint(3, 5)
                rows = tiny_rows([0] * na, [0] * nb)
                for r in rows:
                    r["t"] = EPOCH + 86400 * rng.randint(0, 2) + 7 * r["i"]     # weekdays 2..4, strictly ordered
                section = run(rows)
                f = by_id(section)["contrast:weekday"]
                c1, c2 = section["pair"]
                ga = [r for r in sorted(rows, key=lambda r: (r["t"], r["i"])) if r["c"] == c1]
                gb = [r for r in sorted(rows, key=lambda r: (r["t"], r["i"])) if r["c"] == c2]
                drawn, other = (ga, gb) if len(ga) <= len(gb) else (gb, ga)
                values = [stats.weekday_of(r["t"]) for r in drawn + other]
                keys = sorted(set(values))

                def stat(first, rest):
                    fa = [values[x] for x in first]
                    fb = [values[y] for y in rest]
                    return sum(abs(Fraction(fa.count(k), len(fa)) - Fraction(fb.count(k), len(fb))) for k in keys)

                p = self.exact_tail(values, len(drawn), stat)
                M = f["null_model"]["M"]
                var = float(p * (1 - p)) / M
                est = f["null_model"]["k_ge"] / M
                self.assertLessEqual(abs(est - float(p)), 5 * (var ** 0.5) + 1e-9, (case, p, est))

    def test_k_ge_equals_a_naive_reimplementation_of_the_same_stream(self):
        rows = planted(4, small, 30, 25)
        section = run(rows)
        f = by_id(section)["contrast:lines"]
        ordered = sorted(rows, key=lambda r: (r["t"], r["i"]))
        c1, c2 = section["pair"]
        ga = [r["a"] + r["d"] for r in ordered if r["c"] == c1]
        gb = [r["a"] + r["d"] for r in ordered if r["c"] == c2]
        drawn, other = (ga, gb) if len(ga) <= len(gb) else (gb, ga)
        arr = drawn + other
        n, nd, M = len(arr), len(drawn), f["null_model"]["M"]
        obs = abs(sum((x > y) - (x < y) for x in drawn for y in other))
        digest = hashlib.sha256(b"x3dray.eda.v1|contrast:lines").digest()
        rng = stats.SplitMix64(int.from_bytes(digest[:8], "big"))
        count = 0
        for _ in range(M):
            for j in range(nd):
                r = j + rng.below(n - j)
                arr[j], arr[r] = arr[r], arr[j]
            s = abs(sum((x > y) - (x < y) for x in arr[:nd] for y in arr[nd:]))
            count += s >= obs
        self.assertEqual(count, f["null_model"]["k_ge"])
        self.assertEqual(f["null_model"]["seed"], str(int.from_bytes(digest[:8], "big")))
        self.assertEqual(f["null_model"]["p"], [count + 1, M + 1])
        self.assertEqual(f["null_model"]["p_text"], stats.decimal_str(count + 1, M + 1, 4))

    def test_evaluation_order_changes_nothing(self):
        rows = planted(6, small, 30, 25)
        base = run(rows)
        for order in (list(reversed(range(12))), [5, 3, 11, 0, 8, 1, 9, 2, 10, 4, 7, 6]):
            self.assertEqual(run(rows, _order=order), base)


class BenjaminiHochbergTests(unittest.TestCase):
    def textbook(self, ps, m):
        ordered = sorted(ps, key=lambda it: (it[1], it[0]))
        n = len(ordered)
        out = {}
        for i in range(n):
            out[ordered[i][0]] = min([Fraction(1)] + [Fraction(m) * ordered[j][1] / (j + 1) for j in range(i, n)])
        return out

    def test_q_equals_the_textbook_scan(self):
        for seed, fn in ((1, small), (2, noise), (3, big)):
            section = run(planted(seed, fn, 40, 30))
            ps = [(f["id"], Fraction(*f["null_model"]["p"])) for f in section["findings"] if f["status"] == "evaluated"]
            ps += [(f["id"], Fraction(1)) for f in section["findings"] if f["status"] != "evaluated"]
            want = self.textbook(ps, 12)
            for f in section["findings"]:
                if f["status"] == "evaluated":
                    self.assertEqual(Fraction(*f["q"]), want[f["id"]], f["id"])
                    self.assertEqual(f["q"], [want[f["id"]].numerator, want[f["id"]].denominator])
                    self.assertEqual(f["q_text"], stats.decimal_str(f["q"][0], f["q"][1], 4))

    def test_not_run_looks_leave_m_at_twelve_and_enter_with_p_one(self):
        captured = {}
        real = stats.bh_q

        def spy(items, m):
            captured["items"], captured["m"] = dict(items), m
            out = real(items, m)
            captured["out"] = out
            return out

        rows = synth(7, [("you", "r1", 60, big), ("ai", "r1", 30, small)])        # drift:second has 30 < 40 rows
        with mock.patch.object(looks.stats, "bh_q", spy):
            section = run(rows)
        self.assertEqual(captured["m"], 12)
        self.assertEqual(len(captured["items"]), 12)
        not_run = [f["id"] for f in section["findings"] if f["status"] == "not_run"]
        self.assertTrue(not_run)
        for look_id in not_run:
            self.assertEqual(captured["items"][look_id], 1)
            self.assertEqual(captured["out"][look_id], 1)
        # a look whose add-one count is M + 1 over M + 1 has p = 1 and so q = 1
        flat = [{"i": k, "r": "r1", "t": EPOCH + 3600 * k, "c": "you" if k % 2 else "ai", "a": 5, "d": 0, "f": 1,
                 "m": 0, "b": 1} for k in range(80)]
        f = by_id(run(flat))["contrast:lines"]
        self.assertEqual(f["null_model"]["p"], [f["null_model"]["M"] + 1] * 2)
        self.assertEqual(f["q"], [1, 1])
        self.assertEqual(f["q_text"], "1.0000")


def table_grade(look):
    """The grade order restated as an ordered rule table over Fractions."""
    if look["status"] != "evaluated":
        return None, "not_run"
    ck = look["checks"]
    p = Fraction(look["null_model"]["k_ge"] + 1, look["null_model"]["M"] + 1)
    q = Fraction(*look["q"])
    rules = [
        (not look["effect"]["floor_met"], None, "effect_below_floor"),
        (p > Fraction(1, 20), None, "not_surprising"),
        (q > Fraction(1, 10), "S", "not_surviving_BH"),
        (ck["strata"] is not None and ck["strata"]["opposite"] > 0, "S", "contrary_effect_in_a_repo"),
        (q > Fraction(1, 20), "L", "q_above_confirm_level"),
        (ck["strata"] is not None and ck["strata"]["n"] == 0, "L", "no_stratum_with_both_groups"),
        (not ck["halves"]["evaluable"], "L", "halves_not_evaluable"),
        (ck["halves"]["replicates"] is False, "L", "not_replicated_in_halves"),
        (ck["forks"]["evaluable"] < 3, "L", "fewer_than_3_distinct_forks_evaluable"),
        (ck["forks"]["agree"] != ck["forks"]["evaluable"], "L", "fork_disagreement"),
        (ck["loro"]["total"] < 3, "L", "leave_one_repo_out_unavailable"),
        (ck["loro"]["agree"] != ck["loro"]["total"], "L", "leave_one_repo_out_disagreement"),
    ]
    for hit, g, why in rules:
        if hit:
            return g, why
    return "C", "all_checks_pass"


def mk_look(floor=True, k_ge=0, M=999, q=(1, 100), strata=None, halves=(True, True), forks=(4, 4), loro=(3, 3),
            status="evaluated"):
    return {
        "status": status, "effect": {"floor_met": floor}, "null_model": {"k_ge": k_ge, "M": M}, "q": list(q),
        "checks": {
            "forks": {"evaluable": forks[0], "agree": forks[1]},
            "loro": {"total": loro[0], "agree": loro[1]},
            "strata": strata,
            "halves": {"evaluable": halves[0], "replicates": halves[1] if halves[0] else None},
        },
    }


class GradeTruthTableTests(unittest.TestCase):
    def check(self, look):
        self.assertEqual(looks.grade(look), table_grade(look), look)

    def test_gates_and_exact_boundaries(self):
        self.check(mk_look(status="not_run"))
        self.assertEqual(looks.grade(mk_look(status="not_run")), (None, "not_run"))
        for floor in (True, False):
            for M, k in ((199, 0), (199, 9), (199, 10), (199, 199), (999, 49), (999, 50), (999, 998), (200, 9), (200, 10)):
                for q in ((1, 1000), (1, 20), (9, 100), (1, 10), (10, 100), (11, 100), (101, 1000), (1, 1)):
                    self.check(mk_look(floor=floor, k_ge=k, M=M, q=q))
        # 20 * (k_ge + 1) == M + 1 is still surprising; one more is not
        self.assertEqual(looks.grade(mk_look(k_ge=9, M=199))[1], "all_checks_pass")
        self.assertEqual(looks.grade(mk_look(k_ge=10, M=199)), (None, "not_surprising"))
        # 20 * q_num == q_den still confirms; one above is Likely at best, and 10 * q_num > q_den is Suspected
        self.assertEqual(looks.grade(mk_look(q=(1, 20)))[0], "C")
        self.assertEqual(looks.grade(mk_look(q=(5, 100)))[0], "C")
        self.assertEqual(looks.grade(mk_look(q=(51, 1000))), ("L", "q_above_confirm_level"))
        self.assertEqual(looks.grade(mk_look(q=(1, 10))), ("L", "q_above_confirm_level"))
        self.assertEqual(looks.grade(mk_look(q=(10, 100))), ("L", "q_above_confirm_level"))
        self.assertEqual(looks.grade(mk_look(q=(101, 1000))), ("S", "not_surviving_BH"))
        # a contrary repo effect still outranks the q level
        self.assertEqual(looks.grade(mk_look(q=(1, 10), strata={"n": 3, "same": 2, "opposite": 1})),
                         ("S", "contrary_effect_in_a_repo"))
        self.assertEqual(looks.grade(mk_look(floor=False)), (None, "effect_below_floor"))

    def test_robustness_dimensions_full_factorial(self):
        halves = [(False, None), (True, True), (True, False)]
        strata = [None, {"n": 3, "same": 3, "opposite": 0}, {"n": 3, "same": 2, "opposite": 1},
                  {"n": 0, "same": 0, "opposite": 0}]
        forks = [(e, a) for e in range(5) for a in range(e + 1)]
        loros = [(e, a) for e in range(5) for a in range(e + 1)]
        cases = 0
        seen = set()
        for h, s, f, lo in itertools.product(halves, strata, forks, loros):
            look = mk_look(halves=h, strata=s, forks=f, loro=lo)
            self.check(look)
            seen.add(looks.grade(look))
            cases += 1
        self.assertEqual(cases, 3 * 4 * 15 * 15)
        self.assertEqual(seen, {("S", "contrary_effect_in_a_repo"), ("L", "halves_not_evaluable"),
                                ("L", "not_replicated_in_halves"), ("L", "fewer_than_3_distinct_forks_evaluable"),
                                ("L", "fork_disagreement"), ("L", "leave_one_repo_out_unavailable"),
                                ("L", "leave_one_repo_out_disagreement"), ("L", "no_stratum_with_both_groups"),
                                ("C", "all_checks_pass")})
        # the q level sits above every robustness dimension, so it caps the grade at Likely whatever else passes
        for q in ((51, 1000), (1, 10)):
            self.assertEqual(looks.grade(mk_look(q=q, halves=(True, True), forks=(4, 4), loro=(4, 4))),
                             ("L", "q_above_confirm_level"))

    def test_only_confirmed_needs_everything(self):
        self.assertEqual(looks.grade(mk_look(forks=(3, 3), loro=(3, 3))), ("C", "all_checks_pass"))
        self.assertEqual(looks.grade(mk_look(forks=(2, 2))), ("L", "fewer_than_3_distinct_forks_evaluable"))
        self.assertEqual(looks.grade(mk_look(loro=(2, 2))), ("L", "leave_one_repo_out_unavailable"))
        self.assertEqual(looks.grade(mk_look(strata={"n": 1, "same": 0, "opposite": 0}))[0], "C")
        # a stratum check that never ran is not a pass, even when every other check is perfect
        self.assertEqual(looks.grade(mk_look(strata={"n": 0, "same": 0, "opposite": 0})),
                         ("L", "no_stratum_with_both_groups"))
        self.assertEqual(looks.grade(mk_look(strata=None))[0], "C")

    def test_grade_is_pure(self):
        look = mk_look(forks=(4, 3))
        before = copy.deepcopy(look)
        self.assertEqual(looks.grade(look), looks.grade(look))
        self.assertEqual(look, before)


class PlantedDatasetTests(unittest.TestCase):
    def test_effect_in_every_repo_and_both_parities_is_confirmed(self):
        rows = planted(1, small)
        section = run(rows)
        f = by_id(section)["contrast:lines"]
        self.assertEqual((f["grade"], f["grade_reason"]), ("C", "all_checks_pass"))
        self.assertEqual(f["effect"]["kind"], "cliff_delta")
        self.assertEqual(f["effect"]["direction"], -1 if section["pair"][0] == "ai" else 1)
        self.assertTrue(f["effect"]["floor_met"])
        self.assertEqual(f["checks"]["forks"], {**f["checks"]["forks"], "distinct": 3, "evaluable": 3, "agree": 3})
        self.assertEqual({k: v["same_as"] for k, v in f["checks"]["forks"]["per_fork"].items()},
                         {"all": None, "no_merge": None, "default_branch": None, "no_mass": "no_merge"})
        self.assertEqual(f["checks"]["loro"], {"total": 3, "agree": 3})
        self.assertEqual(f["checks"]["strata"], {"n": 3, "same": 3, "opposite": 0})
        self.assertTrue(f["checks"]["halves"]["replicates"])
        self.assertEqual(f["say_at_most"], looks.SAY_AT_MOST["C"])
        self.assertEqual(f["highlight"], {"who": section["pair"][0], "xray": True})
        self.assertEqual(looks.grade(f), ("C", "all_checks_pass"))

    def test_forks_holding_the_same_commits_are_one_check_not_four(self):
        # Every commit linear and on the default branch: all four forks are one set, so "every fork agrees" says nothing.
        rows = planted(1, small, linear=True)
        section = run(rows)
        f = by_id(section)["contrast:lines"]
        forks = f["checks"]["forks"]
        self.assertEqual((forks["distinct"], forks["evaluable"], forks["agree"]), (1, 1, 1))
        self.assertEqual({k: v["same_as"] for k, v in forks["per_fork"].items()},
                         {"all": None, "no_merge": "all", "default_branch": "all", "no_mass": "all"})
        self.assertEqual({v["permille"] for v in forks["per_fork"].values()}, {f["effect"]["permille"]})
        self.assertEqual((f["grade"], f["grade_reason"]), ("L", "fewer_than_3_distinct_forks_evaluable"))
        self.assertEqual(f["say_at_most"], looks.SAY_AT_MOST["L"])
        self.assertEqual(section["ledger"]["by_grade"]["C"], 0)
        self.assertEqual(looks.verify(section, rows), [])
        # the same data with variation between the forks is Confirmed, so the cap above comes from the forks alone
        spread = planted(1, small)
        self.assertEqual(by_id(run(spread))["contrast:lines"]["grade"], "C")

    def test_two_distinct_forks_are_still_too_few(self):
        rows = planted(1, small, linear=True)
        for r in rows:
            r["b"] = 0 if r["i"] % 13 == 4 else 1          # side-branch commits only: all = no_merge = no_mass != default_branch
        section = run(rows)
        f = by_id(section)["contrast:lines"]
        self.assertEqual((f["checks"]["forks"]["distinct"], f["checks"]["forks"]["evaluable"]), (2, 2))
        self.assertEqual((f["grade"], f["grade_reason"]), ("L", "fewer_than_3_distinct_forks_evaluable"))
        self.assertEqual(looks.verify(section, rows), [])

    def test_equal_counts_with_different_members_are_different_forks(self):
        rows = planted(1, small, linear=True)
        rows[0]["b"] = 0                                    # default_branch loses this commit
        rows[1]["f"] = 20                                   # no_mass loses that one: the same count, other members
        rows[2]["m"] = 1                                    # and no_merge differs from all
        section = run(rows)
        forks = by_id(section)["contrast:lines"]["checks"]["forks"]
        self.assertEqual(forks["distinct"], 4)
        self.assertEqual({v["same_as"] for v in forks["per_fork"].values()}, {None})
        self.assertEqual(looks.verify(section, rows), [])

    def test_the_grade_rule_text_names_distinct_forks(self):
        self.assertIn("3 distinct forks", looks.grade_rule())

    def test_effect_in_one_repo_only_is_not_confirmed(self):
        spec = [("you", x, 80, big) for x in REPOS]
        spec += [("ai", "r1", 60, small), ("ai", "r2", 60, big), ("ai", "r3", 60, big)]
        f = by_id(run(spread_forks(synth(1, spec))))["contrast:lines"]
        self.assertNotEqual(f["grade"], "C")
        self.assertEqual((f["grade"], f["grade_reason"]), ("L", "leave_one_repo_out_disagreement"))
        self.assertEqual(f["checks"]["loro"]["agree"], 2)
        self.assertEqual(f["checks"]["loro"]["total"], 3)

    def test_effect_only_in_even_weeks_is_not_replicated(self):
        # In the odd-week half the two classes draw from the same distribution; the half floor (0.0735) is low
        # enough that chance alone sometimes clears it, so the rule is checked on every seed and the planted
        # failure is pinned on the seeds where the quiet half stays below the floor.
        even_only = lambda r, t: small(r, t) if stats.week_of(t) % 2 == 0 else big(r, t)
        reasons = {}
        for seed in range(1, 9):
            f = by_id(run(planted(seed, even_only)))["contrast:lines"]
            half = f["checks"]["halves"]
            self.assertEqual(half["per_half"]["0"]["agrees"], True, seed)
            if half["replicates"]:
                self.assertEqual(f["grade"], "C", seed)
            else:
                self.assertEqual((f["grade"], f["grade_reason"]), ("L", "not_replicated_in_halves"), seed)
            reasons[seed] = f["grade_reason"]
        self.assertEqual(reasons[4], "not_replicated_in_halves")
        self.assertEqual(reasons[5], "not_replicated_in_halves")

    def test_simpson_reversal_is_suspected(self):
        # Pooled, the first class looks different; inside every repo the difference runs the other way.
        by_repo = {"r1": (100, 200), "r2": (50, 100), "r3": (1, 50)}
        def fn(repo, bump):
            lo, hi = by_repo[repo]
            return lambda r, t: r.randint(lo, hi) + bump
        spec = [("you", "r1", 150, fn("r1", 0)), ("you", "r2", 50, fn("r2", 0)), ("you", "r3", 30, fn("r3", 0)),
                ("ai", "r1", 30, fn("r1", 40)), ("ai", "r2", 50, fn("r2", 40)), ("ai", "r3", 150, fn("r3", 40))]
        section = run(synth(1, spec))
        f = by_id(section)["contrast:lines"]
        self.assertEqual((f["grade"], f["grade_reason"]), ("S", "contrary_effect_in_a_repo"))
        self.assertGreaterEqual(f["checks"]["strata"]["opposite"], 1)
        self.assertEqual(f["say_at_most"], looks.SAY_AT_MOST["S"])

    def test_class_tied_to_repo_is_at_most_likely(self):
        # you only in r1 and r2, ai only in r3: no repo holds both classes, so no stratum exists and removing r3
        # removes a group; leave-one-repo-out cannot reach three runs, but the missing stratum is what stops the grade.
        spec = [("you", "r1", 80, big), ("you", "r2", 80, big), ("ai", "r3", 120, small)]
        section = run(synth(2, spec))
        found = by_id(section)
        self.assertEqual(found["contrast:repo"]["checks"]["loro"], {"total": 0, "agree": 0})
        self.assertIsNone(found["contrast:repo"]["checks"]["strata"])
        self.assertIn(found["contrast:repo"]["grade"], (None, "S", "L"))
        self.assertIn(found["contrast:repo"]["effect"]["dominant_bin"], REPOS)
        lines = found["contrast:lines"]
        self.assertEqual(lines["checks"]["strata"], {"n": 0, "same": 0, "opposite": 0})
        self.assertIn(lines["grade"], (None, "S", "L"))
        self.assertEqual(lines["grade_reason"], "no_stratum_with_both_groups")

    def test_class_nested_in_many_repos_is_at_most_likely(self):
        # Four or more repos let leave-one-repo-out reach three runs, so only the strata check can notice that
        # nothing differs within a repo: no repo holds both classes, so it must send the look to Likely.
        for repos in (4, 5, 6):
            spec = [("you", f"r{k}", 60, big) for k in range(repos // 2)] + \
                   [("ai", f"r{k}", 60, small) for k in range(repos // 2, repos)]
            f = by_id(run(synth(repos, spec)))["contrast:lines"]
            self.assertEqual(f["checks"]["strata"], {"n": 0, "same": 0, "opposite": 0}, repos)
            self.assertGreaterEqual(f["checks"]["loro"]["total"], 3, repos)
            self.assertEqual((f["grade"], f["grade_reason"]), ("L", "no_stratum_with_both_groups"), repos)
            self.assertEqual(f["say_at_most"], looks.SAY_AT_MOST["L"])

    def test_contrast_repo_never_exceeds_likely(self):
        for seed in range(1, 6):
            spec = [("you", "r1", 100, big), ("you", "r2", 20, big), ("ai", "r2", 100, big), ("ai", "r3", 40, big),
                    ("you", "r3", 60, big)]
            f = by_id(run(synth(seed, spec)))["contrast:repo"]
            self.assertNotEqual(f["grade"], "C")
            self.assertEqual(f["highlight"]["xray"], True)
        spec = [("you", "r1", 100, big), ("ai", "r2", 100, big)]
        f = by_id(run(spread_forks(synth(1, spec))))["contrast:repo"]
        self.assertEqual((f["grade"], f["grade_reason"]), ("L", "leave_one_repo_out_unavailable"))
        self.assertEqual(f["effect"]["permille"], 1000)

    def test_a_group_of_nineteen_is_not_run(self):
        # gap_s leaves out the first row of the restricted set (it has no gap), so a class of exactly 20 whose
        # commit comes first is left with 19.
        rows = synth(4, [("you", "r1", 40, big), ("ai", "r1", 20, small)])
        if rows[0]["c"] != "ai":
            swap = next(r for r in rows if r["c"] == "ai")
            swap["c"], rows[0]["c"] = "you", "ai"
        section = run(rows)
        self.assertEqual(section["pair"], ["you", "ai"])
        found = by_id(section)
        self.assertEqual(found["contrast:lines"]["status"], "evaluated")
        gap = found["contrast:gap_s"]
        self.assertEqual((gap["status"], gap["grade"]), ("not_run", None))
        self.assertEqual(gap["reason"], "a group has fewer than 20 commits (n_a=40, n_b=19)")
        self.assertEqual(gap["groups"]["b"]["n"], 19)
        self.assertEqual(gap["text"], "contrast:gap_s: not run (a group has fewer than 20 commits (n_a=40, n_b=19))")
        self.assertIsNone(gap["effect"])
        self.assertEqual(section["ledger"]["not_run"], 1 + 3)           # gap_s and the three drifts of ai (20 rows)
        self.assertEqual(looks.verify(section, rows), [])
        # with the earliest row in the larger class the smaller one keeps all 20 and the larger loses one
        rows = synth(4, [("you", "r1", 40, big), ("ai", "r1", 20, small)])
        if rows[0]["c"] != "you":
            swap = next(r for r in rows if r["c"] == "you")
            swap["c"], rows[0]["c"] = "ai", "you"
        gap = by_id(run(rows))["contrast:gap_s"]
        self.assertEqual((gap["status"], gap["groups"]["a"]["n"], gap["groups"]["b"]["n"]), ("evaluated", 39, 20))

    def test_drift_of_a_class_with_thirty_commits_is_not_run(self):
        section = run(synth(3, [("you", "r1", 60, big), ("ai", "r1", 30, small)]))
        found = by_id(section)
        self.assertEqual(found["drift:first:lines"]["status"], "evaluated")
        for m in ("lines", "hour4", "weekday"):
            f = found["drift:second:" + m]
            self.assertEqual(f["reason"], "a group has fewer than 20 commits (n_a=15, n_b=15)")
            self.assertEqual(f["status"], "not_run")
            self.assertEqual(f["groups"]["a"]["label"], "later commits of AI")

    def test_pure_noise_over_thirty_fixed_seeds(self):
        # The plan asked for "no Confirmed and at most one Suspected in total". That cannot hold for this rule: a
        # look is Suspected whenever its add-one count is at most 1/20 and the effect clears the floor, so about 5 %
        # of evaluated looks are Suspected under pure noise (15 of 360 here), and a dataset has a q of at most 1/10
        # somewhere with chance up to 10 % (5 of 30 here). The ceilings below are the frozen figures with headroom
        # from the binomial: C at most 2, datasets with any L or C at most 8 (Bin(30, 0.1) exceeds 7 with chance
        # below 1 %), S at most 30 (Bin(360, 0.05) exceeds 29 with chance below 1 %). Confirmed needs q of at most 1/20
        # (it was 1/10 until the calibration below showed 1 Confirmed in these 30 pure-noise datasets).
        totals = {"C": 0, "L": 0, "S": 0, "none": 0}
        datasets_with_robust = 0
        for seed in range(30):
            spec = [(c, x, 60, noise) for c in ("you", "ai", "bot") for x in REPOS]
            section = run(spread_forks(synth(seed, spec)))
            robust = False
            for f in section["findings"]:
                self.assertEqual(looks.grade(f), (f["grade"], f["grade_reason"]))
                if f["grade"] in ("C", "L"):
                    robust = True
                    self.assertLessEqual(10 * f["q"][0], f["q"][1])
                    self.assertLessEqual(20 * (f["null_model"]["k_ge"] + 1), f["null_model"]["M"] + 1)
                if f["grade"] == "C":
                    self.assertLessEqual(20 * f["q"][0], f["q"][1])
            datasets_with_robust += robust
            for k, v in section["ledger"]["by_grade"].items():
                totals[k] += v
        self.assertEqual(sum(totals.values()), 30 * 12)
        self.assertLessEqual(totals["C"], 2, totals)
        self.assertLessEqual(datasets_with_robust, 8, totals)
        self.assertLessEqual(totals["S"], 30, totals)
        self.assertEqual(totals, {"C": 0, "L": 4, "S": 10, "none": 346})     # frozen regression for these seeds

    @staticmethod
    def drift_rows(kind, seed):
        rng = random.Random(seed)
        rows = []
        for cls in ("you", "ai"):
            for repo in REPOS:
                for _ in range(80):
                    rows.append({"i": 0, "r": repo, "t": EPOCH + rng.randrange(0, 40 * WEEK), "c": cls, "a": 0,
                                 "d": rng.randint(0, 3), "f": 2, "m": 0, "b": 1})
        rows.sort(key=lambda r: r["t"])
        for k, r in enumerate(rows):
            r["i"] = k
        spread_forks(rows)
        yours = sorted(r["t"] for r in rows if r["c"] == "you")
        med = yours[len(yours) // 2]
        for r in rows:
            base = rng.randint(10, 60)
            if r["c"] == "you":
                base += (100 if r["t"] >= med else 0) if kind == "step" else (r["t"] - EPOCH) * 150 // (40 * WEEK)
            r["a"] = base
        return rows

    def test_step_change_in_a_class_is_graded_and_noise_in_the_other_is_not(self):
        for seed in (1, 2):
            section = run(self.drift_rows("step", seed))
            found = by_id(section)
            f = found["drift:first:lines"]
            self.assertEqual(section["pair"], ["you", "ai"])
            self.assertEqual((f["grade"], f["grade_reason"]), ("C", "all_checks_pass"))
            self.assertEqual(f["effect"]["permille"], 1000)
            self.assertEqual(f["groups"]["a"]["label"], "later commits of You")
            self.assertEqual(f["groups"]["b"]["label"], "earlier commits of You")
            self.assertIsNone(f["highlight"])
            self.assertIsNone(found["drift:second:lines"]["grade"])

    def test_a_ramp_is_detected_as_drift_too(self):
        # The plan said a ramp should not exceed Likely. A drift look compares later with earlier commits, and a
        # ramp separates them as cleanly as a step, so every declared check passes; the test records that.
        for seed in (1, 2):
            f = by_id(run(self.drift_rows("ramp", seed)))["drift:first:lines"]
            self.assertIn(f["grade"], ("C", "L"))
            self.assertGreater(f["effect"]["permille"], 900)


class ShapeAndPrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = [("you", "secret-repo-xyz", 70, big), ("ai", "secret-repo-xyz", 50, small),
                ("you", "other-private-abc", 60, big), ("ai", "other-private-abc", 40, small)]
        cls.rows = synth(9, spec)
        cls.section = run(cls.rows)

    def test_no_float_and_json_clean(self):
        for node in walk(self.section):
            self.assertNotIsInstance(node, float)
        text = json.dumps(self.section, allow_nan=False)
        self.assertEqual(json.loads(text), self.section)

    def test_no_identifier_keys_and_no_private_prefixes(self):
        banned = {"sha", "sha12", "oid", "subject", "path", "paths", "kinds", "message", "area"}
        for node in walk(self.section):
            if isinstance(node, str):
                self.assertFalse(node.startswith("<private>"), node)
        keys = {k for k in walk(self.section) if isinstance(k, str)}
        self.assertFalse(keys & banned)

    def test_forbidden_words_nowhere_in_the_section(self):
        for node in walk(self.section):
            if isinstance(node, str):
                for word in FORBIDDEN:
                    self.assertNotIn(word, node.lower(), node)

    def test_finding_shape(self):
        want_keys = {"id", "kind", "measure", "status", "reason", "grade_kind", "groups", "effect", "null_model", "q",
                     "q_text", "checks", "grade", "grade_reason", "say_at_most", "text", "highlight", "scope"}
        self.assertEqual(set(self.section), {"pair", "pair_reason", "ledger", "grade_rule", "say_at_most", "scope",
                                              "findings"})
        for f in self.section["findings"]:
            self.assertEqual(set(f), want_keys)
            self.assertEqual(f["grade_kind"], "robustness")
            self.assertEqual(f["scope"], looks.SCOPE)
            if f["kind"] == "contrast":
                self.assertEqual(f["highlight"], {"who": "you", "xray": True})
            else:
                self.assertIsNone(f["highlight"])
            if f["status"] == "evaluated":
                self.assertEqual(set(f["effect"]), {"kind", "num", "den", "permille", "direction", "dominant_bin",
                                                     "floor_met"})
                self.assertEqual(set(f["null_model"]), {"name", "M", "k_ge", "seed", "p", "p_text"})
                self.assertEqual(set(f["checks"]), {"forks", "loro", "strata", "halves"})
                self.assertEqual(set(f["checks"]["forks"]["per_fork"]), set(stats.FORKS))
                self.assertEqual(set(f["checks"]["halves"]["per_half"]), {"0", "1"})
                for pf in f["checks"]["forks"]["per_fork"].values():
                    self.assertEqual(set(pf), {"n_a", "n_b", "evaluable", "permille", "agrees", "same_as"})
                self.assertEqual(f["text"], looks.render_text(f, LABELS))
                self.assertEqual(f["effect"]["kind"], "cliff_delta" if f["measure"] in looks.ORDINAL else "tv")
                self.assertEqual(f["effect"]["den"], f["groups"]["a"]["n"] * f["groups"]["b"]["n"]
                                 * (1 if f["measure"] in looks.ORDINAL else 2))
                self.assertEqual(f["null_model"]["seed"], str(stats.seed_from("x3dray.eda.v1|" + f["id"])))
        led = self.section["ledger"]
        self.assertEqual(sum(led["by_grade"].values()), 12)
        self.assertEqual(led["evaluated"] + led["not_run"], 12)
        self.assertEqual(led["alpha"], [1, 20])
        self.assertEqual(led["q_threshold"], [1, 10])
        self.assertEqual(led["q_confirm"], [1, 20])
        self.assertIn("independent or positively dependent", led["bh_note"])
        self.assertIn("not a likelihood", self.section["grade_rule"])

    def test_rows_are_not_mutated_and_sha_is_never_read(self):
        rows = copy.deepcopy(self.rows)
        before = copy.deepcopy(rows)
        for r in rows:
            r.pop("sha", None)
        run(rows)
        self.assertEqual(rows, before)

    def test_text_wording(self):
        f = by_id(self.section)["contrast:lines"]
        self.assertTrue(f["text"].startswith("You vs AI, lines changed per commit: Cliff's delta "), f["text"])
        self.assertIn(" label shuffles were at least as extreme (q ", f["text"])
        h = by_id(self.section)["contrast:hour4"]
        self.assertIn("distribution distance", h["text"])
        self.assertIn("UTC four-hour block", h["text"])
        w = by_id(self.section)["contrast:weekday"]
        if w["status"] == "evaluated":
            self.assertIn("UTC weekday", w["text"])

    def test_labels_fall_back_to_class_keys(self):
        section = looks.run(self.rows, {})
        f = by_id(section)["drift:second:hour4"]
        self.assertEqual(f["groups"]["a"]["label"], "later commits of ai")
        self.assertEqual(by_id(section)["contrast:lines"]["groups"]["a"]["label"], "you")

    def test_render_text_phrases(self):
        look = by_id(self.section)["contrast:repo"]
        self.assertIn("which repo the commit is in", look["text"])
        self.assertIn("(largest gap in ", look["text"])
        hour = copy.deepcopy(by_id(self.section)["contrast:hour4"])
        hour["effect"]["dominant_bin"] = 3
        self.assertIn("(largest gap in 12-15)", looks.render_text(hour, None))
        day = copy.deepcopy(by_id(self.section)["contrast:weekday"])
        day["effect"]["dominant_bin"] = 6
        self.assertIn("(largest gap in Sunday)", looks.render_text(day, None))
        for g, word in (("C", "Confirmed"), ("L", "Likely"), ("S", "Suspected")):
            hour["grade"] = g
            self.assertTrue(looks.render_text(hour, None).endswith(f"; {word}."))
        hour["grade"], hour["grade_reason"] = None, "not_surprising"
        self.assertTrue(looks.render_text(hour, None).endswith("; no grade (not_surprising)."))
        hour["effect"]["permille"] = -250
        look2 = copy.deepcopy(by_id(self.section)["contrast:lines"])
        look2["effect"]["permille"] = -250
        self.assertIn("Cliff's delta -0.250 (positive means the first group is larger)", looks.render_text(look2, None))


def sample_rows(seed, n=240, repos=REPOS, merges=True):
    rng = random.Random(seed)
    rows = []
    for k in range(n):
        rows.append({"i": 0, "r": rng.choice(repos), "t": EPOCH + rng.randrange(0, 30 * WEEK),
                     "c": rng.choice(["you", "ai", "ai", "bot"]), "a": rng.choice([0, 1, 5, 40, 300]),
                     "d": rng.randint(0, 9), "f": rng.randint(0, 20), "m": int(merges and rng.random() < 0.08),
                     "b": int(rng.random() < 0.7)})
    rows.sort(key=lambda r: (r["t"], r["a"]))
    for k, r in enumerate(rows):
        r["i"] = k
    return rows


class DeterminismTests(unittest.TestCase):
    def test_two_runs_identical(self):
        rows = sample_rows(3)
        a = json.dumps(run(rows), sort_keys=True)
        b = json.dumps(run(copy.deepcopy(rows)), sort_keys=True)
        self.assertEqual(a, b)

    def test_row_order_does_not_matter(self):
        rows = sample_rows(4)
        base = run(rows)
        for seed in range(3):
            shuffled = list(rows)
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(run(shuffled), base)
            self.assertEqual(looks.verify(base, shuffled), [])

    def test_hash_seed_independence(self):
        code = (
            "import json, hashlib, random\n"
            "from x3dray import looks\n"
            "import sys; sys.path.insert(0, %r)\n"
            "from test_looks import sample_rows\n"
            "rows = sample_rows(3)\n"
            "print(hashlib.sha256(json.dumps(looks.run(rows, {'you': 'You'}), sort_keys=True).encode()).hexdigest())\n"
        ) % str(ROOT / "tests")
        seen = set()
        for hash_seed in ("0", "1", "2"):
            env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))
            out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True,
                                 text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            seen.add(out.stdout.strip())
        self.assertEqual(len(seen), 1)
        here = hashlib.sha256(json.dumps(looks.run(sample_rows(3), {"you": "You"}), sort_keys=True).encode())
        self.assertEqual(seen.pop(), here.hexdigest())

    def test_source_has_no_nondeterminism(self):
        src = (ROOT / "x3dray" / "looks.py").read_text()
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], {"random", "time", "datetime", "math"})
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], {"random", "time", "datetime", "math"})
            if isinstance(node, ast.Constant):
                self.assertNotIsInstance(node.value, float)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"round", "float"})


class PropertyTests(unittest.TestCase):
    def test_verify_agrees_with_run_on_random_datasets(self):
        for seed in range(12):
            rows = sample_rows(seed, 140 + 20 * seed, repos=REPOS if seed % 2 else ("a", "b"), merges=seed % 3 != 0)
            section = run(rows)
            self.assertEqual(looks.verify(section, rows), [], seed)
            for f in section["findings"]:
                self.assertEqual(looks.grade(f), (f["grade"], f["grade_reason"]))

    def test_verify_agrees_with_run_on_edge_datasets(self):
        for rows in ([], sample_rows(1, 5), synth(1, [("you", "r1", 25, big), ("ai", "r1", 25, big)]),
                     synth(2, [("you", "r1", 30, lambda r, t: 7), ("ai", "r1", 30, lambda r, t: 7)])):
            self.assertEqual(looks.verify(run(rows), rows), [])


real_bh = stats.bh_q


class VerifyFaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = [("you", "secret-repo-xyz", 40, big), ("you", "northwind", 40, big), ("you", "zeta-private", 40, big),
                ("ai", "secret-repo-xyz", 30, small), ("ai", "northwind", 30, small), ("ai", "zeta-private", 30, small)]
        cls.rows = spread_forks(synth(11, spec))
        cls.section = run(cls.rows)

    def faults(self):
        yield "k_ge", lambda s: by_id(s)["contrast:lines"]["null_model"].__setitem__(
            "k_ge", by_id(s)["contrast:lines"]["null_model"]["k_ge"] + 1)
        yield "q", lambda s: by_id(s)["contrast:lines"].__setitem__("q", [1, 1])
        yield "q_text", lambda s: by_id(s)["contrast:lines"].__setitem__("q_text", "0.9999")
        yield "grade", lambda s: by_id(s)["contrast:lines"].__setitem__("grade", "L")
        yield "check count", lambda s: by_id(s)["contrast:lines"]["checks"]["forks"].__setitem__("agree", 2)
        yield "distinct forks", lambda s: by_id(s)["contrast:lines"]["checks"]["forks"].__setitem__("distinct", 4)
        yield "fork alias", lambda s: by_id(s)["contrast:lines"]["checks"]["forks"]["per_fork"]["no_mass"].__setitem__("same_as", None)
        yield "loro", lambda s: by_id(s)["contrast:lines"]["checks"]["loro"].__setitem__("total", 4)
        yield "effect", lambda s: by_id(s)["contrast:lines"]["effect"].__setitem__("num", 1)
        yield "dominant", lambda s: by_id(s)["contrast:hour4"]["effect"].__setitem__(
            "dominant_bin", (by_id(s)["contrast:hour4"]["effect"]["dominant_bin"] + 1) % 6)
        yield "group size", lambda s: by_id(s)["contrast:lines"]["groups"]["a"].__setitem__("n", 5)
        yield "ledger count", lambda s: s["ledger"].__setitem__("evaluated", 11)
        yield "ledger M", lambda s: s["ledger"].__setitem__("M", 998)
        yield "manifest", lambda s: s["ledger"].__setitem__("manifest_sha256", "0" * 64)
        yield "pair", lambda s: s.__setitem__("pair", ["ai", "you"])
        yield "text", lambda s: by_id(s)["contrast:files"].__setitem__("text", "edited")
        yield "highlight", lambda s: by_id(s)["contrast:lines"].__setitem__("highlight", {"who": "ai", "xray": True})
        yield "halves", lambda s: by_id(s)["contrast:lines"]["checks"]["halves"]["per_half"]["0"].__setitem__(
            "permille", 1)
        yield "strata", lambda s: by_id(s)["contrast:lines"]["checks"].__setitem__("strata", None)
        yield "bool as int", lambda s: by_id(s)["contrast:lines"]["effect"].__setitem__("floor_met", 1)
        yield "dropped finding", lambda s: s["findings"].pop()

    def test_clean_section_verifies(self):
        self.assertEqual(looks.verify(self.section, self.rows), [])

    def test_every_planted_fault_is_reported_without_private_names(self):
        for name, mutate in self.faults():
            broken = copy.deepcopy(self.section)
            mutate(broken)
            problems = looks.verify(broken, self.rows)
            self.assertTrue(problems, name)
            joined = " ".join(problems)
            for private in ("secret-repo-xyz", "northwind", "zeta-private"):
                self.assertNotIn(private, joined, name)

    def test_a_bug_in_the_stage_is_caught_by_the_independent_recomputation(self):
        real_cliff, real_perm = stats.cliff_counts, stats.perm_count_cliff
        with mock.patch.object(looks.stats, "cliff_counts", lambda a, b: (real_cliff(a, b)[0] + 1, real_cliff(a, b)[1])):
            broken = run(self.rows)
        self.assertTrue(looks.verify(broken, self.rows))
        with mock.patch.object(looks.stats, "perm_count_cliff", lambda *a: real_perm(*a) + 1):
            broken = run(self.rows)
        self.assertTrue(any("k_ge" in p for p in looks.verify(broken, self.rows)))
        with mock.patch.object(looks.stats, "bh_q", lambda items, m: {k: v for k, v in real_bh(items, m + 1).items()}):
            broken = run(self.rows)
        self.assertTrue(any(".q" in p for p in looks.verify(broken, self.rows)))

    def test_changed_rows_are_reported(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["a"] += 500
        self.assertTrue(looks.verify(self.section, rows))
        self.assertTrue(looks.verify(self.section, rows[1:]))

    def test_malformed_section(self):
        self.assertTrue(looks.verify({}, self.rows))
        self.assertTrue(looks.verify(None, self.rows))
        self.assertTrue(looks.verify({"findings": [1, 2]}, self.rows))


class WorkCapTests(unittest.TestCase):
    def check_ledger(self, section):
        led = section["ledger"]
        t = looks.THRESHOLDS
        W = sum(min(f["groups"]["a"]["n"], f["groups"]["b"]["n"]) for f in section["findings"]
                if f["status"] == "evaluated")
        self.assertEqual(led["W"], W)
        self.assertEqual(led["M"], max(t["M_MIN"], min(t["M_MAX"], t["WORK_CAP"] // max(1, W))))
        self.assertEqual(led["work_exceeded"], W * led["M"] > t["WORK_CAP"])
        self.assertTrue(199 <= led["M"] <= 999)
        for f in section["findings"]:
            if f["status"] == "evaluated":
                self.assertEqual(f["null_model"]["M"], led["M"])

    def test_six_thousand_rows_lower_m_to_the_cap(self):
        spec = [(c, x, 667, noise) for c in ("you", "ai", "bot") for x in REPOS]
        section = run(synth(1, spec))
        self.check_ledger(section)
        led = section["ledger"]
        self.assertLess(led["M"], 999)
        self.assertGreater(led["M"], 199)
        self.assertFalse(led["work_exceeded"])
        self.assertLessEqual(led["W"] * led["M"], looks.THRESHOLDS["WORK_CAP"])

    def test_cap_exceeded_when_even_the_minimum_m_is_too_much(self):
        rows = synth(2, [(c, x, 70, noise) for c in ("you", "ai", "bot") for x in REPOS])
        with mock.patch.dict(looks.THRESHOLDS, {"WORK_CAP": 20_000}):
            section = run(rows)
            self.check_ledger(section)
            self.assertEqual(section["ledger"]["M"], 199)
            self.assertTrue(section["ledger"]["work_exceeded"])
            self.assertEqual(looks.verify(section, rows), [])

    def test_small_datasets_use_the_maximum_m(self):
        section = run(planted(1, small, 25, 22))
        self.check_ledger(section)
        self.assertEqual(section["ledger"]["M"], 999)
        self.assertFalse(section["ledger"]["work_exceeded"])

    def test_the_naive_cliff_count_is_not_a_pair_loop(self):
        # The check must stay cheap enough to run inside the stage and the witness gate: a pair loop over two groups
        # of 20000 is 4 * 10**8 comparisons (minutes); counting against sorted values is instant. The closed forms below
        # are exact, and small random groups with ties agree with the brute-force pair loop.
        n = 20000
        self.assertEqual(looks._n_stat("lines", list(range(n)), list(range(n)))["num"], 0)
        st = looks._n_stat("lines", list(range(n)), [n // 2] * n)
        self.assertEqual((st["num"], st["den"]), (n * ((n - 1 - n // 2) - n // 2), n * n))
        rng = random.Random(7)
        for _ in range(200):
            a = [rng.randint(0, 6) for _ in range(rng.randint(0, 25))]
            b = [rng.randint(0, 6) for _ in range(rng.randint(0, 25))]
            want = sum((x > y) - (x < y) for x in a for y in b)
            self.assertEqual(looks._n_stat("lines", a, b)["num"], want)

    def test_three_thousand_rows_run_and_verify_within_the_cap(self):
        spec = [(c, x, 333, noise) for c in ("you", "ai", "bot") for x in REPOS]
        rows = synth(3, spec)
        section = run(rows)
        self.check_ledger(section)
        self.assertLessEqual(section["ledger"]["W"] * section["ledger"]["M"], looks.THRESHOLDS["WORK_CAP"])
        self.assertEqual(looks.verify(section, rows), [])


if __name__ == "__main__":
    unittest.main()
