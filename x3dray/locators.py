"""Immutable public Git blob witnesses and independent local verification.

    locator = make_locator(checkout, "repo:alpha", full_commit_oid, "src/a.py",
                           line_start=3, line_end=5, detector_id="mention:code")
    verify_locator(checkout, locator, repo_id="repo:alpha")

Line spans are one-based inclusive and split on LF; CRLF bytes remain intact.
Byte spans are zero-based half-open and support binary/non-UTF-8 content. Raw
content SHA-256 is independent of Git's blob-object SHA-1/SHA-256 algorithm.
Canonical repository-to-checkout binding is supplied explicitly by the caller;
verification does not authenticate a remote owner or contact any network host.
Observation time and validity intervals belong to the observation, not this
immutable locator. Non-file/configuration assertions have no Git locator.
"""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
import re
import subprocess

from . import gitio

MAX_BYTES = 8 * 1024 * 1024
MAX_TREE_DEPTH = 64
MAX_TREE_ENTRIES = 100_000
MAX_VERIFIED_BYTES = 32 * 1024 * 1024
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|claude\.ai/code/session_[A-Za-z0-9]+|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY")


def _utf8(value) -> bool:
    try:
        value.encode("utf-8", errors="strict")
    except (AttributeError, UnicodeError):
        return False
    return True


