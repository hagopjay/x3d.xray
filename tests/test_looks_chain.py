"""take_look: Python reference behaviour, Node twin parity (differential vectors), v1 backward compatibility, bounds."""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from x3dray import investigation as inv

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "x3dray" / "proof_web" / "investigation.js"
BASE = os.environ.get("X3D_PRISTINE_ROOT")          # a copy of the tree before this change (scratch only)
FIXTURE, ANALYSIS = "a" * 64, "b" * 64
D = "c" * 64
LOOK_IDS = ["contrast:lines", "drift:first:hour4", "\u4e2d\u6587:\u3053", "\U0001f600:x", "e\u0301", "\u00e9", "\ue000", "a b", "q\"uote\\",
            "\u2028", "\uffff", "\U0010ffff", "\u0085"]
REPOS = ["r0", "r1", "r2"]
CATALOG = {"entities": ["alpha"], "relations": [], "mutations": [], "findings": [], "predicates": []}
LEGACY = CATALOG
CTX = {"looks": LOOK_IDS, "digest": D}


def decl(i, digest=D):
    return {"action": "take_look", "kind": "declared", "id": i, "looks_digest": digest}


def sl(selector, kind="slice", digest=D):
    return {"action": "take_look", "kind": kind, "selector": selector, "looks_digest": digest}


FULL = {"repo": [0, 2, 9999], "who": ["ai", "you"], "from": "2024-01-01", "to": "2024-12-31", "day": "2024-02-29",
        "session": "s-0a17c88568", "rule": "mass_edit", "grade": "L", "hour": 23, "weekday": 6,
        "min_lines": 0, "max_lines": 2**53 - 1, "fork": "no_mass", "measure": "gap_s"}
SLICE = {"fork": "no_merge", "measure": "lines"}
CMP = {"a": {"who": ["ai"]}, "b": {"who": ["you"]}, "fork": "all", "measure": "lines"}      # canonical text of a is below that of b
CMP_BAD = [
    ("swapped groups", {**CMP, "a": CMP["b"], "b": CMP["a"]}), ("the same group twice", {**CMP, "b": CMP["a"]}),
    ("empty group", {**CMP, "b": {}}), ("group is a list", {**CMP, "b": [1]}), ("group is a string", {**CMP, "b": "who=you"}),
    ("session in a group", {**CMP, "b": {"who": ["you"], "session": "s1"}}), ("rule in a group", {**CMP, "b": {"who": ["you"], "rule": "x"}}),
    ("fork in a group", {**CMP, "b": {"who": ["you"], "fork": "all"}}), ("repo name in a group", {**CMP, "b": {"repo": ["r0"]}}),
    ("unsorted who in a group", {**CMP, "b": {"who": ["you", "ai"]}}), ("hour out of range", {**CMP, "b": {"hour": 24}}),
    ("extra key", {**CMP, "c": {"who": ["bot"]}}), ("missing b", {k: v for k, v in CMP.items() if k != "b"}),
    ("missing fork", {k: v for k, v in CMP.items() if k != "fork"}), ("missing measure", {k: v for k, v in CMP.items() if k != "measure"}),
    ("measure outside the six", {**CMP, "measure": "bytes"}), ("measure not a string", {**CMP, "measure": 3}), ("fork outside", {**CMP, "fork": "x"}),
]


def chain(events, catalog=CATALOG, ctx=CTX):
    log = inv.new_log(FIXTURE, ANALYSIS)
    for e in events:
        log = inv.append_event(log, e, catalog, ctx)
    return log


def code_of(fn):
    try:
        fn()
    except inv.InvestigationError as e:
        return e.code
    return None


def section():
    """A real eda.looks section from the stage (small synthetic family)."""
    sys.path.insert(0, str(ROOT / "tests"))
    from eda_support import synthetic_dataset
    from x3dray import eda
    ds = synthetic_dataset(seed=41, repos=3, per_repo=60, private=("r2",), plant=("ai_hours", "outlier"))
    e, problems = eda.run(ds)
    assert problems == [], problems
    return e["looks"]


