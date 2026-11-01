"""Concepts and bridges: two readings of the family's own commits that need no model.

concepts (formal concept analysis, x3dray/fca.py)
  files x the kinds of author that touched them, read as a lattice; repos x the kinds of author each has seen;
  commits x the areas (repo/top-level folder) they touch, read as rules with their exceptions named.
bridges (betweenness, cut vertices and passage points, x3dray/centrality.py)
  the file co-change graph under two distances; the Claude-session x repo graph; the author x repo graph.

Rules every output follows:
- every count names the commits behind it (12-hex shas), and equals the length of that list;
- a private repo enters as counts only: no sha, no path, never itemised;
- whatever was set aside is counted under `excluded` (merges, commits without paths, mass edits, private
  commits), because unknown is not zero. The top-level `excluded` is the scope of the file lattice, the area rules
  and the file co-change graph; the repo lattice, the session graph and the author graph read other rows and say so
  in their own `reads` and `excluded`;
- a commit touching more than MAX_FILES files is a mass edit (a regeneration, a vendored drop, a sweep): it is set
  aside from the file contexts, where it would make every file it touched look coupled to every other;
- the session graph sees only commits that carry a Claude-Session trailer. Two repos one session committed to are
  linked directly; two repos joined only through a chain of sessions and other repos are linked through others; two
  repos no chain joins have "no recorded session link", never "no link" (they may still be tied in other ways).
Same dataset in, same output out. verify() re-checks the witnesses and the stage fails if any check fails.
"""

from __future__ import annotations

import collections
import hashlib
import heapq
import os
import re
from fractions import Fraction

from . import centrality, fca

MAX_FILES = 15            # files per commit; above this a commit is a mass edit
HIGH_CHURN_SHARE = 0.10   # a file touched by at least this share of its repo's usable commits ...
HIGH_CHURN_MIN = 5        # ... and by at least this many is high churn, set aside in the Jaccard variant
MIN_AREA_COMMITS = 3      # an area touched by fewer commits is dropped from the rules
MIN_RULE_CONFIDENCE = 0.8
TOP = 15
LARGEST = 8               # the mass edits named one by one
CLASSES = ["you", "ai", "bot", "other", "upstream"]
USABLE = f"usable commits: public, not merges, with paths, at most {MAX_FILES} files"
PKG = os.path.dirname(os.path.abspath(__file__))


def _code(*names: str) -> dict:
    out = {}
    for n in names:
        with open(os.path.join(PKG, n), "rb") as fh:
            out[n] = hashlib.sha256(fh.read()).hexdigest()[:16]      # bare: a private repo named x3dray must not own them
    return out


def commit_rows(ds: dict) -> list[dict]:
    """The family's own commits (no upstream history) as plain rows. Private repos keep no sha and no paths."""
    rows = []
    for r in ds.get("repos", []):
        if not r.get("present"):
            continue
        priv = r.get("visibility") == "private"
        for c in r.get("commits", []):
            if c.get("upstream"):
                continue
            rows.append({"repo": r["name"], "private": priv, "sha": None if priv else c.get("sha"), "cls": c["cls"], "who": c["who"],
                         "merge": bool(c.get("merge")), "paths": None if priv else c.get("paths"), "sessions": list(dict.fromkeys(c.get("sessions") or [])),
                         "t": c["t"]})
    return _ordered(rows)


def usable(rows: list[dict], max_files: int = MAX_FILES) -> tuple[list[dict], dict, list[dict]]:
    """Public, non-merge commits with at most max_files paths. Returns (rows, excluded counts, the mass edits)."""
    keep, ex, mass = [], collections.Counter(), []
    for r in rows:
        if r["private"]:
            ex["private commits (counted, never itemised)"] += 1
        elif r["merge"]:
            ex["merges (git lists no paths for them)"] += 1
        elif not r["paths"]:
            ex["public commits with no paths"] += 1
        elif len(r["paths"]) > max_files:
            ex[f"mass edits (more than {max_files} files) set aside"] += 1
            mass.append(r)
        else:
            keep.append(r)
    return keep, dict(sorted(ex.items())), mass


def _ranked(counts: dict, key=lambda k: k) -> dict:
    """A count map as the stage writes it: the largest first, ties in key order (repo names; kinds in class order)."""
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], key(kv[0]))))


def _mass_summary(mass: list[dict], used: list[dict]) -> dict:
    touches = sum(len(r["paths"]) for r in mass)
    kept = sum(len(r["paths"]) for r in used)
    top = sorted(mass, key=lambda r: (-len(r["paths"]), r["sha"]))[:LARGEST]
    return {"commits": len(mass), "file_touches": touches, "of_file_touches": touches + kept,
            "by_repo": _ranked(collections.Counter(r["repo"] for r in mass)),
            "by_class": _ranked(collections.Counter(r["cls"] for r in mass), _cls_key),
            "largest": [{"repo": r["repo"], "sha": r["sha"], "files": len(r["paths"]), "class": r["cls"]} for r in top],
            "shas": sorted(r["sha"] for r in mass)}


def area(repo: str, path: str) -> str:
    return f"{repo}/{path.split('/', 1)[0] if '/' in path else '.'}"


def _cls_key(c: str) -> tuple:
    return (CLASSES.index(c) if c in CLASSES else 99, c)


def _cls_sorted(xs) -> list[str]:
    return sorted(xs, key=_cls_key)


# ---------------------------------------------------------------- concepts
def _ordered(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda x: (x["t"], x["repo"], x["sha"] or ""))


