#!/usr/bin/env python3
"""Compare synthetic chain _bipartite() results/timings in two checkouts.

Run from the candidate checkout:
  python3 experiments/benchmark_passage_index.py --baseline ../x3d.xray --candidate . \
    --output experiments/passage-index-benchmark.json

Each checkout is imported in a separate process. The timed region is the whole
_bipartite call, including unchanged exact betweenness. Graph construction and
imports are outside it. These are synthetic observations, not production claims.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from time import perf_counter, process_time


def worker(checkout, sessions, repeats, verify=False):
    sys.path.insert(0, str(checkout.resolve()))
    from x3dray import structure

    pairs = {}
    for i in range(sessions):
        pairs[(f"session:s{i}", f"repo:r{i}")] = [f"sha-left-{i}"]
        pairs[(f"session:s{i}", f"repo:r{i + 1}")] = [f"sha-right-{i}"]
    repos = [f"r{i}" for i in range(sessions + 1)]
    wall, cpu, signatures, verification_wall, verification_cpu = [], [], [], [], []
    for _ in range(repeats):
        start_wall, start_cpu = perf_counter(), process_time()
        result = structure._bipartite(pairs, Counter(), repos)
        cpu.append(process_time() - start_cpu)
        wall.append(perf_counter() - start_wall)
        signatures.append(hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        if verify:
            start_wall, start_cpu = perf_counter(), process_time()
            problems = structure._graph_checks(result[1], result[0])
            verification_cpu.append(process_time() - start_cpu)
            verification_wall.append(perf_counter() - start_wall)
            if problems:
                raise RuntimeError(f"Independent verification found problems: {problems}")
    if len(set(signatures)) != 1:
        raise RuntimeError("Result changed between repetitions")
    return {"sessions": sessions, "repos": sessions + 1, "nodes": sessions * 2 + 1,
            "wall_seconds": wall, "cpu_seconds": cpu,
            "verification_wall_seconds": verification_wall, "verification_cpu_seconds": verification_cpu,
            "median_verification_wall_seconds": statistics.median(verification_wall) if verify else None,
            "median_total_wall_seconds": statistics.median([a + b for a, b in zip(wall, verification_wall)]) if verify else statistics.median(wall),
            "median_wall_seconds": statistics.median(wall), "result_sha256": signatures[0],
            "source_sha256": {name: hashlib.sha256((checkout / "x3dray" / name).read_bytes()).hexdigest()
                              for name in ("centrality.py", "structure.py")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sizes", type=int, nargs="+", default=[10, 20, 40, 60])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--verify", action="store_true", help="also measure the independent production graph verifier")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--sessions", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least one")
    if args.worker is not None:
        print(json.dumps(worker(args.worker, args.sessions, args.repeats, verify=args.verify)))
        return 0
    if args.baseline is None or args.candidate is None:
        parser.error("--baseline and --candidate are required")
    report = {"scope": "Single-host synthetic alternating repository/session chain; full _bipartite call.",
              "python": sys.version, "platform": platform.platform(), "repeats": args.repeats,
              "includes_independent_verifier": args.verify,
              "baseline_commit": subprocess.check_output(["git", "-C", str(args.baseline), "rev-parse", "HEAD"], text=True).strip(),
              "candidate_base_commit": subprocess.check_output(["git", "-C", str(args.candidate), "rev-parse", "HEAD"], text=True).strip(),
              "candidate_state": "working-tree implementation; source patch accompanies deliverable",
              "results": []}
    for size in args.sizes:
        results = []
        for checkout in [args.baseline, args.candidate]:
            cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", str(checkout.resolve()),
                   "--sessions", str(size), "--repeats", str(args.repeats)]
            output = subprocess.check_output(cmd + (["--verify"] if args.verify else []), text=True)
            results.append(json.loads(output))
        old, new = results
        equal = old["result_sha256"] == new["result_sha256"]
        report["results"].append({"sessions": size, "nodes": size * 2 + 1, "baseline": old, "candidate": new,
                                  "exact_output_equal": equal,
                                  "median_speedup": old["median_wall_seconds"] / new["median_wall_seconds"],
                                  "median_total_speedup": old["median_total_wall_seconds"] / new["median_total_wall_seconds"]})
        if not equal:
            raise RuntimeError(f"Output mismatch for {size} sessions")
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
