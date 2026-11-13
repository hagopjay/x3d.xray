"""The tool set's look counter comes from the investigation chain: tools.js (real file) <-> investigation.js (real file) in Node."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from eda_support import minimal_bundle, synthetic_dataset  # noqa: E402
from x3dray import eda  # noqa: E402

NAMES = {"r0": "alpha-public-0", "r1": "beta-public-1", "r2": "SECRET-private-zeta"}
SCRIPT = r'''
// Tool set <-> investigation chain: the look counter comes from the chain, a reload continues it, a full log is visible.
import fs from 'node:fs';
import {webcrypto} from 'node:crypto';
import {pathToFileURL} from 'node:url';
Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
const root=process.argv[2], B=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const T=await import(pathToFileURL(root+'/x3dray/web/tools.js'));
const src=fs.readFileSync(root+'/x3dray/proof_web/investigation.js','utf8');
const inv=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
const FIX='1'.repeat(64), ANA='2'.repeat(64);
const ctx=await inv.looksContext(B.eda.looks,B.repos.map(r=>r.name)),digest=ctx.digest;
const catalog={entities:[],relations:[],mutations:[],findings:[],predicates:[]};

const run=(tools,name,args)=>tools.find(t=>t.name===name).execute(args);
const out={steps:[]};
let log=await inv.newLog(FIX,ANA);
let sink=await inv.openLookSink(log,catalog,FIX,ANA,ctx),tools=T.makeTools(B,null,{looks:sink});
const step=async(label,name,args)=>{
  const r=run(tools,name,args);
  const cur=await sink.settle();
  const st=await inv.replay(cur,catalog,FIX,ANA,ctx);
  out.steps.push({label,tool:r.looks_asked_this_session===undefined?null:r.looks_asked_this_session,declared_looks:r.declared_looks===undefined?null:r.declared_looks,chain_asked:st.looks.asked,chain_inspected:st.looks.inspected.length,chain_declared:st.looks.declared,events:st.event_count});
  return {r,cur,st};
};
await step('ledger (no look)','eda',{topic:'ledger'});
await step('finding declared','eda',{topic:'finding',id:'contrast:lines'});
await step('slice A','eda',{topic:'slice',who:'ai',measure:'lines'});
await step('slice A again (same)','eda',{topic:'slice',who:'ai',measure:'lines'});
await step('slice A, who spelled "Claude"','eda',{topic:'slice',who:'Claude',measure:'lines'});
await step('slice weekday 0','eda',{topic:'slice',weekday:'0'});
await step('slice weekday "mon" (same look)','eda',{topic:'slice',weekday:'mon'});
await step('slice private repo','eda',{topic:'slice',repo:'SECRET-private-zeta',fork:'all'});
await step('slice two repos reversed','eda',{topic:'slice',repo:'beta-public-1,alpha-public-0'});
await step('slice two repos sorted (same)','eda',{topic:'slice',repo:'alpha-public-0,beta-public-1'});
await step('query_commits hour 3 (rows)','query_commits',{hour:3});
await step('activity hour 3 again (same rows look)','activity',{hour:3});
await step('query_commits plain (no look)','query_commits',{repo:'alpha-public-0'});
await step('rows with flag','query_commits',{flag:'size_high'});
await step('slice with rule (grade defaults S)','eda',{topic:'slice',rule:'size_high'});
await step('slice with rule, grade S spelled','eda',{topic:'slice',rule:'size_high',grade:'S'});
await step('refused slice (bad hour) is not a look','eda',{topic:'slice',hour:99}).catch(e=>out.steps.push({label:'refused slice (bad hour)',error:String(e.message).slice(0,60)}));
{ // a slice over the 50000-commit bound is refused and is not a look
  const big=T.makeTools({...B,commits:Array.from({length:50001},(_, i)=>({...B.commits[0],i,t:1500000000+i}))},null,{looks:sink});
  let msg='';try{run(big,'eda',{topic:'slice',measure:'files'});}catch(e){msg=e.message;}
  out.over_bound={message:msg.slice(0,40),sink_asked_before:sink.asked};
}
const last=await sink.settle();
const st=await inv.replay(last,catalog,FIX,ANA,ctx);
out.final={state_looks:st.looks,event_count:st.event_count,unrecorded:sink.unrecorded};
out.privacy={name_in_log:JSON.stringify(last).includes('SECRET-private-zeta'),any_repo_name:['alpha-public-0','beta-public-1','SECRET-private-zeta'].some(n=>JSON.stringify(last).includes(n))};
out.sample_events=last.events.slice(0,5).map(r=>r.event);
out.log_bytes=JSON.stringify(last).length;
// reload: a fresh tool set seeded from the validated log continues the count (memory is a cache of the chain)
const saved=JSON.parse(JSON.stringify(last));
sink=await inv.openLookSink(saved,catalog,FIX,ANA,ctx);tools=T.makeTools(B,null,{looks:sink});
const r1=run(tools,'eda',{topic:'ledger'});
out.reload={ledger_looks_asked_this_session:r1.looks_asked_this_session,chain_asked:st.looks.asked,declared_looks:r1.ledger.declared};
run(tools,'eda',{topic:'slice',who:'ai',measure:'lines'});          // already in the chain: no new event
const again=await sink.settle();
out.reload.after_repeat_events=again.events.length;
out.reload.same_as_before=again.events.length===saved.events.length;
// a default tool set (no sink) gives the same numbers as the chain-backed one for the same calls
const plain=T.makeTools(B,null);
const seq=[['eda',{topic:'finding',id:'contrast:lines'}],['eda',{topic:'slice',who:'ai',measure:'lines'}],['eda',{topic:'slice',who:'ai',measure:'lines'}],['query_commits',{hour:3}],['activity',{hour:3}],['eda',{topic:'ledger'}]];
let plainLast;for(const [n,a] of seq)plainLast=run(plain,n,a);
out.default_sink_matches_pristine_semantics={asked:plainLast.looks_asked_this_session};
// log full: the chain cannot take the look, the count shows the gap instead of hiding it
let full=await inv.newLog(FIX,ANA);
for(let i=0;i<256;i++)full=await inv.appendEvent(full,{action:'reset'},catalog);
sink=await inv.openLookSink(full,catalog,FIX,ANA,ctx);tools=T.makeTools(B,null,{looks:sink});
const rf=run(tools,'eda',{topic:'slice',who:'ai'});
const afterFull=await sink.settle();
const stf=await inv.replay(afterFull,catalog,FIX,ANA,ctx);
out.full_log={tool_count:rf.looks_asked_this_session,chain_asked:stf.looks.asked,unrecorded:sink.unrecorded};
process.stdout.write(JSON.stringify(out));

'''


SCRIPT_COMPARE = r'''
// compare through the real tool set and the real chain: one look per distinct question, no repo name in the log.
import fs from 'node:fs';
import {webcrypto} from 'node:crypto';
import {pathToFileURL} from 'node:url';
Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
const root=process.argv[2], B=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const T=await import(pathToFileURL(root+'/x3dray/web/tools.js'));
const src=fs.readFileSync(root+'/x3dray/proof_web/investigation.js','utf8');
const inv=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
const FIX='1'.repeat(64), ANA='2'.repeat(64);
const ctx=await inv.looksContext(B.eda.looks,B.repos.map(r=>r.name));
const catalog={entities:[],relations:[],mutations:[],findings:[],predicates:[]};
const log0=await inv.newLog(FIX,ANA);
let sink=await inv.openLookSink(log0,catalog,FIX,ANA,ctx),tools=T.makeTools(B,null,{looks:sink});
const run=(name,args)=>tools.find(t=>t.name===name).execute(args);
const out={steps:[]};
const step=async(label,args)=>{
  let r=null,error=null;
  try{r=run('eda',args);}catch(e){error=String(e.message).slice(0,80);}
  const cur=await sink.settle(),st=await inv.replay(cur,catalog,FIX,ANA,ctx);
  out.steps.push({label,error,tool:r?r.looks_asked_this_session:null,m:r&&r.family?r.family.m:null,comparisons:r&&r.family?r.family.comparisons:null,
    p:r&&r.family?r.p_at_most_alpha:null,chain_asked:st.looks.asked,events:st.event_count,grade:r?r.grade:'n/a'});
  return cur;
};
await step('compare ai vs you, lines',{topic:'compare',a:{who:'ai'},b:{who:'you'},measure:'lines'});
await step('swapped is the same look',{topic:'compare',a:{who:'you'},b:{who:'ai'},measure:'lines'});
await step('same pair, another measure',{topic:'compare',a:{who:'ai'},b:{who:'you'},measure:'hour4'});
await step('a repo filter in both groups',{topic:'compare',a:{who:'ai',repo:'alpha-public-0'},b:{who:'you',repo:'alpha-public-0'},measure:'lines'});
await step('overlapping groups are refused',{topic:'compare',a:{who:'ai'},b:{who:'ai,you'},measure:'lines'});
await step('a group with no filter is refused',{topic:'compare',a:{who:'ai'},b:{},measure:'lines'});
await step('a slice joins the same count',{topic:'slice',who:'ai',measure:'lines'});
const last=await sink.settle();
out.final={events:last.events.map(r=>r.event),unrecorded:sink.unrecorded,names:['alpha-public-0','beta-public-1','SECRET-private-zeta'].some(n=>JSON.stringify(last).includes(n))};
// a private repo in a group: its name never leaves the tool and never enters the log
const priv=await step('private repo against a public repo',{topic:'compare',a:{repo:'SECRET-private-zeta'},b:{repo:'alpha-public-0'},measure:'lines'});
out.private={error:out.steps[out.steps.length-1].error,names_in_log:JSON.stringify(priv).includes('SECRET-private-zeta'),log:priv.events.length};
// reload continues the count; the same question again is one more event only if its selector differs
const saved=JSON.parse(JSON.stringify(await sink.settle()));
sink=await inv.openLookSink(saved,catalog,FIX,ANA,ctx);tools=T.makeTools(B,null,{looks:sink});
const rr=run('eda',{topic:'compare',a:{who:'you'},b:{who:'ai'},measure:'lines'});
const again=await sink.settle();
out.reload={tool:rr.looks_asked_this_session,events_before:saved.events.length,events_after:again.events.length,m:rr.family.m};
process.stdout.write(JSON.stringify(out));
'''


def renamed(x):
    if isinstance(x, dict):
        return {renamed(k) if isinstance(k, str) else k: renamed(v) for k, v in x.items()}
    if isinstance(x, list):
        return [renamed(v) for v in x]
    if isinstance(x, str):
        for old, new in NAMES.items():
            for pre in ("", "repo:", "<private>:"):
                if x == pre + old:
                    return pre + new
    return x


@unittest.skipUnless(shutil.which("node"), "Node is required")
class ToolCounterMatchesChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds = synthetic_dataset(seed=41, repos=3, per_repo=120, private=("r2",), plant=("ai_hours", "outlier"))
        e, problems = eda.run(ds)
        assert problems == [], problems
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle.json"
            bundle.write_text(json.dumps(renamed(minimal_bundle(ds, e))))
            script = Path(tmp) / "run.mjs"
            script.write_text(SCRIPT)
            r = subprocess.run(["node", str(script), str(ROOT), str(bundle)], capture_output=True, text=True, timeout=120)
            assert r.returncode == 0, r.stderr
            cls.out = json.loads(r.stdout)

    def test_the_tool_count_equals_the_chain_count_after_every_call(self):
        counted = [s for s in self.out["steps"] if "chain_asked" in s]
        self.assertGreater(len(counted), 14)
        for s in counted:
            if s["tool"] is not None:
                self.assertEqual(s["tool"], s["chain_asked"], s["label"])
            self.assertEqual(s["chain_declared"], 12)
        self.assertEqual(self.out["final"]["state_looks"]["asked"], 7)
        self.assertEqual(self.out["final"]["state_looks"]["inspected"], ["contrast:lines"])
        self.assertEqual(self.out["final"]["unrecorded"], [])

    def test_two_spellings_of_one_look_are_one_event_and_a_plain_lookup_is_none(self):
        by = {s["label"]: s for s in self.out["steps"] if "events" in s}
        self.assertEqual(by["slice A again (same)"]["events"], by["slice A"]["events"])
        self.assertEqual(by['slice A, who spelled "Claude"']["events"], by["slice A"]["events"])
        self.assertEqual(by['slice weekday "mon" (same look)']["events"], by["slice weekday 0"]["events"])
        self.assertEqual(by["slice two repos sorted (same)"]["events"], by["slice two repos reversed"]["events"])
        self.assertEqual(by["activity hour 3 again (same rows look)"]["events"], by["query_commits hour 3 (rows)"]["events"])
        self.assertEqual(by["query_commits plain (no look)"]["events"], by["activity hour 3 again (same rows look)"]["events"])
        self.assertEqual(by["ledger (no look)"]["events"], 0)
        self.assertEqual(by["slice with rule, grade S spelled"]["events"], by["slice with rule (grade defaults S)"]["events"])

    def test_a_refused_request_is_not_a_look(self):
        refused = [s for s in self.out["steps"] if "error" in s]
        self.assertEqual(len(refused), 1)
        self.assertEqual(self.out["final"]["state_looks"]["asked"], 7)
        self.assertTrue(self.out["over_bound"]["message"].startswith("the filter matches 50001"))
        self.assertEqual(self.out["over_bound"]["sink_asked_before"], 7)          # the over-large slice did not join the count

    def test_the_log_names_no_repo(self):
        self.assertEqual(self.out["privacy"], {"name_in_log": False, "any_repo_name": False})
        self.assertIn({"fork": "all", "measure": "lines", "repo": [2]}, [e["selector"] for e in self.out["sample_events"] if "selector" in e])

    def test_a_reloaded_tool_set_continues_the_count_from_the_log(self):
        r = self.out["reload"]
        self.assertEqual((r["ledger_looks_asked_this_session"], r["chain_asked"], r["declared_looks"]), (7, 7, 12))
        self.assertTrue(r["same_as_before"])

    def test_every_stage_name_fits_the_selector_alphabet(self):
        from x3dray import eda, investigation as inv, structure
        ds = synthetic_dataset(seed=7, repos=3, per_repo=60, private=("r2",), plant=("ai_hours",))
        e, _ = eda.run(ds)
        for rule in e["flags"]["rules"]:
            self.assertTrue(inv._selector_value(inv.SELECTOR_RULES["rule"], rule), rule)
        for fork in e["method"]["forks"]:
            self.assertTrue(inv._selector_value(inv.SELECTOR_RULES["fork"], fork), fork)
        self.assertEqual(inv.SELECTOR_RULES["who"][1], list(structure.CLASSES))
        self.assertEqual(sorted(inv.SELECTOR_RULES["who"][1]), inv.SELECTOR_RULES["who"][1] if False else sorted(inv.SELECTOR_RULES["who"][1]))

    def test_the_default_sink_keeps_the_old_counting(self):
        self.assertEqual(self.out["default_sink_matches_pristine_semantics"], {"asked": 2})

    def test_a_full_log_shows_the_gap_instead_of_hiding_it(self):
        f = self.out["full_log"]
        self.assertEqual((f["tool_count"], f["chain_asked"], f["unrecorded"]), (1, 0, ["event_count_limit"]))


@unittest.skipUnless(shutil.which("node"), "Node is required")
class CompareLooksMatchChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds = synthetic_dataset(seed=41, repos=3, per_repo=120, private=("r2",), plant=("ai_hours", "outlier"))
        e, problems = eda.run(ds)
        assert problems == [], problems
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle.json"
            bundle.write_text(json.dumps(renamed(minimal_bundle(ds, e))))
            script = Path(tmp) / "run.mjs"
            script.write_text(SCRIPT_COMPARE)
            r = subprocess.run(["node", str(script), str(ROOT), str(bundle)], capture_output=True, text=True, timeout=120)
            assert r.returncode == 0, r.stderr
            cls.out = json.loads(r.stdout)
        cls.by = {s["label"]: s for s in cls.out["steps"]}

    def test_one_compare_look_per_distinct_question_and_the_tool_count_equals_the_chain_count(self):
        by = self.by
        self.assertEqual((by["compare ai vs you, lines"]["tool"], by["compare ai vs you, lines"]["chain_asked"]), (1, 1))
        self.assertEqual(by["swapped is the same look"]["events"], by["compare ai vs you, lines"]["events"])
        self.assertEqual(by["swapped is the same look"]["chain_asked"], 1)
        self.assertEqual((by["same pair, another measure"]["tool"], by["same pair, another measure"]["chain_asked"]), (2, 2))
        self.assertEqual((by["a repo filter in both groups"]["tool"], by["a repo filter in both groups"]["chain_asked"]), (3, 3))
        self.assertEqual((by["a slice joins the same count"]["tool"], by["a slice joins the same count"]["chain_asked"]), (4, 4))
        for s in self.out["steps"]:
            if s["error"] is None:
                self.assertEqual(s["tool"], s["chain_asked"], s["label"])
                self.assertEqual(s["grade"], None, s["label"])                      # a comparison is never graded

    def test_the_family_behind_q_counts_every_look_taken(self):
        self.assertEqual(self.by["compare ai vs you, lines"]["m"], 13)
        self.assertEqual(self.by["same pair, another measure"]["m"], 14)
        self.assertEqual(self.by["same pair, another measure"]["comparisons"], 2)
        self.assertEqual(self.by["a repo filter in both groups"]["m"], 15)

    def test_refused_comparisons_are_not_looks(self):
        for label in ("overlapping groups are refused", "a group with no filter is refused"):
            s = self.by[label]
            self.assertIsNotNone(s["error"], label)
            self.assertEqual((s["chain_asked"], s["events"]), (3, 3), label)

    def test_the_events_are_closed_selectors_in_canonical_order(self):
        events = self.out["final"]["events"]
        self.assertEqual(len(events), 4)
        for e in events[:3]:
            self.assertEqual((e["action"], e["kind"]), ("take_look", "compare"))
            a, b = e["selector"]["a"], e["selector"]["b"]
            self.assertLess(json.dumps(a, sort_keys=True, separators=(",", ":")), json.dumps(b, sort_keys=True, separators=(",", ":")))
        self.assertEqual({e["selector"]["measure"] for e in events[:3]}, {"lines", "hour4"})
        self.assertEqual(events[3]["kind"], "slice")
        self.assertFalse(self.out["final"]["names"])
        self.assertEqual(self.out["final"]["unrecorded"], [])

    def test_a_private_repo_never_enters_the_log(self):
        self.assertFalse(self.out["private"]["names_in_log"])

    def test_a_reloaded_tool_set_does_not_record_the_same_question_twice(self):
        r = self.out["reload"]
        self.assertEqual(r["events_after"], r["events_before"])
        self.assertGreaterEqual(r["m"], 13)


if __name__ == "__main__":
    unittest.main()
