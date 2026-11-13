"""Intent replay must bind context, preserve history and refuse executable input."""
import copy
import hashlib
import json
import unittest

from x3dray import investigation as inv

FIXTURE = "a" * 64
ANALYSIS = "b" * 64
CATALOG = {"entities": ["alpha", "beta", "中文.py"], "relations": ["alpha-uses-beta"],
           "mutations": ["move-node", "alter-edge"], "findings": ["node-position-mismatch"],
           "predicates": ["declares_use", "shares_blob"]}


def lens(**overrides):
    return {"action": "set_lens", "lens": {"predicates": ["shares_blob", "declares_use"],
            "direction": "outgoing", "max_hops": 4, "grade": "C", **overrides}}


class InvestigationTests(unittest.TestCase):
    def replay(self, log, catalog=None):
        return inv.replay(log, catalog or CATALOG, FIXTURE, ANALYSIS)

    def test_empty_log_has_reproducible_context_bound_identity(self):
        a, b = inv.new_log(FIXTURE, ANALYSIS), inv.new_log(FIXTURE, ANALYSIS)
        self.assertEqual(a, b)
        self.assertNotEqual(a["head"], inv.new_log(FIXTURE, "c" * 64)["head"])
        self.assertEqual(self.replay(a)["event_count"], 0)

    def test_append_is_pure_and_selection_is_single(self):
        original = inv.new_log(FIXTURE, ANALYSIS)
        event = {"action": "select_entity", "id": "中文.py"}
        log = inv.append_event(original, event, CATALOG)
        event["id"] = "beta"
        self.assertEqual(original["events"], [])
        self.assertEqual(self.replay(log)["selected_entity"], "中文.py")
        updated = inv.append_event(log, {"action": "select_relation", "id": "alpha-uses-beta"}, CATALOG)
        state = self.replay(updated)
        self.assertEqual(state["selected_relation"], "alpha-uses-beta")
        self.assertIsNone(state["selected_entity"])
        self.assertEqual(self.replay(log)["selected_entity"], "中文.py")

    def test_replay_all_actions_and_lens_change_clears_stale_diagnostic_intent(self):
        log = inv.new_log(FIXTURE, ANALYSIS)
        for event in [lens(), {"action": "run_mutation", "id": "move-node"},
                      {"action": "inspect_finding", "id": "node-position-mismatch"}]:
            log = inv.append_event(log, event, CATALOG)
        state = self.replay(log)
        self.assertEqual(state["requested_mutation"], "move-node")
        self.assertEqual(state["selected_finding"], "node-position-mismatch")
        state = self.replay(inv.append_event(log, lens(direction="incoming"), CATALOG))
        self.assertIsNone(state["requested_mutation"])
        self.assertIsNone(state["selected_finding"])
        self.assertEqual(state["lens"]["direction"], "incoming")

    def test_reset_restores_view_without_erasing_history(self):
        base = inv.new_log(FIXTURE, ANALYSIS)
        log = inv.append_event(base, {"action": "select_entity", "id": "alpha"}, CATALOG)
        log = inv.append_event(log, {"action": "reset"}, CATALOG)
        state = self.replay(log)
        self.assertIsNone(state["selected_entity"])
        self.assertEqual(state["lens"], inv.DEFAULT_LENS)
        self.assertEqual(state["event_count"], 2)
        self.assertNotEqual(state["state_digest"], self.replay(base)["state_digest"])

    def test_fixture_and_analysis_mismatches_are_refused(self):
        log = inv.new_log(FIXTURE, ANALYSIS)
        for f, a in [("c" * 64, ANALYSIS), (FIXTURE, "c" * 64)]:
            with self.subTest(f=f, a=a), self.assertRaisesRegex(inv.InvestigationError, "context_mismatch"):
                inv.replay(log, CATALOG, f, a)

    def test_chain_matches_independent_canonical_hash_definition(self):
        log = inv.append_event(inv.new_log(FIXTURE, ANALYSIS), {"action": "select_entity", "id": "alpha"}, CATALOG)
        record = log["events"][0]
        raw = {"domain": "x3dray.investigation.event.v1", "seq": 1,
               "previous": record["previous"], "event": {"action": "select_entity", "id": "alpha"}}
        digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(record["digest"], digest)
        self.assertEqual(log["head"], digest)

    def test_payload_head_sequence_reordering_and_removal_tampering_are_detected(self):
        log = inv.new_log(FIXTURE, ANALYSIS)
        for ident in ["alpha", "beta"]:
            log = inv.append_event(log, {"action": "select_entity", "id": ident}, CATALOG)
        variants = []
        altered = copy.deepcopy(log); altered["events"][0]["event"]["id"] = "beta"; variants.append(altered)
        altered = copy.deepcopy(log); altered["head"] = "c" * 64; variants.append(altered)
        altered = copy.deepcopy(log); altered["events"][0]["seq"] = 2; variants.append(altered)
        altered = copy.deepcopy(log); altered["events"].reverse(); variants.append(altered)
        altered = copy.deepcopy(log); altered["events"].pop(); variants.append(altered)
        for altered in variants:
            with self.subTest(altered=altered), self.assertRaises(inv.InvestigationError):
                self.replay(altered)

    def test_catalog_validation_still_applies_after_a_valid_hash_chain(self):
        log = inv.append_event(inv.new_log(FIXTURE, ANALYSIS), {"action": "select_entity", "id": "alpha"}, CATALOG)
        narrowed = copy.deepcopy(CATALOG)
        narrowed["entities"].remove("alpha")
        with self.assertRaisesRegex(inv.InvestigationError, "unknown_entit"):
            self.replay(log, narrowed)

    def test_predicate_set_order_is_explicitly_canonical_and_duplicates_fail(self):
        a = inv.append_event(inv.new_log(FIXTURE, ANALYSIS), lens(), CATALOG)
        b = inv.append_event(inv.new_log(FIXTURE, ANALYSIS), lens(predicates=["declares_use", "shares_blob"]), CATALOG)
        self.assertEqual(a, b)
        with self.assertRaisesRegex(inv.InvestigationError, "duplicate_predicate"):
            inv.append_event(a, lens(predicates=["shares_blob", "shares_blob"]), CATALOG)

    def test_unknown_ids_actions_and_executable_fields_are_refused_without_echo(self):
        log = inv.new_log(FIXTURE, ANALYSIS)
        bad = [{"action": "select_entity", "id": "not-in-catalog"},
               {"action": "run_mutation", "id": "https://example.invalid/run"},
               {"action": "eval", "script": "throw private_canary"},
               {"action": "reset", "url": "https://example.invalid"},
               {"action": "select_relation", "id": "alpha-uses-beta", "claim": "proven correct"}]
        for event in bad:
            with self.subTest(event=event), self.assertRaises(inv.InvestigationError) as caught:
                inv.append_event(log, event, CATALOG)
            self.assertNotIn("private_canary", str(caught.exception))
            self.assertNotIn("example.invalid", str(caught.exception))

    def test_wrong_types_and_legacy_shapes_are_not_coerced(self):
        log = inv.new_log(FIXTURE, ANALYSIS)
        bad = [None, [], {"action": 1}, lens(max_hops=True), lens(max_hops="3"),
               lens(predicates="shares_blob"), lens(direction="both"), lens(grade=["C"])]
        for event in bad:
            with self.subTest(event=event), self.assertRaises(inv.InvestigationError):
                inv.append_event(log, event, CATALOG)
        for altered in [dict(log, schema_version=True), dict(log, schema_version="1"), {"events": []}, dict(log, state={})]:
            with self.subTest(altered=altered), self.assertRaises(inv.InvestigationError):
                self.replay(altered)

    def test_catalog_and_digest_shapes_are_strict(self):
        for digest in ["short", "A" * 64, None, True]:
            with self.subTest(digest=digest), self.assertRaises(inv.InvestigationError):
                inv.new_log(digest, ANALYSIS)
        for bad in [CATALOG | {"unknown": []}, CATALOG | {"entities": "alpha"}, CATALOG | {"entities": ["alpha", "alpha"]},
                    CATALOG | {"entities": ["x\nsecret"]}, CATALOG | {"entities": ["\ud800"]}]:
            with self.subTest(bad=bad), self.assertRaises(inv.InvestigationError):
                self.replay(inv.new_log(FIXTURE, ANALYSIS), bad)

    def test_maximum_event_count_is_accepted_then_refused(self):
        log = inv.new_log(FIXTURE, ANALYSIS)
        for _ in range(inv.MAX_EVENTS):
            log = inv.append_event(log, {"action": "reset"}, CATALOG)
        self.assertEqual(self.replay(log)["event_count"], inv.MAX_EVENTS)
        with self.assertRaisesRegex(inv.InvestigationError, "event_count_limit"):
            inv.append_event(log, {"action": "reset"}, CATALOG)

    def test_byte_bound_applies_to_multibyte_logs(self):
        predicates = ["漢" * 240 + str(i) for i in range(24)]
        catalog = copy.deepcopy(CATALOG); catalog["predicates"] = predicates
        event = lens(predicates=predicates)
        # Twenty-four large Unicode IDs exceed the event byte budget, although the
        # character counts individually fit their documented bound.
        with self.assertRaisesRegex(inv.InvestigationError, "event_byte_limit"):
            inv.append_event(inv.new_log(FIXTURE, ANALYSIS), event, catalog)

    def test_whole_log_byte_budget_refuses_growth_before_event_count_limit(self):
        catalog = copy.deepcopy(CATALOG)
        catalog["predicates"] = ["p" * 240 + str(i) for i in range(32)]
        event = lens(predicates=catalog["predicates"])
        log = inv.new_log(FIXTURE, ANALYSIS)
        for _ in range(inv.MAX_EVENTS):
            before = copy.deepcopy(log)
            try:
                log = inv.append_event(log, event, catalog)
            except inv.InvestigationError as error:
                self.assertEqual(error.code, "log_byte_limit")
                self.assertEqual(log, before)
                self.assertLess(len(log["events"]), inv.MAX_EVENTS)
                self.assertEqual(self.replay(log, catalog)["event_count"], len(log["events"]))
                break
        else:
            self.fail("Expected whole-log byte limit before maximum event count")

    def test_replayed_state_is_detached_from_mutable_log(self):
        log = inv.append_event(inv.new_log(FIXTURE, ANALYSIS), lens(), CATALOG)
        state = self.replay(log)
        state["lens"]["predicates"].append("invented")
        self.assertEqual(self.replay(log)["lens"]["predicates"], ["declares_use", "shares_blob"])

    def test_query_endpoints_replay_exactly_allow_zero_hops_and_reset(self):
        base = inv.new_log(FIXTURE, ANALYSIS)
        query = {"from_entity": "alpha", "to_entity": "中文.py"}
        log = inv.append_event(base, {"action": "set_query", "query": query}, CATALOG)
        query["to_entity"] = "beta"
        state = self.replay(log)
        self.assertEqual(state["query"], {"from_entity": "alpha", "to_entity": "中文.py"})
        state["query"]["from_entity"] = "beta"
        self.assertEqual(self.replay(log)["query"]["from_entity"], "alpha")
        zero = inv.append_event(log, {"action": "set_query", "query": {"from_entity": "alpha", "to_entity": "alpha"}}, CATALOG)
        self.assertEqual(self.replay(zero)["query"], {"from_entity": "alpha", "to_entity": "alpha"})
        self.assertIsNone(self.replay(inv.append_event(zero, {"action": "reset"}, CATALOG))["query"])

    def test_query_shape_and_catalog_references_are_strict(self):
        base = inv.new_log(FIXTURE, ANALYSIS)
        bad = [{}, {"from_entity": "alpha"}, {"from_entity": "alpha", "to_entity": "beta", "url": "https://example.invalid"},
               {"from_entity": "alpha", "to_entity": "missing"}, {"from_entity": True, "to_entity": "beta"}, None]
        for query in bad:
            with self.subTest(query=query), self.assertRaises(inv.InvestigationError):
                inv.append_event(base, {"action": "set_query", "query": query}, CATALOG)


if __name__ == "__main__":
    unittest.main()
