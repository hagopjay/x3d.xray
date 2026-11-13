"""Exploratory data analysis of the family's own commits: what the data can and cannot say, before any look at it.

One stage, `eda` (data/eda.json, embedded as bundle.eda). Everything is exact integer arithmetic from x3dray/stats.py, so
the same dataset.json gives the same bytes on every run. Five parts:

audit          eight data-quality checks (timestamps, duplicate commits, same-second runs, empty and stat-free commits,
               the merge contract, the class residual), a per-repo coverage matrix and the limitations of the harvest
               that were derived from its source, not written by hand;
profile        robust distributions (nearest-rank quantiles, MAD, log2 bins, Gini, top-decile share) of lines, files and
               gaps for the family, every author class and every repo, each shown under four declared forks (forks that
               hold exactly the same commits are shown but count once wherever forks vote);
rhythm         descriptive UTC facts: the longest quiet stretch of hours and the bursts at three gap thresholds;
concentration  how unequal the churn of a repo's files is at the pinned tip;
flags          two rule-based outlier detectors (a very large commit for its repo and class, a commit after a long quiet
               spell), each graded by how many distinct forks agree it is atypical.

The declared battery of looks (contrasts and drift with a seeded permutation null) lives in x3dray/looks.py and is embedded
under `looks`. A grade anywhere in this file is robustness over declared variations, never a probability.

Rules every output follows:
- a private repo enters as counts only: no sha, oid, subject, path or kind list, and flags are commit indices;
- unknown is not zero: a slice, check or repo that cannot be evaluated says available false (or unknown) with a reason;
- no float, no random module, no wall clock; every iteration is over sorted data.
verify() re-derives every section with different code (sorting instead of hashing, counting instead of ranking, pairwise
Fractions instead of the weighted Gini) and reports every difference without naming a private identifier.
"""

from __future__ import annotations

import calendar
import collections
import datetime as dt
import hashlib
import inspect
import itertools
import json
import os
from fractions import Fraction

from . import gitio, stats, structure

THRESHOLDS = {
    "MIN_PROFILE_N": 10,
    "MIN_SIDE": 20,
    "MIN_HALF": 8,
    "MIN_QUIET_N": 120,
    "MIN_BURST_N": 20,
    "MIN_FLAG_STRATUM": 20,
    "MIN_CONC_FILES": 10,
    "MASS_FILES": stats.MASS_FILES,
    "FLAG_CAP": 200,
    "BURST_GAPS": (3600, 7200, 14400),
    "SIZE_BIN_CAP": 16,
    "GAP_BIN_CAP": 19,
    "GAP_BIN_UNIT": 60,
    "TS_MIN": 1112832000,          # 2005-04-07T00:00:00Z: git itself dates from this week
    "CLASS_RESIDUAL_PERMILLE": 250,
    "SAME_SECOND_RUN": 5,
    "SIZE_FLAG_MIN_LINES": 64,
    "SIZE_FLAG_MIN_DELTA": 3,
    "GAP_FLAG_MIN_SECONDS": 604800,
    "GAP_FLAG_MIN_DELTA": 4,
    "QUIET_SHARE_DEN": 96,          # an hour is quiet when 96 * its count <= n, a quarter of the uniform expectation
    "VERIFY_PAIRWISE_N": 2000,      # verify re-derives the Gini from its pairwise definition up to this many values
}

MIN_PROFILE_N = THRESHOLDS["MIN_PROFILE_N"]
MIN_SIDE = THRESHOLDS["MIN_SIDE"]
MIN_QUIET_N = THRESHOLDS["MIN_QUIET_N"]
MIN_BURST_N = THRESHOLDS["MIN_BURST_N"]
MIN_FLAG_STRATUM = THRESHOLDS["MIN_FLAG_STRATUM"]
MIN_CONC_FILES = THRESHOLDS["MIN_CONC_FILES"]
FLAG_CAP = THRESHOLDS["FLAG_CAP"]
BURST_GAPS = THRESHOLDS["BURST_GAPS"]
SIZE_BIN_CAP = THRESHOLDS["SIZE_BIN_CAP"]
GAP_BIN_CAP = THRESHOLDS["GAP_BIN_CAP"]
GAP_BIN_UNIT = THRESHOLDS["GAP_BIN_UNIT"]
TS_MIN = THRESHOLDS["TS_MIN"]
CLASS_RESIDUAL_PERMILLE = THRESHOLDS["CLASS_RESIDUAL_PERMILLE"]
SAME_SECOND_RUN = THRESHOLDS["SAME_SECOND_RUN"]

MEASURES = ("lines", "files", "gap_s")
GRADES = ("C", "L", "S")
RULES = ("size_high", "gap_long")
PKG = os.path.dirname(os.path.abspath(__file__))
PRIVATE_COUNTS = "counts only (private)"

FORK_DEFS = {
    "all": "every commit, merges included",
    "no_merge": "commits with at most one parent",
    "default_branch": "non-merge commits reachable from the default branch",
    "no_mass": f"non-merge commits touching at most {stats.MASS_FILES} files",
}
MEASURE_DEFS = {
    "lines": "add+del",
    "files": "files changed",
    "gap_s": "seconds since the previous member of the slice, ordered by (t,i); the first member has none",
}
PROFILE_SCOPE = ("Descriptive only: no test, no grade. Author times in UTC; the first member of a slice has no gap. A slice with "
                 "fewer than 10 commits in a fork is unavailable, not zero. Private repos enter with their counts, sizes and times.")
RHYTHM_SCOPE = ("Descriptive, ungraded, UTC author time. Built on non-merge commits; a slice below 20 commits has no bursts and "
                "below 120 no quiet window. No dates are stored.")
CONCENTRATION_SCOPE = ("all files at the pinned tip with default-branch non-merge history; renames not followed; vendored and "
                       "generated files count in full")
FLAGS_SCOPE = ("Commit indices only. A flag says a commit is atypical for its stratum in k of m distinct forks where it could "
               "be evaluated (forks holding the same commits count once); it says nothing about the commit being wrong. Lists "
               "are capped; counts are exact.")
QUIET_RULE = ("an hour is quiet when it holds at most a quarter of the uniform expectation (96 * hour count <= n); hours are "
              "UTC author-time hours; the window is the longest circular run of quiet hours; descriptive and ungraded")


# ------------------------------------------------------------------ rows
def class_labels(ds: dict) -> dict[str, str]:
    """The identity label of each author class (the first identity of that class), else the class key."""
    out = {}
    for cls in structure.CLASSES:
        out[cls] = next((i["label"] for i in ds.get("identities", []) if i.get("class") == cls and i.get("label")), cls)
    return out


class DatasetError(ValueError):
    """A commit the stage cannot count: raised with one line that names the place and the field, never the value."""


MAX_EXACT = 2 ** 53        # stats.summarize takes ints below this; larger counts would round in the JS twin
T_LIMIT = 8_640_000_000_000  # seconds either side of the epoch that a JavaScript Date holds; also keeps any two times
                             # (hence any gap) far below 2**53, and the page's day and hour tools can read every time


def _check_repos(ds) -> None:
    """The containers the stage walks, before any of them is read: a dataset that is not shaped like the harvester's
    stops with one named line instead of a traceback. Positions only, never a name or a value."""
    if not isinstance(ds, dict):
        raise DatasetError("dataset is not an object")
    for key in ("repos", "identities"):
        if not isinstance(ds.get(key, []), list):
            raise DatasetError(f"dataset {key} must be a list")
    for n, r in enumerate(ds.get("repos", [])):
        if not isinstance(r, dict):
            raise DatasetError(f"dataset repo #{n} is not an object")
        if not isinstance(r.get("name"), str) or not r["name"]:
            raise DatasetError(f"dataset repo #{n}: name must be a non-empty string")
        for key in ("commits", "files"):
            if not isinstance(r.get(key, []), list):
                raise DatasetError(f"dataset repo #{n}: {key} must be a list")
    for n, ident in enumerate(ds.get("identities", [])):
        if not isinstance(ident, dict):
            raise DatasetError(f"dataset identity #{n} is not an object")


