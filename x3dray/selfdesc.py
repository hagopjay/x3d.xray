"""The tool's description of itself, so the page's Claude (and any agent on the MCP server) knows what built the page.

self.json holds:
- the tool: name, version, its own git head, and whether the working tree had uncommitted changes;
- the pipeline: every stage, its module and entry point, inputs and outputs, and whether a model takes part;
- every module's sha256, size, docstring and top-level definitions with line numbers;
- the code that computed each profile signal (file, lines, source), so an answer can show its arithmetic;
- the tool inside its own data: x3d.xray is one of the repos it x-rays;
- Claude inside the data: how many of the commits Claude wrote, and an honest note on what that does and does not mean;
- the limits of the build.
No model is involved here.
"""

from __future__ import annotations

import ast
import collections
import datetime as dt
import hashlib
import os
import re
import subprocess

from . import __version__

PKG = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(PKG)

PIPELINE = [
    {"stage": "harvest", "module": "x3dray/harvest.py", "entry": "harvest_family", "inputs": ["family config", "git clones"],
     "outputs": ["dataset.json"], "model": None,
     "does": "Reads every repo's history on all refs and its tip tree. Emails never leave this module; Claude-Session URLs become short hashes; a private repo keeps only times, identity classes and sizes."},
    {"stage": "ties", "module": "x3dray/ties.py", "entry": "find_ties", "inputs": ["dataset.json", "git blobs at each tip"],
     "outputs": ["ties.json"], "model": None,
     "does": "Finds ties between repos: written commit hashes (pins), names in manifests, imports, CI, code and docs, identical files, one Claude session committing to two repos, forks, the second account and the stated lineage. Grades C, L or S by rule."},
    {"stage": "concepts", "module": "x3dray/structure.py + x3dray/fca.py", "entry": "concepts", "inputs": ["dataset.json"],
     "outputs": ["concepts.json"], "model": None,
     "does": "Formal concept analysis, three ways: files by the kinds of author that touched them (a lattice), repos by the kinds of author they have seen, and commits by the areas they touch (rules that name their exceptions). Mass edits, merges and private commits are set aside and counted. Its own check re-derives every witness and count from the commits, apart from the lattice code, and fails the stage if one does not hold.",
     "method": {"traps": ["A few mass edits (generated runs, assets, sweeps) carry most file touches; a commit of more than 15 files is set aside and counted, or every file it touched would look coupled to every other.",
                          "Exact implications are too strict for real repos, so area rules are kept at 80% confidence or more and every rule lists the commits that break it."],
                "reads": "how_built stage concepts; the concepts tool answers lookups"}},
    {"stage": "bridges", "module": "x3dray/structure.py + x3dray/centrality.py", "entry": "bridges", "inputs": ["dataset.json"],
     "outputs": ["bridges.json"], "model": None,
     "does": "Betweenness, cut vertices and passage points on three graphs: files that change in the same commit (two distances), Claude sessions by repo, and authors by repo. Exact arithmetic (fractions), so tied paths are real ties. Its own check recounts the session and author graphs by brute force, and the file graphs' sizes, clusters, cut vertices and which files lie on a shortest path.",
     "method": {"traps": ["Hub files (READMEs, build files) top raw betweenness because they change with everything; the Jaccard variant sets the high-churn files aside and normalises by how often each file changes. Both are kept.",
                          "Betweenness is not the same as a passage point: a passage point is a node every path between two groups must cross.",
                          "The session graph only sees commits that carry a Claude-Session trailer: two repos no chain of sessions joins have no recorded session link, not no link, and the ties may still join them.",
                          "Each part says what it read: the file graph reads usable commits only, the session graph every commit with a trailer, the author graph every commit but merges."],
                "reads": "how_built stage bridges; the bridges tool answers lookups"}},
    {"stage": "curated", "module": "x3dray/curated.py", "entry": "import_masterclass", "inputs": ["masterclass ecosystem.json and series.json"],
     "outputs": ["curated.json"], "model": None,
     "does": "Imports the fork masterclass's hand-graded ties and the fork's commit series, with addresses scrubbed."},
    {"stage": "profile", "module": "x3dray/profile.py", "entry": "profile_family", "inputs": ["dataset.json", "ties.json", "curated.json", "concepts.json", "bridges.json"],
     "outputs": ["profile.json"], "model": None,
     "does": "Computes the signals: totals, shares, rhythm, outliers, hotspots, pins. Each signal's sentence is built by code from its own numbers."},
    {"stage": "spec", "module": "x3dray/spec.py", "entry": "render_spec", "inputs": ["specs/<family>.json", "profile.json"],
     "outputs": ["spec.rendered.json"],
     "model": "Claude wrote the spec's prose (view captions, tour stops, suggested questions) in a Claude Code session. The prose holds no numbers of its own: every number is a {signal.path} slot that code fills from the profile, and the fact gate fails the build on a digit outside a slot or a slot that does not resolve.",
     "does": "Checks the spec against its schema and the profile, and fills its slots."},
    {"stage": "self", "module": "x3dray/selfdesc.py", "entry": "describe", "inputs": ["the tool's own source and git", "dataset.json", "profile.json"],
     "outputs": ["self.json"], "model": None, "does": "Writes this description."},
    {"stage": "compile", "module": "x3dray/compiler.py", "entry": "build", "inputs": ["dataset.json", "ties.json", "curated.json", "concepts.json", "bridges.json", "profile.json", "spec.rendered.json", "self.json"],
     "outputs": ["xray.x3d", "layout.json"], "model": None,
     "does": "Writes one X3D 4.0 scene: the time helix, the tie constellation, the code city, the concept lattice, the bridges, and the tool's own pipeline, with viewpoints for the tour."},
    {"stage": "page", "module": "x3dray/page.py", "entry": "build_page", "inputs": ["xray.x3d", "layout.json", "all JSON above", "x3dray/web/*"],
     "outputs": ["index.html", "bundle.json", "app.js", "tools.js", "page.css", "x_ite/", "fonts/", "publish.json"], "model": None,
     "does": "Assembles the Artifact folder: vendored X_ITE, the label font, the scene, the data bundle and the page code."},
    {"stage": "gates", "module": "x3dray/gates.py + x3dray/web/selftest.mjs + x3dray/web/live_gate.cjs", "entry": "run_all", "inputs": ["the page folder"], "outputs": ["gates.json", "frames/"], "model": None,
     "does": "XSD, the Loom gate with the fork validator, the fact gate (no digit outside a slot, every claim backed), the privacy gate, the witness gate (no tie without evidence; the concepts and bridges recomputed and re-derived from the commits), the tool gate (every tool runs under 32 KB, aliases resolve, unknown names are refused, the MCP server answers), and a live X_ITE run under a strict content policy in which Ask runs against a stand-in for the sample runtime."},
    {"stage": "receipt", "module": "x3dray/receipt.py", "entry": "write", "inputs": ["everything above"], "outputs": ["receipt.json"], "model": None,
     "does": "sha256 of every input, module, output and gate result, and one digest over them."},
    {"stage": "ask", "module": "x3dray/web/tools.js + x3dray/web/app.js", "entry": "makeTools, ask", "inputs": ["bundle.json", "the viewer's question"],
     "outputs": ["an answer, camera moves, highlights, notes in the scene"],
     "model": "Claude, on the viewer's own Claude account, through the Artifact's sample capability. It reads a digest of the data and calls the page's tools; it cannot browse and has no memory between questions.",
     "does": "The Ask panel."},
    {"stage": "mcp", "module": "x3dray/web/mcp.mjs", "entry": "stdio server", "inputs": ["bundle.json"], "outputs": ["tool results"],
     "model": "Any MCP client (Claude Code, Claude Desktop, another agent) calls the same data tools the page's Claude calls.",
     "does": "Serves the page's data tools over the Model Context Protocol, from the same tools.js."},
]

