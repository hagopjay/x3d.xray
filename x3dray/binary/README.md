# x3dray.binary — the binary x-ray lens (Pass A + Pass B)

A new subject class for x3d.xray: instead of a repo family, the subject is a
**binary**. It reuses the repo's own engines unchanged — `x3dray.centrality`
(betweenness + passage points), `x3dray.fca` (the capability lattice), and
`x3dray.compiler` formatting helpers (the scene) — and adds only an ingest layer
that shells out to GNU binutils, the way `x3dray.harvest` shells out to git.

Pass A is **static and binutils-only** (`objdump`, `nm`, `file`, `strings`).
Pass B adds the **hypothesis ledger**: each row of a reverse-engineering
investigation becomes an executable experiment, re-run on this machine with a
re-runnable witness, plus RE gates and a standalone report page (below).

## Run

```sh
python3 -m x3dray.binary all path/to/binary --out out/bin-xray
python3 -m x3dray.binary all path/to/binary --out out/bin-xray --font path/to/UbuntuMono-R.ttf
```

Sub-commands: `harvest`, `callgraph`, `capability`, `passage --to NAME`,
`scene`, `verify`, `all`. Outputs in `--out`:

| file | what |
|---|---|
| `binary.json` | triage, sections, functions, imports, strings — each section citing its witness |
| `callgraph.json` | call edges graded (direct / plt / runtime / indirect), exact betweenness, the gatekeeper |
| `capability.json` | functions × capabilities context and the FCA lattice |
| `xray.x3d` | the scene: a binary city + a call constellation, in the repo's palette |
| `receipt.json` | source digest, counts, gatekeeper, capabilities, witness digests |
| `verify.json` | every witness re-run, hash re-checked |

## Witnesses

A witness is `{tool, argv, output_sha256}`. `verify` re-runs each one and refuses
a finding whose output hash no longer matches — the binary analogue of the
repo's witness gate. A claim you cannot reproduce fails closed.

## What the scene says

- **Binary city** — one district per section, one building per function (height =
  instruction count), a hot roof (`HEAT`) on functions that touch anti-analysis
  APIs, fog over spans that could not be disassembled.
- **Call constellation** — functions as spheres, call edges as lines, the
  **gatekeeper** (highest betweenness) enlarged and gold. `passage --to NAME`
  asks the one RE question that matters: *which functions does every path from
  entry to NAME cross?*

On the bundled `crackme1` fixture (symbolized build): the gatekeeper is `main`,
`passage --to derive` returns `["main"]`, and the capability lattice recovers
anti-debug / parse / string / I/O concepts — all without running the binary.

## Scope and honesty (Pass A)

- **Stripped binaries run** but recovery is coarse: functions come from direct
  call targets only. Full stripped recovery (prologue scanning, `main` from the
  `__libc_start_main` argument as a graph node, not just an edge) is a Pass A
  follow-up. The symbolized fixture is the clean showcase.
- Indirect/computed calls are recorded as a single `(indirect)` sink, not
  resolved. Vtable and jump-table resolution is future work.
- This is a **defensive / educational** lens: structure, control flow,
  capabilities, and defeating protections on a binary you own or are authorized
  to analyze. It emits no exploit payloads or weaponization.
- Only x3d.xray-owned engines and stdlib are imported; no third-party Python
  dependency, matching the repo's zero-dependency runtime rule.

## Pass B — the hypothesis ledger, RE witnesses and gates

Method credit: the hypothesis-debugging loop from ioma8,
[“LLM assisted reverse engineering”](https://www.slowchronicle.com/posts/llm-assisted-reverse-engineering)
(Slow Chronicle, 18 Nov 2026): one hypothesis, one definitive experiment, one
recorded verdict, kept on disk. Pass B makes every row code.

```sh
python3 -m x3dray.binary all BIN --out out/cm1 --to derive \
    --ledger specs/crackme1.hypotheses.json --claims specs/crackme1.bin.json \
    --shim targets/ptrace_shim.c            # add --oracle-n 200 for a quick run
python3 -m x3dray.binary gates BIN --out out/cm1 \
    --ledger specs/crackme1.hypotheses.json --claims specs/crackme1.bin.json
```

| witness kind | what is bound | how `verify` re-earns it |
|---|---|---|
| `static` | tool argv + sha256 of stdout | re-run, compare hash |
| `static-decode` | `objdump -s` bytes + decoder model + sha256 of decoded strings | re-run objdump, compare hash |
| `trace` | strace/gdb argv + env + extraction regex + observed value + binary sha256 | re-run, re-extract, compare value (raw gdb output carries pids, so only the value is bound) |
| `oracle` | the real binary run natively (argv list, no shell, no debugger) over seeded vectors + mutated negative controls; transcript sha256 | regenerate the vectors from the seed, re-run, compare transcript |

| gate | fails when |
|---|---|
| `re_fact` | a digit appears in claim prose outside a `{slot}`, or a slot has no witnessed value |
| `re_witness` | any witness above no longer reproduces, or the binary changed |
| `re_verdict` | a CONFIRMED has no re-run witness; disagreements with an imported ledger are listed, never hidden |
| `re_scene` | a function or hypothesis is drawn without a record, or a record is not drawn |

Verdicts: CONFIRMED / REFUTED come only from experiments run here. A row whose
tool is missing (no gdb, no strace) is UNTESTED and graded Suspected; the
imported verdict is shown beside it but never counted.

crackme1 receipt (Python 3.13.18, GNU objdump 2.42, GNU gdb + strace on Ubuntu,
gcc -O2 -g, 23 Nov 2026): **6/6 CONFIRMED**; H1-H5 agree with the outside
agent's ledger, H6 is new (a software breakpoint on `derive` trips the int3
self-scan: with the ptrace shim, `g_salt` = `0x5a5a0000`). The debugger lies,
measured: under gdb `g_salt` = `0xa5a5a5a5` and the poisoned serial `F104-7CB6`
is accepted; natively it is rejected and `5470-E4D2` is accepted. Keygen vs
native oracle: 2000/2000 accepted, 200/200 mutants rejected. 13/13 RE witnesses
re-run and match; all 4 RE gates pass.

New outputs: `ledger.json`, `hypotheses.csv` (the article's CSV, regenerated
from the re-run), `gates.json`, `claims.json`, `page.html` (verdict strip,
"the debugger lies" panel, hypothesis gallery, capability lattice Hasse
diagram, betweenness table, gates, claims, witnesses), and the investigation
helix in `xray.x3d` (one rung per hypothesis, colour = verdict).

Scope: the oracle runs the target natively, so use it only on binaries you
built or are authorized to run, inside a sandbox. Trace probes read a symbol
(`g_salt`), so they need a symbolized build; the stripped case needs the
address recovered first (the outside agent did this in radare2).
