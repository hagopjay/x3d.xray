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
import bisect
import hashlib
import os
import re

from . import gitio, locators
from .harvest import classify_path
from .temporal import normalize_instant

HEX_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{7,64}(?![0-9a-fA-F])", re.I)
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


def _mention_kind(path: str, line: str, name: str, owner: str, matched_alias: str | None = None) -> str:
    low = path.lower()
    base = os.path.basename(low)
    if low.startswith(".github/workflows/"):
        return "ci"
    if base in MANIFESTS or base.startswith("requirements"):
        return "dependency"
    stripped = line.strip()
    for imported_name in {name, matched_alias} - {None}:
        if re.match(r"^(from|import)\s+" + re.escape(imported_name.replace("-", "_").replace(".", "_")) + r"(?=\s|\.|$)", stripped):
            return "dependency"
    kind = classify_path(path)
    if kind not in {"data", "x3d", "log", "media"} and ("pip install" in stripped or "git+https://" in stripped or "git clone" in stripped):
        return "dependency"
    if f"github.com/{owner}/" in line:
        return "url-" + kind
    return kind


def _evidence_key(e: dict) -> tuple:
    """Stable display priority; detector summaries, not this order, decide grades."""
    kind = e["kind"]
    strong = ["pin", "shared_file", "session", "fork_of", "lineage", "second_account", "uses"]
    if kind in strong:
        priority = strong.index(kind)
    elif kind.startswith("mention:"):
        mention = kind.split(":", 1)[1]
        priority = len(strong) + (MENTION_ORDER.index(mention) if mention in MENTION_ORDER else len(MENTION_ORDER))
    else:
        priority = len(strong) + len(MENTION_ORDER) + 1
    return (priority, kind, e.get("path") or "", e.get("line") or 0, e.get("text") or "", e.get("other_path") or "")


def _retain_evidence(p: dict, evidence: dict) -> None:
    """Bound the display sample while keeping a best witness for each detector kind.

    The complete kinds/mention_lines summaries are accumulated separately. Keeping
    one representative of each kind prevents many weak examples hiding the source
    of a stronger judgment. The omission count describes evidence records, not hits.
    """
    p["_evidence_records"] += 1
    candidates = sorted(p["evidence"] + [evidence], key=_evidence_key)
    by_kind = collections.defaultdict(list)
    for e in candidates:
        if len(by_kind[e["kind"]]) < MAX_EVIDENCE:
            by_kind[e["kind"]].append(e)
    representatives = [es[0] for es in by_kind.values()]
    chosen = sorted(representatives, key=_evidence_key)[:MAX_EVIDENCE * 4]
    remainder = sorted((e for es in by_kind.values() for e in es[1:]), key=_evidence_key)
    chosen.extend(remainder[:MAX_EVIDENCE * 4 - len(chosen)])
    p["evidence"] = sorted(chosen, key=_evidence_key)


