"""The inference half of the JavaScript twin: EDA_INFER in x3dray/web/tools.js against x3dray/stats.py and x3dray/looks.py.

Three layers, each exact, none with a tolerance. (1) Primitives: SHA-256, the SplitMix64 stream, below(), canonical JSON,
decimal strings, code-point order, Cliff counts, midranks, TV parts, both permutation counts and Benjamini-Hochberg q, on
boundary and seeded random cases. (2) The twelve declared looks: the stage's whole looks section recomputed from the commit rows in
Node must come back byte for byte, including the adversarial datasets (every value tied, counts near 2**52, times over the whole
range, astral repo names, no pair, 19 and 20 commits on a side). (3) The ad hoc `compare` topic against looks.compare and the naive
oracle looks._n_stat/_n_perm, its seed rule, its family count and q, its refusals and its privacy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "x3dray" / "web"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eda_support import BASE_T, minimal_bundle, synthetic_dataset  # noqa: E402
from test_eda_tool import run_tools  # noqa: E402
from x3dray import eda, looks, stats  # noqa: E402

TOOLS = WEB / "tools.js"
HAS_NODE = shutil.which("node")


def node(script: str, payload, tools: Path = TOOLS, timeout: int = 120):
    """Run `script` as an ES module with tools.js as `T`, EDA_INFER as `K` and the parsed stdin JSON as `input`."""
    head = ("const T = await import(process.argv[1]); const K = T.EDA_INFER;\n"
            "const input = JSON.parse(await new Promise(r => { let s = ''; process.stdin.on('data', d => { s += d; }); process.stdin.on('end', () => r(s)); }));\n")
    run = subprocess.run(["node", "--input-type=module", "-e", head + script, tools.as_uri()], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=timeout)
    if run.returncode != 0:
        raise AssertionError(run.stderr[-2000:])
    return json.loads(run.stdout)


def canon_sha(x) -> str:
    return hashlib.sha256(eda.canonical_json(x).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------------------------- datasets
def ds_plain(seed=41, repos=3, per_repo=100, private=("r2",), plant=("ai_hours",)):
    return synthetic_dataset(seed=seed, repos=repos, per_repo=per_repo, private=private, plant=plant)


def ds_one_class():
    ds = synthetic_dataset(seed=9, repos=2, per_repo=60)
    for r in ds["repos"]:
        for c in r["commits"]:
            c["cls"], c["who"] = "you", "Alice"
    return ds


def ds_thin(ai_commits: int):
    ds = synthetic_dataset(seed=9, repos=1, per_repo=100)
    k = 0
    for c in ds["repos"][0]["commits"]:
        c["merge"] = False
        if c["cls"] == "ai":
            k += 1
            if k > ai_commits:
                c["cls"], c["who"] = "you", "Alice"
    return ds


def ds_ties():
    ds = synthetic_dataset(seed=2, repos=2, per_repo=60)
    k = 0
    for r in ds["repos"]:
        for c in r["commits"]:
            c.update(add=3, files=2, merge=False, default=True, t=BASE_T + k * 3600, cls="you" if k % 2 else "ai", who="x")
            c["del"] = 2
            k += 1
    return ds


def ds_huge():
    ds = synthetic_dataset(seed=4, repos=2, per_repo=60)
    rng = random.Random(4)
    for r in ds["repos"]:
        for c in r["commits"]:
            c["add"] = 2 ** 52 - rng.randint(0, 5000)
            c["del"] = rng.randint(0, 2 ** 52 - 1) if rng.random() < 0.3 else rng.randint(0, 9)
            c["files"] = rng.choice([1, 2, 2 ** 52 + rng.randint(0, 3), 3])
            c["t"] = rng.randint(-8_640_000_000_000, 8_640_000_000_000) if rng.random() < 0.5 else BASE_T + rng.randint(0, 10 ** 7)
        r["commits"].sort(key=lambda c: c["t"])
    return ds


def ds_unicode():
    ds = synthetic_dataset(seed=21, repos=4, per_repo=45)
    for r, n in zip(ds["repos"], ["\U0001F600repo", "rep￿o", "zé", "A"]):
        r["name"] = n
    return ds


def ds_astral_tie():
    """Two repos: the largest gap between the two groups is the same in both, so contrast:repo ties and the first name in code point
    order wins. U+E000 is above every surrogate, so a UTF-16 sort would put the astral name first and choose the other repo."""
    ds = synthetic_dataset(seed=33, repos=2, per_repo=70)
    for r, n in zip(ds["repos"], ["\U0001F600", "\ue000"]):
        r["name"] = n
    return ds


DATASETS = {
    "plain": ds_plain, "confound": lambda: ds_plain(seed=7, per_repo=60, private=(), plant=("confound",)), "ties": ds_ties, "huge": ds_huge,
    "unicode": ds_unicode, "astral_tie": ds_astral_tie, "no_pair": ds_one_class, "ai_19": lambda: ds_thin(19), "ai_20": lambda: ds_thin(20),
}
_CACHE: dict = {}


def built(name: str):
    """(dataset, eda section, bundle) for a named dataset; the stage and its own verify run once per name."""
    if name not in _CACHE:
        ds = DATASETS[name]()
        e, problems = eda.run(ds)
        assert problems == [], (name, problems)
        _CACHE[name] = (ds, e, minimal_bundle(ds, e))
    return copy.deepcopy(_CACHE[name])


# ---------------------------------------------------------------------------------------------------------------- 1. primitives
@unittest.skipUnless(HAS_NODE, "Node is required for the JavaScript twin tests")
class Primitives(unittest.TestCase):
    def test_sha256_matches_hashlib_around_every_padding_boundary(self):
        rng = random.Random(1)
        texts = ["", "abc"] + ["".join(chr(rng.randint(32, 126)) for _ in range(n)) for n in list(range(0, 140)) + [191, 192, 193, 1000]]
        got = node("console.log(JSON.stringify(input.map(s => K.sha256Text(s))));", texts)
        self.assertEqual(got, [hashlib.sha256(t.encode()).hexdigest() for t in texts])
        bad = node("const r = []; for (const s of input) { try { K.sha256Text(s); r.push('accepted'); } catch (e) { r.push(e.constructor.name); } } console.log(JSON.stringify(r));",
                   ["caf\u00e9", "tab\t", "\U0001F600", "x\x7f", "lone\ud800"])
        self.assertEqual(bad, ["RangeError"] * 5)          # a seed label is printable ASCII: no re-encoding choice exists to disagree on

    def test_splitmix64_stream_and_below_match_stats(self):
        rng = random.Random(12345)
        seeds = [0, 1, 2 ** 64 - 1, 2 ** 63, 2 ** 32 - 1, 2 ** 32, 0xFFFFFFFF00000000, 0x9E3779B97F4A7C15, 0x7F4A7C15] + [rng.getrandbits(64) for _ in range(300)]
        ks = [1, 2, 3, 7, 10, 999, 12345, 50000, 65536, 2 ** 20, 2 ** 20 + 1, 2 ** 21 - 1, 2 ** 21]
        want = []
        for s in seeds:
            r, r2 = stats.SplitMix64(s), stats.SplitMix64(s)
            want.append([[str(r.next_u64()) for _ in range(6)], [r2.below(k) for k in ks for _ in range(3)]])
        got = node("""
          const out = input.seeds.map(s => { const S = BigInt(s), hi = Number(S >> 32n), lo = Number(S & 0xFFFFFFFFn);
            const r = K.splitMix(hi, lo), r2 = K.splitMix(hi, lo);
            return [Array.from({ length: 6 }, () => { const [h, l] = r.word(); return ((BigInt(h) << 32n) | BigInt(l)).toString(); }),
                    input.ks.flatMap(k => [r2.below(k), r2.below(k), r2.below(k)])]; });
          console.log(JSON.stringify(out));""", {"seeds": [str(s) for s in seeds], "ks": ks})
        self.assertEqual(got, want)
        refused = node("const r = []; for (const k of input) { try { K.splitMix(1, 2).below(k); r.push('accepted'); } catch (e) { r.push(e.constructor.name); } } console.log(JSON.stringify(r));",
                       [0, -1, 2 ** 21 + 1, 1.5e300, 2.5])
        self.assertEqual(refused, ["RangeError"] * 5)

    def test_below_agrees_with_a_bigint_reference_where_the_low_word_decides(self):
        """(u64 * k) >> 64 differs from the high word alone only when the low word carries; that is about k / 2**32 of the draws, so the large k
        are drawn a million times against a BigInt transcription of the Python line (the Python goldens above hold too few of them to see it)."""
        got = node("""
          const M64 = (1n << 64n) - 1n; let bad = 0, n = 0, carried = 0;
          for (let t = 0; t < 120; t++) {
            const seed = (BigInt(t * 2654435761 % 4294967296) << 32n) | BigInt((t * 40503 + 7) % 4294967296), r = K.splitMix(Number(seed >> 32n), Number(seed & 0xFFFFFFFFn));
            let s = seed & M64;
            for (let j = 0; j < 25000; j++) {
              const k = [2 ** 21, 2 ** 21 - 1, 2 ** 20 + 1, 2 ** 19 + 12345][j & 3];
              s = (s + 0x9E3779B97F4A7C15n) & M64; let z = s;
              z = ((z ^ (z >> 30n)) * 0xBF58476D1CE4E5B9n) & M64; z = ((z ^ (z >> 27n)) * 0x94D049BB133111EBn) & M64; z ^= z >> 31n;
              const want = Number((z * BigInt(k)) >> 64n), highOnly = Number(((z >> 32n) * BigInt(k)) >> 32n);
              n++; if (want !== highOnly) carried++;
              if (r.below(k) !== want) bad++;
            }
          }
          console.log(JSON.stringify({ bad, n, carried }));""", {})
        self.assertEqual(got["bad"], 0)
        self.assertGreater(got["carried"], 100)             # the reference does see the carry, so a version that drops the low word would fail above

    def test_the_constants_cannot_be_edited_from_outside(self):
        got = node("""
          const tried = [];
          for (const f of [() => { K.T.MIN_SIDE = 1; }, () => { K.T.ALPHA[0] = 9; }, () => { K.DECLARED.push(['x', 'y', 'z']); }, () => { K.LIMITS.ADHOC_CAP = 1; }, () => { K.runLooks = null; }]) {
            try { f(); tried.push('changed'); } catch (e) { tried.push(e.constructor.name); }
          }
          console.log(JSON.stringify({ tried, min: K.T.MIN_SIDE, alpha: K.T.ALPHA, n: K.DECLARED.length, cap: K.LIMITS.ADHOC_CAP, run: typeof K.runLooks }));""", {})
        self.assertEqual(got, {"tried": ["TypeError"] * 5, "min": 20, "alpha": [1, 20], "n": 12, "cap": 4000000, "run": "function"})

    def test_known_answers_constants_and_manifest(self):
        got = node("""
          console.log(JSON.stringify({ bad: K.selfCheck(), manifest: K.manifestSha256(), T: K.T, declared: K.DECLARED, lim: K.LIMITS,
            seeds: input.map(l => K.seedText(K.seedHiLo(l))), forks: T.EDA_FORKS }));""", [looks.SEED_PREFIX + d[0] for d in looks.DECLARED])
        self.assertEqual(got["bad"], [])
        self.assertEqual(got["manifest"], looks.manifest_sha256())            # the battery and every threshold, in both languages
        self.assertEqual(got["T"], json.loads(json.dumps(looks.THRESHOLDS)))
        self.assertEqual(got["declared"], [list(d) for d in looks.DECLARED])
        self.assertEqual(got["seeds"], [str(stats.seed_from(looks.SEED_PREFIX + d[0])) for d in looks.DECLARED])
        self.assertEqual(got["forks"], list(stats.FORKS))
        self.assertEqual(got["lim"]["ADHOC_CAP"], looks.ADHOC_WORK_CAP)

    def test_canonical_json_matches_the_stage(self):
        rng = random.Random(3)
        alphabet = ['a', 'Z', '"', '\\', '\n', '\t', '\x00', '\x1f', '\x7f', '\x80', 'é', ' ', '￿', '\U0001F600', '\U0010ffff', '/', ' ']

        def value(depth=0):
            kind = rng.choice(["int", "str", "list", "dict", "bool", "none"] if depth < 3 else ["int", "str", "bool", "none"])
            if kind == "int":
                return rng.choice([0, -1, 7, 2 ** 53 - 1, -(2 ** 53 - 1), rng.randint(-10 ** 6, 10 ** 6)])
            if kind == "str":
                return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 8)))
            if kind == "bool":
                return rng.random() < 0.5
            if kind == "none":
                return None
            if kind == "list":
                return [value(depth + 1) for _ in range(rng.randint(0, 4))]
            return {"".join(rng.choice(alphabet) for _ in range(rng.randint(0, 4))): value(depth + 1) for _ in range(rng.randint(0, 4))}

        cases = [value() for _ in range(400)]
        got = node("console.log(JSON.stringify(input.map(x => K.canon(x))));", cases)
        self.assertEqual(got, [eda.canonical_json(c) for c in cases])
        refused = node("const r = []; for (const x of input) { try { K.canon(x); r.push('accepted'); } catch (e) { r.push(e.constructor.name); } } console.log(JSON.stringify(r));",
                       [1.5, 2 ** 53, -(2 ** 53), [1e300]])
        self.assertEqual(refused, ["RangeError"] * 4)      # a float or an integer past 2**53 is not canonical here, as in the stage's float scan

    def test_decimal_strings_and_code_point_order(self):
        rng = random.Random(5)
        cases = [[n, d, p] for n, d, p in [(5, 2, 0), (-5, 2, 0), (1, 3, 4), (-1, 20000, 3), (0, 7, 3), (1, 2000, 3), (-1, 2000, 3), (999999, 1000, 3)]]
        cases += [[rng.randint(-10 ** 12, 10 ** 12), rng.randint(1, 10 ** 10), rng.randint(0, 6)] for _ in range(800)]
        got = node("console.log(JSON.stringify(input.map(([n, d, p]) => K.decimalStr(n, d, p))));", cases)
        self.assertEqual(got, [stats.decimal_str(n, d, p) for n, d, p in cases])
        names = ["b", "a", "\U0001F600", "￿", "", "A", "zé", "zé", "", "a\U0001F600", "a￿", "퟿", "\U00010000"]
        got = node("console.log(JSON.stringify(input.slice().sort(K.cmpStr)));", names)
        self.assertEqual(got, sorted(names))                  # Python orders by code point; a plain JS sort would put U+1F600 before U+FFFF

    def test_cliff_midranks_and_tv_parts_match_stats(self):
        rng = random.Random(7)
        ords, tvs = [], []
        for _ in range(250):
            hi = rng.choice([1, 2, 5, 40, 10 ** 6, 2 ** 52])
            ords.append([[rng.randint(0, hi) for _ in range(rng.randint(1, 30))], [rng.randint(0, hi) for _ in range(rng.randint(1, 30))]])
            pool = rng.choice([list(range(3)), list(range(24)), ["r0", "r1", "\U0001F600", "￿", "z"]])
            a, b = [rng.choice(pool) for _ in range(rng.randint(1, 30))], [rng.choice(pool) for _ in range(rng.randint(1, 30))]
            tvs.append([a, b, rng.choice([None, pool[0], pool[-1], "absent" if isinstance(pool[0], str) else 99])])
        got = node("""
          const tally = vs => { const m = new Map(); for (const v of vs) m.set(v, (m.get(v) || 0) + 1); return m; };
          console.log(JSON.stringify({ cl: input.o.map(([a, b]) => K.cliffCounts(a, b)),
            mr: input.o.map(([a, b]) => Array.from(K.doubledMidranks(a.concat(b)))),
            tv: input.t.map(([a, b, ref]) => K.tvParts(tally(a), tally(b), a.length, b.length, ref)) }));""", {"o": ords, "t": tvs})
        self.assertEqual(got["cl"], [list(stats.cliff_counts(a, b)) for a, b in ords])
        self.assertEqual(got["mr"], [stats.doubled_midranks(a + b) for a, b in ords])
        want = []
        for a, b, ref in tvs:
            ca, cb = {}, {}
            for v in a:
                ca[v] = ca.get(v, 0) + 1
            for v in b:
                cb[v] = cb.get(v, 0) + 1
            want.append(list(stats.tv_parts(ca, cb, len(a), len(b), ref)))
        self.assertEqual(got["tv"], want)

    def test_permutation_counts_match_stats(self):
        rng = random.Random(11)
        cl, tv = [], []
        for _ in range(60):
            n = rng.randint(6, 70)
            nd = rng.randint(1, n - 1)
            hi = rng.choice([2, 5, 50, 10 ** 9])
            vals = [rng.randint(0, hi) for _ in range(n)]
            r2 = stats.doubled_midranks(vals)
            a, b = vals[:nd], vals[nd:]
            gt, lt = stats.cliff_counts(a, b)
            cl.append([r2, nd, abs(gt - lt), rng.choice([0, 1, abs(gt - lt) // 2]), rng.randint(5, 60), str(rng.getrandbits(64))])
            bins = [rng.randint(0, rng.choice([1, 3, 9])) for _ in range(n)]
            tv.append([bins, nd, rng.randint(0, n * n), rng.randint(5, 60), str(rng.getrandbits(64))])
        got = node("""
          const rg = s => { const S = BigInt(s); return K.splitMix(Number(S >> 32n), Number(S & 0xFFFFFFFFn)); };
          console.log(JSON.stringify({ c: input.c.map(([r2, nd, s, d, M, seed]) => K.permCountCliff(r2, nd, s + d, M, rg(seed))),
                                        t: input.t.map(([bins, nd, t, M, seed]) => K.permCountTv(bins, nd, t, M, rg(seed))) }));""", {"c": cl, "t": tv})
        self.assertEqual(got["c"], [stats.perm_count_cliff(r2, nd, s + d, M, stats.SplitMix64(int(seed))) for r2, nd, s, d, M, seed in cl])
        self.assertEqual(got["t"], [stats.perm_count_tv(bins, nd, t, M, stats.SplitMix64(int(seed))) for bins, nd, t, M, seed in tv])

    def test_benjamini_hochberg_matches_stats(self):
        rng = random.Random(13)
        cases = []
        for _ in range(300):
            M = rng.choice([199, 500, 999])
            n = rng.randint(1, 14)
            ks = [rng.choice([0, 0, 1, 5, 20, 60, M // 2, M]) for _ in range(n)]
            m = n + rng.choice([0, 0, 3, 18, 100, 2000])
            cases.append([[[f"id{j}", [k + 1, M + 1]] for j, k in enumerate(ks)] + ([["unrun", [1, 1]]] if rng.random() < 0.4 else []), 0, m])
            cases[-1][2] = max(m, len(cases[-1][0]))
        got = node("console.log(JSON.stringify(input.map(([items, _, m]) => [...K.bhQ(items, m).entries()])));", cases)
        for (items, _, m), res in zip(cases, got):
            want = stats.bh_q([(n, Fraction(p[0], p[1])) for n, p in items], m)
            self.assertEqual(dict((k, tuple(v)) for k, v in res), {k: (v.numerator, v.denominator) for k, v in want.items()})
        err = node("const r = []; for (const [items, m] of input) { try { K.bhQ(items, m); r.push('accepted'); } catch (e) { r.push(e.constructor.name); } } console.log(JSON.stringify(r));",
                   [[[["a", [1, 5]], ["b", [1, 5]]], 1], [[["a", [0, 5]]], 3], [[["a", [6, 5]]], 3]])
        self.assertEqual(err, ["RangeError"] * 3)

    def test_a_sample_is_named_by_its_digests_in_digest_order(self):
        rng = random.Random(17)
        cases = []
        for _ in range(80):
            pool = rng.choice([list(range(5)), [rng.randint(0, 2 ** 52) for _ in range(9)], ["r0", "\U0001F600", "z\u00e9"]])
            cases.append([[rng.choice(pool) for _ in range(rng.randint(0, 25))], [rng.choice(pool) for _ in range(rng.randint(0, 25))], rng.choice(looks.CONTRAST_MEASURES)])
        got = node("""
          const dig = v => K.sha256Text(K.canon(v));
          console.log(JSON.stringify(input.map(([a, b, m]) => { const da = dig(a), db = dig(b), sw = K.cmpStr(db, da) < 0;
            return [K.canon({ measure: m, a: sw ? db : da, b: sw ? da : db }), sw]; })));""", cases)
        for (a, b, m), (canon, swapped) in zip(cases, got):
            da, db = looks._digest(a), looks._digest(b)
            self.assertEqual(swapped, db < da)
            self.assertEqual(canon, looks._canon({"measure": m, "a": db if swapped else da, "b": da if swapped else db}))
        self.assertTrue(any(c[1] for c in got) and not all(c[1] for c in got))


# ---------------------------------------------------------------------------------------------------------------- 2. the twelve declared looks
@unittest.skipUnless(HAS_NODE, "Node is required for the JavaScript twin tests")
class Recompute(unittest.TestCase):
    def recompute(self, bundle, tools: Path = TOOLS):
        script = ("const B = input; const t = Object.fromEntries(T.makeTools(B, null).map(x => [x.name, x]));\n"
                  "const r = t.eda.execute({ topic: 'recompute' });\n"
                  "const rows = K.checkRows(B.commits), sec = K.runLooks(rows, B.eda.classes.labels).section;\n"
                  "console.log(JSON.stringify({ tool: r, kernel_canon: K.canon(sec), unchanged: true }));")
        return node(script, bundle, tools)

    def test_the_stored_section_comes_back_byte_for_byte_on_every_dataset(self):
        for name in DATASETS:
            with self.subTest(name):
                ds, e, b = built(name)
                got = self.recompute(b)
                self.assertEqual(got["kernel_canon"], eda.canonical_json(e["looks"]))
                tool = got["tool"]
                self.assertTrue(tool["match"], tool["differences"])
                self.assertEqual((tool["differences_total"], tool["numbers_match"], tool["wording_match"]), (0, True, True))
                self.assertEqual(tool["stored_sha256"], canon_sha(e["looks"]))        # the digest the page prints is the one Python computes
                self.assertEqual(tool["recomputed_sha256"], tool["stored_sha256"])
                self.assertEqual(tool["ledger"], e["looks"]["ledger"])
                self.assertEqual([r["id"] for r in tool["looks"]], [f["id"] for f in e["looks"]["findings"]])
                self.assertTrue(all(r["match"] for r in tool["looks"]))
                self.assertIn("byte for byte", tool["summary"])

    def test_the_astral_tie_is_resolved_by_code_point_order(self):
        ds, e, b = built("astral_tie")
        repo_look = next(f for f in e["looks"]["findings"] if f["id"] == "contrast:repo")
        self.assertEqual(repo_look["status"], "evaluated")
        self.assertEqual(repo_look["effect"]["dominant_bin"], "\ue000")             # Python: U+E000 < U+1F600
        self.assertTrue(self.recompute(b)["tool"]["match"])

    def test_the_work_cap_branch_matches_when_both_languages_lower_the_cap(self):
        with mock.patch.dict(looks.THRESHOLDS, {"WORK_CAP": 5000}):
            ds = DATASETS["plain"]()
            e, problems = eda.run(ds)
            self.assertEqual(problems, [])
            self.assertTrue(e["looks"]["ledger"]["work_exceeded"])
            self.assertEqual(e["looks"]["ledger"]["M"], 199)
            with tempfile.TemporaryDirectory() as tmp:
                patched = Path(tmp) / "tools.js"
                text = TOOLS.read_text()
                self.assertEqual(text.count("WORK_CAP: 10000000"), 1)
                patched.write_text(text.replace("WORK_CAP: 10000000", "WORK_CAP: 5000"))
                got = self.recompute(minimal_bundle(ds, e), patched)
        self.assertTrue(got["tool"]["match"], got["tool"]["differences"])
        self.assertEqual(got["kernel_canon"], eda.canonical_json(e["looks"]))        # includes the manifest hash, so the patched thresholds agree too

    def test_a_look_exactly_at_alpha_is_graded_and_one_count_above_is_not(self):
        # The cut is "add-one count at most alpha". Lower alpha to exactly (k+1)/(M+1) of a real look so the
        # tie is reached with real data; both languages must grade it, and must not grade it one count lower.
        base = looks.THRESHOLDS["ALPHA"]
        e0, _ = eda.run(DATASETS["plain"]())
        look = next(f for f in e0["looks"]["findings"] if f["id"] == "contrast:weekday")
        k, M = look["null_model"]["k_ge"], look["null_model"]["M"]
        self.assertEqual((k, M, base), (58, 999, (1, 20)))
        for alpha, graded in (((k + 1, M + 1), True), ((k, M + 1), False)):
            with self.subTest(alpha=alpha), mock.patch.dict(looks.THRESHOLDS, {"ALPHA": alpha}):
                ds = DATASETS["plain"]()
                e, problems = eda.run(ds)
                self.assertEqual(problems, [])
                f = next(f for f in e["looks"]["findings"] if f["id"] == "contrast:weekday")
                self.assertEqual(f["grade_reason"] != "not_surprising", graded)
                with tempfile.TemporaryDirectory() as tmp:
                    patched = Path(tmp) / "tools.js"
                    text = TOOLS.read_text()
                    self.assertEqual(text.count("ALPHA: [1, 20]"), 1)
                    patched.write_text(text.replace("ALPHA: [1, 20]", "ALPHA: [%d, %d]" % alpha))
                    got = self.recompute(minimal_bundle(ds, e), patched)
                self.assertTrue(got["tool"]["match"], got["tool"]["differences"])
                self.assertEqual(got["kernel_canon"], eda.canonical_json(e["looks"]))

    def test_a_changed_stored_value_or_row_is_found_and_named_by_path_only(self):
        ds, e, b = built("plain")
        cases = {
            "k_ge": lambda x: x["eda"]["looks"]["findings"][0]["null_model"].__setitem__("k_ge", x["eda"]["looks"]["findings"][0]["null_model"]["k_ge"] + 1),
            "q": lambda x: x["eda"]["looks"]["findings"][2]["q"].__setitem__(0, x["eda"]["looks"]["findings"][2]["q"][0] + 1),
            "grade": lambda x: x["eda"]["looks"]["findings"][3].__setitem__("grade", "S"),
            "seed": lambda x: x["eda"]["looks"]["findings"][4]["null_model"].__setitem__("seed", "1"),
            "manifest": lambda x: x["eda"]["looks"]["ledger"].__setitem__("manifest_sha256", "0" * 64),
            "row_a": lambda x: x["commits"][17].__setitem__("a", x["commits"][17]["a"] + 1),
            "row_class": lambda x: x["commits"].__setitem__(5, {**x["commits"][5], "c": "ai" if x["commits"][5]["c"] == "you" else "you"}),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                x = copy.deepcopy(b)
                mutate(x)
                tool = self.recompute(x)["tool"]
                self.assertFalse(tool["match"])
                self.assertFalse(tool["numbers_match"])
                self.assertGreaterEqual(tool["differences_total"], 1)
                self.assertTrue(all(re.fullmatch(r"looks[\w.\[\]:-]*", p) for p in tool["differences"]))
                self.assertNotRegex(json.dumps(tool), r'"r2"')                  # a private repo's name is not in what the tool prints
        x = copy.deepcopy(b)
        x["eda"]["looks"]["findings"][5]["extra"] = 1
        del x["eda"]["looks"]["findings"][6]["grade_kind"]
        tool = self.recompute(x)["tool"]
        self.assertEqual(sorted(tool["differences"]), ["looks.findings[contrast:repo].extra", "looks.findings[drift:first:lines].grade_kind"])
        x = copy.deepcopy(b)
        x["eda"]["looks"]["findings"][1]["text"] += "."
        x["eda"]["looks"]["scope"] += " "
        tool = self.recompute(x)["tool"]
        self.assertEqual((tool["numbers_match"], tool["wording_match"], tool["differences_total"]), (True, False, 2))
        self.assertIn("looks.findings[contrast:files].text", tool["differences"])

    def test_python_verify_and_the_browser_flag_the_same_paths_on_the_same_faults(self):
        """looks.verify recomputes with deliberately naive code; the page recomputes with a port of the stage. Neither shares code with the
        other, so a fault they both flag by the same look id and field is not one reader's reading of the other."""
        ds, e, b = built("confound")
        rows = eda.indexed_rows(ds)
        faults = {
            "k_ge": lambda f: f["null_model"].__setitem__("k_ge", f["null_model"]["k_ge"] + 1),
            "q": lambda f: f["q"].__setitem__(1, f["q"][1] + 1),
            "grade": lambda f: f.__setitem__("grade", "C" if f["grade"] != "C" else "S"),
            "seed": lambda f: f["null_model"].__setitem__("seed", "7"),
            "effect": lambda f: f["effect"].__setitem__("num", f["effect"]["num"] + 1),
            "fork": lambda f: f["checks"]["forks"]["per_fork"]["all"].__setitem__("agrees", not f["checks"]["forks"]["per_fork"]["all"]["agrees"]),
            "text": lambda f: f.__setitem__("text", f["text"] + "!"),
        }
        for name, fault in faults.items():
            with self.subTest(fault=name):
                x = copy.deepcopy(b)
                target = next(f for f in x["eda"]["looks"]["findings"] if f["id"] == "contrast:lines")
                fault(target)
                py = {tuple(m.groups()) for m in map(re.compile(r"^(.+?): look\.(.+) differs$").match, looks.verify(x["eda"]["looks"], rows)) if m}
                js = {tuple(m.groups()) for m in map(re.compile(r"^looks\.findings\[(.+?)\]\.(.+)$").match, self.recompute(x)["tool"]["differences"]) if m}
                self.assertEqual(py, js)
                self.assertEqual(len(py), 1, py)

    def test_a_row_that_breaks_the_stats_contract_refuses_the_whole_call(self):
        ds, e, b = built("plain")
        bad = {
            "position": lambda c: c[3].__setitem__("i", 99), "t_text": lambda c: c[3].__setitem__("t", "5"), "t_range": lambda c: c[3].__setitem__("t", 8_640_000_000_001),
            "a_negative": lambda c: c[3].__setitem__("a", -1), "a_float": lambda c: c[3].__setitem__("a", 1.5), "sum": lambda c: c[3].update(a=2 ** 52, d=2 ** 52),
            "m_bool": lambda c: c[3].__setitem__("m", True), "b_two": lambda c: c[3].__setitem__("b", 2), "r_empty": lambda c: c[3].__setitem__("r", ""),
            "c_number": lambda c: c[3].__setitem__("c", 7), "hole": lambda c: c.pop(10), "not_object": lambda c: c.__setitem__(3, "SECRET"),
        }
        for name, mutate in bad.items():
            with self.subTest(name):
                x = copy.deepcopy(b)
                x["commits"][3]["r"] = "SECRET-NAME" if name != "r_empty" else ""
                mutate(x["commits"])
                (r,), _ = run_tools(x, [["eda", {"topic": "recompute"}]])
                self.assertIn("error", r)
                self.assertRegex(r["error"], r"^commit row #\d+")
                self.assertNotIn("SECRET", r["error"])
        out = node("""
          const rows = n => Array.from({ length: n }, (_, i) => ({ i, r: 'x', t: i, c: 'you', a: 1, d: 1, f: 1, m: 0, b: 1 }));
          const r = []; for (const n of [K.LIMITS.MAX_ROWS, K.LIMITS.MAX_ROWS + 1]) { try { K.checkRows(rows(n)); r.push('ok'); } catch (e) { r.push(e.constructor.name); } }
          console.log(JSON.stringify(r));""", {})
        self.assertEqual(out, ["ok", "RangeError"])

    def test_an_oversized_recomputation_is_refused_before_any_draw(self):
        ds, e, b = built("plain")
        got = node("""
          const { section } = K.runLooks(K.checkRows(input.commits), input.eda.classes.labels);
          let msg = null; try { K.runLooks(K.checkRows(input.commits), input.eda.classes.labels, section.ledger.W * section.ledger.M - 1); } catch (e) { msg = e.constructor.name + ': ' + e.message; }
          console.log(JSON.stringify({ msg, draws: section.ledger.W * section.ledger.M }));""", b)
        self.assertRegex(got["msg"], r"^RangeError: recomputing needs \d+ permutation draws")

    def test_a_runtime_that_fails_its_known_answers_computes_nothing(self):
        ds, e, b = built("ai_20")
        got = node("""
          Math.imul = () => 0;                               // an engine whose 32-bit multiply is wrong
          const t = Object.fromEntries(T.makeTools(input, null).map(x => [x.name, x]));
          let msg = null; try { t.eda.execute({ topic: 'recompute' }); } catch (e) { msg = e.message; }
          console.log(JSON.stringify({ msg, bad: K.selfCheck() }));""", b)
        self.assertIn("known-answer checks", got["msg"])
        self.assertIn("splitmix64", got["bad"])

    def test_the_kernel_uses_no_clock_no_random_and_no_float_routines(self):
        text = TOOLS.read_text()
        block = text[text.index("// BEGIN EDA INFERENCE KERNEL"):text.index("// END EDA INFERENCE KERNEL")]
        code = "\n".join(re.sub(r"\s//.*$", "", ln) for ln in block.splitlines() if not ln.lstrip().startswith("//"))
        for banned in (r"\bDate\b", r"Math\.random", r"Math\.round", r"toFixed", r"parseFloat", r"\bperformance\b", r"\bcrypto\b", r"\beval\b", r"new Function",
                       r"setTimeout", r"Number\.EPSILON", r"\b\d+\.\d+\b"):
            self.assertIsNone(re.search(banned, code), banned)
        self.assertEqual(len(re.findall(r"^export ", block, flags=re.M)), 1)         # one top-level name: EDA_INFER
        ds, e, b = built("ai_20")
        stripped = re.sub(r"^export (?=(?:const|function|async function)\b)", "", text, flags=re.M)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tools.txt").write_text(stripped)
            (Path(tmp) / "bundle.json").write_text(json.dumps(b))
            script = """
              const fs = await import('node:fs'); const vm = await import('node:vm');
              const ctx = vm.createContext({ TextEncoder, console });
              vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
              ctx.B = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
              // The classic-script global scope (as in the Observatory), then the things the kernel must never touch are made to throw.
              const out = vm.runInContext(`
                Date = undefined; Math.random = () => { throw new Error('random'); }; globalThis.crypto = undefined; globalThis.performance = undefined;
                const rows = EDA_INFER.checkRows(B.commits), sec = EDA_INFER.runLooks(rows, B.eda.classes.labels).section;
                EDA_INFER.canon(sec)`, ctx);
              console.log(JSON.stringify(out));"""
            run = subprocess.run(["node", "--input-type=module", "-e", script, str(Path(tmp) / "tools.txt"), str(Path(tmp) / "bundle.json")],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), eda.canonical_json(e["looks"]))


