"""Reproducible witnesses: a claim is only as good as the command that backs it.

A witness records the exact tool invocation behind a fact and the sha256 of its
output. ``verify`` re-runs it and checks the digest, so a finding that cannot be
reproduced fails closed -- the binary-x-ray analogue of the repo's witness gate.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess


class ToolMissing(RuntimeError):
    pass


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run(argv: list[str], *, text: bool = True) -> tuple[str, "dict"]:
    """Run a tool, return (stdout, witness). Raises ToolMissing if absent.

    The witness binds the argv and the sha256 of raw stdout bytes. It deliberately
    does not capture stderr in the digest: a warning must not change a finding's id.
    """
    if not have(argv[0]):
        raise ToolMissing(argv[0])
    proc = subprocess.run(argv, capture_output=True)
    out = proc.stdout
    witness = {"tool": argv[0], "argv": argv, "output_sha256": _sha(out),
               "returncode": proc.returncode, "bytes": len(out)}
    return (out.decode("utf-8", "replace") if text else out, witness)


def verify(witness: "dict") -> "dict":
    """Re-run a witness and report whether the output hash still matches."""
    argv = witness.get("argv")
    if not isinstance(argv, list) or not argv or not have(argv[0]):
        return {**_summary(witness), "status": "unavailable",
                "reason": "tool_or_argv_unavailable"}
    proc = subprocess.run(argv, capture_output=True)
    got = _sha(proc.stdout)
    ok = got == witness.get("output_sha256")
    return {**_summary(witness), "status": "match" if ok else "mismatch",
            "recomputed_sha256": got}


def _summary(witness: "dict") -> "dict":
    return {"tool": witness.get("tool"), "argv": witness.get("argv"),
            "expected_sha256": witness.get("output_sha256")}
