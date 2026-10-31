"""Betweenness centrality, cut vertices and obligatory passage points, standard library only. Used by the bridges
stage (x3dray/structure.py).

Betweenness (Brandes 2001): for each node, the share of shortest paths between other pairs
that pass through it. It says where traffic concentrates. An obligatory passage point (Callon,
actor-network theory) is stricter: a node every path between two groups must cross. Removing
it disconnects them. The two answer different questions, so this module offers both.
"""
from __future__ import annotations

import heapq
from collections import deque
from fractions import Fraction


def make_graph(edges, directed=False):
    """edges: iterable of (u, v) or (u, v, w). Returns adjacency dict node -> {nbr: w}.
    Repeated edges add their weights, so w is a count when you feed (u, v) pairs."""
    adj = {}
    for e in edges:
        u, v = e[0], e[1]
        w = e[2] if len(e) > 2 else 1
        adj.setdefault(u, {})
        adj.setdefault(v, {})
        if u == v:
            continue
        adj[u][v] = adj[u].get(v, 0) + w
        if not directed:
            adj[v][u] = adj[v].get(u, 0) + w
    return adj


def cochange_graph(commits, max_files=15):
    """File co-change graph. commits: iterable of file lists. Edge weight = number of commits
    that touched both files. Commits touching more than max_files files are skipped: a mass edit
    makes a clique that says nothing about coupling (the count skipped is returned)."""
    edges, skipped = [], 0
    for files in commits:
        fs = sorted(set(files))
        if len(fs) > max_files:
            skipped += 1
            continue
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                edges.append((fs[i], fs[j], 1))
    adj = make_graph(edges)
    for f in {x for files in commits for x in files if len(set(files)) <= max_files}:
        adj.setdefault(f, {})
    return adj, skipped


def to_distance(adj):
    """Turn co-occurrence counts into distances: more co-changes means closer (1/count)."""
    return {u: {v: Fraction(1, w) for v, w in nb.items()} for u, nb in adj.items()}


def betweenness(adj, weighted=False, directed=False, normalized=False):
    """Brandes. weighted=True treats edge values as distances (use to_distance for counts).
    Distances are compared exactly (Fractions) so ties between equal-length paths are real ties."""
    nodes = list(adj)
    bc = {v: Fraction(0) for v in nodes}
    for s in nodes:
        stack, pred = [], {v: [] for v in nodes}
        sigma = {v: 0 for v in nodes}
        sigma[s] = 1
        if not weighted:
            dist = {s: 0}
            q = deque([s])
            while q:
                v = q.popleft()
                stack.append(v)
                for w in adj[v]:
                    if w not in dist:
                        dist[w] = dist[v] + 1
                        q.append(w)
                    if dist[w] == dist[v] + 1:
                        sigma[w] += sigma[v]
                        pred[w].append(v)
        else:
            dist, seen = {}, {s: Fraction(0)}
            heap, counter = [(Fraction(0), 0, s)], 1
            while heap:
                d, _, v = heapq.heappop(heap)
                if v in dist:
                    continue
                dist[v] = d
                stack.append(v)
                for w, wt in adj[v].items():
                    nd = d + Fraction(wt)
                    if w not in dist and (w not in seen or nd < seen[w]):
                        seen[w] = nd
                        heapq.heappush(heap, (nd, counter, w))
                        counter += 1
            # predecessors and path counts from the final exact distances
            for v in stack:
                for w, wt in adj[v].items():
                    if w != s and w in dist and dist[w] == dist[v] + Fraction(wt):
                        pred[w].append(v)
            for v in stack:
                for u in pred[v]:
                    sigma[v] += sigma[u]
        delta = {v: Fraction(0) for v in nodes}
        for w in reversed(stack):
            for v in pred[w]:
                delta[v] += Fraction(sigma[v], sigma[w]) * (1 + delta[w])
            if w != s:
                bc[w] += delta[w]
    n = len(nodes)
    scale = Fraction(1) if directed else Fraction(1, 2)
    if normalized and n > 2:
        scale *= Fraction(1, (n - 1) * (n - 2)) * (1 if directed else 2)
    return {v: float(b * scale) for v, b in bc.items()}


def components(adj, removed=None):
    removed = removed or set()
    seen, comps = set(), []
    for s in adj:
        if s in seen or s in removed:
            continue
        comp, q = {s}, deque([s])
        seen.add(s)
        while q:
            v = q.popleft()
            for w in adj[v]:
                if w not in seen and w not in removed:
                    seen.add(w)
                    comp.add(w)
                    q.append(w)
        comps.append(comp)
    return comps


def articulation_points(adj):
    """Nodes whose removal increases the number of connected components (undirected, iterative
    Tarjan). Plain cut vertices: the structural single points of failure."""
    disc, low, parent, ap = {}, {}, {}, set()
    t = 0
    for root in adj:
        if root in disc:
            continue
        disc[root] = low[root] = t
        t += 1
        parent[root] = None
        children = 0
        stack = [(root, iter(adj[root]))]
        while stack:
            v, it = stack[-1]
            adv = False
            for w in it:
                if w not in disc:
                    parent[w] = v
                    disc[w] = low[w] = t
                    t += 1
                    if v == root:
                        children += 1
                    stack.append((w, iter(adj[w])))
                    adv = True
                    break
                elif w != parent[v]:
                    low[v] = min(low[v], disc[w])
            if adv:
                continue
            stack.pop()
            if stack:
                p = stack[-1][0]
                low[p] = min(low[p], low[v])
                if p != root and low[v] >= disc[p]:
                    ap.add(p)
        if children > 1:
            ap.add(root)
    return ap


def passage_points(adj, group_a, group_b):
    """Obligatory passage points between two groups: nodes outside both groups whose removal
    cuts some a in A off from some b in B that were connected before. Callon's definition."""
    a_set, b_set = set(group_a), set(group_b)
    base = components(adj)
    comp_of = {v: i for i, c in enumerate(base) for v in c}
    pairs = [(a, b) for a in a_set for b in b_set if a in comp_of and b in comp_of and comp_of[a] == comp_of[b]]
    out = {}
    for v in adj:
        if v in a_set or v in b_set:
            continue
        cut = components(adj, {v})
        cid = {x: i for i, c in enumerate(cut) for x in c}
        lost = [(a, b) for a, b in pairs if cid.get(a) != cid.get(b)]
        if lost:
            out[v] = {"pairs_cut": len(lost), "of_pairs": len(pairs)}
    return out