# ---------------------------------------------------------------------------------------------------------------- 3. compare
def groups_by_filter(rows, ea, eb):
    """Brute-force group membership for the two filters used below (who, repo, hour, weekday), independent of tools.js."""
    def keep(r, f):
        if "who" in f and r["c"] not in f["who"]:
            return False
        if "repo" in f and r["r"] not in f["repo"]:
            return False
        if "hour" in f and stats.hour_of(r["t"]) != f["hour"]:
            return False
        if "weekday" in f and stats.weekday_of(r["t"]) != f["weekday"]:
            return False
        return True
    return [r["i"] for r in rows if keep(r, ea)], [r["i"] for r in rows if keep(r, eb)]


def flip(effect):
    out = dict(effect)
    out["direction"] = -effect["direction"]
    if effect["kind"] == "cliff_delta":
        out["num"], out["permille"] = -effect["num"], -effect["permille"]
    return out


def expect(rows, ea, eb, fork, measure):
    """Python's answer for the question in the asker's order: looks.compare gives the canonical order, turned back here when swapped."""
    ia, ib = groups_by_filter(rows, ea, eb)
    r = looks.compare(rows, ia, ib, measure, fork)
    if r["swapped"]:
        return {**r, "n_a": r["n_second"], "n_b": r["n_first"], "effect": flip(r["effect"])}
    return {**r, "n_a": r["n_first"], "n_b": r["n_second"]}


