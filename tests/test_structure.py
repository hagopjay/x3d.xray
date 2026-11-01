"""Known-answer tests for the concepts and bridges stage (x3dray/structure.py) and the gates that re-check it. Synthetic rows,
standard library only."""

import copy
import json
import unittest

from x3dray import gates, profile, structure


def row(repo, sha, cls, paths, private=False, merge=False, who="a1", sessions=(), t=1):
    return {"repo": repo, "private": private, "sha": None if private else sha, "cls": cls, "who": who, "merge": merge,
            "paths": None if private else paths, "sessions": list(sessions), "t": t}


ROWS = [
    row("r1", "s01", "you", ["src/a.py", "tests/a.py"]),
    row("r1", "s02", "ai", ["src/a.py", "tests/a.py", "README.md"], who="claude", sessions=["S1"]),
    row("r1", "s03", "ai", ["src/b.py"], who="claude", sessions=["S1"]),
    row("r1", "s04", "you", ["src/b.py", "tests/b.py"]),
    row("r1", "s08", "ai", ["src/c.py", "tests/c.py"], who="claude", sessions=["S2"]),
    row("r2", "s05", "ai", ["lib/x.py"], who="claude", sessions=["S1"]),
    row("r2", "s06", "you", ["lib/x.py", "lib/y.py"]),
    row("r3", "s09", "you", ["notes.md"]),                                            # no session ever reached r3
    row("r1", "s07", "bot", ["vendor/%d.js" % i for i in range(30)], who="bot1"),      # mass edit
    row("r1", "m01", "you", [], merge=True),                                           # merge: git lists no paths
    row("priv", None, "you", None, private=True, sessions=["S1"], t=5),                # private: counts only
]


def run(rows=ROWS):
    con, bri = structure.concepts(rows), structure.bridges(rows)
    return con, bri


class Excluded(unittest.TestCase):
    def test_set_asides_are_counted(self):
        con, bri = run()
        for ex in (con["excluded"], bri["excluded"]):
            self.assertEqual(ex["mass edits (more than 15 files) set aside"], 1)
            self.assertEqual(ex["merges (git lists no paths for them)"], 1)
            self.assertEqual(ex["private commits (counted, never itemised)"], 1)
        self.assertEqual(con["mass_edits"]["shas"], ["s07"])
        self.assertEqual(con["mass_edits"]["file_touches"], 30)

    def test_mass_edit_boundary(self):
        rows = [row("r", "k15", "you", ["f%d" % i for i in range(15)]), row("r", "k16", "you", ["g%d" % i for i in range(16)])]
        keep, ex, mass = structure.usable(rows)
        self.assertEqual([r["sha"] for r in keep], ["k15"])
        self.assertEqual([r["sha"] for r in mass], ["k16"])


