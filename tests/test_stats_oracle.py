"""Brute-force oracles for x3dray/stats.py: every statistic is checked against an independent, slow definition."""

import datetime
import decimal
import itertools
import math
import os
import random
import subprocess
import sys
import unittest
from fractions import Fraction
from pathlib import Path

from x3dray import stats

ROOT = Path(__file__).resolve().parent.parent
EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
BIG = 2 ** 53 - 1


def random_vectors(rng, count, max_n=40, max_v=60):
    """Random int vectors with heavy ties, all-equal, all-zero, singletons and values near 2**53."""
    out = [[], [0], [5], [3, 3], [0, 0, 0], [7] * 12, [0] * 11, [BIG], [BIG, BIG - 1, 0], [1, 2], [2, 1, 3]]
    while len(out) < count:
        n = rng.randint(1, max_n)
        kind = rng.randrange(4)
        if kind == 0:
            xs = [rng.randint(0, max_v) for _ in range(n)]
        elif kind == 1:
            xs = [rng.choice([0, 1, 2, 3]) for _ in range(n)]
        elif kind == 2:
            xs = [rng.randint(0, 1) * rng.randint(0, 5000) for _ in range(n)]
        else:
            xs = [BIG - rng.randint(0, 5) for _ in range(n)]
        out.append(xs)
    return out