def concepts(rows: list[dict]) -> dict:
    rows = _ordered(rows)
    used, ex, mass = usable(rows)

    # 1. files x the kinds of author that touched them
    ctx = collections.defaultdict(set)
    wit = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in used:
        for p in r["paths"]:
            o = f"{r['repo']}:{p}"
            ctx[o].add(r["cls"])
            wit[o][r["cls"]].append(r["sha"])
    a = fca.analyse(dict(ctx), min_support=1)
    exact = collections.Counter(a["object_concept"].values())
    n_commits = {o: len({s for v in w.values() for s in v}) for o, w in wit.items()}
    cs = []
    for k, c in enumerate(a["concepts"]):
        ext = c["extent"]
        top = sorted(ext, key=lambda o: (-n_commits[o], o))[:6]
        cs.append({"i": k, "intent": _cls_sorted(c["intent"]), "support": c["support"], "exact": exact.get(k, 0),
                   "level": c["level"], "x": c["x"], "by_repo": _ranked(collections.Counter(o.split(":", 1)[0] for o in ext)),
                   "top_files": [{"id": o, "commits": n_commits[o]} for o in top]})
    files = {o: {"by": {cl: sorted(set(v)) for cl, v in sorted(w.items(), key=lambda kv: _cls_key(kv[0]))}, "concept": a["object_concept"][o]}
             for o, w in sorted(wit.items())}
    files_out = {
        "reads": USABLE,
        "objects": f"every file touched by a usable commit (public, not a merge, at most {MAX_FILES} files), named repo:path",
        "attributes": "the identity class of the commit's author: you (HagopJay), ai (Claude), bot, other, upstream",
        "n_files": len(ctx), "n_commits": len(used), "n_concepts": len(cs),
        "concepts": cs, "covers": a["covers"], "files": files,
        "note": "A concept is a set of kinds of author with every file that all of them touched. 'support' counts the files "
                "touched by at least those kinds; 'exact' counts the files touched by exactly those kinds and no other.",
    }

    # 2. repos x the kinds of author each has seen (private repos as counts)
    rctx, rcount, first = collections.defaultdict(set), collections.defaultdict(collections.Counter), collections.defaultdict(dict)
    privs = set()
    for r in rows:
        if r["merge"]:
            continue
        rctx[r["repo"]].add(r["cls"])
        rcount[r["repo"]][r["cls"]] += 1
        if r["private"]:
            privs.add(r["repo"])
        elif r["sha"] and r["cls"] not in first[r["repo"]]:
            first[r["repo"]][r["cls"]] = r["sha"]
    ra = fca.analyse(dict(rctx), min_support=1)
    rexact = collections.defaultdict(list)
    for o, k in ra["object_concept"].items():
        rexact[k].append(o)
    rcs = [{"i": k, "intent": _cls_sorted(c["intent"]), "support": c["support"], "repos": sorted(c["extent"]),
            "exact": sorted(rexact.get(k, [])), "level": c["level"], "x": c["x"]} for k, c in enumerate(ra["concepts"])]
    n_merges = sum(1 for r in rows if r["merge"])
    repos_out = {
        "reads": "every own commit that is not a merge: mass edits and commits with no paths included, private commits as counts",
        "excluded": {"merges": n_merges} if n_merges else {},
        "objects": "repos with at least one own commit that is not a merge", "attributes": "the identity classes of their authors",
        "concepts": rcs, "covers": ra["covers"],
        "repos": {n: {"commits_by_class": dict(sorted(rcount[n].items(), key=lambda kv: _cls_key(kv[0]))), "private": n in privs,
                      "first_commit_by_class": None if n in privs else dict(sorted(first[n].items(), key=lambda kv: _cls_key(kv[0])))}
                  for n in sorted(rctx)},
        "note": "Private repos enter with counts only. first_commit_by_class names one commit per class as its witness.",
    }

    # 3. commits x areas: which areas move together, as rules with their exceptions
    actx = {(r["repo"], r["sha"]): {area(r["repo"], p) for p in r["paths"]} for r in used}   # keyed by repo too: two repos can share a sha
    pruned = fca.prune_attributes(actx, min_objects=MIN_AREA_COMMITS)
    before = {m for v in actx.values() for m in v}
    after = {m for v in pruned.values() for m in v}
    pruned = {k: v for k, v in pruned.items() if v}
    rules, eq = fca.implications(pruned, min_support=MIN_AREA_COMMITS, min_confidence=MIN_RULE_CONFIDENCE)
    for rl in rules:
        rl["exceptions"] = sorted(sha for _, sha in rl["exceptions"])    # a rule's areas sit in one repo, so the sha is enough
    areas_out = {
        "reads": USABLE,
        "objects": "usable commits", "attributes": f"the areas (repo/top-level folder) they touch; areas with fewer than {MIN_AREA_COMMITS} commits dropped",
        "areas": len(after), "areas_dropped": len(before - after), "commits": len(pruned), "commits_only_in_dropped_areas": len(actx) - len(pruned),
        "rules": rules, "equivalences": eq,
        "note": "A rule 'a => b' reads: of the commits that touched a, this share also touched b. 'exceptions' are the commits that "
                f"touched a but not b. Only rules with confidence at least {MIN_RULE_CONFIDENCE} are kept.",
    }
    return {
        "stage": "concepts",
        "method": {"formal concept analysis": "every closed set of attributes and the objects that share it (Wille), ordered as a lattice",
                   "params": {"max_files_per_commit": MAX_FILES, "min_area_commits": MIN_AREA_COMMITS, "min_rule_confidence": MIN_RULE_CONFIDENCE},
                   "code_sha256": _code("structure.py", "fca.py")},
        "rows": len(rows), "excluded": ex, "excluded_applies_to": "files_by_author_class and areas (repos_by_author_class has its own)",
        "mass_edits": _mass_summary(mass, used),
        "files_by_author_class": files_out, "repos_by_author_class": repos_out, "areas": areas_out,
    }


# ---------------------------------------------------------------- bridges
def _norm_share(bc: float, n: int) -> float:
    """Betweenness as a share of the pairs of other nodes in its component (0 to 1)."""
    pairs = (n - 1) * (n - 2) / 2
    return round(bc / pairs, 4) if pairs > 0 else 0.0


def _largest_by_repo(comps) -> dict:
    """Per repo, its largest cluster of files. A commit changes files of one repo, so a cluster never spans two."""
    big: dict[str, int] = {}
    for c in comps:
        r = min(c).split(":", 1)[0]
        big[r] = max(big.get(r, 0), len(c))
    return dict(sorted(big.items()))


def _graph_summary(adj: dict, dist: dict, node_shas: dict, edge_shas: dict, label: str) -> dict:
    comps = centrality.components(adj)
    comp_size = {v: len(c) for c in comps for v in c}
    bc = centrality.betweenness(dist, weighted=True)
    cuts = centrality.articulation_points(adj)
    ranked = sorted(((v, b) for v, b in bc.items() if b > 0), key=lambda t: (-t[1], t[0]))
    per_file = {}
    for v, b in ranked:
        per_file[v] = {"betweenness": round(b, 4), "share": _norm_share(b, comp_size[v]), "component": comp_size[v], "degree": len(adj[v]),
                       "cut_vertex": v in cuts, "commits": len(node_shas[v])}
    for v in sorted(cuts):
        per_file.setdefault(v, {"betweenness": 0.0, "share": 0.0, "component": comp_size[v], "degree": len(adj[v]), "cut_vertex": True,
                                "commits": len(node_shas[v])})
    return {
        "distance": label, "n_nodes": len(adj), "n_edges": sum(len(x) for x in adj.values()) // 2, "n_components": len(comps),
        "largest_component": max((len(c) for c in comps), default=0),
        "top": [{"id": v, **per_file[v], "shas": sorted(node_shas[v])} for v, _ in ranked[:TOP]],
        "top_edges": [{"a": u, "b": w, "weight": len(sh), "shas": sorted(sh)}
                      for (u, w), sh in sorted(edge_shas.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:TOP]],
        "cut_vertices": len(cuts), "cut_vertices_by_repo": dict(sorted(collections.Counter(v.split(":", 1)[0] for v in cuts).items())),
        "largest_cluster_by_repo": _largest_by_repo(comps),
        "per_file": per_file,
    }


