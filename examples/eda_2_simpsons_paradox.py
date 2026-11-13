#!/usr/bin/env python3
"""Example 2: when the repo explains the difference, the grade says so.

In this synthetic history the author class follows the repo: one repo is all human commits and the others all Claude commits. Across the whole
family Claude's commits look very different from the human ones (they are in different repos, and repos differ). Nothing inside any one repo
differs, because no repo has both kinds of author. A tool that only compared the two groups would call that a finding. This one grades it Likely
at best, and names the reason: no repo held both groups, so the difference cannot be separated from the repo.

Run: python3 examples/eda_2_simpsons_paradox.py            (about 10 s)
"""
from __future__ import annotations

from _common import eda, synthetic_dataset, table

DATA = dict(seed=9, repos=4, per_repo=150, plant="confound")
FOCUS = ("contrast:lines", "contrast:hour4", "contrast:repo")


def run() -> dict:
    section, problems = eda.run(synthetic_dataset(**DATA))
    assert problems == [], problems
    looks = section["looks"]
    by_id = {f["id"]: f for f in looks["findings"]}
    return {"ledger": looks["ledger"]["by_grade"], "pair": looks["pair"], "focus": {i: by_id[i] for i in FOCUS}, "audit_ok": problems == []}


def report(r: dict) -> str:
    rows = []
    for i, f in r["focus"].items():
        s = f["checks"]["strata"]
        rows.append([i, f"{f['effect']['permille']}/1000", f["q_text"], f["grade"] or "no grade", f["grade_reason"],
                     "no repo holds both groups" if s and s["n"] == 0 or s is None else f"{s['same']} of {s['n']} repos agree"])
    return "\n".join([
        "CONFOUNDED: the author class follows the repo (synthetic, seed 9, 4 repos x 150 commits)",
        table(rows, ["look", "effect", "q", "grade", "why", "repo strata"]),
        f"\nledger: {r['ledger']}",
        "Read it as: the difference between the groups is large and its q is small, yet nothing is Confirmed. Strata and leave-one-repo-out are part of the grade,",
        "so a difference that lives between repos is never promoted to a finding about authors. contrast:repo is 1000/1000 because the repo and the class are the same variable here."])


def main() -> int:
    print(report(run()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
