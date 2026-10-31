"""Harvest a family of git repositories into one dataset (no model involved).

Privacy rules, applied here so nothing downstream can leak them:
- author emails never leave this module; people appear as identity labels;
- Claude-Session URLs are reduced to short hashes (enough to see one session touch two repos);
- a repo marked "private" keeps only times, identity classes and sizes: no subjects, paths,
  branch names or file lists.
A public repo's own commits keep the paths they touched (git numstat, renames not followed), which the
concepts and bridges stages read; upstream history in a fork keeps none.
"""

from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import os
import re

from . import gitio

FAMILY_AUTHOR_RE = r"(?i)(hagopjay|hagsmac|hagop jay|claude|x3d-xfer|noreply@anthropic)"

X3D_EXT = {".x3d", ".x3dv", ".x3dj", ".x3dz", ".x3db", ".wrl"}
MEDIA_EXT = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".exr", ".hdr", ".ply", ".splat", ".spz",
    ".ksplat", ".sog", ".glb", ".gltf", ".obj", ".fbx", ".mp4", ".webm", ".blend", ".ico", ".ttf",
    ".woff", ".woff2", ".wasm", ".pdf",
}
DATA_EXT = {".json", ".jsonl", ".csv", ".tsv", ".ttl", ".owl", ".rdf", ".nt", ".xml", ".xsd", ".parquet", ".npz", ".npy", ".pkl"}
CONFIG_EXT = {".yaml", ".yml", ".toml", ".cfg", ".ini", ".lock", ".env", ".txt"}
WEB_EXT = {".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".map"}
CODE_EXT = {".py", ".rs", ".go", ".c", ".cc", ".cpp", ".h", ".hpp", ".java", ".sh", ".bash", ".glsl", ".frag", ".vert", ".lua", ".rb", ".r", ".jl", ".sql"}
DOC_EXT = {".md", ".rst", ".adoc", ".tex"}

KIND_ORDER = ["code", "tests", "web", "x3d", "data", "docs", "media", "config", "ci", "notebook", "log", "other"]


def classify_path(path: str) -> str:
    low = path.lower()
    name = os.path.basename(low)
    ext = os.path.splitext(name)[1]
    parts = low.split("/")
    if low.startswith(".github/") or name in {".gitlab-ci.yml", "dockerfile", "makefile"}:
        return "ci"
    if ext in X3D_EXT:
        return "x3d"
    is_test_name = (
        name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".spec.js", ".test.ts", ".spec.ts", ".test.mjs"))
        or name == "conftest.py"
    )
    if (ext in CODE_EXT or ext in WEB_EXT) and (is_test_name or any(p in {"test", "tests", "__tests__"} for p in parts[:-1])):
        return "tests"
    if ext == ".ipynb":
        return "notebook"
    if ext == ".log":
        return "log"
    if ext in DOC_EXT or name in {"readme", "license", "licence", "changelog", "notice", "authors"}:
        return "docs"
    if ext in MEDIA_EXT:
        return "media"
    if ext in DATA_EXT:
        return "data"
    if ext in WEB_EXT:
        return "web"
    if ext in CODE_EXT:
        return "code"
    if ext in CONFIG_EXT or name.startswith("."):
        return "config"
    return "other"


def iso(t: int) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day(t: int) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")


def session_token(url: str) -> str:
    return "s-" + hashlib.sha256(url.strip().encode()).hexdigest()[:10]


def ident_hash(s: str) -> str:
    """How a family config names a person without writing the name or address: sha256 of the lowercased text, 16 hex digits."""
    return hashlib.sha256(s.strip().lower().encode("utf-8")).hexdigest()[:16]


class IdentityBook:
    def __init__(self, cfg: dict):
        self.rules = cfg.get("identities", [])
        self.default = cfg.get("default_identity", {"label": "other", "class": "other"})
        self.seen: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    def classify(self, name: str, email: str) -> tuple[str, str]:
        user, _, domain = email.lower().partition("@")
        name_h, email_h = ident_hash(name), ident_hash(email)
        for rule in self.rules:
            if name in rule.get("match_names", []) or name_h in rule.get("match_name_sha256", []):
                return rule["label"], rule["class"]
        for rule in self.rules:
            if email_h in rule.get("match_email_sha256", []):
                return rule["label"], rule["class"]
        for rule in self.rules:
            users = [u.lower() for u in rule.get("match_email_users", [])]
            domains = [d.lower() for d in rule.get("match_email_domains", [])]
            if users and user in users and (not domains or domain in domains):
                return rule["label"], rule["class"]
            if domains and not users and domain in domains:
                return rule["label"], rule["class"]
        return self.default["label"], self.default["class"]


