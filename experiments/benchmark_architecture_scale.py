#!/usr/bin/env python3
"""Measure production architecture compile/check/query at declared synthetic sizes.

Example:
  python3 experiments/benchmark_architecture_scale.py \
    --output ../architecture-pass3-deliverables/qa/scale-benchmark.json \
    --jsdom-modules ../qa-runtime/node_modules

Full generated scenes and fixture records are stored next to the report under
scale-fixtures/. Reports state static/query/DOM scope; no rendering is measured.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from x3dray.scaling import benchmark, TOPOLOGIES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sizes', type=int, nargs='+', default=[10, 100, 1000])
    parser.add_argument('--topologies', choices=TOPOLOGIES, nargs='+', default=list(TOPOLOGIES))
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--warmups', type=int, default=2)
    parser.add_argument('--jsdom-modules', type=Path)
    args = parser.parse_args()
    report = benchmark(sizes=args.sizes, topologies=args.topologies, repeats=args.repeats, warmups=args.warmups,
                       output_dir=args.output.resolve().parent / 'scale-fixtures', jsdom_modules=args.jsdom_modules)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(args.output), 'rows': len(report['rows']),
                      'sizes': args.sizes, 'topologies': args.topologies,
                      'disclosure': report['disclosure']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
