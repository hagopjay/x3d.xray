#!/usr/bin/env python3
"""Serve this exported laboratory on loopback, from its explicit asset manifest.

Windows: py -3 start_lab.py
WSL/Linux: python3 start_lab.py
The receipt is detached, not a publisher signature. Use a trusted export.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


def approved_assets(root: Path) -> set[str]:
    root = root.resolve()
    receipt = json.loads((root / "lab-receipt.json").read_text(encoding="utf-8"))
    allowed = set()
    for item in receipt["assets"] + receipt.get("controls", []):
        rel = item["path"]
        pp = PurePosixPath(rel)
        if pp.is_absolute() or ".." in pp.parts or any(c in rel for c in "\\:%?#\0") or pp.as_posix() != rel:
            raise ValueError("Invalid manifest path")
        p = root / rel
        if not p.resolve().is_relative_to(root) or p.is_symlink() or not p.is_file():
            raise ValueError("Missing or escaping manifest asset")
        if hashlib.sha256(p.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("A registered file changed after export: " + rel)
    for item in receipt["assets"]:
        if item["path"] in allowed:
            raise ValueError("Duplicate manifest asset")
        allowed.add(item["path"])
    if "index.html" not in allowed:
        raise ValueError("Manifest has no entrypoint")
    allowed.add("lab-receipt.json")
    return allowed


def handler(root: Path, allowed: set[str]):
    class ManifestHandler(SimpleHTTPRequestHandler):
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript", ".wasm": "application/wasm", ".ttf": "font/ttf", ".woff2": "font/woff2", ".x3d": "model/x3d+xml"}
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def send_head(self):
            path = unquote(urlsplit(self.path).path)
            rel = "index.html" if path in ("", "/") else path.removeprefix("/")
            if rel not in allowed:
                self.send_error(404, "Not a registered laboratory asset")
                return None
            # Refuse a symlink substituted after startup as well. This is not a
            # substitute for immutable output or protection from local writers.
            p = root / rel
            if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):
                self.send_error(404)
                return None
            self.path = "/" + rel
            return super().send_head()

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()
    return ManifestHandler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    allowed = approved_assets(root)
    if args.verify_only:
        print(f"Verified {len(allowed) - 1} registered assets and detached receipt.", flush=True)
        return 0
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler(root, allowed)) as server:
        print(f"Open http://localhost:{server.server_port} — Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
