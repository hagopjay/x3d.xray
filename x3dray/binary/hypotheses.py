"""Pass B: the hypothesis ledger, re-run as executable experiments.

The method is the hypothesis-debugging loop described by ioma8 in "LLM assisted
reverse engineering" (Slow Chronicle, 18 Nov 2026,
https://www.slowchronicle.com/posts/llm-assisted-reverse-engineering): one
hypothesis, one experiment that definitively proves or disproves it, one
recorded verdict, written to disk so an agent cannot go in circles.

This module turns each ledger row into code. An imported verdict (from another
agent, another container) is never trusted: every row is re-run here and earns
its own verdict from a re-runnable witness. Four RE witness kinds:

  static-decode     objdump bytes -> decoder model -> decoded strings (no execution)
  static-pattern    constants the hypothesis names, found in the witnessed disasm
  trace             strace / gdb runs; the witness keeps argv + env + the
                    extracted observation (raw gdb output carries pids, so only
                    the extracted value is bound)
  oracle            the real binary run natively, no debugger, as a judge of a
                    model; seeded vectors so ``verify`` regenerates the same run

Verdicts: CONFIRMED (prediction met by a re-run witness), REFUTED (prediction
failed), UNTESTED (a tool is missing here; the imported verdict is shown but
graded Suspected). Grades follow the project ladder: Confirmed / Likely /
Suspected.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import string
import subprocess
import tempfile

from . import witnesses

M32 = 0xFFFFFFFF


# --------------------------------------------------------------------- model
def _int(x) -> int:
    return int(x, 0) if isinstance(x, str) else int(x)


def derive_model(name: str, salt: int, model: dict) -> int:
    """The H5 reconstruction of derive(), in exact 32-bit arithmetic."""
    h = (_int(model["seed"]) ^ salt) & M32
    mul, rol = _int(model["mul"]), int(model["rol"])
    data = name.encode("utf-8")
    for c in data:
        h ^= c
        h = ((h << rol) | (h >> (32 - rol))) & M32
        h = (h * mul) & M32
    return (h ^ ((len(data) << int(model["len_shift"])) & M32)) & M32


def serial_of(v: int) -> str:
    return f"{(v >> 16) & 0xFFFF:04X}-{v & 0xFFFF:04X}"


def rolling_xor(data: bytes, base: int, step: int) -> bytes:
    return bytes(b ^ ((base + step * i) & 0xFF) for i, b in enumerate(data))


# ------------------------------------------------------------------- helpers
def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def file_sha(path: str) -> str:
    with open(path, "rb") as fh:
        return _sha(fh.read())


def _run(argv, env=None, timeout=30):
    e = dict(os.environ)
    e.update(env or {})
    e["LC_ALL"] = "C"
    return subprocess.run(argv, capture_output=True, env=e, timeout=timeout)


def _section_bytes(path: str, section: str):
    """Raw bytes + base VMA of one section, read from `objdump -s -j SECTION`."""
    out, w = witnesses.run(["objdump", "-s", "-j", section, path])
    base, buf = None, bytearray()
    for line in out.splitlines():
        m = re.match(r"^\s([0-9a-fA-F]+)\s((?:[0-9a-fA-F]{2,8}\s){1,4})", line + " ")
        if not m:
            continue
        addr = int(m.group(1), 16)
        if base is None:
            base = addr
        hexs = "".join(m.group(2).split())
        buf[addr - base:addr - base + len(hexs) // 2] = bytes.fromhex(hexs)
    return bytes(buf), (base or 0), w


_PRINT = set(bytes(string.printable, "ascii")) - set(b"\x0b\x0c")


def decode_runs(data: bytes, base: int, step: int, min_run: int):
    """Try the rolling-XOR model at every offset; keep maximal printable runs.

    The key restarts at each string, so the model is tested at every start
    offset. A run is kept when it is at least ``min_run`` printable bytes and
    ends at a NUL-equivalent or a non-printable byte; overlaps keep the first."""
    found, taken_until = [], -1
    for off in range(len(data)):
        if off < taken_until:
            continue
        n = 0
        while off + n < len(data):
            c = data[off + n] ^ ((base + step * n) & 0xFF)
            if c not in _PRINT:
                break
            n += 1
        if n >= min_run:
            txt = rolling_xor(data[off:off + n], base, step).decode("ascii")
            # a real message has letters and spaces, not just punctuation
            if sum(ch.isalpha() for ch in txt) >= n // 2 and " " in txt:
                found.append({"offset": off, "length": n, "text": txt})
                taken_until = off + n
    return found


# --------------------------------------------------------------- experiments
def _gdb_salt(binp, where: str, shim: str | None):
    argv = ["gdb", "-q", "-batch", "-nx", "-ex", "set startup-with-shell off"]
    env = {}
    if shim:
        argv += ["-ex", f"set environment LD_PRELOAD={shim}"]
    argv += ["-ex", f"break {where}", "-ex", "run",
             "-ex", 'printf "g_salt=0x%08x\\n", g_salt', "-ex", "kill",
             "--args", binp, "alice", "0000-0000"]
    return argv, env, r"g_salt=(0x[0-9a-f]{8})"


def _probe(binp, probe: str, shim: str | None):
    if probe == "strace-ptrace":
        argv, env, rx = (["strace", "-e", "trace=ptrace", binp, "alice", "0000-0000"], {},
                         r"(PTRACE_TRACEME\)\s*= -?\d+(?: E[A-Z]+)?)")
    elif probe == "gdb-salt-at-main":
        argv, env, rx = _gdb_salt(binp, "main", None)
    elif probe == "gdb-salt-at-main-with-shim":
        argv, env, rx = _gdb_salt(binp, "main", shim)
    elif probe == "gdb-salt-at-derive-with-shim":
        argv, env, rx = _gdb_salt(binp, "derive", shim)
    else:
        raise ValueError(probe)
    return trace_witness(argv, env, rx, binp)


def trace_witness(argv, env, rx, binp):
    if not witnesses.have(argv[0]):
        return {"kind": "trace", "argv": argv, "env": env, "extract": rx,
                "observed": None, "status": "unavailable",
                "binary_sha256": file_sha(binp)}
    p = _run(argv, env)
    text = (p.stdout + p.stderr).decode("utf-8", "replace")
    m = re.search(rx, text)
    obs = re.sub(r"\s+", " ", m.group(1)) if m else None
    return {"kind": "trace", "argv": argv, "env": env, "extract": rx,
            "observed": obs, "status": "ok" if obs else "no-match",
            "binary_sha256": file_sha(binp)}


def build_shim(src: str, out_dir: str) -> str | None:
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc or not os.path.exists(src):
        return None
    so = os.path.join(out_dir, "ptrace_shim.so")
    subprocess.run([cc, "-shared", "-fPIC", "-O2", "-o", so, src], check=True)
    return so


def _native(binp, name, serial):
    p = subprocess.run([binp, name, serial], capture_output=True, timeout=10)  # argv, no shell
    return p.returncode, p.stdout.decode("utf-8", "replace").strip()


def oracle_vectors(seed: int, n: int):
    rng = random.Random(seed)
    alphabet = string.ascii_letters + string.digits + " _-.$`'\"\\;|&*()"
    return ["".join(rng.choice(alphabet) for _ in range(rng.randint(4, 32))) for _ in range(n)]


def oracle_witness(binp, model, seed, n, mutate=True):
    names = oracle_vectors(seed, n)
    transcript = hashlib.sha256()
    ok = bad = mut_rej = mut_n = 0
    first_bad = None
    for i, nm in enumerate(names):
        s = serial_of(derive_model(nm, 0, model))
        rc, out = _native(binp, nm, s)
        transcript.update(f"{nm}\t{s}\t{rc}\n".encode())
        if rc == 0 and "accepted" in out:
            ok += 1
        else:
            bad += 1
            first_bad = first_bad or {"name": nm, "serial": s, "rc": rc}
        if mutate and i % 10 == 0:          # negative control: flip one hex digit
            flip = s[:-1] + ("0" if s[-1] != "0" else "1")
            rc2, _ = _native(binp, nm, flip)
            transcript.update(f"{nm}\t{flip}\t{rc2}\n".encode())
            mut_n += 1
            mut_rej += rc2 != 0
    return {"kind": "oracle", "seed": seed, "n": n, "accepted": ok, "rejected": bad,
            "mutants": mut_n, "mutants_rejected": mut_rej, "first_mismatch": first_bad,
            "transcript_sha256": transcript.hexdigest(), "binary_sha256": file_sha(binp),
            "status": "ok"}


def static_patterns(disasm: str, model: dict) -> list[dict]:
    """Constants the hypotheses name, located in the witnessed disassembly."""
    want = {"seed": model["seed"], "mul": model["mul"], "poison_ctor": model["poison_ctor"],
            "poison_int3": model["poison_int3"], "int3_byte": "0xcc"}
    hits = []
    for key, k in want.items():
        rx = re.compile(r"^\s*([0-9a-f]+):\s+(.*\$" + re.escape(hex(_int(k))) + r"\b.*)$", re.M)
        for m in rx.finditer(disasm):
            hits.append({"constant": key, "value": hex(_int(k)), "addr": "0x" + m.group(1),
                         "insn": re.sub(r"\s+", " ", m.group(2)).strip()})
    return hits


# ------------------------------------------------------------------- runner
def run_ledger(binp: str, spec: dict, *, out_dir: str, disasm: str,
               shim_src: str | None = None, oracle_n: int | None = None) -> dict:
    model = spec["model"]
    os.makedirs(out_dir, exist_ok=True)
    shim = build_shim(shim_src, out_dir) if shim_src else None
    pats = static_patterns(disasm, model)
    rows = []
    for h in spec["hypotheses"]:
        ex, pred = h["experiment"], h["prediction"]
        wit, observed, met, avail = [], {}, True, True
        k = ex["kind"]
        if k == "static-decode":
            data, _base, w = _section_bytes(binp, ex.get("section", ".rodata"))
            runs = decode_runs(data, _int(model["xor_base"]), int(model["xor_step"]),
                               int(ex.get("min_run", 8)))
            texts = [r["text"] for r in runs]
            observed["decoded"] = texts
            plain = set(s for s in _plain_strings(binp))
            observed["visible_to_strings"] = sorted(t for t in texts if t in plain)
            wit.append({"kind": "static-decode", **w,
                        "decoded_sha256": _sha(json.dumps(texts).encode())})
            met = all(any(p in t for t in texts) for p in pred["decoded_contains"]) \
                and not observed["visible_to_strings"]
        elif k == "trace":
            for probe in ex["probes"]:
                tw = _probe(binp, probe, shim)
                tw["probe"] = probe
                wit.append(tw)
                if tw["status"] == "unavailable":
                    avail = False
                observed[probe] = tw["observed"]
            if "strace" in pred:
                met &= bool(observed.get("strace-ptrace")) and \
                    pred["strace"].replace(" ", "") in (observed["strace-ptrace"] or "").replace(" ", "")
            if "g_salt" in pred:
                gv = [v for p, v in observed.items() if p.startswith("gdb")]
                met &= bool(gv) and all(v == pred["g_salt"] for v in gv)
        elif k == "negative-control":
            salt = _int(ex["salt"])
            s = serial_of(derive_model(ex["name"], salt, model))
            observed["poisoned_serial"] = s
            gdb_argv = ["gdb", "-q", "-batch", "-nx", "-ex", "set startup-with-shell off",
                        "-ex", "run", "--args", binp, ex["name"], s]
            tw = trace_witness(gdb_argv, {}, r"(Serial accepted|Invalid serial)", binp)
            tw["probe"] = "gdb-run-poisoned-serial"
            rc, out = _native(binp, ex["name"], s)
            nw = {"kind": "oracle", "probe": "native-run-poisoned-serial",
                  "argv": [binp, ex["name"], s], "observed": out, "returncode": rc,
                  "binary_sha256": file_sha(binp), "status": "ok"}
            wit += [tw, nw]
            avail = tw["status"] != "unavailable"
            observed["under_gdb"] = "accepted" if tw["observed"] == "Serial accepted" else "rejected"
            observed["native"] = "accepted" if rc == 0 else "rejected"
            met = observed["under_gdb"] == pred["under_gdb"] and observed["native"] == pred["native"]
        elif k == "oracle":
            n = int(oracle_n or ex.get("n", 2000))
            ow = oracle_witness(binp, model, int(ex["seed"]), n, ex.get("mutate_control", True))
            wit.append(ow)
            observed.update({"accepted": f"{ow['accepted']}/{n}",
                             "mutants_rejected": f"{ow['mutants_rejected']}/{ow['mutants']}",
                             "alice": serial_of(derive_model("alice", 0, model))})
            met = ow["accepted"] == n and ow["mutants_rejected"] == ow["mutants"]
        else:
            raise ValueError(f"unknown experiment kind {k!r}")
        support = [p for p in pats if _supports(h["id"], p["constant"])]
        if not avail:
            verdict, grade = "UNTESTED", "Suspected"
        elif met:
            verdict, grade = "CONFIRMED", "Confirmed"
        else:
            verdict, grade = "REFUTED", "Confirmed"   # a refutation is itself a firm result
        rows.append({"id": h["id"], "phase": h["phase"], "hypothesis": h["hypothesis"],
                     "experiment": ex, "prediction": pred, "observed": observed,
                     "verdict": verdict, "grade": grade,
                     "imported_verdict": h.get("imported_verdict"),
                     "agrees_with_import": (h.get("imported_verdict") in (None, verdict)),
                     "static_support": support, "witnesses": wit})
    return {"schema_version": 1, "subject": spec.get("subject"), "method": spec.get("method"),
            "binary_sha256": file_sha(binp), "shim": bool(shim), "rows": rows,
            "static_patterns": pats,
            "tally": {v: sum(r["verdict"] == v for r in rows)
                      for v in ("CONFIRMED", "REFUTED", "UNTESTED")}}


_SUPPORT = {"H2": {"poison_ctor"}, "H4": {"poison_ctor", "poison_int3", "int3_byte"},
            "H5": {"seed", "mul"}, "H6": {"poison_int3", "int3_byte"}, "H3": {"poison_ctor"}}


def _supports(hid, const):
    return const in _SUPPORT.get(hid, set())


def _plain_strings(binp):
    try:
        out, _ = witnesses.run(["strings", "-n", "5", binp])
    except witnesses.ToolMissing:
        return []
    return out.splitlines()


def write_csv(ledger: dict, path: str):
    """The article's on-disk CSV, regenerated from the re-run ledger."""
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "hypothesis", "experiment", "prediction", "result", "verdict", "grade",
                    "imported_verdict"])
        for r in ledger["rows"]:
            w.writerow([r["id"], r["hypothesis"], r["experiment"]["kind"],
                        json.dumps(r["prediction"], sort_keys=True),
                        json.dumps(r["observed"], sort_keys=True), r["verdict"], r["grade"],
                        r["imported_verdict"] or ""])


