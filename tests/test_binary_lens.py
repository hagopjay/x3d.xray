"""Pass A tests for the binary x-ray lens. Skip cleanly when binutils is absent.

Builds the crackme1 fixture with cc, then checks: the gatekeeper is `main`,
passage(entry -> derive) == ['main'], the capability lattice recovers the
anti-debug concept, the scene is well-formed XML, and every witness verifies.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
import xml.dom.minidom as minidom

from x3dray.binary import harvest_bin, callgraph, capability, scene_bin, witnesses

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "targets", "crackme1.c")
GEN = os.path.join(HERE, "..", "targets", "gen_strings.py")
HAVE_CC = shutil.which("cc") or shutil.which("gcc")
HAVE_TOOLS = witnesses.have("objdump") and witnesses.have("file")


@unittest.skipUnless(HAVE_CC and HAVE_TOOLS and os.path.exists(SRC),
                     "needs a C compiler, GNU binutils and the crackme1 fixture")
class BinaryLensTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="binlens-")
        subprocess.run(["python3", os.path.basename(GEN)],
                       cwd=os.path.dirname(GEN), check=True)
        cls.bin = os.path.join(cls.tmp, "crackme1")
        cc = shutil.which("cc") or shutil.which("gcc")
        subprocess.run([cc, "-O2", "-g", "-o", cls.bin, SRC], check=True)
        cls.disasm, _ = harvest_bin._objdump_disasm(cls.bin)
        cls.b = harvest_bin.harvest(cls.bin)
        cls.g = callgraph.build(cls.b, cls.disasm)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_triage(self):
        self.assertEqual(self.b["triage"]["arch"], "x86-64")
        self.assertIn("ptrace", self.b["imports"])

    def test_gatekeeper_is_main(self):
        self.assertEqual(self.g["gatekeeper"], "main")

    def test_passage_to_derive_is_main(self):
        res = callgraph.passage(self.g, "derive")
        self.assertEqual(res["passage_points"], ["main"])

    def test_anti_debug_detected(self):
        anti = {fn["name"] for fn in self.b["functions"] if fn["anti_debug"]}
        self.assertIn("guard", anti)   # the ptrace constructor
        self.assertIn("main", anti)    # the int3 self-scan

    def test_capability_lattice_has_anti_debug(self):
        cap = capability.analyse(self.b)
        caps = {c for v in cap["context"].values() for c in v}
        self.assertIn("anti-debug", caps)
        self.assertIn("parse", caps)

    def test_scene_is_well_formed(self):
        xml = scene_bin.build_scene(self.b, self.g, source_digest="deadbeef")
        minidom.parseString(xml)  # raises on malformed XML
        self.assertIn("Viewpoint", xml)
        self.assertIn("The gatekeeper: main", xml)

    def test_every_witness_verifies(self):
        for name, w in self.b["witnesses"].items():
            if w:
                self.assertEqual(witnesses.verify(w)["status"], "match", name)


if __name__ == "__main__":
    unittest.main()