class Witnesses(unittest.TestCase):
    def test_every_sha_is_a_row_and_counts_match(self):
        con, bri = run()
        self.assertEqual(structure.verify(con, bri, ROWS), [])

    def assertCaught(self, con, bri, words, rows=ROWS):
        probs = structure.verify(con, bri, rows)
        self.assertTrue(any(words in p for p in probs), probs)
        self.assertFalse([p for p in probs if "nope" in p or "secret" in p])        # never prints an unknown sha or a private path

    def test_verify_catches_planted_faults(self):
        con, bri = run()
        bad = copy.deepcopy(con)
        bad["files_by_author_class"]["files"]["r1:src/a.py"]["by"]["you"].append("nope")
        self.assertCaught(bad, bri, "r1:src/a.py cites a commit that is not a usable you commit touching it")
        bad = copy.deepcopy(bri)
        bad["files"]["variants"]["count"]["top_edges"][0]["shas"].append("nope")
        self.assertCaught(con, bad, "unknown commit")
        bad = copy.deepcopy(con)
        bad["files_by_author_class"]["files"]["r1:src/a.py"]["by"]["ai"] = ["s01"]       # s01 is HagopJay's, not Claude's
        self.assertCaught(bad, bri, "r1:src/a.py cites a commit that is not a usable ai commit touching it")
        bad = copy.deepcopy(con)
        bad["files_by_author_class"]["files"]["r1:src/a.py"]["by"]["you"] = ["s04"]      # HagopJay's, in r1, but it never touched src/a.py
        self.assertCaught(bad, bri, "r1:src/a.py cites a commit that is not a usable you commit touching it")
        bad = copy.deepcopy(con)
        bad["repos_by_author_class"]["repos"]["priv"]["first_commit_by_class"] = {"you": "s01"}
        self.assertCaught(bad, bri, "private repo priv names a commit")
        bad = copy.deepcopy(con)
        bad["files_by_author_class"]["files"]["r1:src/a.py"]["concept"] = 0                # the top concept's kinds are not its own
        self.assertCaught(bad, bri, "sits in a concept whose kinds are not its own")
        bad = copy.deepcopy(con)
        bad["areas"]["rules"][0]["exceptions"] = ["s01"]
        bad["areas"]["rules"][0]["support"] = bad["areas"]["rules"][0]["both"] + 1
        self.assertCaught(bad, bri, "does not match the commits")
        bad = copy.deepcopy(con)
        bad["mass_edits"]["shas"] = ["s01"]
        self.assertCaught(bad, bri, "mass_edits does not match")

    def test_verify_ties_bridges_witnesses_to_what_they_witness(self):
        con, bri = run()
        bad = copy.deepcopy(bri)
        e = bad["files"]["variants"]["count"]["top_edges"][0]
        e["shas"] = ["s05", "s06"][: len(e["shas"])]                                       # r2 commits for an r1 edge
        self.assertCaught(con, bad, "cites commits that did not touch both")
        bad = copy.deepcopy(bri)
        ed = [x for x in bad["authors"]["edges"] if x["author"] == "claude" and x["repo"] == "r2"][0]
        ed["shas"] = ["s06"]                                                                # a1's commit, not claude's
        self.assertCaught(con, bad, "cites commits that are not that author's there")
        bad = copy.deepcopy(bri)
        pr = [x for x in bad["sessions"]["repo_pairs"] if (x["a"], x["b"]) == ("r1", "r2")][0]
        pr["shas"] = ["s01"]                                                                # carries no session
        self.assertCaught(con, bad, "does not match the sessions they share")
        bad = copy.deepcopy(bri)
        bad["sessions"]["no_recorded_link"] = [p for p in bad["sessions"]["no_recorded_link"] if p != ["r1", "r3"]]
        bad["sessions"]["linked_through_others"].append(["r1", "r3"])
        self.assertCaught(con, bad, "r1 and r3 belong in no_recorded_link")
        bad = copy.deepcopy(bri)
        bad["files"]["high_churn"] = []
        rows = [row("r", "h%02d" % i, "you", ["hub.md", "f%d.py" % i]) for i in range(10)]
        c2, b2 = run(rows)
        b2 = copy.deepcopy(b2)
        b2["files"]["high_churn"] = []
        self.assertCaught(c2, b2, "high_churn does not list", rows)

    def test_verify_recounts_every_part(self):
        con, bri = run()
        S = "sessions"

        def first_drop(c):
            c["repos_by_author_class"]["repos"]["r1"]["first_commit_by_class"].pop("ai")

        def first_late(c):
            c["repos_by_author_class"]["repos"]["r1"]["first_commit_by_class"]["ai"] = "s03"    # an ai commit in r1, but not the first

        def mass_files(c):
            c["mass_edits"]["largest"][0]["files"] = 29

        def mass_class(c):
            c["mass_edits"]["largest"][0]["class"] = "you"

        def mass_touches(c):
            c["mass_edits"]["file_touches"] = 31

        def drop_rule(c):
            c["areas"]["rules"].pop()

        def drop_cover(c):
            c["files_by_author_class"]["covers"].pop()

        def drop_concept(c):
            c["files_by_author_class"]["concepts"].pop()
        cases_con = [
            (first_drop, "first_commit_by_class does not name the first commit"), (first_late, "first_commit_by_class does not name the first commit"),
            (mass_files, "largest"), (mass_class, "largest cites a commit that is not that mass edit"), (mass_touches, "mass_edits does not match"),
            (lambda c: c["repos_by_author_class"].update(excluded={}), "repo lattice's excluded"),
            (lambda c: c["files_by_author_class"].update(n_concepts=99), "misnumbered or miscounted"),
            (lambda c: c["areas"].update(areas=99), "area counts"), (lambda c: c["areas"].update(commits_only_in_dropped_areas=99), "area counts"),
            (drop_rule, "area rule(s) missing or extra"), (drop_cover, "lines do not join"), (drop_concept, "not every closed set"),
        ]
        for f, words in cases_con:
            bad = copy.deepcopy(con)
            f(bad)
            self.assertCaught(bad, bri, words)

        def sess_pp(b):
            b[S]["passage_points"]["r1 -> r2"] = ["r3"]

        def sess_cut(b):
            b[S]["cut_vertices"] = []

        def sess_bc(b):
            b[S]["betweenness"][0]["betweenness"] += 1

        def auth_pp(b):
            b["authors"]["passage_points"] = {}

        def deg(b):
            b["files"]["variants"]["count"]["per_file"][next(iter(b["files"]["variants"]["count"]["per_file"]))]["degree"] += 1

        def cut_flag(b):
            pf = b["files"]["variants"]["count"]["per_file"]
            k = next(k for k, v in pf.items() if v["cut_vertex"])
            pf[k]["cut_vertex"] = False
        cases_bri = [
            (lambda b: b[S].update(excluded={}), "trailer counts or excluded"), (lambda b: b[S].update(commits_with_trailer=0), "trailer counts or excluded"),
            (lambda b: b[S].update(n_repos=9), "n_repos or repos_without_session"), (lambda b: b[S].update(repos_without_session=[]), "n_repos or repos_without_session"),
            (lambda b: b["authors"].update(excluded={}), "author graph's excluded"),
            (sess_pp, "session graph's passage points"), (sess_cut, "session graph's cut vertices"), (sess_bc, "session graph's betweenness"),
            (auth_pp, "author graph's passage points"),
            (lambda b: b["files"]["variants"]["count"].update(n_edges=99), "size or clusters"),
        ]
        for f, words in cases_bri:
            bad = copy.deepcopy(bri)
            f(bad)
            self.assertCaught(con, bad, words)
        rows = [row("r", "p1", "you", ["a.py", "b.py"], t=1), row("r", "p2", "you", ["b.py", "c.py"], t=2)]    # b.py sits between the two
        c2, b2 = run(rows)
        self.assertEqual(structure.verify(c2, b2, rows), [])
        for f, words in ((deg, "is miscounted"), (cut_flag, "cut vertices do not match")):
            bad = copy.deepcopy(b2)
            f(bad)
            self.assertCaught(c2, bad, words, rows)

    def test_verify_reads_each_list_as_written(self):
        rows = [row("r", "p%d" % i, "you", ["hub.md", "f%d.py" % i], t=i) for i in range(10)] + [row("r", "q1", "you", ["a.py", "b.py"], t=20),
                row("r", "q2", "you", ["b.py", "c.py"], t=21), row("r2", "q3", "ai", ["z.py"], who="claude", sessions=["S1"], t=22),
                row("r", "q4", "ai", ["a.py"], who="claude", sessions=["S1"], t=23), row("priv", None, "ai", None, private=True, who="claude", t=24)]
        rows += [row("r3", "c%d" % i, "you", [x, y], t=30 + i) for i, (x, y) in enumerate((("w", "x"), ("x", "y"), ("y", "z"), ("z", "w")))]   # a ring: no cut vertex
        con, bri = run(rows)
        self.assertEqual(structure.verify(con, bri, rows), [])

        def rev_bc(b):
            b["authors"]["betweenness"].reverse()

        def dup_bc(b):
            b["sessions"]["betweenness"].insert(0, {"id": b["sessions"]["betweenness"][-1]["id"], "betweenness": 99.0})

        def dup_churn(b):
            b["files"]["high_churn"].append(b["files"]["high_churn"][0])

        def dup_cut(b):
            b["sessions"]["cut_vertices"].append(b["sessions"]["cut_vertices"][0])

        def dup_top(b):
            b["files"]["variants"]["count"]["top"].append(b["files"]["variants"]["count"]["top"][-1])

        def drop_file(b):
            pf = b["files"]["variants"]["count"]["per_file"]
            k = next(k for k, v in pf.items() if not v["cut_vertex"])
            del pf[k]
            b["files"]["variants"]["count"]["top"] = [t for t in b["files"]["variants"]["count"]["top"] if t["id"] != k]

        def priv_class(b):
            next(e for e in b["authors"]["edges"] if e["repo"] == "priv")["class"] = "you"

        def priv_flag(b):
            del next(e for e in b["authors"]["edges"] if e["repo"] == "priv")["private"]

        def pub_dup(b):
            e = next(e for e in b["authors"]["edges"] if e["repo"] == "r")
            e["shas"].append(e["shas"][0])
            e["commits"] += 1

        def edges_order(b):
            b["authors"]["edges"].reverse()

        def top_shas(b):
            t = next(t for t in b["files"]["variants"]["count"]["top"] if len(t["shas"]) > 1)
            t["shas"].reverse()

        def top_edges_order(b):
            b["files"]["variants"]["count"]["top_edges"].reverse()
        for f, words in ((rev_bc, "author graph's betweenness"), (dup_bc, "session graph's betweenness"), (dup_churn, "high_churn does not list"),
                         (dup_cut, "session graph's cut vertices"), (dup_top, "top is not the files"), (drop_file, "on a shortest path between two others"),
                         (priv_class, "is not counts only, or is miscounted"), (priv_flag, "is not counts only, or is miscounted"),
                         (pub_dup, "not that author's there, or is miscounted"), (edges_order, "author edges are not each author"),
                         (top_shas, "top r:hub.md cites commits"), (top_edges_order, "top_edges are not the heaviest edges, in order")):
            bad = copy.deepcopy(bri)
            f(bad)
            self.assertCaught(con, bad, words, rows)
        bad = copy.deepcopy(con)
        c = next(c for c in bad["files_by_author_class"]["concepts"] if c["top_files"])
        c["top_files"] = c["top_files"][1:]
        self.assertCaught(bad, bri, "top files are not its most-changed files", rows)

        def dup_sha(c):
            by = c["files_by_author_class"]["files"]["r:a.py"]["by"]
            by["you"].append(by["you"][0])

        def class_order(c):
            by = c["files_by_author_class"]["files"]["r:a.py"]["by"]
            c["files_by_author_class"]["files"]["r:a.py"]["by"] = dict(reversed(list(by.items())))

        def dup_kind(c):
            k = next(k for k in c["files_by_author_class"]["concepts"] if k["intent"])
            k["intent"] = k["intent"] + k["intent"][:1]

        def repo_counts_order(c):
            rec = c["repos_by_author_class"]["repos"]["r"]
            rec["commits_by_class"] = dict(reversed(list(rec["commits_by_class"].items())))
        for f, words in ((dup_sha, "r:a.py does not list every usable commit that touched it, once"),
                         (class_order, "its kinds in class order"), (dup_kind, "lists a kind twice"),
                         (repo_counts_order, "r commits_by_class does not match the rows, in class order")):
            bad = copy.deepcopy(con)
            f(bad)
            self.assertCaught(bad, bri, words, rows)
        self.assertEqual(list(con["files_by_author_class"]["files"]["r:a.py"]["by"]), ["you", "ai"])    # class order, not alphabetical

    def test_verify_reads_ranked_maps_in_order(self):
        rows = [row("r", "a%d" % i, "you" if i % 2 else "ai", ["x.py", "y%d.py" % i], who="a1" if i % 2 else "claude", t=i) for i in range(4)]
        rows += [row("r4", "d1", "you", ["a", "b"], t=10), row("r4", "d2", "you", ["b", "c"], t=11), row("r5", "e1", "you", ["k.py"], t=12)]
        rows += [row("r", "m%d" % i, "you", ["v%d.js" % j for j in range(20)], t=20 + i) for i in range(2)]
        rows += [row("r4", "m9", "bot", ["w%d.js" % j for j in range(20)], who="bot1", t=30)]
        con, bri = run(rows)
        self.assertEqual(structure.verify(con, bri, rows), [])
        self.assertEqual((list(con["mass_edits"]["by_repo"]), list(con["mass_edits"]["by_class"])), (["r", "r4"], ["you", "bot"]))
        self.assertEqual(list(con["repos_by_author_class"]["repos"]["r"]["first_commit_by_class"]), ["you", "ai"])   # class order, not time
        self.assertEqual(bri["files"]["variants"]["count"]["largest_cluster_by_repo"], {"r": 5, "r4": 3, "r5": 1})
        wide = max(con["files_by_author_class"]["concepts"], key=lambda c: len(c["by_repo"]))
        self.assertEqual(list(wide["by_repo"]), ["r", "r4", "r5"])

        def flip(holder, key):
            holder[key] = dict(reversed(list(holder[key].items())))
        for f, words in ((lambda c: flip(next(k for k in c["files_by_author_class"]["concepts"] if k["i"] == wide["i"]), "by_repo"),
                          f"concept {wide['i']} by_repo does not match the files, largest first"),
                         (lambda c: flip(c["mass_edits"], "by_repo"), "concepts: mass_edits does not match"),
                         (lambda c: flip(c["mass_edits"], "by_class"), "concepts: mass_edits does not match"),
                         (lambda c: flip(c["repos_by_author_class"]["repos"]["r"], "first_commit_by_class"), "r first_commit_by_class does not name")):
            bad = copy.deepcopy(con)
            f(bad)
            self.assertCaught(bad, bri, words, rows)
        for f, words in ((lambda b: flip(b["files"]["variants"]["count"], "cut_vertices_by_repo"), "count cut vertices do not match"),
                         (lambda b: flip(b["mass_edits"], "by_repo"), "bridges: mass_edits does not match"),
                         (lambda b: b["files"]["variants"]["jaccard"]["largest_cluster_by_repo"].update(r4=4), "jaccard largest_cluster_by_repo"),
                         (lambda b: flip(b["files"]["variants"]["jaccard"], "largest_cluster_by_repo"), "jaccard largest_cluster_by_repo")):
            bad = copy.deepcopy(bri)
            f(bad)
            self.assertCaught(con, bad, words, rows)

    def test_a_label_with_two_kinds_is_named_as_such(self):
        rows = [row("r", "a1", "you", ["x.py"], who="pat", t=1), row("r", "a2", "bot", ["x.py", "y.py"], who="pat", t=2)]
        con, bri = run(rows)
        self.assertEqual(structure.verify(con, bri, rows),
                         ["bridges: author pat has commits of 2 kinds (you, bot): the family's identities give each label one kind"])

    def test_verify_never_prints_a_private_file_or_area(self):
        con, bri = run()
        leak = "priv:secret.py"

        def top_file(c):
            c["files_by_author_class"]["concepts"][0]["top_files"].append({"id": leak, "commits": 1})

        def rule(c):
            c["areas"]["rules"].append({"if": "priv/secret", "then": "r1/src", "support": 3, "of": 3, "both": 3, "confidence": 1.0, "exceptions": []})

        def churn(b):
            b["files"]["high_churn"].append({"id": leak, "commits": 9, "of": 9})

        def per_file(b):
            b["files"]["variants"]["count"]["per_file"][leak] = {"betweenness": 1.0, "share": 1.0, "component": 3, "degree": 2, "cut_vertex": True, "commits": 1}

        def top(b):
            b["files"]["variants"]["jaccard"]["top"].append({"id": leak, "betweenness": 1.0, "shas": []})

        def edge(b):
            b["files"]["variants"]["count"]["top_edges"].append({"a": leak, "b": "r1:src/a.py", "weight": 0, "shas": []})
        for f in (top_file, rule):
            bad = copy.deepcopy(con)
            f(bad)
            probs = structure.verify(bad, bri, ROWS)
            self.assertTrue(probs)
            self.assertFalse([p for p in probs if "secret" in p], probs)
        quoted = 'priv:"src/secret\\303\\257.md"'                       # git's quoted form

        def pair(b):
            b["sessions"]["repo_pairs"][0]["a"] = quoted

        def author(b):
            b["authors"]["edges"][0]["author"] = quoted
        for f in (churn, per_file, top, edge, pair, author):
            bad = copy.deepcopy(bri)
            f(bad)
            probs = structure.verify(con, bad, ROWS)
            self.assertTrue(probs)
            self.assertFalse([p for p in probs if "secret" in p], probs)

    def test_repos_that_share_history_keep_their_areas_apart(self):
        rows = [row(r, "z%02d" % i, "you", ["src/x.py", "tests/x.py"] if i < 3 else ["src/x.py"], t=i) for i in range(4) for r in ("r1", "r4")]
        con, bri = run(rows)
        self.assertEqual(structure.verify(con, bri, rows), [])
        pairs = {(r["if"], r["then"]) for r in con["areas"]["rules"]}
        self.assertIn(("r1/tests", "r1/src"), pairs)
        self.assertIn(("r4/tests", "r4/src"), pairs)
        r1 = [r for r in con["areas"]["rules"] if r["if"] == "r1/tests"][0]
        self.assertEqual((r1["support"], r1["both"]), (3, 3))          # not 6: the other repo's copies of the commits stay apart
        self.assertFalse([p for p in pairs if p[0].split("/")[0] != p[1].split("/")[0]])

    def test_deterministic(self):
        self.assertEqual(json.dumps(run()), json.dumps(run(list(reversed(ROWS)))))           # map order included