# ------------------------------------------------------------------- verify
def verify_witness(w: dict, model: dict | None = None) -> dict:
    """Re-run one RE witness. Fails closed: anything not reproduced is a mismatch."""
    k = w.get("kind")
    if k == "static-decode":
        r = witnesses.verify(w)
        return {"kind": k, "status": r["status"]}
    if k == "trace":
        if w.get("status") == "unavailable" or not witnesses.have(w["argv"][0]):
            return {"kind": k, "probe": w.get("probe"), "status": "unavailable"}
        bin_path = w["argv"][-3] if "--args" in w["argv"] else w["argv"][3]
        if os.path.exists(bin_path) and file_sha(bin_path) != w["binary_sha256"]:
            return {"kind": k, "probe": w.get("probe"), "status": "mismatch", "reason": "binary changed"}
        again = trace_witness(w["argv"], w.get("env") or {}, w["extract"], bin_path)
        ok = again["observed"] == w["observed"]
        return {"kind": k, "probe": w.get("probe"), "status": "match" if ok else "mismatch",
                "observed": again["observed"]}
    if k == "oracle":
        if "argv" in w:   # single native run
            rc, out = _native(*w["argv"])
            ok = rc == w["returncode"] and out == w["observed"]
            return {"kind": k, "probe": w.get("probe"), "status": "match" if ok else "mismatch"}
        raise ValueError("seeded oracle needs the binary path: use verify_ledger")
    return {"kind": k, "status": "mismatch", "reason": "unknown witness kind"}


def verify_ledger(ledger: dict, binp: str, spec: dict) -> dict:
    model = spec["model"]
    results = []
    for r in ledger["rows"]:
        for w in r["witnesses"]:
            if w.get("kind") == "oracle" and "seed" in w:
                again = oracle_witness(binp, model, w["seed"], w["n"], True)
                st = "match" if again["transcript_sha256"] == w["transcript_sha256"] else "mismatch"
                results.append({"id": r["id"], "kind": "oracle", "status": st})
            else:
                results.append({"id": r["id"], **verify_witness(w, model)})
    ok = all(x["status"] in ("match", "unavailable") for x in results)
    return {"ok": ok, "results": results}
