"""Formal concept analysis (Wille), standard library only. Used by the concepts stage (x3dray/structure.py).

A formal context is objects x attributes. A concept is a pair (extent, intent): the extent is
every object that has all attributes in the intent, and the intent is every attribute shared
by all objects in the extent. Concepts ordered by extent inclusion form a lattice (a Hasse
diagram with a top and a bottom). Everything here is deterministic and needs no model.
"""
from __future__ import annotations


class TooManyConcepts(Exception):
    pass


def normalize(ctx):
    """dict object -> iterable of attributes, to dict object -> frozenset."""
    return {g: frozenset(a) for g, a in ctx.items()}


def concepts(ctx, max_concepts=50000):
    """All formal concepts as a list of (extent, intent) frozensets, sorted for stable output.

    Every intent is an intersection of object intents (or the full attribute set, the empty
    intersection), so we close that family under intersection and read each extent off it.
    """
    ctx = normalize(ctx)
    attrs = frozenset().union(*ctx.values()) if ctx else frozenset()
    intents = {attrs}
    for a in ctx.values():
        intents |= {s & a for s in intents}
        if len(intents) > max_concepts:
            raise TooManyConcepts(
                "more than %d concepts: drop rare attributes (prune_attributes) or filter objects" % max_concepts)
    out = []
    for s in intents:
        ext = frozenset(g for g, a in ctx.items() if s <= a)
        out.append((ext, s))
    out.sort(key=lambda c: (-len(c[0]), sorted(c[1]), sorted(c[0])))
    return out


def prune_attributes(ctx, min_objects=2):
    """Drop attributes held by fewer than min_objects objects. Real contexts explode otherwise."""
    ctx = normalize(ctx)
    count = {}
    for a in ctx.values():
        for m in a:
            count[m] = count.get(m, 0) + 1
    keep = {m for m, n in count.items() if n >= min_objects}
    return {g: a & keep for g, a in ctx.items()}


def iceberg(cs, min_support):
    """Keep concepts whose extent has at least min_support objects (the iceberg lattice)."""
    return [c for c in cs if len(c[0]) >= min_support]


def covers(cs):
    """Hasse diagram: list of (lower_index, upper_index) over indices into cs.

    Upper means a strictly larger extent with nothing in between.
    """
    order = sorted(range(len(cs)), key=lambda i: len(cs[i][0]))
    edges = []
    for pos, i in enumerate(order):
        ext = cs[i][0]
        chosen = []
        for j in order[pos + 1:]:
            e2 = cs[j][0]
            if len(e2) > len(ext) and ext < e2 and not any(cs[k][0] < e2 for k in chosen):
                chosen.append(j)
        edges.extend((i, j) for j in chosen)
    return edges


def labels(ctx, cs):
    """Reduced labelling: each attribute at the highest concept that has it, each object at
    the lowest concept that contains it. Returns (attr_at, obj_at): dicts to concept indices."""
    ctx = normalize(ctx)
    by_ext = {c[0]: i for i, c in enumerate(cs)}
    by_int = {c[1]: i for i, c in enumerate(cs)}
    attr_at = {}
    for m in frozenset().union(*ctx.values()) if ctx else []:
        ext = frozenset(g for g, a in ctx.items() if m in a)
        attr_at[m] = by_ext[ext]
    obj_at = {g: by_int[a] for g, a in ctx.items()}
    return attr_at, obj_at


def levels(cs, edges):
    """Level of each concept = longest chain down from the top (0 = top). Bottom-heavy lattices
    read well with level on Y."""
    n = len(cs)
    ups = {i: [] for i in range(n)}
    for lo, up in edges:
        ups[lo].append(up)
    memo = {}

    def depth(i):
        if i in memo:
            return memo[i]
        memo[i] = 0 if not ups[i] else 1 + max(depth(u) for u in ups[i])
        return memo[i]

    return [depth(i) for i in range(n)]


def layout(cs, edges, sweeps=6):
    """Level plus an x in [0,1] from barycentre sweeps. The scene compiler maps (x, level) to 3D
    and may use z for a third dimension such as time or hash."""
    lv = levels(cs, edges)
    n = len(cs)
    nbrs = {i: [] for i in range(n)}
    for lo, up in edges:
        nbrs[lo].append(up)
        nbrs[up].append(lo)
    rows = {}
    for i in range(n):
        rows.setdefault(lv[i], []).append(i)
    pos = {}
    for l, r in rows.items():
        r.sort(key=lambda i: (sorted(cs[i][1]), sorted(cs[i][0])))
        for k, i in enumerate(r):
            pos[i] = k / max(1, len(r) - 1) if len(r) > 1 else 0.5
    for s in range(sweeps):
        for l in sorted(rows) if s % 2 == 0 else sorted(rows, reverse=True):
            r = rows[l]
            bary = {i: (sum(pos[j] for j in nbrs[i]) / len(nbrs[i])) if nbrs[i] else pos[i] for i in r}
            r.sort(key=lambda i: (bary[i], i))
            for k, i in enumerate(r):
                pos[i] = k / max(1, len(r) - 1) if len(r) > 1 else 0.5
    return [{"level": lv[i], "x": round(pos[i], 4)} for i in range(n)]


def implications(ctx, min_support=2, min_confidence=1.0):
    """Single-attribute rules a => b with support (objects having a) and confidence
    (share of those that also have b). min_confidence=1.0 gives exact implications. Below 1.0
    each rule lists its exceptions (objects with a but not b), so the number is checkable.
    Attributes that always occur together are returned separately as equivalences."""
    ctx = normalize(ctx)
    ext = {}
    for g, a in ctx.items():
        for m in a:
            ext.setdefault(m, set()).add(g)
    rules, eq = [], []
    ms = sorted(ext)
    for a in ms:
        if len(ext[a]) < min_support:
            continue
        for b in ms:
            if a == b:
                continue
            both = ext[a] & ext[b]
            conf = len(both) / len(ext[a])
            if conf < min_confidence:
                continue
            if ext[a] == ext[b]:
                if a < b:
                    eq.append({"attrs": [a, b], "support": len(ext[a])})
                continue
            rules.append({"if": a, "then": b, "support": len(ext[a]), "of": len(ext[b]),
                          "both": len(both), "confidence": round(conf, 4),
                          "exceptions": sorted(ext[a] - ext[b])})
    rules.sort(key=lambda d: (-d["confidence"], -d["support"], d["if"], d["then"]))
    return rules, eq


def analyse(ctx, min_support=1, max_concepts=50000, min_confidence=1.0):
    """One call for the pipeline: concepts, Hasse edges, labels, layout, implications.
    min_support > 1 keeps only the iceberg; labels then point at surviving concepts only."""
    ctx = normalize(ctx)
    full = concepts(ctx, max_concepts)
    cs = iceberg(full, min_support)
    ed = covers(cs)
    attr_full, obj_full = labels(ctx, full)
    kept = set(cs)
    keep = {c: k for k, c in enumerate(cs)}
    attr_at = {m: keep[full[i]] for m, i in attr_full.items() if full[i] in kept}
    obj_at = {g: keep[full[i]] for g, i in obj_full.items() if full[i] in kept}
    lay = layout(cs, ed)
    imps, eq = implications(ctx, max(2, min_support), min_confidence)
    return {
        "concepts": [{"extent": sorted(e), "intent": sorted(i), "support": len(e), **lay[k]}
                     for k, (e, i) in enumerate(cs)],
        "covers": ed,
        "attribute_concept": attr_at,
        "object_concept": obj_at,
        "implications": imps,
        "equivalences": eq,
    }