class Privacy(unittest.TestCase):
    def test_private_rows_are_counted_never_itemised(self):
        con, bri = run()
        pe = [e for e in bri["authors"]["edges"] if e["repo"] == "priv"]
        self.assertEqual([(e["commits"], e["shas"], e["private"]) for e in pe], [(1, None, True)])
        pr = con["repos_by_author_class"]["repos"]["priv"]
        self.assertTrue(pr["private"])
        self.assertFalse(pr["first_commit_by_class"])
        self.assertFalse([o for o in con["files_by_author_class"]["files"] if o.startswith("priv:")])
        self.assertEqual(bri["sessions"]["sessions"]["S1"]["repos"]["priv"], 1)                 # a count, no sha
        self.assertNotIn(None, bri["sessions"]["sessions"]["S1"]["shas"])

    def test_privacy_gate_finds_a_planted_leak(self):
        con, bri = run()
        self.assertEqual(gates._itemised(con, {"priv"}, "concepts") + gates._itemised(bri, {"priv"}, "bridges"), [])
        leak = copy.deepcopy(con)
        leak["files_by_author_class"]["files"]["priv:secret.py"] = leak["files_by_author_class"]["files"]["r1:src/a.py"]
        self.assertTrue(gates._itemised(leak, {"priv"}, "concepts"))
        leak = copy.deepcopy(bri)
        leak["authors"]["edges"].append({"author": "a1", "class": "you", "repo": "priv", "commits": 1, "shas": ["x"], "private": True})
        self.assertTrue(gates._itemised(leak, {"priv"}, "bridges"))
        leak = copy.deepcopy(con)
        leak["areas"]["rules"].append({"if": "priv/src", "then": "r1/src"})
        self.assertTrue(gates._itemised(leak, {"priv"}, "concepts"))

    def test_a_private_repo_may_be_named_like_a_word_the_stage_writes(self):
        for name in ("repo", "session", "author", "x3dray"):
            rows = [dict(r, repo=name) if r["repo"] == "priv" else r for r in ROWS]
            con, bri = run(rows)
            self.assertEqual(structure.verify(con, bri, rows), [], name)
            nodes = structure.node_ids({r["repo"] for r in rows}, {r["who"] for r in rows}, {s_ for r in rows for s_ in r["sessions"]})
            self.assertEqual(gates._itemised(con, {name}, "concepts", nodes) + gates._itemised(bri, {name}, "bridges", nodes), [], name)
        rows = [dict(r, repo="author") if r["repo"] == "priv" else r for r in ROWS]
        con, bri = run(rows)
        leak = copy.deepcopy(bri)
        leak["authors"]["cut_vertices"].append("author:secret.py")       # a file of the private repo "author", not an author
        probs = structure.verify(con, leak, rows)
        self.assertTrue([p for p in probs if "names a private repo's file or area" in p], probs)
        self.assertFalse([p for p in probs if "secret" in p], probs)

    def test_the_privacy_gate_tells_graph_nodes_from_private_files(self):
        import os
        import tempfile
        rows = [dict(r, repo="author") if r["repo"] == "priv" else r for r in ROWS]
        con, bri = run(rows)

        def bundle(b):
            return {"repos": [{"name": n, "visibility": "private" if n == "author" else "public"} for n in ("r1", "r2", "r3", "author")],
                    "commits": [{"r": r["repo"], "w": r["who"], "s": r["sessions"], **({"h": r["sha"]} if r["sha"] else {})} for r in rows],
                    "files": {}, "concepts": con, "bridges": b}
        leak = copy.deepcopy(bri)
        leak["authors"]["cut_vertices"].append("author:secret.py")
        with tempfile.TemporaryDirectory() as d:
            for b, want in ((bri, "pass"), (leak, "fail")):
                with open(os.path.join(d, "bundle.json"), "w", encoding="utf-8") as fh:
                    json.dump(bundle(b), fh)
                self.assertEqual(gates.gate_privacy(d)["status"], want)

    def test_an_id_names_a_private_file_only_from_its_start(self):
        self.assertEqual(gates._itemised({"priv:secret.md": 1}, {"priv"}, "gates"), ["gates has a key naming a private repo's file or area"])
        self.assertTrue(gates._itemised({"m": ["priv/src"]}, {"priv"}, "concepts"))
        self.assertTrue(gates._itemised({"m": 'priv:"src/na\\303\\257ve.md"'}, {"priv"}, "concepts"))      # git's quoted form
        self.assertFalse(gates._itemised({"n": "repo:priv", "o": "notpriv:x", "p": "a.priv/x", "q": "priv + r1"}, {"priv"}, "gates"))
        # every id starts with its repo, so a public id is public whatever folders its path holds, and wherever they sit
        public = ["r1:docs/priv/plan.md", "r1:priv/notes.md", 'r1:"priv/na\\303\\257ve.md"', "r1:notes/JEPA priv/plan.md",
                  "r1:probes/B checks (priv/validate.py) round/a.x3d", "r1:a,priv/x", "r1:a=priv/x", "r1:`priv/x`", "r1:a@priv/x"]
        self.assertFalse(gates._itemised({p_: 1 for p_ in public} | {"e": public}, {"priv"}, "concepts"))
        self.assertFalse(gates._itemised({"x3d.onto:dynasty-loom/x3d/build.py": 1}, {"x3d"}, "concepts"))

    def test_free_text_names_a_private_file_where_a_word_starts(self):
        rx = structure.private_rx({"priv"})
        for t in ("bridges: count priv:secret.md is not a usable file", "rule priv/src => r1/src", "(priv:x.py)", '"priv:a"', "priv/src",
                  'error Text string priv:"docs/na\\303\\257ve.md" is too long', 'priv:\\"src/a.md'):    # git's quoted form, raw and in JSON
            self.assertTrue(rx.search(t), t)
        for t in ("built /home/u/priv/site/xray.x3d", "repo:priv", "priv: counts only", "a.priv/x", "r1:docs/priv/plan.md", "priv",
                  '{"a": "priv:"}', 'priv:""'):
            self.assertFalse(rx.search(t), t)

    def test_gate_results_never_carry_a_private_file(self):
        g = {"witness": {"status": "fail", "summary": "1 tie", "structure": ["bridges: count priv:secret.md is not a usable file"]},
             "tools": {"status": "pass", "summary": "ran priv/secret checks", "failed": []},
             "privacy": {"status": "pass", "summary": "0 findings"}, "xsd": {"status": "pass", "summary": "validates"}}
        own = gates._guard(g, {"priv"})
        self.assertTrue(own)
        self.assertNotIn("secret", json.dumps(g))
        self.assertEqual(g["privacy"]["status"], "fail")
        self.assertEqual((g["xsd"]["status"], g["xsd"]["summary"]), ("pass", "validates"))
        clean = {"xsd": {"status": "pass", "summary": "validates"}, "privacy": {"status": "pass", "summary": "0 findings"}}
        self.assertEqual(gates._guard(clean, {"priv"}), [])
        self.assertEqual(clean["privacy"]["status"], "pass")

    def test_a_public_folder_named_like_the_private_repo_is_not_flagged(self):
        odd = ["docs/priv/plan.md", "priv/notes.md", '"priv/na\\303\\257ve.md"', "notes/JEPA priv/plan.md", "probes/B checks (priv/v.py) round/a.x3d"]
        rows = ROWS + [row("r1", "s1%d" % i, "you", [p_, "src/a.py"], t=9 + i) for i, p_ in enumerate(odd)]
        con, bri = run(rows)
        self.assertEqual(structure.verify(con, bri, rows), [])
        self.assertEqual(gates._itemised(con, {"priv"}, "concepts") + gates._itemised(bri, {"priv"}, "bridges"), [])

    def test_the_gates_own_words_do_not_fail_the_privacy_gate(self):
        # a private repo may be named like a word the gates write before ':' or '/': data, tools, files, fork, out
        def results():
            return {"xsd": {"status": "pass", "summary": "xray.x3d validates", "xsd": "x3d-4.0.xsd"},
                    "loom": {"status": "pass", "summary": "1 files: 0 errors, 0 warnings, 1 info",
                             "notes": ["xray.x3d:0: info fork-ok [] fork: XSD valid, 0 semantic errors, profile OK"]},
                    "privacy": {"status": "pass", "summary": "0 findings in 5 files we wrote", "scanned": ["bundle.json", "tools.js"]},
                    "tools": {"status": "pass", "summary": "3 of 3 checks pass", "failed": [],
                              "checks": ["mcp tools/list has the data tools and no scene tools", "mcp tools/call resolves an alias", "vocabulary: aliases resolve"]},
                    "live": {"status": "pass", "summary": "desktop-dark pass", "csp": "default-src 'none'; img-src 'self' data: blob:; connect-src https://x",
                             "chromium": "141.0", "caveat": "Likely on claude.ai, not Confirmed."}}
        for name in ("data", "tools", "files", "fork", "out", "vocabulary", "blob", "https", "priv"):
            g = results()
            self.assertEqual(gates._guard(g, {name}), [], name)
            self.assertEqual(g, results(), name)
        g = results()
        g["tools"]["failed"] = ["concepts {file}: answered tools/secret.py"]
        self.assertTrue(gates._guard(g, {"tools"}))                 # a data field still counts
        self.assertEqual(g["privacy"]["status"], "fail")

    def test_a_quoted_private_path_in_a_summary_is_withheld(self):
        g = {"loom": {"status": "fail", "summary": 'xray.x3d:9: error Text string priv:"docs/na\\303\\257ve.md" is too long'},
             "privacy": {"status": "pass", "summary": "0"}}
        self.assertTrue(gates._guard(g, {"priv"}))
        self.assertNotIn("docs/na", json.dumps(g))
        self.assertEqual((g["loom"]["status"], g["privacy"]["status"]), ("fail", "fail"))

    def test_a_summary_is_tested_whole_before_it_is_cut(self):
        g = {"xsd": {"status": "fail", "summary": "x" * 184 + " someone@example.org is not accepted"}, "privacy": {"status": "pass", "summary": "0"}}
        self.assertTrue(gates._guard(g, set()))
        self.assertNotIn("someone", json.dumps(g))
        self.assertEqual(g["privacy"]["status"], "fail")

    def test_a_private_commit_in_two_shared_sessions_counts_once(self):
        rows = [row("r1", "s01", "ai", ["a.py"], who="claude", sessions=["S1"], t=1), row("r1", "s02", "ai", ["a.py"], who="claude", sessions=["S3"], t=2),
                row("priv", None, "ai", None, private=True, who="claude", sessions=["S1", "S3"], t=3)]
        con, bri = run(rows)
        pair = [p for p in bri["sessions"]["repo_pairs"] if (p["a"], p["b"]) == ("priv", "r1")][0]
        self.assertEqual((pair["sessions"], pair["private_commits"]), (["S1", "S3"], 1))
        self.assertEqual(structure.verify(con, bri, rows), [])
        ds = {"repos": [{"name": "r", "present": True, "visibility": "public",
                         "commits": [{"sha": "a" * 40, "cls": "ai", "who": "claude", "t": 1, "paths": ["x"], "sessions": ["S1", "S1"]}]}]}
        self.assertEqual(structure.commit_rows(ds)[0]["sessions"], ["S1"])        # a session listed twice in one commit counts once


