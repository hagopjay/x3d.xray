"""Binary x-ray lens for x3d.xray (Pass A, static, binutils-only).

A new *subject class* for the same evidence->graph->X3D spine: instead of a repo
family, the subject is a binary. Every record carries a reproducible witness
``{tool, argv, output_sha256}``; ``verify`` re-runs each witness and checks the
hash, the way the repo's gates re-derive their claims.

Pass A is static and depends only on ``objdump``/``nm``/``file`` (GNU binutils).
It reuses the repo's own engines unchanged: ``x3dray.centrality`` for the
gatekeeper (betweenness + passage points) and ``x3dray.fca`` for the capability
lattice, plus ``x3dray.compiler`` formatting helpers for the X3D scene.

Dynamic witnesses (gdb/unicorn) and the keygen oracle are Pass C, intentionally
out of scope here so the lens runs anywhere binutils is installed.
"""
__all__ = ["harvest_bin", "callgraph", "capability", "scene_bin", "witnesses"]
