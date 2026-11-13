"""A declared battery of twelve looks at the family's commits, each with a label-permutation null and a robustness grade.

Exploration here is disciplined, not free: the twelve looks are fixed in code before any row is read (six contrasts
between the two largest author classes, six drifts of one class between its later and earlier commits), so the family
size m behind the Benjamini-Hochberg correction never depends on the data. A look that cannot run (a group under
MIN_SIDE commits, or no pair of classes) stays in the ledger and enters the correction with p = 1.

Every degree of freedom that could rescue or kill a finding is run and printed: four forks of the row set (a fork that
holds the same commits as an earlier one counts once), leave-one-repo-out, per-repo strata, interleaved-week halves. The
grade is a pure function of those printed counts (grade()). It is a robustness grade over declared checks, never a chance
estimate, and its label is not a tie grade.

Statistics are the exact integers of x3dray/stats.py: Cliff's delta as (gt - lt) / (n_a * n_b), total variation as
T / (2 * n_a * n_b), a seeded SplitMix64 permutation null counted with an add-one rule, Fractions for q. No float, no
random module, no clock. Same rows in, same bytes out, whatever order the rows arrive in.

verify() re-derives every number from the rows with naive code (counting against sorted values, Fraction histograms, a
from-scratch shuffle per permutation, a textbook step-up) and reports each difference by look id and field name only.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from fractions import Fraction

from . import stats

THRESHOLDS = {
    "MIN_SIDE": 20,              # commits a group needs before a look runs, and before a fork or repo removal counts
    "MIN_HALF": 8,               # commits per group in each interleaved-week half
    "FLOOR_CLIFF": (147, 1000),  # |Cliff's delta| must reach 0.147 (a conventional "small" effect)
    "FLOOR_TV": (1, 10),         # total variation distance must reach 0.1
    "ALPHA": (1, 20),            # a look is surprising when its add-one count is at most 1/20
    "Q_BH": (1, 10),             # and survives the correction when q is at most 1/10 (below that it is Suspected)
    "Q_CONFIRM": (1, 20),        # Confirmed needs q at most 1/20; between 1/20 and 1/10 the best grade is Likely
    "WORK_CAP": 10_000_000,      # permutations x drawn commits, summed over the evaluable looks
    "M_MIN": 199,
    "M_MAX": 999,
    "MIN_STRATUM_SIDE": 20,      # commits per group inside one repo for that repo to act as a stratum
}

CONTRAST_MEASURES = ("lines", "files", "gap_s", "hour4", "weekday", "repo")
DRIFT_MEASURES = ("lines", "hour4", "weekday")
DECLARED = (
    [("contrast:" + m, "contrast", m) for m in CONTRAST_MEASURES]
    + [(f"drift:{w}:{m}", "drift", m) for w in ("first", "second") for m in DRIFT_MEASURES]
)
M_DECLARED = len(DECLARED)

CLASS_ORDER = ("you", "ai", "bot")
ORDINAL = ("lines", "files", "gap_s")
SEED_PREFIX = "x3dray.eda.v1|"
NULL_NAME = "label permutation with an add-one count"

PAIR_REASON = "fewer than two author classes have {n} or more commits outside merges"
SMALL_REASON = "a group has fewer than {n} commits (n_a={a}, n_b={b})"

SCOPE = ("the recorded commits of this family; commits are treated as exchangeable under the null; "
         "not a population and not a cause")
BH_NOTE = "BH assumes independent or positively dependent looks; looks here share commits, so q is indicative"
SAY_AT_MOST = {
    "C": "a pattern that survived every declared check in this history; not causal, not predictive",
    "L": "a likely pattern in this history; at least one robustness check failed or could not be run",
    "S": "worth a second look; do not call it real",
}
GRADE_WORD = {"C": "Confirmed", "L": "Likely", "S": "Suspected"}
MEASURE_PHRASE = {
    "lines": "lines changed per commit",
    "files": "files changed per commit",
    "gap_s": "quiet time before a commit",
    "hour4": "UTC four-hour block",
    "weekday": "UTC weekday",
    "repo": "which repo the commit is in",
}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def grade_rule() -> str:
    t = THRESHOLDS
    return (
        "A look is graded by the first rule that matches: not run or effect below the floor "
        f"(|Cliff's delta| {t['FLOOR_CLIFF'][0]}/{t['FLOOR_CLIFF'][1]}, total variation {t['FLOOR_TV'][0]}/{t['FLOOR_TV'][1]}) "
        f"or add-one count above {t['ALPHA'][0]}/{t['ALPHA'][1]} gives no grade; q above {t['Q_BH'][0]}/{t['Q_BH'][1]} "
        "or an opposite effect in a repo gives Suspected; otherwise Likely unless q is at most "
        f"{t['Q_CONFIRM'][0]}/{t['Q_CONFIRM'][1]}, at least one repo holds "
        f"{t['MIN_STRATUM_SIDE']} commits of each group, the interleaved-week halves replicate and at least 3 distinct forks "
        "(forks holding exactly the same commits count once) and "
        "3 leave-one-repo-out runs are evaluable and all agree, which gives Confirmed. "
        "A robustness grade over declared checks, not a likelihood or a chance estimate; "
        "Confirmed does not mean causal or predictive."
    )


def manifest_sha256() -> str:
    body = [[i, k, m] for i, k, m in DECLARED] + [THRESHOLDS]
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


def _sort_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["t"], r["i"]))


def choose_pair(rows: list[dict]) -> tuple[str, str] | None:
    """The two author classes with the most commits outside merges, each with at least MIN_SIDE; the larger comes
    first, ties go to the earlier class in you < ai < bot."""
    counts = {c: 0 for c in CLASS_ORDER}
    for r in rows:
        if not r["m"] and r["c"] in counts:
            counts[r["c"]] += 1
    eligible = [c for c in CLASS_ORDER if counts[c] >= THRESHOLDS["MIN_SIDE"]]
    if len(eligible) < 2:
        return None
    eligible.sort(key=lambda c: (-counts[c], CLASS_ORDER.index(c)))
    return eligible[0], eligible[1]


def _value(measure: str, row: dict):
    if measure == "lines":
        return row["a"] + row["d"]
    if measure == "files":
        return row["f"]
    if measure == "hour4":
        return stats.hour_of(row["t"]) // 4
    if measure == "weekday":
        return stats.weekday_of(row["t"])
    return row["r"]


def _look_classes(look_id: str, kind: str, pair: tuple[str, str]):
    """Contrasts compare the pair; a drift follows one class (first or second of the pair)."""
    if kind == "contrast":
        return pair
    return pair[0] if look_id.split(":")[1] == "first" else pair[1]


class _Sets:
    """Restricted, (t, i)-ordered views of all rows, cached by restriction. Gaps are measured inside the restricted set
    of all rows, before any class is cut out of it."""

    def __init__(self, rows: list[dict]):
        self.all = _sort_rows(rows)
        self.repos = sorted({r["r"] for r in self.all})
        self.aliases = stats.fork_aliases(self.all)
        self._cache: dict = {}

    def get(self, key: tuple):
        hit = self._cache.get(key)
        if hit is None:
            kind, arg = key
            if kind == "fork":
                rs = [r for r in self.all if stats.in_fork(r, arg)]
            elif kind == "drop":
                rs = [r for r in self.all if r["r"] != arg]
            elif kind == "only":
                rs = [r for r in self.all if r["r"] == arg]
            else:
                rs = [r for r in self.all if stats.week_of(r["t"]) % 2 == arg]
            hit = (rs, [None] + [g for _, g in stats.gap_pairs(rs)])
            self._cache[key] = hit
        return hit


def _cut(kind: str, measure: str, classes, rs: list[dict], gaps: list):
    """(group a, group b) as lists of (value, repo) in (t, i) order. Contrast: a is the first class. Drift: a is the
    later half of the class, b the earlier half (the first len // 2 rows)."""
    if kind == "contrast":
        c1, c2 = classes
        a, b = [], []
        for k, r in enumerate(rs):
            if r["c"] not in (c1, c2):
                continue
            if measure == "gap_s":
                if gaps[k] is None:
                    continue
                v = gaps[k]
            else:
                v = _value(measure, r)
            (a if r["c"] == c1 else b).append((v, r["r"]))
        return a, b
    items = [(_value(measure, r), r["r"]) for r in rs if r["c"] == classes]
    kk = len(items) // 2
    return items[kk:], items[:kk]


def _stat(measure: str, a: list, b: list, ref_bin=None) -> dict:
    na, nb = len(a), len(b)
    if measure in ORDINAL:
        gt, lt = stats.cliff_counts([v for v, _ in a], [v for v, _ in b])
        s = gt - lt
        return {"n_a": na, "n_b": nb, "num": s, "den": na * nb, "direction": _sign(s), "k": None}
    ca: dict = {}
    cb: dict = {}
    for v, _ in a:
        ca[v] = ca.get(v, 0) + 1
    for v, _ in b:
        cb[v] = cb.get(v, 0) + 1
    t, k, diff = stats.tv_parts(ca, cb, na, nb, ref_bin)
    return {"n_a": na, "n_b": nb, "num": t, "den": 2 * na * nb, "direction": _sign(diff), "k": k}


def _floor(measure: str, st: dict, half: bool = False) -> bool:
    if st["den"] <= 0:
        return False
    fn, fd = THRESHOLDS["FLOOR_CLIFF" if measure in ORDINAL else "FLOOR_TV"]
    return (2 if half else 1) * fd * abs(st["num"]) >= fn * st["den"]


def _agrees(measure: str, head: dict, st: dict, minimum: int, half: bool = False) -> bool:
    return (st["n_a"] >= minimum and st["n_b"] >= minimum and _floor(measure, st, half)
            and head["direction"] != 0 and st["direction"] == head["direction"])


def _permille(st: dict) -> int:
    return stats.permille(st["num"], st["den"]) if st["den"] > 0 else 0


def _effect(measure: str, st: dict) -> dict:
    return {
        "kind": "cliff_delta" if measure in ORDINAL else "tv",
        "num": st["num"], "den": st["den"], "permille": _permille(st), "direction": st["direction"],
        "dominant_bin": st["k"], "floor_met": _floor(measure, st),
    }


def _checks(look_id: str, kind: str, measure: str, classes, ctx: _Sets, head: dict, head_groups) -> dict:
    t = THRESHOLDS

    def run_key(key):
        rs, gaps = ctx.get(key)
        a, b = _cut(kind, measure, classes, rs, gaps)
        return _stat(measure, a, b, head["k"]), a, b

    per_fork = {}
    evaluable = agree = 0
    for fork in stats.FORKS:
        st, _, _ = run_key(("fork", fork))
        ok = st["n_a"] >= t["MIN_SIDE"] and st["n_b"] >= t["MIN_SIDE"]
        yes = _agrees(measure, head, st, t["MIN_SIDE"])
        same = ctx.aliases[fork] if ctx.aliases[fork] != fork else None
        if same is None:
            evaluable += ok
            agree += yes
        per_fork[fork] = {"n_a": st["n_a"], "n_b": st["n_b"], "evaluable": ok, "permille": _permille(st), "agrees": yes,
                          "same_as": same}

    loro = {"total": 0, "agree": 0}
    strata = None
    if measure != "repo":
        for repo in sorted({repo for _, repo in head_groups[0] + head_groups[1]}):
            st, _, _ = run_key(("drop", repo))
            if st["n_a"] >= t["MIN_SIDE"] and st["n_b"] >= t["MIN_SIDE"]:
                loro["total"] += 1
                loro["agree"] += _agrees(measure, head, st, t["MIN_SIDE"])
        strata = {"n": 0, "same": 0, "opposite": 0}
        for repo in ctx.repos:
            st, _, _ = run_key(("only", repo))
            if st["n_a"] < t["MIN_STRATUM_SIDE"] or st["n_b"] < t["MIN_STRATUM_SIDE"]:
                continue
            strata["n"] += 1
            if head["direction"] != 0 and st["direction"] == head["direction"]:
                strata["same"] += 1
            elif head["direction"] != 0 and st["direction"] == -head["direction"] and _floor(measure, st):
                strata["opposite"] += 1

    per_half = {}
    half_ok = True
    half_agree = True
    for parity in (0, 1):
        st, _, _ = run_key(("parity", parity))
        yes = _agrees(measure, head, st, t["MIN_HALF"], half=True)
        half_ok = half_ok and st["n_a"] >= t["MIN_HALF"] and st["n_b"] >= t["MIN_HALF"]
        half_agree = half_agree and yes
        per_half[str(parity)] = {"n_a": st["n_a"], "n_b": st["n_b"], "permille": _permille(st), "agrees": yes}

    return {
        "forks": {"distinct": len(set(ctx.aliases.values())), "evaluable": evaluable, "agree": agree, "per_fork": per_fork},
        "loro": loro,
        "strata": strata,
        "halves": {"evaluable": half_ok, "replicates": half_agree if half_ok else None, "per_half": per_half},
    }


def _null_count(measure: str, a: list, b: list, head: dict, M: int, seed_label: str) -> int:
    """Draw the smaller group (ties: a). Both groups stay in (t, i) order, drawn group first, so the chain is a pure
    function of the rows."""
    drawn, other = (a, b) if len(a) <= len(b) else (b, a)
    arranged = [v for v, _ in drawn] + [v for v, _ in other]
    rng = stats.SplitMix64(stats.seed_from(seed_label))
    if measure in ORDINAL:
        return stats.perm_count_cliff(stats.doubled_midranks(arranged), len(drawn), abs(head["num"]), M, rng)
    dense = {k: n for n, k in enumerate(sorted(set(arranged)))}
    return stats.perm_count_tv([dense[v] for v in arranged], len(drawn), head["num"], M, rng)


def _bin_label(measure: str, k) -> str:
    if measure == "hour4":
        return f"{4 * k:02d}-{4 * k + 3:02d}"
    if measure == "weekday":
        return WEEKDAYS[k]
    return str(k)


def grade(look: dict) -> tuple[str | None, str]:
    """(grade, reason) from the stored fields of a look; the first rule that matches wins."""
    if look.get("status") != "evaluated":
        return None, "not_run"
    t = THRESHOLDS
    if not look["effect"]["floor_met"]:
        return None, "effect_below_floor"
    nm = look["null_model"]
    an, ad = t["ALPHA"]
    if ad * (nm["k_ge"] + 1) > an * (nm["M"] + 1):
        return None, "not_surprising"
    qn, qd = look["q"]
    tn, td = t["Q_BH"]
    if td * qn > tn * qd:
        return "S", "not_surviving_BH"
    ck = look["checks"]
    if ck["strata"] and ck["strata"]["opposite"] >= 1:
        return "S", "contrary_effect_in_a_repo"
    cn, cd = t["Q_CONFIRM"]
    if cd * qn > cn * qd:
        # Survives the correction at the looser level but not at the level Confirmed needs.
        return "L", "q_above_confirm_level"
    if ck["strata"] is not None and ck["strata"]["n"] == 0:
        # No repo holds both groups in strength, so the within-repo check never ran; an unrun check cannot pass.
        return "L", "no_stratum_with_both_groups"
    h = ck["halves"]
    if not h["evaluable"]:
        return "L", "halves_not_evaluable"
    if h["replicates"] is False:
        return "L", "not_replicated_in_halves"
    f = ck["forks"]
    if f["evaluable"] < 3:
        return "L", "fewer_than_3_distinct_forks_evaluable"
    if f["agree"] < f["evaluable"]:
        return "L", "fork_disagreement"
    lo = ck["loro"]
    if lo["total"] < 3:
        return "L", "leave_one_repo_out_unavailable"
    if lo["agree"] < lo["total"]:
        return "L", "leave_one_repo_out_disagreement"
    return "C", "all_checks_pass"


def render_text(look: dict, labels: dict | None = None) -> str:
    """The sentence for a look, built from its stored numbers only. Group labels come from the look itself;
    `labels` (class -> label) is only the fallback for a look that carries none."""
    if look["status"] != "evaluated":
        return f"{look['id']}: not run ({look['reason']})"
    labels = labels or {}
    ga = (look.get("groups") or {}).get("a", {}).get("label") or labels.get("a", "group a")
    gb = (look.get("groups") or {}).get("b", {}).get("label") or labels.get("b", "group b")
    eff = look["effect"]
    if eff["kind"] == "cliff_delta":
        effect = ("Cliff's delta " + stats.decimal_str(eff["permille"], 1000, 3)
                  + " (positive means the first group is larger)")
    else:
        effect = ("distribution distance " + stats.decimal_str(eff["permille"], 1000, 3)
                  + " (largest gap in " + _bin_label(look["measure"], eff["dominant_bin"]) + ")")
    nm = look["null_model"]
    word = GRADE_WORD.get(look["grade"])
    phrase = word if word else f"no grade ({look['grade_reason']})"
    return (f"{ga} vs {gb}, {MEASURE_PHRASE[look['measure']]}: {effect}; {nm['k_ge']} of {nm['M']} label shuffles "
            f"were at least as extreme (q {look['q_text']}); {phrase}.")


def _blank(look_id: str, kind: str, measure: str, status: str, reason: str | None, groups) -> dict:
    return {
        "id": look_id, "kind": kind, "measure": measure, "status": status, "reason": reason,
        "grade_kind": "robustness", "groups": groups, "effect": None, "null_model": None, "q": None, "q_text": None,
        "checks": None, "grade": None, "grade_reason": "not_run", "say_at_most": None, "text": "", "highlight": None,
        "scope": SCOPE,
    }


def _group_labels(look_id: str, kind: str, pair, labels: dict):
    if kind == "contrast":
        return labels.get(pair[0], pair[0]), labels.get(pair[1], pair[1])
    c = _look_classes(look_id, kind, pair)
    x = labels.get(c, c)
    return "later commits of " + x, "earlier commits of " + x


def run(rows: list[dict], labels: dict[str, str], *, _order: list[int] | None = None) -> dict:
    """Evaluate the twelve declared looks. `_order` is a test hook: the order in which looks draw their permutations
    (each look has its own stream, so it must not change any result)."""
    t = THRESHOLDS
    ctx = _Sets(rows)
    pair = choose_pair(rows)
    pair_reason = None if pair else PAIR_REASON.format(n=t["MIN_SIDE"])
    looks: dict[str, dict] = {}
    heads: dict[str, tuple] = {}
    W = 0

    for look_id, kind, measure in DECLARED:
        if pair is None:
            looks[look_id] = _blank(look_id, kind, measure, "not_run", pair_reason, None)
            continue
        classes = _look_classes(look_id, kind, pair)
        rs, gaps = ctx.get(("fork", "all"))
        a, b = _cut(kind, measure, classes, rs, gaps)
        la, lb = _group_labels(look_id, kind, pair, labels)
        groups = {"a": {"label": la, "n": len(a)}, "b": {"label": lb, "n": len(b)}}
        if min(len(a), len(b)) < t["MIN_SIDE"]:
            reason = SMALL_REASON.format(n=t["MIN_SIDE"], a=len(a), b=len(b))
            looks[look_id] = _blank(look_id, kind, measure, "not_run", reason, groups)
        else:
            looks[look_id] = _blank(look_id, kind, measure, "evaluated", None, groups)
            heads[look_id] = (classes, a, b)
            W += min(len(a), len(b))

    M = max(t["M_MIN"], min(t["M_MAX"], t["WORK_CAP"] // max(1, W)))
    order = list(range(len(DECLARED))) if _order is None else list(_order)
    for idx in order:
        look_id, kind, measure = DECLARED[idx]
        if look_id not in heads:
            continue
        classes, a, b = heads[look_id]
        head = _stat(measure, a, b)
        look = looks[look_id]
        k_ge = _null_count(measure, a, b, head, M, SEED_PREFIX + look_id)
        look["effect"] = _effect(measure, head)
        look["null_model"] = {
            "name": NULL_NAME, "M": M, "k_ge": k_ge, "seed": str(stats.seed_from(SEED_PREFIX + look_id)),
            "p": [k_ge + 1, M + 1], "p_text": stats.decimal_str(k_ge + 1, M + 1, 4),
        }
        look["checks"] = _checks(look_id, kind, measure, classes, ctx, head, (a, b))

    items = []
    for look_id, _, _ in DECLARED:
        look = looks[look_id]
        if look["status"] == "evaluated":
            items.append((look_id, Fraction(look["null_model"]["k_ge"] + 1, M + 1)))
        else:
            items.append((look_id, Fraction(1)))
    qs = stats.bh_q(items, M_DECLARED)

    by_grade = {"C": 0, "L": 0, "S": 0, "none": 0}
    for look_id, kind, measure in DECLARED:
        look = looks[look_id]
        if look["status"] == "evaluated":
            q = qs[look_id]
            look["q"] = [q.numerator, q.denominator]
            look["q_text"] = stats.decimal_str(q.numerator, q.denominator, 4)
        g, why = grade(look)
        look["grade"], look["grade_reason"] = g, why
        look["say_at_most"] = SAY_AT_MOST.get(g)
        look["text"] = render_text(look)
        if kind == "contrast" and pair:
            look["highlight"] = {"who": pair[0], "xray": True}
        by_grade[g if g else "none"] += 1

    evaluated = len(heads)
    return {
        "pair": list(pair) if pair else None,
        "pair_reason": pair_reason,
        "ledger": {
            "declared": M_DECLARED, "evaluated": evaluated, "not_run": M_DECLARED - evaluated, "by_grade": by_grade,
            "alpha": list(t["ALPHA"]), "q_threshold": list(t["Q_BH"]), "q_confirm": list(t["Q_CONFIRM"]),
            "M": M, "W": W, "work_cap": t["WORK_CAP"],
            "work_exceeded": W * M > t["WORK_CAP"], "manifest_sha256": manifest_sha256(), "bh_note": BH_NOTE,
        },
        "grade_rule": grade_rule(),
        "say_at_most": dict(SAY_AT_MOST),
        "scope": SCOPE,
        "findings": [looks[i] for i, _, _ in DECLARED],
    }


# ---------------------------------------------------------------------------------------------------------------
# verify: the same quantities again, from the rows, with deliberately naive code.

def _n_keep(r: dict, fork: str) -> bool:
    if fork == "all":
        return True
    if r["m"]:
        return False
    if fork == "no_merge":
        return True
    if fork == "default_branch":
        return r["b"] == 1
    return r["f"] <= stats.MASS_FILES


def _n_groups(kind: str, measure: str, classes, rs: list[dict]):
    """Plain loops over a (t, i)-ordered list: (values of a, values of b, repos of a, repos of b)."""
    def val(r, k):
        if measure == "lines":
            return r["a"] + r["d"]
        if measure == "files":
            return r["f"]
        if measure == "hour4":
            return ((r["t"] % 86400) // 3600) // 4
        if measure == "weekday":
            return ((r["t"] // 86400) + 3) % 7
        if measure == "repo":
            return r["r"]
        return None if k == 0 else r["t"] - rs[k - 1]["t"]       # gap_s

    av, bv, ar, br = [], [], [], []
    if kind == "contrast":
        c1, c2 = classes
        for k, r in enumerate(rs):
            v = val(r, k)
            if v is None or r["c"] not in (c1, c2):
                continue
            (av if r["c"] == c1 else bv).append(v)
            (ar if r["c"] == c1 else br).append(r["r"])
    else:
        mine = [r for r in rs if r["c"] == classes]
        half = len(mine) // 2
        for k, r in enumerate(mine):
            v = val(r, 0)
            (bv if k < half else av).append(v)
            (br if k < half else ar).append(r["r"])
    return av, bv, ar, br


def _n_stat(measure: str, av: list, bv: list, ref=None) -> dict:
    na, nb = len(av), len(bv)
    if measure in ORDINAL:
        # gt - lt by counting, for each value of a, the values of b below and above it in sorted b: a different route
        # from the stage's ranks, and n log n where a pair loop is n_a * n_b.
        sb = sorted(bv)
        s = sum(bisect.bisect_left(sb, x) - (nb - bisect.bisect_right(sb, x)) for x in av)
        return {"n_a": na, "n_b": nb, "num": s, "den": na * nb, "direction": (s > 0) - (s < 0), "k": None}
    keys = sorted(set(av) | set(bv))
    if na == 0 or nb == 0:
        return {"n_a": na, "n_b": nb, "num": 0, "den": 2 * na * nb, "direction": 0, "k": ref}
    gap = {k: Fraction(av.count(k), na) - Fraction(bv.count(k), nb) for k in keys}
    total = sum(abs(g) for g in gap.values()) * na * nb
    assert total.denominator == 1
    star = ref
    if ref is None:
        star = keys[0]
        for k in keys:
            if abs(gap[k]) > abs(gap[star]):
                star = k
    g = gap.get(star, Fraction(0))
    return {"n_a": na, "n_b": nb, "num": int(total), "den": 2 * na * nb,
            "direction": (g > 0) - (g < 0), "k": star}


def _n_floor(measure: str, st: dict, half: bool) -> bool:
    if st["den"] == 0:
        return False
    num, den = THRESHOLDS["FLOOR_CLIFF"] if measure in ORDINAL else THRESHOLDS["FLOOR_TV"]
    ratio = Fraction(abs(st["num"]), st["den"])
    need = Fraction(num, den) / (2 if half else 1)
    return ratio >= need


def _n_agrees(measure: str, head: dict, st: dict, minimum: int, half: bool) -> bool:
    return bool(st["n_a"] >= minimum and st["n_b"] >= minimum and _n_floor(measure, st, half)
                and head["direction"] != 0 and st["direction"] == head["direction"])


def _n_perm(measure: str, av: list, bv: list, head: dict, M: int, label: str) -> int:
    """Count permutations whose statistic reaches the observed one, drawing with the same stream and the same
    partial Fisher-Yates as the stage but recomputing the statistic from the shuffled values each time."""
    drawn, other = (av, bv) if len(av) <= len(bv) else (bv, av)
    pooled = drawn + other
    n, nd = len(pooled), len(drawn)
    rng = stats.SplitMix64(int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:8], "big"))
    if measure in ORDINAL:
        order = sorted(pooled)
        arr = [2 * bisect.bisect_left(order, x) + (bisect.bisect_right(order, x) - bisect.bisect_left(order, x)) + 1 for x in pooled]
        target = abs(head["num"])
    else:
        keys = sorted(set(pooled))
        arr = [keys.index(x) for x in pooled]
        target = head["num"]
    count = 0
    for _ in range(M):
        for j in range(nd):
            r = j + rng.below(n - j)
            arr[j], arr[r] = arr[r], arr[j]
        if measure in ORDINAL:
            hit = abs(sum(arr[:nd]) - nd * (n + 1)) >= target
        else:
            first, rest = arr[:nd], arr[nd:]
            dist = sum(abs(Fraction(first.count(k), nd) - Fraction(rest.count(k), n - nd))
                       for k in range(len(keys))) * nd * (n - nd)
            hit = dist >= target
        count += hit
    return count


def _n_bh(ps: list[tuple[str, Fraction]], m: int) -> dict:
    ordered = sorted(ps, key=lambda it: (it[1], it[0]))
    out = {}
    for i, (name, _) in enumerate(ordered):
        out[name] = min([Fraction(1)] + [m * ordered[j][1] / (j + 1) for j in range(i, len(ordered))])
    return out


def _n_look(look_id: str, kind: str, measure: str, pair, all_rows: list[dict], stored: dict | None):
    """Everything about one look except q and grade, which need the other looks. Returns (look, W part)."""
    t = THRESHOLDS
    look = _blank(look_id, kind, measure, "not_run", None, None)
    if pair is None:
        look["reason"] = PAIR_REASON.format(n=t["MIN_SIDE"])
        return look, 0, None
    classes = _look_classes(look_id, kind, pair)
    old = ((stored or {}).get("groups") or {})
    ga = (old.get("a") or {}).get("label")
    gb = (old.get("b") or {}).get("label")
    ordered = sorted(all_rows, key=lambda r: (r["t"], r["i"]))

    def restrict(**kw):
        out = ordered
        if "fork" in kw:
            out = [r for r in out if _n_keep(r, kw["fork"])]
        if "drop" in kw:
            out = [r for r in out if r["r"] != kw["drop"]]
        if "only" in kw:
            out = [r for r in out if r["r"] == kw["only"]]
        if "parity" in kw:
            out = [r for r in out if (((r["t"] // 86400) + 3) // 7) % 2 == kw["parity"]]
        return out

    av, bv, ar, br = _n_groups(kind, measure, classes, restrict(fork="all"))
    look["groups"] = {"a": {"label": ga, "n": len(av)}, "b": {"label": gb, "n": len(bv)}}
    if min(len(av), len(bv)) < t["MIN_SIDE"]:
        look["reason"] = SMALL_REASON.format(n=t["MIN_SIDE"], a=len(av), b=len(bv))
        return look, 0, None
    look["status"] = "evaluated"
    head = _n_stat(measure, av, bv)
    ctx = (restrict, classes, head, av, bv, ar, br)
    return look, min(len(av), len(bv)), ctx


def _n_finish(look: dict, kind: str, measure: str, ctx, M: int) -> None:
    t = THRESHOLDS
    restrict, classes, head, av, bv, ar, br = ctx
    look_id = look["id"]
    look["effect"] = {
        "kind": "cliff_delta" if measure in ORDINAL else "tv", "num": head["num"], "den": head["den"],
        "permille": stats.permille(head["num"], head["den"]), "direction": head["direction"],
        "dominant_bin": head["k"], "floor_met": _n_floor(measure, head, False),
    }
    k_ge = _n_perm(measure, av, bv, head, M, SEED_PREFIX + look_id)
    look["null_model"] = {
        "name": NULL_NAME, "M": M, "k_ge": k_ge,
        "seed": str(int.from_bytes(hashlib.sha256((SEED_PREFIX + look_id).encode("utf-8")).digest()[:8], "big")),
        "p": [k_ge + 1, M + 1], "p_text": stats.decimal_str(k_ge + 1, M + 1, 4),
    }

    def groups_of(rs):
        return _n_stat(measure, *_n_groups(kind, measure, classes, rs)[:2], head["k"])

    per_fork, ev, ag = {}, 0, 0
    seen: dict[frozenset, str] = {}
    for fork in stats.FORKS:
        members = restrict(fork=fork)
        st = groups_of(members)
        ok = st["n_a"] >= t["MIN_SIDE"] and st["n_b"] >= t["MIN_SIDE"]
        yes = _n_agrees(measure, head, st, t["MIN_SIDE"], False)
        twin = seen.setdefault(frozenset(r["i"] for r in members), fork)
        if twin == fork:
            ev, ag = ev + ok, ag + yes
        per_fork[fork] = {"n_a": st["n_a"], "n_b": st["n_b"], "evaluable": ok,
                          "permille": stats.permille(st["num"], st["den"]) if st["den"] else 0, "agrees": yes,
                          "same_as": None if twin == fork else twin}
    loro, strata = {"total": 0, "agree": 0}, None
    if measure != "repo":
        for repo in sorted(set(ar) | set(br)):
            st = groups_of(restrict(drop=repo))
            if st["n_a"] >= t["MIN_SIDE"] and st["n_b"] >= t["MIN_SIDE"]:
                loro["total"] += 1
                loro["agree"] += _n_agrees(measure, head, st, t["MIN_SIDE"], False)
        strata = {"n": 0, "same": 0, "opposite": 0}
        for repo in sorted({r["r"] for r in restrict(fork="all")}):
            st = groups_of(restrict(only=repo))
            if min(st["n_a"], st["n_b"]) < t["MIN_STRATUM_SIDE"]:
                continue
            strata["n"] += 1
            if head["direction"] and st["direction"] == head["direction"]:
                strata["same"] += 1
            if head["direction"] and st["direction"] == -head["direction"] and _n_floor(measure, st, False):
                strata["opposite"] += 1
    per_half, ok_all, agree_all = {}, True, True
    for p in (0, 1):
        st = groups_of(restrict(parity=p))
        yes = _n_agrees(measure, head, st, t["MIN_HALF"], True)
        ok_all = ok_all and min(st["n_a"], st["n_b"]) >= t["MIN_HALF"]
        agree_all = agree_all and yes
        per_half[str(p)] = {"n_a": st["n_a"], "n_b": st["n_b"],
                            "permille": stats.permille(st["num"], st["den"]) if st["den"] else 0, "agrees": yes}
    look["checks"] = {
        "forks": {"distinct": len(seen), "evaluable": ev, "agree": ag, "per_fork": per_fork},
        "loro": loro, "strata": strata,
        "halves": {"evaluable": ok_all, "replicates": agree_all if ok_all else None, "per_half": per_half},
    }


def _diff(path: str, got, want, out: list[str], look_id: str) -> None:
    """Record each path where `got` is not exactly `want` (bool and int are different types here). Values are never
    printed, only the path."""
    if isinstance(want, dict):
        if not isinstance(got, dict):
            out.append(f"{look_id}: {path} differs")
            return
        for k in sorted(set(got) | set(want), key=str):
            if k not in got or k not in want:
                out.append(f"{look_id}: {path}.{k} differs")
            else:
                _diff(f"{path}.{k}", got[k], want[k], out, look_id)
    elif isinstance(want, (list, tuple)):
        if not isinstance(got, (list, tuple)) or len(got) != len(want):
            out.append(f"{look_id}: {path} differs")
            return
        for n, (g, w) in enumerate(zip(got, want)):
            _diff(f"{path}[{n}]", g, w, out, look_id)
    elif got != want or type(got) is not type(want):
        out.append(f"{look_id}: {path} differs")


def verify(section: dict, rows: list[dict]) -> list[str]:
    """Problems found by recomputing the whole section from the rows; empty means it checks out. Each message names
    a look id (or 'looks') and a field path, never a value or a repo name."""
    problems: list[str] = []
    if not isinstance(section, dict) or not isinstance(section.get("findings"), list):
        return ["looks: section is not an object with findings"]
    t = THRESHOLDS
    ids = [i for i, _, _ in DECLARED]
    if [f.get("id") if isinstance(f, dict) else None for f in section["findings"]] != ids:
        problems.append("looks: declared look ids differ from the declared battery")
    ledger = section.get("ledger") if isinstance(section.get("ledger"), dict) else {}
    body = json.dumps([[i, k, m] for i, k, m in DECLARED] + [THRESHOLDS], sort_keys=True, separators=(",", ":"))
    if ledger.get("manifest_sha256") != hashlib.sha256(body.encode("utf-8")).hexdigest():
        problems.append("looks: ledger.manifest_sha256 differs from the declared battery")

    counts: dict = {}
    for r in rows:
        if not r["m"]:
            counts[r["c"]] = counts.get(r["c"], 0) + 1
    big = [c for c in CLASS_ORDER if counts.get(c, 0) >= t["MIN_SIDE"]]
    pair = None
    if len(big) >= 2:
        big = sorted(big, key=lambda c: (-counts[c], CLASS_ORDER.index(c)))
        pair = (big[0], big[1])

    stored = {f.get("id"): f for f in section["findings"] if isinstance(f, dict)}
    built, W = {}, 0
    for look_id, kind, measure in DECLARED:
        look, w, ctx = _n_look(look_id, kind, measure, pair, rows, stored.get(look_id))
        built[look_id] = (look, ctx)
        W += w
    M = max(t["M_MIN"], min(t["M_MAX"], t["WORK_CAP"] // max(1, W)))
    ps = []
    for look_id, kind, measure in DECLARED:
        look, ctx = built[look_id]
        if ctx is not None:
            _n_finish(look, kind, measure, ctx, M)
            ps.append((look_id, Fraction(look["null_model"]["k_ge"] + 1, M + 1)))
        else:
            ps.append((look_id, Fraction(1)))
    qs = _n_bh(ps, 12)

    by_grade = {"C": 0, "L": 0, "S": 0, "none": 0}
    for look_id, kind, measure in DECLARED:
        look, ctx = built[look_id]
        if ctx is not None:
            look["q"] = [qs[look_id].numerator, qs[look_id].denominator]
            look["q_text"] = stats.decimal_str(qs[look_id].numerator, qs[look_id].denominator, 4)
        look["grade"], look["grade_reason"] = grade(look)
        look["say_at_most"] = SAY_AT_MOST.get(look["grade"])
        look["text"] = render_text(look)
        if kind == "contrast" and pair:
            look["highlight"] = {"who": pair[0], "xray": True}
        by_grade[look["grade"] or "none"] += 1
        got = stored.get(look_id)
        if got is None:
            problems.append(f"{look_id}: missing from the section")
        else:
            _diff("look", got, look, problems, look_id)

    evaluated = sum(1 for look_id, _, _ in DECLARED if built[look_id][1] is not None)
    want = {
        "pair": list(pair) if pair else None,
        "pair_reason": None if pair else PAIR_REASON.format(n=t["MIN_SIDE"]),
        "ledger": {
            "declared": 12, "evaluated": evaluated, "not_run": 12 - evaluated, "by_grade": by_grade,
            "alpha": list(t["ALPHA"]), "q_threshold": list(t["Q_BH"]), "q_confirm": list(t["Q_CONFIRM"]),
            "M": M, "W": W, "work_cap": t["WORK_CAP"],
            "work_exceeded": W * M > t["WORK_CAP"], "manifest_sha256": ledger.get("manifest_sha256"),
            "bh_note": BH_NOTE,
        },
        "grade_rule": grade_rule(), "say_at_most": dict(SAY_AT_MOST), "scope": SCOPE,
    }
    for key, value in want.items():
        _diff(key, section.get(key), value, problems, "looks")
    return problems