def text_lines(data: bytes) -> int | None:
    if not data:
        return 0
    if b"\0" in data[:8192]:
        return None
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def count_tests(path: str, data: bytes) -> int:
    if not data or b"\0" in data[:8192]:
        return 0
    text = data.decode("utf-8", errors="replace")
    if path.endswith(".py"):
        return len(re.findall(r"^\s*(?:async\s+)?def\s+test_\w*\s*\(", text, flags=re.M))
    if path.endswith((".js", ".mjs", ".ts", ".cjs")):
        return len(re.findall(r"(?<![\w.])(?:it|test)\s*\(\s*['\"`]", text))
    return 0


def harvest_repo(rcfg: dict, root: str, book: IdentityBook) -> dict:
    path = os.path.join(root, rcfg["dir"])
    name = rcfg["name"]
    private = rcfg.get("visibility") == "private"
    kind = rcfg["kind"]
    rec: dict = {
        "name": name,
        "kind": kind,
        "visibility": rcfg.get("visibility", "public"),
        "role": rcfg.get("role", ""),
        "github": rcfg.get("github", {}),
        "present": os.path.isdir(os.path.join(path, ".git")),
    }
    if not rec["present"]:
        rec["note"] = "no clone found; listed from the family config only"
        return rec

    dref = gitio.default_ref(path)
    rec["default_branch"] = None if private else dref.removeprefix("origin/")
    rec["head"] = None if private else gitio.git(path, "rev-parse", dref).strip()
    rec["commits_all_refs"] = int(gitio.git(path, "rev-list", "--all", "--count").strip() or 0)

    upstream_shas: set[str] = set()
    if rcfg.get("upstream"):
        up = os.path.join(root, rcfg["upstream"]["dir"])
        if os.path.isdir(os.path.join(up, ".git")):
            upstream_shas = gitio.rev_set(up, "--all")
            rec["upstream"] = {
                "name": rcfg["upstream"]["name"],
                "commits": len(upstream_shas),
                "head": gitio.git(up, "rev-parse", gitio.default_ref(up)).strip(),
            }

    on_default = gitio.rev_set(path, dref)
    if kind == "fork-idle":
        fam = gitio.git(path, "log", "--all", "--perl-regexp", f"--author={FAMILY_AUTHOR_RE}", "--format=%H", check=False)
        rec["commits"] = []
        rec["family_commits"] = len([x for x in fam.splitlines() if x.strip()])
        rec["note"] = "fork with no commits by the family's identities" if not rec["family_commits"] else "idle fork"
        rec["tree"] = {"files": 0, "bytes": 0, "lines": 0, "by_kind": {}, "tests": 0, "x3d_files": 0}
        rec["own_commits"] = rec["family_commits"]
        rec["_blobs"], rec["_path"], rec["_dref"] = [], path, dref
        return rec

    author_regex = FAMILY_AUTHOR_RE if (kind == "fork-active" and not upstream_shas) else None
    commits = []
    file_hist: dict[str, dict] = {}
    for c in gitio.log_with_numstat(path, ["--all"], author_regex=author_regex):
        label, cls = book.classify(c["author_name"], c["author_email"])
        is_up = c["sha"] in upstream_shas
        adds = sum(a for a, _, _ in c["numstat"] if a is not None)
        dels = sum(d for _, d, _ in c["numstat"] if d is not None)
        ai_trailer = any("claude" in x.lower() or "anthropic" in x.lower() for x in c["coauthors"])
        entry = {
            "t": c["t"],
            "who": label,
            "cls": cls,
            "files": len(c["numstat"]),
            "add": adds,
            "del": dels,
            "merge": len(c["parents"]) > 1,
            "ai_trailer": ai_trailer,
            "sessions": [session_token(s) for s in c["sessions"]],
            "default": c["sha"] in on_default,
            "upstream": is_up,
        }
        if not private:
            entry["sha"] = c["sha"][:12]
            entry["subject"] = c["subject"][:160]
            kinds = collections.Counter(classify_path(p) for _, _, p in c["numstat"])
            entry["kinds"] = dict(kinds)
            if not is_up:
                entry["paths"] = sorted({p for _, _, p in c["numstat"]})   # for the concepts and bridges stages; renames not followed
        commits.append(entry)
        if private or not entry["default"] or entry["merge"]:
            continue
        for a, d, p in c["numstat"]:
            h = file_hist.setdefault(p, {"commits": 0, "churn": 0, "first": c["t"], "last": c["t"], "by": collections.Counter()})
            h["commits"] += 1
            ch = (a or 0) + (d or 0)
            h["churn"] += ch
            h["first"] = min(h["first"], c["t"])
            h["last"] = max(h["last"], c["t"])
            h["by"][cls] += max(ch, 1)
    commits.sort(key=lambda e: e["t"])
    rec["commits"] = commits

    own = [e for e in commits if not e["upstream"]]
    rec["own_commits"] = len(own)
    if own:
        rec["first_own"] = iso(own[0]["t"])
        rec["last_own"] = iso(own[-1]["t"])
    by_cls = collections.Counter(e["cls"] for e in own)
    rec["own_by_class"] = dict(by_cls)
    rec["own_lines"] = {"add": sum(e["add"] for e in own), "del": sum(e["del"] for e in own)}
    rec["ai_trailer_commits"] = sum(1 for e in own if e["ai_trailer"])
    days = sorted({day(e["t"]) for e in own})
    rec["active_days"] = len(days)
    rec["streak_days"] = longest_streak(days)
    per_day = collections.Counter(day(e["t"]) for e in own)
    if per_day:
        peak = max(per_day.items(), key=lambda kv: (kv[1], kv[0]))
        rec["peak_day"] = {"date": peak[0], "commits": peak[1]}

    # Branches: names for public repos, a count for private ones.
    br = list(gitio.branches(path))
    rec["branch_count"] = len(br)
    if not private:
        out = []
        for bname, ts, obj in br:
            ahead = gitio.git(path, "rev-list", "--count", f"{dref}..{obj}", check=False).strip()
            out.append({"name": bname, "last": iso(ts) if ts else None, "ahead_of_default": int(ahead or 0)})
        rec["branches"] = sorted(out, key=lambda b: b["last"] or "")

    # Files at the default branch tip.
    files = []
    kind_tot: dict[str, dict] = {}
    tests = 0
    blobs = []
    fork_scope = None
    if kind == "fork-active" and not upstream_shas:
        fork_scope = set()
        for c in gitio.log_with_numstat(path, ["--all"], author_regex=FAMILY_AUTHOR_RE):
            fork_scope.update(p for _, _, p in c["numstat"])
    if kind != "fork-active" or upstream_shas:
        for blob, size, p in gitio.ls_tree(path, dref):
            k = classify_path(p)
            data = b""
            lines = None
            if size <= 2_000_000 and k not in {"media"}:
                data = gitio.cat_blob(path, blob)
                lines = text_lines(data)
                tests += count_tests(p, data) if k == "tests" else 0
            blobs.append((blob, size, p))
            kt = kind_tot.setdefault(k, {"files": 0, "bytes": 0, "lines": 0})
            kt["files"] += 1
            kt["bytes"] += size
            kt["lines"] += lines or 0
            if not private:
                h = file_hist.get(p)
                by = h["by"] if h else collections.Counter()
                tot = sum(by.values()) or 1
                files.append(
                    {
                        "path": p,
                        "kind": k,
                        "bytes": size,
                        "lines": lines,
                        "commits": h["commits"] if h else 0,
                        "churn": h["churn"] if h else 0,
                        "first": iso(h["first"]) if h else None,
                        "last": iso(h["last"]) if h else None,
                        "share": {c: round(v / tot, 3) for c, v in by.items()},
                    }
                )
    rec["tree"] = {
        "files": sum(v["files"] for v in kind_tot.values()),
        "bytes": sum(v["bytes"] for v in kind_tot.values()),
        "lines": sum(v["lines"] for v in kind_tot.values()),
        "by_kind": {k: kind_tot[k] for k in KIND_ORDER if k in kind_tot},
        "tests": tests,
        "x3d_files": kind_tot.get("x3d", {}).get("files", 0),
    }
    if fork_scope is not None:
        rec["tree"]["note"] = "fork: only files the family's commits touched are counted below"
        rec["fork_touched_paths"] = sorted(fork_scope) if not private else len(fork_scope)
    if not private:
        rec["files"] = files
    rec["_blobs"] = blobs  # internal: used by tie detection, stripped before writing
    rec["_path"] = path
    rec["_dref"] = dref
    return rec


def longest_streak(days: list[str]) -> int:
    best = cur = 0
    prev = None
    for d in days:
        cur_d = dt.date.fromisoformat(d)
        cur = cur + 1 if prev is not None and (cur_d - prev).days == 1 else 1
        best = max(best, cur)
        prev = cur_d
    return best


def harvest_family(cfg: dict, root: str) -> dict:
    book = IdentityBook(cfg)
    repos = [harvest_repo(r, root, book) for r in cfg["repos"]]
    return {
        "family": cfg["family"],
        "title": cfg.get("title", cfg["family"]),
        "lineage": cfg.get("lineage", []),
        "lineage_source": cfg.get("lineage_source", ""),
        "identities": [
            {k: v for k, v in r.items() if k in {"label", "class", "note"}} for r in cfg.get("identities", [])
        ]
        + [cfg.get("default_identity", {})],
        "repos": repos,
    }


def strip_internal(dataset: dict) -> dict:
    out = dict(dataset)
    out["repos"] = [{k: v for k, v in r.items() if not k.startswith("_")} for r in dataset["repos"]]
    return out