def _bipartite(pairs_public: dict, pairs_private: collections.Counter, repos_all=()) -> tuple[dict, dict]:
    """repos_all: every repo with own commits, so a repo no session reached still counts in the pairs with no recorded link."""
    edges = [(a, b, len(s)) for (a, b), s in pairs_public.items()] + [(a, b, n) for (a, b), n in pairs_private.items()]
    adj = centrality.make_graph(edges)
    for n in repos_all:
        adj.setdefault("repo:" + n, {})
    repos = sorted(n for n in adj if n.startswith("repo:"))
    comps = centrality.components(adj)
    comp_of = {v: i for i, c in enumerate(comps) for v in c}
    pp, unlinked = {}, []
    for i, a in enumerate(repos):
        for b in repos[i + 1:]:
            if comp_of[a] != comp_of[b]:
                unlinked.append([a[5:], b[5:]])
                continue
            p = centrality.passage_points(adj, [a], [b])
            if p:
                pp[f"{a[5:]} -> {b[5:]}"] = sorted(x.split(":", 1)[1] if x.startswith("repo:") else x for x in p)
    bc = centrality.betweenness(adj)
    return adj, {"betweenness": [{"id": n, "betweenness": round(v, 4)} for n, v in sorted(bc.items(), key=lambda t: (-t[1], t[0])) if v > 0],
                 "cut_vertices": sorted(centrality.articulation_points(adj)), "passage_points": pp, "no_recorded_link": unlinked,
                 "components": [sorted(c) for c in sorted(comps, key=lambda c: (-len(c), sorted(c)))]}


def bridges(rows: list[dict]) -> dict:
    rows = _ordered(rows)
    used, ex, mass = usable(rows)

    # 1. the file co-change graph
    edge_shas, node_shas = collections.defaultdict(list), collections.defaultdict(set)
    for r in used:
        fs = sorted({f"{r['repo']}:{p}" for p in r["paths"]})
        for f in fs:
            node_shas[f].add(r["sha"])
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                edge_shas[(fs[i], fs[j])].append(r["sha"])
    adj = centrality.make_graph([(u, v, len(s)) for (u, v), s in edge_shas.items()])
    for n in node_shas:
        adj.setdefault(n, {})
    count_v = _graph_summary(adj, centrality.to_distance(adj), node_shas, edge_shas, "1 / commits that changed both files")
    per_repo = collections.Counter(r["repo"] for r in used)
    high = sorted(f for f, sh in node_shas.items()
                  if len(sh) >= HIGH_CHURN_MIN and len(sh) / per_repo[f.split(":", 1)[0]] >= HIGH_CHURN_SHARE)
    hset = set(high)
    edge_j = {k: v for k, v in edge_shas.items() if k[0] not in hset and k[1] not in hset}
    node_j = {n: s for n, s in node_shas.items() if n not in hset}
    adj_j = centrality.make_graph([(u, v, len(s)) for (u, v), s in edge_j.items()])
    for n in node_j:
        adj_j.setdefault(n, {})
    dist_j = {u: {v: Fraction(len(node_j[u] | node_j[v]), len(edge_j[(min(u, v), max(u, v))])) for v in nb} for u, nb in adj_j.items()}
    jac_v = _graph_summary(adj_j, dist_j, node_j, edge_j, "1 / Jaccard (commits that changed both over commits that changed either), high-churn files set aside")
    files_out = {
        "reads": USABLE,
        "variants": {"count": count_v, "jaccard": jac_v},
        "high_churn": [{"id": f, "commits": len(node_shas[f]), "of": per_repo[f.split(":", 1)[0]]} for f in high],
        "note": "Two files are linked when one commit changed both. Betweenness counts the shortest paths between other files that pass "
                "through a file; 'share' is that count over the pairs of other files in its component, so compare files within one "
                "component. A cut vertex is a file whose removal splits its component. The count variant ranks files that change often "
                f"(READMEs, build files) first; the Jaccard variant sets aside {len(high)} high-churn files and normalises by how often "
                "each file changes.",
    }

    # 2. Claude sessions x repos (private repos as counts)
    sp, spriv = collections.defaultdict(list), collections.Counter()
    for r in rows:
        for sid in r["sessions"]:
            key = ("session:" + sid, "repo:" + r["repo"])
            if r["private"]:
                spriv[key] += 1
            elif r["sha"]:
                sp[key].append(r["sha"])
    all_repos = sorted({r["repo"] for r in rows})
    s_adj, s_graph = _bipartite(sp, spriv, all_repos)
    reps = collections.defaultdict(set)
    for (s, rep) in list(sp) + list(spriv):
        reps[s].add(rep[5:])
    pairs = collections.defaultdict(list)
    for s, rs in reps.items():
        rs = sorted(rs)
        for i, a in enumerate(rs):
            for b in rs[i + 1:]:
                pairs[(a, b)].append(s[8:])
    sessions = {s[8:]: {"repos": {rep[5:]: len(sp.get((s, rep), [])) + spriv.get((s, rep), 0) for rep in sorted(s_adj[s])},
                        "shas": sorted({x for rep in s_adj[s] for x in sp.get((s, rep), [])})}
                for s in sorted(reps)}
    no_trailer = sum(1 for r in rows if not r["sessions"])
    direct = {(a, b) for (a, b) in pairs}
    unlinked = {tuple(p) for p in s_graph["no_recorded_link"]}
    through = [[a, b] for i, a in enumerate(all_repos) for b in all_repos[i + 1:] if (a, b) not in direct and (a, b) not in unlinked]
    sess_out = {
        "n_sessions": len(reps), "n_multi_repo": sum(1 for v in reps.values() if len(v) > 1),
        "n_repos": len(all_repos), "n_repo_pairs": len(all_repos) * (len(all_repos) - 1) // 2,
        "repos_without_session": [n for n in all_repos if not any(n in rs for rs in reps.values())],
        "sessions": sessions,
        "repo_pairs": [{"a": a, "b": b, "sessions": sorted(ss),
                        "shas": sorted({x for s in ss for rep in (a, b) for x in sp.get(("session:" + s, "repo:" + rep), [])}),
                        "private_commits": sum(1 for r in rows if r["private"] and r["repo"] in (a, b) and set(ss) & set(r["sessions"]))}
                       for (a, b), ss in sorted(pairs.items(), key=lambda kv: (-len(kv[1]), kv[0]))],
        **s_graph,
        "linked_through_others": through,
        "reads": "every own commit that carries a Claude-Session trailer: merges and mass edits included, private commits as counts",
        "excluded": {"own commits with no session trailer": no_trailer} if no_trailer else {},
        "commits_without_trailer": no_trailer, "commits_with_trailer": len(rows) - no_trailer,
        "note": "One Claude session that committed to two repos links them directly (repo_pairs); two repos joined only through a chain "
                "of sessions and other repos are linked through others; two repos no chain joins are in no_recorded_link. Only commits "
                "that carry a Claude-Session trailer can show a link, so that is 'no recorded session link', not 'no link', and the "
                "repos may still be tied in other ways. A passage point between two repos is a node every path between them in this "
                "session graph crosses. Private repos enter with commit counts only.",
    }

    # 3. authors (identity labels) x repos (private repos as counts)
    ap, apriv, acls = collections.defaultdict(list), collections.Counter(), {}
    for r in rows:
        if r["merge"]:
            continue
        key = ("author:" + r["who"], "repo:" + r["repo"])
        acls[r["who"]] = r["cls"]
        if r["private"]:
            apriv[key] += 1
        else:
            ap[key].append(r["sha"])
    _, a_graph = _bipartite(ap, apriv)
    auth_out = {
        "reads": "every own commit that is not a merge: mass edits and commits with no paths included, private commits as counts",
        "excluded": {"merges": sum(1 for r in rows if r["merge"])} if any(r["merge"] for r in rows) else {},
        "edges": [{"author": a[7:], "class": acls[a[7:]], "repo": b[5:], "commits": len(s), "shas": sorted(s)} for (a, b), s in sorted(ap.items())]
        + [{"author": a[7:], "class": acls[a[7:]], "repo": b[5:], "commits": n, "shas": None, "private": True} for (a, b), n in sorted(apriv.items())],
        **a_graph,
        "note": "Identity labels from the family config, not people's names or addresses. With few identities this graph is mostly two "
                "hubs; a repo with one author hangs off that author.",
    }
    return {
        "stage": "bridges",
        "method": {"betweenness": "Brandes, exact (fractions), on weighted distances for files and hop counts for the session and author graphs",
                   "passage points": "a node whose removal disconnects two repos that were connected (Callon)",
                   "params": {"max_files_per_commit": MAX_FILES, "high_churn_share": HIGH_CHURN_SHARE, "high_churn_min_commits": HIGH_CHURN_MIN, "top": TOP},
                   "code_sha256": _code("structure.py", "centrality.py")},
        "rows": len(rows), "excluded": ex, "excluded_applies_to": "files (sessions and authors have their own)",
        "mass_edits": _mass_summary(mass, used),
        "files": files_out, "sessions": sess_out, "authors": auth_out,
    }


