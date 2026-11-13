"""Synthetic datasets for the EDA tests (not a test module). Everything is deterministic from the seed: a local
random.Random, no wall clock, no global state. The dataset has the shape of data/dataset.json."""
from __future__ import annotations

import copy
import hashlib
import random

HARVESTED_AT = "2026-02-01T00:00:00Z"
HARVEST_EPOCH = 1769904000          # 2026-02-01T00:00:00Z
BASE_T = 1500000000                 # 2017-07-14, well inside the plausible range
PLANTS = ("ai_hours", "ai_smaller", "confound", "defects", "outlier")


def _oid(seed: int, repo: str, k: int) -> str:
    return hashlib.sha1(f"{seed}|{repo}|{k}".encode()).hexdigest()


def synthetic_dataset(seed: int = 1, repos: int = 3, per_repo: int = 60, private=(), plant=None) -> dict:
    """A dataset of `repos` repos named r0, r1, ... with `per_repo` own commits each. `private` lists repo names that are
    private (they carry no sha, oid, subject, kinds or paths). `plant` is None, one name or a collection of names from PLANTS:
    ai_hours (ai commits only in UTC 8-22, you around the clock), ai_smaller (ai lines 5x smaller everywhere), confound (the
    class follows the repo, no difference inside a repo), defects (see below), outlier (one commit 1000x the median lines)."""
    plant = set([plant] if isinstance(plant, str) else (plant or ()))
    unknown = plant - set(PLANTS)
    if unknown:
        raise ValueError(f"unknown plant {sorted(unknown)}")
    rng = random.Random(seed)
    private = set(private)
    out_repos = []
    for ri in range(repos):
        name = f"r{ri}"
        pv = name in private
        commits, used, day = [], set(), 0
        for k in range(per_repo):
            day += rng.choice([0, 0, 1, 1, 2, 3])
            if rng.random() < 0.03:
                day += rng.randint(10, 40)
            if "confound" in plant:
                cls = "you" if ri == 0 else "ai"
            else:
                cls = "you" if rng.random() < 0.6 else "ai"
            if "ai_hours" in plant and cls == "ai":
                tod = 8 * 3600 + rng.randint(0, 14 * 3600 - 1)
            else:
                tod = rng.randint(0, 86399)
            t = BASE_T + day * 86400 + tod
            while t in used:
                t += 1
            used.add(t)
            merge = rng.random() < 0.05
            files = 0 if merge else rng.choice([1, 1, 1, 2, 2, 3, 4, 6, 9, 12])
            if merge:
                add = dele = 0
            else:
                scale = 6 if ("confound" in plant and ri == 0) else 1
                add = int(rng.random() ** 3 * 120 * scale) + 1
                dele = int(rng.random() ** 3 * 60 * scale)
                if "ai_smaller" in plant and cls == "ai":
                    add, dele = add // 5, dele // 5
            c = {
                "t": t, "who": "Alice" if cls == "you" else "Bot-AI", "cls": cls, "files": files, "add": add, "del": dele,
                "merge": merge, "ai_trailer": cls == "ai", "sessions": [], "default": rng.random() < 0.9, "upstream": False,
            }
            if not pv:
                oid = _oid(seed, name, k)
                c.update(sha=oid[:12], oid=oid, subject=f"change {name} {k}",
                         kinds={"code": files} if files else {},
                         paths=[f"src/{name}/f{rng.randint(0, 40)}_{j}.py" for j in range(files)])
            commits.append(c)
        files_list = [{"path": f"src/f{j}.py", "churn": int(rng.random() ** 2 * 500) + 1, "kind": "code", "lines": 10 + j}
                      for j in range(14)]
        out_repos.append({
            "name": name, "present": True, "visibility": "private" if pv else "public", "kind": "own",
            "tree_measured": not pv, "tree": {"files": 0 if pv else 14, "bytes": 0, "lines": 0, "by_kind": {}},
            "files": [] if pv else files_list, "commits": commits,
        })
    if "defects" in plant:
        _plant_defects(out_repos)
    if "outlier" in plant:
        _plant_outlier(out_repos)
    for r in out_repos:
        r["commits"].sort(key=lambda c: c["t"])
    return {
        "family": "synthetic", "title": "synthetic", "harvested_at": HARVESTED_AT, "lineage": [],
        "identities": [{"label": "Alice", "class": "you"}, {"label": "Bot-AI", "class": "ai"}],
        "repos": out_repos,
    }


