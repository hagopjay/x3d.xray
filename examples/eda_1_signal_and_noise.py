#!/usr/bin/env python3
"""Example 1: what a Confirmed look is, and what it is not.

Two histories are run through the same twelve declared looks. In one, Claude's commits are planted to be five times smaller and to fall in a
narrower band of hours. In the other nothing is planted at all. The planted effects come out Confirmed, as they should. Then the noise: of the
six pure-noise histories below, one still earns a Confirmed (the window of seeds was chosen to include it; the unselected rate is the calibration line below). That is not a bug. The grade is a robustness grade (the pattern survived the declared
checks, at q <= 1/20), not a probability that the pattern is real, and a few noise histories in a hundred will earn one, which is why the answer
prints how many looks were taken and the notes say "a pattern that survived every declared check in this history; not causal, not predictive".

Run: python3 examples/eda_1_signal_and_noise.py            (about 30 s)
     python3 examples/eda_1_signal_and_noise.py --calibrate (recomputes the 150-history calibration, about 6 minutes)
"""
from __future__ import annotations

import sys

from _common import eda, synthetic_dataset, table

PLANTED = dict(seed=7, repos=4, per_repo=150, plant=("ai_hours", "ai_smaller"))
NOISE_SEEDS = tuple(range(1028, 1034))               # seed 1031 is the one that earns a Confirmed
NOISE = dict(repos=3, per_repo=120)
CALIBRATION_SEEDS = range(1000, 1150)
RECORDED_CALIBRATION = "4 of 150 histories (seeds 1000-1149) earned at least one Confirmed; recorded in the eda PR, not recomputed unless --calibrate"


def looks_of(**kw) -> dict:
    section, problems = eda.run(synthetic_dataset(**kw))
    assert problems == [], problems
    return section["looks"]


def summary(looks: dict) -> dict:
    graded = [f for f in looks["findings"] if f["grade"]]
    return {"by_grade": dict(looks["ledger"]["by_grade"]), "graded": [(f["id"], f["grade"], f["grade_reason"], f["effect"]["permille"], f["q_text"]) for f in graded]}


def run(calibrate: bool = False) -> dict:
    planted = summary(looks_of(**PLANTED))
    noise = {seed: summary(looks_of(seed=seed, **NOISE)) for seed in NOISE_SEEDS}
    out = {"planted": planted, "noise": noise, "noise_with_confirmed": [s for s, r in noise.items() if r["by_grade"]["C"]]}
    if calibrate:
        out["calibration"] = sum(1 for s in CALIBRATION_SEEDS if looks_of(seed=s, **NOISE)["ledger"]["by_grade"]["C"])
    return out


def report(r: dict) -> str:
    lines = ["PLANTED: Claude's commits 5x smaller and in a narrower band of hours (synthetic, seed 7, 4 repos x 150 commits)"]
    lines.append(table([[i, g, why, f"{pm}/1000", q] for i, g, why, pm, q in r["planted"]["graded"]], ["look", "grade", "why", "effect", "q"]))
    lines.append(f"  ledger: {r['planted']['by_grade']}\n")
    lines.append(f"NOISE: nothing planted (synthetic, 3 repos x 120 commits, seeds {NOISE_SEEDS[0]}-{NOISE_SEEDS[-1]})")
    lines.append(table([[s, v["by_grade"]["C"], v["by_grade"]["L"], v["by_grade"]["S"], v["by_grade"]["none"]] for s, v in r["noise"].items()],
                       ["seed", "Confirmed", "Likely", "Suspected", "no grade"]))
    for seed in r["noise_with_confirmed"]:
        lines.append(f"\n  seed {seed} earned a Confirmed from nothing:")
        lines.append("  " + table([[i, g, why, f"{pm}/1000", q] for i, g, why, pm, q in r["noise"][seed]["graded"]], ["look", "grade", "why", "effect", "q"]).replace("\n", "\n  "))
    lines.append("\nCALIBRATION: " + (f"{r['calibration']} of 150 histories (seeds 1000-1149) earned at least one Confirmed (recomputed now)" if "calibration" in r else RECORDED_CALIBRATION))
    lines.append("Read it as: a Confirmed means the pattern survived the declared checks, with q <= 1/20 over all twelve looks. A few noise histories will still earn one.")
    return "\n".join(lines)


def main(argv=None) -> int:
    print(report(run("--calibrate" in (argv if argv is not None else sys.argv[1:]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
