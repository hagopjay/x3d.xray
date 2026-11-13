"""Typed predicates are bounded by their witnesses, not association strength."""
import copy
import itertools
import json
import random
import unittest

from x3dray import relations


def dataset(*names, private=()):
    return {"family": "fixture", "repos": [{"name": n, "visibility": "private" if n in private else "public",
            "head": "abcdef123456", "commits": [{"sha": "abcdef123456", "t": 10, "cls": "you"}]} for n in names]}


def pair(a, b, kinds, evidence, grade="C"):
    return {"from": a, "to": b, "kinds": kinds, "evidence": evidence, "grade": grade}


def edge(a, b, predicate="declares_use", directed=True, grade="C", suffix=""):
    return {"id": f"relation:{a}:{b}:{suffix}", "from": a, "to": b, "predicate": predicate,
            "directed": directed, "grade": grade, "evidence": [{"id": f"witness:{a}:{b}:{suffix}", "text": "synthetic"}]}


def graph(edges, nodes=None):
    names = set(nodes or ()) | {e[k] for e in edges for k in ("from", "to")}
    return {"schema_version": 1, "snapshot_digest": "synthetic", "nodes": [{"id": n} for n in sorted(names)],
            "records": edges, "predicates": {e["predicate"]: {"directed": e["directed"]} for e in edges}}