def valid_repo_id(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 512 and ":" in value and value.strip() == value and not any(ord(c) < 32 for c in value) and _utf8(value) and not _SENSITIVE.search(value)


def _oid(value, object_format) -> bool:
    return isinstance(value, str) and object_format in {"sha1", "sha256"} and len(value) == (40 if object_format == "sha1" else 64) and bool(_HEX.fullmatch(value))


def _path(value) -> bool:
    if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value:
        return False
    p = PurePosixPath(value)
    if not p.parts or p.is_absolute() or ".." in p.parts or p.as_posix() != value or len(p.parts) > MAX_TREE_DEPTH:
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return not _SENSITIVE.search(value)


def _name(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128 and value.strip() == value and not any(ord(c) < 32 for c in value) and _utf8(value) and not _SENSITIVE.search(value)


def normalize_source_locator(value) -> dict | None:
    """Return only safe, supported public fields; this is shape validation.

    A successfully normalized locator has not thereby been locally verified.
    Invalid or private values return None without source details in an error.
    """
    try:
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1 or value.get("kind") != "git_blob" or value.get("visibility") != "public" or not valid_repo_id(value.get("repo_id")) or not _path(value.get("path")):
            return None
        revision, blob, span, detector = (value[k] for k in ("revision", "blob", "span", "detector"))
        if not all(isinstance(v, dict) for v in (revision, blob, span, detector)):
            return None
        fmt = revision.get("object_format")
        if not _oid(revision.get("oid"), fmt) or blob.get("object_format") != fmt or not _oid(blob.get("oid"), fmt):
            return None
        size = blob.get("size_bytes")
        if type(size) is not int or size < 0 or size > MAX_BYTES or not isinstance(blob.get("content_sha256"), str) or not _SHA256.fullmatch(blob["content_sha256"]) or blob.get("mode") not in {"100644", "100755", "120000"}:
            return None
        start, end, bs, be = (span[k] for k in ("start", "end", "byte_start", "byte_end"))
        if not all(type(v) is int for v in (start, end, bs, be)) or not 0 <= bs <= be <= size or not isinstance(span.get("content_sha256"), str) or not _SHA256.fullmatch(span["content_sha256"]):
            return None
        if span.get("kind") == "lines":
            # Every nonempty LF-delimited line consumes at least one byte.
            # Bounding here also prevents enormous integers entering JSON.
            if not 1 <= start <= end <= size or not bs < be or span.get("encoding") != "utf-8":
                return None
        elif span.get("kind") == "bytes":
            if (start, end) != (bs, be) or span.get("encoding") != "bytes":
                return None
        else:
            return None
        if not _name(detector.get("id")) or not _name(detector.get("version")):
            return None
        return {"schema_version": 1, "kind": "git_blob", "visibility": "public", "repo_id": value["repo_id"],
                "revision": {"oid": revision["oid"].lower(), "object_format": fmt},
                "blob": {"oid": blob["oid"].lower(), "object_format": fmt, "mode": blob["mode"], "size_bytes": size, "content_sha256": blob["content_sha256"]},
                "path": value["path"], "span": {"kind": span["kind"], "encoding": span["encoding"], "start": start, "end": end,
                    "byte_start": bs, "byte_end": be, "content_sha256": span["content_sha256"]},
                "detector": {"id": detector["id"], "version": detector["version"]}}
    except (KeyError, TypeError, ValueError):
        return None


def _line_bounds(content: bytes, start: int, end: int) -> tuple[int, int]:
    if type(start) is not int or type(end) is not int or not 1 <= start <= end:
        raise ValueError("invalid line span")
    # These bytes must actually be UTF-8 text; replacement decoding would move
    # witness meaning without proving the original source representation.
    content.decode("utf-8", errors="strict")
    if b"\0" in content:
        raise ValueError("binary content has no UTF-8 line locator")
    if not content or end > len(content):
        raise ValueError("line span exceeds source")
    # Locate only requested offsets; an all-newline blob must not allocate an
    # integer object for each byte just to prove one bounded line span.
    line, cursor, bs = 1, 0, 0 if start == 1 else None
    while True:
        newline = content.find(b"\n", cursor)
        next_start = len(content) if newline < 0 else newline + 1
        if line == end:
            return bs, next_start
        if next_start >= len(content):
            raise ValueError("line span exceeds source")
        line, cursor = line + 1, next_start
        if line == start:
            bs = next_start


def locator_from_blob(repo_id: str, revision_oid: str, object_format: str, blob_oid: str, path: str, content: bytes, *,
                      line_start: int | None = None, line_end: int | None = None, byte_start: int | None = None,
                      byte_end: int | None = None, detector_id: str, detector_version="1", mode="100644", visibility="public") -> dict:
    """Record an already harvested tree/blob binding, checking its content OID.

    The caller supplies the immutable tree binding. verify_locator independently
    rechecks that binding against local Git rather than trusting this producer.
    """
    if visibility != "public" or not isinstance(content, bytes) or len(content) > MAX_BYTES or not _oid(blob_oid, object_format):
        raise ValueError("unsupported public source blob")
    object_bytes = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    if hashlib.new(object_format, object_bytes).hexdigest() != blob_oid.lower():
        raise ValueError("source bytes do not match blob identity")
    line_mode = line_start is not None or line_end is not None
    if line_mode:
        if byte_start is not None or byte_end is not None:
            raise ValueError("ambiguous span kind")
        start, end = line_start, line_end if line_end is not None else line_start
        try:
            bs, be = _line_bounds(content, start, end)
        except UnicodeError:
            raise ValueError("source is not UTF-8 text") from None
        span_kind, encoding = "lines", "utf-8"
    else:
        start = 0 if byte_start is None else byte_start
        end = len(content) if byte_end is None else byte_end
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(content):
            raise ValueError("invalid byte span")
        bs, be, span_kind, encoding = start, end, "bytes", "bytes"
    value = {"schema_version": 1, "kind": "git_blob", "visibility": "public", "repo_id": repo_id,
             "revision": {"oid": revision_oid, "object_format": object_format},
             "blob": {"oid": blob_oid, "object_format": object_format, "mode": mode, "size_bytes": len(content), "content_sha256": hashlib.sha256(content).hexdigest()},
             "path": path, "span": {"kind": span_kind, "encoding": encoding, "start": start, "end": end,
                 "byte_start": bs, "byte_end": be, "content_sha256": hashlib.sha256(content[bs:be]).hexdigest()},
             "detector": {"id": detector_id, "version": detector_version}}
    normalized = normalize_source_locator(value)
    if normalized is None:
        raise ValueError("invalid immutable source locator")
    return normalized


def _read_object(repo: str, fmt: str, oid: str, kind: str, bound: int) -> bytes:
    if not _oid(oid, fmt) or gitio.local_git_bytes(repo, "cat-file", "-t", oid).strip() != kind.encode("ascii"):
        raise ValueError("unexpected Git object type")
    raw_size = gitio.local_git_bytes(repo, "cat-file", "-s", oid).strip()
    if not raw_size.isdigit() or int(raw_size) > bound:
        raise ValueError("Git object exceeds verification bound")
    content = gitio.local_git_bytes(repo, "cat-file", kind, oid)
    if len(content) != int(raw_size) or hashlib.new(fmt, kind.encode("ascii") + b" " + raw_size + b"\0" + content).hexdigest() != oid.lower():
        raise ValueError("Git object bytes do not match identity")
    return content


def _tree_entries(content: bytes, fmt: str):
    cursor, raw_oid_size = 0, 20 if fmt == "sha1" else 32
    previous, names = None, set()
    while cursor < len(content):
        space = content.find(b" ", cursor)
        zero = content.find(b"\0", space + 1)
        if space < cursor or zero <= space + 1 or zero + 1 + raw_oid_size > len(content):
            raise ValueError("invalid Git tree bytes")
        mode, name = content[cursor:space], content[space + 1:zero]
        if mode not in {b"40000", b"100644", b"100755", b"120000", b"160000"} or b"/" in name or name in {b".", b".."} or name in names:
            raise ValueError("invalid Git tree entry")
        # Git sorts directories as if their names end in '/', not NUL. A
        # hash-valid but malformed unsorted tree can disagree with Git's own
        # commit:path resolver; such a tree is unsupported source evidence.
        key = name + (b"/" if mode == b"40000" else b"")
        if previous is not None and key <= previous or len(names) >= MAX_TREE_ENTRIES:
            raise ValueError("noncanonical or oversized Git tree")
        previous = key
        names.add(name)
        yield mode.decode("ascii"), name, content[zero + 1:zero + 1 + raw_oid_size].hex()
        cursor = zero + 1 + raw_oid_size


def _read_source(repo: str, revision_oid: str, path: str) -> tuple[str, str, str, bytes]:
    if not _path(path):
        raise ValueError("unsupported Git path")
    fmt = gitio.local_git_bytes(repo, "rev-parse", "--show-object-format").decode("ascii").strip()
    if not _oid(revision_oid, fmt):
        raise ValueError("a full immutable revision OID is required")
    commit = _read_object(repo, fmt, revision_oid, "commit", 2 * 1024 * 1024)
    first = commit.split(b"\n", 1)[0]
    if not first.startswith(b"tree "):
        raise ValueError("commit has no tree header")
    current = first[5:].decode("ascii")
    parts = path.encode("utf-8").split(b"/")
    verified_bytes = len(commit)
    for i, part in enumerate(parts):
        tree = _read_object(repo, fmt, current, "tree", MAX_BYTES)
        verified_bytes += len(tree)
        if verified_bytes > MAX_VERIFIED_BYTES:
            raise ValueError("source tree traversal exceeds verification bound")
        found = [(mode, oid) for mode, name, oid in _tree_entries(tree, fmt) if name == part]
        if len(found) != 1:
            raise ValueError("revision path does not identify one tree entry")
        mode, current = found[0]
        if i < len(parts) - 1:
            if mode not in {"40000", "040000"}:
                raise ValueError("path traverses a non-tree object")
        elif mode not in {"100644", "100755", "120000"}:
            raise ValueError("path is not a supported Git blob")
    content = _read_object(repo, fmt, current, "blob", MAX_BYTES)
    return fmt, mode, current, content


def make_locator(repo: str, repo_id: str, revision_oid: str, path: str, **span_and_detector) -> dict:
    """Read one exact commit:path locally, then record its immutable locator."""
    try:
        fmt, mode, blob_oid, content = _read_source(repo, revision_oid, path)
    except (RuntimeError, OSError, subprocess.SubprocessError, UnicodeError):
        raise ValueError("immutable local source is unavailable") from None
    return locator_from_blob(repo_id, revision_oid, fmt, blob_oid, path, content, mode=mode, **span_and_detector)


def verify_locator(repo: str, locator, *, repo_id: str | None = None) -> dict:
    """Independently re-read a locator's commit:path using offline Git plumbing.

    The expected canonical repo_id must be supplied from a trusted checkout map.
    A clone/fork sharing objects does not prove remote ownership. Findings are
    stable codes without paths, source excerpts, OIDs or arbitrary exception text.
    """
    normalized = normalize_source_locator(locator)
    result = {"ok": False, "status": "fail", "findings": [], "checks": [],
              "scope": "Caller-bound local repository, exact commit:path/blob bytes and span; no remote authentication or detector truth claim."}
    if normalized is None:
        result["findings"] = ["invalid_locator"]
        return result
    if not valid_repo_id(repo_id) or normalized["repo_id"] != repo_id:
        result["findings"] = ["repository_identity_unverified"]
        return result
    result["checks"].append("caller_repository_binding")
    try:
        fmt, mode, actual_blob, content = _read_source(repo, normalized["revision"]["oid"], normalized["path"])
    except ValueError:
        result["findings"] = ["revision_path_or_bound_mismatch"]
        return result
    except (RuntimeError, OSError, subprocess.SubprocessError, UnicodeError):
        result["status"], result["findings"] = "unavailable", ["local_objects_or_git_capability_unavailable"]
        return result
    result["checks"].extend(["replacement_and_lazy_fetch_disabled", "commit_and_tree_object_hashes", "immutable_commit_path_read"])
    try:
        span = normalized["span"]
        kwargs = {"line_start": span["start"], "line_end": span["end"]} if span["kind"] == "lines" else {"byte_start": span["start"], "byte_end": span["end"]}
        expected = locator_from_blob(repo_id, normalized["revision"]["oid"], fmt, actual_blob, normalized["path"], content,
            detector_id=normalized["detector"]["id"], detector_version=normalized["detector"]["version"], mode=mode, **kwargs)
    except (ValueError, UnicodeError):
        result["findings"] = ["source_span_invalid"]
        return result
    if expected != normalized:
        result["findings"] = ["blob_content_or_span_mismatch"]
        return result
    result.update(ok=True, status="pass")
    result["checks"].extend(["git_object_format_and_blob_identity", "raw_content_sha256", "span_bounds_and_sha256"])
    return result
