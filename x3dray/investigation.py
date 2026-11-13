"""Bounded, deterministic investigation intent logs with source/result binding.

Events select known records or request an experiment; they do not execute code,
fetch URLs, establish facts, or prove that a verifier has run. Actual experiment
results remain separately witnessed data. The hash chain detects accidental
alteration, not an author who can replace the log and recompute its hashes.

Catalog shape: exactly the lists ``entities``, ``relations``, ``mutations``,
``findings`` and ``predicates``. IDs are exact strings, never guessed or coerced.
Lens predicates form a set: canonical order is sorted; duplicates are refused.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

MAX_EVENTS = 256
MAX_ID_CHARS = 256
MAX_PREDICATES = 32
MAX_CATALOG_IDS = 10_000
MAX_EVENT_BYTES = 16_384
MAX_LOG_BYTES = 262_144
CATALOG_KEYS = frozenset({"entities", "relations", "mutations", "findings", "predicates"})
LOG_KEYS = frozenset({"schema_version", "fixture_digest", "analysis_digest", "events", "head"})
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
DEFAULT_LENS = {"predicates": [], "direction": "outgoing", "max_hops": 3, "grade": "all"}


class InvestigationError(ValueError):
    """A stable bounded validation code; rejected source fragments are not echoed."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str):
    raise InvestigationError(code)


def _keys(value, required, code):
    if type(value) is not dict or set(value) != set(required):
        _fail(code)


def _identifier(value, code="invalid_identifier") -> str:
    if type(value) is not str or not 1 <= len(value) <= MAX_ID_CHARS:
        _fail(code)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        _fail(code)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _digest(value, code="invalid_digest") -> str:
    if type(value) is not str or DIGEST.fullmatch(value) is None:
        _fail(code)
    return value


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _genesis(fixture_digest: str, analysis_digest: str) -> str:
    return _hash({"domain": "x3dray.investigation.v1", "fixture_digest": fixture_digest, "analysis_digest": analysis_digest})


def _catalog(catalog: dict) -> dict[str, frozenset[str]]:
    _keys(catalog, CATALOG_KEYS, "invalid_catalog_shape")
    result = {}
    for category in sorted(CATALOG_KEYS):
        values = catalog[category]
        if type(values) is not list or len(values) > MAX_CATALOG_IDS:
            _fail("invalid_catalog_ids")
        for value in values:
            _identifier(value, "invalid_catalog_id")
        if len(values) != len(set(values)):
            _fail("duplicate_catalog_id")
        result[category] = frozenset(values)
    return result


def _event(event: dict, catalog: dict[str, frozenset[str]]) -> dict:
    if type(event) is not dict or type(event.get("action")) is not str:
        _fail("invalid_event_shape")
    action = event["action"]
    selections = {"select_entity": "entities", "select_relation": "relations", "run_mutation": "mutations", "inspect_finding": "findings"}
    if action in selections:
        _keys(event, {"action", "id"}, "invalid_event_shape")
        ident = _identifier(event["id"])
        if ident not in catalog[selections[action]]:
            singular = {"entities": "entity", "relations": "relation", "mutations": "mutation", "findings": "finding"}
            _fail("unknown_" + singular[selections[action]])
        normalized = {"action": action, "id": ident}
    elif action == "set_query":
        _keys(event, {"action", "query"}, "invalid_event_shape")
        query = event["query"]
        _keys(query, {"from_entity", "to_entity"}, "invalid_query_shape")
        endpoints = {}
        for key in ("from_entity", "to_entity"):
            ident = _identifier(query[key])
            if ident not in catalog["entities"]:
                _fail("unknown_entity")
            endpoints[key] = ident
        # Equal endpoints intentionally describe a zero-hop query.
        normalized = {"action": action, "query": endpoints}
    elif action == "set_time":
        _keys(event, {"action", "time"}, "invalid_event_shape")
        from .temporal import normalize_time_filter
        try:
            time = normalize_time_filter(event["time"])
        except (ValueError, TypeError, OverflowError):
            _fail("invalid_time_filter")
        normalized = {"action": action, "time": copy.deepcopy(time)}
    elif action == "set_lens":
        _keys(event, {"action", "lens"}, "invalid_event_shape")
        lens = event["lens"]
        _keys(lens, {"predicates", "direction", "max_hops", "grade"}, "invalid_lens_shape")
        predicates = lens["predicates"]
        if type(predicates) is not list or len(predicates) > MAX_PREDICATES:
            _fail("invalid_predicates")
        for predicate in predicates:
            _identifier(predicate, "invalid_predicate")
            if predicate not in catalog["predicates"]:
                _fail("unknown_predicate")
        if len(predicates) != len(set(predicates)):
            _fail("duplicate_predicate")
        if type(lens["direction"]) is not str or lens["direction"] not in {"outgoing", "incoming", "association"}:
            _fail("invalid_direction")
        if type(lens["max_hops"]) is not int or not 1 <= lens["max_hops"] <= 6:
            _fail("invalid_max_hops")
        if type(lens["grade"]) is not str or lens["grade"] not in {"C", "L", "S", "all"}:
            _fail("invalid_grade")
        normalized = {"action": action, "lens": {"predicates": sorted(predicates), "direction": lens["direction"],
                                                "max_hops": lens["max_hops"], "grade": lens["grade"]}}
    elif action == "reset":
        _keys(event, {"action"}, "invalid_event_shape")
        normalized = {"action": action}
    else:
        _fail("unknown_action")
    if len(_canonical(normalized)) > MAX_EVENT_BYTES:
        _fail("event_byte_limit")
    return normalized


