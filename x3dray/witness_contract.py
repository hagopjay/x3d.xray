"""Versioned bounded source projection used by the scene producer and page.

The independent scene reader implements this contract separately. This digest
binds a public four-witness sample, or private aggregate counts, to the scene;
it does not authenticate the source. Immutable locator verification is separate.
"""
from __future__ import annotations

import hashlib
import json

FIELDS = ("kind", "path", "line", "text", "other_path", "locator", "locators", "observed_at", "valid_time")
_FILE_KINDS = {"code", "tests", "docs", "web", "config", "notebook", "data", "x3d", "log", "other", "media", "ci"}
_PUBLIC_DETECTORS = {"pin", "pin-weak", "uses", "fork_of", "shared_file", "session", "lineage", "second_account"} | {"mention:"+k for k in _FILE_KINDS} | {"mention:url-"+k for k in _FILE_KINDS}


def _qualifies(value, source):
    if isinstance(value, str):
        return source == "curated" and bool(value.strip())
    return isinstance(value, dict) and any(isinstance(value.get(k), str) and value[k].strip() for k in ("path", "text"))


def project_evidence(record, source):
    evidence = record.get("evidence", [])
    if not isinstance(evidence, list):
        return []
    result = []
    for raw in evidence:
        if not _qualifies(raw, source):
            continue
        if isinstance(raw, str):
            result.append(raw[:160])
        else:
            item = {}
            for key in FIELDS:
                value = raw.get(key)
                if value is None:
                    continue
                if key == "locator":
                    from .locators import normalize_source_locator
                    value = normalize_source_locator(value)
                    if value is None:
                        continue
                elif key == "locators":
                    from .locators import normalize_source_locator
                    value = [v for v in (normalize_source_locator(v) for v in value) if v is not None] if isinstance(value, list) else []
                    if not value:
                        continue
                item[key] = value[:160] if isinstance(value, str) else value
            result.append(item)
        if len(result) == 4:
            break
    return result


def evidence_count(record):
    evidence = record.get("evidence", [])
    count = record.get("evidence_records", {}).get("total") if isinstance(record.get("evidence_records", {}), dict) else None
    if type(count) is not int or count < 0:
        count = 0
    return max(count, len(evidence) if isinstance(evidence, list) else 0)


def source_payload(record, source, private_names=()):
    private = record.get("from") in private_names or record.get("to") in private_names
    count = evidence_count(record)
    kinds = ({k: v for k, v in sorted(record.get("kinds", {}).items()) if type(v) is int and v > 0}
             if source == "machine" else {"curated_kind": record.get("kind", "")})
    if private:
        if source == "curated":
            kinds = {"curated_kind": "aggregate"}
        else:
            other = sum(v for k, v in kinds.items() if k not in _PUBLIC_DETECTORS)
            kinds = {k: v for k, v in kinds.items() if k in _PUBLIC_DETECTORS}
            if other:
                kinds["other"] = other
    return {"schema_version": 1, "source": source, "from": record.get("from"), "to": record.get("to"), "grade": record.get("grade"),
            "kinds": kinds, "visibility": "aggregate_only" if private else "public", "evidence_count": count,
            "evidence": [{"count": count}] if private else project_evidence(record, source)}


def source_digest(record, source, private_names=()):
    payload = json.dumps(source_payload(record, source, private_names), ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def scene_sources(dataset, ties, curated):
    """Complete source-membership ledger, including explicit ineligible inputs.

    It uses the same bounded public sample as the displayed witness digest.
    Private endpoint records retain count-only witness placeholders.
    """
    private = {r["name"] for r in dataset["repos"] if r.get("visibility") == "private"}
    result = {"machine": [], "curated": []}
    for source, rows in (("machine", ties.get("pairs", [])), ("curated", (curated or {}).get("ties", []))):
        for row in rows:
            item = {k: row[k] for k in ("id", "from", "to", "grade", "kind", "kinds") if k in row}
            item["evidence"] = project_evidence(row, source)
            total = evidence_count(row)
            item["evidence_records"] = {"total": total}
            if row.get("from") in private or row.get("to") in private:
                item["evidence"] = ([{"path": "[private repo]"}] if source == "machine" else ["[private repo]"]) if item["evidence"] else []
                if source == "machine":
                    item["kinds"] = source_payload(row, source, private)["kinds"]
                else:
                    item["kind"] = "aggregate"
            result[source].append(item)
    return result
