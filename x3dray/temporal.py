"""Explicit observation time and claimed valid time, without inferred dates.

Supported instants are timezone-qualified ISO datetimes with up to microsecond
precision. A valid interval is a bounded half-open [start, end) assertion; None
means unknown, not an interval extending forever. These helpers authenticate
neither the supplied timestamp nor the underlying relation claim.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

_INSTANT = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?(Z|[+-][0-9]{2}:[0-9]{2})$")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
TIME_MODES = frozenset({"observed_by", "valid_at"})
UNKNOWN_POLICIES = frozenset({"exclude", "include"})


def _instant(value) -> tuple[str, int]:
    if not isinstance(value, str) or not (match := _INSTANT.fullmatch(value)):
        raise ValueError("time: expected a timezone-qualified ISO datetime with at most six fractional digits")
    try:
        year, month, day, hour, minute, second = map(int, match.groups()[:6])
        fraction = int((match[7] or "").ljust(6, "0"))
        zone = match[8]
        offset = 0
        if zone != "Z":
            hours, minutes = int(zone[1:3]), int(zone[4:6])
            if hours > 14 or minutes > 59 or hours == 14 and minutes != 0:
                raise ValueError
            offset = (hours * 60 + minutes) * (1 if zone[0] == "+" else -1)
        date = datetime(year, month, day, hour, minute, second, fraction, tzinfo=timezone.utc) - timedelta(minutes=offset)
    except (ValueError, OverflowError):
        raise ValueError("time: invalid calendar datetime or UTC offset") from None
    stem = f"{date.year:04d}-{date.month:02d}-{date.day:02d}T{date.hour:02d}:{date.minute:02d}:{date.second:02d}"
    tail = ("." + f"{date.microsecond:06d}".rstrip("0")) if date.microsecond else ""
    delta = date - _EPOCH
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    return stem + tail + "Z", micros


def normalize_instant(value) -> str:
    return _instant(value)[0]


def instant_micros(value) -> int:
    return _instant(value)[1]


def normalize_valid_time(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise ValueError("time: valid_time must be unknown or an explicit start/end interval")
    start, a = _instant(value["start"])
    end, b = _instant(value["end"])
    if a >= b:
        raise ValueError("time: valid_time start must precede its exclusive end")
    return {"start": start, "end": end}


def normalize_time_filter(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not {"at", "time_mode"} <= set(value) or set(value) - {"at", "time_mode", "unknown_time"}:
        raise ValueError("time: filter requires at and time_mode, with optional unknown_time")
    mode, unknown = value["time_mode"], value.get("unknown_time", "exclude")
    if not isinstance(mode, str) or mode not in TIME_MODES or not isinstance(unknown, str) or unknown not in UNKNOWN_POLICIES:
        raise ValueError("time: unsupported temporal mode or unknown-time policy")
    return {"at": normalize_instant(value["at"]), "time_mode": mode, "unknown_time": unknown}


def filter_records(records: list[dict], time_filter: dict | None) -> tuple[list[dict], dict, dict]:
    """Filter candidate records and retain a basis-specific omission ledger.

    Validation runs even without an active temporal filter. Missing fields remain
    unknown for legacy records. Unknown-time inclusion is explicitly provisional.
    All valid_at hops are tested at the same instant; disjoint known intervals
    therefore cannot be joined into an allegedly co-valid path.
    """
    query = normalize_time_filter(time_filter)
    ledger = {"active": query is not None, "candidate_records": len(records), "eligible_records": 0,
              "known_records": 0, "unknown_records": 0, "included_known_records": 0,
              "included_unknown_records": 0, "excluded_known_records": 0, "excluded_unknown_records": 0,
              "observed_time_known_records": 0, "valid_time_known_records": 0}
    chosen, statuses = [], {}
    point = instant_micros(query["at"]) if query else None
    for record in records:
        observed = normalize_instant(record["observed_at"]) if record.get("observed_at") is not None else None
        valid = normalize_valid_time(record.get("valid_time"))
        ledger["observed_time_known_records"] += observed is not None
        ledger["valid_time_known_records"] += valid is not None
        if query is None:
            chosen.append(record)
            continue
        known = observed is not None if query["time_mode"] == "observed_by" else valid is not None
        ledger["known_records" if known else "unknown_records"] += 1
        if not known:
            include = query["unknown_time"] == "include"
        elif query["time_mode"] == "observed_by":
            include = instant_micros(observed) <= point
        else:
            include = instant_micros(valid["start"]) <= point < instant_micros(valid["end"])
        ledger[("included_" if include else "excluded_") + ("known_records" if known else "unknown_records")] += 1
        if include:
            chosen.append(record)
            statuses[record["id"]] = "known_time" if known else "provisional_unknown_time"
    ledger["eligible_records"] = len(chosen)
    ledger["scope"] = ("No temporal filter; observation and valid-time fields were not used to infer availability." if query is None
        else "Observation time <= at describes recorded visibility, not historical validity." if query["time_mode"] == "observed_by"
        else "Claimed start <= at < end; this does not impose an observation-time knowledge cutoff. Unknown inclusion is provisional.")
    return chosen, ledger, statuses