AI_ROLES = [
    {"where": "harvest, ties, concepts, bridges, curated, profile, compile, gates, receipt", "ai": "none", "check": "deterministic code; the receipt hashes it"},
    {"where": "the tool's code and the spec's prose", "ai": "Claude, in Claude Code sessions (see the Co-Authored-By and Claude-Session trailers in x3d.xray's history)",
     "check": "the spec holds no numbers of its own; the fact gate fails any digit outside a {slot} and any slot that does not resolve"},
    {"where": "the fork masterclass's 40 graded ties", "ai": "Claude, in an earlier Claude Code session, reading code, commits and CI",
     "check": "each tie carries its evidence; x3d-ray shows them beside its own machine-found ties instead of merging them"},
    {"where": "the Ask panel", "ai": "Claude, on the viewer's account", "check": "it answers from the digest and the tools' results; the page shows every tool call it made"},
    {"where": "the MCP server", "ai": "whatever agent connects", "check": "the same tools and the same 32 KB cap as the page"},
]

IDENTITY = {
    "repo": "a Kind: the GitHub owner/name in the family config; fork, private and idle are roles it plays, not kinds",
    "commit": "a Kind: its sha (12 hex digits shown); private repos keep the commit but not its sha",
    "file": "a Kind: repo plus path at the tip; renames are not followed in this version",
    "person": "a Role over commits: the identity label the family config assigns to an author name or a hashed address; unknown authors count as upstream contributors",
    "Claude session": "the short hash of a Claude-Session trailer URL",
    "tie": "machine ties: the (from, to) pair, with its evidence as witnesses; graded ties: the masterclass id E01 to E40",
    "signal": "its id in profile.json, with the code that computed it",
    "concept": "a set of kinds of author (its intent) with every file all of them touched (its extent); two concepts are the same when their intents are",
    "area": "a repo and the top-level folder of a path (x3d_mcp/src); files at the root are the area '.'",
}

