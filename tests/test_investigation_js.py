"""Cross-runtime replay parity: Python and browser JS must hash the same intent."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from x3dray import investigation as inv

JS = Path(__file__).resolve().parents[1] / "x3dray" / "proof_web" / "investigation.js"
FIXTURE, ANALYSIS = "a" * 64, "b" * 64
CATALOG = {"entities": ["alpha", "中文.py", "😀.x3d", "e\u0301", "é"],
           "relations": ["alpha-uses-scene"], "mutations": ["move-node"],
           "findings": ["position-mismatch"], "predicates": ["uses", "\ue000", "😀"]}

RUNNER = """
import fs from 'node:fs';
import {webcrypto} from 'node:crypto';
Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const source=fs.readFileSync(input.source,'utf8');
const api=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const output=[];
for(const q of input.tasks){
 try{
  let value;
  if(q.op==='new')value=await api.newLog(q.fixture,q.analysis);
  if(q.op==='chain'){
   let log=await api.newLog(q.fixture,q.analysis);
   for(const event of q.events)log=await api.appendEvent(log,event,q.catalog);
   value={log,state:await api.replay(log,q.catalog,q.fixture,q.analysis)};
  }
  if(q.op==='replay')value=await api.replay(q.log,q.catalog,q.fixture,q.analysis);
  if(q.op==='append'){
   const before=JSON.stringify(q.log),eventBefore=JSON.stringify(q.event);
   const log=await api.appendEvent(q.log,q.event,q.catalog);
   value={log,logUnchanged:before===JSON.stringify(q.log),eventUnchanged:eventBefore===JSON.stringify(q.event)};
  }
  if(q.op==='async_snapshot'){
   const pending=api.appendEvent(q.log,q.event,q.catalog);
   q.event.id='alpha';q.catalog.entities.length=0;
   value=await pending;
  }
  if(q.op==='accessor'){
   let touched=false;const event={};Object.defineProperty(event,'action',{enumerable:true,get(){touched=true;return 'reset';}});
   try{await api.appendEvent(q.log,event,q.catalog);}catch(error){value={code:error.code,touched};}
  }
  if(q.op==='sparse'){
   const catalog={...q.catalog,entities:new Array(1)};
   value=await api.replay(q.log,catalog,q.fixture,q.analysis);
  }
  if(q.op==='no_crypto'){
   Object.defineProperty(globalThis,'crypto',{value:undefined,configurable:true});
   try{await api.newLog(q.fixture,q.analysis);}finally{Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});}
  }
  output.push({ok:true,value});
 }catch(error){output.push({ok:false,code:error.code||error.name});}
}
process.stdout.write(JSON.stringify(output));
"""


@unittest.skipUnless(shutil.which("node"), "Node is required for browser-twin parity")
class BrowserInvestigationParity(unittest.TestCase):
    def js(self, tasks):
        result = subprocess.run(["node", "--input-type=module", "-e", RUNNER],
                                input=json.dumps({"source": str(JS), "tasks": tasks}, ensure_ascii=True),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def task(self, op, **kwargs):
        return {"op": op, "fixture": FIXTURE, "analysis": ANALYSIS, "catalog": copy.deepcopy(CATALOG), **kwargs}

    def python_chain(self, events):
        log = inv.new_log(FIXTURE, ANALYSIS)
        for event in events:
            log = inv.append_event(log, event, CATALOG)
        return {"log": log, "state": inv.replay(log, CATALOG, FIXTURE, ANALYSIS)}

    def test_genesis_and_all_event_hashes_match_python(self):
        events = [{"action": "select_entity", "id": "中文.py"},
                  {"action": "set_query", "query": {"from_entity": "alpha", "to_entity": "😀.x3d"}},
                  {"action": "set_lens", "lens": {"predicates": ["😀", "\ue000", "uses"], "direction": "incoming", "max_hops": 6, "grade": "L"}},
                  {"action": "select_relation", "id": "alpha-uses-scene"},
                  {"action": "run_mutation", "id": "move-node"},
                  {"action": "inspect_finding", "id": "position-mismatch"}, {"action": "reset"}]
        expected = self.python_chain(events)
        genesis, actual = self.js([self.task("new"), self.task("chain", events=events)])
        self.assertEqual(genesis, {"ok": True, "value": inv.new_log(FIXTURE, ANALYSIS)})
        self.assertEqual(actual, {"ok": True, "value": expected})
        self.assertEqual(expected["log"]["events"][2]["event"]["lens"]["predicates"], ["uses", "\ue000", "😀"])

    def test_both_runtimes_replay_each_others_unicode_logs(self):
        for ident in ["😀.x3d", "e\u0301", "é"]:
            with self.subTest(id=ident):
                expected = self.python_chain([{"action": "select_entity", "id": ident}])
                result = self.js([self.task("replay", log=expected["log"]),
                                  self.task("chain", events=[{"action": "select_entity", "id": ident}])])
                self.assertEqual(result[0]["value"], expected["state"])
                self.assertEqual(inv.replay(result[1]["value"]["log"], CATALOG, FIXTURE, ANALYSIS), expected["state"])

    def test_corrupted_payload_head_and_context_are_rejected(self):
        good = self.python_chain([{"action": "select_entity", "id": "alpha"}])["log"]
        bad = copy.deepcopy(good); bad["events"][0]["event"]["id"] = "中文.py"
        head = copy.deepcopy(good); head["head"] = "c" * 64
        results = self.js([self.task("replay", log=bad), self.task("replay", log=head),
                           self.task("replay", log=good, analysis="c" * 64)])
        self.assertEqual([r["code"] for r in results], ["event_digest_mismatch", "head_mismatch", "context_mismatch"])

    def test_unknown_actions_ids_and_ill_typed_lenses_match_rejection(self):
        base = inv.new_log(FIXTURE, ANALYSIS)
        events = [{"action": "execute", "url": "https://example.invalid"},
                  {"action": "select_entity", "id": "unknown"},
                  {"action": "reset", "claim": "proven"},
                  {"action": "set_lens", "lens": {"predicates": [], "direction": "outgoing", "max_hops": True, "grade": "all"}},
                  {"action": "set_lens", "lens": {"predicates": [], "direction": "both", "max_hops": 3, "grade": "all"}}]
        expected = []
        for event in events:
            with self.assertRaises(inv.InvestigationError) as err:
                inv.append_event(base, event, CATALOG)
            expected.append(err.exception.code)
        actual = self.js([self.task("append", log=base, event=event) for event in events])
        self.assertEqual([r["code"] for r in actual], expected)

    def test_unpaired_surrogates_and_control_characters_are_refused(self):
        base = inv.new_log(FIXTURE, ANALYSIS)
        tasks = []
        for ident in ["\ud800", "\udc00", "line\nname", "\x7f"]:
            catalog = copy.deepcopy(CATALOG); catalog["entities"] = [ident]
            tasks.append(self.task("replay", log=base, catalog=catalog))
        self.assertEqual([r["code"] for r in self.js(tasks)], ["invalid_catalog_id"] * len(tasks))

    def test_identifier_bound_counts_codepoints_not_utf16_units(self):
        base = inv.new_log(FIXTURE, ANALYSIS)
        okay, too_long = "😀" * 256, "😀" * 257
        catalog = copy.deepcopy(CATALOG); catalog["entities"] = [okay]
        expected = inv.append_event(base, {"action": "select_entity", "id": okay}, catalog)
        bad = copy.deepcopy(CATALOG); bad["entities"] = [too_long]
        a, b = self.js([self.task("append", log=base, event={"action": "select_entity", "id": okay}, catalog=catalog),
                        self.task("replay", log=base, catalog=bad)])
        self.assertEqual(a["value"]["log"], expected)
        self.assertEqual(b["code"], "invalid_catalog_id")

    def test_byte_budget_is_utf8_and_catalog_and_event_caps_are_enforced(self):
        base = inv.new_log(FIXTURE, ANALYSIS)
        catalog = copy.deepcopy(CATALOG); catalog["predicates"] = ["漢" * 240 + str(i) for i in range(24)]
        event = {"action": "set_lens", "lens": {"predicates": catalog["predicates"], "direction": "outgoing", "max_hops": 3, "grade": "all"}}
        large = copy.deepcopy(CATALOG); large["entities"] = [str(i) for i in range(10001)]
        bad_log = copy.deepcopy(base); bad_log["events"] = [None] * 257
        results = self.js([self.task("append", log=base, event=event, catalog=catalog),
                           self.task("replay", log=base, catalog=large), self.task("replay", log=bad_log)])
        self.assertEqual([r["code"] for r in results], ["event_byte_limit", "invalid_catalog_ids", "event_count_limit"])

    def test_append_is_pure_and_snapshots_inputs_before_async_hashing(self):
        base = inv.new_log(FIXTURE, ANALYSIS); event = {"action": "select_entity", "id": "中文.py"}
        pure, asynchronous = self.js([self.task("append", log=base, event=event),
                                      self.task("async_snapshot", log=base, event=event)])
        self.assertTrue(pure["value"]["logUnchanged"])
        self.assertTrue(pure["value"]["eventUnchanged"])
        self.assertEqual(asynchronous["value"], inv.append_event(base, event, CATALOG))

    def test_accessors_and_sparse_arrays_do_not_trigger_coercion_or_code(self):
        base = inv.new_log(FIXTURE, ANALYSIS)
        accessor, sparse = self.js([self.task("accessor", log=base), self.task("sparse", log=base)])
        self.assertEqual(accessor["value"], {"code": "invalid_event_shape", "touched": False})
        self.assertEqual(sparse["code"], "invalid_catalog_ids")

    def test_crypto_unavailability_is_explicit(self):
        result = self.js([self.task("no_crypto")])[0]
        self.assertEqual(result, {"ok": False, "code": "crypto_unavailable"})

    def test_maximum_valid_history_matches_python_then_refuses_append(self):
        expected = self.python_chain([{"action": "reset"}] * inv.MAX_EVENTS)
        valid, too_many = self.js([self.task("replay", log=expected["log"]),
                                   self.task("append", log=expected["log"], event={"action": "reset"})])
        self.assertEqual(valid, {"ok": True, "value": expected["state"]})
        self.assertEqual(too_many["code"], "event_count_limit")

    def test_complete_log_byte_budget_matches_python(self):
        catalog = copy.deepcopy(CATALOG)
        catalog["predicates"] = ["p" * 240 + str(i) for i in range(32)]
        event = {"action": "set_lens", "lens": {"predicates": catalog["predicates"], "direction": "outgoing", "max_hops": 3, "grade": "all"}}
        log = inv.new_log(FIXTURE, ANALYSIS)
        for _ in range(inv.MAX_EVENTS):
            try:
                log = inv.append_event(log, event, catalog)
            except inv.InvestigationError as error:
                self.assertEqual(error.code, "log_byte_limit")
                break
        else:
            self.fail("Expected whole-log byte budget to bind")
        good, over = self.js([self.task("replay", log=log, catalog=catalog),
                              self.task("append", log=log, event=event, catalog=catalog)])
        self.assertEqual(good["value"], inv.replay(log, catalog, FIXTURE, ANALYSIS))
        self.assertEqual(over["code"], "log_byte_limit")

    def test_query_endpoints_including_zero_hops_match_python(self):
        for query in [{"from_entity": "alpha", "to_entity": "中文.py"}, {"from_entity": "😀.x3d", "to_entity": "😀.x3d"}]:
            events = [{"action": "set_query", "query": query}]
            expected = self.python_chain(events)
            result = self.js([self.task("chain", events=events)])[0]
            self.assertEqual(result, {"ok": True, "value": expected})
            self.assertEqual(result["value"]["state"]["query"], query)

    def test_query_shape_and_unknown_entity_rejections_match_python(self):
        base = inv.new_log(FIXTURE, ANALYSIS)
        queries = [{}, {"from_entity": "alpha"}, {"from_entity": "alpha", "to_entity": "missing"},
                   {"from_entity": True, "to_entity": "中文.py"},
                   {"from_entity": "alpha", "to_entity": "中文.py", "run": "script"}]
        expected = []
        for query in queries:
            with self.assertRaises(inv.InvestigationError) as err:
                inv.append_event(base, {"action": "set_query", "query": query}, CATALOG)
            expected.append(err.exception.code)
        actual = self.js([self.task("append", log=base, event={"action": "set_query", "query": query}) for query in queries])
        self.assertEqual([r["code"] for r in actual], expected)


if __name__ == "__main__":
    unittest.main()