def _plant_defects(repos: list[dict]) -> None:
    """Needs at least two public repos with 15 or more commits. Planted in repo 0 by commit position (before the final
    sort): 2 a future timestamp, 3 a pre-2005 timestamp, 5-11 seven commits in one second, 12 the oid shared with repo 1,
    13 the fingerprint shared with repo 1, 14 a merge with three files. In repo 1: 12 takes repo 0's oid and sha, 13 takes
    repo 0's t, class, author and sizes with its own oid, 14 an empty non-merge commit. Expected audit counts: ts_range 2,
    dup_oid 2, dup_fingerprint 2, same_second 7, merge_contract 1, empty_commit 1."""
    a, b = repos[0]["commits"], repos[1]["commits"]
    a[2]["t"] = HARVEST_EPOCH + 1000
    a[3]["t"] = 1000000000
    for k in range(5, 12):
        a[k]["t"] = a[5]["t"]
    b[12]["oid"], b[12]["sha"] = a[12]["oid"], a[12]["sha"]
    for key in ("t", "cls", "who", "add", "del", "files", "merge"):
        b[13][key] = a[13][key]
    a[13]["merge"] = b[13]["merge"] = False
    a[13]["files"] = b[13]["files"] = max(1, a[13]["files"])
    a[13]["add"] = b[13]["add"] = max(2, a[13]["add"])
    a[13]["ai_trailer"] = b[13]["ai_trailer"] = a[13]["cls"] == "ai"
    for c in (a[13], b[13]):
        c["paths"] = c["paths"][:c["files"]] if c.get("paths") else c.get("paths")
    a[14].update(merge=True, files=3, add=0, **{"del": 0})
    b[14].update(merge=False, files=0, add=0, **{"del": 0}, paths=[], kinds={})
    b[14]["cls"], b[14]["who"] = "you", "Alice"


def _plant_outlier(repos: list[dict]) -> None:
    """Commit 30 of repo 0 gets 1000 times the median lines of the dataset; it is a non-merge, default-branch, one-file
    commit by `you`, so every fork contains it."""
    lines = sorted(c["add"] + c["del"] for r in repos for c in r["commits"] if not c["merge"])
    med = lines[(len(lines) - 1) // 2]
    c = repos[0]["commits"][30]
    c.update(merge=False, files=1, add=1000 * max(1, med), cls="you", who="Alice", ai_trailer=False, default=True)
    c["del"] = 0
    if "paths" in c:
        c["paths"], c["kinds"] = ["src/big.py"], {"code": 1}


def bundle_commit_rows(ds: dict) -> list[dict]:
    """The commit rows of page.make_bundle for this dataset (the same construction, copied)."""
    rows = sorted(((r["name"], c) for r in ds["repos"] for c in r.get("commits", []) if not c.get("upstream")),
                  key=lambda rc: (rc[1]["t"], rc[0]))
    out = []
    for i, (name, c) in enumerate(rows):
        row = {"i": i, "r": name, "t": c["t"], "c": c["cls"], "w": c["who"], "a": c["add"], "d": c["del"], "f": c["files"],
               "m": 1 if c.get("merge") else 0, "b": 1 if c.get("default", True) else 0, "tr": 1 if c.get("ai_trailer") else 0}
        if c.get("sessions"):
            row["s"] = c["sessions"]
        if c.get("sha"):
            row["h"] = c["sha"]
        if c.get("subject"):
            row["sub"] = c["subject"]
        out.append(row)
    return out


def minimal_bundle(ds: dict, eda=None) -> dict:
    """The smallest bundle the JavaScript tool tests need: repos, an empty vocabulary, the commit rows and the eda stage."""
    return {"repos": [{"name": r["name"], "visibility": r.get("visibility", "public")} for r in ds["repos"]],
            "vocab": {}, "commits": bundle_commit_rows(ds), "eda": copy.deepcopy(eda)}