class Concepts(unittest.TestCase):
    def test_files_by_kind(self):
        con, _ = run()
        F = con["files_by_author_class"]
        self.assertEqual(F["files"]["r1:src/a.py"]["by"], {"you": ["s01"], "ai": ["s02"]})
        both = [c for c in F["concepts"] if c["intent"] == ["you", "ai"]][0]
        self.assertEqual(both["support"], 4)    # src/a.py, tests/a.py, src/b.py and lib/x.py: touched by both kinds
        self.assertEqual(sum(c["exact"] for c in F["concepts"]), F["n_files"])

    def test_rules_name_their_exceptions(self):
        con, _ = run()
        rules = {(r["if"], r["then"]): r for r in con["areas"]["rules"]}
        r = rules[("r1/src", "r1/tests")]
        self.assertEqual((r["support"], r["both"], r["exceptions"]), (5, 4, ["s03"]))
        self.assertEqual(rules[("r1/tests", "r1/src")]["exceptions"], [])
        self.assertNotIn(("r2/lib", "r1/src"), rules)     # areas touched by fewer than three commits are dropped


class Bridges(unittest.TestCase):
    def test_cochange_weight_is_the_sha_list(self):
        _, bri = run()
        e = [x for x in bri["files"]["variants"]["count"]["top_edges"] if {x["a"], x["b"]} == {"r1:src/a.py", "r1:tests/a.py"}][0]
        self.assertEqual((e["weight"], e["shas"]), (2, ["s01", "s02"]))

    def test_session_links_and_no_recorded_link(self):
        _, bri = run()
        S = bri["sessions"]
        pairs = {(p["a"], p["b"]): p["sessions"] for p in S["repo_pairs"]}
        self.assertEqual(pairs[("r1", "r2")], ["S1"])
        self.assertEqual(pairs[("priv", "r1")], ["S1"])
        priv_pair = [p for p in S["repo_pairs"] if (p["a"], p["b"]) == ("priv", "r1")][0]
        self.assertEqual((priv_pair["shas"], priv_pair["private_commits"]), (["s02", "s03"], 1))   # the private commit is a count
        self.assertEqual(S["linked_through_others"], [])
        self.assertEqual((S["n_repos"], S["n_repo_pairs"]), (4, 6))
        self.assertEqual(S["repos_without_session"], ["r3"])
        self.assertEqual(sorted(map(tuple, S["no_recorded_link"])), [("priv", "r3"), ("r1", "r3"), ("r2", "r3")])
        self.assertEqual(S["passage_points"]["r1 -> r2"], ["session:S1"])
        self.assertEqual(S["commits_without_trailer"], 6)
        self.assertEqual(S["excluded"], {"own commits with no session trailer": 6})

    def test_chains_of_sessions_link_through_others(self):
        rows = [row("ra", "c1", "ai", ["a.py"], who="claude", sessions=["A"]), row("rb", "c2", "ai", ["b.py"], who="claude", sessions=["A"]),
                row("rb", "c3", "ai", ["b.py"], who="claude", sessions=["B"]), row("rc", "c4", "ai", ["c.py"], who="claude", sessions=["B"]),
                row("rd", "c5", "you", ["d.py"])]
        con, bri = run(rows)
        S = bri["sessions"]
        self.assertEqual(sorted((p["a"], p["b"]) for p in S["repo_pairs"]), [("ra", "rb"), ("rb", "rc")])
        self.assertEqual(S["linked_through_others"], [["ra", "rc"]])
        self.assertEqual(sorted(map(tuple, S["no_recorded_link"])), [("ra", "rd"), ("rb", "rd"), ("rc", "rd")])
        self.assertEqual(S["passage_points"]["ra -> rc"], ["rb", "session:A", "session:B"])
        self.assertEqual(structure.verify(con, bri, rows), [])

    def test_each_part_says_what_it_read(self):
        con, bri = run()
        self.assertEqual(con["repos_by_author_class"]["excluded"], {"merges": 1})
        self.assertEqual(bri["authors"]["excluded"], {"merges": 1})
        self.assertEqual(con["repos_by_author_class"]["repos"]["r1"]["commits_by_class"], {"you": 2, "ai": 3, "bot": 1})   # mass edit counted here
        for part in (con["files_by_author_class"], con["areas"], con["repos_by_author_class"], bri["files"], bri["sessions"], bri["authors"]):
            self.assertTrue(part["reads"])

    def test_high_churn_files_are_set_aside_in_the_jaccard_variant(self):
        rows = [row("r", "h%02d" % i, "you", ["hub.md", "f%d.py" % i]) for i in range(10)]
        _, bri = run(rows)
        self.assertEqual([h["id"] for h in bri["files"]["high_churn"]], ["r:hub.md"])
        self.assertIn("r:hub.md", bri["files"]["variants"]["count"]["per_file"])
        self.assertNotIn("r:hub.md", bri["files"]["variants"]["jaccard"]["per_file"])
        self.assertTrue(bri["files"]["variants"]["count"]["per_file"]["r:hub.md"]["cut_vertex"])


