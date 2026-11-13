"""An explicit asset registry shared by export, privacy scanning and receipts.

The registry is build control, not a public asset. Builders register the files
they emit; directory contents never grant publication permission. receipt.json
is a detached attestation and therefore deliberately excludes its own byte hash.
This module does not make concurrent directory mutation safe: seal a stopped
build, then promote/copy it to its serving destination.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

REGISTRY = ".publication-assets.json"
GENERATED = ("index.html", "bundle.json", "app.js", "tools.js", "xray.x3d")
REPORTS = ("gates.json", "receipt.json")
TYPES = {".html": "text/html", ".x3d": "application/xml", ".json": "application/json", ".js": "text/javascript", ".mjs": "text/javascript",
         ".ttf": "font/ttf", ".png": "image/png", ".txt": "text/plain", ".md": "text/markdown", ".css": "text/css", ".wasm": "application/wasm",
         ".svg": "image/svg+xml", ".woff2": "font/woff2"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_path(out_dir: str | Path, rel: str) -> Path:
    root = Path(out_dir).resolve()
    if not isinstance(rel, str) or not rel:
        raise ValueError("publication: asset path must be a nonempty string")
    parts = PurePosixPath(rel)
    if parts.is_absolute() or ".." in parts.parts or any(c in rel for c in "\\:%?#\0") or parts.as_posix() != rel:
        raise ValueError("publication: invalid relative asset path")
    path = root.joinpath(*parts.parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError("publication: asset escapes output directory")
    current = root
    for part in parts.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("publication: symlink assets are not allowed")
    return path


def entry(rel: str, role: str = "owned", digest: str | None = None) -> dict:
    ext = PurePosixPath(rel).suffix.lower()
    if ext not in TYPES or role not in {"owned", "vendored"}:
        raise ValueError("publication: unsupported asset type or role")
    out = {"path": rel, "role": role, "contentType": TYPES[ext]}
    if digest is not None:
        out["sha256"] = digest
    return out


def register(out_dir: str, entries: list[dict]) -> None:
    """Replace the current builder's registry only after every asset exists."""
    checked = []
    seen = set()
    for item in entries:
        rel = item["path"]
        if rel in seen or rel in REPORTS or rel in {"publish.json", REGISTRY, "layout.json"}:
            raise ValueError("publication: duplicate or reserved asset")
        seen.add(rel)
        path = safe_path(out_dir, rel)
        if not path.is_file():
            raise ValueError("publication: registered asset is missing")
        clean = entry(rel, item.get("role", "owned"), item.get("sha256"))
        if clean["role"] == "vendored":
            clean["sha256"] = sha256(path)
        checked.append(clean)
    registry = {"schema_version": 1, "assets": sorted(checked, key=lambda x: x["path"])}
    target = safe_path(out_dir, REGISTRY)
    temporary = target.with_suffix(".tmp")
    safe_path(out_dir, temporary.name)
    temporary.write_text(json.dumps(registry, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)


def assets(out_dir: str) -> list[dict]:
    """Return the exact served set, including the separately served entrypoint.

    Before `page` has registered assets, old fixtures and individually invoked
    stages use only known generated root names. No vendor directory is inferred.
    """
    registry_path = Path(out_dir) / REGISTRY
    if registry_path.exists():
        safe_path(out_dir, REGISTRY)
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(registry, dict) or registry.get("schema_version") != 1 or not isinstance(registry.get("assets"), list):
            raise ValueError("publication: invalid asset registry")
        selected = registry["assets"]
    else:
        selected = [entry(rel) for rel in GENERATED if safe_path(out_dir, rel).is_file()]
    selected = list(selected) + [entry(rel) for rel in REPORTS if safe_path(out_dir, rel).is_file()]
    checked, seen = [], set()
    for item in selected:
        if not isinstance(item, dict):
            raise ValueError("publication: invalid asset entry")
        rel = item.get("path")
        path = safe_path(out_dir, rel)
        if rel in seen or rel in {"publish.json", REGISTRY, "layout.json"}:
            raise ValueError("publication: duplicate or reserved asset")
        seen.add(rel)
        if not path.is_file():
            raise ValueError("publication: registered asset is missing")
        clean = entry(rel, item.get("role", "owned"), item.get("sha256"))
        if item.get("contentType") != clean["contentType"]:
            raise ValueError("publication: asset content type does not match its path")
        if clean["role"] == "vendored" and (not clean.get("sha256") or clean["sha256"] != sha256(path)):
            raise ValueError("publication: registered vendor asset bytes changed")
        checked.append(clean)
    return sorted(checked, key=lambda x: x["path"])
