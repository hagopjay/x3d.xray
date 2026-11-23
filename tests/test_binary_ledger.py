"""Pass B tests: the hypothesis ledger, the RE witness kinds and the RE gates.

Static and oracle checks need only a C compiler and binutils. Trace checks
(strace, gdb) run only when those tools work here; otherwise the ledger must
say UNTESTED rather than invent a verdict.
"""
import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from x3dray.binary import harvest_bin, callgraph, capability, scene_bin, hypotheses as H, gates_bin

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.path.join(ROOT, "targets", "crackme1.c")
SHIM = os.path.join(ROOT, "targets", "ptrace_shim.c")
GEN = os.path.join(ROOT, "targets", "gen_strings.py")
LEDGER = os.path.join(ROOT, "specs", "crackme1.hypotheses.json")
CLAIMS = os.path.join(ROOT, "specs", "crackme1.bin.json")
CC = shutil.which("cc") or shutil.which("gcc")
HAVE = CC and shutil.which("objdump") and shutil.which("file")


def _load(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.model = _load(LEDGER)["model"]

    def test_keygen_model_matches_both_imported_serials(self):
        # 5470-E4D2: the outside agent's clean dump (H4); F104-7CB6: its gdb dump (H3)
        self.assertEqual(H.serial_of(H.derive_model("alice", 0, self.model)), "5470-E4D2")
        self.assertEqual(H.serial_of(H.derive_model("alice", 0xA5A5A5A5, self.model)), "F104-7CB6")

    def test_rolling_xor_roundtrip_and_decoder_needs_restart(self):
        msg = b"Invalid serial."
        enc = H.rolling_xor(msg, 0x33, 7)
        blob = b"\x00" * 5 + enc + b"\x00" * 3
        runs = H.decode_runs(blob, 0x33, 7, 8)
        self.assertEqual([r["text"] for r in runs], ["Invalid serial."])
        self.assertEqual(runs[0]["offset"], 5)

    def test_oracle_vectors_are_seeded(self):
        self.assertEqual(H.oracle_vectors(7, 5), H.oracle_vectors(7, 5))
        self.assertNotEqual(H.oracle_vectors(7, 5), H.oracle_vectors(8, 5))


@unittest.skipUnless(HAVE and os.path.exists(SRC), "needs cc + binutils + the fixture")
class LedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="binledger-")
        subprocess.run(["python3", os.path.basename(GEN)], cwd=os.path.dirname(GEN), check=True,
                       capture_output=True)
        cls.bin = os.path.join(cls.tmp, "crackme1")
        subprocess.run([CC, "-O2", "-g", "-o", cls.bin, SRC], check=True)
        cls.spec, cls.claims = _load(LEDGER), _load(CLAIMS)
        cls.out = os.path.join(cls.tmp, "out")
        disasm, _ = harvest_bin._objdump_disasm(cls.bin)
        b = harvest_bin.harvest(cls.bin)
        g = callgraph.build(b, disasm)
        g["passage"] = callgraph.passage(g, "derive")
        cap = capability.analyse(b)
        cls.L = H.run_ledger(cls.bin, cls.spec, out_dir=cls.out, disasm=disasm,
                             shim_src=SHIM, oracle_n=60)
        for name, obj in (("binary.json", b), ("callgraph.json", g), ("capability.json", cap),
                          ("ledger.json", cls.L)):
            with open(os.path.join(cls.out, name), "w") as fh:
                json.dump(obj, fh)
        with open(os.path.join(cls.out, "xray.x3d"), "w") as fh:
            fh.write(scene_bin.build_scene(b, g, ledger=cls.L))
        cls.rows = {r["id"]: r for r in cls.L["rows"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_static_rows_confirmed(self):
        self.assertEqual(self.rows["H1"]["verdict"], "CONFIRMED")
        self.assertIn("Invalid serial.", self.rows["H1"]["observed"]["decoded"])
        self.assertEqual(self.rows["H1"]["observed"]["visible_to_strings"], [])

    def test_oracle_row_confirmed_with_negative_control(self):
        r = self.rows["H5"]
        self.assertEqual(r["verdict"], "CONFIRMED")
        self.assertEqual(r["observed"]["accepted"], "60/60")
        self.assertEqual(r["observed"]["mutants_rejected"], "6/6")

    def test_dynamic_rows_never_invent_a_verdict(self):
        for hid in ("H2", "H3", "H4", "H6"):
            r = self.rows[hid]
            avail = all(w.get("status") != "unavailable" for w in r["witnesses"])
            if avail:
                self.assertEqual(r["verdict"], "CONFIRMED", (hid, r["observed"]))
            else:
                self.assertEqual((r["verdict"], r["grade"]), ("UNTESTED", "Suspected"))

    def test_static_support_found_in_disasm(self):
        consts = {p["constant"] for p in self.L["static_patterns"]}
        self.assertTrue({"seed", "mul", "poison_ctor", "poison_int3", "int3_byte"} <= consts)

    def test_gates_pass(self):
        res = gates_bin.run_all(self.out, self.bin, self.claims, self.spec)
        self.assertTrue(res["ok"], json.dumps({k: g["problems"] for k, g in res["gates"].items()}))

    def test_fact_gate_catches_stray_digit(self):
        bad = copy.deepcopy(self.claims)
        bad["claims"][0]["text"] += " It is called 3 times."
        self.assertEqual(gates_bin.gate_re_fact(self.out, bad)["status"], "fail")

    def test_witness_gate_fails_closed_on_tampered_observation(self):
        L = copy.deepcopy(self.L)
        L["rows"][0]["witnesses"][0]["output_sha256"] = "0" * 64
        alt = os.path.join(self.tmp, "tampered")
        shutil.copytree(self.out, alt)
        with open(os.path.join(alt, "ledger.json"), "w") as fh:
            json.dump(L, fh)
        self.assertEqual(gates_bin.gate_re_witness(alt, self.bin, self.spec)["status"], "fail")

    def test_scene_gate_catches_missing_rung(self):
        alt = os.path.join(self.tmp, "norung")
        shutil.copytree(self.out, alt, dirs_exist_ok=True)
        p = os.path.join(alt, "xray.x3d")
        with open(p) as fh:
            s = fh.read()
        with open(p, "w") as fh:
            fh.write(s.replace("DEF='HYP_H6'", "DEF='GONE_H6'"))
        self.assertEqual(gates_bin.gate_re_scene(alt)["status"], "fail")


if __name__ == "__main__":
    unittest.main()