class Reference(unittest.TestCase):
    def test_declared_and_ad_hoc_looks_replay_with_a_denominator(self):
        log = chain([decl("contrast:lines"), sl(SLICE), sl({"hour": 3}, "rows"), decl("\u4e2d\u6587:\u3053"), {"action": "reset"}])
        s = inv.replay(log, CATALOG, FIXTURE, ANALYSIS, CTX)
        self.assertEqual(s["looks"], {"looks_digest": D, "declared": 13, "inspected": ["contrast:lines", "\u4e2d\u6587:\u3053"], "asked": 2})
        self.assertEqual(s["event_count"], 5)

    def test_zero_looks_still_shows_the_denominator_when_a_context_is_given(self):
        s = inv.replay(inv.new_log(FIXTURE, ANALYSIS), CATALOG, FIXTURE, ANALYSIS, CTX)
        self.assertEqual(s["looks"], {"looks_digest": D, "declared": 13, "inspected": [], "asked": 0})

    def test_without_a_context_the_state_has_no_looks_key_and_take_look_is_refused(self):
        s = inv.replay(inv.new_log(FIXTURE, ANALYSIS), CATALOG, FIXTURE, ANALYSIS)
        self.assertNotIn("looks", s)
        log = chain([decl("contrast:lines")])
        self.assertEqual(code_of(lambda: inv.replay(log, CATALOG, FIXTURE, ANALYSIS)), "looks_context_missing")
        self.assertEqual(code_of(lambda: inv.append_event(inv.new_log(FIXTURE, ANALYSIS), decl("\u00e9"), CATALOG)), "looks_context_missing")

    def test_a_reset_keeps_the_looks(self):
        a = inv.replay(chain([decl("\u00e9"), {"action": "reset"}]), CATALOG, FIXTURE, ANALYSIS, CTX)
        self.assertEqual(a["looks"]["inspected"], ["\u00e9"])

    def test_refusals_are_single_codes_and_never_echo(self):
        cases = {
            "unknown_look": decl("nope"), "invalid_identifier": decl(""), "invalid_digest": decl("\u00e9", digest="x"),
            "looks_digest_mismatch": decl("\u00e9", digest="d" * 64), "invalid_look_kind": {**decl("\u00e9"), "kind": "slices"},
            "invalid_event_shape": {**decl("\u00e9"), "extra": 1},
        }
        for code, event in cases.items():
            with self.subTest(code=code):
                self.assertEqual(code_of(lambda: chain([event])), code)

    def test_selector_alphabet_is_closed(self):
        bad = [
            ("empty", sl({})), ("unknown key", sl({**SLICE, "repo_name": "x"})), ("name in repo", sl({**SLICE, "repo": ["r0"]})),
            ("unsorted repo", sl({**SLICE, "repo": [2, 0]})), ("duplicate repo", sl({**SLICE, "repo": [1, 1]})),
            ("repo too many", sl({**SLICE, "repo": list(range(33))})), ("repo range", sl({**SLICE, "repo": [10000]})),
            ("bool hour", sl({**SLICE, "hour": True})), ("float hour", sl({**SLICE, "hour": 3.0})), ("hour range", sl({**SLICE, "hour": 24})),
            ("who unknown", sl({**SLICE, "who": ["human"]})), ("who unsorted", sl({**SLICE, "who": ["you", "ai"]})),
            ("date shape", sl({**SLICE, "day": "2024-1-01"})), ("date arabic-indic digits", sl({**SLICE, "day": "\u0662\u0660\u0662\u0664-01-01"})),
            ("date trailing newline", sl({**SLICE, "day": "2024-01-0\n"})), ("session charset", sl({**SLICE, "session": "s 1"})),
            ("session long", sl({**SLICE, "session": "s" * 65})), ("rule upper", sl({**SLICE, "rule": "Mass"})),
            ("grade without rule", sl({**SLICE, "grade": "L"})), ("grade bad", sl({**SLICE, "rule": "x", "grade": "A"})),
            ("slice no fork", sl({"measure": "lines"})), ("slice no measure", sl({"fork": "all"})), ("fork bad", sl({"fork": "x", "measure": "lines"})),
            ("rows with fork", sl({"hour": 1, "fork": "all"}, "rows")), ("rows no data filter", sl({"repo": [0]}, "rows")),
            ("max_lines huge", sl({**SLICE, "max_lines": 2**53})), ("selector list", sl([1])), ("selector str", sl("hour=3")),
            ("selector null", sl(None)),
        ]
        for name, event in bad:
            with self.subTest(name=name):
                self.assertEqual(code_of(lambda: chain([event])), "invalid_selector", name)

    def test_compare_selector_is_closed_and_canonical(self):
        for measure in inv.COMPARE_MEASURES:
            log = chain([sl({**CMP, "measure": measure}, "compare")])
            self.assertEqual(inv.replay(log, CATALOG, FIXTURE, ANALYSIS, CTX)["looks"]["asked"], 1, measure)
        for name, selector in CMP_BAD:
            with self.subTest(name=name):
                self.assertEqual(code_of(lambda: chain([sl(selector, "compare")])), "invalid_selector", name)
        # a comparison and a slice with the same words are different looks; two spellings of one comparison are one
        both = chain([sl(SLICE), sl(CMP, "compare")])
        self.assertEqual(inv.replay(both, CATALOG, FIXTURE, ANALYSIS, CTX)["looks"]["asked"], 2)
        self.assertEqual(chain([sl(CMP, "compare")])["head"], chain([sl(dict(reversed(list(CMP.items()))), "compare")])["head"])
        self.assertEqual(code_of(lambda: chain([sl(CMP, "compare"), sl(CMP, "compare")])), "duplicate_look")
        # a key of the other kinds is not a compare key
        self.assertEqual(code_of(lambda: chain([sl({"a": {"hour": 1}, "b": {"hour": 2}, "fork": "all", "measure": "lines"}, "slice")])), "invalid_selector")
        self.assertEqual(code_of(lambda: chain([sl({**CMP, "who": ["ai"]}, "compare")])), "invalid_selector")

    def test_full_selector_is_accepted_and_key_order_is_irrelevant(self):
        shuffled = dict(reversed(list(FULL.items())))
        a, b = chain([sl(FULL)]), chain([sl(shuffled)])
        self.assertEqual(a["head"], b["head"])
        self.assertEqual(inv.replay(a, CATALOG, FIXTURE, ANALYSIS, CTX)["looks"]["asked"], 1)

    def test_a_repeat_is_refused_at_append_and_at_replay(self):
        for e in (decl("\u00e9"), sl(SLICE), sl({"hour": 3}, "rows")):
            log = chain([e])
            self.assertEqual(code_of(lambda: inv.append_event(log, copy.deepcopy(e), CATALOG, CTX)), "duplicate_look")
        # the same selector under the other kind is a different look
        chain([sl({"hour": 3, "fork": "all", "measure": "lines"}), sl({"hour": 3}, "rows")])
        # a log that holds a repeat (built by hand, hashes valid) is refused by replay
        log = chain([decl("\u00e9")])
        rec = copy.deepcopy(log["events"][0])
        rec["seq"], rec["previous"] = 2, log["head"]
        rec["digest"] = inv._hash({"domain": "x3dray.investigation.event.v1", "seq": 2, "previous": rec["previous"], "event": rec["event"]})
        log["events"].append(rec)
        log["head"] = rec["digest"]
        self.assertEqual(code_of(lambda: inv.replay(log, CATALOG, FIXTURE, ANALYSIS, CTX)), "duplicate_look")

    def test_a_log_recorded_under_one_digest_is_not_replayable_under_another(self):
        log = chain([decl("\u00e9")])
        self.assertEqual(code_of(lambda: inv.replay(log, CATALOG, FIXTURE, ANALYSIS, {**CTX, "digest": "d" * 64})), "looks_digest_mismatch")

    def test_tampering_a_take_look_breaks_the_chain(self):
        log = chain([decl("\u00e9"), sl(SLICE)])
        for edit in (lambda l: l["events"][0]["event"].update(id="a b"), lambda l: l["events"][1]["event"]["selector"].update(measure="files"),
                     lambda l: l["events"].pop(0)):
            bad = copy.deepcopy(log)
            edit(bad)
            self.assertIn(code_of(lambda: inv.replay(bad, CATALOG, FIXTURE, ANALYSIS, CTX)),
                          {"event_digest_mismatch", "sequence_mismatch", "previous_digest_mismatch", "head_mismatch"})

    def test_the_catalog_contract_is_unchanged(self):
        # the looks come with the third context, so the five-list catalog stays exact: a sixth key is still refused
        self.assertEqual(code_of(lambda: inv.replay(inv.new_log(FIXTURE, ANALYSIS), {**CATALOG, "looks": ["a"]}, FIXTURE, ANALYSIS)), "invalid_catalog_shape")
        self.assertEqual(code_of(lambda: inv.replay(inv.new_log(FIXTURE, ANALYSIS), {**CATALOG, "unknown": []}, FIXTURE, ANALYSIS)), "invalid_catalog_shape")

    def test_the_looks_context_is_strict(self):
        log = inv.new_log(FIXTURE, ANALYSIS)
        bad = [{"looks": [], "digest": "X"}, {"looks": ["a", "a"], "digest": D}, {"looks": "x", "digest": D}, {"digest": D}, {"looks": []},
               {"looks": [], "digest": D, "x": 1}, {"looks": [""], "digest": D}, {"looks": ["a\u0001"], "digest": D}, {"looks": [1], "digest": D},
               {"looks": ["a"] * 2, "digest": D}, "abc", [], D, 5, {"looks": ["x"] * 10001, "digest": D}]
        for ctx in bad:
            self.assertEqual(code_of(lambda: inv.replay(log, CATALOG, FIXTURE, ANALYSIS, ctx)), "invalid_looks_context", ctx if len(str(ctx)) < 80 else "long")
        self.assertIsNone(code_of(lambda: inv.replay(log, CATALOG, FIXTURE, ANALYSIS, {"looks": [], "digest": D})))

    def test_looks_context_from_a_real_section(self):
        sec = section()
        ctx = inv.looks_context(sec, REPOS)
        self.assertEqual(len(ctx["looks"]), sec["ledger"]["declared"])
        self.assertEqual(ctx["looks"], [f["id"] for f in sec["findings"]])
        # independent recomputation of the definition
        raw = json.dumps({"domain": "x3dray.looks.ledger.v1", "looks": sec, "repos": REPOS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(ctx["digest"], hashlib.sha256(raw.encode("utf-8")).hexdigest())
        # one changed stored number changes the digest
        sec2 = copy.deepcopy(sec)
        sec2["findings"][0]["grade_reason"] = sec2["findings"][0]["grade_reason"] + "."
        self.assertNotEqual(inv.looks_context(sec2, REPOS)["digest"], ctx["digest"])

    def test_looks_context_refuses_inexact_sections(self):
        sec = section()
        def edit(fn):
            s = copy.deepcopy(sec)
            fn(s)
            return code_of(lambda: inv.looks_context(s, REPOS))
        self.assertEqual(edit(lambda s: s["ledger"].update(M=1.0)), "invalid_looks_section")
        self.assertEqual(edit(lambda s: s["ledger"].update(M=2**53)), "invalid_looks_section")
        self.assertEqual(edit(lambda s: s["findings"][0].update(text="\ud800")), "invalid_looks_section")
        self.assertEqual(edit(lambda s: s["findings"].append(copy.deepcopy(s["findings"][0]))), "invalid_looks_section")
        self.assertEqual(edit(lambda s: s["ledger"].update(declared=11)), "invalid_looks_section")
        self.assertEqual(edit(lambda s: s["findings"][0].update(id="a\u0001")), "invalid_looks_section")
        self.assertEqual(edit(lambda s: s.update(deep=[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]])), "invalid_looks_section")
        self.assertEqual(edit(lambda s: s.update(pad="x" * 1_100_000)), "looks_section_too_large")
        self.assertEqual(code_of(lambda: inv.looks_context([], REPOS)), "invalid_looks_section")
        for repos in ([], ["a", "a"], ["a", 1], [""], ["a"] * 2, "r0", None, ["x" * 1025]):
            self.assertEqual(code_of(lambda: inv.looks_context(sec, repos)), "invalid_looks_section", repos)
        # JSON "1.0" is the integer 1 once JS parses it, so only Python can refuse it; the stage emits no float (eda._floats) and the
        # witness gate compares the bundle with a fresh run, so the two runtimes cannot disagree on a gated bundle.
        self.assertEqual(edit(lambda s: s["ledger"].update(M=1.0)), "invalid_looks_section")


class OrdinalBinding(unittest.TestCase):
    def test_the_digest_pins_what_a_repo_position_means(self):
        sec = section()
        a, b = inv.looks_context(sec, ["r0", "r1", "r2"]), inv.looks_context(sec, ["r1", "r0", "r2"])
        self.assertEqual(a["looks"], b["looks"])
        self.assertNotEqual(a["digest"], b["digest"])
        self.assertNotEqual(a["digest"], inv.looks_context(sec, ["r0", "r1", "renamed"])["digest"])
        self.assertNotEqual(a["digest"], inv.looks_context(sec, ["r0", "r1"])["digest"])
        log = inv.append_event(inv.new_log(FIXTURE, ANALYSIS), sl({"repo": [2], **SLICE}, digest=a["digest"]), CATALOG, a)
        self.assertEqual(code_of(lambda: inv.replay(log, CATALOG, FIXTURE, ANALYSIS, b)), "looks_digest_mismatch")
        self.assertNotIn("r2", json.dumps(log["events"]))          # the names are in the hash only, never in the log

    def test_a_changed_stored_number_is_a_different_ledger(self):
        sec = section()
        a = inv.looks_context(sec, REPOS)
        log = inv.append_event(inv.new_log(FIXTURE, ANALYSIS), decl("contrast:lines", digest=a["digest"]), CATALOG, a)
        sec2 = copy.deepcopy(sec)
        sec2["findings"][0]["effect"]["permille"] += 1
        self.assertEqual(code_of(lambda: inv.replay(log, CATALOG, FIXTURE, ANALYSIS, inv.looks_context(sec2, REPOS))), "looks_digest_mismatch")


class Bounds(unittest.TestCase):
    def test_declared_event_size_and_the_256_event_ceiling(self):
        ctx = {"looks": [f"look-{i:03d}" for i in range(256)], "digest": D}
        log = inv.new_log(FIXTURE, ANALYSIS)
        for i in range(256):
            log = inv.append_event(log, decl(f"look-{i:03d}"), CATALOG, ctx)
        self.assertEqual(len(log["events"]), 256)
        self.assertEqual(code_of(lambda: inv.append_event(log, decl("look-000"), CATALOG, ctx)), "event_count_limit")
        n = len(inv._canonical(log))
        print(f"\n[bounds] 256 declared take_look events: {n} bytes of {inv.MAX_LOG_BYTES}")
        self.assertLess(n, inv.MAX_LOG_BYTES)

    def test_worst_case_selector_event_and_log_budget(self):
        worst = {**FULL, "repo": list(range(9968, 10000)), "session": "s" * 64, "who": ["ai", "bot", "other", "upstream", "you"]}
        ev = sl(worst)
        size = len(inv._canonical(ev))
        print(f"\n[bounds] worst-case selector event: {size} bytes of MAX_EVENT_BYTES {inv.MAX_EVENT_BYTES}")
        self.assertLess(size, inv.MAX_EVENT_BYTES)
        group = {"repo": list(range(9968, 10000)), "who": ["ai", "bot", "other", "upstream", "you"], "from": "2024-01-01", "to": "2024-12-31",
                 "day": "2024-02-29", "hour": 23, "weekday": 6, "min_lines": 0, "max_lines": 2**53 - 1}
        cmp_ev = sl({"a": {**group, "hour": 22}, "b": group, "fork": "default_branch", "measure": "weekday"}, "compare")
        cmp_size = len(inv._canonical(cmp_ev))
        print(f"[bounds] worst-case compare event: {cmp_size} bytes of MAX_EVENT_BYTES {inv.MAX_EVENT_BYTES}")
        self.assertLess(cmp_size, inv.MAX_EVENT_BYTES)
        self.assertEqual(code_of(lambda: chain([cmp_ev])), None)
        log = inv.new_log(FIXTURE, ANALYSIS)
        count = 0
        for i in range(256):
            w = {**worst, "min_lines": i}
            try:
                log = inv.append_event(log, sl(w), CATALOG, CTX)
            except inv.InvestigationError as e:
                self.assertEqual(e.code, "log_byte_limit")
                break
            count += 1
        print(f"[bounds] worst-case selectors fit before log_byte_limit: {count}; log bytes {len(inv._canonical(log))}")
        self.assertGreater(count, 100)


# ---------------------------------------------------------------------------------------------- Node twin
RUNNER = """
import fs from 'node:fs';
import {webcrypto} from 'node:crypto';
Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const api=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(input.source,'utf8')).toString('base64'));
const out=[];
for(const q of input.tasks){
 try{
  let value;
  if(q.op==='chain'){
   let log=await api.newLog(q.fixture,q.analysis);
   for(const e of q.events)log=await api.appendEvent(log,e,q.catalog,q.looks);
   value={log,state:await api.replay(log,q.catalog,q.fixture,q.analysis,q.looks)};
  }
  if(q.op==='replay')value=await api.replay(q.log,q.catalog,q.fixture,q.analysis,q.looks);
  if(q.op==='context')value=await api.looksContext(q.section,q.repos);
  if(q.op==='key')value=q.events.map(e=>api.lookKey(e));
  if(q.op==='tables')value={limits:api.INVESTIGATION_LOOKS,events:api.INVESTIGATION_LIMITS};
  out.push({ok:true,value});
 }catch(error){out.push({ok:false,code:error.code||('JS:'+error.name+':'+error.message)});}
}
process.stdout.write(JSON.stringify(out));
"""


def js(tasks, source=JS):
    r = subprocess.run(["node", "--input-type=module", "-e", RUNNER], input=json.dumps({"source": str(source), "tasks": tasks}, ensure_ascii=True),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def py_chain(events, catalog=CATALOG, ctx=CTX):
    try:
        log = chain(events, catalog, ctx)
        return {"ok": True, "value": {"log": log, "state": inv.replay(log, catalog, FIXTURE, ANALYSIS, ctx)}}
    except inv.InvestigationError as e:
        return {"ok": False, "code": e.code}


def task(op, **kw):
    return {"op": op, "fixture": FIXTURE, "analysis": ANALYSIS, "catalog": copy.deepcopy(CATALOG), "looks": copy.deepcopy(CTX), **kw}


@unittest.skipUnless(shutil.which("node"), "Node is required")
class NodeParity(unittest.TestCase):
    def vectors(self):
        v = [("declared ascii", [decl("contrast:lines")]), ("declared cjk", [decl("\u4e2d\u6587:\u3053")]), ("declared astral", [decl("\U0001f600:x")]),
             ("declared combining", [decl("e\u0301")]), ("declared precomposed", [decl("\u00e9")]), ("declared private use", [decl("\ue000")]),
             ("declared quote backslash", [decl("q\"uote\\")]), ("both normalisations are two looks", [decl("e\u0301"), decl("\u00e9")]),
             ("full slice", [sl(FULL)]), ("slice minimal", [sl(SLICE)]), ("rows hour", [sl({"hour": 0}, "rows")]),
             ("rows rule", [sl({"rule": "mass_edit"}, "rows")]), ("rows full", [sl({k: v for k, v in FULL.items() if k not in ("fork", "measure")}, "rows")]),
             ("mixed with navigation", [{"action": "select_entity", "id": "alpha"}, decl("\u00e9"), sl(SLICE), {"action": "reset"}, sl({"hour": 1}, "rows")]),
             ("ints at the bounds", [sl({**SLICE, "min_lines": 0, "max_lines": 2**53 - 1, "repo": [0, 9999], "hour": 0, "weekday": 0})]),
             ("shuffled keys", [sl(dict(reversed(list(FULL.items()))))]),
             ("compare minimal", [sl(CMP, "compare")]), ("compare shuffled keys", [sl(dict(reversed(list(CMP.items()))), "compare")]),
             ("compare each measure", [sl({**CMP, "measure": m}, "compare") for m in inv.COMPARE_MEASURES]),
             ("compare full groups", [sl({"a": {"repo": [0, 2], "who": ["ai"], "from": "2024-01-01", "to": "2024-06-30", "day": "2024-02-29", "hour": 3,
                                                 "weekday": 6, "min_lines": 0, "max_lines": 2**53 - 1},
                                          "b": {"repo": [1], "who": ["you"]}, "fork": "default_branch", "measure": "gap_s"}, "compare")]),
             ("compare with slice and rows", [sl(SLICE), sl({"hour": 1}, "rows"), sl(CMP, "compare"), {"action": "reset"}])]
        bad = [decl("nope"), decl(""), decl("\u00e9", digest="d" * 64), decl("\u00e9", digest="X"), {**decl("\u00e9"), "kind": "x"}, {**decl("\u00e9"), "extra": 0},
               {"action": "take_look"}, {"action": "take_look", "kind": "declared"}, {**decl("\u00e9"), "kind": None},
               sl({}), sl([]), sl("x"), sl(None), sl({**SLICE, "x": 1}), sl({**SLICE, "repo": ["r0"]}), sl({**SLICE, "repo": [2, 1]}), sl({**SLICE, "repo": [1, 1]}),
               sl({**SLICE, "repo": list(range(33))}), sl({**SLICE, "repo": [-1]}), sl({**SLICE, "repo": [10000]}), sl({**SLICE, "hour": True}),
               sl({**SLICE, "hour": 3.5}), sl({**SLICE, "hour": 24}), sl({**SLICE, "hour": "3"}), sl({**SLICE, "who": ["you", "ai"]}), sl({**SLICE, "who": []}),
               sl({**SLICE, "who": ["x"]}), sl({**SLICE, "day": "2024-1-01"}), sl({**SLICE, "day": "\u0662\u0660\u0662\u0664-01-01"}), sl({**SLICE, "day": "2024-01-0\n"}),
               sl({**SLICE, "day": "2024-01-011"}), sl({**SLICE, "session": ""}), sl({**SLICE, "session": "s \u00e9"}), sl({**SLICE, "session": "s" * 65}),
               sl({**SLICE, "rule": "R"}), sl({**SLICE, "grade": "L"}), sl({**SLICE, "rule": "x", "grade": "l"}), sl({"measure": "lines"}), sl({"fork": "all"}),
               sl({"fork": "all", "measure": "bytes"}), sl({"hour": 1, "fork": "all"}, "rows"), sl({"repo": [0]}, "rows"), sl({"grade": "S"}, "rows"),
               sl({**SLICE, "max_lines": 2**53}), sl({**SLICE, "min_lines": -1}), sl({**SLICE, "min_lines": 1e300}),
               {"action": "take_look", "kind": "slice", "selector": SLICE}, {"action": "take_look", "kind": "slice", "looks_digest": D}]
        bad += [sl(sel, "compare") for _, sel in CMP_BAD] + [sl(CMP, "compare", digest="d" * 64), {"action": "take_look", "kind": "compare", "looks_digest": D}]
        return v + [("refused " + json.dumps(e, ensure_ascii=True)[:90], [e]) for e in bad]

    def test_differential_event_vectors(self):
        vs = self.vectors()
        got = js([task("chain", events=e) for _, e in vs])
        for (name, events), actual in zip(vs, got):
            with self.subTest(name=name):
                self.assertEqual(actual, py_chain(events), name)
        self.assertGreater(sum(1 for a in got if a["ok"]), 15)
        self.assertGreater(sum(1 for a in got if not a["ok"]), 40)

    def test_repeats_and_missing_context_agree(self):
        for events in ([decl("\u00e9"), decl("\u00e9")], [sl(SLICE), sl(SLICE)], [sl({"hour": 3}, "rows"), sl({"hour": 3}, "rows")]):
            self.assertEqual(js([task("chain", events=events)])[0], py_chain(events))
        got = js([task("chain", events=[decl("\u00e9")], looks=None)])[0]
        self.assertEqual(got, {"ok": False, "code": "looks_context_missing"})

    def test_state_digest_parity_and_replay_of_python_logs_in_js(self):
        log = chain([decl("\U0001f600:x"), sl(FULL), sl({"hour": 3}, "rows"), {"action": "reset"}, decl("\u4e2d\u6587:\u3053")])
        expected = inv.replay(log, CATALOG, FIXTURE, ANALYSIS, CTX)
        [a, b] = js([task("replay", log=log), task("replay", log=log, looks={**CTX, "digest": "e" * 64})])
        self.assertEqual(a, {"ok": True, "value": expected})
        self.assertEqual(b, {"ok": False, "code": "looks_digest_mismatch"})
        # without a context both runtimes refuse
        [c] = js([task("replay", log=log, looks=None)])
        self.assertEqual(c, {"ok": False, "code": "looks_context_missing"})

    def test_tamper_vectors_agree_on_single_defects(self):
        log = chain([decl("\u00e9"), sl(SLICE), sl({"hour": 3}, "rows")])
        def mut(fn):
            b = copy.deepcopy(log)
            fn(b)
            return b
        mutants = [mut(lambda l: l["events"][0]["event"].update(id="a b")), mut(lambda l: l["events"][1]["event"]["selector"].update(measure="files")),
                   mut(lambda l: l["events"][0].update(digest="0" * 64)), mut(lambda l: l.update(head="0" * 64)),
                   mut(lambda l: l["events"][0]["event"].update(looks_digest="d" * 64)), mut(lambda l: l["events"][2]["event"]["selector"].update(hour=4)),
                   mut(lambda l: l["events"][2]["event"]["selector"].update(who=["you", "ai"])), mut(lambda l: l["events"].reverse())]
        got = js([task("replay", log=m) for m in mutants])
        for m, g in zip(mutants, got):
            try:
                want = {"ok": True, "value": inv.replay(m, CATALOG, FIXTURE, ANALYSIS, CTX)}
            except inv.InvestigationError as e:
                want = {"ok": False, "code": e.code}
            self.assertEqual(g, want)
            self.assertFalse(g["ok"])

    def test_look_keys_and_invalid_contexts_agree(self):
        events = [decl(i) for i in LOOK_IDS] + [sl(FULL), sl(dict(reversed(list(FULL.items())))), sl({"hour": 3}, "rows"), sl(SLICE)]
        [keys] = js([task("key", events=events)])
        self.assertEqual(keys["value"], [inv.look_key(e) for e in events])
        self.assertEqual(inv.look_key(sl(FULL)), inv.look_key(sl(dict(reversed(list(FULL.items()))))))
        self.assertNotEqual(inv.look_key(decl("\u00e9")), inv.look_key(decl("e\u0301")))       # no Unicode normalisation: two ids, two looks
        log = chain([decl("\u00e9")])
        bads = [{"looks": [], "digest": "X"}, {"looks": ["a", "a"], "digest": D}, {"looks": "x", "digest": D}, {"digest": D}, {"looks": [], "digest": D, "x": 1},
                {"looks": ["a\u0001"], "digest": D}, {"looks": [1], "digest": D}, {"looks": [""], "digest": D}, "abc", [], D, 5, {"looks": ["x"] * 10001, "digest": D},
                {"looks": ["a\ud800"], "digest": D}, {"looks": ["a"], "digest": "A" * 64}, {"looks": ["a"], "digest": "c" * 63}]
        for got, bad in zip(js([task("replay", log=log, looks=b) for b in bads]), bads):
            self.assertEqual(got, {"ok": False, "code": "invalid_looks_context"}, str(bad)[:60])
            self.assertEqual(code_of(lambda: inv.replay(log, CATALOG, FIXTURE, ANALYSIS, bad)), "invalid_looks_context")

    def test_tables_are_identical(self):
        [t] = js([task("tables")])
        limits = t["value"]["limits"]
        self.assertEqual(limits["selector"], inv.SELECTOR_RULES)
        self.assertEqual(limits["kinds"], list(inv.LOOK_KINDS))
        self.assertEqual({k: limits["kindKeys"][k] for k in limits["kindKeys"]}, inv.SELECTOR_KIND_KEYS)
        self.assertEqual(limits["rowsNeedOneOf"], inv.ROWS_NEED_ONE_OF)
        self.assertEqual(limits["compareMeasures"], inv.COMPARE_MEASURES)
        self.assertEqual(limits["compareGroupKeys"], inv.COMPARE_GROUP_KEYS)
        self.assertEqual((limits["domain"], limits["maxBytes"], limits["maxDepth"], limits["maxSafeInt"]),
                         (inv.LOOKS_DOMAIN, inv.MAX_LOOKS_BYTES, inv.MAX_LOOKS_DEPTH, inv.MAX_SAFE_INT))

    def test_selector_enums_match_the_stage_and_the_tool(self):
        from x3dray import structure
        self.assertEqual(inv.SELECTOR_RULES["who"][1], structure.CLASSES)
        tools = (ROOT / "x3dray" / "web" / "tools.js").read_text(encoding="utf-8")
        for fork in inv.SELECTOR_RULES["fork"][1]:
            self.assertIn(f"'{fork}'", tools.split("export const EDA_FORKS")[1].split("\n")[0])
        meas = tools.split("const EDA_MEASURES = ")[1].split("\n")[0]
        for m in inv.SELECTOR_RULES["measure"][1]:
            self.assertIn(m + ":", meas)
        # the six measures of compare and the keys of its groups are the tool's own lists
        self.assertIn(json.dumps(inv.COMPARE_MEASURES).replace('"', "'").replace(", ", ", "), tools)
        self.assertIn(json.dumps(inv.COMPARE_GROUP_KEYS).replace('"', "'"), tools)

    def test_looks_context_parity_on_a_real_section_and_inexact_variants(self):
        sec = section()
        variants = [sec]
        def with_(fn):
            s = copy.deepcopy(sec)
            fn(s)
            variants.append(s)
        with_(lambda s: s["findings"][0].update(text="\u2028\u007f\u0000 \U0001f600 e\u0301 \"\\"))
        with_(lambda s: s["ledger"].update(M=1.5)); with_(lambda s: s["ledger"].update(M=2**53)); with_(lambda s: s["ledger"].update(M=2**53 - 1))
        with_(lambda s: s["findings"][0].update(text="\ud800")); with_(lambda s: s["findings"].append(copy.deepcopy(s["findings"][0])))
        with_(lambda s: s["ledger"].update(declared=3)); with_(lambda s: s["findings"][1].update(id="x\u0007"))
        with_(lambda s: s.update(deep=json.loads("[" * 40 + "1" + "]" * 40))); with_(lambda s: s.update(pad="x" * 1_100_000))
        with_(lambda s: s.update(unicode_key={"\U0001f600": 1, "z": 2, "\uffff": 3, "a\u0301": 4}))
        variants += [[], None, 3, "s"]
        want = []
        for s in variants:
            try:
                want.append({"ok": True, "value": inv.looks_context(s, REPOS)})
            except inv.InvestigationError as e:
                want.append({"ok": False, "code": e.code})
        got = js([task("context", section=s, repos=REPOS) for s in variants])
        self.assertEqual(got, want)
        self.assertTrue(want[0]["ok"] and want[1]["ok"] and not want[2]["ok"])



@unittest.skipUnless(shutil.which("node"), "Node is required")
class NodeFuzz(unittest.TestCase):
    """Seeded mutation fuzz: 1500 one-field mutations of valid take_look events must give the same outcome in both runtimes."""

    def test_python_and_node_agree_on_mutated_events(self):
        import random
        rng = random.Random(20260101)
        pool = [None, True, False, 0, 1, -1, 3, 23, 24, 6, 7, 9999, 10000, 2**53 - 1, 2**53, 3.5, -0.5, "", "x", "S", "L", "A", "ai", "you", "human", "2024-02-29",
                "2024-2-29", "\u0662\u0660\u0662\u0664-01-01", "s-0a17c88568", "s \u00e9", "size_high", "Size", "no_merge", "lines", "bytes", "\u00e9", "e\u0301",
                "\U0001f600", "\u2028", "\x00", "a\nb", "x" * 65, [], [0], [1, 0], [0, 0], [0, 1, 2], list(range(33)), ["ai"], ["you", "ai"], ["ai", "ai"], {}, {"a": 1}, [[]]]
        bases = [decl("contrast:lines"), decl("\u4e2d\u6587:\u3053"), sl(FULL), sl(SLICE), sl({"hour": 3}, "rows"), sl({"rule": "size_high", "grade": "S", "who": ["ai"], "repo": [0, 2]}, "rows")]

        def mutate(e):
            e = copy.deepcopy(e)
            for _ in range(rng.choice([1, 1, 2])):
                op = rng.choice(["set", "set", "del", "add", "sel_set", "sel_del", "sel_add", "kind"])
                if op == "set":
                    e[rng.choice(list(e))] = rng.choice(pool)
                elif op == "del" and len(e) > 1:
                    e.pop(rng.choice(list(e)))
                elif op == "add":
                    e[rng.choice(["x", "selector", "id", "kind", "looks_digest"])] = rng.choice(pool)
                elif op == "kind":
                    e["kind"] = rng.choice(["declared", "slice", "rows", "Slice", "", None, 1])
                elif isinstance(e.get("selector"), dict):
                    sel = e["selector"]
                    if op == "sel_set" and sel:
                        sel[rng.choice(list(sel))] = rng.choice(pool)
                    elif op == "sel_del" and sel:
                        sel.pop(rng.choice(list(sel)))
                    elif op == "sel_add":
                        sel[rng.choice(list(inv.SELECTOR_RULES) + ["zzz"])] = rng.choice(pool)
            return e

        events = [mutate(rng.choice(bases)) for _ in range(1500)]
        got = js([task("chain", events=[e]) for e in events])
        outcomes = set()
        for e, g in zip(events, got):
            self.assertEqual(g, py_chain([e]), json.dumps(e, ensure_ascii=True)[:200])
            outcomes.add("ok" if g["ok"] else g["code"])
        self.assertGreaterEqual(len(outcomes), 6, outcomes)       # the mutations reach the shape, kind, selector and identifier refusals and accept some

# ------------------------------------------------------------------------------ v1 backward compatibility
EVIDENCE = [ROOT / "docs" / "evidence" / "pass-2" / "browser-history.json", ROOT / "docs" / "evidence" / "pass-3" / "browser-history.json"]
PRISTINE = """
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from x3dray import investigation as inv
d = json.load(open(sys.argv[2])); cat = json.load(open(sys.argv[3])); log = d["log"]
try:
    print(json.dumps({"ok": True, "state": inv.replay(log, cat, log["fixture_digest"], log["analysis_digest"])}, sort_keys=True))
except inv.InvestigationError as e:
    print(json.dumps({"ok": False, "code": e.code}))
"""


def catalog_from(log):
    cat = {k: [] for k in inv.CATALOG_KEYS}
    of = {"select_entity": "entities", "select_relation": "relations", "run_mutation": "mutations", "inspect_finding": "findings"}
    for rec in log["events"]:
        e = rec["event"]
        a = e["action"]
        if a in of and e["id"] not in cat[of[a]]:
            cat[of[a]].append(e["id"])
        if a == "set_query":
            for k in ("from_entity", "to_entity"):
                if e["query"][k] not in cat["entities"]:
                    cat["entities"].append(e["query"][k])
        if a == "set_lens":
            cat["predicates"] += [p for p in e["lens"]["predicates"] if p not in cat["predicates"]]
    return cat


class BackwardCompatibility(unittest.TestCase):
    def test_saved_v1_logs_replay_unchanged_without_and_with_a_looks_context(self):
        for path in EVIDENCE:
            with self.subTest(path=path.name):
                raw = json.loads(path.read_text(encoding="utf-8"))
                log = raw["log"]
                cat = catalog_from(log)
                legacy = inv.replay(log, cat, log["fixture_digest"], log["analysis_digest"])
                self.assertEqual(legacy["event_head"], log["head"])
                self.assertNotIn("looks", legacy)
                if path.parent.name == "pass-3":
                    self.assertEqual(legacy["state_digest"], raw["state"]["state_digest"])     # byte-identical state
                self.assertEqual({k: v for k, v in legacy.items() if k in raw["state"] and k != "state_digest"},
                                 {k: v for k, v in raw["state"].items() if k != "state_digest" and k in legacy})
                with_looks = inv.replay(log, cat, log["fixture_digest"], log["analysis_digest"], {"looks": ["contrast:lines"], "digest": D})
                self.assertEqual({k: v for k, v in with_looks.items() if k not in ("looks", "state_digest")},
                                 {k: v for k, v in legacy.items() if k != "state_digest"})
                self.assertEqual(with_looks["looks"], {"looks_digest": D, "declared": 1, "inspected": [], "asked": 0})
                self.assertNotEqual(with_looks["state_digest"], legacy["state_digest"])

    @unittest.skipUnless(BASE, "set X3D_PRISTINE_ROOT to a tree that predates this change")
    def test_pristine_code_and_new_code_agree_on_old_logs_and_old_readers_fail_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for path in EVIDENCE:
                raw = json.loads(path.read_text(encoding="utf-8"))
                cat = catalog_from(raw["log"])
                cp = Path(tmp) / "cat.json"
                cp.write_text(json.dumps(cat))
                out = subprocess.run([sys.executable, "-I", "-c", PRISTINE, BASE, str(path), str(cp)], capture_output=True, text=True, check=True).stdout
                old = json.loads(out)
                new = inv.replay(raw["log"], cat, raw["log"]["fixture_digest"], raw["log"]["analysis_digest"])
                self.assertEqual(old, {"ok": True, "state": json.loads(json.dumps(new, sort_keys=True))})
            # a new-format log that holds a take_look: the pristine reader refuses it with a stable code, never reads it
            log = chain([decl("\u00e9")])
            lp = Path(tmp) / "new.json"
            lp.write_text(json.dumps({"log": {**log, "fixture_digest": log["fixture_digest"]}}))
            cp.write_text(json.dumps(CATALOG))
            out = json.loads(subprocess.run([sys.executable, "-I", "-c", PRISTINE, BASE, str(lp), str(cp)], capture_output=True, text=True, check=True).stdout)
            self.assertEqual(out, {"ok": False, "code": "unknown_action"})
            # a new-format log without take_look has the same bytes the old code writes (chain unchanged)
            plain = inv.append_event(inv.new_log(FIXTURE, ANALYSIS), {"action": "select_entity", "id": "alpha"}, LEGACY)
            lp.write_text(json.dumps({"log": plain}))
            cp.write_text(json.dumps(LEGACY))
            out = json.loads(subprocess.run([sys.executable, "-I", "-c", PRISTINE, BASE, str(lp), str(cp)], capture_output=True, text=True, check=True).stdout)
            self.assertEqual(out["state"]["event_head"], plain["head"])


if __name__ == "__main__":
    unittest.main()
