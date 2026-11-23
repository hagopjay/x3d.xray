"""Capability lattice: functions x capabilities, through x3dray.fca unchanged.

Formal concept analysis clusters functions by what they can do. Anti-debug,
parsing and I/O concepts fall out automatically, and fca's implications give the
"every function that does X also does Y -- except one" rules where the
interesting code hides.
"""
from __future__ import annotations

from .. import fca

# imported symbol -> capability label. Unmapped imports pass through by name.
CAPABILITY = {
    "ptrace": "anti-debug", "getenv": "env",
    "strtoul": "parse", "strtol": "parse", "atoi": "parse", "sscanf": "parse",
    "strlen": "string", "strcmp": "string", "strncmp": "string", "memcmp": "string",
    "memcpy": "mem", "memset": "mem",
    "puts": "io", "printf": "io", "__printf_chk": "io", "fwrite": "io",
    "write": "io", "open": "io", "read": "io", "fopen": "io",
    "malloc": "heap", "free": "heap",
    "__stack_chk_fail": "hardening",
}


def canonical(sym: str) -> str:
    """Fold libc's standard-revision aliases onto the plain name. glibc 2.38+
    compiled for C23 binds strtoul to __isoc23_strtoul (and older builds bind
    sscanf to __isoc99_sscanf); the capability is the same."""
    for pre in ("__isoc23_", "__isoc99_"):
        if sym.startswith(pre):
            return sym[len(pre):]
    return sym


def context(binary: dict) -> dict:
    """Build the FCA formal context: objects = functions, attributes = capabilities.

    Only *imported* symbols become capabilities (calls to local functions are
    graph edges, not capabilities), so the concepts stay about what the binary
    can do, not how its own functions are wired."""
    imports = set(binary.get("imports", []))
    ctx = {}
    for fn in binary["functions"]:
        caps = set()
        for c in fn.get("calls", []):
            if c in imports:
                caps.add(CAPABILITY.get(canonical(c), canonical(c)))
        if fn.get("anti_debug"):
            caps.add("anti-debug")
        if caps:
            ctx[fn["name"]] = sorted(caps)
    return ctx


def analyse(binary: dict) -> dict:
    ctx = context(binary)
    if not ctx:
        return {"context": {}, "lattice": None}
    result = fca.analyse(ctx, min_support=1)
    return {"context": ctx, "lattice": result}
