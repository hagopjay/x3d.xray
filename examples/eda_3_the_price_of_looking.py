#!/usr/bin/env python3
"""Example 3: the same question, asked later, is worth less.

One question about one synthetic history ("do human and Claude commits in one repo fall in different four-hour blocks?") is asked after k other
looks have been taken. The data do not change and neither does the permutation count behind it: the add-one p is 0.0020 every time. What changes
is q, because q is corrected over the 12 declared looks plus every look taken in the session, and the answer prints that denominator. By the
time about 38 other looks have been taken, the same answer no longer clears q <= 1/20. This is the tool counting the garden of forking paths for
you. The count is also what the page's notebook shows, and what the investigation chain records.

It runs the page's own tools.js in Node (the same code Ask Claude and the MCP server use), so it needs `node`.

Run: python3 examples/eda_3_the_price_of_looking.py            (about 5 s)
"""
from __future__ import annotations

from _common import eda, minimal_bundle, node_available, run_node, synthetic_dataset, table

DATA = dict(seed=7, repos=4, per_repo=150, plant=("ai_hours",))
PRIOR_LOOKS = (0, 1, 2, 4, 8, 16, 24, 31, 38)
SCRIPT = r'''
const ks = JSON.parse(process.argv[2]);
const mk = () => { const seen = new Set(); let asked = 0;
  return { note(l) { const k = JSON.stringify(l); if (seen.has(k)) return; seen.add(k); if (l.kind !== 'declared') asked++; }, get asked() { return asked; }, settle: async () => null, unrecorded: [] }; };
const run = (t, a) => t.find(x => x.name === 'eda').execute(a);
const decoys = [];
for (let h = 0; h < 24; h++) decoys.push({ topic: 'slice', hour: h, measure: 'lines' });
for (let w = 0; w < 7; w++) decoys.push({ topic: 'slice', weekday: String(w), measure: 'lines' });
for (const r of B.repos) decoys.push({ topic: 'slice', repo: r.name, measure: 'lines' });
for (const r of B.repos) for (const m of ['files', 'gap_s']) decoys.push({ topic: 'slice', repo: r.name, measure: m });
const rows = [];
for (const k of ks) {
  const t = T.makeTools(B, null, { looks: mk() });
  for (let i = 0; i < k; i++) run(t, decoys[i]);
  const r = run(t, { topic: 'compare', a: { who: 'you', repo: 'r1' }, b: { who: 'ai', repo: 'r1' }, measure: 'hour4', fork: 'all' });
  rows.push({ prior: k, m: r.family.m, p: r.null_model.p_text, q: r.q_text, asked: r.looks_asked_this_session, text: r.text });
}
console.log(JSON.stringify({ rows, decoys: decoys.length }));
'''


def run() -> dict:
    if not node_available():
        raise RuntimeError("this example runs the page's tools.js and needs node")
    section, problems = eda.run(synthetic_dataset(**DATA))
    assert problems == [], problems
    out = run_node(SCRIPT, minimal_bundle(synthetic_dataset(**DATA), section), __import__("json").dumps(list(PRIOR_LOOKS)))
    out["limit"] = "q <= 1/20"
    return out


def report(r: dict) -> str:
    rows = [[x["prior"], x["m"], x["p"], x["q"], "clears q <= 1/20" if float(x["q"]) <= 0.05 else "does not clear"] for x in r["rows"]]
    return "\n".join(["THE PRICE OF LOOKING (synthetic, seed 7: 'you' vs 'ai' in one repo, UTC four-hour block, all commits)",
                      table(rows, ["other looks first", "q counted over m =", "p (add-one)", "q", "against 1/20"]),
                      f"\none answer: {r['rows'][0]['text']}",
                      "Read it as: p never moves, since the data and the permutation count are the same. q moves, because m does: every look you take is added to the family."])


def main() -> int:
    print(report(run()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
