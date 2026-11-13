"""Exact integer arithmetic for the EDA stage.

Every statistic here is an int, an exact (numerator, denominator) pair or a Fraction: no float, no round(), no
random module and no wall clock, so the same rows give the same bytes on every run and every Python. Ratios that
reach a file are int permille or exact pairs. Rounding is half away from zero on integers (permille, decimal_str);
Python's round() is half-even and is never used.

Row contract shared by the stage, the looks and the JavaScript twin: a row is a dict with int keys i (index in
bundle.commits), t (unix seconds, author time, UTC), a (lines added), d (lines deleted), f (files changed), m (1 for
a merge), b (1 on the default branch) and str keys r (repo name) and c (author class).

The descriptive subset (bit_bin, nearest_rank, lower_median, mad, gini, top_share, summarize, in_fork, hour_of,
weekday_of) is mirrored in x3dray/web/tools.js so a slice asked for in the page is computed with the same
arithmetic as a stored profile. The inference parts (permutation counts, Benjamini-Hochberg) are Python-only.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left, bisect_right
from fractions import Fraction

FORKS = ("all", "no_merge", "default_branch", "no_mass")
MASS_FILES = 15            # a commit touching more files than this is a mass edit
SAFE_INT = 2 ** 53 - 1     # largest integer a JavaScript number holds exactly
PCTS = {"p10": (1, 10), "p25": (1, 4), "p50": (1, 2), "p75": (3, 4), "p90": (9, 10), "p99": (99, 100)}

M64 = (1 << 64) - 1
MIN_SHARE_N = 10           # Gini and top-decile share are undefined below this many values


def permille(num: int, den: int) -> int:
    """num/den times 1000, rounded half away from zero."""
    if den <= 0:
        raise ValueError("denominator must be positive")
    sign = -1 if num < 0 else 1
    return sign * ((2000 * abs(num) + den) // (2 * den))


def decimal_str(num: int, den: int, places: int) -> str:
    """num/den as a decimal string with `places` digits, rounded half away from zero."""
    if den <= 0:
        raise ValueError("denominator must be positive")
    if places < 0:
        raise ValueError("places must not be negative")
    v = (2 * 10 ** places * abs(num) + den) // (2 * den)
    digits = str(v).rjust(places + 1, "0")
    text = digits[:-places] + "." + digits[-places:] if places > 0 else digits
    return "-" + text if num < 0 and v > 0 else text


def bit_bin(x: int, cap: int, unit: int = 1) -> int:
    """Log2 bin: 0 for x < unit, then the bit length of x // unit, never above cap."""
    if x < 0:
        raise ValueError("bit_bin needs a non-negative value")
    return min(cap, (x // unit).bit_length())


def nearest_rank(sorted_xs: list[int], num: int, den: int) -> int:
    """Nearest-rank quantile num/den: the ceil(n*num/den)-th smallest value, at least the first."""
    n = len(sorted_xs)
    if n == 0:
        raise ValueError("nearest_rank needs at least one value")
    k = max(1, (n * num + den - 1) // den)
    return sorted_xs[k - 1]


def lower_median(xs: list[int]) -> int:
    if not xs:
        raise ValueError("lower_median needs at least one value")
    return sorted(xs)[(len(xs) - 1) // 2]


def mad(xs: list[int]) -> int:
    """Median absolute deviation about the lower median, itself a lower median."""
    med = lower_median(xs)
    return lower_median([abs(x - med) for x in xs])


def gini_ratio(xs: list[int]) -> tuple[int, int] | None:
    """Gini coefficient as an unreduced (num, den); None below MIN_SHARE_N values or when the total is zero."""
    n = len(xs)
    total = sum(xs)
    if n < MIN_SHARE_N or total <= 0:
        return None
    weighted = sum(k * x for k, x in enumerate(sorted(xs), 1))
    return 2 * weighted - (n + 1) * total, n * total


def top_share_ratio(xs: list[int], parts: int = 10) -> tuple[int, int] | None:
    """(sum of the ceil(n/parts) largest values, total); None under the same guard as gini_ratio."""
    n = len(xs)
    total = sum(xs)
    if n < MIN_SHARE_N or total <= 0:
        return None
    k = (n + parts - 1) // parts
    return sum(sorted(xs, reverse=True)[:k]), total


def half_k(xs: list[int]) -> int | None:
    """Fewest values (largest first) that together reach half of the total; None when the total is zero."""
    total = sum(xs)
    if total <= 0:
        return None
    run = 0
    for k, x in enumerate(sorted(xs, reverse=True), 1):
        run += x
        if 2 * run >= total:
            return k
    return None


def json_int(v: int) -> int | str:
    """An int a JavaScript number holds exactly stays an int; a larger one travels as a decimal string."""
    return v if abs(v) <= SAFE_INT else str(v)


def summarize(values: list[int], bin_cap: int, bin_unit: int = 1) -> dict:
    """Distribution profile of non-negative ints below 2**53: n, sum, min, max, six quantiles, MAD, log2 bins,
    Gini and top-decile share in permille (None below ten values). An empty input is just {'n': 0}."""
    n = len(values)
    if n == 0:
        return {"n": 0}
    if any(type(v) is not int or v < 0 or v > SAFE_INT for v in values):
        raise ValueError("summarize needs ints in [0, 2**53)")
    xs = sorted(values)
    bins = [0] * (bin_cap + 1)
    for x in xs:
        bins[bit_bin(x, bin_cap, bin_unit)] += 1
    gini = gini_ratio(xs)
    top = top_share_ratio(xs, 10)
    out = {"n": n, "sum": json_int(sum(xs)), "min": xs[0], "max": xs[-1]}
    for name, (num, den) in PCTS.items():
        out[name] = nearest_rank(xs, num, den)
    out["mad"] = mad(xs)
    out["bins"] = bins
    out["bin_rule"] = {"unit": bin_unit, "cap": bin_cap}
    out["gini_permille"] = permille(*gini) if gini else None
    out["top_decile_permille"] = permille(*top) if top else None
    return out


def in_fork(row: dict, fork: str) -> bool:
    if fork == "all":
        return True
    if fork == "no_merge":
        return not row["m"]
    if fork == "default_branch":
        return row["b"] == 1 and not row["m"]
    if fork == "no_mass":
        return not row["m"] and row["f"] <= MASS_FILES
    raise ValueError(f"unknown fork {fork!r}")


def fork_aliases(rows: list[dict]) -> dict[str, str]:
    """fork -> the first fork, in FORKS order, that holds exactly the same rows (by row index). A history with no merges,
    only default-branch commits and no mass edit makes all four forks one set; counting them as four agreeing checks
    would grade a pattern on a variation that never varied, so the graders count each distinct set once."""
    first: dict[tuple, str] = {}
    return {fork: first.setdefault(tuple(r["i"] for r in rows if in_fork(r, fork)), fork) for fork in FORKS}


def hour_of(t: int) -> int:
    return (t % 86400) // 3600


def weekday_of(t: int) -> int:
    """Monday is 0; 1970-01-01 was a Thursday. Floor division keeps negative t defined."""
    return ((t // 86400) + 3) % 7


def week_of(t: int) -> int:
    """Whole weeks since the Monday before the epoch (1969-12-29)."""
    return ((t // 86400) + 3) // 7


def gap_pairs(members: list[dict]) -> list[tuple[dict, int]]:
    """Each member after the first, with the seconds since the one before it in (t, i) order. The first member has
    no gap and is left out: unknown is not zero."""
    ordered = sorted(members, key=lambda r: (r["t"], r["i"]))
    return [(ordered[k], ordered[k]["t"] - ordered[k - 1]["t"]) for k in range(1, len(ordered))]


def cliff_counts(a: list[int], b: list[int]) -> tuple[int, int]:
    """(#pairs with x > y, #pairs with x < y) over x in a, y in b. S = gt - lt; delta = S / (len(a) * len(b))."""
    bs = sorted(b)
    gt = lt = 0
    for x in a:
        gt += bisect_left(bs, x)
        lt += len(bs) - bisect_right(bs, x)
    return gt, lt


def doubled_midranks(values: list[int]) -> list[int]:
    """Twice the average rank of each value (a tie group at positions p..q gets p + q), in input order.
    For a pooled sample of n with group A, S = sum(r2 over A) - n_A * (n + 1) = gt - lt."""
    order = sorted(range(len(values)), key=lambda k: values[k])
    out = [0] * len(values)
    pos = 0
    while pos < len(order):
        end = pos
        while end + 1 < len(order) and values[order[end + 1]] == values[order[pos]]:
            end += 1
        for k in order[pos:end + 1]:
            out[k] = (pos + 1) + (end + 1)
        pos = end + 1
    return out


def tv_parts(counts_a: dict, counts_b: dict, n_a: int, n_b: int, ref_bin=None) -> tuple[int, object, int]:
    """Integer parts of the total variation distance between two histograms (distance = T / (2 * n_a * n_b)):
    T = sum_k |a_k*n_b - b_k*n_a|, the bin k* where the shares differ most (ties to the smallest key, or the
    given ref_bin) and that bin's signed difference a_k*n_b - b_k*n_a (0 when the bin is absent)."""
    keys = sorted(set(counts_a) | set(counts_b))
    diffs = {k: counts_a.get(k, 0) * n_b - counts_b.get(k, 0) * n_a for k in keys}
    total = sum(abs(v) for v in diffs.values())
    if ref_bin is None:
        best = None
        for k in keys:
            if best is None or abs(diffs[k]) > abs(diffs[best]):
                best = k
        ref_bin = best
    return total, ref_bin, diffs.get(ref_bin, 0)


class SplitMix64:
    """Steele, Lea and Flood's 64-bit generator: an explicit, portable stream so a seed fixes every permutation."""

    def __init__(self, seed: int):
        self.state = seed & M64

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & M64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
        return z ^ (z >> 31)

    def below(self, k: int) -> int:
        """An integer in [0, k) by exact multiply-shift."""
        if k < 1:
            raise ValueError("below needs k >= 1")
        return (self.next_u64() * k) >> 64


def seed_from(label: str) -> int:
    return int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:8], "big")


def perm_count_cliff(r2: list[int], n_draw: int, s_abs_obs: int, M: int, rng: SplitMix64) -> int:
    """How many of M label permutations give |S| >= s_abs_obs. The array is shuffled in place from permutation to
    permutation (a Markov chain whose every state is uniform), only its first n_draw cells are re-drawn each time.
    |S| is the same whichever group is drawn, so callers draw the smaller one."""
    arr = list(r2)
    n = len(arr)
    if not 0 <= n_draw <= n:
        raise ValueError("n_draw out of range")
    off = n_draw * (n + 1)
    count = 0
    for _ in range(M):
        for j in range(n_draw):
            r = j + rng.below(n - j)
            arr[j], arr[r] = arr[r], arr[j]
        if abs(sum(arr[:n_draw]) - off) >= s_abs_obs:
            count += 1
    return count


def perm_count_tv(bins: list[int], n_draw: int, t_obs: int, M: int, rng: SplitMix64) -> int:
    """How many of M label permutations give T >= t_obs, T as in tv_parts. `bins` holds a bin id (0..K-1) per
    pooled value, the drawn group first; the array is carried over between permutations as in perm_count_cliff."""
    arr = list(bins)
    n = len(arr)
    if not 0 <= n_draw <= n:
        raise ValueError("n_draw out of range")
    k_bins = max(arr) + 1 if arr else 0
    tot = [0] * k_bins
    for v in arr:
        tot[v] += 1
    n_other = n - n_draw
    count = 0
    for _ in range(M):
        for j in range(n_draw):
            r = j + rng.below(n - j)
            arr[j], arr[r] = arr[r], arr[j]
        d = [0] * k_bins
        for v in arr[:n_draw]:
            d[v] += 1
        if sum(abs(d[k] * n_other - (tot[k] - d[k]) * n_draw) for k in range(k_bins)) >= t_obs:
            count += 1
    return count


def bh_q(items: list[tuple[str, Fraction]], m: int) -> dict[str, Fraction]:
    """Benjamini-Hochberg adjusted q-values over a declared family of size m (items are the looks that produced a
    p; the rest of the family counts through m). q_i = min(q_{i+1}, m * p_i / i), capped at 1."""
    if m < len(items):
        raise ValueError("family size m is smaller than the number of items")
    ordered = sorted(items, key=lambda it: (it[1], it[0]))
    out: dict[str, Fraction] = {}
    running = Fraction(1)
    for rank in range(len(ordered), 0, -1):
        name, p = ordered[rank - 1]
        if not 0 < p <= 1:
            raise ValueError("p must lie in (0, 1]")
        running = min(running, Fraction(m) * p / rank)
        out[name] = running
    return out