def find_ties(cfg: dict, dataset: dict) -> dict:
    owner = cfg.get("owner", "")
    pats = _alias_patterns(cfg)
    repos = {r["name"]: r for r in dataset["repos"]}
    private = {n for n, r in repos.items() if r.get("visibility") == "private"}
    try:
        observed_at = normalize_instant(dataset.get("harvested_at"))
    except ValueError:
        observed_at = None

    locator_cache = {}

    def file_locator(src, path, blob, size, detector, line=None, data=None):
        """Record the already-pinned public source, never a mutable HEAD read."""
        key = (src, path, blob, detector, line)
        if key in locator_cache:
            return locator_cache[key]
        unavailable = lambda reason: {"locator_status": "unavailable", "locator_unavailable_reason": reason}
        r = repos[src]
        fmt, revision = r.get("object_format"), r.get("head")
        if src in private:
            result = {"locator_status": "withheld_private"}
        elif fmt not in {"sha1", "sha256"} or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-fA-F]{40}" if fmt == "sha1" else r"[0-9a-fA-F]{64}", revision):
            result = unavailable("no_public_pinned_revision")
        elif size > locators.MAX_BYTES:
            result = unavailable("unsupported_blob_size")
        else:
            try:
                path.encode("utf-8", errors="strict")
            except UnicodeError:
                result = unavailable("source_not_utf8_path")
            else:
                mode = r.get("_blob_modes", {}).get(path)
                if mode not in {"100644", "100755", "120000"}:
                    result = unavailable("source_content_unavailable")
                else:
                    if data is None:
                        data = gitio.cat_blob(r["_path"], blob)
                    if len(data) != size:
                        result = unavailable("source_content_unavailable")
                    else:
                        kwargs = {"line_start": line, "line_end": line} if line is not None else {}
                        try:
                            locator = locators.locator_from_blob(r.get("repo_id", "repo:" + src), revision, fmt, blob, path, data,
                                detector_id=detector, mode=mode, **kwargs)
                        except ValueError:
                            # A detector can inspect replacement-decoded text.
                            # Preserve the exact raw LF line as bytes if the
                            # source is not valid UTF-8; never claim text proof.
                            if line is not None:
                                a = 0
                                for _ in range(1, line):
                                    a = data.find(b"\n", a)
                                    if a < 0:
                                        break
                                    a += 1
                                if 0 <= a < len(data):
                                    newline = data.find(b"\n", a)
                                    b = len(data) if newline < 0 else newline + 1
                                    try:
                                        locator = locators.locator_from_blob(r.get("repo_id", "repo:" + src), revision, fmt, blob, path, data,
                                            detector_id=detector, mode=mode, byte_start=a, byte_end=b)
                                    except ValueError:
                                        locator = None
                                else:
                                    locator = None
                            else:
                                locator = None
                            actual = hashlib.new(fmt, b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
                            reason = "invalid_blob_identity" if actual != blob.lower() else "line_span_unavailable" if line is not None else "source_content_unavailable"
                            result = {"locator_status": "recorded", "locator": locator} if locator else unavailable(reason)
                        else:
                            result = {"locator_status": "recorded", "locator": locator}
        locator_cache[key] = result
        return result

    # Full object IDs are retained internally by the harvester until ties are
    # found. Old datasets with abbreviated sha fields remain usable, but cannot
    # prove a longer token's suffix. Index only public commits and upstream tips.
    sha_index: dict[str, set[tuple[str, str]]] = collections.defaultdict(set)
    for n, r in repos.items():
        if n in private:
            continue
        for c in r.get("commits", []):
            s = c.get("_oid") or c.get("oid") or c.get("sha")
            if s:
                sha_index[s[:7].lower()].add((n, s.lower()))
        up = r.get("upstream")
        if up and up.get("head"):
            sha_index[up["head"][:7].lower()].add((n, up["head"].lower()))
    pin_rejections = collections.Counter()
    pairs: dict[tuple[str, str], dict] = {}

    def pair(a: str, b: str) -> dict:
        key = (a, b)
        if key not in pairs:
            pairs[key] = {"from": a, "to": b, "kinds": collections.Counter(), "evidence": [], "_evidence_records": 0}
        return pairs[key]

    def add_ev(p: dict, kind: str, path: str, line_no: int | None, text: str | None, src: str, blob: str, size: int, data: bytes):
        p["kinds"][kind] += 1
        if src in private:
            _retain_evidence(p, {"kind": kind, "path": "[private repo]", "line": None, "text": None})
        else:
            locator = ({"locator_status": "withheld_private"} if p["from"] in private or p["to"] in private
                       else file_locator(src, path, blob, size, kind, line_no, data))
            _retain_evidence(p, {"kind": kind, "path": path, "line": line_no, "text": (text or "").strip()[:200], **locator})

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
                set_aside["hash_shaped"] += sum(1 for m in HEX_RE.finditer(text) if not m.group(0).isdigit() and sha_index.get(m.group(0)[:7].lower()))
                continue
            # Judge every hit, but retain only one best witness per file. A hit
            # display cap must not hide a later import/install line. Newline
            # offsets avoid repeatedly counting the whole prefix for each hit.
            newlines = [m.start() for m in re.finditer("\n", text)]
            for other, pat in pats.items():
                if other == n:
                    continue
                kinds_here = collections.Counter()
                best_hit = None
                for m in pat.finditer(text):
                    line_no = bisect.bisect_left(newlines, m.start()) + 1
                    window = _window(text, m.start(), m.end())
                    mention = _mention_kind(path, window, other, owner, m.group(0))
                    kinds_here[mention] += 1
                    priority = MENTION_ORDER.index(mention) if mention in MENTION_ORDER else len(MENTION_ORDER)
                    if best_hit is None or priority < best_hit[0]:
                        best_hit = (priority, mention, line_no, window)
                if best_hit is None:
                    continue
                p = pair(n, other)
                ml = p.setdefault("mention_lines", collections.Counter())
                for k, cnt in kinds_here.items():
                    ml[k] += cnt
                _, best, ln_no, win = best_hit
                add_ev(p, "mention:" + best, path, ln_no, win, n, blob, size, data)
            # commit hashes of other repos written here
            for m in HEX_RE.finditer(text):
                tok = m.group(0).lower()
                if tok.isdigit():
                    continue
                indexed = sha_index.get(tok[:7], set())
                matches = {(other, sha) for other, sha in indexed if sha.startswith(tok)}
                if not matches:
                    if indexed:
                        pin_rejections["unresolved_or_longer_than_recorded_id"] += 1
                    continue
                # A short token naming several different commits is not an
                # unambiguous pin. The same full object in several forks is fine.
                if len({sha for _, sha in matches}) != 1:
                    pin_rejections["ambiguous_abbreviation"] += 1
                    continue
                for other, sha in sorted(matches):
                    if other == n:
                        continue
                    win = _window(text, m.start(), m.end(), 60)
                    strong = len(tok) >= 12 or bool(PIN_CONTEXT.search(win))
                    line_no = bisect.bisect_left(newlines, m.start()) + 1
                    pp = pair(n, other)
                    add_ev(pp, "pin" if strong else "pin-weak", path, line_no, _window(text, m.start(), m.end()), n, blob, size, data)
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
                pa_path = "[private repo]" if a in private else pa[1]
                pb_path = "[private repo]" if b in private else pb[1]
                if a in private or b in private:
                    source = {"locator_status": "withheld_private"}
                else:
                    source_a = file_locator(a, pa[1], blob, pa[2], "shared_file")
                    source_b = file_locator(b, pb[1], blob, pb[2], "shared_file")
                    if all(s.get("locator_status") == "recorded" for s in (source_a, source_b)):
                        source = {"locator_status": "recorded", "locators": [source_a["locator"], source_b["locator"]]}
                    else:
                        reason = next(s["locator_unavailable_reason"] for s in (source_a, source_b) if s.get("locator_status") == "unavailable")
                        source = {"locator_status": "unavailable", "locator_unavailable_reason": reason}
                _retain_evidence(p, {"kind": "shared_file", "path": pa_path, "other_path": pb_path, "bytes": pa[2], "line": None, "text": None, **source})

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
                _retain_evidence(p,
                        {"kind": "session", "path": None, "line": None, "text": f"session {s}: {len(by_repo[a])} commits in {a}, {len(by_repo[b])} in {b}",
                         "t": [min(by_repo[a] + by_repo[b]), max(by_repo[a] + by_repo[b])]}
                    )

    # structure: forks, second account, owner-stated lineage
    for n, r in repos.items():
        up = r.get("upstream")
        if up:
            p = pair(n, up["name"])
            p["kinds"]["fork_of"] += 1
            _retain_evidence(p, {"kind": "fork_of", "path": None, "line": None, "text": f"{n} shares {up['commits']} commits with {up['name']}"})
        if r.get("kind", "").startswith("fork") and not up:
            p = pair(n, n + " (upstream)")
            p["kinds"]["fork_of"] += 1
            _retain_evidence(p, {"kind": "fork_of", "path": None, "line": None, "text": f"{n} is a GitHub fork"})
        hs = [c for c in r.get("commits", []) if c.get("who") == "hagsmac"]
        if hs:
            p = pair("hagsmac (account)", n)
            p["kinds"]["second_account"] += len(hs)
            _retain_evidence(p, {"kind": "second_account", "path": None, "line": None, "text": f"{len(hs)} commits by hagsmac in {n}"})
    for n, r in observers.items():
        for target, why in (r.get("uses") or {}).items():
            p = pair(n, target)
            p["kinds"]["uses"] += 1
            _retain_evidence(p, {"kind": "uses", "path": None, "line": None, "text": f"declared by the family config: {why}"})
    lineage, resolved = resolve_lineage(cfg.get("lineage", []), set(repos), cfg.get("vocabulary") or {})
    note = "; ".join(f"'{a}' read as {b} through the vocabulary" for a, b in resolved.items())
    for a, b in zip(lineage, lineage[1:]):
        p = pair(a, b)
        p["kinds"]["lineage"] += 1
        _retain_evidence(p, {"kind": "lineage", "path": None, "line": None,
                              "text": cfg.get("lineage_source", "stated by the owner") + (f" ({note})" if note else "")})

    out = []
    for p in pairs.values():
        for evidence in p["evidence"]:
            evidence["observed_at"], evidence["valid_time"] = observed_at, None
            if p["from"] in private or p["to"] in private:
                evidence.pop("locator", None)
                evidence.pop("locators", None)
                evidence.pop("locator_unavailable_reason", None)
                evidence["locator_status"] = "withheld_private"
            elif "locator_status" not in evidence:
                evidence.update(locator_status="unavailable", locator_unavailable_reason="non_file_provenance")
        total = p.pop("_evidence_records")
        p["evidence_records"] = {"total": total, "returned": len(p["evidence"]), "omitted": total - len(p["evidence"]),
                                 "limit": MAX_EVIDENCE * 4, "per_kind_limit": MAX_EVIDENCE}
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
    return {"pairs": out, "scanned_files": dict(scanned), "observer": observer, "pin_rejections": dict(pin_rejections),
            "detectors": __doc__.strip().splitlines()[2:9]}


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
    # These complete detector summaries are independent of display sample size.
    mention_kinds = {kind.split(":", 1)[1] for kind, count in k.items() if count and kind.startswith("mention:")}
    mention_kinds.update(kind for kind, count in p.get("mention_lines", {}).items() if count)
    if mention_kinds & {"dependency", "ci", "url-code", "url-config"}:
        return "C", "named in a manifest, import, install line or CI workflow"
    if mention_kinds & {"code", "tests", "url-docs", "docs", "web", "config", "notebook"}:
        return "L", "named in code, tests or docs"
    if k.get("pin-weak") or mention_kinds:
        return "S", "only short hashes or mentions inside data files"
    return "S", "no strong detector fired"
