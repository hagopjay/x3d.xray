"""Find ties between the repos of a family, deterministically, with evidence.

Detectors (each tie carries the files and lines that ground it):
- pin:          a commit hash of repo B written inside repo A (CI pins, lock files, notes);
- dependency:   repo B named in a manifest, an install line, an import, or a CI workflow of repo A;
- mention:      repo B named in A's code, tests, docs or data (weaker, graded lower);
- shared_file:  the same file content (same git blob) at both tips: copied or vendored code;
- session:      one Claude session committed to both repos (from Claude-Session trailers);
- structure:    fork-of, second-account work, and the lineage HagopJay stated himself.

Grades follow the Loom: C Confirmed, L Likely, S Suspected. A model never sets a grade here;
reviewers may only downgrade or annotate, and their notes are kept beside the machine grade.
"""

from __future__ import annotations

import collections
import os
import re

from . import gitio
from .harvest import classify_path

HEX_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{7,40}(?![0-9a-fA-F])")
MANIFESTS = {
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg", "setup.py",
    "package.json", "environment.yml", "environment.yaml", "pipfile", "uv.lock", "poetry.lock",
}
PIN_CONTEXT = re.compile(r"(?i)(commit|pin|sha|rev|ref|@|checkout|tag|validator|x3d_mcp|fork)")
TRIVIAL_NAMES = {"license", "licence", ".gitignore", "__init__.py", ".gitattributes", "code_of_conduct.md", "security.md"}
MAX_EVIDENCE = 12


def _alias_patterns(cfg: dict) -> dict[str, re.Pattern]:
    owner = cfg.get("owner", "")
    pats = {}
    for r in cfg["repos"]:
        alts = [re.escape(a) for a in r.get("aliases", [])]
        alts.append(re.escape(f"github.com/{owner}/{r['name']}"))
        # an alias must not be glued to word characters on either side
        pats[r["name"]] = re.compile(r"(?<![\w-])(?:" + "|".join(sorted(set(alts), key=len, reverse=True)) + r")(?![\w-])")
    return pats


MENTION_ORDER = ["dependency", "ci", "url-code", "url-config", "code", "tests", "url-docs", "docs", "web", "url-web",
                 "config", "notebook", "url-data", "data", "x3d", "log", "url-other", "other"]


def _window(text: str, start: int, end: int, radius: int = 90) -> str:
    """The text around a hit, kept on its own line."""
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    le = len(text) if le == -1 else le
    a = max(ls, start - radius)
    b = min(le, end + radius)
    return ("…" if a > ls else "") + text[a:b].strip() + ("…" if b < le else "")


def _mention_kind(path: str, line: str, name: str, owner: str) -> str:
    low = path.lower()
    base = os.path.basename(low)
    if low.startswith(".github/workflows/"):
        return "ci"
    if base in MANIFESTS or base.startswith("requirements"):
        return "dependency"
    stripped = line.strip()
    if re.match(r"^(from|import)\s+" + re.escape(name.replace("-", "_").replace(".", "_")), stripped):
        return "dependency"
    kind = classify_path(path)
    if kind not in {"data", "x3d", "log", "media"} and ("pip install" in stripped or "git+https://" in stripped or "git clone" in stripped):
        return "dependency"
    if f"github.com/{owner}/" in line:
        return "url-" + kind
    return kind