class WitnessGate(unittest.TestCase):
    def test_scene_may_not_draw_what_the_bundle_lacks(self):
        con, bri = run()
        b = {"concepts": con, "bridges": bri}
        L = {"concepts": {"files": [{"i": 0}], "repos": []}, "bridges": {"repos": {"r1": {}}, "sessions": {"S1": {}}, "files": []}}
        probs, _ = gates._structure_witness(b, L, None)
        self.assertEqual(probs, ["no dataset.json to check the concepts and bridges against"])
        L["bridges"]["sessions"]["S9"] = {}
        L["bridges"]["repos"]["r3"] = {}
        L["concepts"]["files"].append({"i": 99})
        probs, _ = gates._structure_witness(b, L, None)
        self.assertIn("scene draws session S9, which the bundle lacks", probs)
        self.assertIn("scene draws r3 in the session graph without a session", probs)
        self.assertIn("scene draws file concept 99, which the bundle lacks", probs)


class Signals(unittest.TestCase):
    def dataset(self):
        def c(sha, cls, paths, t):
            return {"sha": sha * 40, "cls": cls, "who": {"you": "HagopJay", "ai": "claude", "bot": "bot1"}[cls], "t": 1760000000 + t, "add": 1,
                    "del": 0, "paths": paths, "sessions": [], "subject": "s", "upstream": False, "merge": False}
        cs = [c("a", "you", ["req.txt", "a.py"], 1), c("b", "ai", ["req.txt", "b.py"], 2), c("c", "bot", ["req.txt"], 3),
              c("d", "you", ["a.py"], 4), c("e", "ai", ["b.py"], 5), c("f", "bot", ["c.txt"], 6)]
        return {"family": "synthetic", "lineage": [], "repos": [{"name": "r", "present": True, "visibility": "public", "kind": "own",
                                                                 "commits": cs, "files": [], "branches": [], "tree": {}}]}

    def test_both_authors_counted_when_their_pair_is_not_a_concept(self):
        ds = self.dataset()
        con, bri, problems = structure.run(ds)
        self.assertEqual(problems, [])
        self.assertNotIn(["you", "ai"], [c["intent"] for c in con["files_by_author_class"]["concepts"]])   # {you, ai} is not closed here
        prof = profile.profile_family(ds, {"pairs": []}, None, con, bri)
        s = next(x for x in prof["signals"] if x["id"] == "concepts_files")
        self.assertEqual((s["values"]["you_ai"], s["values"]["you_ai_exact"]), (1, 0))
        self.assertIn("1 of them by both HagopJay and Claude", s["text"])
        only = s["values"]["only"]
        self.assertEqual(sum(only.values()) + s["values"]["shared"], s["values"]["n_files"])

    def test_bridge_files_say_how_many_repos_have_a_file_between_others(self):
        def c(sha, paths, t):
            return {"sha": sha.ljust(40, "0"), "cls": "you", "who": "HagopJay", "t": 1760000000 + t, "add": 1, "del": 0, "paths": paths,
                    "sessions": [], "subject": "s", "upstream": False, "merge": False}

        def repo(n, cs):
            return {"name": n, "present": True, "visibility": "public", "kind": "own", "commits": cs, "files": [], "branches": [], "tree": {}}
        chain = [c("a%02d" % i, ["f%02d.py" % i, "f%02d.py" % (i + 1)], i) for i in range(11)]       # twelve files in a line
        clique = [c("b", ["g%d.py" % i for i in range(10)], 50)]                                       # ten files, one commit: none between
        ds = {"family": "synthetic", "lineage": [], "repos": [repo("p", chain), repo("q", clique)]}
        con, bri, problems = structure.run(ds)
        self.assertEqual(problems, [])
        prof = profile.profile_family(ds, {"pairs": []}, None, con, bri)
        s = next(x for x in prof["signals"] if x["id"] == "bridges_files")
        self.assertTrue(s["text"].startswith("1 of the 2 repos with a cluster of ten or more files has a file between others in it; in p,"), s["text"])
        self.assertNotIn("most others", s["text"])
        self.assertNotIn("high-churn", s["text"])                    # none to set aside, so no "with them kept" either
        self.assertNotIn("With them kept", s["text"])
        self.assertEqual((s["values"]["with_bridge"], s["values"]["with_cluster"]), (1, 2))
        self.assertEqual(s["values"]["repos_with_a_cluster_of_ten"], ["p", "q"])
        self.assertLessEqual(len(s["text"]), 320)

    def test_the_session_sentence_counts_session_trailers(self):
        def c(sha, cls, paths, t, sessions=()):
            return {"sha": sha.ljust(40, "0"), "cls": cls, "who": {"you": "HagopJay", "ai": "claude"}[cls], "t": 1760000000 + t, "add": 1,
                    "del": 0, "paths": paths, "sessions": list(sessions), "subject": "s", "upstream": False, "merge": False, "ai_trailer": cls == "you"}

        def repo(n, cs):
            return {"name": n, "present": True, "visibility": "public", "kind": "own", "commits": cs, "files": [], "branches": [], "tree": {}}
        r1 = [c("a", "ai", ["a.py"], 1, ["S1"])] + [c("b%d" % i, "you", ["a.py"], 2 + i) for i in range(4)]     # the profile wants five
        ds = {"family": "synthetic", "lineage": [], "repos": [repo("r1", r1), repo("r2", [c("d", "ai", ["x.py"], 9, ["S1"]), c("e", "you", ["x.py"], 10)])]}
        con, bri, problems = structure.run(ds)
        self.assertEqual(problems, [])
        prof = profile.profile_family(ds, {"pairs": []}, None, con, bri)
        t = next(x for x in prof["signals"] if x["id"] == "bridges_sessions")["text"]
        self.assertIn("1 of 1 sessions that left a trailer committed to more than one repo", t)
        self.assertIn("5 own commits carry no session trailer", t)          # each also carries a co-author trailer: not the same trailer


if __name__ == "__main__":
    unittest.main()
