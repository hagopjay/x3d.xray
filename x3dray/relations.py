"""Conservative, evidence-bound architecture relations and bounded traversal.

The old tie is an association between endpoints. This projection separates each
observed detector into a predicate without claiming more than that detector
established: a manifest/import/install-line *reference* is not automatically an
executable dependency. C/L/S grade the particular recorded observation; they are
neither calibrated probabilities nor a transitive path-confidence measure.

Private endpoints may retain identity and aggregate detector counts. Their
paths, hashes and source text are never exported by this projection. Graph
identity is over the public-safe projection, not hidden private source bytes.
"""
from __future__ import annotations

from collections import Counter, deque
import copy
import hashlib
import json
import re

from .structure import private_id_rx, private_rx

MAX_WITNESSES = 8
GRADES = frozenset({"C", "L", "S"})
PREDICATES = {
    "dependency_reference": {"directed": True, "definition": "A target name was found in a manifest, import, install or clone context; this alone does not establish an executable dependency."},
    "ci_reference": {"directed": True, "definition": "A target name was found in the source endpoint's CI workflow."},
    "declares_use": {"directed": True, "definition": "The family configuration explicitly declares that the source uses the target; execution is not independently verified."},
    "pins_revision": {"directed": True, "definition": "A validated target revision token occurs in source content under the pin detector's declared identity policy."},
    "hash_reference": {"directed": True, "definition": "A short target revision abbreviation appears without sufficient pin context."},
    "fork_of": {"directed": True, "definition": "Repository configuration or observed shared history records the source as a fork of the target."},
    "shares_blob": {"directed": False, "definition": "The endpoints contain identical observed blob content; copying direction and runtime use are not established."},
    "shared_session": {"directed": False, "definition": "A recorded session trailer occurs in commits at both endpoints; dataflow is not established."},
    "claims_lineage_from": {"directed": True, "definition": "The owner states the source descends conceptually from the target; this reverses the detector's predecessor-to-successor ordering and is not git ancestry."},
    "recorded_authorship": {"directed": True, "definition": "An explicitly identified account has recorded commits in the target repository."},
    "authorship_association": {"directed": False, "definition": "An authorship detector associated endpoints without an explicit account-to-repository orientation."},
    "mentions": {"directed": True, "definition": "Source content lexically names the target; no dependency or execution claim follows."},
    "curated_association": {"directed": False, "definition": "A curator's provided evidence associates the endpoints; free-prose direction is retained only as attribution."},
}

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SESSION = re.compile(r"claude\.ai/code/session_[A-Za-z0-9]+")
_SECRET = re.compile(r"AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY")


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode("ascii")).hexdigest()


