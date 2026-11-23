"""Static triage of one binary: format, sections, functions, imports, strings.

binutils only (objdump/nm/file). Functions come from symbols when present and
are recovered from call targets when the binary is stripped, so the lens works
on both. Every section of the emitted record cites the witness behind it.
"""
from __future__ import annotations

import re

from . import witnesses

_FUNC_LABEL = re.compile(r"^([0-9a-fA-F]+)\s+<([^>]+)>:")
_INSN = re.compile(r"^\s*([0-9a-fA-F]+):\s+(.*)$")
_CALL = re.compile(r"\b(call|callq|bl|jmp|jmpq)\s+(?:0x)?([0-9a-fA-F]+)\s*<([^>+]+)(?:\+0x[0-9a-fA-F]+)?>")
_CALL_BARE = re.compile(r"\b(call|callq|bl)\s+(?:0x)?([0-9a-fA-F]+)\b")
# a software-breakpoint self-scan: comparing a byte against 0xcc (int3)
_INT3_SCAN = re.compile(r"\bcmp\b.*\b0xcc\b")


def _objdump_disasm(path: str):
    out, w = witnesses.run(["objdump", "-d", "--no-show-raw-insn", path])
    return out, w


def _sections(path: str):
    out, w = witnesses.run(["objdump", "-h", path])
    secs = []
    for line in out.splitlines():
        m = re.match(r"\s*\d+\s+(\.[\w.$-]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)", line)
        if m:
            secs.append({"name": m.group(1), "size": int(m.group(2), 16),
                         "vma": int(m.group(3), 16)})
    return secs, w


def _triage(path: str):
    out, w = witnesses.run(["file", "-b", path])
    desc = out.strip()
    arch = "x86-64" if "x86-64" in desc else ("aarch64" if "aarch64" in desc or "ARM aarch64" in desc else "?")
    return {"description": desc, "arch": arch,
            "stripped": "not stripped" not in desc,
            "pie": "pie executable" in desc or "shared object" in desc}, w


def _imports(path: str):
    try:
        out, w = witnesses.run(["nm", "-D", "--defined-only", "/dev/null"])
    except witnesses.ToolMissing:
        return [], None
    out, w = witnesses.run(["nm", "-D", path])
    imps = []
    for line in out.splitlines():
        m = re.match(r"\s*(?:[0-9a-fA-F]*)\s+([UwWvV])\s+(\S+)", line)
        if m and m.group(1) in "Uw":
            imps.append(m.group(2).split("@")[0])
    return sorted(set(imps)), w


def _parse_functions(disasm: str):
    """Return {entry_addr: {'name','start','insns':[(addr,text)]}}. Recovers
    entries from call targets when the binary carries no function labels."""
    lines = disasm.splitlines()
    labels = {}
    insns = []
    for ln in lines:
        m = _FUNC_LABEL.match(ln)
        if m:
            nm = m.group(2)
            if not nm.startswith("."):   # skip section labels (.text, .plt, ...)
                labels[int(m.group(1), 16)] = nm
            continue
        mi = _INSN.match(ln)
        if mi:
            insns.append((int(mi.group(1), 16), mi.group(2)))
    if len(labels) < 2:  # stripped: synthesize entries from internal call targets
        targets = set()
        for _, text in insns:
            mb = _CALL_BARE.search(text)
            if mb:
                targets.add(int(mb.group(2), 16))
        addrs = {a for a, _ in insns}
        for t in sorted(targets & addrs):
            labels.setdefault(t, f"fn_{t:x}")
        if insns and not labels:
            labels[insns[0][0]] = f"fn_{insns[0][0]:x}"
    starts = sorted(labels)
    funcs = {a: {"name": labels[a], "start": a, "insns": []} for a in starts}
    import bisect
    for addr, text in insns:
        i = bisect.bisect_right(starts, addr) - 1
        if i >= 0:
            funcs[starts[i]]["insns"].append((addr, text))
    return funcs


def harvest(path: str) -> dict:
    disasm, w_dis = _objdump_disasm(path)
    triage, w_tri = _triage(path)
    sections, w_sec = _sections(path)
    imports, w_imp = _imports(path)
    funcs = _parse_functions(disasm)

    sec_of = _section_namer(sections)
    out_funcs = []
    for start, fn in sorted(funcs.items()):
        texts = [t for _, t in fn["insns"]]
        calls_imported = sorted({m.group(3).replace("@plt", "")
                                 for t in texts for m in [_CALL.search(t)] if m})
        anti_debug = any(_INT3_SCAN.search(t) for t in texts) or "ptrace" in calls_imported
        out_funcs.append({
            "name": fn["name"], "addr": start, "section": sec_of(start),
            "insns": len(fn["insns"]),
            "blocks": _count_blocks(fn["insns"]),
            "calls": calls_imported,
            "anti_debug": anti_debug,
        })

    strings, w_str = _strings(path)
    return {
        "schema_version": 1,
        "target": path,
        "triage": triage,
        "sections": sections,
        "functions": out_funcs,
        "imports": imports,
        "strings_sample": strings[:40],
        "strings_total": len(strings),
        "witnesses": {
            "disasm": w_dis, "triage": w_tri, "sections": w_sec,
            "imports": w_imp, "strings": w_str,
        },
    }


def _section_namer(sections):
    ranges = [(s["vma"], s["vma"] + s["size"], s["name"]) for s in sections if s["vma"]]
    ranges.sort()

    def name_of(addr):
        for lo, hi, nm in ranges:
            if lo <= addr < hi:
                return nm
        return ".text"
    return name_of


def _count_blocks(insns):
    """Cheap basic-block estimate: 1 + branch instructions in the function."""
    br = sum(1 for _, t in insns if re.match(r"\s*(j\w+|call|ret|bl)\b", t))
    return max(1, br)


def _strings(path: str):
    try:
        out, w = witnesses.run(["strings", "-n", "5", path])
    except witnesses.ToolMissing:
        return [], None
    return [s for s in out.splitlines() if s.strip()], w
