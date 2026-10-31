"""Betweenness, cut vertices and passage points (x3dray/centrality.py) against brute-force oracles."""

import itertools
import random
import unittest
from fractions import Fraction

from x3dray import centrality as c


def brute_betweenness(adj, dist_of=lambda w: 1):
    """Oracle: Floyd-Warshall with exact path counts, then the textbook sum over pairs."""
    nodes = list(adj)
    INF = None
    d = {(u, v): (0 if u == v else INF) for u in nodes for v in nodes}
    n = {(u, v): (1 if u == v else 0) for u in nodes for v in nodes}
    for u in nodes:
        for v, w in adj[u].items():
            d[(u, v)] = Fraction(dist_of(w))
            n[(u, v)] = 1
    for k in nodes:
        for i in nodes:
            for j in nodes:
                if i == j or d[(i, k)] is INF or d[(k, j)] is INF or k in (i, j):
                    continue
                alt = d[(i, k)] + d[(k, j)]
                if d[(i, j)] is INF or alt < d[(i, j)]:
                    d[(i, j)], n[(i, j)] = alt, n[(i, k)] * n[(k, j)]
                elif alt == d[(i, j)]:
                    n[(i, j)] += n[(i, k)] * n[(k, j)]
    bc = {v: 0.0 for v in nodes}
    for s, t in itertools.combinations(nodes, 2):
        if d[(s, t)] is INF:
            continue
        for v in nodes:
            if v in (s, t) or d[(s, v)] is INF or d[(v, t)] is INF:
                continue
            if d[(s, v)] + d[(v, t)] == d[(s, t)]:
                bc[v] += n[(s, v)] * n[(v, t)] / n[(s, t)]
    return bc


def random_graph(rnd, n, p):
    edges = [("n%d" % i, "n%d" % j, rnd.randint(1, 4)) for i in range(n) for j in range(i + 1, n) if rnd.random() < p]
    adj = c.make_graph(edges)
    for i in range(n):
        adj.setdefault("n%d" % i, {})
    return adj


class BetweennessTests(unittest.TestCase):
    def test_path_of_five(self):
        adj = c.make_graph([(0, 1), (1, 2), (2, 3), (3, 4)])
        self.assertEqual(c.betweenness(adj), {0: 0, 1: 3, 2: 4, 3: 3, 4: 0})

    def test_star(self):
        k = 6
        adj = c.make_graph([("hub", i) for i in range(k)])
        bc = c.betweenness(adj)
        self.assertEqual(bc["hub"], k * (k - 1) / 2)
        self.assertTrue(all(bc[i] == 0 for i in range(k)))

    def test_four_cycle_splits_ties(self):
        adj = c.make_graph([(0, 1), (1, 2), (2, 3), (3, 0)])
        self.assertEqual(c.betweenness(adj), {0: 0.5, 1: 0.5, 2: 0.5, 3: 0.5})

    def test_normalized_star_hub_is_one(self):
        adj = c.make_graph([("hub", i) for i in range(5)])
        self.assertAlmostEqual(c.betweenness(adj, normalized=True)["hub"], 1.0)

    def test_matches_oracle_unweighted_random(self):
        rnd = random.Random(7)
        for _ in range(25):
            adj = random_graph(rnd, rnd.randint(2, 11), 0.3)
            got, want = c.betweenness(adj), brute_betweenness(adj)
            for v in adj:
                self.assertAlmostEqual(got[v], want[v], places=9, msg=str(adj))

    def test_matches_oracle_weighted_random(self):
        rnd = random.Random(11)
        for _ in range(25):
            adj = random_graph(rnd, rnd.randint(2, 10), 0.4)
            dist = c.to_distance(adj)
            got = c.betweenness(dist, weighted=True)
            want = brute_betweenness(dist, dist_of=lambda w: w)
            for v in adj:
                self.assertAlmostEqual(got[v], want[v], places=9, msg=str(adj))

    def test_weights_change_the_answer(self):
        # a-b-d is two hops, a-c-d too; make the b route much closer so c carries nothing
        adj = c.make_graph([("a", "b", 5), ("b", "d", 5), ("a", "c", 1), ("c", "d", 1)])
        dist = c.to_distance(adj)
        w = c.betweenness(dist, weighted=True)
        self.assertGreater(w["b"], w["c"])
        u = c.betweenness(adj)
        self.assertEqual(u["b"], u["c"])

    def test_directed_chain(self):
        adj = c.make_graph([(0, 1), (1, 2)], directed=True)
        self.assertEqual(c.betweenness(adj, directed=True), {0: 0, 1: 1, 2: 0})


class StructureTests(unittest.TestCase):
    def test_articulation_points_match_removal(self):
        rnd = random.Random(3)
        for _ in range(40):
            adj = random_graph(rnd, rnd.randint(2, 12), 0.22)
            base = len(c.components(adj))
            want = {v for v in adj if len(c.components(adj, {v})) > base - (1 if not adj[v] else 0) and adj[v]}
            self.assertEqual(c.articulation_points(adj), want, str(adj))

    def test_bridge_node_is_a_passage_point_but_a_cycle_is_not(self):
        two_rooms = c.make_graph([("a1", "a2"), ("a2", "door"), ("door", "b1"), ("b1", "b2")])
        pp = c.passage_points(two_rooms, ["a1", "a2"], ["b1", "b2"])
        self.assertEqual(set(pp), {"door"})
        self.assertEqual(pp["door"]["pairs_cut"], 4)
        ring = c.make_graph([("a", "x"), ("x", "b"), ("a", "y"), ("y", "b")])
        self.assertEqual(c.passage_points(ring, ["a"], ["b"]), {})
        # x and y still carry betweenness in the ring but neither is obligatory
        bc = c.betweenness(ring)
        self.assertGreater(bc["x"], 0)

    def test_cochange_skips_mass_edits_and_counts_them(self):
        commits = [["a", "b"], ["a", "b", "c"], ["a"] + ["f%d" % i for i in range(20)]]
        adj, skipped = c.cochange_graph(commits, max_files=15)
        self.assertEqual(skipped, 1)
        self.assertEqual(adj["a"]["b"], 2)
        self.assertNotIn("f0", adj)


if __name__ == "__main__":
    unittest.main()