def _identifier(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 256 and value.strip() == value and not any(ord(c) < 32 for c in value) and not any(rx.search(value) for rx in (_EMAIL, _SESSION, _SECRET))


def _safe_text(value, private_pattern) -> bool:
    return isinstance(value, str) and len(value) <= 4096 and not any(rx.search(value) for rx in (_EMAIL, _SESSION, _SECRET)) and not (private_pattern and private_pattern.search(value))


def _substantive(evidence) -> bool:
    if isinstance(evidence, str):
        return bool(evidence.strip())
    return isinstance(evidence, dict) and any(evidence.get(k) for k in ("path", "other_path", "text"))


def _safe_evidence(evidence, kind: str, private: bool, count: int, private_pattern) -> dict | None:
    if not _substantive(evidence):
        return None
    if private:
        # This witness attests only the supplied detector summary. It does not
        # reveal, hash or paraphrase the withheld source location or contents.
        return {"kind": kind, "visibility": "aggregate_only", "count": count,
                "scope": "Recorded detector count; source details withheld."}
    if isinstance(evidence, str):
        return {"kind": kind, "visibility": "public", "text": evidence} if _safe_text(evidence, private_pattern) else None
    out = {"kind": kind, "visibility": "public"}
    for key in ("path", "other_path", "text"):
        value = evidence.get(key)
        if value is not None:
            if not _safe_text(value, private_pattern) or value == "[private repo]":
                return None
            out[key] = value
    line = evidence.get("line")
    if line is not None:
        if type(line) is not int or line < 1:
            return None
        out["line"] = line
    size = evidence.get("bytes")
    if size is not None:
        if type(size) is not int or size < 0:
            return None
        out["bytes"] = size
    return out


def _classification(kind: str, source: str, target: str, repos: dict) -> tuple[str, str]:
    if kind == "pin":
        return "pins_revision", "C"
    if kind == "pin-weak":
        return "hash_reference", "S"
    if kind == "uses":
        return "declares_use", "C"
    if kind == "fork_of":
        return "fork_of", "C"
    if kind == "shared_file":
        return "shares_blob", "C"
    if kind == "session":
        return "shared_session", "C"
    if kind == "lineage":
        return "claims_lineage_from", "C"
    if kind == "second_account":
        return ("recorded_authorship", "C") if source.endswith(" (account)") and target in repos else ("authorship_association", "L")
    if kind == "mention:dependency":
        return "dependency_reference", "C"
    if kind == "mention:ci":
        return "ci_reference", "C"
    if kind.startswith("mention:"):
        subtype = kind.split(":", 1)[1]
        file_kinds = {"code", "tests", "docs", "web", "config", "notebook", "data", "x3d", "log", "other", "media", "ci"}
        if subtype not in file_kinds | {"url-" + k for k in file_kinds}:
            raise KeyError("unsupported mention detector")
        likely = {"code", "tests", "docs", "web", "config", "notebook", "url-code", "url-config", "url-docs", "url-web"}
        return "mentions", "L" if subtype in likely else "S"
    raise KeyError("unsupported detector")


def build_relations(dataset: dict, ties: dict, curated: dict | None = None) -> dict:
    """Project raw tie inputs into typed, separately graded relation records.

    `evidence_records.total` is the supplied detector's full observation count
    when available. The returned witnesses remain a bounded provided sample;
    missing representatives withhold that predicate rather than invent support.
    Omission diagnostics contain reason counts, never withheld source excerpts.
    """
    if not isinstance(dataset, dict) or not isinstance(dataset.get("repos"), list) or not isinstance(ties, dict) or not isinstance(ties.get("pairs", []), list):
        raise ValueError("relations: invalid dataset or ties shape")
    if curated is not None and (not isinstance(curated, dict) or not isinstance(curated.get("ties", []), list)):
        raise ValueError("relations: invalid curated shape")
    repos = {}
    for repo in dataset["repos"]:
        if not isinstance(repo, dict) or not _identifier(repo.get("name")) or repo["name"] in repos:
            raise ValueError("relations: invalid or duplicate repository identity")
        repos[repo["name"]] = repo
    private_names = {name for name, repo in repos.items() if repo.get("visibility") == "private"}
    private_pattern = private_rx(private_names)
    private_identifier = private_id_rx(private_names)
    nodes = {name: {"id": name, "name": name, "visibility": "private" if name in private_names else "public", "kind": "repo"} for name in repos}
    records, withheld = [], Counter()
    source_counts = Counter({"machine_pairs": 0, "machine_detector_assertions": 0, "curated_assertions": 0})
    seen_sources = set()

    def emit(source_type, source_key, a, b, kind, predicate, grade, supplied, count, annotation=None, source_endpoints=None):
        if not _identifier(a) or not _identifier(b) or a == b:
            withheld["invalid_or_self_endpoint"] += 1
            return
        if any(private_pattern and private_pattern.search(name) and name not in private_names for name in (a, b)):
            withheld["ambiguous_private_locator_endpoint"] += 1
            return
        is_private = a in private_names or b in private_names
        safe = []
        for raw in supplied:
            item = _safe_evidence(raw, kind, is_private, count, private_pattern)
            if item is not None:
                safe.append(item)
        # Deduplicate projection-equivalent witnesses before assigning IDs; a
        # private relation deliberately has one count-only representative.
        unique = {_canonical(e): e for e in safe}
        selected = [unique[key] for key in sorted(unique)[:MAX_WITNESSES]]
        if not selected:
            withheld["missing_safe_witness"] += 1
            return
        record_id = "relation:" + _digest([source_type, source_key, kind, a, b])
        for item in selected:
            item["id"] = "evidence:" + _digest([record_id, item])
        returned = len(selected)
        total = max(count, len(supplied), returned)
        record = {"id": record_id, "from": a, "to": b, "predicate": predicate,
                  "directed": PREDICATES[predicate]["directed"], "grade": grade,
                  "source": source_type, "source_key": source_key, "detector_kind": kind,
                  "evidence": selected, "evidence_records": {"total": total, "returned": returned, "omitted": total - returned,
                    "scope": "Provided detector observation count and bounded source-witness sample."},
                  "reason": PREDICATES[predicate]["definition"],
                  "method": {"id": "typed-tie-projection", "version": "1", "verification": "recorded_source_witness", "observation_count": count}}
        if source_endpoints is not None:
            record["source_endpoints"] = {"from": source_endpoints[0], "to": source_endpoints[1]}
        if annotation and not is_private and _safe_text(annotation, private_pattern):
            record["declared_direction"] = annotation
        records.append(record)
        for name in (a, b):
            nodes.setdefault(name, {"id": name, "name": name, "visibility": "external", "kind": "external"})

    for pair in ties.get("pairs", []):
        source_counts["machine_pairs"] += 1
        if not isinstance(pair, dict) or not isinstance(pair.get("kinds", {}), dict) or not isinstance(pair.get("evidence", []), list):
            raise ValueError("relations: malformed machine source")
        a, b = pair.get("from"), pair.get("to")
        if not _identifier(a) or not _identifier(b):
            n = sum(type(count) is int and count > 0 for count in pair.get("kinds", {}).values())
            source_counts["machine_detector_assertions"] += n
            withheld["invalid_or_self_endpoint"] += n
            continue
        source_key = "machine:" + _digest([a, b])
        if source_key in seen_sources:
            raise ValueError("relations: duplicate machine source pair")
        seen_sources.add(source_key)
        for kind, count in sorted(pair.get("kinds", {}).items()):
            if type(count) is not int or count < 0 or not isinstance(kind, str):
                raise ValueError("relations: invalid detector summary")
            if not count:
                continue
            source_counts["machine_detector_assertions"] += 1
            try:
                predicate, grade = _classification(kind, a if isinstance(a, str) else "", b, repos)
            except KeyError:
                withheld["unsupported_detector"] += 1
                continue
            evidence = [e for e in pair.get("evidence", []) if isinstance(e, dict) and e.get("kind") == kind]
            u, v = (b, a) if kind == "lineage" else (a, b)
            emit("machine", source_key, u, v, kind, predicate, grade, evidence, count, source_endpoints=(a, b))

    for item in (curated or {}).get("ties", []):
        source_counts["curated_assertions"] += 1
        if not isinstance(item, dict) or not isinstance(item.get("evidence", []), list):
            raise ValueError("relations: malformed curated source")
        if not _identifier(item.get("id")) or (private_identifier and private_identifier.match(item["id"])):
            withheld["invalid_curated_identity"] += 1
            continue
        source_key = "curated:" + item["id"]
        if source_key in seen_sources:
            raise ValueError("relations: duplicate curated identity")
        seen_sources.add(source_key)
        grade = item.get("grade")
        if grade not in GRADES:
            withheld["unknown_curated_grade"] += 1
            continue
        supplied = item.get("evidence", [])
        emit("curated", source_key, item.get("from"), item.get("to"), "curated", "curated_association", grade,
             supplied, len(supplied), item.get("direction"))

    records.sort(key=lambda r: (r["from"], r["to"], r["predicate"], r["detector_kind"], r["source"], r["id"]))
    node_list = [nodes[name] for name in sorted(nodes)]
    # Public tips and redacted commit metadata help distinguish source snapshots
    # without making a digest of withheld private paths, hashes or subjects.
    observations = []
    for name, repo in sorted(repos.items()):
        population = []
        commits = repo.get("commits", [])
        if not isinstance(commits, list):
            raise ValueError("relations: invalid commit population")
        for commit in commits:
            if not isinstance(commit, dict):
                raise ValueError("relations: invalid commit population")
            row = {}
            for key in ("t", "add", "del", "files"):
                if type(commit.get(key)) is int:
                    row[key] = commit[key]
            for key in ("merge", "default", "upstream"):
                if type(commit.get(key)) is bool:
                    row[key] = commit[key]
            if isinstance(commit.get("cls"), str) and commit["cls"] in {"you", "ai", "bot", "other", "upstream"}:
                row["cls"] = commit["cls"]
            if name not in private_names and commit.get("sha"):
                row["sha"] = commit["sha"]
            population.append(row)
        observations.append({"id": name, "head": repo.get("head") if name not in private_names else None,
                             "present": bool(repo.get("present", True)), "commits": sorted(population, key=_canonical)})
    coverage = {**dict(source_counts), "records": len(records), "nodes": len(node_list),
                "withheld_assertions": sum(withheld.values()), "withheld_by_reason": dict(sorted(withheld.items())),
                "private_relations": sum(any(nodes[n]["visibility"] == "private" for n in (r["from"], r["to"])) for r in records),
                "scope": "Provided source ties only; unobserved or withheld relations are not evidence of absence."}
    family = dataset.get("family")
    payload = {"schema_version": 1, "family": family if _identifier(family) and _safe_text(family, private_pattern) else None,
               "nodes": node_list, "records": records, "predicates": copy.deepcopy(PREDICATES), "coverage": coverage}
    payload["snapshot_digest"] = _digest({"projection": payload, "observations": observations})
    payload["identity_scope"] = "Canonical safe relation projection, public tips and public/redacted commit observations; not publisher authentication."
    return payload


def _filter_values(values, allowed: set, label: str) -> set:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [v.strip() for v in values.split(",") if v.strip()]
    try:
        found = set(values)
    except (TypeError, ValueError):
        raise ValueError(f"relations: invalid {label}") from None
    if any(not isinstance(v, str) or v not in allowed for v in found):
        raise ValueError(f"relations: unknown {label}")
    return found


def traverse(graph: dict, source: str, target: str | None = None, *, predicates=None, direction="outgoing", grades=None,
             max_hops=3, limit=12) -> dict:
    """Bounded exact shortest-node-path traversal over declared predicates.

    Parallel relation records do not multiply node paths. Directed edges follow
    `outgoing` or `incoming`; symmetric predicates follow either direction.
    `association` deliberately ignores direction and is labelled as that lens.
    Absence is only absence of a path in this supplied, filtered, bounded graph.
    """
    if not isinstance(graph, dict) or type(graph.get("schema_version")) is not int or graph["schema_version"] != 1 or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("records"), list) or not isinstance(graph.get("predicates"), dict):
        raise ValueError("relations: typed graph unavailable or unsupported")
    snapshot = graph.get("snapshot_digest")
    if not isinstance(snapshot, str) or not 0 < len(snapshot) <= 128:
        raise ValueError("relations: missing graph snapshot identity")
    if direction not in {"outgoing", "incoming", "association"} or type(max_hops) is not int or not 1 <= max_hops <= 6 or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("relations: invalid traversal scope")
    if len(graph["nodes"]) > 10_000 or len(graph["records"]) > 50_000:
        raise ValueError("relations: graph exceeds bounded traversal budget")
    if any(not isinstance(n, dict) or not _identifier(n.get("id")) for n in graph["nodes"]):
        raise ValueError("relations: invalid graph node")
    nodes = {node["id"] for node in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]):
        raise ValueError("relations: duplicate graph node identity")
    if not isinstance(source, str) or source not in nodes or (target is not None and (not isinstance(target, str) or target not in nodes)):
        raise ValueError("relations: unknown endpoint")
    wanted = _filter_values(predicates, set(graph.get("predicates", PREDICATES)), "predicate")
    grade_filter = _filter_values(grades, set(GRADES), "grade")
    adjacent = {name: {} for name in nodes}
    record_ids = set()
    for record in graph["records"]:
        if not isinstance(record, dict) or not _identifier(record.get("id")) or record["id"] in record_ids:
            raise ValueError("relations: invalid or duplicate relation identity")
        record_ids.add(record["id"])
        predicate = record.get("predicate")
        definition = graph["predicates"].get(predicate) if isinstance(predicate, str) else None
        if not isinstance(definition, dict) or type(definition.get("directed")) is not bool or type(record.get("directed")) is not bool or definition["directed"] != record["directed"] or not isinstance(record.get("grade"), str) or record["grade"] not in GRADES:
            raise ValueError("relations: invalid predicate, direction or grade")
        if not isinstance(record.get("evidence"), list) or not record["evidence"]:
            raise ValueError("relations: relation lacks provided evidence")
        a, b = record.get("from"), record.get("to")
        if not isinstance(a, str) or not isinstance(b, str) or a not in nodes or b not in nodes or a == b:
            raise ValueError("relations: invalid graph endpoint")
        if wanted and record["predicate"] not in wanted or grade_filter and record["grade"] not in grade_filter:
            continue
        directions = [(a, b), (b, a)] if direction == "association" or not record["directed"] else [(b, a)] if direction == "incoming" else [(a, b)]
        for u, v in directions:
            adjacent[u].setdefault(v, []).append(record)
    for nbs in adjacent.values():
        for records in nbs.values():
            records.sort(key=lambda r: r["id"])
    distances, previous, count = {source: 0}, {source: []}, {source: 1}
    q = deque([source])
    while q:
        u = q.popleft()
        if distances[u] == max_hops:
            continue
        for v in sorted(adjacent[u]):
            nd = distances[u] + 1
            if v not in distances:
                distances[v], previous[v], count[v] = nd, [], 0
                q.append(v)
            if distances[v] == nd:
                previous[v].append(u)
                count[v] += count[u]
    base = {"route": "architecture", "source": source, "target": target, "direction": direction,
            "predicates": sorted(wanted), "grades": sorted(grade_filter), "max_hops": max_hops,
            "snapshot_digest": graph.get("snapshot_digest"),
            "scope": "Recorded typed relations in this filter and hop bound; no causal or runtime-dependency inference."}
    if target is None:
        found = sorted((n for n in distances if n != source), key=lambda n: (distances[n], n))
        returned = found[:limit]
        kept = {source, *returned}
        reached = set(distances)
        all_induced = {r["id"]: r for u in reached for v, rs in adjacent[u].items() if v in reached for r in rs}
        induced = {r["id"]: r for u in kept for v, rs in adjacent[u].items() if v in kept for r in rs}
        rows = [induced[key] for key in sorted(induced)]
        return {**base, "nodes": [{"id": n, "hops": distances[n]} for n in returned],
                "total_nodes": len(found), "returned_nodes": len(returned), "omitted_nodes": len(found) - len(returned),
                "records": copy.deepcopy(rows[:100]), "total_records": len(all_induced), "returned_records": min(100, len(rows)), "omitted_records": len(all_induced) - min(100, len(rows)),
                "record_scope": "Eligible relation records with both endpoints reached within hop limit; returned records are induced by the source and returned neighbours."}
    total = count.get(target, 0)
    paths = []
    if target in distances:
        relevant, stack = {target}, [target]
        while stack:
            for pred in previous[stack.pop()]:
                if pred not in relevant:
                    relevant.add(pred)
                    stack.append(pred)
        successors = {n: [] for n in relevant}
        for n in relevant:
            for pred in previous[n]:
                if pred in relevant:
                    successors[pred].append(n)
        # At most six edges deep. Bounded enumeration follows only the shortest
        # predecessor DAG; exact path count was computed without enumerating it.
        def collect(v, chain):
            if len(paths) >= limit:
                return
            if v == target:
                hops = []
                for a, b in zip(chain, chain[1:]):
                    records = adjacent[a][b]
                    hops.append({"from": a, "to": b, "relations": copy.deepcopy(records[:4]),
                                 "total_relations": len(records), "returned_relations": min(4, len(records)), "omitted_relations": max(0, len(records) - 4)})
                paths.append({"nodes": chain, "hops": hops})
                return
            for child in sorted(successors[v]):
                collect(child, chain + [child])
        collect(source, [source])
    encode = lambda n: n if n <= 9_007_199_254_740_991 else str(n)
    result = {**base, "hops": distances.get(target), "total_paths": encode(total), "returned_paths": len(paths), "omitted_paths": encode(total - len(paths)), "paths": paths}
    if total > 9_007_199_254_740_991:
        result["count_encoding"] = "decimal_string"
    if not total:
        result["note"] = "No recorded path within the supplied predicates, grades, direction and hop bound."
    return result