def _check_commit(repo_no: int, pos: int, c: dict) -> None:
    where = f"dataset repo #{repo_no} commit #{pos}"
    if not isinstance(c, dict):
        raise DatasetError(f"{where} is not an object")
    for key in ("t", "add", "del", "files"):
        v = c.get(key)
        if type(v) is not int:
            raise DatasetError(f"{where}: {key} must be an integer, not {'missing' if key not in c else type(v).__name__}")
        if key == "t" and abs(v) > T_LIMIT:
            raise DatasetError(f"{where}: t is outside plus or minus {T_LIMIT} seconds, the dates the page can read")
        if key != "t" and not 0 <= v < MAX_EXACT:
            raise DatasetError(f"{where}: {key} is outside 0 to 2**53 - 1, the range the stage counts exactly")
    if c["add"] + c["del"] >= MAX_EXACT:
        raise DatasetError(f"{where}: add + del reaches 2**53, the limit of exact counting")
    for key in ("cls", "who"):
        if not isinstance(c.get(key), str):
            raise DatasetError(f"{where}: {key} must be a string")


def indexed_rows(ds: dict) -> list[dict]:
    """The family's own commits in bundle order, built exactly like page.make_bundle: sorted by (t, repo name), stable, so
    row i is bundle.commits[i]. Shas, oids, paths and kinds are dropped for private repos whatever the dataset holds."""
    _check_repos(ds)
    private = {r["name"] for r in ds.get("repos", []) if r.get("visibility") == "private"}
    pairs = []
    for n, r in enumerate(ds.get("repos", [])):
        for k, c in enumerate(r.get("commits", [])):
            if not (isinstance(c, dict) and c.get("upstream")):
                _check_commit(n, k, c)
                pairs.append((r["name"], c))
    pairs.sort(key=lambda rc: (rc[1]["t"], rc[0]))
    rows = []
    for i, (name, c) in enumerate(pairs):
        pv = name in private
        rows.append({
            "i": i, "r": name, "t": c["t"], "c": c["cls"], "w": c["who"], "a": c["add"], "d": c["del"], "f": c["files"],
            "m": 1 if c.get("merge") else 0, "b": 1 if c.get("default", True) else 0, "tr": 1 if c.get("ai_trailer") else 0,
            "pv": pv,
            "sha": None if pv else c.get("sha"),
            "oid": None if pv else c.get("oid"),
            "paths": None if pv else c.get("paths"),
            "kinds": None if pv else c.get("kinds"),
        })
    return rows


def _plain(x):
    """The JSON image of x: tuples become lists, so a freshly built section equals the same section read back from disk."""
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def canonical_json(x) -> str:
    """One text per JSON value in which 1, 1.0 and true differ (Python's == calls them equal), so two sections that
    compare equal here have the same types as well as the same values. NaN and infinity raise."""
    return json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True)


def _ne(a, b) -> bool:
    return canonical_json(a) != canonical_json(b)


def _floats(x, path: str, out: list[str]) -> None:
    """Paths of every float in x: the stage emits integers and Fractions-as-pairs only, so a float is a tampered value."""
    if type(x) is float:
        out.append(path)
    elif isinstance(x, dict):
        for k in sorted(x, key=str):
            _floats(x[k], f"{path}.{k}", out)
    elif isinstance(x, (list, tuple)):
        for n, v in enumerate(x):
            _floats(v, f"{path}[{n}]", out)


def _sha12(r: dict):
    s = r.get("sha") or (r.get("oid") or "")[:12]
    return s[:12] or None


def _fork_rows(rows: list[dict]) -> dict[str, list[dict]]:
    return {fork: [r for r in rows if stats.in_fork(r, fork)] for fork in stats.FORKS}


def _class_order(rows: list[dict]) -> list[str]:
    present = {r["c"] for r in rows}
    return [c for c in structure.CLASSES if c in present] + sorted(present - set(structure.CLASSES))