class RelationProjectionTests(unittest.TestCase):
    def test_detector_specific_predicates_and_grades_do_not_inherit_pair_grade(self):
        p = pair("a", "b", {"shared_file": 1, "mention:docs": 2, "mention:dependency": 1}, [
            {"kind": "shared_file", "path": "src/a.py", "other_path": "copy/a.py", "bytes": 1500},
            {"kind": "mention:docs", "path": "README.md", "line": 3, "text": "b is discussed"},
            {"kind": "mention:dependency", "path": "package.json", "line": 4, "text": '"b": "*"'}])
        result = relations.build_relations(dataset("a", "b"), {"pairs": [p]})
        by_kind = {r["detector_kind"]: r for r in result["records"]}
        self.assertEqual(by_kind["shared_file"]["predicate"], "shares_blob")
        self.assertFalse(by_kind["shared_file"]["directed"])
        self.assertEqual(by_kind["mention:docs"]["grade"], "L")
        self.assertEqual(by_kind["mention:dependency"]["predicate"], "dependency_reference")
        self.assertNotIn("imports", result["predicates"])
        for record in result["records"]:
            self.assertTrue(all(e["kind"] == record["detector_kind"] for e in record["evidence"]))

    def test_missing_predicate_witness_is_withheld_not_borrowed(self):
        p = pair("a", "b", {"pin": 4, "mention:docs": 1}, [{"kind": "mention:docs", "path": "README.md", "text": "b"}])
        result = relations.build_relations(dataset("a", "b"), {"pairs": [p]})
        self.assertEqual([r["predicate"] for r in result["records"]], ["mentions"])
        self.assertEqual(result["coverage"]["withheld_by_reason"], {"missing_safe_witness": 1})
        self.assertEqual(result["coverage"]["machine_detector_assertions"], 2)

    def test_lineage_direction_means_successor_claims_from_predecessor(self):
        p = pair("blender", "ml", {"lineage": 1}, [{"kind": "lineage", "text": "owner stated blender, then ml"}])
        record = relations.build_relations(dataset("blender", "ml"), {"pairs": [p]})["records"][0]
        self.assertEqual((record["from"], record["to"], record["predicate"]), ("ml", "blender", "claims_lineage_from"))
        self.assertEqual(record["source_endpoints"], {"from": "blender", "to": "ml"})
        self.assertIn("not git ancestry", record["reason"])

    def test_curated_prose_does_not_establish_direction_or_predicate(self):
        cur = {"ties": [{"id": "E1", "from": "a", "to": "b", "grade": "L", "kind": "imports",
                         "direction": "a probably calls b", "evidence": ["Review notes mention both modules."]}]}
        record = relations.build_relations(dataset("a", "b"), {"pairs": []}, cur)["records"][0]
        self.assertEqual(record["predicate"], "curated_association")
        self.assertFalse(record["directed"])
        self.assertEqual(record["declared_direction"], "a probably calls b")
        self.assertEqual(record["grade"], "L")

    def test_private_projection_exports_counts_without_detail_or_detail_hash_identity(self):
        ds = dataset("a", "private", private={"private"})
        ds["repos"][1]["head"] = "f" * 40
        ds["repos"][1]["commits"][0].update(sha="e" * 40, subject="private subject", paths=["secret.py"])
        p = pair("private", "a", {"pin": 3}, [{"kind": "pin", "path": "secret.py", "text": "pin " + "d" * 40}])
        result = relations.build_relations(ds, {"pairs": [p]})
        text = json.dumps(result)
        for withheld in ("secret.py", "private subject", "f" * 40, "e" * 40, "d" * 40):
            self.assertNotIn(withheld, text)
        record = result["records"][0]
        self.assertEqual(record["evidence"][0]["visibility"], "aggregate_only")
        self.assertEqual(record["evidence"][0]["count"], 3)
        self.assertEqual(record["evidence_records"], {"total": 3, "returned": 1, "omitted": 2,
            "scope": "Provided detector observation count and bounded source-witness sample."})
        changed = copy.deepcopy(ds)
        changed["repos"][1]["head"] = "changed private tip"
        changed["repos"][1]["commits"][0].update(sha="changed", subject="other", paths=["other"])
        changed_pair = copy.deepcopy(p)
        changed_pair["evidence"][0].update(path="other-private.py", text="other-private-details")
        self.assertEqual(result, relations.build_relations(changed, {"pairs": [changed_pair]}))

    def test_unsafe_public_witness_and_private_locator_are_withheld_without_echo(self):
        ds = dataset("a", "b", "private", private={"private"})
        for text in ("canary@example.invalid", "ghp_" + "a" * 30, "private:src/secret.py"):
            p = pair("a", "b", {"mention:docs": 1}, [{"kind": "mention:docs", "path": "README.md", "text": text}])
            result = relations.build_relations(ds, {"pairs": [p]})
            self.assertEqual(result["records"], [])
            self.assertNotIn(text, json.dumps(result))

    def test_order_and_source_mutability_do_not_change_snapshot(self):
        ds = dataset("a", "b")
        evidence = [{"kind": "mention:docs", "path": f"docs/{i}.md", "text": "b"} for i in range(12)]
        p = pair("a", "b", {"mention:docs": 50}, evidence)
        one = relations.build_relations(ds, {"pairs": [p]})
        p2 = copy.deepcopy(p)
        p2["evidence"].reverse()
        ds2 = copy.deepcopy(ds)
        ds2["repos"].reverse()
        self.assertEqual(one, relations.build_relations(ds2, {"pairs": [p2]}))
        ledger = one["records"][0]["evidence_records"]
        self.assertEqual((ledger["total"], ledger["returned"], ledger["omitted"]), (50, 8, 42))
        one["records"][0]["evidence"][0]["text"] = "mutated return"
        self.assertNotIn("mutated return", json.dumps(p))
        self.assertEqual(relations.build_relations(ds, {"pairs": [p]}), relations.build_relations(ds, {"pairs": [p]}))

    def test_public_snapshot_changes_but_private_hidden_fields_do_not(self):
        ds = dataset("a")
        before = relations.build_relations(ds, {"pairs": []})["snapshot_digest"]
        ds["repos"][0]["head"] = "new public revision"
        after = relations.build_relations(ds, {"pairs": []})["snapshot_digest"]
        self.assertNotEqual(before, after)

    def test_unknown_detector_and_empty_curated_witness_are_counted(self):
        result = relations.build_relations(dataset("a", "b"), {"pairs": [pair("a", "b", {"invented": 1}, [])]},
            {"ties": [{"id": "E1", "from": "a", "to": "b", "grade": "C", "evidence": []}]})
        self.assertEqual(result["coverage"]["withheld_by_reason"], {"missing_safe_witness": 1, "unsupported_detector": 1})
        self.assertEqual(result["records"], [])

    def test_duplicate_source_identity_and_bad_summary_fail_explicitly(self):
        p = pair("a", "b", {"uses": 1}, [{"kind": "uses", "text": "declared config use"}])
        with self.assertRaises(ValueError):
            relations.build_relations(dataset("a", "b"), {"pairs": [p, p]})
        p["kinds"]["uses"] = True
        with self.assertRaises(ValueError):
            relations.build_relations(dataset("a", "b"), {"pairs": [p]})

    def test_unknown_detector_and_curated_identifier_do_not_smuggle_private_locators(self):
        ds = dataset("a", "b", "private", private={"private"})
        result = relations.build_relations(ds, {"pairs": [pair("a", "b", {"mention:private:secret.py": 1},
            [{"kind": "mention:private:secret.py", "path": "README.md", "text": "b"}])]},
            {"ties": [{"id": "private:secret.py", "from": "a", "to": "b", "grade": "C", "evidence": ["public text"]}]})
        self.assertEqual(result["records"], [])
        self.assertNotIn("secret.py", json.dumps(result))
        self.assertEqual(result["coverage"]["withheld_assertions"], 2)

    def test_explicit_account_orientation_is_not_assumed_for_ambiguous_authorship(self):
        a = pair("alex (account)", "repo", {"second_account": 2}, [{"kind": "second_account", "text": "Two recorded account commits"}])
        b = pair("other", "repo", {"second_account": 2}, [{"kind": "second_account", "text": "Two recorded commits"}])
        result = relations.build_relations(dataset("repo", "other"), {"pairs": [a, b]})
        by_from = {r["from"]: r for r in result["records"]}
        self.assertEqual(by_from["alex (account)"]["predicate"], "recorded_authorship")
        self.assertTrue(by_from["alex (account)"]["directed"])
        self.assertEqual(by_from["other"]["predicate"], "authorship_association")
        self.assertFalse(by_from["other"]["directed"])


