"""Formal concept analysis (x3dray/fca.py) against its definition: a brute-force oracle closes every subset of objects."""

import itertools
import random
import unittest

from x3dray import fca

# Wille's textbook context "Living beings and water" (objects x attributes)
LIVING = {
    "leech": "needs_water lives_in_water motile",
    "bream": "needs_water lives_in_water motile has_limbs",
    "frog": "needs_water lives_in_water lives_on_land motile has_limbs",
    "dog": "needs_water lives_on_land motile has_limbs suckles_young",
    "spike_weed": "needs_water lives_in_water needs_chlorophyll one_seed_leaf",
    "reed": "needs_water lives_in_water lives_on_land needs_chlorophyll one_seed_leaf",
    "bean": "needs_water lives_on_land needs_chlorophyll two_seed_leaves",
    "maize": "needs_water lives_on_land needs_chlorophyll one_seed_leaf",
}
LIVING = {k: v.split() for k, v in LIVING.items()}


def brute(ctx):
    """Oracle: close every subset of objects (the definition itself, no cleverness)."""
    ctx = fca.normalize(ctx)
    objs = list(ctx)
    attrs = frozenset().union(*ctx.values()) if ctx else frozenset()
    found = set()
    for r in range(len(objs) + 1):
        for sub in itertools.combinations(objs, r):
            intent = attrs
            for g in sub:
                intent = intent & ctx[g]
            extent = frozenset(g for g in objs if intent <= ctx[g])
            found.add((extent, intent))
    return found


class FcaTests(unittest.TestCase):
    def test_matches_definition_on_textbook_context(self):
        self.assertEqual(set(fca.concepts(LIVING)), brute(LIVING))

    def test_matches_definition_on_random_contexts(self):
        rnd = random.Random(20261031)
        for _ in range(40):
            n, m = rnd.randint(1, 9), rnd.randint(1, 7)
            ctx = {"o%d" % i: [a for a in range(m) if rnd.random() < 0.45] for i in range(n)}
            self.assertEqual(set(fca.concepts(ctx)), brute(ctx), ctx)

    def test_every_concept_is_closed(self):
        ctx = fca.normalize(LIVING)
        for ext, inten in fca.concepts(LIVING):
            self.assertEqual(ext, frozenset(g for g, a in ctx.items() if inten <= a))
            shared = frozenset.intersection(*[ctx[g] for g in ext]) if ext else frozenset().union(*ctx.values())
            self.assertEqual(inten, shared)

    def test_lattice_has_top_and_bottom(self):
        cs = fca.concepts(LIVING)
        self.assertEqual(len(cs[0][0]), len(LIVING))          # top holds every object
        self.assertTrue(any(len(e) == 0 for e, _ in cs))      # bottom: no object has every attribute

    def test_covers_are_the_transitive_reduction(self):
        cs = fca.concepts(LIVING)
        ed = set(fca.covers(cs))
        for i, (ei, _) in enumerate(cs):
            for j, (ej, _) in enumerate(cs):
                strictly_below = ei < ej
                between = any(ei < cs[k][0] < ej for k in range(len(cs)))
                self.assertEqual((i, j) in ed, strictly_below and not between)

    def test_labels_put_each_name_once(self):
        cs = fca.concepts(LIVING)
        attr_at, obj_at = fca.labels(LIVING, cs)
        self.assertEqual(set(obj_at), set(LIVING))
        for m, i in attr_at.items():
            self.assertIn(m, cs[i][1])
        for g, i in obj_at.items():
            self.assertIn(g, cs[i][0])

    def test_implications_and_equivalences(self):
        ctx = {"a": ["x", "y"], "b": ["x", "y"], "c": ["x"], "d": ["z"]}
        imps, eq = fca.implications(ctx, min_support=1)
        y_x = [i for i in imps if i["if"] == "y" and i["then"] == "x"][0]
        self.assertEqual((y_x["support"], y_x["of"], y_x["confidence"], y_x["exceptions"]), (2, 3, 1.0, []))
        self.assertFalse([i for i in imps if i["if"] == "x" and i["then"] == "y"])
        self.assertEqual(eq, [])
        imps, eq = fca.implications({"a": ["p", "q"], "b": ["p", "q"]}, 1)
        self.assertEqual(eq, [{"attrs": ["p", "q"], "support": 2}])

    def test_confidence_rules_agree_with_counting(self):
        rnd = random.Random(5)
        for _ in range(20):
            ctx = {"o%d" % i: [a for a in "pqrs" if rnd.random() < 0.5] for i in range(rnd.randint(3, 12))}
            rules, _ = fca.implications(ctx, min_support=1, min_confidence=0.5)
            for r in rules:
                has_a = [g for g, a in ctx.items() if r["if"] in a]
                has_both = [g for g in has_a if r["then"] in ctx[g]]
                self.assertEqual(r["support"], len(has_a))
                self.assertEqual(r["both"], len(has_both))
                self.assertEqual(r["exceptions"], sorted(set(has_a) - set(has_both)))
                self.assertAlmostEqual(r["confidence"], len(has_both) / len(has_a), places=4)
                self.assertGreaterEqual(r["confidence"], 0.5)

    def test_levels_run_top_to_bottom(self):
        cs = fca.concepts(LIVING)
        ed = fca.covers(cs)
        lv = fca.levels(cs, ed)
        self.assertEqual(lv[0], 0)
        for lo, up in ed:
            self.assertGreater(lv[lo], lv[up])

    def test_analyse_is_json_ready_and_stable(self):
        import json
        a = fca.analyse(LIVING)
        b = fca.analyse(LIVING)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_iceberg_keeps_only_supported_concepts(self):
        a = fca.analyse(LIVING, min_support=3)
        self.assertTrue(all(c["support"] >= 3 for c in a["concepts"]))
        for i in a["attribute_concept"].values():
            self.assertLess(i, len(a["concepts"]))

    def test_too_many_concepts_is_a_clear_error(self):
        ctx = {"o%d" % i: [j for j in range(14) if j != i] for i in range(14)}  # 2^14 concepts
        with self.assertRaises(fca.TooManyConcepts):
            fca.concepts(ctx, max_concepts=1000)

    def test_empty_context(self):
        self.assertEqual(fca.concepts({}), [(frozenset(), frozenset())])


if __name__ == "__main__":
    unittest.main()