def _slices(rows: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """(slice id, kind, rows): the family, each author class with a commit, each repo by (-commits, name)."""
    out = [("family", "family", rows)]
    for c in _class_order(rows):
        out.append((f"class:{c}", "class", [r for r in rows if r["c"] == c]))
    by_repo: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by_repo[r["r"]].append(r)
    for name in sorted(by_repo, key=lambda n: (-len(by_repo[n]), n)):
        out.append((f"repo:{name}", "repo", by_repo[name]))
    return out


def _repo_index(ds: dict) -> dict[str, dict]:
    """The first dataset record of each repo name."""
    out: dict[str, dict] = {}
    for r in ds.get("repos", []):
        out.setdefault(r["name"], r)
    return out


def _present_names(ds: dict, rows: list[dict]) -> list[str]:
    names = {r["name"] for r in ds.get("repos", []) if r.get("present", True)}
    return sorted(names | {r["r"] for r in rows})


def _harvest_epoch(ds: dict):
    text = str(ds.get("harvested_at") or "")[:19]
    try:
        return calendar.timegm(dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").timetuple())
    except ValueError:
        return None


# ------------------------------------------------------------------ audit
def _check(cid: str, title: str, flagged: list[dict], grade, note: str, status: str | None = None) -> dict:
    by = collections.Counter(r["r"] for r in flagged)
    shas = sorted({s for s in (_sha12(r) for r in flagged if not r["pv"]) if s})
    return {
        "id": cid,
        "title": title,
        "status": status or ("flag" if flagged else "pass"),
        "grade": grade if flagged else None,
        "count": len(flagged),
        "by_repo": [{"repo": n, "count": by[n]} for n in sorted(by, key=lambda n: (-by[n], n))],
        "witnesses": shas[:5],
        "note": note,
    }


def _dup_oid_set(rows: list[dict]) -> set[int]:
    oid_repos: dict[str, set] = collections.defaultdict(set)
    sha_count: collections.Counter = collections.Counter()
    pub = [r for r in rows if not r["pv"]]
    for r in pub:
        if r["oid"]:
            oid_repos[r["oid"]].add(r["r"])
        if _sha12(r):
            sha_count[(r["r"], _sha12(r))] += 1
    return {r["i"] for r in pub
            if (r["oid"] and len(oid_repos[r["oid"]]) >= 2) or (_sha12(r) and sha_count[(r["r"], _sha12(r))] >= 2)}


def _fingerprint(r: dict) -> tuple:
    return (r["t"], r["c"], r["w"], r["a"], r["d"], r["f"], r["m"])


def _dup_fingerprint(rows: list[dict], skip: set[int]):
    """(flagged rows, grade): rows sharing a fingerprint with a row of another repo, set aside any row dup_oid counted."""
    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for r in rows:
        if r["i"] in skip or r["m"] or (r["f"] == 0 and r["a"] + r["d"] == 0):
            continue
        groups[_fingerprint(r)].append(r)
    flagged, likely = [], False
    for key in sorted(groups):
        g = groups[key]
        if len({r["r"] for r in g}) < 2:
            continue
        flagged.extend(g)
        if all(not r["pv"] and r["oid"] for r in g) and len({r["oid"] for r in g}) == len(g):
            likely = True
    flagged.sort(key=lambda r: r["i"])
    return flagged, ("L" if likely else "S")


def _same_second(rows: list[dict]):
    """(flagged rows, longest run): flagged only when some repo has SAME_SECOND_RUN or more commits in one second."""
    cnt = collections.Counter((r["r"], r["t"]) for r in rows)
    longest = max(cnt.values(), default=0)
    if longest < SAME_SECOND_RUN:
        return [], longest
    return [r for r in rows if cnt[(r["r"], r["t"])] >= 2], longest


def _class_residual(rows: list[dict]):
    """(flagged rows, repos flagged, repos too small to judge, repos judged)."""
    by_repo: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by_repo[r["r"]].append(r)
    flagged, hit, small, judged = [], 0, 0, 0
    for name in sorted(by_repo):
        g = by_repo[name]
        if len(g) < stats.MIN_SHARE_N:
            small += 1
            continue
        judged += 1
        resid = [r for r in g if r["c"] in ("other", "upstream")]
        if stats.permille(len(resid), len(g)) >= CLASS_RESIDUAL_PERMILLE:
            hit += 1
            flagged.extend(resid)
    flagged.sort(key=lambda r: r["i"])
    return flagged, hit, small, judged


def _limitations(ds: dict) -> tuple[list[dict], list[str]]:
    out, notes = [], []
    try:
        fmt = gitio.LOG_FORMAT_NUL
        if "%at" in fmt and not any(x in fmt for x in ("%ai", "%aI", "%ad", "%ci", "%cI", "%cd")):
            out.append({"id": "author_time_utc", "text": "Times are author times in UTC; the harvest records no timezone offset, "
                        "so local hours are unknown."})
    except (AttributeError, TypeError):
        notes.append("limitation author_time_utc could not be derived")
    try:
        if "--no-renames" in inspect.getsource(gitio.log_with_numstat):
            out.append({"id": "renames_not_followed", "text": "Renames are not followed: a moved file counts as a deletion and an "
                        "addition, and per-file history stops at the move."})
    except (OSError, TypeError, AttributeError):
        notes.append("limitation renames_not_followed could not be derived")
    up = sum(1 for r in ds.get("repos", []) for c in r.get("commits", []) if c.get("upstream"))
    out.append({"id": "upstream_excluded", "text": f"{up} commits flagged as upstream are dropped before any statistic; only "
                "the family's own commits are described."})
    out.append({"id": "private_counts_only", "text": "Private repos contribute counts, sizes and times only: no sha, subject, "
                "path or kind list reaches this file."})
    out.append({"id": "exchangeable", "text": "Contrasts treat commits as exchangeable under the null; commits of one session "
                "or day are correlated, so permutation scores are anti-conservative."})
    return out, notes


def audit(rows: list[dict], ds: dict) -> dict:
    epoch = _harvest_epoch(ds)
    checks = []

    odd = [r for r in rows if r["t"] < TS_MIN or (epoch is not None and r["t"] > epoch)]
    note = f"commits dated before {TS_MIN} (2005-04-07T00:00:00Z) or after the harvest"
    if epoch is None:
        note = f"commits dated before {TS_MIN} (2005-04-07T00:00:00Z); harvested_at is missing or unparsable, so the future half was skipped"
    # Without a harvest time half the check cannot run; a clean lower bound alone must not read as a pass.
    checks.append(_check("ts_range", "Timestamps inside the plausible range", odd, "C", note,
                         status="unavailable" if epoch is None and not odd else None))

    dup = _dup_oid_set(rows)
    checks.append(_check("dup_oid", "Commit ids unique across repos", [r for r in rows if r["i"] in dup], "C",
                         "public commits whose full oid is in two or more repos, or whose 12-hex sha repeats inside one repo"))

    fp, fp_grade = _dup_fingerprint(rows, dup)
    checks.append(_check("dup_fingerprint", "No identical commit fingerprints across repos", fp, fp_grade,
                         "same time, class, author, lines, files and merge flag in two repos with different ids (L) or "
                         "involving a private repo (S); merges and empty commits are ignored"))

    ss, longest = _same_second(rows)
    checks.append(_check("same_second", "No long same-second runs", ss, "S",
                         f"commits sharing an author second with another commit of the same repo, flagged when some repo has "
                         f"{SAME_SECOND_RUN} or more in one second (longest run {longest})"))

    checks.append(_check("empty_commit", "Commits with no changed file", [r for r in rows if not r["m"] and r["f"] == 0], "S",
                         "non-merge commits with no changed file; empty commits are legal in git"))
    checks.append(_check("stat_free_change", "Changed files carry line counts",
                         [r for r in rows if not r["m"] and r["f"] > 0 and r["a"] + r["d"] == 0], "S",
                         "non-merge commits whose files add and delete no line (binary or mode-only changes)"))
    checks.append(_check("merge_contract", "Merges carry no statistics",
                         [r for r in rows if r["m"] and (r["f"] > 0 or r["a"] > 0 or r["d"] > 0)], "C",
                         "merge commits with files or lines; the harvest promises zeros for them"))

    resid, hit, small, judged = _class_residual(rows)
    note = (f"share of commits classed other or upstream, flagged at {CLASS_RESIDUAL_PERMILLE} permille or more in a repo; "
            f"{judged} repos judged, {small} repos with fewer than {stats.MIN_SHARE_N} commits unavailable (not clean)")
    check = _check("class_residual", "Author classes cover the commits", resid, "S", note,
                   status=None if judged else "unavailable")
    checks.append(check)

    by_repo = collections.defaultdict(list)
    for r in rows:
        by_repo[r["r"]].append(r)
    repo_rec = _repo_index(ds)
    cov = []
    for name in _present_names(ds, rows):
        g = by_repo.get(name, [])
        rec = repo_rec.get(name, {})
        priv = rec.get("visibility") == "private"
        nonmerge = [r for r in g if not r["m"]]
        with_paths = [r for r in nonmerge if r["paths"]]
        n_nonmerge = len(nonmerge)
        cov.append({
            "repo": name,
            "visibility": "private" if priv else (rec.get("visibility") or "public"),
            "n": len(g),
            "n_nonmerge": n_nonmerge,
            "n_default": sum(1 for r in nonmerge if r["b"] == 1),
            "n_with_paths": None if priv else len(with_paths),
            "n_usable": None if priv else sum(1 for r in with_paths if r["f"] <= structure.MAX_FILES),
            "tree": PRIVATE_COUNTS if priv else ("measured" if rec.get("tree_measured") else "not harvested"),
            "history_scope": rec.get("history_scope"),
            "eligible": {
                "profile": n_nonmerge >= MIN_PROFILE_N,
                "rhythm": n_nonmerge >= MIN_QUIET_N,
                "bursts": n_nonmerge >= MIN_BURST_N,
                "contrast": n_nonmerge >= MIN_SIDE,
            },
        })
    cov.sort(key=lambda e: (-e["n"], e["repo"]))

    limitations, notes = _limitations(ds)
    scope = ("Checks run over the family's own non-upstream commits as bundled. Grade C is a contradiction of the harvest's own "
             "contract, L a likely defect, S a suspicion; none says which side is wrong. Witnesses are public 12-hex shas only; "
             "private repos contribute counts.")
    if notes:
        scope += " " + "; ".join(notes) + "."
    return {"checks": checks, "coverage": cov, "limitations": limitations, "scope": scope}


# ------------------------------------------------------------------ profile
def _profile_record(slice_id: str, fork: str, measure: str, members: list[dict]) -> dict:
    rec = {"slice": slice_id, "fork": fork, "measure": measure, "available": False, "reason": None, "members": len(members)}
    if len(members) < MIN_PROFILE_N:
        rec["reason"] = f"fewer than {MIN_PROFILE_N} commits in this slice and fork"
        return rec
    if measure == "lines":
        rec.update(stats.summarize([r["a"] + r["d"] for r in members], SIZE_BIN_CAP))
    elif measure == "files":
        rec.update(stats.summarize([r["f"] for r in members], SIZE_BIN_CAP))
    else:
        rec.update(stats.summarize([g for _, g in stats.gap_pairs(members)], GAP_BIN_CAP, GAP_BIN_UNIT))
    rec["available"] = True
    return rec


def profile(rows: list[dict], ds: dict) -> dict:
    recs = []
    for sid, kind, srows in _slices(rows):
        forks = stats.FORKS if kind != "repo" else ("no_merge",)
        for fork in forks:
            members = [r for r in srows if stats.in_fork(r, fork)]
            for measure in MEASURES:
                recs.append(_profile_record(sid, fork, measure, members))
    return {"records": recs, "scope": PROFILE_SCOPE, "measure_defs": dict(MEASURE_DEFS)}


# ------------------------------------------------------------------ rhythm
def _quiet(hours: list[int], n: int) -> dict:
    """Longest circular run of quiet hours: the smallest start among equal runs, the whole circle counting as run 24."""
    quiet = [THRESHOLDS["QUIET_SHARE_DEN"] * h <= n for h in hours]
    if all(quiet):
        return {"run": 24, "start_hour": 0, "quiet_hours": list(range(24))}
    best, start = 0, None
    for x in range(24):
        if not quiet[x] or quiet[(x - 1) % 24]:
            continue
        length = 0
        while quiet[(x + length) % 24]:
            length += 1
        if length > best:
            best, start = length, x
    return {"run": best, "start_hour": start, "quiet_hours": [x for x in range(24) if quiet[x]]}


def _bursts(members: list[dict], theta: int) -> list[list[dict]]:
    ordered = sorted(members, key=lambda r: (r["t"], r["i"]))
    out: list[list[dict]] = []
    for r in ordered:
        if out and r["t"] - out[-1][-1]["t"] <= theta:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def _burst_record(members: list[dict]) -> dict:
    by_gap = {}
    for theta in BURST_GAPS:
        bursts = _bursts(members, theta)
        sizes = [len(b) for b in bursts]
        largest = max(range(len(bursts)), key=lambda k: (sizes[k], -k))
        big = bursts[largest]
        by_gap[str(theta)] = {
            "n_bursts": len(bursts),
            "median_commits": stats.lower_median(sizes),
            "largest_commits": sizes[largest],
            "largest_duration_s": big[-1]["t"] - big[0]["t"],
            "single_commit_permille": stats.permille(sum(1 for s in sizes if s == 1), len(bursts)),
        }
    counts = [v["n_bursts"] for v in by_gap.values()]
    return {"available": True, "reason": None, "by_gap": by_gap, "stable": 2 * max(counts) <= 3 * min(counts)}


def rhythm(rows: list[dict]) -> dict:
    out = []
    for sid, kind, srows in _slices(rows):
        members = [r for r in srows if not r["m"]]
        n = len(members)
        hours = None
        if kind != "repo":
            hours = [0] * 24
            for r in members:
                hours[stats.hour_of(r["t"])] += 1
        quiet = {"available": False, "reason": f"fewer than {MIN_QUIET_N} commits outside merges", "threshold": QUIET_RULE,
                 "run": None, "start_hour": None, "quiet_hours": []}
        if n >= MIN_QUIET_N:
            hist = hours if hours is not None else _hour_hist(members)
            quiet.update(available=True, reason=None, **_quiet(hist, n))
        if n >= MIN_BURST_N:
            bursts = _burst_record(members)
        else:
            bursts = {"available": False, "reason": f"fewer than {MIN_BURST_N} commits outside merges", "by_gap": {}, "stable": None}
        out.append({"slice": sid, "n": n, "hours": hours, "quiet": quiet, "bursts": bursts})
    return {"slices": out, "scope": RHYTHM_SCOPE}


def _hour_hist(members: list[dict]) -> list[int]:
    h = [0] * 24
    for r in members:
        h[stats.hour_of(r["t"])] += 1
    return h


# ------------------------------------------------------------------ concentration
def _churn_values(repo_no: int, files: list) -> list[int]:
    """The nonzero churn of a repo's files. A negative churn would turn the Gini weights negative and a float would reach
    the output, so either stops the stage with one line that names the file's position."""
    values = []
    for k, f in enumerate(files):
        if not isinstance(f, dict):
            raise DatasetError(f"dataset repo #{repo_no} file #{k} is not an object")
        v = f.get("churn")
        if v is None:
            continue
        if type(v) is not int or not 0 <= v < MAX_EXACT:
            raise DatasetError(f"dataset repo #{repo_no} file #{k}: churn must be an integer from 0 to 2**53 - 1")
        if v:
            values.append(v)
    return values


def concentration(ds: dict) -> dict:
    out = []
    repos = _repo_index(ds)
    names = sorted({r["name"] for r in ds.get("repos", []) if r.get("present", True)})
    for name in names:
        rec = repos[name]
        base = {"repo": name, "available": False, "unknown": False, "reason": None, "n_files": None, "total_churn": None,
                "gini_permille": None, "top_decile_permille": None, "half_k": None}
        if rec.get("visibility") == "private":
            base.update(unknown=True, reason=PRIVATE_COUNTS)
        elif not rec.get("tree_measured") or not rec.get("files"):
            base.update(unknown=True, reason="tree not harvested")
        else:
            values = _churn_values(next(n for n, r in enumerate(ds["repos"]) if r["name"] == name), rec["files"])
            base["n_files"] = len(values)
            if len(values) < MIN_CONC_FILES:
                base["reason"] = f"fewer than {MIN_CONC_FILES} files with churn"
            else:
                base.update(available=True, total_churn=stats.json_int(sum(values)),
                            gini_permille=stats.permille(*stats.gini_ratio(values)),
                            top_decile_permille=stats.permille(*stats.top_share_ratio(values)),
                            half_k=stats.half_k(values))
        out.append(base)
    return {"repos": out, "scope": CONCENTRATION_SCOPE}


# ------------------------------------------------------------------ flags
def flag_grade(m: int, k: int) -> str:
    """C: atypical in every distinct fork it could be judged in, and at least three. L: in all of two, or in most of three
    or more. S: anything else with at least one fork."""
    if m >= 3 and k == m:
        return "C"
    if (m >= 2 and k == m) or (m >= 3 and 2 * k > m):
        return "L"
    return "S"


def _flag_counts(rows: list[dict]) -> dict[str, dict[int, list[int]]]:
    """Per rule and commit index, [m, k]: distinct forks where the commit could be judged and where it fires. A fork that
    holds the same rows as an earlier one would repeat the same verdict, so it is skipped."""
    out: dict[str, dict[int, list[int]]] = {rule: {} for rule in RULES}
    aliases = stats.fork_aliases(rows)
    min_lines, d_size = THRESHOLDS["SIZE_FLAG_MIN_LINES"], THRESHOLDS["SIZE_FLAG_MIN_DELTA"]
    min_gap, d_gap = THRESHOLDS["GAP_FLAG_MIN_SECONDS"], THRESHOLDS["GAP_FLAG_MIN_DELTA"]
    for fork, members in _fork_rows(rows).items():
        if aliases[fork] != fork:
            continue
        strata: dict[tuple, list[dict]] = collections.defaultdict(list)
        by_repo: dict[str, list[dict]] = collections.defaultdict(list)
        for r in members:
            strata[(r["r"], r["c"])].append(r)
            by_repo[r["r"]].append(r)
        for key in sorted(strata):
            g = strata[key]
            if len(g) < MIN_FLAG_STRATUM:
                continue
            bins = [stats.bit_bin(r["a"] + r["d"], SIZE_BIN_CAP) for r in g]
            med, md = stats.lower_median(bins), stats.mad(bins)
            for r, b in zip(g, bins):
                mk = out["size_high"].setdefault(r["i"], [0, 0])
                mk[0] += 1
                if b - med >= max(d_size, 3 * md) and r["a"] + r["d"] >= min_lines:
                    mk[1] += 1
        for name in sorted(by_repo):
            pairs = stats.gap_pairs(by_repo[name])
            if len(pairs) < MIN_FLAG_STRATUM:
                continue
            bins = [stats.bit_bin(gap, GAP_BIN_CAP, GAP_BIN_UNIT) for _, gap in pairs]
            med, md = stats.lower_median(bins), stats.mad(bins)
            for (r, gap), b in zip(pairs, bins):
                mk = out["gap_long"].setdefault(r["i"], [0, 0])
                mk[0] += 1
                if b - med >= max(d_gap, 3 * md) and gap >= min_gap:
                    mk[1] += 1
    return out


def flags(rows: list[dict]) -> dict:
    counted = _flag_counts(rows)
    by_rule = {}
    for rule in RULES:
        per = counted[rule]
        listed: dict[str, list] = {g: [] for g in GRADES}
        totals = dict.fromkeys(GRADES, 0)
        for i in sorted(per):
            m, k = per[i]
            if k < 1:
                continue
            g = flag_grade(m, k)
            totals[g] += 1
            if len(listed[g]) < FLAG_CAP:
                listed[g].append([i, k, m])
        by_rule[rule] = {
            "C": listed["C"], "L": listed["L"], "S": listed["S"],
            "counts": {**totals, "evaluable": len(per)},
            "omitted": {g: totals[g] - len(listed[g]) for g in GRADES},
            "cap": FLAG_CAP,
        }
    t = THRESHOLDS
    rules = {
        "size_high": {
            "stratum": "repo and author class", "min_n": MIN_FLAG_STRATUM,
            "params": {"bin_cap": SIZE_BIN_CAP, "min_lines": t["SIZE_FLAG_MIN_LINES"], "min_delta": t["SIZE_FLAG_MIN_DELTA"], "mad_multiple": 3},
            "definition": (f"log2 bin of add+del is at least max({t['SIZE_FLAG_MIN_DELTA']}, 3 MAD) above the stratum's lower "
                           f"median and the commit changes at least {t['SIZE_FLAG_MIN_LINES']} lines"),
        },
        "gap_long": {
            "stratum": "repo", "min_n": MIN_FLAG_STRATUM,
            "params": {"bin_cap": GAP_BIN_CAP, "bin_unit": GAP_BIN_UNIT, "min_seconds": t["GAP_FLAG_MIN_SECONDS"],
                       "min_delta": t["GAP_FLAG_MIN_DELTA"], "mad_multiple": 3},
            "definition": (f"the commit ends a gap whose log2 minute bin is at least max({t['GAP_FLAG_MIN_DELTA']}, 3 MAD) above "
                           f"the repo's lower median and the gap is at least {t['GAP_FLAG_MIN_SECONDS']} seconds"),
        },
    }
    return {"scope": FLAGS_SCOPE, "rules": rules, "by_rule": by_rule}


# ------------------------------------------------------------------ assembly
def _code_sha256() -> dict:
    out = {}
    for name in ("eda.py", "looks.py", "stats.py"):
        try:
            with open(os.path.join(PKG, name), "rb") as fh:
                out[name] = hashlib.sha256(fh.read()).hexdigest()[:16]
        except OSError:
            out[name] = None
    return out


def _method() -> dict:
    return {
        "kernel": "exact integer arithmetic (x3dray/stats.py): no float, no random module, no clock; ratios are int permille or exact pairs",
        "rounding": "half away from zero on integers",
        "quantile": "nearest rank: the ceil(n*q)-th smallest value",
        "thresholds": _plain(THRESHOLDS),
        "forks": dict(FORK_DEFS),
        "seed_label": "x3dray.eda.v1|<look id>",
        "code_sha256": _code_sha256(),
    }


def _coverage(rows: list[dict], ds: dict) -> dict:
    aliases = stats.fork_aliases(rows)
    return {
        "commits": len(rows),
        "merges": sum(r["m"] for r in rows),
        "private_commits": sum(1 for r in rows if r["pv"]),
        "repos": _present_names(ds, rows),
        "private_repos": sum(1 for n, rec in sorted(_repo_index(ds).items()) if rec.get("visibility") == "private" and rec.get("present", True)),
        "by_fork": {fork: sum(1 for r in rows if stats.in_fork(r, fork)) for fork in stats.FORKS},
        "distinct_forks": len(set(aliases.values())),
        "fork_same_as": {fork: None if aliases[fork] == fork else aliases[fork] for fork in stats.FORKS},
    }


def _import_looks():
    from . import looks
    return looks


def build(ds: dict) -> dict:
    rows = indexed_rows(ds)
    looks_section = _import_looks().run(rows, class_labels(ds))
    return {
        "stage": "eda",
        "schema_version": 1,
        "method": _method(),
        "classes": {"order": list(structure.CLASSES), "labels": class_labels(ds),
                    "pair": looks_section.get("pair"), "pair_reason": looks_section.get("pair_reason")},
        "coverage": _coverage(rows, ds),
        "audit": audit(rows, ds),
        "profile": profile(rows, ds),
        "rhythm": rhythm(rows),
        "concentration": concentration(ds),
        "flags": flags(rows),
        "looks": looks_section,
    }


def run(ds: dict) -> tuple[dict, list[str]]:
    eda = _plain(build(ds))
    return eda, verify(eda, ds)


# ------------------------------------------------------------------ verify
# Everything below re-derives the sections from the dataset with other code: it never calls the functions above, and
# differs on purpose (sorting and itertools.groupby instead of dict grouping, counting elements instead of ranking,
# the pairwise Gini instead of the weighted one, datetime instead of modular arithmetic for hours).

def _naive_rows(ds: dict) -> list[dict]:
    """Rows by an independent construction: stable sort of (t, name, position) triples, privacy applied last."""
    private = [r["name"] for r in ds.get("repos", []) if r.get("visibility") == "private"]
    triples = []
    for repo in ds.get("repos", []):
        for c in repo.get("commits", []):
            if c.get("upstream"):
                continue
            triples.append((c["t"], repo["name"], len(triples), c))
    triples.sort(key=lambda x: (x[0], x[1], x[2]))
    rows = []
    for pos, (t, name, _, c) in enumerate(triples):
        pub = name not in private
        rows.append({"i": pos, "r": name, "t": t, "c": c["cls"], "w": c["who"], "a": c["add"], "d": c["del"], "f": c["files"],
                     "m": int(bool(c.get("merge"))), "b": int(bool(c.get("default", True))), "tr": int(bool(c.get("ai_trailer"))),
                     "pv": not pub,
                     "sha": c.get("sha") if pub else None, "oid": c.get("oid") if pub else None,
                     "paths": c.get("paths") if pub else None, "kinds": c.get("kinds") if pub else None})
    return rows


def _naive_fork(r: dict, fork: str) -> bool:
    if fork == "all":
        return True
    if r["m"]:
        return False
    if fork == "no_merge":
        return True
    if fork == "default_branch":
        return r["b"] == 1
    return r["f"] <= 15


def _naive_hour_weekday(t: int) -> tuple[int, int]:
    try:
        d = dt.datetime.fromtimestamp(t, dt.timezone.utc)
        return d.hour, d.weekday()
    except (ValueError, OverflowError, OSError):
        days, rest = divmod(t, 86400)
        return rest // 3600, (days + 3) % 7


def _naive_sorted_by_time(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["t"], r["i"]))


def _naive_quantile(sorted_vals: list[int], num: int, den: int) -> int:
    """The least value x with at least ceil(n*num/den) elements <= x, counting elements as it goes."""
    n = len(sorted_vals)
    need = max(1, -(-n * num // den))
    seen = 0
    for x, grp in itertools.groupby(sorted_vals):
        seen += sum(1 for _ in grp)
        if seen >= need:
            return x
    return sorted_vals[-1]


def _naive_median(vals: list[int]) -> int:
    s = sorted(vals)
    return s[(len(s) - 1) // 2]


def _naive_permille(frac: Fraction) -> int:
    """frac * 1000 rounded half away from zero, by Fraction arithmetic."""
    scaled = abs(frac) * 1000 + Fraction(1, 2)
    v = scaled.numerator // scaled.denominator
    return -v if frac < 0 else v


def _naive_gini_permille(vals: list[int]):
    n, total = len(vals), sum(vals)
    if n < 10 or total <= 0:
        return None
    if n <= THRESHOLDS["VERIFY_PAIRWISE_N"]:
        mult = collections.Counter(vals)
        keys = sorted(mult)
        acc = 0
        for a, b in itertools.combinations(keys, 2):
            acc += 2 * mult[a] * mult[b] * (b - a)          # ordered pairs (a,b) and (b,a)
        return _naive_permille(Fraction(acc, 2 * n * total))
    s = sorted(vals)
    return _naive_permille(Fraction(sum((2 * k - n - 1) * x for k, x in enumerate(s, 1)), n * total))


def _naive_top_permille(vals: list[int]):
    n, total = len(vals), sum(vals)
    if n < 10 or total <= 0:
        return None
    k = -(-n // 10)
    return _naive_permille(Fraction(sum(sorted(vals, reverse=True)[:k]), total))


def _naive_summary(vals: list[int], cap: int, unit: int) -> dict:
    s = sorted(vals)
    n = len(s)
    bins = [0] * (cap + 1)
    for x in s:
        k = 0
        y = x // unit
        while y:
            y >>= 1
            k += 1
        bins[min(k, cap)] += 1
    med = _naive_median(s)
    out = {"n": n, "sum": sum(s) if sum(s) <= stats.SAFE_INT else str(sum(s)), "min": s[0], "max": s[-1]}
    for name, (num, den) in stats.PCTS.items():
        out[name] = _naive_quantile(s, num, den)
    out["mad"] = _naive_median([abs(x - med) for x in s])
    out["bins"] = bins
    out["bin_rule"] = {"unit": unit, "cap": cap}
    out["gini_permille"] = _naive_gini_permille(s)
    out["top_decile_permille"] = _naive_top_permille(s)
    return out


def _naive_gaps(members: list[dict]) -> list[int]:
    ts = [r["t"] for r in _naive_sorted_by_time(members)]
    return [ts[k] - ts[k - 1] for k in range(1, len(ts))]


def _naive_profile(rows: list[dict]) -> list[dict]:
    recs = []
    classes = [c for c in structure.CLASSES if any(r["c"] == c for r in rows)]
    classes += sorted({r["c"] for r in rows} - set(structure.CLASSES))
    counts = collections.Counter(r["r"] for r in rows)
    repos = sorted(counts, key=lambda n: (-counts[n], n))
    plan = [("family", stats.FORKS, rows)]
    plan += [(f"class:{c}", stats.FORKS, [r for r in rows if r["c"] == c]) for c in classes]
    plan += [(f"repo:{n}", ("no_merge",), [r for r in rows if r["r"] == n]) for n in repos]
    for sid, forks, srows in plan:
        for fork in forks:
            mem = [r for r in srows if _naive_fork(r, fork)]
            for measure in MEASURES:
                rec = {"slice": sid, "fork": fork, "measure": measure, "available": len(mem) >= 10, "reason": None, "members": len(mem)}
                if len(mem) < 10:
                    rec["reason"] = "fewer than 10 commits in this slice and fork"
                else:
                    if measure == "lines":
                        rec.update(_naive_summary([r["a"] + r["d"] for r in mem], 16, 1))
                    elif measure == "files":
                        rec.update(_naive_summary([r["f"] for r in mem], 16, 1))
                    else:
                        rec.update(_naive_summary(_naive_gaps(mem), 19, 60))
                recs.append(rec)
    return recs


def _naive_circular_quiet(hours: list[int], n: int) -> tuple[int, object]:
    flags_ = [96 * h <= n for h in hours]
    if all(flags_):
        return 24, 0
    best, start = 0, None
    for s in range(24):
        length = 0
        while length < 24 and flags_[(s + length) % 24]:
            length += 1
        if length > best:
            best, start = length, s
    return best, start


def _naive_bursts(members: list[dict], theta: int) -> list[tuple[int, int, int]]:
    """(size, first t, last t) of each burst by a left-to-right sweep."""
    ts = [r["t"] for r in _naive_sorted_by_time(members)]
    out, lo = [], 0
    for k in range(1, len(ts) + 1):
        if k == len(ts) or ts[k] - ts[k - 1] > theta:
            out.append((k - lo, ts[lo], ts[k - 1]))
            lo = k
    return out


def _naive_rhythm(rows: list[dict]) -> list[dict]:
    counts = collections.Counter(r["r"] for r in rows)
    classes = [c for c in structure.CLASSES if any(r["c"] == c for r in rows)] + sorted({r["c"] for r in rows} - set(structure.CLASSES))
    plan = [("family", "family", rows)] + [(f"class:{c}", "class", [r for r in rows if r["c"] == c]) for c in classes]
    plan += [(f"repo:{n}", "repo", [r for r in rows if r["r"] == n]) for n in sorted(counts, key=lambda n: (-counts[n], n))]
    out = []
    for sid, kind, srows in plan:
        mem = [r for r in srows if not r["m"]]
        n = len(mem)
        hist = [0] * 24
        for r in mem:
            hist[_naive_hour_weekday(r["t"])[0]] += 1
        rec = {"slice": sid, "n": n, "hours": hist if kind != "repo" else None}
        if n >= 120:
            run_, start = _naive_circular_quiet(hist, n)
            rec["quiet"] = {"available": True, "run": run_, "start_hour": start,
                            "quiet_hours": [x for x in range(24) if 96 * hist[x] <= n]}
        else:
            rec["quiet"] = {"available": False, "run": None, "start_hour": None, "quiet_hours": []}
        if n >= 20:
            by_gap = {}
            for theta in (3600, 7200, 14400):
                bs = _naive_bursts(mem, theta)
                sizes = sorted(b[0] for b in bs)
                top = max(b[0] for b in bs)
                first = next(b for b in bs if b[0] == top)
                singles = sum(1 for b in bs if b[0] == 1)
                by_gap[str(theta)] = {"n_bursts": len(bs), "median_commits": sizes[(len(sizes) - 1) // 2], "largest_commits": top,
                                      "largest_duration_s": first[2] - first[1],
                                      "single_commit_permille": _naive_permille(Fraction(singles, len(bs)))}
            nb = [v["n_bursts"] for v in by_gap.values()]
            rec["bursts"] = {"available": True, "by_gap": by_gap, "stable": 2 * max(nb) <= 3 * min(nb)}
        else:
            rec["bursts"] = {"available": False, "by_gap": {}, "stable": None}
        out.append(rec)
    return out


def _naive_concentration(ds: dict) -> list[dict]:
    seen, out = set(), []
    for rec in sorted(ds.get("repos", []), key=lambda r: r["name"]):
        if rec["name"] in seen or not rec.get("present", True):
            continue
        seen.add(rec["name"])
        e = {"repo": rec["name"], "available": False, "unknown": False}
        if rec.get("visibility") == "private":
            e["unknown"], e["reason"] = True, "counts only (private)"
        elif not rec.get("tree_measured") or not rec.get("files"):
            e["unknown"], e["reason"] = True, "tree not harvested"
        else:
            vals = [f["churn"] for f in rec["files"] if f.get("churn")]
            e["n_files"] = len(vals)
            if len(vals) >= 10:
                e["available"] = True
                e["total_churn"] = sum(vals) if sum(vals) <= stats.SAFE_INT else str(sum(vals))
                e["gini_permille"] = _naive_gini_permille(vals)
                e["top_decile_permille"] = _naive_top_permille(vals)
                run_, half = 0, None
                total = sum(vals)
                for k, x in enumerate(sorted(vals, reverse=True), 1):
                    run_ += x
                    if run_ * 2 >= total:
                        half = k
                        break
                e["half_k"] = half
        out.append(e)
    return out


def _naive_flags(rows: list[dict]) -> dict:
    """{rule: {grade: [[i,k,m]...], 'evaluable': n}} with each stratum cut by plain filtering of the fork's members."""
    fires: dict[str, dict[int, list[int]]] = {"size_high": {}, "gap_long": {}}
    seen: set[frozenset] = set()
    for fork in ("all", "no_merge", "default_branch", "no_mass"):
        mem = [r for r in rows if _naive_fork(r, fork)]
        ids = frozenset(r["i"] for r in mem)
        if ids in seen:
            continue
        seen.add(ids)
        keys = sorted({(r["r"], r["c"]) for r in mem})
        for repo, cls in keys:
            g = [r for r in mem if r["r"] == repo and r["c"] == cls]
            if len(g) < 20:
                continue
            b = [min(16, (r["a"] + r["d"]).bit_length()) for r in g]
            med = _naive_median(b)
            md = _naive_median([abs(x - med) for x in b])
            for r, x in zip(g, b):
                e = fires["size_high"].setdefault(r["i"], [0, 0])
                e[0] += 1
                if x - med >= max(3, 3 * md) and r["a"] + r["d"] >= 64:
                    e[1] += 1
        for repo in sorted({r["r"] for r in mem}):
            g = _naive_sorted_by_time([r for r in mem if r["r"] == repo])
            if len(g) - 1 < 20:
                continue
            gaps = [g[k]["t"] - g[k - 1]["t"] for k in range(1, len(g))]
            b = [min(19, (x // 60).bit_length()) for x in gaps]
            med = _naive_median(b)
            md = _naive_median([abs(x - med) for x in b])
            for k in range(1, len(g)):
                e = fires["gap_long"].setdefault(g[k]["i"], [0, 0])
                e[0] += 1
                if b[k - 1] - med >= max(4, 3 * md) and gaps[k - 1] >= 604800:
                    e[1] += 1
    out = {}
    for rule, per in fires.items():
        entry = {"C": [], "L": [], "S": [], "evaluable": len(per), "totals": {"C": 0, "L": 0, "S": 0}}
        for i in sorted(per):
            m, k = per[i]
            if k < 1:
                continue
            if m >= 3 and k == m:
                gr = "C"
            elif (m >= 2 and k == m) or (m >= 3 and 2 * k > m):
                gr = "L"
            else:
                gr = "S"
            entry["totals"][gr] += 1
            if len(entry[gr]) < 200:
                entry[gr].append([i, k, m])
        out[rule] = entry
    return out


def _naive_audit(rows: list[dict], ds: dict) -> dict:
    """{check id: (count, grade or None, status)} and the coverage matrix, by sort-and-compare."""
    epoch = _harvest_epoch(ds)
    res: dict[str, tuple] = {}

    bad = [r for r in rows if r["t"] < 1112832000 or (epoch is not None and r["t"] > epoch)]
    res["ts_range"] = (len(bad), "C", {r["i"] for r in bad})

    pub = [r for r in rows if not r["pv"]]
    hit: set[int] = set()
    with_oid = sorted((r for r in pub if r["oid"]), key=lambda r: (r["oid"], r["r"]))
    for _, grp in itertools.groupby(with_oid, key=lambda r: r["oid"]):
        grp = list(grp)
        if len({r["r"] for r in grp}) >= 2:
            hit.update(r["i"] for r in grp)
    with_sha = sorted((r for r in pub if (r["sha"] or (r["oid"] or "")[:12])), key=lambda r: (r["r"], (r["sha"] or r["oid"][:12])[:12]))
    for _, grp in itertools.groupby(with_sha, key=lambda r: (r["r"], (r["sha"] or r["oid"][:12])[:12])):
        grp = list(grp)
        if len(grp) >= 2:
            hit.update(r["i"] for r in grp)
    res["dup_oid"] = (len(hit), "C", hit)

    cand = sorted((r for r in rows if r["i"] not in hit and not r["m"] and not (r["f"] == 0 and r["a"] + r["d"] == 0)),
                  key=lambda r: (r["t"], r["c"], r["w"], r["a"], r["d"], r["f"], r["m"]))
    fp: set[int] = set()
    likely = False
    for _, grp in itertools.groupby(cand, key=lambda r: (r["t"], r["c"], r["w"], r["a"], r["d"], r["f"], r["m"])):
        grp = list(grp)
        if len({r["r"] for r in grp}) >= 2:
            fp.update(r["i"] for r in grp)
            if all((not r["pv"]) and r["oid"] for r in grp) and len({r["oid"] for r in grp}) == len(grp):
                likely = True
    res["dup_fingerprint"] = (len(fp), "L" if likely else "S", fp)

    ordered = sorted(rows, key=lambda r: (r["r"], r["t"]))
    runs = [(list(g)) for _, g in itertools.groupby(ordered, key=lambda r: (r["r"], r["t"]))]
    longest = max((len(g) for g in runs), default=0)
    ss: set[int] = set()
    if longest >= 5:
        for g in runs:
            if len(g) >= 2:
                ss.update(r["i"] for r in g)
    res["same_second"] = (len(ss), "S", ss)

    em = {r["i"] for r in rows if r["m"] == 0 and r["f"] == 0}
    res["empty_commit"] = (len(em), "S", em)
    sf = {r["i"] for r in rows if r["m"] == 0 and r["f"] > 0 and r["a"] + r["d"] == 0}
    res["stat_free_change"] = (len(sf), "S", sf)
    mc = {r["i"] for r in rows if r["m"] == 1 and (r["f"] or r["a"] or r["d"])}
    res["merge_contract"] = (len(mc), "C", mc)

    judged, cr = 0, set()
    for repo, grp in itertools.groupby(sorted(rows, key=lambda r: r["r"]), key=lambda r: r["r"]):
        grp = list(grp)
        if len(grp) < 10:
            continue
        judged += 1
        resid = [r for r in grp if r["c"] in ("other", "upstream")]
        if _naive_permille(Fraction(len(resid), len(grp))) >= 250:
            cr.update(r["i"] for r in resid)
    res["class_residual"] = (len(cr), "S", cr)
    return {"checks": res, "judged": judged, "longest_same_second": longest}


def _mask(text: str, private: set[str]) -> str:
    """A message never names a private repo before a ':' or '/'."""
    for name in sorted((p for p in private if p), key=len, reverse=True):
        for sep in (":", "/"):
            text = text.replace(name + sep, "(private)" + sep)
    return text


def _slice_label(sid: str, private: set[str]) -> str:
    if sid.startswith("repo:") and sid[5:] in private:
        return "repo:(private)"
    return sid


def _diff_dict(path: str, a, b, out: list[str], limit: int = 3) -> None:
    """Names the first keys where two JSON values differ; never prints a value."""
    if not _ne(a, b):
        return
    if isinstance(a, dict) and isinstance(b, dict):
        bad = [k for k in sorted(set(a) | set(b), key=str) if _ne(a.get(k), b.get(k))]
        out.append(f"{path} differs in {', '.join(str(k) for k in bad[:limit])}")
    else:
        out.append(f"{path} differs")


def _verify_structure(eda: dict, private: set[str], out: list[str]) -> None:
    prof = eda.get("profile", {}).get("records", [])
    fam: dict[str, int] = {}
    for rec in prof:
        if rec.get("slice") == "family" and rec.get("measure") == "lines":
            fam[rec.get("fork")] = rec.get("members", 0)
    for rec in prof:
        label = _slice_label(str(rec.get("slice")), private)
        if rec.get("available"):
            if sum(rec.get("bins", [])) != rec.get("n"):
                out.append(f"profile {label} {rec.get('fork')} {rec.get('measure')}: bins do not sum to n")
            if rec.get("n", 0) > rec.get("members", 0):
                out.append(f"profile {label} {rec.get('fork')} {rec.get('measure')}: n exceeds members")
        if rec.get("members", 0) > fam.get(rec.get("fork"), 0) and rec.get("fork") in fam:
            out.append(f"profile {label} {rec.get('fork')}: slice larger than the family")
    for rule, body in eda.get("flags", {}).get("by_rule", {}).items():
        cap = body.get("cap", 0)
        for g in GRADES:
            listed = body.get(g, [])
            omitted = body.get("omitted", {}).get(g, -1)
            if type(omitted) is not int or type(cap) is not int:
                out.append(f"flags {rule} {g}: omitted or cap is not an integer")
            if len(listed) + omitted != body.get("counts", {}).get(g, -2):
                out.append(f"flags {rule} {g}: listed plus omitted differs from the count")
            if len(listed) > cap:
                out.append(f"flags {rule} {g}: more entries than the cap")
            if [e[0] for e in listed] != sorted({e[0] for e in listed}):
                out.append(f"flags {rule} {g}: indices are not ascending and unique")
    for chk in eda.get("audit", {}).get("checks", []):
        if chk.get("grade") not in (None, "C", "L", "S"):
            out.append(f"audit {chk.get('id')}: grade outside C, L, S")
        if chk.get("status") == "pass" and chk.get("count"):
            out.append(f"audit {chk.get('id')}: passes with a nonzero count")


def verify(eda: dict, ds: dict) -> list[str]:
    private = {r["name"] for r in ds.get("repos", []) if r.get("visibility") == "private"}
    out: list[str] = []
    try:
        rows = _naive_rows(ds)
        want = _plain(eda)
        floats: list[str] = []
        _floats(want, "eda", floats)
        if floats:
            out.append(f"eda: {len(floats)} float values, first at {floats[0]}; the stage writes integers only")
        if want.get("stage") != "eda" or type(want.get("schema_version")) is not int or want.get("schema_version") != 1:
            out.append("eda: stage or schema_version is wrong")
        method = want.get("method", {})
        if _ne(method.get("thresholds"), _plain(THRESHOLDS)):
            out.append("method: thresholds differ from the code's")
        if method.get("code_sha256") != _code_sha256():
            out.append("method: code_sha256 differs from the source files")
        if _ne(method.get("forks"), FORK_DEFS):
            out.append("method: fork definitions differ")
        labels = {c: next((i["label"] for i in ds.get("identities", []) if i.get("class") == c and i.get("label")), c)
                  for c in structure.CLASSES}
        cls = want.get("classes", {})
        if cls.get("order") != list(structure.CLASSES) or cls.get("labels") != labels:
            out.append("classes: order or labels differ")
        looks_section = want.get("looks", {})
        if isinstance(looks_section, dict) and (cls.get("pair"), cls.get("pair_reason")) != (looks_section.get("pair"), looks_section.get("pair_reason")):
            out.append("classes: pair differs from looks")

        names = sorted({r["name"] for r in ds.get("repos", []) if r.get("present", True)} | {r["r"] for r in rows})
        cov = want.get("coverage", {})
        forks4 = ("all", "no_merge", "default_branch", "no_mass")
        cov_want = {
            "commits": len(rows), "merges": sum(1 for r in rows if r["m"] == 1), "private_commits": sum(1 for r in rows if r["pv"]),
            "repos": names,
            "private_repos": len({r["name"] for r in ds.get("repos", []) if r.get("visibility") == "private" and r.get("present", True)}),
            "by_fork": {f: sum(1 for r in rows if _naive_fork(r, f)) for f in ("all", "no_merge", "default_branch", "no_mass")},
            "distinct_forks": len({frozenset(r["i"] for r in rows if _naive_fork(r, f)) for f in forks4}),
            "fork_same_as": {},
        }
        first_of: dict[frozenset, str] = {}
        for f in forks4:
            twin = first_of.setdefault(frozenset(r["i"] for r in rows if _naive_fork(r, f)), f)
            cov_want["fork_same_as"][f] = None if twin == f else twin
        _diff_dict("coverage", cov, cov_want, out)

        # audit
        na = _naive_audit(rows, ds)
        have = {c.get("id"): c for c in want.get("audit", {}).get("checks", [])}
        order = ["ts_range", "dup_oid", "dup_fingerprint", "same_second", "empty_commit", "stat_free_change", "merge_contract", "class_residual"]
        if list(have) != order:
            out.append("audit: check ids or order differ")
        for cid in order:
            if cid not in have:
                continue
            count, grade, idx = na["checks"][cid]
            c = have[cid]
            if _ne(c.get("count"), count):
                out.append(f"audit {cid}: count differs")
            if count and _ne(c.get("grade"), grade):
                out.append(f"audit {cid}: grade differs")
            if not count and c.get("grade") is not None:
                out.append(f"audit {cid}: grade on a passing check")
            by = collections.Counter(rows[i]["r"] for i in idx)
            want_by = [{"repo": n, "count": by[n]} for n in sorted(by, key=lambda n: (-by[n], n))]
            if _ne(c.get("by_repo"), want_by):
                out.append(f"audit {cid}: by_repo differs")
            shas = sorted({(rows[i]["sha"] or rows[i]["oid"][:12])[:12] for i in idx
                           if not rows[i]["pv"] and (rows[i]["sha"] or rows[i]["oid"])})[:5]
            if _ne(c.get("witnesses"), shas):
                out.append(f"audit {cid}: witnesses differ")
            want_status = "pass" if not count else "flag"
            if (cid == "class_residual" and not na["judged"]) or (cid == "ts_range" and not count and _harvest_epoch(ds) is None):
                want_status = "unavailable"
            if c.get("status") != want_status:
                out.append(f"audit {cid}: status differs")
        mine_cov = {e.get("repo"): e for e in want.get("audit", {}).get("coverage", [])}
        order_cov = sorted(mine_cov, key=lambda n: (-mine_cov[n].get("n", 0), n))
        if list(mine_cov) != order_cov or sorted(mine_cov) != names:
            out.append("audit coverage: repos or order differ")
        repo_rec = {}
        for r in ds.get("repos", []):
            repo_rec.setdefault(r["name"], r)
        for name, e in mine_cov.items():
            g = [r for r in rows if r["r"] == name]
            nm = [r for r in g if r["m"] == 0]
            priv = repo_rec.get(name, {}).get("visibility") == "private"
            wp = [r for r in nm if r["paths"]]
            exp = {"n": len(g), "n_nonmerge": len(nm), "n_default": sum(1 for r in nm if r["b"] == 1),
                   "n_with_paths": None if priv else len(wp),
                   "n_usable": None if priv else sum(1 for r in wp if r["f"] <= structure.MAX_FILES),
                   "eligible": {"profile": len(nm) >= 10, "rhythm": len(nm) >= 120, "bursts": len(nm) >= 20, "contrast": len(nm) >= 20}}
            for k, v in exp.items():
                if _ne(e.get(k), v):
                    out.append(f"audit coverage {_slice_label('repo:' + name, private)}: {k} differs")
        lim_ids = [x.get("id") for x in want.get("audit", {}).get("limitations", [])]
        if "private_counts_only" not in lim_ids or "exchangeable" not in lim_ids or "upstream_excluded" not in lim_ids:
            out.append("audit: a standing limitation is missing")
        up = sum(1 for r in ds.get("repos", []) for c in r.get("commits", []) if c.get("upstream"))
        for x in want.get("audit", {}).get("limitations", []):
            if x.get("id") == "upstream_excluded" and f"{up} commits" not in x.get("text", ""):
                out.append("audit: upstream_excluded count differs")

        # profile
        mine = want.get("profile", {}).get("records", [])
        exp = _naive_profile(rows)
        if len(mine) != len(exp):
            out.append(f"profile: {len(mine)} records, expected {len(exp)}")
        for k, (a, b) in enumerate(zip(mine, exp)):
            if _ne(a, b):
                for key in ("slice", "fork", "measure"):
                    if a.get(key) != b.get(key):
                        out.append(f"profile record {k}: {key} differs")
                        break
                else:
                    _diff_dict(f"profile {_slice_label(str(b['slice']), private)} {b['fork']} {b['measure']}", a, b, out)

        # rhythm
        mine = want.get("rhythm", {}).get("slices", [])
        exp = _naive_rhythm(rows)
        if [s.get("slice") for s in mine] != [s["slice"] for s in exp]:
            out.append("rhythm: slices differ")
        else:
            for a, b in zip(mine, exp):
                label = _slice_label(b["slice"], private)
                for key in ("n", "hours"):
                    if _ne(a.get(key), b[key]):
                        out.append(f"rhythm {label}: {key} differs")
                for part in ("quiet", "bursts"):
                    for k, v in b[part].items():
                        if _ne(a.get(part, {}).get(k), v):
                            out.append(f"rhythm {label}: {part}.{k} differs")

        # concentration
        mine = want.get("concentration", {}).get("repos", [])
        exp = _naive_concentration(ds)
        if [e.get("repo") for e in mine] != [e["repo"] for e in exp]:
            out.append("concentration: repos differ")
        else:
            for a, b in zip(mine, exp):
                for k, v in b.items():
                    if _ne(a.get(k), v):
                        out.append(f"concentration {_slice_label('repo:' + b['repo'], private)}: {k} differs")

        # flags
        nf = _naive_flags(rows)
        by_rule = want.get("flags", {}).get("by_rule", {})
        for rule in RULES:
            body = by_rule.get(rule, {})
            e = nf[rule]
            for g in GRADES:
                if _ne(body.get(g), e[g]):
                    out.append(f"flags {rule} {g}: entries differ")
                if _ne(body.get("counts", {}).get(g), e["totals"][g]):
                    out.append(f"flags {rule} {g}: count differs")
            if _ne(body.get("counts", {}).get("evaluable"), e["evaluable"]):
                out.append(f"flags {rule}: evaluable count differs")
        if sorted(by_rule) != sorted(RULES):
            out.append("flags: rule set differs")

        _verify_structure(want, private, out)
        if not isinstance(looks_section, dict):
            out.append("looks: section is missing")
        else:
            try:
                out.extend(_import_looks().verify(looks_section, indexed_rows(ds)))
            except Exception as exc:
                out.append(f"looks: verification failed ({type(exc).__name__})")
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        out.append(f"eda: malformed section ({type(exc).__name__})")
    return [_mask(m, private) for m in out]