LIMITS = [
    "A private repo shows only commit times, identity classes and sizes: no subjects, paths, branches or files.",
    "People appear as identity labels from the family config; an identity the config does not name counts as an upstream contributor.",
    "Lines changed come from git numstat, so a vendored schema or a generated file counts as much as hand-written code.",
    "The masterclass ties were graded on the date their source records; some repos have moved since (see curated.notes).",
    "Commits made after the harvest are not in the data, including this tool's own later commits.",
    "Concepts and bridges read file paths from git numstat without following renames, so a renamed file counts as two files.",
    "The file lattice, the area rules and the file co-change graph set aside merges, private commits and commits of more than 15 files; the repo lattice and the author graph leave out only merges, and the session graph reads every commit with a trailer. Each answer counts what its part set aside.",
]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _py_defs(src: str) -> tuple[str | None, list[dict]]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None, []
    doc = ast.get_docstring(tree)
    defs = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.append({"name": n.name, "line": n.lineno, "end": getattr(n, "end_lineno", n.lineno),
                         "kind": "class" if isinstance(n, ast.ClassDef) else "def"})
    return (doc.strip().splitlines()[0] if doc else None), defs


JS_DEF = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)|^(?:export\s+)?const\s+(\w+)\s*=", re.M)


def _js_defs(src: str) -> tuple[str | None, list[dict]]:
    m = re.match(r"\s*//\s*(.+)", src)
    defs = []
    for mm in JS_DEF.finditer(src):
        defs.append({"name": mm.group(1) or mm.group(2), "line": src.count("\n", 0, mm.start()) + 1, "kind": "def"})
    return (m.group(1).strip() if m else None), defs


def modules() -> list[dict]:
    out = []
    for base, _, files in os.walk(PKG):
        if "__pycache__" in base:
            continue
        for f in sorted(files):
            if not f.endswith((".py", ".js", ".mjs", ".cjs", ".css", ".html")):
                continue
            p = os.path.join(base, f)
            b = open(p, "rb").read()
            rel = os.path.relpath(p, REPO)
            src = b.decode("utf-8", errors="replace")
            doc, defs = _py_defs(src) if f.endswith(".py") else (_js_defs(src) if f.endswith((".js", ".mjs", ".cjs")) else (None, []))
            out.append({"path": rel, "sha256": _sha(b), "bytes": len(b), "lines": src.count("\n") + (0 if src.endswith("\n") else 1),
                        "doc": doc, "defs": defs})
    out.sort(key=lambda m: m["path"])
    return out


SIG_CALL = re.compile(r'sig\(\s*"(\w+)"')


def signal_code(profile_src: str, rel: str = "x3dray/profile.py") -> dict:
    """For each sig("<id>", ...) call: the code that computes it, from the end of the previous signal's call (or the
    blank line above) to this call's closing parenthesis."""
    lines = profile_src.splitlines()
    spans = []
    for m in SIG_CALL.finditer(profile_src):
        depth, i = 0, m.start() + 3
        while i < len(profile_src):
            c = profile_src[i]
            depth += (c == "(") - (c == ")")
            if depth == 0 and c == ")":
                break
            i += 1
        spans.append((m.group(1), profile_src.count("\n", 0, m.start()), profile_src.count("\n", 0, i)))
    out = {}
    prev_end = -1
    for sid, a, b in spans:
        first = a
        k = a - 1
        while k > prev_end and lines[k].strip() and not lines[k].lstrip().startswith("def "):
            first = k
            k -= 1
        prev_end = b
        if sid not in out:
            out[sid] = {"file": rel, "start": first + 1, "end": b + 1, "code": "\n".join(lines[first:b + 1])[:4000]}
    return out