def find_ties(cfg: dict, dataset: dict) -> dict:
    owner = cfg.get("owner", "")
    pats = _alias_patterns(cfg)
    repos = {r["name"]: r for r in dataset["repos"]}
    private = {n for n, r in repos.items() if r.get("visibility") == "private"}

    # Index of commit hashes per repo (first 12 hex digits), for pin detection.
    sha_index: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for n, r in repos.items():
        for c in r.get("commits", []):
            s = c.get("sha")
            if s:
                sha_index[s[:7]].append((n, s))
        up = r.get("upstream")
        if up and up.get("head"):
            sha_index[up["head"][:7]].append((n, up["head"][:12]))
    # also index full upstream history of forks, so a pin to an upstream commit counts
    pairs: dict[tuple[str, str], dict] = {}

    def pair(a: str, b: str) -> dict:
        key = (a, b)
        if key not in pairs:
            pairs[key] = {"from": a, "to": b, "kinds": collections.Counter(), "evidence": []}
        return pairs[key]

    def add_ev(p: dict, kind: str, path: str, line_no: int | None, text: str | None, src: str):
        p["kinds"][kind] += 1
        if len(p["evidence"]) < MAX_EVIDENCE * 4:
            if src in private:
                p["evidence"].append({"kind": kind, "path": "[private repo]", "line": None, "text": None})
            else:
                p["evidence"].append({"kind": kind, "path": path, "line": line_no, "text": (text or "").strip()[:200]})

    # The observer effect: the tool's own repo names every repo it reads (its config, spec, tests, docs, layout), by construction.
    # Those mentions and hash-shaped strings are set aside and counted, and only the uses its config declares become ties.
    observers = {r["name"]: r for r in cfg.get("repos", []) if r.get("observer")}
    set_aside = collections.Counter()
    blob_owners: dict[str, list[tuple[str, str, int]]] = collections.defaultdict(list)
    scanned = collections.Counter()
    for n, r in repos.items():
        rpath = r.get("_path")
        if not rpath:
            continue
        for blob, size, path in r.get("_blobs", []):
            base = os.path.basename(path).lower()
            if size >= 1024 and base not in TRIVIAL_NAMES:
                blob_owners[blob].append((n, path, size))
            kind = classify_path(path)
            if kind == "media" or size > 1_500_000:
                continue
            data = gitio.cat_blob(rpath, blob)
            if not data or b"\0" in data[:8192]:
                continue
            scanned[n] += 1
            text = data.decode("utf-8", errors="replace")
            if n in observers:
                for other, pat in pats.items():
                    if other != n:
                        k = sum(1 for _ in pat.finditer(text))
                        if k:
                            set_aside["mentions"] += k
                            set_aside["files"] += 1
                set_aside["hash_shaped"] += sum(1 for m in HEX_RE.finditer(text) if not m.group(0).isdigit() and sha_index.get(m.group(0)[:7]))
                continue
            # mentions of other repos, by alias; each hit is judged on a window around it,
            # so a one-line JSON file cannot turn into a "dependency" because of distant text
            for other, pat in pats.items():
                if other == n:
                    continue
                hits = []
                for m in pat.finditer(text):
                    hits.append((text.count("\n", 0, m.start()) + 1, _window(text, m.start(), m.end())))
                    if len(hits) >= 50:
                        break
                if not hits:
                    continue
                p = pair(n, other)
                kinds_here = collections.Counter(_mention_kind(path, w, other, owner) for _, w in hits)
                ml = p.setdefault("mention_lines", collections.Counter())
                for k, cnt in kinds_here.items():
                    ml[k] += cnt
                best = min(kinds_here, key=lambda k: MENTION_ORDER.index(k) if k in MENTION_ORDER else len(MENTION_ORDER))
                ln_no, win = next(h for h in hits if _mention_kind(path, h[1], other, owner) == best)
                add_ev(p, "mention:" + best, path, ln_no, win, n)
            # commit hashes of other repos written here
            for m in HEX_RE.finditer(text):
                tok = m.group(0)
                if tok.isdigit():
                    continue
                for other, sha in sha_index.get(tok[:7], []):
                    if other == n:
                        continue
                    if not (sha.startswith(tok[:12]) and (len(tok) <= 12 or tok.startswith(sha))):
                        continue
                    win = _window(text, m.start(), m.end(), 60)
                    strong = len(tok) >= 12 or bool(PIN_CONTEXT.search(win))
                    line_no = text.count("\n", 0, m.start()) + 1
                    pp = pair(n, other)
                    add_ev(pp, "pin" if strong else "pin-weak", path, line_no, _window(text, m.start(), m.end()), n)
                    if strong:
                        pp.setdefault("pin_targets", collections.Counter())[sha] += 1

    # same file content at both tips
    for blob, owners in blob_owners.items():
        names = sorted({o[0] for o in owners})
        if len(names) < 2:
            continue
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pa = next(o for o in owners if o[0] == a)
                pb = next(o for o in owners if o[0] == b)
                p = pair(a, b)
                p["kinds"]["shared_file"] += 1
                p.setdefault("shared_bytes", 0)
                p["shared_bytes"] += pa[2]
                if len([e for e in p["evidence"] if e["kind"] == "shared_file"]) < MAX_EVIDENCE:
                    pa_path = "[private repo]" if a in private else pa[1]
                    pb_path = "[private repo]" if b in private else pb[1]
                    p["evidence"].append({"kind": "shared_file", "path": pa_path, "other_path": pb_path, "bytes": pa[2], "line": None, "text": None})

    # one Claude session committing to two repos
    sess = collections.defaultdict(lambda: collections.defaultdict(list))
    for n, r in repos.items():
        for c in r.get("commits", []):
            for s in c.get("sessions", []):
                sess[s][n].append(c["t"])
    for s, by_repo in sess.items():
        names = sorted(by_repo)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                p = pair(a, b)
                p["kinds"]["session"] += 1
                if len([e for e in p["evidence"] if e["kind"] == "session"]) < MAX_EVIDENCE:
                    p["evidence"].append(
                        {"kind": "session", "path": None, "line": None, "text": f"session {s}: {len(by_repo[a])} commits in {a}, {len(by_repo[b])} in {b}",
                         "t": [min(by_repo[a] + by_repo[b]), max(by_repo[a] + by_repo[b])]}
                    )

    # structure: forks, second account, owner-stated lineage
    for n, r in repos.items():
        up = r.get("upstream")
        if up:
            p = pair(n, up["name"])
            p["kinds"]["fork_of"] += 1
            p["evidence"].append({"kind": "fork_of", "path": None, "line": None, "text": f"{n} shares {up['commits']} commits with {up['name']}"})
        if r.get("kind", "").startswith("fork") and not up:
            p = pair(n, n + " (upstream)")
            p["kinds"]["fork_of"] += 1
            p["evidence"].append({"kind": "fork_of", "path": None, "line": None, "text": f"{n} is a GitHub fork"})
        hs = [c for c in r.get("commits", []) if c.get("who") == "hagsmac"]
        if hs:
            p = pair("hagsmac (account)", n)
            p["kinds"]["second_account"] += len(hs)
            p["evidence"].append({"kind": "second_account", "path": None, "line": None, "text": f"{len(hs)} commits by hagsmac in {n}"})
    for n, r in observers.items():
        for target, why in (r.get("uses") or {}).items():
            p = pair(n, target)
            p["kinds"]["uses"] += 1
            p["evidence"].append({"kind": "uses", "path": None, "line": None, "text": f"declared by the family config: {why}"})
    lineage, resolved = resolve_lineage(cfg.get("lineage", []), set(repos), cfg.get("vocabulary") or {})
    note = "; ".join(f"'{a}' read as {b} through the vocabulary" for a, b in resolved.items())
    for a, b in zip(lineage, lineage[1:]):
        p = pair(a, b)
        p["kinds"]["lineage"] += 1
        p["evidence"].append({"kind": "lineage", "path": None, "line": None,
                              "text": cfg.get("lineage_source", "stated by the owner") + (f" ({note})" if note else "")})

    out = []
    for p in pairs.values():
        p["kinds"] = dict(p["kinds"])
        if "mention_lines" in p:
            p["mention_lines"] = dict(p["mention_lines"])
        if "pin_targets" in p:
            p["pin_targets"] = dict(p["pin_targets"].most_common())
        p["grade"], p["grade_reason"] = machine_grade(p)
        out.append(p)
    out.sort(key=lambda p: ({"C": 0, "L": 1, "S": 2}[p["grade"]], p["from"], p["to"]))
    observer = {"repos": sorted(observers), "set_aside": dict(set_aside),
                "why": "The tool's own files name every repo it reads, by construction, so they are not evidence of a tie; only its declared uses count."} if observers else None
    return {"pairs": out, "scanned_files": dict(scanned), "observer": observer, "detectors": __doc__.strip().splitlines()[2:9]}


