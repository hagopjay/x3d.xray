"""Python and JavaScript agree on the descriptive kernel and the commit filters.

The page computes a slice with the integer twin in tools.js (edaBin, edaSummarize, edaHourOf, edaWeekdayOf, edaInFork,
edaSliceValues). These tests run that twin in Node against x3dray/stats.py, datetime and a brute-force list comprehension,
on boundary vectors and on seeded random cases, so a drift in either side fails here. No tolerance anywhere: every
comparison is exact.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "x3dray" / "web"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eda_support import minimal_bundle, synthetic_dataset  # noqa: E402
from x3dray import eda, stats  # noqa: E402

TOOLS_URI = (WEB / "tools.js").as_uri()


def node(script: str, payload, timeout: int = 60):
    """Run `script` as an ES module with tools.js imported as `T`; `input` is the parsed stdin JSON."""
    head = ("const T = await import(process.argv[1]);\n"
            "const input = JSON.parse(await new Promise(r => { let s = ''; process.stdin.on('data', d => { s += d; }); "
            "process.stdin.on('end', () => r(s)); }));\n")
    run = subprocess.run(["node", "--input-type=module", "-e", head + script, TOOLS_URI], input=json.dumps(payload),
                         capture_output=True, text=True, check=False, timeout=timeout)
    if run.returncode != 0:
        raise AssertionError(run.stderr[-2000:])
    return json.loads(run.stdout)


def jsonable(v):
    """What a value looks like after a JSON round trip, so Python ints and JavaScript numbers compare as the page sees them."""
    return json.loads(json.dumps(v))


@unittest.skipUnless(shutil.which("node"), "Node is required for the JavaScript twin tests")
class KernelParity(unittest.TestCase):
    def test_constants_match_the_stage(self):
        got = node("console.log(JSON.stringify({forks: T.EDA_FORKS}));", {})
        self.assertEqual(got["forks"], list(stats.FORKS))
        # The mass-edit threshold lives in both languages; the stage records its own in method.thresholds.
        self.assertEqual(eda.THRESHOLDS["MASS_FILES"], stats.MASS_FILES)
        edge = node("""
          const f = n => T.edaInFork({ m: 0, b: 1, f: n }, 'no_mass');
          console.log(JSON.stringify([f(%d), f(%d)]));""" % (stats.MASS_FILES, stats.MASS_FILES + 1), {})
        self.assertEqual(edge, [True, False])

    def test_bin_matches_bit_bin_on_boundaries(self):
        cases = []
        xs = {0, 1, 2, 3, 2 ** 53 - 1, 2 ** 53 - 2}
        for k in range(0, 53):
            for d in (-1, 0, 1):
                v = 2 ** k + d
                if 0 <= v <= 2 ** 53 - 1:
                    xs.add(v)
        for v in range(2 ** 31 - 3, 2 ** 31 + 4):
            xs.add(v)
        for v in range(2 ** 32 - 3, 2 ** 32 + 4):
            xs.add(v)
        for unit in (1, 60):
            for k in range(0, 40):
                for d in (-1, 0, 1):
                    v = unit * 2 ** k + d
                    if 0 <= v <= 2 ** 53 - 1:
                        xs.add(v)
        for x in sorted(xs):
            for unit in (1, 60):
                for cap in (16, 19, 99):
                    cases.append([x, cap, unit])
        got = node("console.log(JSON.stringify(input.map(([x, c, u]) => T.edaBin(x, c, u))));", cases)
        want = [stats.bit_bin(x, cap, unit) for x, cap, unit in cases]
        self.assertEqual(len(cases), len(got))
        self.assertEqual(got, want)
        self.assertGreater(len(cases), 1000)

    def test_bin_refuses_what_the_stage_refuses(self):
        res = node("""
          const t = f => { try { f(); return 'ok'; } catch (e) { return e.constructor.name; } };
          console.log(JSON.stringify([t(() => T.edaBin(-1, 16)), t(() => T.edaBin(1.5, 16)), t(() => T.edaBin(5, 16, 0)),
            t(() => T.edaBin(2 ** 53, 16))]));""", {})
        self.assertEqual(res, ["RangeError"] * 4)
        with self.assertRaises(ValueError):
            stats.bit_bin(-1, 16)

    def test_quantile_matches_nearest_rank(self):
        rng = random.Random(7)
        cases = []
        for _ in range(400):
            n = rng.choice([1, 2, 3, 9, 10, 11, 99, 100, 101, 250])
            xs = sorted(rng.randint(0, 50) for _ in range(n))
            for num, den in stats.PCTS.values():
                cases.append([xs, num, den])
        got = node("console.log(JSON.stringify(input.map(([xs, a, b]) => T.edaQuantile(xs, a, b))));", cases)
        self.assertEqual(got, [stats.nearest_rank(xs, a, b) for xs, a, b in cases])

    def vectors(self):
        rng = random.Random(20260101)
        out = [[], [0], [5], [0, 0], [1, 2], [3] * 9, [3] * 10, [0] * 10, [0] * 40, list(range(10)), list(range(1, 11)),
               [2 ** 53 - 1] * 3, [2 ** 53 - 1, 0, 1], [2 ** 53 - 1] * 10, [2 ** 52] * 4 + [1],
               [1] * 9 + [10 ** 12], [10 ** 15] * 11, [0] * 9 + [1], [1, 1, 1, 1, 1, 1, 1, 1, 1, 1000]]
        shapes = []
        while len(out) + len(shapes) < 300:
            n = rng.choice([0, 1, 2, 3, 9, 10, 11, 19, 20, 21, 57, 100, 199])
            kind = rng.choice(["small", "ties", "zeros", "wide", "huge", "sparse", "ramp"])
            if kind == "small":
                xs = [rng.randint(0, 20) for _ in range(n)]
            elif kind == "ties":
                pool = [rng.randint(0, 9) for _ in range(3)]
                xs = [rng.choice(pool) for _ in range(n)]
            elif kind == "zeros":
                xs = [0] * n
            elif kind == "wide":
                xs = [rng.randint(0, 10 ** rng.randint(0, 9)) for _ in range(n)]
            elif kind == "huge":
                xs = [rng.randint(2 ** 52, 2 ** 53 - 1) for _ in range(n)]
            elif kind == "sparse":
                xs = [rng.choice([0, 0, 0, 0, rng.randint(1, 10 ** 6)]) for _ in range(n)]
            else:
                xs = [k * rng.randint(1, 5) for k in range(n)]
            rng.shuffle(xs)
            shapes.append(xs)
        return out + shapes

    def test_summarize_matches_stats_summarize_on_random_vectors(self):
        rng = random.Random(99)
        cases = []
        for xs in self.vectors():
            for cap, unit in ((16, 1), (19, 60), (rng.randint(1, 20), rng.choice([1, 2, 7, 60, 1000]))):
                cases.append([xs, cap, unit])
        got = node("console.log(JSON.stringify(input.map(([xs, c, u]) => T.edaSummarize(xs, c, u))));", cases)
        want = [jsonable(stats.summarize(xs, cap, unit)) for xs, cap, unit in cases]
        self.assertEqual(len(got), len(want))
        for k, (g, w, c) in enumerate(zip(got, want, cases)):
            self.assertEqual(g, w, f"case {k}: {c[0][:12]} cap {c[1]} unit {c[2]}")
        self.assertGreaterEqual(len(self.vectors()), 300)
        # The cases are meant to include every special branch.
        self.assertTrue(any(w.get("n") == 0 for w in want))
        self.assertTrue(any(isinstance(w.get("sum"), str) for w in want), "no case with a sum above 2**53")
        self.assertTrue(any(w.get("gini_permille") is None and w.get("n", 0) >= 10 for w in want))
        self.assertTrue(any(w.get("gini_permille") is not None for w in want))

    def test_summarize_key_order_and_types(self):
        got = node("console.log(JSON.stringify(T.edaSummarize(input, 16, 1)));", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual(list(got), list(stats.summarize([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], 16)))

    def test_summarize_refuses_non_integers(self):
        res = node("""
          const t = v => { try { T.edaSummarize(v, 16); return 'ok'; } catch (e) { return e.constructor.name; } };
          console.log(JSON.stringify([t([1.5]), t([-1]), t([2 ** 53]), t([NaN])]));""", {})
        self.assertEqual(res, ["RangeError"] * 4)

    def test_half_boundaries_round_away_from_zero_in_both(self):
        """Gini and top-decile share are ratios of integers; an exact .5 permille must land on the same integer."""
        rng = random.Random(5)
        halves, cases = 0, []
        for _ in range(4000):
            n = rng.randint(10, 14)
            xs = [rng.randint(0, 6) for _ in range(n)]
            g, t = stats.gini_ratio(xs), stats.top_share_ratio(xs)
            if g is None:
                continue
            exact = [Fraction(1000 * num, den) % 1 == Fraction(1, 2) for num, den in (g, t)]
            if any(exact):
                halves += 1
                cases.append(xs)
        # Hand-made ones: 1999 of 2000 is 999.5 permille, which is 1000.
        cases.append([1] + [0] * 8 + [1999])
        cases.append([1000] + [0] * 9 + [1000])
        self.assertGreater(halves, 20, "the search found too few exact .5 cases for the test to mean anything")
        got = node("console.log(JSON.stringify(input.map(xs => T.edaSummarize(xs, 16))));", cases)
        for xs, g in zip(cases, got):
            w = jsonable(stats.summarize(xs, 16))
            self.assertEqual((g["gini_permille"], g["top_decile_permille"]), (w["gini_permille"], w["top_decile_permille"]), xs)
        self.assertEqual(got[-2]["top_decile_permille"], 1000)
        self.assertEqual(stats.permille(1999, 2000), 1000)
        self.assertEqual(stats.permille(-1999, 2000), -1000)

    def test_hour_and_weekday_match_stats_and_datetime(self):
        rng = random.Random(11)
        lo, hi = 0, int(datetime(2100, 1, 1, tzinfo=timezone.utc).timestamp())
        ts = [rng.randint(lo, hi) for _ in range(2000)]
        ts += [rng.randint(-4 * 10 ** 9, -1) for _ in range(300)]
        for base in (0, 86400, 86400 * 7, -86400, -86400 * 7, 951782400, 4102444800):
            ts += [base - 1, base, base + 1, base + 3599, base + 3600, base + 86399]
        ts += [-1, -3600, -3601, -86399, -86400, -86401, 2 ** 40, -2 ** 40]
        got = node("console.log(JSON.stringify(input.map(t => [T.edaHourOf(t), T.edaWeekdayOf(t)])));", ts)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        for t, (h, w) in zip(ts, got):
            try:
                d = epoch + timedelta(seconds=t)
            except OverflowError:
                self.assertEqual((h, w), (stats.hour_of(t), stats.weekday_of(t)))
                continue
            self.assertEqual((h, w), (d.hour, d.weekday()), t)
            self.assertEqual((h, w), (stats.hour_of(t), stats.weekday_of(t)), t)


def day_of(t: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
RANK = {"C": 3, "L": 2, "S": 1}


def weekday_arg(v) -> int:
    if isinstance(v, int):
        return v
    s = v.lower()
    if s.isdigit():
        return int(s)
    return next(k for k, n in enumerate(WEEKDAY_NAMES) if s in (n, n[:3]))


def brute(bundle: dict, q: dict) -> list[int]:
    """The commit indices a query selects, by a plain loop over the rows with Python's own date and weekday functions."""
    repos = {r.strip().lower() for r in q["repo"].split(",")} if q.get("repo") else set()
    who = {w.strip() for w in q["who"].split(",")} if q.get("who") else set()
    flags = {}
    if q.get("flag"):
        for g in "CLS":
            for i, _k, _m in bundle["eda"]["flags"]["by_rule"][q["flag"]][g]:
                flags.setdefault(i, g)
    out = []
    for c in bundle["commits"]:
        d = day_of(c["t"])
        lines = c["a"] + c["d"]
        wd = weekday_arg(q["weekday"]) if q.get("weekday") is not None else None
        ok = (
            (not repos or c["r"].lower() in repos)
            and (not who or c["c"] in who)
            and (not q.get("from") or d >= q["from"])
            and (not q.get("to") or d <= q["to"])
            and (q.get("day") is None or d == q["day"])
            and (q.get("min_lines") is None or lines >= q["min_lines"])
            and (q.get("max_lines") is None or lines <= q["max_lines"])
            and (q.get("hour") is None or (c["t"] // 3600) % 24 == q["hour"])
            and (wd is None or datetime.fromtimestamp(c["t"], timezone.utc).weekday() == wd)
            and (not q.get("flag") or (c["i"] in flags and RANK[flags[c["i"]]] >= RANK[q.get("flag_grade") or "S"]))
            and (q.get("merges") is None or bool(c["m"]) == q["merges"])
            and (q.get("side_branch") is None or (not c["b"]) == q["side_branch"])
        )
        if ok:
            out.append(c["i"])
    return out


@unittest.skipUnless(shutil.which("node"), "Node is required for the JavaScript twin tests")
class FilterParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = synthetic_dataset(seed=41, repos=3, per_repo=100, private=("r2",), plant="outlier")
        cls.eda, problems = eda.run(cls.ds)
        assert problems == [], problems
        cls.bundle = minimal_bundle(cls.ds, cls.eda)

    def queries(self, n=400):
        rng = random.Random(2468)
        b = self.bundle
        days = sorted({day_of(c["t"]) for c in b["commits"]})
        out = []
        for _ in range(n):
            q = {}
            if rng.random() < 0.35:
                q["repo"] = rng.choice(["r0", "R1", "r2", "r0,r1", "r1, r2", "r0,r1,r2"])
            if rng.random() < 0.35:
                q["who"] = rng.choice(["you", "ai", "you,ai", "bot", "claude"])
                if q["who"] == "claude":
                    q["who"] = "ai"
            if rng.random() < 0.25:
                a, z = sorted(rng.sample(days, 2))
                q["from"], q["to"] = (a, z) if rng.random() < 0.6 else rng.choice([(a, ""), ("", z)])
            if rng.random() < 0.08:
                q["day"] = rng.choice(days)
            if rng.random() < 0.3:
                lo = rng.randint(0, 80)
                q["min_lines"] = lo
                if rng.random() < 0.5:
                    q["max_lines"] = lo + rng.randint(0, 150)
            elif rng.random() < 0.15:
                q["max_lines"] = rng.randint(0, 100)
            if rng.random() < 0.25:
                q["hour"] = rng.randint(0, 23)
            if rng.random() < 0.25:
                q["weekday"] = rng.choice([0, 1, 2, 3, 4, 5, 6, "mon", "TUE", "Wednesday", "thu", "fri", "sat", "Sun", "4"])
            if rng.random() < 0.18:
                q["flag"] = rng.choice(["size_high", "gap_long"])
                if rng.random() < 0.7:
                    q["flag_grade"] = rng.choice("CLS")
            if rng.random() < 0.3:
                q["merges"] = rng.random() < 0.5
            if rng.random() < 0.2:
                q["side_branch"] = rng.random() < 0.5
            out.append(q)
        return out

    def test_400_random_combinations_match_a_brute_force_loop(self):
        qs = self.queries()
        got = node("""
          const B = input.bundle;
          console.log(JSON.stringify(input.queries.map(q => T.matchCommits(B, q).map(c => c.i))));""",
                   {"bundle": self.bundle, "queries": qs})
        flagged_hits = nonempty = 0
        for q, g in zip(qs, got):
            bq = dict(q)
            for key in ("from", "to"):
                if bq.get(key) == "":
                    del bq[key]
            want = brute(self.bundle, bq)
            self.assertEqual(g, want, q)
            nonempty += bool(g)
            flagged_hits += bool(g and q.get("flag"))
        self.assertEqual(len(qs), 400)
        self.assertGreater(nonempty, 100, "too many empty answers for the comparison to mean much")
        self.assertGreater(flagged_hits, 5, "no flag filter ever matched")

    def test_the_flag_filter_is_at_least_a_grade(self):
        lists = self.eda["flags"]["by_rule"]["size_high"]
        got = node("""
          const B = input;
          const ix = g => T.matchCommits(B, { flag: 'size_high', flag_grade: g }).map(c => c.i);
          console.log(JSON.stringify({ C: ix('C'), L: ix('L'), S: ix('S'), none: T.matchCommits(B, { flag: 'size_high' }).map(c => c.i) }));""",
                   self.bundle)
        ids = {g: sorted(i for i, _k, _m in lists[g]) for g in "CLS"}
        self.assertEqual(got["C"], ids["C"])
        self.assertEqual(got["L"], sorted(ids["C"] + ids["L"]))
        self.assertEqual(got["S"], sorted(ids["C"] + ids["L"] + ids["S"]))
        self.assertEqual(got["none"], got["S"])
        self.assertTrue(ids["C"] or ids["L"] or ids["S"], "the fixture has no size flags to filter on")

    def test_bad_filters_are_refused_with_a_reason(self):
        cases = [
            ({"min_lines": 10, "max_lines": 5}, r"min_lines 10 is above max_lines 5"),
            ({"hour": 24}, r"hour must be an integer from 0 to 23"),
            ({"hour": -1}, r"hour must be an integer from 0 to 23"),
            ({"hour": 1.5}, r"hour must be an integer"),
            ({"weekday": 7}, r"weekday must be an integer from 0 to 6"),
            ({"weekday": "someday"}, r"weekday must be 0 to 6"),
            ({"min_lines": 1.5}, r"min_lines must be a non-negative integer"),
            ({"min_lines": -1}, r"min_lines must be a non-negative integer"),
            ({"max_lines": "5"}, r"max_lines must be a non-negative integer"),
            ({"flag": "nope"}, r'unknown flag "nope"; known: size_high, gap_long'),
            ({"flag": "size_high", "flag_grade": "X"}, r"flag_grade must be C, L or S"),
            ({"flag_grade": "C"}, r"flag_grade needs a flag"),
        ]
        got = node("""
          const out = input.queries.map(q => { try { T.matchCommits(input.bundle, q); return null; } catch (e) { return e.message; } });
          console.log(JSON.stringify(out));""", {"bundle": self.bundle, "queries": [q for q, _ in cases]})
        for (q, pattern), message in zip(cases, got):
            self.assertIsNotNone(message, q)
            self.assertRegex(message, pattern)

    def test_a_flag_without_eda_says_so(self):
        b = dict(self.bundle)
        b["eda"] = None
        got = node("""
          const t = q => { try { T.matchCommits(input, q); return 'ok'; } catch (e) { return e.message; } };
          console.log(JSON.stringify([t({ flag: 'size_high' }), t({ min_lines: 3 })]));""", b)
        self.assertRegex(got[0], r"needs eda data")
        self.assertEqual(got[1], "ok")

    def test_the_filters_reach_the_tools_and_their_schemas(self):
        got = node("""
          const B = input.bundle;
          const tools = Object.fromEntries(T.makeTools(B, { highlight: q => ({ lit: T.matchCommits(B, q).length }), show() {}, note() {}, colorBy() {} }).map(t => [t.name, t]));
          const r = {};
          r.query = tools.query_commits.execute({ min_lines: 5, max_lines: 60, hour: 3, weekday: 'tue', side_branch: false, limit: 1 }).matched;
          r.activity = tools.activity.execute({ by: 'hour', flag: 'size_high', flag_grade: 'S' }).buckets.reduce((s, b) => s + b.total, 0);
          r.highlight = tools.highlight.execute({ who: 'you', xray: true, hour: 5, weekday: '2' }).lit;
          r.finding = tools.highlight.execute(input.highlight).lit;
          const bad = [{ hour: 'x' }, { weekday: true }, { weekday: 1.5 }, { flag_grade: 'Z' }, { side_branch: 'yes' }, { min_lines: 1.5 }];
          r.bad = bad.map(q => { try { tools.query_commits.execute(q); return 'accepted'; } catch (e) { return e.message; } });
          console.log(JSON.stringify(r));""", {"bundle": self.bundle, "highlight": self.eda["looks"]["findings"][0]["highlight"]})
        b = self.bundle
        self.assertEqual(got["query"], len(brute(b, {"min_lines": 5, "max_lines": 60, "hour": 3, "weekday": "tue", "side_branch": False})))
        self.assertEqual(got["highlight"], len(brute(b, {"who": "you", "hour": 5, "weekday": 2})))
        self.assertEqual(got["finding"], len(brute(b, {"who": "you"})))
        self.assertEqual(got["activity"], len(brute(b, {"flag": "size_high", "flag_grade": "S"})))
        self.assertTrue(all(m != "accepted" for m in got["bad"]), got["bad"])
        self.assertIn("hour must be", got["bad"][0])
        self.assertIn("weekday must be", got["bad"][1])


@unittest.skipUnless(shutil.which("node"), "Node is required for the JavaScript twin tests")
class SliceTwin(unittest.TestCase):
    """Every stored profile record is reproduced in JavaScript, once through edaSliceValues and once through the tool."""

    def run_case(self, ds):
        e, problems = eda.run(ds)
        self.assertEqual(problems, [])
        bundle = minimal_bundle(ds, e)
        recs = [r for r in e["profile"]["records"] if r["available"]]
        got = node("""
          const B = input.bundle;
          const eda = T.makeTools(B).find(t => t.name === 'eda');
          const out = input.records.map(r => {
            const { members, values } = T.edaSliceValues(B, r);
            const m = r.measure === 'gap_s' ? [19, 60] : [16, 1];
            const args = { topic: 'slice', fork: r.fork, measure: r.measure };
            if (r.slice.startsWith('class:')) args.who = r.slice.slice(6);
            if (r.slice.startsWith('repo:')) args.repo = r.slice.slice(5);
            const viaTool = eda.execute(args);
            return { members, twin: T.edaSummarize(values, m[0], m[1]), tool: viaTool.statistics, toolMembers: viaTool.members };
          });
          console.log(JSON.stringify(out));""", {"bundle": bundle, "records": recs})
        for r, g in zip(recs, got):
            want = {k: v for k, v in r.items() if k not in ("slice", "fork", "measure", "available", "reason", "members")}
            tag = (r["slice"], r["fork"], r["measure"])
            self.assertEqual(g["members"], r["members"], tag)
            self.assertEqual(g["twin"], want, tag)
            self.assertEqual(g["tool"], want, tag)
            self.assertEqual(g["toolMembers"], r["members"], tag)
        return recs

    def test_every_available_profile_record_is_reproduced(self):
        ds = synthetic_dataset(seed=17, repos=3, per_repo=70, private=("r2",), plant=("ai_hours", "outlier"))
        recs = self.run_case(ds)
        self.assertEqual({r["fork"] for r in recs}, set(stats.FORKS))
        self.assertEqual({r["measure"] for r in recs}, {"lines", "files", "gap_s"})
        kinds = {r["slice"].split(":")[0] for r in recs}
        self.assertEqual(kinds, {"family", "class", "repo"})

    def test_a_second_dataset_with_ties_and_merges(self):
        ds = synthetic_dataset(seed=29, repos=2, per_repo=45, plant=("defects", "confound"))
        recs = self.run_case(ds)
        self.assertGreater(len(recs), 20)


if __name__ == "__main__":
    unittest.main()
