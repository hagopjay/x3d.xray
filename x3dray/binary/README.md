# x3dray.binary — the binary x-ray lens (Pass A)

A new subject class for x3d.xray: instead of a repo family, the subject is a
**binary**. It reuses the repo's own engines unchanged — `x3dray.centrality`
(betweenness + passage points), `x3dray.fca` (the capability lattice), and
`x3dray.compiler` formatting helpers (the scene) — and adds only an ingest layer
that shells out to GNU binutils, the way `x3dray.harvest` shells out to git.

Pass A is **static and binutils-only** (`objdump`, `nm`, `file`, `strings`).
Dynamic witnesses (gdb/unicorn) and the keygen oracle are Pass C.

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