def resolve_lineage(stated: list[str], known: set[str], vocabulary: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """The owner's words go through the vocabulary first. ".prov" is a stage inside x3d.ML, not a repo, so the stated chain
    blender-io -> .prov -> ML becomes blender-io -> ML. Returns the chain and what was resolved; the evidence keeps the words."""
    vocab = {k.lower(): v for k, v in vocabulary.items()}
    chain, resolved = [], {}
    for x in stated:
        y = x if x in known else vocab.get(x.lower(), x)
        if y != x:
            resolved[x] = y
        if not chain or chain[-1] != y:
            chain.append(y)
    return chain, resolved


STRONG = {"pin", "shared_file", "session", "fork_of", "lineage", "second_account", "uses"}


def machine_grade(p: dict) -> tuple[str, str]:
    k = p["kinds"]
    for strong in ["pin", "shared_file", "session", "fork_of", "lineage", "second_account", "uses"]:
        if k.get(strong):
            return "C", strong
    mention_kinds = {e["kind"].split(":", 1)[1] for e in p["evidence"] if e["kind"].startswith("mention:")}
    if mention_kinds & {"dependency", "ci", "url-code", "url-config"}:
        return "C", "named in a manifest, import, install line or CI workflow"
    if mention_kinds & {"code", "tests", "url-docs", "docs", "web", "config", "notebook"}:
        return "L", "named in code, tests or docs"
    if k.get("pin-weak") or mention_kinds:
        return "S", "only short hashes or mentions inside data files"
    return "S", "no strong detector fired"