# ---------------------------------------------------------------- witnesses
def private_rx(priv) -> "re.Pattern | None":
    """For free text (the check's own messages, the gate results): a private repo's name, then ':' or '/' and the start of a
    path, wherever a word starts: at the start or after a space, a quote or a bracket. The path may open with a quote, as git
    quotes a path with odd characters (priv:"src/na\\303\\257ve.md"). "x3d_ai: counts only" is not a path; a folder of that
    name inside a path (other/x3d_ai/notes.md, repo:x3d_ai/...) is not the repo. It still errs towards a match: a public path
    with such a folder after a space matches too. Not for ids, see private_id_rx."""
    names = sorted(p for p in priv if p)
    path = r"(?=[^\s\"'`,;)\]}>]|\"[^\s\"'`,;)\]}>:])"            # a path starts, bare or in quotes
    return re.compile("|".join(r"(?<![\w.:/-])" + re.escape(p) + r"[:/]" + path for p in names)) if names else None


def private_id_rx(priv) -> "re.Pattern | None":
    """For ids (repo:path, repo/area): the stage and the bundle write each id as a whole string that starts with its repo, so
    an id names a private repo's file or area when it starts with that repo's name and ':' or '/'. A public id never does,
    whatever folders its path holds. The graphs' node ids (repo:, session: or author: and a name) are told apart by
    node_ids(), the method names its modules bare, and the stage's other strings start with a word and a space, so a private
    repo may be named repo, session, author or x3dray."""
    names = sorted(p for p in priv if p)
    return re.compile("(?:" + "|".join(re.escape(p) for p in names) + ")[:/]") if names else None


def node_ids(repos, labels, sessions) -> set[str]:
    """The ids the session and author graphs give their nodes, from the commits: repo:<name>, author:<label> and
    session:<id>. A private repo named repo, author or session would otherwise read them as its files."""
    return {"repo:" + x for x in repos} | {"author:" + x for x in labels} | {"session:" + x for x in sessions}


def itemised(x, priv: set, trail: str, nodes=frozenset()) -> list[str]:
    """Where x names a private repo's file, path or area (a key or a string that starts with one and is not one of the
    graphs' node ids in nodes), or lists a private record's shas. The messages carry the trail only, with any private key in
    it withheld."""
    rx, out = private_id_rx(priv), []

    def names(t: str) -> bool:
        return bool(rx and rx.match(t)) and t not in nodes

    def go(v, tr):
        if isinstance(v, dict):
            rp = v.get("repo")
            if (v.get("private") is True or (isinstance(rp, str) and rp in priv)) and v.get("shas"):
                out.append(f"{tr} lists the shas of a private repo")
            for k, w in v.items():
                kk = str(k)
                if names(kk):
                    out.append(f"{tr} has a key naming a private repo's file or area")
                    kk = "(withheld)"
                go(w, f"{tr}/{kk}")
        elif isinstance(v, list):
            for i, w in enumerate(v):
                go(w, f"{tr}[{i}]")
        elif isinstance(v, str) and names(v):
            out.append(f"{tr} names a private repo's file or area")
    go(x, trail)
    return out


def _components(adj: dict, removed=frozenset()) -> list[set]:
    seen, comps = set(removed), []
    for s in sorted(adj):
        if s in seen:
            continue
        comp, stack = {s}, [s]
        seen.add(s)
        while stack:
            v = stack.pop()
            for w in adj[v]:
                if w not in seen:
                    seen.add(w)
                    comp.add(w)
                    stack.append(w)
        comps.append(comp)
    return comps


def _reach(adj: dict, a, removed) -> set:
    seen, stack = {a, removed}, [a]
    while stack:
        v = stack.pop()
        for w in adj[v]:
            if w not in seen:
                seen.add(w)
                stack.append(w)
    seen.discard(removed)
    return seen


def _splits(adj: dict, v) -> bool:
    """A cut vertex: its neighbours do not all stay connected once it is removed."""
    nb = sorted(adj[v])
    return len(nb) >= 2 and not set(nb) <= _reach(adj, nb[0], v)


def _brute_betweenness(adj: dict) -> dict:
    """Hop-count betweenness from its definition: over unordered pairs s, t of other nodes, the share of shortest s-t paths
    through v (sigma_sv * sigma_vt / sigma_st when v lies on one). Exact fractions. Independent of Brandes in centrality.py."""
    nodes = sorted(adj)
    dist, sigma = {}, {}
    for s in nodes:
        d, sg, q = {s: 0}, {s: 1}, collections.deque([s])
        while q:
            v = q.popleft()
            for w in adj[v]:
                if w not in d:
                    d[w], sg[w] = d[v] + 1, 0
                    q.append(w)
                if d[w] == d[v] + 1:
                    sg[w] += sg[v]
        dist[s], sigma[s] = d, sg
    bc = {v: Fraction(0) for v in nodes}
    for i, s in enumerate(nodes):
        for t in nodes[i + 1:]:
            if t not in dist[s]:
                continue
            for v in nodes:
                if v != s and v != t and v in dist[s] and dist[s][v] + dist[v][t] == dist[s][t]:
                    bc[v] += Fraction(sigma[s][v] * sigma[v][t], sigma[s][t])
    return bc


