"""Known-answer tests for the parts of x3d.xray that decide what the page may say. Standard library only:
python3 -m unittest discover -s tests
"""

import unittest

from x3dray import harvest, spec, ties
from x3dray.page import privacy_scan

PROFILE = {
    "signals": [
        {"id": "family_span", "kind": "totals", "text": "", "values": {"commits": 553, "repos": 11, "first": "2025-09-26", "active_days": 40}},
        {"id": "who_commits", "kind": "identity", "text": "", "values": {"shares": {"ai": 37}}},
    ],
    "per_repo": {"x3d.ML": {"x3d_files": 918}},
}


class FactGate(unittest.TestCase):
    def test_slots_are_filled_by_code(self):
        out, slots, problems = spec.fill("{family_span.commits} commits since {family_span.first|date}", PROFILE)
        self.assertEqual(out, "553 commits since 26 Sep 2025")
        self.assertEqual([s["ref"] for s in slots], ["family_span.commits", "family_span.first"])
        self.assertEqual(problems, [])

    def test_a_digit_outside_a_slot_fails(self):
        _, _, problems = spec.fill("about 550 commits", PROFILE)
        self.assertTrue(any("digits outside a slot" in p for p in problems))

    def test_names_with_digits_are_allowed(self):
        _, _, problems = spec.fill("x3d_mcp and x3d.ML hold X3D files; tie E09 and series S07", PROFILE)
        self.assertEqual(problems, [])

    def test_repo_slots_with_dots_resolve(self):
        out, _, problems = spec.fill("{repo:x3d.ML/x3d_files|n}", PROFILE)
        self.assertEqual((out, problems), ("918", []))

    def test_an_unknown_slot_fails(self):
        _, _, problems = spec.fill("{family_span.nope}", PROFILE)
        self.assertTrue(any("does not resolve" in p for p in problems))


class Claims(unittest.TestCase):
    def render(self, claim):
        s = {"family": "t", "title": "t", "views": [{"id": "helix", "label": "Helix", "caption": "c", "claim": claim}], "tour": [], "questions": []}
        return spec.render_spec(s, PROFILE)

    def test_a_backed_git_claim_passes_and_says_who_speaks(self):
        r = self.render({"text": "{who_commits.shares.ai|pct} of commits", "backs": ["who_commits", "how_built:claude"], "basis": "git"})
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["views"][0]["claim"]["text"], "37% of commits")
        self.assertEqual(r["views"][0]["claim"]["says"], "git says")

    def test_a_git_claim_without_a_slot_fails(self):
        r = self.render({"text": "Claude made a third of the commits", "backs": ["who_commits"], "basis": "git"})
        self.assertTrue(any("must carry at least one slot" in p for p in r["problems"]))

    def test_a_claim_must_be_backed_by_something_real(self):
        r = self.render({"text": "a reading", "backs": ["no_such_signal", "how_built:nothing"], "basis": "reading"})
        self.assertEqual(len([p for p in r["problems"] if "backs" in p]), 2)

    def test_the_basis_must_be_known(self):
        r = self.render({"text": "a reading", "backs": ["who_commits"], "basis": "vibes"})
        self.assertTrue(any("basis" in p for p in r["problems"]))


class Vocabulary(unittest.TestCase):
    def test_the_stated_lineage_resolves_dot_prov_to_the_repo_that_holds_it(self):
        chain, resolved = ties.resolve_lineage(["x3d-blender-io", ".prov", "x3d.ML", "x3d_ai", "x3d.onto"],
                                               {"x3d-blender-io", "x3d.ML", "x3d_ai", "x3d.onto"}, {".prov": "x3d.ML"})
        self.assertEqual(chain, ["x3d-blender-io", "x3d.ML", "x3d_ai", "x3d.onto"])
        self.assertEqual(resolved, {".prov": "x3d.ML"})

    def test_an_unknown_word_stays_as_said(self):
        chain, resolved = ties.resolve_lineage(["a", "mystery"], {"a"}, {})
        self.assertEqual((chain, resolved), (["a", "mystery"], {}))


class Identities(unittest.TestCase):
    def test_a_person_is_matched_by_hash_and_the_config_never_holds_the_address(self):
        cfg = {"identities": [{"label": "Pat", "class": "you", "match_email_sha256": [harvest.ident_hash("pat@example.org")]},
                              {"label": "Claude", "class": "ai", "match_names": ["Claude"], "match_email_domains": ["anthropic.com"]}]}
        book = harvest.IdentityBook(cfg)
        self.assertEqual(book.classify("P. Somebody", "Pat@Example.org"), ("Pat", "you"))
        self.assertEqual(book.classify("Claude", "noreply@anthropic.com"), ("Claude", "ai"))
        self.assertEqual(book.classify("someone", "x@y.z"), ("other", "other"))
        self.assertNotIn("example.org", repr(cfg))

    def test_one_label_is_one_class(self):
        two = {"identities": [{"label": "Pat", "class": "you", "match_names": ["Pat"]}, {"label": "Pat", "class": "bot", "match_names": ["pat-bot"]}]}
        with self.assertRaises(ValueError):
            harvest.IdentityBook(two)
        with self.assertRaises(ValueError):                                # the default identity is other, of class other
            harvest.IdentityBook({"identities": [{"label": "other", "class": "you", "match_names": ["x"]}]})
        harvest.IdentityBook({"identities": [{"label": "Pat", "class": "you", "match_names": ["Pat"]}, {"label": "Pat", "class": "you", "match_names": ["P"]}]})


class Privacy(unittest.TestCase):
    def test_addresses_and_session_urls_are_caught(self):
        found = privacy_scan('{"a": "write to pat@example.org", "b": "https://claude.ai/code/session_abc123"}')
        self.assertEqual(len(found), 2)

    def test_clean_text_passes(self):
        self.assertEqual(privacy_scan('{"a": "x3d_mcp commit eb4b4b4, session s-1a2b3c"}'), [])


if __name__ == "__main__":
    unittest.main()