def frac_ceil(f):
    return -((-f.numerator) // f.denominator)


class RoundingTests(unittest.TestCase):
    def test_exact_half_boundaries(self):
        self.assertEqual(stats.permille(1, 8), 125)
        self.assertEqual(stats.permille(5, 8), 625)
        self.assertEqual(stats.permille(-1, 3), -333)
        self.assertEqual(stats.permille(1, 2000), 1)
        self.assertEqual(stats.permille(-1, 2000), -1)
        self.assertEqual(stats.permille(1, 4000), 0)
        self.assertEqual(stats.permille(0, 7), 0)
        self.assertEqual(stats.permille(25, 200), 125)
        self.assertEqual(round(Fraction(125, 10)), 12)      # the half-even trap this module avoids
        self.assertEqual(stats.permille(1250, 100000), 13)   # 12.5 per mille -> 13
        self.assertEqual(stats.permille(-1250, 100000), -13)
        self.assertEqual(stats.decimal_str(1, 8, 3), "0.125")
        self.assertEqual(stats.decimal_str(5, 8, 2), "0.63")
        self.assertEqual(stats.decimal_str(-1, 8, 3), "-0.125")
        self.assertEqual(stats.decimal_str(1, 2000, 3), "0.001")
        self.assertEqual(stats.decimal_str(825, 1000, 2), "0.83")
        self.assertEqual(stats.decimal_str(-825, 1000, 2), "-0.83")
        self.assertEqual(stats.decimal_str(-1, 3000, 2), "0.00")
        self.assertEqual(stats.decimal_str(7, 2, 0), "4")
        self.assertEqual(stats.decimal_str(0, 5, 3), "0.000")

    def test_bad_denominator(self):
        with self.assertRaises(ValueError):
            stats.permille(1, 0)
        with self.assertRaises(ValueError):
            stats.decimal_str(1, -2, 2)

    def test_permille_against_decimal(self):
        rng = random.Random(11)
        ctx = decimal.Context(prec=200, rounding=decimal.ROUND_HALF_UP)
        cases = [(rng.randint(-10 ** 6, 10 ** 6), rng.randint(1, 10 ** 5)) for _ in range(500)]
        cases += [(n, d) for d in (8, 16, 2000, 4000, 200) for n in range(-40, 41)]
        for num, den in cases:
            value = ctx.divide(ctx.multiply(decimal.Decimal(num), 1000), decimal.Decimal(den))
            want = int(value.quantize(decimal.Decimal(1), rounding=decimal.ROUND_HALF_UP, context=ctx))
            self.assertEqual(stats.permille(num, den), want, (num, den))
            # sign symmetric
            self.assertEqual(stats.permille(-num, den), -stats.permille(num, den))
            for places in (0, 1, 2, 3):
                q = decimal.Decimal(1).scaleb(-places)
                v = ctx.divide(decimal.Decimal(num), decimal.Decimal(den)).quantize(
                    q, rounding=decimal.ROUND_HALF_UP, context=ctx)
                text = format(v, "f")
                if text.startswith("-") and not any(c in "123456789" for c in text):
                    text = text[1:]
                self.assertEqual(stats.decimal_str(num, den, places), text, (num, den, places))

    def test_huge_values_exact(self):
        self.assertEqual(stats.permille(BIG, BIG), 1000)
        self.assertEqual(stats.permille(BIG, 3 * BIG), 333)
        self.assertEqual(stats.permille(2 * BIG, 3 * BIG), 667)


class BinTests(unittest.TestCase):
    def test_bit_bin_against_bin_and_log2(self):
        xs = {0, 1, 2, 3}
        for k in range(1, 41):
            xs.update({2 ** k - 1, 2 ** k, 2 ** k + 1})
        rng = random.Random(3)
        xs.update(rng.randint(0, 2 ** 40) for _ in range(300))
        for x in sorted(xs):
            want = 0 if x == 0 else len(bin(x)) - 2
            self.assertEqual(stats.bit_bin(x, 99), want, x)
            if x:
                # exact integer log2 via math.isqrt-free check: 2**(b-1) <= x < 2**b
                self.assertTrue(2 ** (want - 1) <= x < 2 ** want)
                if x < 2 ** 40:
                    self.assertEqual(want, math.floor(math.log2(x)) + 1)
            self.assertEqual(stats.bit_bin(x, 5), min(5, want))

    def test_unit_and_cap(self):
        for x in (0, 59, 60, 119, 120, 3600, 10 ** 7):
            q = x // 60
            self.assertEqual(stats.bit_bin(x, 30, 60), 0 if q == 0 else len(bin(q)) - 2)
        self.assertEqual(stats.bit_bin(10 ** 12, 12, 60), 12)
        with self.assertRaises(ValueError):
            stats.bit_bin(-1, 5)


class QuantileTests(unittest.TestCase):
    def test_nearest_rank_against_ceiling(self):
        rng = random.Random(21)
        for xs in random_vectors(rng, 300):
            if not xs:
                with self.assertRaises(ValueError):
                    stats.nearest_rank([], 1, 2)
                continue
            sx = sorted(xs)
            n = len(sx)
            for num, den in list(stats.PCTS.values()) + [(0, 1), (1, 1), (1, 3)]:
                k = frac_ceil(Fraction(n * num, den))
                want = sx[max(k, 1) - 1]
                self.assertEqual(stats.nearest_rank(sx, num, den), want, (xs, num, den))

    def test_median_and_mad_definitions(self):
        rng = random.Random(22)
        for xs in random_vectors(rng, 300):
            if not xs:
                with self.assertRaises(ValueError):
                    stats.lower_median(xs)
                continue
            n = len(xs)
            sx = sorted(xs)
            med = sx[n // 2] if n % 2 else sx[n // 2 - 1]
            self.assertEqual(stats.lower_median(xs), med)
            self.assertEqual(stats.lower_median(xs), stats.nearest_rank(sx, 1, 2))
            dev = sorted(abs(x - med) for x in xs)
            want = dev[n // 2] if n % 2 else dev[n // 2 - 1]
            self.assertEqual(stats.mad(xs), want)
        self.assertEqual(stats.mad([1, 2, 3, 4]), 1)   # lower median 2, deviations 1,0,1,2 -> sorted 0,1,1,2 -> 1
        self.assertEqual(stats.mad([4, 4, 4]), 0)


class ConcentrationTests(unittest.TestCase):
    def test_gini_against_pairwise_definition(self):
        rng = random.Random(31)
        checked = 0
        for xs in random_vectors(rng, 260, max_n=30):
            n, s = len(xs), sum(xs)
            got = stats.gini_ratio(xs)
            if n < 10 or s == 0:
                self.assertIsNone(got, xs)
                continue
            want = Fraction(sum(abs(a - b) for a in xs for b in xs), 2 * n * s)
            self.assertEqual(Fraction(*got), want, xs)
            checked += 1
        self.assertGreater(checked, 100)
        self.assertIsNone(stats.gini_ratio([1] * 9))
        self.assertEqual(Fraction(*stats.gini_ratio([1] * 10)), 0)
        self.assertEqual(Fraction(*stats.gini_ratio([0] * 9 + [5])), Fraction(9, 10))
        self.assertIsNone(stats.gini_ratio([0] * 12))

    def test_top_share_and_half_k(self):
        rng = random.Random(32)
        for xs in random_vectors(rng, 260, max_n=35):
            n, s = len(xs), sum(xs)
            for parts in (10, 4):
                got = stats.top_share_ratio(xs, parts)
                if n < 10 or s == 0:
                    self.assertIsNone(got)
                else:
                    k = frac_ceil(Fraction(n, parts))
                    desc = sorted(xs, reverse=True)
                    self.assertEqual(got, (sum(desc[:k]), s))
            got = stats.half_k(xs)
            if s == 0:
                self.assertIsNone(got)
                continue
            desc = sorted(xs, reverse=True)
            want = next(k for k in range(1, n + 1) if Fraction(sum(desc[:k])) >= Fraction(s, 2))
            self.assertEqual(got, want, xs)
        self.assertEqual(stats.half_k([5, 5]), 1)            # exactly half is enough
        self.assertEqual(stats.half_k([1, 1, 1, 1, 1, 1]), 3)
        self.assertEqual(stats.top_share_ratio(list(range(1, 11))), (10, 55))
        self.assertEqual(stats.top_share_ratio(list(range(1, 12))), (11 + 10, 66))


class SummarizeTests(unittest.TestCase):
    KEYS = ["n", "sum", "min", "max", "p10", "p25", "p50", "p75", "p90", "p99", "mad", "bins", "bin_rule",
            "gini_permille", "top_decile_permille"]

    def test_shape_and_values(self):
        rng = random.Random(41)
        for xs in random_vectors(rng, 120):
            out = stats.summarize(xs, 12, 1)
            if not xs:
                self.assertEqual(out, {"n": 0})
                continue
            self.assertEqual(sorted(out), sorted(self.KEYS))
            self.assertEqual(out["n"], len(xs))
            self.assertEqual(sum(out["bins"]), len(xs))
            self.assertEqual(len(out["bins"]), 13)
            self.assertEqual(out["bin_rule"], {"unit": 1, "cap": 12})
            self.assertEqual(out["min"], min(xs))
            self.assertEqual(out["max"], max(xs))
            self.assertEqual(out["p50"], stats.lower_median(xs))
            self.assertEqual(out["mad"], stats.mad(xs))
            want_bins = [0] * 13
            for x in xs:
                want_bins[min(12, 0 if x == 0 else len(bin(x)) - 2)] += 1
            self.assertEqual(out["bins"], want_bins)
            total = sum(xs)
            self.assertEqual(out["sum"], total if total <= BIG else str(total))
            if len(xs) < 10 or total == 0:
                self.assertIsNone(out["gini_permille"])
                self.assertIsNone(out["top_decile_permille"])
            else:
                ratio = Fraction(*stats.gini_ratio(xs))
                self.assertEqual(out["gini_permille"], stats.permille(ratio.numerator, ratio.denominator))
        self.assertEqual(stats.summarize([], 4), {"n": 0})

    def test_sum_goes_to_string_above_safe_int(self):
        out = stats.summarize([BIG, 1], 4)
        self.assertEqual(out["sum"], str(BIG + 1))
        self.assertEqual(stats.summarize([BIG], 4)["sum"], BIG)
        self.assertEqual(stats.json_int(-BIG), -BIG)
        self.assertEqual(stats.json_int(-BIG - 1), str(-BIG - 1))

    def test_rejects_bad_values(self):
        for bad in ([-1], [2 ** 53], [1.5], [True]):
            with self.assertRaises(ValueError):
                stats.summarize(bad, 4)

    def test_unit_bins(self):
        out = stats.summarize([0, 59, 60, 120, 240], 5, 60)
        self.assertEqual(out["bins"], [2, 1, 1, 1, 0, 0])
        self.assertEqual(out["bin_rule"], {"unit": 60, "cap": 5})

    def test_worked_example(self):
        out = stats.summarize([1, 2, 3, 4, 100], 8)
        self.assertEqual((out["p10"], out["p25"], out["p50"], out["p75"], out["p90"], out["p99"]), (1, 2, 3, 4, 100, 100))


class ForkAndClockTests(unittest.TestCase):
    def test_fork_truth_table(self):
        for m, b, f in itertools.product((0, 1), (0, 1), (0, 1, 15, 16, 400)):
            row = {"m": m, "b": b, "f": f}
            self.assertTrue(stats.in_fork(row, "all"))
            self.assertEqual(stats.in_fork(row, "no_merge"), m == 0)
            self.assertEqual(stats.in_fork(row, "default_branch"), m == 0 and b == 1)
            self.assertEqual(stats.in_fork(row, "no_mass"), m == 0 and f <= 15)
        with self.assertRaises(ValueError):
            stats.in_fork({"m": 0, "b": 1, "f": 1}, "nope")
        self.assertEqual(stats.FORKS, ("all", "no_merge", "default_branch", "no_mass"))

    def test_clock_against_datetime(self):
        rng = random.Random(51)
        ts = [rng.randint(0, 4102444800) for _ in range(2000)] + [rng.randint(-2 * 10 ** 9, -1) for _ in range(300)]
        ts += [0, 86399, 86400, -1, -86400, -86401, 3 * 86400 - 1, 4 * 86400, 7 * 86400 * 3]
        monday = datetime.date(1969, 12, 29)
        for t in ts:
            dt = EPOCH + datetime.timedelta(seconds=t)
            self.assertEqual(stats.hour_of(t), dt.hour, t)
            self.assertEqual(stats.weekday_of(t), dt.weekday(), t)
            self.assertEqual(stats.week_of(t), (dt.date() - monday).days // 7, t)
        self.assertEqual(stats.weekday_of(0), 3)

    def test_gap_pairs(self):
        rows = [{"i": 5, "t": 100}, {"i": 2, "t": 100}, {"i": 9, "t": 90}, {"i": 1, "t": 400}]
        got = stats.gap_pairs(rows)
        self.assertEqual([(r["i"], g) for r, g in got], [(2, 10), (5, 0), (1, 300)])
        self.assertEqual(stats.gap_pairs([]), [])
        self.assertEqual(stats.gap_pairs(rows[:1]), [])


def naive_average_rank2(values):
    out = []
    for x in values:
        less = sum(1 for y in values if y < x)
        equal = sum(1 for y in values if y == x)
        out.append(2 * (Fraction(less) + Fraction(equal + 1, 2)))
    return out


class RankTests(unittest.TestCase):
    def test_cliff_counts_and_identity(self):
        rng = random.Random(61)
        for _ in range(300):
            a = [rng.randint(0, rng.choice([2, 5, 30])) for _ in range(rng.randint(1, 12))]
            b = [rng.randint(0, rng.choice([2, 5, 30])) for _ in range(rng.randint(1, 12))]
            gt = sum(1 for x in a for y in b if x > y)
            lt = sum(1 for x in a for y in b if x < y)
            self.assertEqual(stats.cliff_counts(a, b), (gt, lt))
            pooled = a + b
            r2 = stats.doubled_midranks(pooled)
            n = len(pooled)
            self.assertEqual(sum(r2[:len(a)]) - len(a) * (n + 1), gt - lt)
            self.assertEqual(sum(r2[len(a):]) - len(b) * (n + 1), lt - gt)
        self.assertEqual(stats.cliff_counts([], [1, 2]), (0, 0))

    def test_doubled_midranks_against_fraction_ranks(self):
        rng = random.Random(62)
        for _ in range(300):
            vals = [rng.randint(0, rng.choice([1, 3, 10, 100])) for _ in range(rng.randint(0, 25))]
            got = stats.doubled_midranks(vals)
            self.assertEqual([Fraction(v) for v in got], naive_average_rank2(vals))
        self.assertEqual(stats.doubled_midranks([5, 5, 5]), [4, 4, 4])
        self.assertEqual(stats.doubled_midranks([3, 1, 2]), [6, 2, 4])


class TotalVariationTests(unittest.TestCase):
    def hist(self, xs):
        out = {}
        for x in xs:
            out[x] = out.get(x, 0) + 1
        return out

    def test_against_fraction_histograms(self):
        rng = random.Random(71)
        for _ in range(300):
            a = [rng.randint(0, 6) for _ in range(rng.randint(1, 20))]
            b = [rng.randint(0, 6) for _ in range(rng.randint(1, 20))]
            ca, cb = self.hist(a), self.hist(b)
            t, k, diff = stats.tv_parts(ca, cb, len(a), len(b))
            keys = sorted(set(ca) | set(cb))
            shares = {x: Fraction(ca.get(x, 0), len(a)) - Fraction(cb.get(x, 0), len(b)) for x in keys}
            tv = sum(abs(v) for v in shares.values()) / 2
            self.assertEqual(Fraction(t, 2 * len(a) * len(b)), tv)
            top = max(abs(v) for v in shares.values())
            self.assertEqual(abs(shares[k]), top)
            self.assertEqual(k, min(x for x in keys if abs(shares[x]) == top))   # ties to the smallest key
            self.assertEqual(Fraction(diff, len(a) * len(b)), shares[k])
            # a given reference bin is honoured
            ref = rng.randint(0, 9)
            t2, k2, d2 = stats.tv_parts(ca, cb, len(a), len(b), ref)
            self.assertEqual((t2, k2), (t, ref))
            self.assertEqual(d2, ca.get(ref, 0) * len(b) - cb.get(ref, 0) * len(a))

    def test_missing_ref_bin_and_ties(self):
        t, k, diff = stats.tv_parts({1: 2}, {2: 2}, 2, 2, ref_bin=7)
        self.assertEqual((k, diff), (7, 0))
        self.assertEqual(t, 8)
        t, k, diff = stats.tv_parts({1: 2}, {2: 2}, 2, 2)
        self.assertEqual((t, k, diff), (8, 1, 4))
        self.assertEqual(stats.tv_parts({}, {}, 0, 0), (0, None, 0))


class GeneratorTests(unittest.TestCase):
    def test_reference_vectors(self):
        g = stats.SplitMix64(0)
        self.assertEqual([g.next_u64() for _ in range(3)],
                         [0xE220A8397B1DCDAF, 0x6E789E6AA1B965F4, 0x06C45D188009454F])

    def test_below(self):
        g = stats.SplitMix64(12345)
        seen = set()
        for _ in range(20000):
            v = g.below(24)
            self.assertTrue(0 <= v < 24)
            seen.add(v)
        self.assertEqual(seen, set(range(24)))
        self.assertEqual(stats.SplitMix64(7).below(1), 0)
        with self.assertRaises(ValueError):
            g.below(0)
        # exact multiply-shift: the top bits of the 64-bit output
        a, b = stats.SplitMix64(99), stats.SplitMix64(99)
        self.assertEqual(a.below(1000), (b.next_u64() * 1000) >> 64)

    def test_seed_from_stable(self):
        label = "contrast:ändring/☃"
        import hashlib
        self.assertEqual(stats.seed_from(label), int(hashlib.sha256(label.encode("utf-8")).hexdigest()[:16], 16))
        self.assertEqual(stats.seed_from(label), stats.seed_from(label))
        self.assertNotEqual(stats.seed_from("a"), stats.seed_from("b"))
        self.assertTrue(0 <= stats.seed_from("a") < 2 ** 64)
        # seeds above 2**64 are masked, negative ones wrap
        self.assertEqual(stats.SplitMix64(2 ** 64 + 5).next_u64(), stats.SplitMix64(5).next_u64())

    def test_uniformity_rough(self):
        g = stats.SplitMix64(1)
        counts = [0] * 10
        for _ in range(50000):
            counts[g.below(10)] += 1
        for c in counts:
            self.assertTrue(4500 < c < 5500, counts)


def exact_tail_cliff(values, n_draw, s_abs_obs):
    """Enumerate every assignment of n_draw of the pooled values to the drawn group; statistic from pair counts."""
    n = len(values)
    hit = total = 0
    for chosen in itertools.combinations(range(n), n_draw):
        drawn = [values[i] for i in chosen]
        rest = [values[i] for i in range(n) if i not in chosen]
        s = sum((x > y) - (x < y) for x in drawn for y in rest)
        total += 1
        hit += abs(s) >= s_abs_obs
    return hit, total


def exact_tail_tv(bins, n_draw, t_obs):
    n = len(bins)
    keys = sorted(set(bins))
    hit = total = 0
    for chosen in itertools.combinations(range(n), n_draw):
        drawn = [bins[i] for i in chosen]
        rest = [bins[i] for i in range(n) if i not in chosen]
        t = sum(abs(drawn.count(k) * len(rest) - rest.count(k) * len(drawn)) for k in keys)
        total += 1
        hit += t >= t_obs
    return hit, total


def naive_cliff_perm(r2, n_draw, s_abs_obs, M, rng):
    arr = list(r2)
    n = len(arr)
    count = 0
    for _ in range(M):
        for j in range(n_draw):
            r = j + rng.below(n - j)
            arr[j], arr[r] = arr[r], arr[j]
        s = 0
        for v in arr[:n_draw]:
            s += v
        s -= n_draw * (n + 1)
        if s < 0:
            s = -s
        if s >= s_abs_obs:
            count += 1
    return count


def naive_tv_perm(bins, n_draw, t_obs, M, rng):
    arr = list(bins)
    n = len(arr)
    keys = sorted(set(arr))
    count = 0
    for _ in range(M):
        for j in range(n_draw):
            r = j + rng.below(n - j)
            arr[j], arr[r] = arr[r], arr[j]
        drawn, rest = arr[:n_draw], arr[n_draw:]
        t = 0
        for k in keys:
            t += abs(drawn.count(k) * len(rest) - rest.count(k) * len(drawn))
        if t >= t_obs:
            count += 1
    return count


class PermutationTests(unittest.TestCase):
    def tolerance(self, p, M):
        return 5 * math.sqrt(p * (1 - p) / M) + 1 / (M + 1)

    def test_cliff_against_enumeration(self):
        rng = random.Random(81)
        M = 999
        for case in range(25):
            n = rng.randint(2, 10)
            n_draw = rng.randint(1, n - 1)
            values = [rng.randint(0, rng.choice([2, 4, 20])) for _ in range(n)]
            r2 = stats.doubled_midranks(values)
            obs = abs(sum(r2[:n_draw]) - n_draw * (n + 1))
            hit, total = exact_tail_cliff(values, n_draw, obs)
            # the identity ties the rank statistic to the pair counts for the observed split
            gt, lt = stats.cliff_counts(values[:n_draw], values[n_draw:])
            self.assertEqual(abs(gt - lt), obs)
            count = stats.perm_count_cliff(r2, n_draw, obs, M, stats.SplitMix64(case))
            p_exact = Fraction(hit, total)
            p_mc = Fraction(count + 1, M + 1)
            self.assertLessEqual(abs(float(p_mc - p_exact)), self.tolerance(float(p_exact), M), (values, n_draw))

    def test_tv_against_enumeration(self):
        rng = random.Random(82)
        M = 999
        for case in range(25):
            n = rng.randint(2, 10)
            n_draw = rng.randint(1, n - 1)
            bins = [rng.randint(0, rng.choice([1, 2, 4])) for _ in range(n)]
            ca, cb = {}, {}
            for v in bins[:n_draw]:
                ca[v] = ca.get(v, 0) + 1
            for v in bins[n_draw:]:
                cb[v] = cb.get(v, 0) + 1
            t_obs = stats.tv_parts(ca, cb, n_draw, n - n_draw)[0]
            hit, total = exact_tail_tv(bins, n_draw, t_obs)
            count = stats.perm_count_tv(bins, n_draw, t_obs, M, stats.SplitMix64(case + 100))
            p_exact = Fraction(hit, total)
            p_mc = Fraction(count + 1, M + 1)
            self.assertLessEqual(abs(float(p_mc - p_exact)), self.tolerance(float(p_exact), M), (bins, n_draw))

    def test_matches_naive_reimplementation_and_does_not_mutate(self):
        rng = random.Random(83)
        for case in range(50):
            n = rng.randint(2, 30)
            n_draw = rng.randint(1, n)
            values = [rng.randint(0, 15) for _ in range(n)]
            r2 = stats.doubled_midranks(values)
            snapshot = list(r2)
            obs = rng.randint(0, n_draw * (n - n_draw) + 1)
            M = rng.randint(1, 60)
            r_a, r_b = stats.SplitMix64(case), stats.SplitMix64(case)
            self.assertEqual(stats.perm_count_cliff(r2, n_draw, obs, M, r_a), naive_cliff_perm(r2, n_draw, obs, M, r_b))
            self.assertEqual(r_a.state, r_b.state)           # same rng consumption order
            self.assertEqual(r2, snapshot)
            bins = [rng.randint(0, 5) for _ in range(n)]
            bsnap = list(bins)
            t_obs = rng.randint(0, 2 * n * n)
            r_a, r_b = stats.SplitMix64(case + 7), stats.SplitMix64(case + 7)
            self.assertEqual(stats.perm_count_tv(bins, n_draw, t_obs, M, r_a), naive_tv_perm(bins, n_draw, t_obs, M, r_b))
            self.assertEqual(r_a.state, r_b.state)
            self.assertEqual(bins, bsnap)

    def test_degenerate_counts(self):
        r2 = stats.doubled_midranks([1, 2, 3, 4])
        self.assertEqual(stats.perm_count_cliff(r2, 2, 0, 17, stats.SplitMix64(1)), 17)    # |S| >= 0 always
        self.assertEqual(stats.perm_count_cliff(r2, 2, 5, 17, stats.SplitMix64(1)), 0)     # max |S| is 4
        self.assertEqual(stats.perm_count_cliff(r2, 2, 1, 0, stats.SplitMix64(1)), 0)
        self.assertEqual(stats.perm_count_tv([0, 0, 1, 1], 2, 0, 9, stats.SplitMix64(1)), 9)
        with self.assertRaises(ValueError):
            stats.perm_count_cliff(r2, 5, 1, 3, stats.SplitMix64(1))


def textbook_bh_rejected(ps, m, alpha):
    """Step-up: with ps sorted ascending find the largest i with p_(i) <= alpha*i/m, reject the first i."""
    ordered = sorted(ps)
    cut = 0
    for i, (p, _) in enumerate(ordered, 1):
        if p * m <= alpha * i:
            cut = i
    return {name for _, name in ordered[:cut]}


class BenjaminiHochbergTests(unittest.TestCase):
    def random_ps(self, rng):
        n = rng.randint(1, 14)
        grid = rng.choice([4, 20, 100, 1000])
        return [(Fraction(rng.randint(1, grid), grid), f"L{k:02}") for k in range(n)]

    def test_against_textbook_scan(self):
        rng = random.Random(91)
        for _ in range(500):
            ps = self.random_ps(rng)
            for m in (len(ps), len(ps) + rng.randint(1, 8)):
                q = stats.bh_q([(name, p) for p, name in ps], m)
                self.assertEqual(sorted(q), sorted(name for _, name in ps))
                for alpha in (Fraction(1, 100), Fraction(1, 20), Fraction(1, 10)):
                    got = {name for name, v in q.items() if v <= alpha}
                    self.assertEqual(got, textbook_bh_rejected(ps, m, alpha), (ps, m, alpha))
                for v in q.values():
                    self.assertTrue(0 < v <= 1)

    def test_monotone_and_padding(self):
        rng = random.Random(92)
        for _ in range(200):
            ps = self.random_ps(rng)
            m = len(ps) + rng.randint(0, 3)
            q = stats.bh_q([(name, p) for p, name in ps], m)
            for (p1, n1), (p2, n2) in itertools.combinations(ps, 2):
                if p1 <= p2:
                    self.assertLessEqual(q[n1], q[n2])
            k = rng.randint(1, 5)
            padded = [(name, p) for p, name in ps] + [(f"Z{j}", Fraction(1)) for j in range(k)]
            q2 = stats.bh_q(padded, m + k)
            for name in q:
                self.assertGreaterEqual(q2[name], q[name])
            for j in range(k):
                self.assertEqual(q2[f"Z{j}"], 1)

    def test_worked_example_and_errors(self):
        q = stats.bh_q([("a", Fraction(1, 100)), ("b", Fraction(4, 100)), ("c", Fraction(3, 100))], 12)
        self.assertEqual(q["a"], Fraction(12, 100))
        self.assertEqual(q["c"], Fraction(16, 100))   # min(12*.03/2, q_b)
        self.assertEqual(q["b"], Fraction(16, 100))
        self.assertEqual(stats.bh_q([], 12), {})
        self.assertEqual(stats.bh_q([("only", Fraction(9, 10))], 12), {"only": Fraction(1)})
        with self.assertRaises(ValueError):
            stats.bh_q([("a", Fraction(1, 2)), ("b", Fraction(1, 2))], 1)
        with self.assertRaises(ValueError):
            stats.bh_q([("a", Fraction(0))], 3)

    def test_ties_do_not_depend_on_order(self):
        p = Fraction(1, 10)
        q = stats.bh_q([("b", p), ("a", p), ("B", p)], 3)
        self.assertEqual(q, {"a": Fraction(1, 10), "b": Fraction(1, 10), "B": Fraction(1, 10)})
        # equal p means equal q whatever the id order, so the tie order cannot change a result
        for ids in itertools.permutations("aBb"):
            self.assertEqual(stats.bh_q([(i, p) for i in ids], 3), q)


class DeterminismTests(unittest.TestCase):
    SCRIPT = (
        "import json\n"
        "from fractions import Fraction\n"
        "from x3dray import stats\n"
        "xs=[3,1,4,1,5,9,2,6,5,3,5,8,9,7,9]\n"
        "q=stats.bh_q([('x%d'%i,Fraction(i+1,20)) for i in range(7)],12)\n"
        "g=stats.SplitMix64(stats.seed_from('k'))\n"
        "n=stats.perm_count_cliff(stats.doubled_midranks(xs),6,5,200,g)\n"
        "print(json.dumps([stats.summarize(xs,8),{k:str(v) for k,v in q.items()},n],sort_keys=True))\n"
    )

    def test_identical_under_hash_seeds(self):
        outs = []
        for seed in ("0", "1", "2"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE="1")
            run = subprocess.run([sys.executable, "-c", self.SCRIPT], cwd=ROOT, env=env, capture_output=True,
                                 text=True, check=True)
            outs.append(run.stdout)
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], outs[2])


class HygieneTests(unittest.TestCase):
    def test_no_float_random_or_round_in_source(self):
        import ast
        tree = ast.parse((ROOT / "x3dray" / "stats.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                self.assertNotIsInstance(node.value, float)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                self.assertFalse(any(n in ("random", "time", "datetime", "math") for n in names), names)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("round", "float"))


if __name__ == "__main__":
    unittest.main()