@unittest.skipUnless(HAS_NODE, "Node is required for the JavaScript twin tests")
class Compare(unittest.TestCase):
    def setUp(self):
        self.ds, self.e, self.b = built("plain")
        self.rows = eda.indexed_rows(self.ds)

    def ask(self, calls, bundle=None):
        res, unchanged = run_tools(bundle or self.b, [["eda", {"topic": "compare", **c}] for c in calls])
        self.assertTrue(unchanged)
        return res

    def test_every_measure_and_fork_matches_looks_compare(self):
        ea, eb = {"who": ["ai"]}, {"who": ["you"]}
        calls = [{"a": {"who": "ai"}, "b": {"who": "you"}, "fork": f, "measure": m} for f in stats.FORKS for m in looks.CONTRAST_MEASURES]
        for call, r in zip(calls, self.ask(calls)):
            with self.subTest(fork=call["fork"], measure=call["measure"]):
                got = r["value"]
                want = expect(self.rows, ea, eb, call["fork"], call["measure"])
                self.assertEqual((got["groups"]["a"]["n"], got["groups"]["b"]["n"]), (want["n_a"], want["n_b"]))
                self.assertEqual(got["null_model"]["k_ge"], want["k_ge"])
                self.assertEqual((got["null_model"]["M"], got["null_model"]["seed"], got["null_model"]["p"], got["null_model"]["draws"]),
                                 (want["M"], want["seed"], want["p"], want["draws"]))
                eff = {k: got["effect"][k] for k in ("kind", "num", "den", "permille", "direction", "dominant_bin", "floor_met")}
                self.assertEqual(eff, want["effect"])
                self.assertEqual(got["null_model"]["p_text"], stats.decimal_str(want["p"][0], want["p"][1], 4))
                self.assertIsNone(got["grade"])

    def test_the_naive_oracle_agrees_too(self):
        """looks._n_stat and _n_perm recompute with other code (counting against sorted values, a Fraction histogram, a shuffle per permutation)."""
        ea, eb = {"who": ["ai"]}, {"who": ["you"]}
        for fork, measure in [("all", "lines"), ("no_merge", "files"), ("all", "gap_s"), ("default_branch", "hour4"), ("no_mass", "weekday"), ("all", "repo")]:
            with self.subTest(fork=fork, measure=measure):
                (r,) = self.ask([{"a": {"who": "ai"}, "b": {"who": "you"}, "fork": fork, "measure": measure}])
                label = expect(self.rows, ea, eb, fork, measure)["label"]
                keep = [x for x in sorted(self.rows, key=lambda x: (x["t"], x["i"])) if looks._n_keep(x, fork)]
                av, bv = [], []
                for k, x in enumerate(keep):
                    if x["c"] not in ("ai", "you"):
                        continue
                    if measure == "gap_s":
                        if k == 0:
                            continue
                        v = x["t"] - keep[k - 1]["t"]
                    else:
                        v = {"lines": x["a"] + x["d"], "files": x["f"], "hour4": ((x["t"] % 86400) // 3600) // 4, "weekday": ((x["t"] // 86400) + 3) % 7, "repo": x["r"]}[measure]
                    (av if x["c"] == "ai" else bv).append(v)
                swapped = looks._digest(bv) < looks._digest(av)
                x, y = (bv, av) if swapped else (av, bv)                      # the canonical order the stream is defined in
                head = looks._n_stat(measure, x, y)
                M = max(199, min(999, looks.ADHOC_WORK_CAP // min(len(av), len(bv))))
                self.assertEqual(r["value"]["null_model"]["k_ge"], looks._n_perm(measure, x, y, head, M, label))
                self.assertEqual(abs(r["value"]["effect"]["num"]), abs(head["num"]))

    def test_asking_the_other_way_round_is_the_same_question(self):
        a, b = {"who": "ai"}, {"who": "you"}
        r1, r2, r3 = self.ask([{"a": a, "b": b}, {"a": b, "b": a}, {"a": a, "b": b, "measure": "hour4"}])
        x, y = r1["value"], r2["value"]
        self.assertEqual(x["null_model"], y["null_model"])
        self.assertEqual(x["null_model"]["k_ge"], y["null_model"]["k_ge"])
        self.assertEqual(x["null_model"]["seed"], y["null_model"]["seed"])
        self.assertEqual(x["groups"]["a"], {**y["groups"]["b"]})
        self.assertEqual(x["effect"]["num"], -y["effect"]["num"])
        self.assertEqual((x["effect"]["direction"], x["effect"]["permille"]), (-y["effect"]["direction"], -y["effect"]["permille"]))
        self.assertEqual(y["looks_asked_this_session"], 1)                       # one question: the second ask did not add a look
        self.assertEqual(r3["value"]["looks_asked_this_session"], 2)             # a different measure is a different question, and a look
        self.assertEqual(y["family"]["comparisons"], 1)

    def test_two_spellings_of_one_sample_are_one_look_with_one_stream(self):
        """min_lines 0, the alias claude, an open date range and the list of every repo name the same commits."""
        base = {"a": {"who": "ai"}, "b": {"who": "you"}, "measure": "lines", "fork": "all"}
        calls = [base, {**base, "a": {"who": "ai", "min_lines": 0}}, {**base, "a": {"who": "claude"}, "b": {"who": "you", "max_lines": 10 ** 9}},
                 {**base, "a": {"who": "ai", "from": "2000-01-01", "repo": "r0,r1,r2"}}]
        res = self.ask(calls)
        seeds = {r["value"]["null_model"]["seed"] for r in res}
        self.assertEqual(len(seeds), 1)
        self.assertEqual([r["value"]["looks_asked_this_session"] for r in res], [1, 1, 1, 1])
        self.assertEqual({json.dumps(r["value"]["null_model"]) for r in res}, {json.dumps(res[0]["value"]["null_model"])})

    def test_family_count_and_q_follow_every_look_taken(self):
        cmp = lambda m: ["eda", {"topic": "compare", "a": {"who": "ai"}, "b": {"who": "you"}, "measure": m}]
        calls = [cmp("lines"), cmp("files"), ["eda", {"topic": "slice", "who": "ai"}], ["query_commits", {"hour": 3}], cmp("hour4")]
        res, _ = run_tools(self.b, calls)
        first, last = res[0]["value"], res[-1]["value"]
        self.assertEqual(last["looks_asked_this_session"], 5)
        self.assertEqual({k: last["family"][k] for k in ("declared", "comparisons", "other_looks", "m")}, {"declared": 12, "comparisons": 3, "other_looks": 2, "m": 17})
        self.assertEqual(first["family"]["m"], 13)                                  # the first answer was given when the family was one look larger than declared
        items = [(f["id"], Fraction(*f["null_model"]["p"]) if f["status"] == "evaluated" else Fraction(1)) for f in self.e["looks"]["findings"]]
        picked = {r["value"]["null_model"]["seed"]: Fraction(*r["value"]["null_model"]["p"]) for r in res if r["value"].get("topic") == "compare"}
        want = stats.bh_q(items + list(picked.items()), 17)
        self.assertEqual(Fraction(*last["q"]), want[last["null_model"]["seed"]])
        self.assertEqual(last["q_text"], stats.decimal_str(last["q"][0], last["q"][1], 4))
        only = stats.bh_q(items + [(first["null_model"]["seed"], Fraction(*first["null_model"]["p"]))], 13)
        self.assertEqual(Fraction(*first["q"]), only[first["null_model"]["seed"]])
        self.assertEqual([t["n"] for t in last["taken"]], [1, 2, 3])
        self.assertEqual(Fraction(*last["taken"][0]["q"]), want[first["null_model"]["seed"]])     # the table is re-read at the current m
        self.assertGreaterEqual(Fraction(*last["taken"][0]["q"]), Fraction(*first["q"]))          # a bigger family never lowers an earlier q
        self.assertEqual(last["p_at_most_alpha"], 20 * last["null_model"]["p"][0] <= last["null_model"]["p"][1])

    def test_declared_looks_that_did_not_run_enter_the_family_at_p_one(self):
        ds, e, b = built("ai_19")                          # 19 AI commits: all 12 declared looks are not run
        self.assertEqual({f["status"] for f in e["looks"]["findings"]}, {"not_run"})
        res, _ = run_tools(b, [["eda", {"topic": "compare", "a": {"max_lines": 30}, "b": {"min_lines": 31}, "measure": m}] for m in ("hour4", "weekday")])
        for r, m_total in zip(res, (13, 14)):
            v = r["value"]
            self.assertEqual(v["family"]["m"], m_total)
            items = [(f["id"], Fraction(1)) for f in e["looks"]["findings"]]
            seeds = {x["value"]["null_model"]["seed"]: Fraction(*x["value"]["null_model"]["p"]) for x in res[:res.index(r) + 1]}
            want = stats.bh_q(items + list(seeds.items()), m_total)[v["null_model"]["seed"]]
            self.assertEqual(Fraction(*v["q"]), want)
        # p = 1/2 for the unrun looks would cap this q below 1 (m*p/rank = 13/2/13); at p = 1 nothing is below 1
        self.assertEqual(Fraction(*res[0]["value"]["q"]), 1)

    def test_refusals_are_not_looks(self):
        calls = [{"a": {"repo": "r0"}, "b": {"who": "ai"}},                        # share commits
                 {"a": {"who": "ai", "hour": 3}, "b": {"who": "you", "hour": 3}},  # a group under 20 commits
                 {"a": {}, "b": {"who": "you"}},                                   # an empty filter is the whole family
                 {"a": {"who": "ai"}, "b": {"who": "ai"}},                         # the same group twice
                 {"a": {"who": "ai"}, "b": {"who": "you"}, "measure": "nope"},
                 {"a": {"who": "ai"}, "b": {"who": "you"}, "fork": "nope"},
                 {"a": {"who": "ai"}, "b": {"who": "robot"}},
                 {"a": {"who": "ai"}},
                 {"a": {"who": "ai"}, "b": {"who": "you"}, "id": "contrast:lines"}]
        res = self.ask(calls)
        self.assertTrue(all("error" in r for r in res), res)
        self.assertRegex(res[0]["error"], "share commits")
        self.assertRegex(res[1]["error"], r"fewer than 20 commits \(n_a=\d+, n_b=\d+\)")
        res2, _ = run_tools(self.b, [["eda", {"topic": "compare", **c}] for c in calls] + [["eda", {"topic": "ledger"}]])
        self.assertEqual(res2[-1]["value"]["looks_asked_this_session"], 0)         # nothing refused was counted

    def test_the_thirty_first_distinct_comparison_is_refused_and_a_repeat_is_not(self):
        import datetime
        stamps = sorted({datetime.datetime.fromtimestamp(r["t"], datetime.timezone.utc).strftime("%Y-%m-%d") for r in self.rows if r["c"] == "ai"})
        days = stamps[len(stamps) // 3:][:31]
        self.assertEqual(len(days), 31)
        calls = [{"a": {"who": "ai", "to": d}, "b": {"who": "you"}, "measure": "lines", "fork": "all"} for d in days]
        res = self.ask(calls + [calls[0]])
        self.assertEqual([("value" in r) for r in res], [True] * 30 + [False, True])      # 30 distinct, the 31st refused, the repeat of the first answered
        self.assertIn("already taken 30 comparisons", res[30]["error"])
        self.assertEqual(res[31]["value"]["null_model"], res[0]["value"]["null_model"])
        self.assertEqual(res[31]["value"]["looks_asked_this_session"], 30)

    def test_forks_that_hold_the_same_commits_are_one_question(self):
        """In this history no commit is above the mass-edit line, so no_mass is no_merge; the sample is the same and so is the stream."""
        a, b = {"who": "ai"}, {"who": "you"}
        res = self.ask([{"a": a, "b": b, "fork": "no_merge", "measure": "files"}, {"a": a, "b": b, "fork": "no_mass", "measure": "files"},
                        {"a": a, "b": b, "fork": "all", "measure": "files"}])
        v = [r["value"] for r in res]
        self.assertEqual(v[0]["null_model"], v[1]["null_model"])
        self.assertNotEqual(v[0]["null_model"]["seed"], v[2]["null_model"]["seed"])       # merges are in the sample under all: a different question
        self.assertEqual([x["looks_asked_this_session"] for x in v], [1, 1, 2])

    def test_adversarial_datasets_match_looks_compare_on_every_measure(self):
        """Values tied everywhere, counts near 2**52, times over the whole range and before 1970, astral repo names."""
        for name in ("ties", "huge", "unicode", "plain"):
            ds, e, b = built(name)
            rows = eda.indexed_rows(ds)
            calls = [{"a": {"who": "ai"}, "b": {"who": "you"}, "fork": f, "measure": m} for f in ("all", "no_merge") for m in looks.CONTRAST_MEASURES]
            res, _ = run_tools(b, [["eda", {"topic": "compare", **c}] for c in calls])
            for call, r in zip(calls, res):
                with self.subTest(dataset=name, fork=call["fork"], measure=call["measure"]):
                    got, want = r["value"], expect(rows, {"who": ["ai"]}, {"who": ["you"]}, call["fork"], call["measure"])
                    self.assertEqual((got["null_model"]["k_ge"], got["null_model"]["M"], got["null_model"]["seed"]), (want["k_ge"], want["M"], want["seed"]))
                    self.assertEqual({k: got["effect"][k] for k in want["effect"]}, want["effect"])
                    self.assertEqual((got["groups"]["a"]["n"], got["groups"]["b"]["n"]), (want["n_a"], want["n_b"]))
        ds, e, b = built("ties")
        (r,), _ = run_tools(b, [["eda", {"topic": "compare", "a": {"who": "ai"}, "b": {"who": "you"}, "measure": "lines", "fork": "all"}]])
        v = r["value"]
        self.assertEqual((v["effect"]["num"], v["effect"]["permille"], v["effect"]["floor_met"]), (0, 0, False))
        self.assertEqual(v["null_model"]["k_ge"], v["null_model"]["M"])                    # every shuffle ties the observed zero: p is 1, not "no evidence" read as small
        self.assertTrue(v["p_at_most_alpha"] is False and v["q"] == [1, 1])

    def test_asking_a_declared_contrast_by_hand_gives_its_effect_and_an_independent_stream(self):
        """The ad hoc sample is built by other code (filters, then sample()) than the stage's cut, so the effect of a declared contrast must come back
        field for field; the permutation stream must not (an ad hoc question never borrows a declared look's seed)."""
        pair = self.e["classes"]["pair"]
        by_id = {f["id"]: f for f in self.e["looks"]["findings"]}
        calls = [{"a": {"who": pair[0]}, "b": {"who": pair[1]}, "fork": "all", "measure": m} for m in looks.CONTRAST_MEASURES]
        for call, r in zip(calls, self.ask(calls)):
            with self.subTest(measure=call["measure"]):
                v, f = r["value"], by_id["contrast:" + call["measure"]]
                self.assertEqual({k: v["effect"][k] for k in f["effect"]}, f["effect"])
                self.assertEqual((v["groups"]["a"]["n"], v["groups"]["b"]["n"]), (f["groups"]["a"]["n"], f["groups"]["b"]["n"]))
                self.assertNotEqual(v["null_model"]["seed"], f["null_model"]["seed"])

    def test_the_work_cap_refuses_instead_of_thinning(self):
        ia, ib = groups_by_filter(self.rows, {"who": ["ai"]}, {"who": ["you"]})
        with mock.patch.object(looks, "ADHOC_WORK_CAP", 3000):
            with self.assertRaisesRegex(ValueError, "work above the cap"):
                looks.compare(self.rows, ia, ib, "lines", "all")
        with tempfile.TemporaryDirectory() as tmp:
            patched = Path(tmp) / "tools.js"
            text = TOOLS.read_text()
            self.assertEqual(text.count("ADHOC_CAP: 4000000"), 1)
            patched.write_text(text.replace("ADHOC_CAP: 4000000", "ADHOC_CAP: 3000"))
            got = node("""
              const t = Object.fromEntries(T.makeTools(input, null).map(x => [x.name, x]));
              let msg = null; try { t.eda.execute({ topic: 'compare', a: { who: 'ai' }, b: { who: 'you' } }); } catch (e) { msg = e.message; }
              console.log(JSON.stringify({ msg, asked: t.eda.execute({ topic: 'ledger' }).looks_asked_this_session }));""", self.b, patched)
        self.assertRegex(got["msg"], r"the smaller group has \d+ commits; a comparison takes at most 15")
        self.assertEqual(got["asked"], 0)                                          # refused, so not a look

    def test_the_permutation_count_clamp_matches_python_and_the_cap_edge_is_exact(self):
        ws = list(range(1, 400)) + [999, 1000, 4000, 4008, 4009, 5000, 20099, 20100, 20101, 20102, 50000, 200000]
        got = node("console.log(JSON.stringify({ a: input.map(w => K.adhocM(w)), d: input.map(w => K.clampM(w)) }));", ws)
        cap, lo, hi = looks.ADHOC_WORK_CAP, looks.THRESHOLDS["M_MIN"], looks.THRESHOLDS["M_MAX"]
        self.assertEqual(got["a"], [max(lo, min(hi, cap // w)) for w in ws])
        self.assertEqual(got["d"], [max(lo, min(hi, looks.THRESHOLDS["WORK_CAP"] // max(1, w))) for w in ws])
        ok = [w for w in ws if w * got["a"][ws.index(w)] <= cap]
        self.assertEqual((max(ok), cap // lo), (20100, 20100))                     # the largest smaller group a comparison takes

    def test_results_are_bounded_and_private_repos_are_counts_not_names(self):
        # r2 is private: it may be asked about, and answered about, without its name leaving the tool
        calls = [{"a": {"repo": "r2"}, "b": {"repo": "r0"}, "measure": "repo"}, {"a": {"repo": "r2"}, "b": {"repo": "r1"}, "measure": "lines"},
                 {"a": {"repo": "r0", "who": "ai"}, "b": {"repo": "r1", "who": "ai"}, "measure": "repo"}]
        for r in self.ask(calls):
            v = r["value"]
            text = json.dumps(v)
            self.assertLessEqual(len(text.encode()), 30000)
            self.assertNotRegex(text, r"\br2\b")
        v = self.ask(calls[:1])[0]["value"]
        self.assertEqual(v["question"]["a"], {"private_repos": 1})
        self.assertTrue(v["effect"].get("dominant_bin_private") or v["effect"]["dominant_bin"] in ("r0", "r1"))
        self.assertNotIn("r2", v["text"])

    def test_unicode_repo_names_order_ties_by_code_point(self):
        ds, e, b = built("unicode")
        rows = eda.indexed_rows(ds)
        names = [r["name"] for r in ds["repos"]]
        ea, eb = {"repo": sorted(names[:2])}, {"repo": sorted(names[2:])}
        (r,), _ = run_tools(b, [["eda", {"topic": "compare", "a": {"repo": ",".join(names[:2])}, "b": {"repo": ",".join(names[2:])}, "measure": "repo", "fork": "all"}]])
        want = expect(rows, ea, eb, "all", "repo")
        self.assertEqual(r["value"]["null_model"]["k_ge"], want["k_ge"])
        self.assertEqual(r["value"]["effect"]["num"], want["effect"]["num"])
        self.assertEqual(r["value"]["effect"]["dominant_bin"], want["effect"]["dominant_bin"])

    def test_compare_is_deterministic_across_tool_sets_and_the_bundle_is_unchanged(self):
        call = [{"a": {"who": "ai"}, "b": {"who": "you"}, "measure": "lines"}]
        x, y = self.ask(call)[0]["value"], self.ask(call)[0]["value"]
        self.assertEqual(x, y)


if __name__ == "__main__":
    unittest.main()