def describe(dataset: dict, profile: dict, stamp: str | None = None, family_cfg: dict | None = None) -> dict:
    mods = modules()
    by_path = {m["path"]: m for m in mods}
    pipeline = []
    for st in PIPELINE:
        st = dict(st)
        paths = [p.strip() for p in st["module"].split("+")]
        st["sha256"] = {p: by_path[p]["sha256"][:16] for p in paths if p in by_path}
        st["lines"] = sum(by_path[p]["lines"] for p in paths if p in by_path)
        pipeline.append(st)

    prof_src = open(os.path.join(PKG, "profile.py"), encoding="utf-8").read()
    head = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--", "x3dray", "families", "specs")
    tool = {
        "name": "x3d.xray", "package": "x3dray", "version": __version__,
        "repo": "https://github.com/hagopjay/x3d.xray",
        "git": {"head": head[:12] if head else None, "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
                "uncommitted_changes": bool(status), "commits": int(_git("rev-list", "--count", "HEAD") or 0)},
        "built_at": stamp,
        "modules": len(mods), "lines_of_code": sum(m["lines"] for m in mods),
        "code_digest": _sha("".join(m["sha256"] for m in mods).encode())[:16],
    }

    # the tool inside its own data
    me = next((r for r in dataset.get("repos", []) if r.get("name") == "x3d.xray"), None)
    harvested = dataset.get("harvested_at")
    since = None
    if harvested:
        log = _git("log", "--format=%H", f"--since={harvested}")
        since = len(log.splitlines()) if log else 0
    own = [c for c in (me or {}).get("commits", []) if not c.get("upstream")]
    in_data = {
        "repo": "x3d.xray", "present": bool(me),
        "commits_in_data": len(own),
        "by_class": dict(collections.Counter(c["cls"] for c in own)),
        "first": own[0].get("sha") if own else None,
        "harvested_at": harvested,
        "commits_since_harvest": since,
        "where": {"helix": "its ring in the time helix, labelled x3d.xray", "constellation": "the node marked 'this tool'",
                  "city": "the district marked 'this tool'", "self": "the pipeline row in front of the helix",
                  "concepts and bridges": "its files are objects in the lattice and nodes in the co-change graph; the sessions that wrote it are in the session graph"},
        "note": "x3d.xray is one of the repos it x-rays. Its own commits are in the helix, its node in the constellation and its files in the city; commits made after the harvest are not.",
    }
    tm = next((s_ for s_ in profile.get("signals", []) if s_["id"] == "ties_machine"), None)
    if tm and "observer_set_aside" in tm["values"]:
        in_data["observer_effect"] = (f"Its own files name every repo it reads (config, spec, tests, docs), so {tm['values']['observer_set_aside']} such "
                                      "mentions were set aside rather than counted as ties; only the uses its config declares (the fork's "
                                      "XSD and validator, x3d.onto's Loom gate) are ties.")

    # Claude inside the data
    allc = [(r["name"], c) for r in dataset.get("repos", []) for c in r.get("commits", []) if not c.get("upstream")]
    ai = [(n, c) for n, c in allc if c["cls"] == "ai"]
    sess = collections.defaultdict(set)
    for n, c in allc:
        for s in c.get("sessions", []):
            sess[s].add(n)
    claude = {
        "commits": len(ai), "of": len(allc), "share_pct": round(100 * len(ai) / len(allc)) if allc else 0,
        "by_repo": dict(collections.Counter(n for n, _ in ai).most_common()),
        "lines": {"add": sum(c["add"] for _, c in ai), "del": sum(c["del"] for _, c in ai)},
        "sessions": len(sess), "multi_repo_sessions": sum(1 for v in sess.values() if len(v) > 1),
        "paired_commits": sum(1 for _, c in allc if c["cls"] == "you" and c.get("ai_trailer")),
        "first": min((c["t"] for _, c in ai), default=None),
        "note": ("The Claude answering in this page is not the session that wrote these commits and has no memory of them. "
                 "It knows them only from this data: commit times, sizes, subjects, trailers and session hashes."),
    }
    nm = [(n, c) for n, c in allc if not c.get("merge")]
    nb = [(n, c) for n, c in allc if c["cls"] != "bot"]
    claude["denominators"] = {
        "all own commits": f"{len(ai)} of {len(allc)}",
        "without merges": f"{sum(1 for _, c in nm if c['cls'] == 'ai')} of {len(nm)}",
        "without bots": f"{len(ai)} of {len(nb)}",
        "by lines changed": f"{claude['lines']['add'] + claude['lines']['del']:,} of {sum(c['add'] + c['del'] for _, c in allc):,}",
        "note": "Commits only count authorship. Commits under HagopJay's names with a Claude co-author trailer count as his; squashed or rebased history hides work either way.",
    }
    if claude["first"]:
        claude["first"] = dt.datetime.fromtimestamp(claude["first"], dt.timezone.utc).strftime("%Y-%m-%d")

    return {
        "tool": tool,
        "pipeline": pipeline,
        "ai_roles": AI_ROLES,
        "limits": LIMITS + ([f"curated: {n}" for n in (family_cfg or {}).get("curated_notes", [])]),
        "in_its_own_data": in_data,
        "claude_in_the_data": claude,
        "identity": IDENTITY,
        "modules": mods,
        "signal_code": signal_code(prof_src),
    }