def _view_state() -> dict:
    return {"selected_entity": None, "selected_relation": None, "query": None, "time": None, "lens": copy.deepcopy(DEFAULT_LENS),
            "requested_mutation": None, "selected_finding": None}


def _reduce(state: dict, event: dict) -> None:
    action = event["action"]
    if action == "select_entity":
        state["selected_entity"], state["selected_relation"] = event["id"], None
    elif action == "select_relation":
        state["selected_relation"], state["selected_entity"] = event["id"], None
    elif action == "set_query":
        state["query"] = copy.deepcopy(event["query"])
    elif action == "set_time":
        state["time"] = copy.deepcopy(event["time"])
    elif action == "set_lens":
        state["lens"] = copy.deepcopy(event["lens"])
        state["requested_mutation"], state["selected_finding"] = None, None
    elif action == "run_mutation":
        state["requested_mutation"], state["selected_finding"] = event["id"], None
    elif action == "inspect_finding":
        state["selected_finding"] = event["id"]
    elif action == "reset":
        state.update(_view_state())


def new_log(fixture_digest: str, analysis_digest: str) -> dict:
    """Create an empty log bound to exact source and analytical-result digests."""
    fixture_digest, analysis_digest = _digest(fixture_digest), _digest(analysis_digest)
    return {"schema_version": 1, "fixture_digest": fixture_digest, "analysis_digest": analysis_digest,
            "events": [], "head": _genesis(fixture_digest, analysis_digest)}


def replay(log: dict, catalog: dict, expected_fixture_digest: str, expected_analysis_digest: str) -> dict:
    """Validate the entire chain/catalog/context, then reduce interaction intent.

    Returned state includes history identity, so a reset preserves the audit
    trail even when it restores the initial view. No factual result is executed
    or inferred. A hash chain is integrity bookkeeping, not authentication.
    """
    known = _catalog(catalog)
    _keys(log, LOG_KEYS, "invalid_log_shape")
    if type(log["schema_version"]) is not int or log["schema_version"] != 1:
        _fail("unsupported_schema")
    fixture, analysis = _digest(log["fixture_digest"]), _digest(log["analysis_digest"])
    if fixture != _digest(expected_fixture_digest) or analysis != _digest(expected_analysis_digest):
        _fail("context_mismatch")
    _digest(log["head"])
    records = log["events"]
    if type(records) is not list or len(records) > MAX_EVENTS:
        _fail("event_count_limit")
    state, head = _view_state(), _genesis(fixture, analysis)
    for number, record in enumerate(records, 1):
        _keys(record, {"seq", "previous", "event", "digest"}, "invalid_record_shape")
        if type(record["seq"]) is not int or record["seq"] != number:
            _fail("sequence_mismatch")
        if _digest(record["previous"]) != head:
            _fail("previous_digest_mismatch")
        normalized = _event(record["event"], known)
        if record["event"] != normalized:
            _fail("noncanonical_event")
        expected = _hash({"domain": "x3dray.investigation.event.v1", "seq": number, "previous": head, "event": normalized})
        if _digest(record["digest"]) != expected:
            _fail("event_digest_mismatch")
        _reduce(state, normalized)
        head = expected
    if log["head"] != head:
        _fail("head_mismatch")
    if len(_canonical(log)) > MAX_LOG_BYTES:
        _fail("log_byte_limit")
    state.update({"schema_version": 1, "fixture_digest": fixture, "analysis_digest": analysis,
                  "event_count": len(records), "event_head": head})
    state["state_digest"] = _hash(state)
    return state


def append_event(log: dict, event: dict, catalog: dict) -> dict:
    """Return a new validated log; never mutate the caller's log or event.

    This validates integrity using the log's bound context. Call replay with the
    externally expected digests when accepting a log from another source.
    """
    _keys(log, LOG_KEYS, "invalid_log_shape")
    replay(log, catalog, log["fixture_digest"], log["analysis_digest"])
    if len(log["events"]) >= MAX_EVENTS:
        _fail("event_count_limit")
    normalized = _event(event, _catalog(catalog))
    updated = copy.deepcopy(log)
    seq, previous = len(updated["events"]) + 1, updated["head"]
    digest = _hash({"domain": "x3dray.investigation.event.v1", "seq": seq, "previous": previous, "event": normalized})
    updated["events"].append({"seq": seq, "previous": previous, "event": normalized, "digest": digest})
    updated["head"] = digest
    if len(_canonical(updated)) > MAX_LOG_BYTES:
        _fail("log_byte_limit")
    return updated
