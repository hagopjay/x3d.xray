"""Transport and presentation boundaries with synthetic data; Node, no packages."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "x3dray" / "web"


@unittest.skipUnless(shutil.which("node"), "Node is required for protocol tests")
class ResultLimits(unittest.TestCase):
    def node(self, script):
        run = subprocess.run(["node", "--input-type=module", "-e", script, (WEB / "tools.js").as_uri()],
                             capture_output=True, text=True, check=True, timeout=20)
        return json.loads(run.stdout)

    def test_utf8_scalars_arrays_and_maps_fit_final_budget(self):
        results = self.node("""
          const {cap} = await import(process.argv[1]);
          const values = [
            {rows: ['漢'.repeat(12000)]}, {digest: 'x'.repeat(50000)},
            '🛰️'.repeat(20000), Array.from({length:100}, (_,i)=>({i, text:'中文'.repeat(200)})),
            {matched: 500, nested: {records: Array.from({length:100}, ()=>({x:'q'.repeat(1000)}))}},
            Object.fromEntries(Array.from({length:1000}, (_,i)=>['key'+i,'"\\\\漢'.repeat(20)]))
          ];
          console.log(JSON.stringify(values.map(value => {
            const before=JSON.stringify(value), result=cap(value, 1500);
            return {bytes:Buffer.byteLength(JSON.stringify(result)), truncated:!!result.truncated,
              originalUnchanged:before===JSON.stringify(value), metadata:result._result_limit};
          })));
        """)
        self.assertEqual(len(results), 6)
        for r in results:
            self.assertLessEqual(r["bytes"], 1500)
            self.assertTrue(r["truncated"])
            self.assertTrue(r["originalUnchanged"])
            self.assertGreater(r["metadata"]["original_bytes"], 1500)

    def test_records_and_citations_are_atomic_and_omissions_are_counted(self):
        r = self.node("""
          const {cap}=await import(process.argv[1]);
          const rows=Array.from({length:40},(_,i)=>({id:i,text:'漢'.repeat(100),evidence:{shas:['one','two'],commits:2}}));
          const result=cap({matched:80,rows,rows_omitted:40},2200);
          console.log(JSON.stringify({result,atomic:result.rows.every((row,i)=>JSON.stringify(row)===JSON.stringify(rows[i]))}));
        """)
        value = r["result"]
        self.assertTrue(r["atomic"])
        self.assertEqual(value["matched"], 80)
        self.assertEqual(len(value["rows"]) + value["rows_omitted"], 80)
        omitted = next(x for x in value["_result_limit"]["omissions"] if x["path"] == "/rows")
        self.assertEqual(omitted["original_items"], 40)
        self.assertEqual(omitted["returned_items"], len(value["rows"]))

    def test_small_results_and_tiny_valid_limit(self):
        r = self.node("""
          const {cap}=await import(process.argv[1]);
          const small={rows:[{a:1}],matched:1};
          const tiny=cap({["huge".repeat(1000)]:'abc'},256);
          console.log(JSON.stringify({unchanged:cap(small)===small,bytes:Buffer.byteLength(JSON.stringify(tiny)),tiny}));
        """)
        self.assertTrue(r["unchanged"])
        self.assertLessEqual(r["bytes"], 256)
        self.assertTrue(r["tiny"]["_result_limit"]["entire_payload_omitted"])

    def test_published_argument_schema_is_enforced_by_shared_tools(self):
        r = self.node("""
          const {makeTools}=await import(process.argv[1]);
          const tools=Object.fromEntries(makeTools({}).map(t=>[t.name,t]));
          const cases=[['query_commits',{limit:'40'}],['query_commits',{merges:'false'}],['query_commits',{limit:1.5}],
            ['query_commits',null],['how_built',{}],['query_commits',{sort:'anything'}]];
          console.log(JSON.stringify(cases.map(([name,args])=>{try{tools[name].execute(args);return false;}catch(e){return /must be|required/.test(e.message);}})));
        """)
        self.assertEqual(r, [True] * 6)


@unittest.skipUnless(shutil.which("node"), "Node is required for protocol tests")
class McpEnvelopes(unittest.TestCase):
    def run_server(self, messages, extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            b = {"meta": {"title": "Synthetic", "version": "test"}, "repos": [], "commits": [],
                 "files": {}, "signals": [], "ties": {"machine": [], "curated": [], "series": [], "grades": {}},
                 "self": {"signal_code": {}}, "receipt": {"digest": '"\\漢' * 20000}}
            b.update(extra or {})
            path.write_text(json.dumps(b))
            data = "\n".join(json.dumps(x) if not isinstance(x, str) else x for x in messages) + "\n"
            run = subprocess.run(["node", str(WEB / "mcp.mjs"), "--bundle", str(path)], input=data,
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        return [json.loads(line) for line in run.stdout.splitlines()], run.stdout.splitlines()

    def test_malformed_messages_do_not_kill_the_next_request(self):
        messages = [None, [], True, {}, {"jsonrpc": "2.0", "method": 7, "id": 1}, "{invalid",
                    {"jsonrpc": "2.0", "method": "ping", "params": [], "id": 2},
                    {"jsonrpc": "2.0", "method": "ping", "id": 3}]
        rows, _ = self.run_server(messages)
        self.assertEqual([r["error"]["code"] for r in rows[:-1]], [-32600] * 5 + [-32700, -32602])
        self.assertEqual(rows[-1], {"jsonrpc": "2.0", "id": 3, "result": {}})

    def test_oversized_line_is_discarded_and_next_request_is_served(self):
        rows, _ = self.run_server(['"' + "x" * 70000 + '"', {"jsonrpc": "2.0", "method": "ping", "id": "after"}])
        self.assertEqual(rows[0]["error"]["code"], -32600)
        self.assertEqual(rows[-1]["id"], "after")
        self.assertEqual(rows[-1]["result"], {})

    def test_supported_version_is_counteroffered_not_arbitrarily_echoed(self):
        messages = [{"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {"protocolVersion": "future-unknown"}},
                    {"jsonrpc": "2.0", "method": "initialize", "id": 2, "params": {"protocolVersion": 77}},
                    {"jsonrpc": "2.0", "method": "initialize", "id": 3, "params": {"protocolVersion": "2025-06-18"}}]
        rows, _ = self.run_server(messages)
        self.assertEqual(rows[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(rows[1]["error"]["code"], -32602)
        self.assertEqual(rows[2]["result"]["protocolVersion"], "2025-06-18")

    def test_arguments_are_validated_and_wire_result_is_bounded(self):
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "query_commits", "arguments": None}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "query_commits", "arguments": {"limit": "4"}}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "how_built", "arguments": {"topic": "receipt"}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "ping"}]
        rows, lines = self.run_server(messages)
        self.assertEqual(rows[0]["error"]["code"], -32602)
        self.assertTrue(rows[1]["result"]["isError"])
        self.assertIn("truncated", json.loads(rows[2]["result"]["content"][0]["text"]))
        self.assertLessEqual(len(lines[2].encode("utf-8")), 32000)
        self.assertEqual(rows[-1]["result"], {})


if __name__ == "__main__":
    unittest.main()
