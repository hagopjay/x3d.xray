"""Block-cut queries against the independent vertex-removal reference.

Exhaustive small graphs exercise topology, rather than one implementation's
internal representation. Deep chains exercise the iterative construction.
"""
import collections
import copy
import itertools
import random
import unittest
from unittest.mock import patch

from x3dray import centrality as c, structure


def graph(n, edges):
    adj = c.make_graph(edges)
    for v in range(n):
        adj.setdefault(v, {})
    return adj


class PassageIndexTests(unittest.TestCase):
    def assert_pair_oracle(self, adj):
        index = c.BlockCutIndex(adj)
        self.assertEqual(index.cut_vertices, c.articulation_points(adj))
        for a, b in itertools.combinations(adj, 2):
            self.assertEqual(index.pair(a, b), set(c.passage_points(adj, [a], [b])), (adj, a, b))
            self.assertEqual(index.pair(b, a), index.pair(a, b))

    def test_all_simple_graphs_through_five_vertices(self):
        for n in range(6):
            possible = list(itertools.combinations(range(n), 2))
            for mask in range(1 << len(possible)):
                adj = graph(n, [edge for i, edge in enumerate(possible) if mask & (1 << i)])
                self.assert_pair_oracle(adj)

    def test_standard_topologies(self):
        fixtures = [
            graph(7, [(i, i + 1) for i in range(6)]),            # path
            graph(7, [(0, i) for i in range(1, 7)]),            # star
            graph(7, [(i, (i + 1) % 7) for i in range(7)]),     # cycle
            graph(7, [(0, 1), (1, 2), (2, 0), (2, 3), (4, 5)]),
            graph(7, []),                                      # isolated vertices
        ]
        for adj in fixtures:
            self.assert_pair_oracle(adj)

    def test_group_semantics_overlap_unknown_and_disconnected(self):
        adj = graph(9, [(0, 1), (1, 2), (2, 3), (3, 4), (5, 6)])
        index = c.BlockCutIndex(adj)
        cases = [([0, 1], [3, 4]), ([0, 3], [3, 4]), ([0, 5], [4, 6]),
                 ([0, 99], [4, 6]), ([], [4]), ([2], [2]), ([7], [8])]
        for a, b in cases:
            self.assertEqual(index.passage_points(a, b), c.passage_points(adj, a, b), (a, b))
        self.assertFalse(index.connected(0, 99))
        self.assertTrue(index.connected(7, 7))
        self.assertEqual(index.pair(0, 99), set())

    def test_random_groups_match_vertex_removal(self):
        rnd = random.Random(931)
        for _ in range(70):
            n = rnd.randint(2, 20)
            adj = graph(n, [(a, b, rnd.randint(1, 8)) for a, b in itertools.combinations(range(n), 2)
                            if rnd.random() < 0.18])
            a, b = rnd.sample(range(n), rnd.randint(1, n)), rnd.sample(range(n), rnd.randint(1, n))
            self.assertEqual(c.BlockCutIndex(adj).passage_points(a, b), c.passage_points(adj, a, b))

    def test_deep_chain_does_not_depend_on_recursion_limit(self):
        n = 5000
        index = c.BlockCutIndex(graph(n, [(i, i + 1) for i in range(n - 1)]))
        self.assertEqual(index.pair(0, n - 1), set(range(1, n - 1)))
        self.assertEqual(index.pair(2500, 2501), set())

    def test_self_loop_does_not_create_a_passage(self):
        adj = {"a": {"a": 1, "b": 2}, "b": {"a": 2}}
        self.assertEqual(c.BlockCutIndex(adj).pair("a", "b"), set())

    def test_asymmetric_adjacency_is_explicitly_rejected(self):
        with self.assertRaisesRegex(ValueError, "undirected"):
            c.BlockCutIndex({"a": {"b": 1}, "b": {}})

    def test_bipartite_output_matches_removal_oracle(self):
        public = {("session:s1", "repo:a"): ["one"], ("session:s1", "repo:b"): ["two"],
                  ("session:s2", "repo:b"): ["three"], ("session:s2", "repo:c"): ["four"]}
        private = collections.Counter({("session:s3", "repo:c"): 2, ("session:s3", "repo:private"): 1})
        got = structure._bipartite(public, private, ["a", "b", "c", "private", "unreached"])

        class RemovalOracle:
            def __init__(self, adj):
                self.adj = adj
                self.cut_vertices = c.articulation_points(adj)

            def pair(self, a, b):
                return set(c.passage_points(self.adj, [a], [b]))

        with patch.object(c, "BlockCutIndex", RemovalOracle):
            want = structure._bipartite(public, private, ["a", "b", "c", "private", "unreached"])
        self.assertEqual(got, want)

    def test_graph_verifier_remains_independent_and_detects_mutation(self):
        public = {("session:s1", "repo:a"): ["one"], ("session:s1", "repo:b"): ["two"],
                  ("session:s2", "repo:b"): ["three"], ("session:s2", "repo:c"): ["four"]}
        adj, summary = structure._bipartite(public, collections.Counter(), ["a", "b", "c", "unreached"])
        with patch.object(c, "BlockCutIndex", side_effect=AssertionError("verifier must not use production index")):
            self.assertEqual(structure._graph_checks(summary, adj), [])
            changed = copy.deepcopy(summary)
            changed["passage_points"]["a -> c"] = []
            self.assertIn("passage points", structure._graph_checks(changed, adj))

    def test_cochange_single_pass_iterables_keep_singletons(self):
        commits = [["only"], ["a", "b"], ["large%d" % i for i in range(16)], []]
        want = c.cochange_graph(commits)
        self.assertEqual(c.cochange_graph(iter(commits)), want)
        self.assertEqual(c.cochange_graph((iter(paths) for paths in commits)), want)
        self.assertEqual(want[0]["only"], {})
        self.assertNotIn("large0", want[0])
        self.assertEqual(want[1], 1)


if __name__ == "__main__":
    unittest.main()
