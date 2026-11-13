# x3d.xray documentation and evidence

Everything here is generated, synthetic and self-contained; nothing under `docs/` is read by the build or the tests.
Each row names the file, what it is, and the first 16 hex of its SHA-256 so a copy can be checked against this table.

## Reports and laboratories

| File | Pass | What it is | sha256 (first 16) |
|---|---|---|---|
| [reports/X3D-Xray-Review.html](reports/X3D-Xray-Review.html) | before pass 1 | Evidence Observatory review of upstream b3fec1f7307a: findings, five-PR plan | `dc56d2d978bc89e1` |
| [development-pass-1.html](development-pass-1.html) | 1 | Development Field Guide: engineering walkthrough, limits, exercises | `2880ce1e8a16a0fa` |
| [labs/X3D-Xray-Bridge-Laboratory.html](labs/X3D-Xray-Bridge-Laboratory.html) | 1 | Standalone Bridge Laboratory (57 synthetic observations; map and scores work offline) | `0cac06f8880867a8` |

## Evidence (raw outputs the reports cite)

- [evidence/pass-1/](evidence/pass-1/): unit-test transcript (178 tests), DOM results, XSD result, passage-index benchmark, synthetic-pipeline gates, patch check, lab X3D export.
  Produced by the pass author before this repository received the patch; re-run results are in the pull request description.

## Scope notes

- The labs are DOM-integration and CPU measurements. No real browser layout, WebGL, GPU or frame-rate claim is made anywhere in this folder.
- Hashes in the evidence detect a changed file; they do not authenticate who produced it.
- Observations are synthetic. No private repository harvest is included.
