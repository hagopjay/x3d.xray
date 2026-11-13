"""The three EDA examples (examples/) run offline, and what they print is what the code does."""
import importlib.util
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class SignalAndNoise(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load("eda_1_signal_and_noise")
        cls.r = cls.mod.run()

    def test_planted_effects_are_confirmed_and_nothing_else_is(self):
        self.assertEqual(self.r["planted"]["by_grade"], {"C": 2, "L": 0, "S": 0, "none": 10})
        self.assertEqual([g[0] for g in self.r["planted"]["graded"]], ["contrast:lines", "contrast:hour4"])
        self.assertTrue(all(g[1] == "C" and g[2] == "all_checks_pass" and float(g[4]) <= 0.05 for g in self.r["planted"]["graded"]))

    def test_one_pure_noise_history_still_earns_a_confirmed(self):
        self.assertEqual(self.r["noise_with_confirmed"], [1031])
        g = self.r["noise"][1031]
        self.assertEqual(g["by_grade"], {"C": 1, "L": 1, "S": 1, "none": 9})
        self.assertEqual([x[0] for x in g["graded"] if x[1] == "C"], ["contrast:hour4"])

    def test_the_report_states_the_recorded_calibration_and_says_how_to_read_it(self):
        text = self.mod.report(self.r)
        self.assertIn("4 of 150 histories", text)
        self.assertIn("not recomputed unless --calibrate", text)
        self.assertIn("A few noise histories will still earn one", text)


class SimpsonsParadox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load("eda_2_simpsons_paradox")
        cls.r = cls.mod.run()

    def test_a_large_difference_with_a_small_q_is_not_confirmed(self):
        f = self.r["focus"]["contrast:lines"]
        self.assertEqual(f["effect"]["permille"], -589)
        self.assertLessEqual(float(f["q_text"]), 0.05)
        self.assertEqual(f["grade"], "L")
        self.assertEqual(f["grade_reason"], "no_stratum_with_both_groups")
        self.assertEqual(self.r["ledger"]["C"], 0)

    def test_the_repo_look_is_the_same_variable_as_the_class(self):
        f = self.r["focus"]["contrast:repo"]
        self.assertEqual(abs(f["effect"]["permille"]), 1000)
        self.assertEqual(f["grade"], "L")

    def test_a_look_with_no_difference_gets_no_grade(self):
        f = self.r["focus"]["contrast:hour4"]
        self.assertIsNone(f["grade"])
        self.assertEqual(f["grade_reason"], "effect_below_floor")

    def test_the_report_names_the_reason(self):
        text = self.mod.report(self.r)
        self.assertIn("no repo holds both groups", text)
        self.assertIn("never promoted to a finding about authors", text)


@unittest.skipUnless(shutil.which("node"), "Node is required")
class PriceOfLooking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load("eda_3_the_price_of_looking")
        cls.r = cls.mod.run()

    def test_p_is_constant_and_q_rises_with_the_count(self):
        rows = self.r["rows"]
        self.assertEqual([x["prior"] for x in rows], list(self.mod.PRIOR_LOOKS))
        self.assertEqual({x["p"] for x in rows}, {"0.0020"})
        self.assertEqual([x["m"] for x in rows], [13 + k for k in self.mod.PRIOR_LOOKS])
        qs = [float(x["q"]) for x in rows]
        self.assertEqual(qs, sorted(qs))
        self.assertEqual([x["asked"] for x in rows], [k + 1 for k in self.mod.PRIOR_LOOKS])

    def test_the_same_answer_crosses_the_line_after_enough_looks(self):
        rows = {x["prior"]: x for x in self.r["rows"]}
        self.assertEqual(rows[0]["q"], "0.0130")
        self.assertEqual(rows[31]["q"], "0.0440")
        self.assertEqual(rows[38]["q"], "0.0510")
        self.assertLessEqual(float(rows[31]["q"]), 0.05)
        self.assertGreater(float(rows[38]["q"]), 0.05)

    def test_every_answer_is_ungraded_and_names_its_denominator(self):
        self.assertTrue(all("ungraded" in x["text"] and f"m = {x['m']} looks" in x["text"] for x in self.r["rows"]))

    def test_the_report(self):
        text = self.mod.report(self.r)
        self.assertIn("does not clear", text)
        self.assertIn("every look you take is added to the family", text)


class Index(unittest.TestCase):
    def test_every_example_is_in_the_index_and_has_a_run_line(self):
        index = (ROOT / "examples" / "README.md").read_text()
        for path in sorted((ROOT / "examples").glob("eda_*.py")):
            self.assertIn(path.name, index)
            self.assertIn(f"python3 examples/{path.name}", path.read_text())


if __name__ == "__main__":
    unittest.main()