def _graph_checks(G: dict, adj: dict) -> list[str]:
    """A bipartite repo graph (sessions or authors x repos) recounted by brute force: its clusters, the pairs no path joins,
    the nodes every path between two repos crosses, its cut vertices and its betweenness."""
    bad = []
    comps = _components(adj)
    if G.get("components") != [sorted(c) for c in sorted(comps, key=lambda c: (-len(c), sorted(c)))]:
        bad.append("clusters")
    comp_of = {v: i for i, c in enumerate(comps) for v in c}
    reps = sorted(n for n in adj if n.startswith("repo:"))
    pp, unl = {}, []
    for i, a in enumerate(reps):
        for b in reps[i + 1:]:
            if comp_of[a] != comp_of[b]:
                unl.append([a[5:], b[5:]])
                continue
            via = {v for v in comps[comp_of[a]] if v not in (a, b) and b not in _reach(adj, a, v)}
            if via:
                pp[f"{a[5:]} -> {b[5:]}"] = sorted(x[5:] if x.startswith("repo:") else x for x in via)
    if G.get("passage_points") != pp:
        bad.append("passage points")
    if G.get("no_recorded_link") != unl:
        bad.append("pairs with no recorded link")
    if G.get("cut_vertices") != sorted(v for v in adj if _splits(adj, v)):
        bad.append("cut vertices")
    want = [{"id": v, "betweenness": round(float(b), 4)} for v, b in sorted(_brute_betweenness(adj).items(), key=lambda t: (-t[1], t[0])) if b > 0]
    if G.get("betweenness") != want:
        bad.append("betweenness")
    return bad


def _on_a_shortest_path(adj: dict) -> set:
    """The nodes that lie inside a shortest path between two other nodes, for exact positive edge lengths: v does exactly
    when two of its neighbours u, w are as far apart as the way through v (a sub-path of a shortest path is one too). Those
    are the nodes with betweenness above zero. Dijkstra on exact fractions, independent of Brandes in centrality.py."""
    dist = {}
    for s in adj:
        d, done, heap = {s: Fraction(0)}, set(), [(Fraction(0), s)]
        while heap:
            du, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            for w, ln in adj[u].items():
                if w not in d or du + ln < d[w]:
                    d[w] = du + ln
                    heapq.heappush(heap, (d[w], w))
        dist[s] = d
    out = set()
    for v, nb in adj.items():
        ns = sorted(nb)
        if any(dist[u].get(w) == nb[u] + nb[w] for i, u in enumerate(ns) for w in ns[i + 1:]):
            out.add(v)
    return out


def _closed_intents(intents) -> set:
    """Every intersection of the objects' attribute sets: the intents of the concepts with at least one object."""
    closed = {frozenset(x) for x in intents}
    while True:
        new = {a & b for a in closed for b in closed} - closed
        if not new:
            return closed
        closed |= new


def _covers(intents: list) -> set:
    """(i, j) when concept j's intent sits just below concept i's: a proper subset with no concept between them."""
    return {(i, j) for i, a in enumerate(intents) for j, b in enumerate(intents)
            if b < a and not any(b < c < a for c in intents)}


