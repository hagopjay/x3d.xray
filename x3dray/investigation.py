"""Bounded, deterministic investigation intent logs with source/result binding.

Events select known records or request an experiment; they do not execute code,
fetch URLs, establish facts, or prove that a verifier has run. Actual experiment
results remain separately witnessed data. The hash chain detects accidental
alteration, not an author who can replace the log and recompute its hashes.

Catalog shape: exactly the lists ``entities``, ``relations``, ``mutations``,
``findings`` and ``predicates``. IDs are exact strings, never guessed or coerced.
Lens predicates form a set: canonical order is sorted; duplicates are refused.

Looks. ``take_look`` records that one look was inspected: a declared look of the
exploration ledger (by id), an ad hoc slice or row query, or a comparison of two
groups of commits (``compare``: two group filters in canonical order, a fork and a
measure), each by a closed selector that names no repo and carries no free text. It is accepted only when the caller
passes the third replay context, ``looks_context(section, repos)``: the declared
look ids and a digest of the stored ``eda.looks`` section; every event carries
that digest. A replay given the context reports ``state["looks"]``: ``declared``
(the denominator: how many looks the ledger declares), ``inspected`` (declared
ids in first-take order) and ``asked`` (distinct ad hoc selectors, slices, row queries and comparisons). A look is
recorded once; a repeat is refused, so the counts are event counts.

What the chain does NOT prove: that a look was computed, that its result was
shown or read, that the digest was the one displayed, who acted, or that nothing
else was looked at. It proves the order and integrity of what was recorded. The
counts are a lower bound: a look left out of the log, a log that stopped at its
256-event ceiling, or a replaced log cannot be told from a session that never
had it. A selector names a repo only by its position in ``bundle.repos``; the
digest pins that list, so a position cannot be silently re-read.
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

MAX_SAFE_INT = 2**53 - 1
LOOKS_DOMAIN = "x3dray.looks.ledger.v1"
MAX_LOOKS_BYTES = 1_048_576
MAX_LOOKS_DEPTH = 32
MAX_REPO_ORDINALS = 10_000
LOOK_KINDS = ("declared", "slice", "rows", "compare")
_TOKEN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
# One closed alphabet for an ad hoc look (a slice or a filtered row query). Every key is optional unless the kind
# requires it; a value is an integer in a range, a member of an enum, a date shape, a token over a fixed charset, or a
# strictly ascending list of those. There is no free text and no repo name. The JS twin holds the identical table
# (INVESTIGATION_LOOKS.selector) and a test compares them.
SELECTOR_RULES = {
    "repo": ["ordinals", 0, MAX_REPO_ORDINALS - 1, 32],
    "who": ["enum_list", ["you", "ai", "bot", "other", "upstream"]],
    "from": ["date"], "to": ["date"], "day": ["date"],
    "session": ["token", 64, _TOKEN + "._-"],
    "rule": ["token", 64, "abcdefghijklmnopqrstuvwxyz0123456789_"],
    "grade": ["enum", ["C", "L", "S"]],
    "hour": ["int", 0, 23], "weekday": ["int", 0, 6],
    "min_lines": ["int", 0, MAX_SAFE_INT], "max_lines": ["int", 0, MAX_SAFE_INT],
    "fork": ["enum", ["all", "no_merge", "default_branch", "no_mass"]],
    "measure": ["enum", ["lines", "files", "gap_s"]],
}
SELECTOR_KIND_KEYS = {"slice": {"required": ["fork", "measure"], "forbidden": []},
                      "rows": {"required": [], "forbidden": ["fork", "measure"]}}
ROWS_NEED_ONE_OF = ["hour", "weekday", "min_lines", "max_lines", "rule"]
# A comparison of two groups of commits (the eda `compare` topic): each group is a filter written with these keys, the pair is written in
# one canonical order (smaller canonical text first), and the measure may be any of the six that `compare` takes.
COMPARE_MEASURES = ["lines", "files", "gap_s", "hour4", "weekday", "repo"]
COMPARE_GROUP_KEYS = ["repo", "who", "from", "to", "day", "hour", "weekday", "min_lines", "max_lines"]

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


def _looks_ctx(context) -> dict | None:
    """The third replay context, exactly what ``looks_context`` returns; None means no looks context."""
    if context is None:
        return None
    _keys(context, {"looks", "digest"}, "invalid_looks_context")
    ids = context["looks"]
    if type(ids) is not list or len(ids) > MAX_CATALOG_IDS:
        _fail("invalid_looks_context")
    for ident in ids:
        _identifier(ident, "invalid_looks_context")
    if len(set(ids)) != len(ids):
        _fail("invalid_looks_context")
    return {"ids": frozenset(ids), "declared": len(ids), "digest": _digest(context["digest"], "invalid_looks_context")}


def _selector_value(rule: list, value) -> bool:
    kind = rule[0]
    if kind == "int":
        return type(value) is int and rule[1] <= value <= rule[2]
    if kind == "enum":
        return type(value) is str and value in rule[1]
    if kind == "date":
        return (type(value) is str and len(value) == 10 and value[4] == "-" and value[7] == "-"
                and all(c in "0123456789" for c in value[:4] + value[5:7] + value[8:]))
    if kind == "token":
        return type(value) is str and 1 <= len(value) <= rule[1] and all(c in rule[2] for c in value)
    if kind in ("ordinals", "enum_list"):
        if type(value) is not list or not 1 <= len(value) <= (rule[3] if kind == "ordinals" else len(rule[1])):
            return False
        if kind == "ordinals":
            ok = all(type(v) is int and rule[1] <= v <= rule[2] for v in value)
        else:
            ok = all(type(v) is str and v in rule[1] for v in value)
        return ok and all(a < b for a, b in zip(value, value[1:]))     # strictly ascending: sorted and distinct
    return False


def _compare_selector(selector) -> dict:
    """A comparison as a closed, name-free object: groups a and b (filters over the group keys), a fork and a measure. The groups are
    written smaller canonical text first, so (a, b) and (b, a) are one spelling; the same group twice is refused."""
    if type(selector) is not dict or set(selector) != {"a", "b", "fork", "measure"}:
        _fail("invalid_selector")
    if not _selector_value(SELECTOR_RULES["fork"], selector["fork"]) or type(selector["measure"]) is not str \
            or selector["measure"] not in COMPARE_MEASURES:
        _fail("invalid_selector")
    groups = []
    for name in ("a", "b"):
        group = selector[name]
        if type(group) is not dict or not group:
            _fail("invalid_selector")
        for key, value in group.items():
            if type(key) is not str or key not in COMPARE_GROUP_KEYS or not _selector_value(SELECTOR_RULES[key], value):
                _fail("invalid_selector")
        groups.append(group)
    if not _canonical(groups[0]) < _canonical(groups[1]):
        _fail("invalid_selector")
    return copy.deepcopy(selector)


def _selector(kind: str, selector) -> dict:
    """An ad hoc look as a closed, name-free object; anything outside the table is refused."""
    if type(selector) is not dict or not selector:
        _fail("invalid_selector")
    if kind == "compare":
        return _compare_selector(selector)
    spec = SELECTOR_KIND_KEYS[kind]
    for key, value in selector.items():
        rule = SELECTOR_RULES.get(key) if type(key) is str else None
        if rule is None or key in spec["forbidden"] or not _selector_value(rule, value):
            _fail("invalid_selector")
    if any(key not in selector for key in spec["required"]):
        _fail("invalid_selector")
    if "grade" in selector and "rule" not in selector:
        _fail("invalid_selector")
    if kind == "rows" and not any(key in selector for key in ROWS_NEED_ONE_OF):
        _fail("invalid_selector")
    return copy.deepcopy(selector)


def _event(event: dict, catalog: dict[str, frozenset[str]], looks: dict | None = None) -> dict:
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
    elif action == "take_look":
        if looks is None:
            _fail("looks_context_missing")
        kind = event.get("kind")
        if type(kind) is not str or kind not in LOOK_KINDS:
            _fail("invalid_look_kind")
        if kind == "declared":
            _keys(event, {"action", "kind", "id", "looks_digest"}, "invalid_event_shape")
            ident = _identifier(event["id"])
            if ident not in looks["ids"]:
                _fail("unknown_look")
            body = {"kind": kind, "id": ident}
        else:
            _keys(event, {"action", "kind", "selector", "looks_digest"}, "invalid_event_shape")
            body = {"kind": kind, "selector": _selector(kind, event["selector"])}
        if _digest(event["looks_digest"]) != looks["digest"]:
            _fail("looks_digest_mismatch")
        normalized = {"action": action, **body, "looks_digest": looks["digest"]}
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


def look_key(event: dict) -> str:
    """The one canonical text that says two look events are the same look (kind and id, or kind and selector)."""
    body = {"kind": event["kind"], "id": event["id"]} if event["kind"] == "declared" else {"kind": event["kind"], "selector": event["selector"]}
    return _canonical(body).decode("utf-8")


def _take(looks: dict, seen: set, event: dict) -> None:
    """Record one look. A repeat is refused, so the counts below are event counts."""
    key = look_key(event)
    if key in seen:
        _fail("duplicate_look")
    seen.add(key)
    if event["kind"] == "declared":
        looks["inspected"].append(event["id"])
    else:
        looks["asked"] += 1


def new_log(fixture_digest: str, analysis_digest: str) -> dict:
    """Create an empty log bound to exact source and analytical-result digests."""
    fixture_digest, analysis_digest = _digest(fixture_digest), _digest(analysis_digest)
    return {"schema_version": 1, "fixture_digest": fixture_digest, "analysis_digest": analysis_digest,
            "events": [], "head": _genesis(fixture_digest, analysis_digest)}


def _replay(log: dict, catalog: dict, expected_fixture_digest: str, expected_analysis_digest: str,
            expected_looks: dict | None) -> tuple[dict, set]:
    context = _looks_ctx(expected_looks)
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
    looks = None if context is None else {"looks_digest": context["digest"], "declared": context["declared"], "inspected": [], "asked": 0}
    seen: set = set()
    for number, record in enumerate(records, 1):
        _keys(record, {"seq", "previous", "event", "digest"}, "invalid_record_shape")
        if type(record["seq"]) is not int or record["seq"] != number:
            _fail("sequence_mismatch")
        if _digest(record["previous"]) != head:
            _fail("previous_digest_mismatch")
        normalized = _event(record["event"], known, context)
        if record["event"] != normalized:
            _fail("noncanonical_event")
        expected = _hash({"domain": "x3dray.investigation.event.v1", "seq": number, "previous": head, "event": normalized})
        if _digest(record["digest"]) != expected:
            _fail("event_digest_mismatch")
        _reduce(state, normalized)
        if normalized["action"] == "take_look":
            _take(looks, seen, normalized)
        head = expected
    if log["head"] != head:
        _fail("head_mismatch")
    if len(_canonical(log)) > MAX_LOG_BYTES:
        _fail("log_byte_limit")
    if looks is not None:
        state["looks"] = looks          # outside _view_state(): a reset cannot erase it, and a log without a looks context is unchanged
    state.update({"schema_version": 1, "fixture_digest": fixture, "analysis_digest": analysis,
                  "event_count": len(records), "event_head": head})
    state["state_digest"] = _hash(state)
    return state, seen


def replay(log: dict, catalog: dict, expected_fixture_digest: str, expected_analysis_digest: str,
           expected_looks: dict | None = None) -> dict:
    """Validate the entire chain/catalog/context, then reduce interaction intent.

    Returned state includes history identity, so a reset preserves the audit
    trail even when it restores the initial view. No factual result is executed
    or inferred. A hash chain is integrity bookkeeping, not authentication.
    ``expected_looks`` is the optional third context (what ``looks_context``
    returns): without it a log holding ``take_look`` is refused
    (``looks_context_missing``) and the state has no ``looks`` key, exactly as
    before.
    """
    return _replay(log, catalog, expected_fixture_digest, expected_analysis_digest, expected_looks)[0]


def append_event(log: dict, event: dict, catalog: dict, looks: dict | None = None) -> dict:
    """Return a new validated log; never mutate the caller's log or event.

    This validates integrity using the log's bound context. Call replay with the
    externally expected digests when accepting a log from another source.
    """
    _keys(log, LOG_KEYS, "invalid_log_shape")
    _, seen = _replay(log, catalog, log["fixture_digest"], log["analysis_digest"], looks)
    if len(log["events"]) >= MAX_EVENTS:
        _fail("event_count_limit")
    normalized = _event(event, _catalog(catalog), _looks_ctx(looks))
    if normalized["action"] == "take_look":
        _take({"inspected": [], "asked": 0}, seen, normalized)      # refuses a repeat before it is appended
    updated = copy.deepcopy(log)
    seq, previous = len(updated["events"]) + 1, updated["head"]
    digest = _hash({"domain": "x3dray.investigation.event.v1", "seq": seq, "previous": previous, "event": normalized})
    updated["events"].append({"seq": seq, "previous": previous, "event": normalized, "digest": digest})
    updated["head"] = digest
    if len(_canonical(updated)) > MAX_LOG_BYTES:
        _fail("log_byte_limit")
    return updated


def _exact(value, depth: int = 0) -> None:
    if depth > MAX_LOOKS_DEPTH:
        _fail("invalid_looks_section")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > MAX_SAFE_INT:
            _fail("invalid_looks_section")
    elif type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            _fail("invalid_looks_section")
    elif type(value) is list:
        for item in value:
            _exact(item, depth + 1)
    elif type(value) is dict:
        for key, item in value.items():
            _exact(key, depth + 1)
            if type(key) is not str:
                _fail("invalid_looks_section")
            _exact(item, depth + 1)
    else:
        _fail("invalid_looks_section")      # a float, NaN or any other type: the stored section is exact integers only


def looks_context(section: dict, repos: list) -> dict:
    """The ids and the digest of a stored ``eda.looks`` section: ``{"looks": [ids], "digest": hex64}``.

    ``looks`` goes into the catalog, ``digest`` is the third replay context. ``repos`` is the bundle's repo names in
    ``bundle.repos`` order: a selector names a repo only by its position in that list, so the digest pins what a position
    means (reorder or rename a repo and every recorded digest stops matching). The names go into the hash and nowhere else.
    The digest is sha256 over the canonical JSON (this module's dialect) of {domain, looks, repos}, so it pins every stored
    number and string the ledger shows. The section must hold exact integers only (floats are refused), one finding per
    declared look, distinct ids.
    """
    if type(section) is not dict or type(repos) is not list or not 1 <= len(repos) <= MAX_REPO_ORDINALS:
        _fail("invalid_looks_section")
    _exact(section)
    _exact(repos)
    if any(type(name) is not str or not 1 <= len(name) <= 1024 for name in repos) or len(set(repos)) != len(repos):
        _fail("invalid_looks_section")
    findings, ledger = section.get("findings"), section.get("ledger")
    if type(findings) is not list or type(ledger) is not dict or len(findings) > MAX_CATALOG_IDS:
        _fail("invalid_looks_section")
    ids = []
    for finding in findings:
        if type(finding) is not dict:
            _fail("invalid_looks_section")
        ids.append(_identifier(finding.get("id"), "invalid_looks_section"))
    if len(ids) != len(set(ids)) or type(ledger.get("declared")) is not int or ledger["declared"] != len(ids):
        _fail("invalid_looks_section")
    if len(_canonical(section)) > MAX_LOOKS_BYTES:
        _fail("looks_section_too_large")
    return {"looks": ids, "digest": _hash({"domain": LOOKS_DOMAIN, "looks": section, "repos": repos})}