class ArchitectureTraversalTests(unittest.TestCase):
    def test_outgoing_incoming_and_association_are_distinct(self):
        g = graph([edge("a", "b"), edge("b", "c")])
        self.assertEqual(relations.traverse(g, "a", "c")["hops"], 2)
        missing = relations.traverse(g, "c", "a")
        self.assertEqual(missing["total_paths"], 0)
        self.assertIn("hop bound", missing["note"])
        self.assertEqual(relations.traverse(g, "c", "a", direction="incoming")["hops"], 2)
        self.assertEqual(relations.traverse(g, "c", "a", direction="association")["hops"], 2)

    def test_symmetric_relations_work_in_either_direction(self):
        g = graph([edge("a", "b", "shares_blob", False)])
        for direction in ("outgoing", "incoming", "association"):
            self.assertEqual(relations.traverse(g, "b", "a", direction=direction)["hops"], 1)

    def test_parallel_records_do_not_multiply_paths_and_caps_are_counted(self):
        g = graph([edge("a", "b", suffix=str(i)) for i in range(8)] + [edge("b", "c")])
        result = relations.traverse(g, "a", "c")
        self.assertEqual(result["total_paths"], 1)
        hop = result["paths"][0]["hops"][0]
        self.assertEqual((hop["total_relations"], hop["returned_relations"], hop["omitted_relations"]), (8, 4, 4))

    def test_exact_shortest_path_count_and_lexicographic_bounded_output(self):
        g = graph([edge("s", "a"), edge("a", "z"), edge("z", "t"), edge("s", "b"), edge("b", "c"), edge("c", "t")])
        result = relations.traverse(g, "s", "t", limit=1)
        self.assertEqual((result["total_paths"], result["returned_paths"], result["omitted_paths"]), (2, 1, 1))
        self.assertEqual(result["paths"][0]["nodes"], ["s", "a", "z", "t"])

    def test_filters_and_hop_bound_are_expressed_in_the_result(self):
        g = graph([edge("a", "b"), edge("b", "c", "mentions", grade="L"), edge("a", "c", "shares_blob", False, "S")])
        result = relations.traverse(g, "a", "c", predicates="declares_use,mentions", grades="C,L", max_hops=1)
        self.assertEqual(result["total_paths"], 0)
        self.assertEqual(result["predicates"], ["declares_use", "mentions"])
        self.assertEqual(relations.traverse(g, "a", "c", predicates=["declares_use", "mentions"], grades=["C", "L"])["hops"], 2)

    def test_neighbour_omissions_and_returned_subgraph_are_explicit(self):
        g = graph([edge("a", n) for n in ("b", "c", "d")])
        result = relations.traverse(g, "a", limit=2)
        self.assertEqual((result["total_nodes"], result["returned_nodes"], result["omitted_nodes"]), (3, 2, 1))
        self.assertEqual({n["id"] for n in result["nodes"]}, {"b", "c"})
        self.assertEqual((result["total_records"], result["returned_records"], result["omitted_records"]), (3, 2, 1))
        self.assertIn("returned", result["record_scope"])

    def test_identity_path_and_invalid_requests(self):
        g = graph([], nodes=["a"])
        result = relations.traverse(g, "a", "a")
        self.assertEqual((result["hops"], result["total_paths"], result["paths"][0]["nodes"]), (0, 1, ["a"]))
        for kwargs in ({"direction": "both"}, {"max_hops": True}, {"limit": 0}, {"predicates": ["not-a-predicate"]}, {"grades": ["probably"]}):
            with self.assertRaises(ValueError):
                relations.traverse(g, "a", **kwargs)
        with self.assertRaises(ValueError):
            relations.traverse(g, "missing")

    def test_random_shortest_paths_match_independent_simple_path_enumeration(self):
        rng = random.Random(912)
        for _ in range(30):
            names = list("abcde")
            edges = [edge(a, b, suffix=str(i)) for i, (a, b) in enumerate(itertools.permutations(names, 2)) if rng.random() < .18]
            g = graph(edges, nodes=names)
            adj = {n: set() for n in names}
            for e in edges:
                adj[e["from"]].add(e["to"])
            for source, target in itertools.permutations(names, 2):
                paths = []
                for hops in range(1, 4):
                    for middle in itertools.permutations([n for n in names if n not in {source, target}], hops - 1):
                        chain = [source, *middle, target]
                        if all(b in adj[a] for a, b in zip(chain, chain[1:])):
                            paths.append(chain)
                    if paths:
                        break
                result = relations.traverse(g, source, target, limit=50)
                self.assertEqual(result["total_paths"], len(paths))
                self.assertEqual([p["nodes"] for p in result["paths"]], sorted(paths))

    def test_mutated_graph_contract_is_refused_before_traversal(self):
        original = graph([edge("a", "b")])
        mutations = [
            lambda g: g.update(schema_version=True),
            lambda g: g.update(snapshot_digest=""),
            lambda g: g["nodes"].append({"id": "a"}),
            lambda g: g["records"].append(copy.deepcopy(g["records"][0])),
            lambda g: g["records"][0].update(directed="false"),
            lambda g: g["records"][0].update(to="missing"),
            lambda g: g["records"][0].update(evidence=[]),
            lambda g: g["records"][0].update(predicate="unknown"),
            lambda g: g["predicates"]["declares_use"].update(directed=False),
        ]
        for mutate in mutations:
            g = copy.deepcopy(original)
            mutate(g)
            with self.assertRaises(ValueError):
                relations.traverse(g, "a", "b")


if __name__ == "__main__":
    unittest.main()