def verify(con: dict, bri: dict, rows: list[dict]) -> list[str]:
    """Re-derive from the commits, independently of the lattice and graph code, what the two outputs claim: each cited sha
    is a public own commit that did what it is cited for (touched that file, both files, that area; is that kind of author,
    that author, in that repo; carries that session; is the first of its kind there; is that mass edit); every count equals
    its list and the counts re-derived from the rows, including each part's own set-asides; the lattices hold every closed
    set of kinds once, with the lines between them; the area rules are every rule that meets the thresholds; the file graphs'
    sizes, clusters, degrees, cut vertices and which files lie on a shortest path between two others; the session and author
    graphs' clusters, passage points, cut vertices and betweenness, recounted by brute force; every list of shas, kinds,
    files and pairs, and every count map, as the stage writes it (sorted or ranked, no repeats; a lattice's lines are a set,
    checked once each); that each author label is of one kind; and no private repo is itemised. The size of each file's betweenness is checked against a brute-force oracle in the
    tests. Messages never print a private repo's file or area, or an unknown sha."""
    rows = _ordered(rows)
    used, ex, mass = usable(rows)
    priv = {r["repo"] for r in rows if r["private"]}
    rx, idrx = private_rx(priv), private_id_rx(priv)
    nodes = node_ids({r["repo"] for r in rows}, {r["who"] for r in rows}, {s_ for r in rows for s_ in r["sessions"]})
    pub = collections.defaultdict(list)                      # sha -> its public rows (two repos can share a sha)
    for r in rows:
        if r["sha"]:
            pub[r["sha"]].append(r)
    problems: list[str] = []

    def P(msg: str) -> None:
        if rx and rx.search(msg):                            # a backstop: nm() below should already have withheld it
            msg = msg.split(":", 1)[0].split("/", 1)[0] + ": a check failed on a private repo's file or area (details withheld)"
        problems.append(msg)

    def nm(x):                                               # x is an id: it starts with its repo, or is a graph node
        return "(a private repo's file or area)" if idrx and isinstance(x, str) and idrx.match(x) and x not in nodes else x

    def row(sha, repo):
        return next((r for r in pub.get(sha, []) if r["repo"] == repo), None)

    # 0. every list of shas: known public commits, counted right; nothing private itemised
    def walk(x, trail):
        if isinstance(x, dict):
            sh = x.get("shas")
            if isinstance(sh, list):
                n_bad = sum(1 for s_ in sh if s_ not in pub)
                if n_bad:
                    P(f"{trail} cites {n_bad} unknown commit(s)")
                for k in ("weight", "commits"):
                    if isinstance(x.get(k), int) and "of" not in x and x[k] != len(sh):
                        P(f"{trail} {k} {x[k]} != {len(sh)} shas")
            for k, v in x.items():
                walk(v, f"{trail}/{'(withheld)' if nm(str(k)) != str(k) else k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{trail}[{i}]")

    walk(con, "concepts")
    walk(bri, "bridges")
    for t in itemised(con, priv, "concepts", nodes) + itemised(bri, priv, "bridges", nodes):
        P(t)

    # 1. totals and set-asides tie to the rows; the mass edits are those commits, the largest named right
    n_merges = sum(1 for r in rows if r["merge"])
    merges = {"merges": n_merges} if n_merges else {}
    touches = sum(len(r["paths"]) for r in mass)
    want_mass = {"commits": len(mass), "file_touches": touches, "of_file_touches": touches + sum(len(r["paths"]) for r in used),
                 "by_repo": list(_ranked(collections.Counter(r["repo"] for r in mass)).items()),
                 "by_class": list(_ranked(collections.Counter(r["cls"] for r in mass), _cls_key).items()),
                 "shas": sorted(r["sha"] for r in mass)}
    biggest = sorted((len(r["paths"]) for r in mass), reverse=True)[:LARGEST]
    for name, out in (("concepts", con), ("bridges", bri)):
        if out.get("rows") != len(rows):
            P(f"{name}: rows {out.get('rows')} != {len(rows)} in the dataset")
        if out.get("excluded") != ex:
            P(f"{name}: excluded does not match the rows")
        me = out.get("mass_edits") or {}
        if any((list((me.get(k) or {}).items()) if k in ("by_repo", "by_class") else me.get(k)) != v for k, v in want_mass.items()):
            P(f"{name}: mass_edits does not match the commits of more than {MAX_FILES} files, its maps largest first")
        largest = me.get("largest") or []
        if [e.get("files") for e in largest] != biggest:
            P(f"{name}: mass_edits.largest is not the largest mass edits")
        for e in largest:
            r = row(e.get("sha"), e.get("repo"))
            if not r or r["merge"] or len(r["paths"] or []) != e.get("files") or len(r["paths"] or []) <= MAX_FILES or r["cls"] != e.get("class"):
                P(f"{name}: mass_edits.largest cites a commit that is not that mass edit")

    # 2. files x kinds of author: each sha touched that file, in that repo, as that kind; the lattice is every closed set
    F = con["files_by_author_class"]
    wit = collections.defaultdict(lambda: collections.defaultdict(set))
    for r in used:
        for p_ in r["paths"]:
            wit[f"{r['repo']}:{p_}"][r["cls"]].add(r["sha"])
    if set(F["files"]) != set(wit):
        P(f"concepts: {len(set(F['files']) ^ set(wit))} file(s) differ from the files the usable commits touched")
    if F["n_files"] != len(wit) or F["n_commits"] != len(used):
        P("concepts: n_files or n_commits does not match the usable commits")
    kinds = {o: set(w) for o, w in wit.items()}
    for o, rec in F["files"].items():
        repo, _, path = o.partition(":")
        if repo in priv:
            continue                                         # already reported by itemised(), without the path
        for cl, shas in rec["by"].items():
            for s_ in shas:
                r = row(s_, repo)
                if not r or r["cls"] != cl or r["merge"] or path not in (r["paths"] or []) or len(r["paths"]) > MAX_FILES:
                    P(f"concepts: {nm(o)} cites a commit that is not a usable {cl} commit touching it")
                    break
        if o in wit and list(rec["by"].items()) != [(k, sorted(wit[o][k])) for k in _cls_sorted(wit[o])]:
            P(f"concepts: {nm(o)} does not list every usable commit that touched it, once, sorted, its kinds in class order")
        c = F["concepts"][rec["concept"]] if 0 <= rec["concept"] < len(F["concepts"]) else None
        if not c or set(c["intent"]) != set(rec["by"]):
            P(f"concepts: {nm(o)} sits in a concept whose kinds are not its own")
    intents = [frozenset(c["intent"]) for c in F["concepts"]]
    if [c["i"] for c in F["concepts"]] != list(range(len(intents))) or F["n_concepts"] != len(intents):
        P("concepts: the file lattice's concepts are misnumbered or miscounted")
    if any(c["intent"] != _cls_sorted(set(c["intent"])) for c in F["concepts"]):
        P("concepts: a file concept lists a kind twice, or its kinds out of class order")
    if len(set(intents)) != len(intents) or set(intents) != _closed_intents(kinds.values()):
        P("concepts: the file lattice is not every closed set of kinds, once each")
    if {tuple(e) for e in F["covers"]} != _covers(intents) or len(F["covers"]) != len(_covers(intents)):
        P("concepts: the file lattice's lines do not join each concept to the ones just above and below it")
    n_of = {o: len({x for v in w.values() for x in v}) for o, w in wit.items()}
    for c in F["concepts"]:
        want = {o for o, k in kinds.items() if set(c["intent"]) <= k}
        if c["support"] != len(want) or c["exact"] != sum(1 for o in want if kinds[o] == set(c["intent"])):
            P(f"concepts: concept {c['i']} support or exact does not match the files")
        if list(c["by_repo"].items()) != list(_ranked(collections.Counter(o.split(":", 1)[0] for o in want)).items()):
            P(f"concepts: concept {c['i']} by_repo does not match the files, largest first")
        if c["top_files"] != [{"id": o, "commits": n_of[o]} for o in sorted(want, key=lambda o: (-n_of[o], o))[:6]]:
            P(f"concepts: concept {c['i']} top files are not its most-changed files, counted right")

    # 3. repos x kinds of author: counts from every non-merge commit; each witness is the first commit of that kind there
    R = con["repos_by_author_class"]
    rc, firsts = collections.defaultdict(collections.Counter), collections.defaultdict(dict)
    for r in rows:
        if not r["merge"]:
            rc[r["repo"]][r["cls"]] += 1
            if r["sha"]:
                firsts[r["repo"]].setdefault(r["cls"], r["sha"])
    if R.get("excluded") != merges:
        P("concepts: the repo lattice's excluded does not match the merges")
    if set(R["repos"]) != set(rc):
        P("concepts: the repo lattice's repos do not match the non-merge commits")
    for name, rec in R["repos"].items():
        if list(rec["commits_by_class"].items()) != [(k, rc[name][k]) for k in _cls_sorted(rc.get(name, {}))]:
            P(f"concepts: {name} commits_by_class does not match the rows, in class order")
        if rec["private"] != (name in priv):
            P(f"concepts: {name} private flag is wrong")
        if rec["private"] and rec["first_commit_by_class"]:
            P(f"concepts: private repo {name} names a commit")
        elif not rec["private"] and list((rec["first_commit_by_class"] or {}).items()) != \
                [(k, firsts[name][k]) for k in _cls_sorted(firsts.get(name, {}))]:
            P(f"concepts: {name} first_commit_by_class does not name the first commit of each kind there, in class order")
    rk = {n: set(c) for n, c in rc.items()}
    rint = [frozenset(c["intent"]) for c in R["concepts"]]
    if any(c["intent"] != _cls_sorted(set(c["intent"])) for c in R["concepts"]):
        P("concepts: a repo concept lists a kind twice, or its kinds out of class order")
    if len(set(rint)) != len(rint) or set(rint) != _closed_intents(rk.values()) or [c["i"] for c in R["concepts"]] != list(range(len(rint))):
        P("concepts: the repo lattice is not every closed set of kinds, once each")
    if {tuple(e) for e in R["covers"]} != _covers(rint) or len(R["covers"]) != len(_covers(rint)):
        P("concepts: the repo lattice's lines do not join each concept to the ones just above and below it")
    for c in R["concepts"]:
        want = {n for n, k in rk.items() if set(c["intent"]) <= k}
        if c["repos"] != sorted(want) or c["support"] != len(want) or c["exact"] != sorted(n for n in want if rk[n] == set(c["intent"])):
            P(f"concepts: repo concept {c['i']} does not match the repos")

    # 4. areas: the counts, and every rule that meets the thresholds, re-derived from the usable commits
    A = con["areas"]
    areas_of = {(r["repo"], r["sha"]): {area(r["repo"], p_) for p_ in r["paths"]} for r in used}
    cnt = collections.Counter(a_ for v in areas_of.values() for a_ in v)
    kept = {a_ for a_, n in cnt.items() if n >= MIN_AREA_COMMITS}
    n_in = sum(1 for v in areas_of.values() if v & kept)
    if (A["areas"], A["areas_dropped"], A["commits"], A["commits_only_in_dropped_areas"]) != (len(kept), len(cnt) - len(kept), n_in, len(areas_of) - n_in):
        P("concepts: the area counts do not match the usable commits")
    ext = collections.defaultdict(set)
    for k, v in areas_of.items():
        for a_ in v & kept:
            ext[a_].add(k)
    want_rules, want_eq = {}, set()
    for a_ in sorted(kept):
        for b_ in sorted(kept):
            both = ext[a_] & ext[b_]
            if a_ == b_ or len(both) / len(ext[a_]) < MIN_RULE_CONFIDENCE:
                continue
            if ext[a_] == ext[b_]:
                if a_ < b_:
                    want_eq.add((a_, b_))
                continue
            want_rules[(a_, b_)] = (len(ext[a_]), len(ext[b_]), len(both), round(len(both) / len(ext[a_]), 4),
                                    sorted(sha for _, sha in ext[a_] - ext[b_]))
    got = {(rl["if"], rl["then"]): rl for rl in A["rules"]}
    if set(got) != set(want_rules) or len(got) != len(A["rules"]):
        P(f"concepts: {len(set(got) ^ set(want_rules))} area rule(s) missing or extra")
    elif [(rl["if"], rl["then"]) for rl in A["rules"]] != sorted(want_rules, key=lambda k: (-want_rules[k][3], -want_rules[k][0], k[0], k[1])):
        P("concepts: the area rules are not in order of confidence, then support")
    for k, rl in got.items():
        w = want_rules.get(k)
        if w and (rl["support"], rl["of"], rl["both"], rl["confidence"], rl["exceptions"]) != w:
            P(f"concepts: rule {nm(k[0])} => {nm(k[1])} does not match the commits")
    if A["equivalences"] != [{"attrs": [a_, b_], "support": len(ext[a_])} for a_, b_ in sorted(want_eq)]:
        P("concepts: the area equivalences do not match the commits")

    # 5. file co-change: high churn as stated; per variant the graph's size, clusters, degrees, cut vertices, top and edges
    node, edges = collections.defaultdict(set), collections.defaultdict(set)
    for r in used:
        fs = sorted({f"{r['repo']}:{p_}" for p_ in r["paths"]})
        for f_ in fs:
            node[f_].add(r["sha"])
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                edges[(fs[i], fs[j])].add(r["sha"])
    per_repo = collections.Counter(r["repo"] for r in used)
    hc = {f_ for f_, sh in node.items() if len(sh) >= HIGH_CHURN_MIN and len(sh) / per_repo[f_.split(":", 1)[0]] >= HIGH_CHURN_SHARE}
    if [h["id"] for h in bri["files"]["high_churn"]] != sorted(hc):
        P("bridges: high_churn does not list each file that meets the threshold once")
    for h in bri["files"]["high_churn"]:
        if h["commits"] != len(node.get(h["id"], ())) or h["of"] != per_repo[h["id"].split(":", 1)[0]]:
            P(f"bridges: high-churn file {nm(h['id'])} is miscounted")
    for vname, V in bri["files"]["variants"].items():
        keep_n = set(node) - (hc if vname == "jaccard" else set())
        E = {k: v for k, v in edges.items() if k[0] in keep_n and k[1] in keep_n}
        adj = {n: set() for n in keep_n}
        for a_, b_ in E:
            adj[a_].add(b_)
            adj[b_].add(a_)
        comps = _components(adj)
        size = {v: len(c) for c in comps for v in c}
        if (V["n_nodes"], V["n_edges"], V["n_components"], V["largest_component"]) != (len(adj), len(E), len(comps), max(map(len, comps), default=0)):
            P(f"bridges: {vname} graph's size or clusters do not match the usable commits")
        for fid, rec in V["per_file"].items():
            pairs = (size.get(fid, 0) - 1) * (size.get(fid, 0) - 2) / 2
            if (fid not in adj or rec["commits"] != len(node[fid]) or rec["degree"] != len(adj[fid]) or rec["component"] != size[fid]
                    or abs(rec["share"] - (rec["betweenness"] / pairs if pairs > 0 else 0.0)) > 1.01e-4):
                P(f"bridges: {vname} {nm(fid)} is not a usable file, is miscounted or should be set aside")
        dist = {u: {w: (Fraction(1, len(E[(min(u, w), max(u, w))])) if vname == "count" else
                        Fraction(len(node[u] | node[w]), len(E[(min(u, w), max(u, w))]))) for w in nb} for u, nb in adj.items()}
        between = _on_a_shortest_path(dist)
        if set(V["per_file"]) != between:
            P(f"bridges: {vname} lists {len(set(V['per_file']) ^ between)} file(s) wrongly as lying, or not, on a shortest path between two others")
        cut_ids = {f_ for f_, rec in V["per_file"].items() if rec["cut_vertex"]}
        if cut_ids != {v for v in adj if _splits(adj, v)} or V["cut_vertices"] != len(cut_ids) or \
                list(V["cut_vertices_by_repo"].items()) != sorted(collections.Counter(f_.split(":", 1)[0] for f_ in cut_ids).items()):
            P(f"bridges: {vname} cut vertices do not match a brute-force recount")
        big = {}
        for c_ in comps:
            big[min(c_).split(":", 1)[0]] = max(big.get(min(c_).split(":", 1)[0], 0), len(c_))
        if list(V["largest_cluster_by_repo"].items()) != sorted(big.items()):
            P(f"bridges: {vname} largest_cluster_by_repo does not match the clusters, by repo")
        ranked = sorted((rec["betweenness"] for rec in V["per_file"].values() if rec["betweenness"] > 0), reverse=True)
        tb = [t.get("betweenness") for t in V["top"]]
        if tb != ranked[:len(tb)] or len(tb) != min(TOP, len(ranked)) or len({t["id"] for t in V["top"]}) != len(tb):
            P(f"bridges: {vname} top is not the files with the highest betweenness")
        for t in V["top"]:
            rec = V["per_file"].get(t["id"]) or {}
            if t["shas"] != sorted(node.get(t["id"], set())) or not rec or any(t.get(k) != v for k, v in rec.items()):
                P(f"bridges: {vname} top {nm(t['id'])} cites commits that did not touch it, or differs from its record")
        if [(e["a"], e["b"]) for e in V["top_edges"]] != sorted(E, key=lambda k: (-len(E[k]), k))[:TOP]:
            P(f"bridges: {vname} top_edges are not the heaviest edges, in order")
        for e in V["top_edges"]:
            sh = E.get((e["a"], e["b"]), set())
            if e["shas"] != sorted(sh) or e["weight"] != len(sh):
                P(f"bridges: {vname} edge {nm(e['a'])} - {nm(e['b'])} cites commits that did not touch both, or is miscounted")

    # 6. sessions: per session and per pair, re-derived from the trailers; every pair of repos in exactly one list; the graph
    S = bri["sessions"]
    sess = collections.defaultdict(lambda: {"repos": collections.Counter(), "shas": set()})
    for r in rows:
        for sid in r["sessions"]:
            sess[sid]["repos"][r["repo"]] += 1
            if r["sha"]:
                sess[sid]["shas"].add(r["sha"])
    if set(S["sessions"]) != set(sess):
        P("bridges: the sessions do not match the trailers")
    for sid, rec in S["sessions"].items():
        if sid in sess and (list(rec["repos"].items()) != sorted(sess[sid]["repos"].items()) or rec["shas"] != sorted(sess[sid]["shas"])):
            P(f"bridges: session {sid} does not match its commits")
    if S["n_sessions"] != len(sess) or S["n_multi_repo"] != sum(1 for v in sess.values() if len(v["repos"]) > 1):
        P("bridges: n_sessions or n_multi_repo does not match the trailers")
    n_untrailered = sum(1 for r in rows if not r["sessions"])
    if (S["commits_without_trailer"], S.get("commits_with_trailer"), S.get("excluded")) != \
            (n_untrailered, len(rows) - n_untrailered, {"own commits with no session trailer": n_untrailered} if n_untrailered else {}):
        P("bridges: the session graph's trailer counts or excluded do not match the rows")
    repos_all = sorted({r["repo"] for r in rows})
    if S.get("n_repos") != len(repos_all) or S.get("repos_without_session") != [n for n in repos_all if not any(n in v["repos"] for v in sess.values())]:
        P("bridges: n_repos or repos_without_session does not match the rows")
    for pr in S["repo_pairs"]:
        ss = {sid for sid, v in sess.items() if pr["a"] in v["repos"] and pr["b"] in v["repos"]}
        shas = {r["sha"] for r in rows if r["sha"] and r["repo"] in (pr["a"], pr["b"]) and ss & set(r["sessions"])}
        npriv = sum(1 for r in rows if r["private"] and r["repo"] in (pr["a"], pr["b"]) and ss & set(r["sessions"]))
        if pr["sessions"] != sorted(ss) or pr["shas"] != sorted(shas) or pr.get("private_commits", 0) != npriv:
            P(f"bridges: pair {pr['a']} + {pr['b']} does not match the sessions they share")
    shared = collections.Counter((a, b) for v in sess.values() for i, a in enumerate(sorted(v["repos"])) for b in sorted(v["repos"])[i + 1:])
    if [(p_["a"], p_["b"]) for p_ in S["repo_pairs"]] != sorted(shared, key=lambda k: (-shared[k], k)):
        P("bridges: repo_pairs is not every pair a session committed to, once, most sessions first")
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for sid, v in sess.items():
        for rep in v["repos"]:
            parent[find("s:" + sid)] = find("r:" + rep)
    lists = {"repo_pairs": {(p_["a"], p_["b"]) for p_ in S["repo_pairs"]}, "linked_through_others": {tuple(p_) for p_ in S.get("linked_through_others", [])},
             "no_recorded_link": {tuple(p_) for p_ in S["no_recorded_link"]}}
    through_want = []
    for i, a in enumerate(repos_all):
        for b in repos_all[i + 1:]:
            share = any(a in v["repos"] and b in v["repos"] for v in sess.values())
            joined = find("r:" + a) == find("r:" + b)
            want = "repo_pairs" if share else "linked_through_others" if joined else "no_recorded_link"
            if want == "linked_through_others":
                through_want.append([a, b])
            got_in = [k for k, v in lists.items() if (a, b) in v]
            if got_in != [want]:
                P(f"bridges: {a} and {b} belong in {want}, found in {got_in or 'none'}")
    if S.get("linked_through_others") != through_want:
        P("bridges: linked_through_others is not each such pair once, in order")
    if S.get("n_repo_pairs") != len(repos_all) * (len(repos_all) - 1) // 2 or \
            sum(len(S.get(k) or []) for k in ("repo_pairs", "linked_through_others", "no_recorded_link")) != S.get("n_repo_pairs"):
        P("bridges: n_repo_pairs does not match the repos, or the three lists do not hold each pair once")
    sadj = {"repo:" + n: set() for n in repos_all}
    for sid, v in sess.items():
        sadj.setdefault("session:" + sid, set())
        for rep in v["repos"]:
            sadj["session:" + sid].add("repo:" + rep)
            sadj["repo:" + rep].add("session:" + sid)
    for what in _graph_checks(S, sadj):
        P(f"bridges: the session graph's {what} do not match a brute-force recount")

    # 7. authors: each edge's shas are that author's non-merge commits in that repo; private edges are counts; the graph
    Au = bri["authors"]
    ae = collections.defaultdict(list)
    for r in rows:
        if not r["merge"]:
            ae[(r["who"], r["repo"])].append(r)
    kinds_of = collections.defaultdict(set)
    for (who, _), rs in ae.items():
        kinds_of[who] |= {r["cls"] for r in rs}
    two = {w for w, k in kinds_of.items() if len(k) > 1}
    for w in sorted(two):
        P(f"bridges: author {w} has commits of {len(kinds_of[w])} kinds ({', '.join(_cls_sorted(kinds_of[w]))}): the family's "
          "identities give each label one kind")
    if Au.get("excluded") != merges:
        P("bridges: the author graph's excluded does not match the merges")
    if [(e["author"], e["repo"]) for e in Au["edges"]] != sorted(k for k in ae if k[1] not in priv) + sorted(k for k in ae if k[1] in priv):
        P("bridges: the author edges are not each author and repo with non-merge commits once, public first, in order")
    for e in Au["edges"]:
        rs = ae.get((e["author"], e["repo"]), [])
        if e["repo"] in priv:
            if set(e) != {"author", "class", "repo", "commits", "shas", "private"} or e["shas"] is not None or e["private"] is not True \
                    or e["commits"] != len(rs) or (e["author"] not in two and any(r["cls"] != e["class"] for r in rs)):
                P(f"bridges: author edge to {e['repo']} is not counts only, or is miscounted")
        elif set(e) != {"author", "class", "repo", "commits", "shas"} or e["shas"] != sorted(r["sha"] for r in rs) \
                or e["commits"] != len(rs) or (e["author"] not in two and any(r["cls"] != e["class"] for r in rs)):
            P(f"bridges: author edge {e['author']} - {e['repo']} cites commits that are not that author's there, or is miscounted")
    aadj = {}
    for who, rep in ae:
        aadj.setdefault("author:" + who, set()).add("repo:" + rep)
        aadj.setdefault("repo:" + rep, set()).add("author:" + who)
    for what in _graph_checks(Au, aadj):
        P(f"bridges: the author graph's {what} do not match a brute-force recount")
    return problems


def run(ds: dict) -> tuple[dict, dict, list[str]]:
    rows = commit_rows(ds)
    con, bri = concepts(rows), bridges(rows)
    return con, bri, verify(con, bri, rows)
