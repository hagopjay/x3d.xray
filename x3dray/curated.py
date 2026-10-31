"""Import the hand-graded ties and commit series of the x3d_mcp fork masterclass (reused, not redone).

The masterclass graded 40 ties between the family's repos by reading code, commits and CI (data/ecosystem.json)
and grouped the fork's commits into 12 series (data/series.json). x3d-ray shows them beside its own machine-found
ties so the two can be compared. Nothing personal comes across: series authors are dropped, and any address-shaped
text is replaced with "[address]". Repos named in --exclude (private ones, for a public build) are dropped whole.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _scrub(s):
    return EMAIL.sub("[address]", s) if isinstance(s, str) else s


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def import_masterclass(src_dir: str, exclude: tuple[str, ...] = (), notes: list[str] | None = None,
                       series_repo: str = "x3d_mcp") -> dict:
    eco_p = os.path.join(src_dir, "data", "ecosystem.json")
    ser_p = os.path.join(src_dir, "data", "series.json")
    eco = json.load(open(eco_p, encoding="utf-8"))
    ties = []
    for e in eco.get("edges", []):
        if e["from"] in exclude or e["to"] in exclude:
            continue
        ties.append({
            "id": e["id"], "from": e["from"], "to": e["to"], "kind": e.get("kind"),
            "what": _scrub(e.get("what", "")), "grade": e.get("grade"), "first_date": e.get("first_date"),
            "direction": _scrub(e.get("direction")), "evidence": [_scrub(x) for x in e.get("evidence", [])][:6],
        })
    nodes = []
    for n in eco.get("nodes", []):
        if n["id"] in exclude:
            continue
        nodes.append({"id": n["id"], "kind": n.get("kind"), "role": _scrub(n.get("role")), "visibility": n.get("visibility"),
                      "fork_of": n.get("fork_of"), "grade": n.get("grade")})
    series = []
    sources = [{"file": "data/ecosystem.json", "sha256": _sha(eco_p), "generated_utc": eco.get("generated_utc")}]
    if os.path.exists(ser_p):
        ser = json.load(open(ser_p, encoding="utf-8"))
        sources.append({"file": "data/series.json", "sha256": _sha(ser_p), "generated_utc": ser.get("generated_utc")})
        repo = series_repo  # series.json names the fork and its upstream in one phrase; the series are the fork's
        for s in ser.get("series", []):
            cs = s.get("commits", [])
            if not cs:
                continue
            dates = sorted(c["date"][:10] for c in cs if c.get("date"))
            series.append({
                "id": s["id"], "slug": s.get("slug"), "title": _scrub(s.get("title")), "repo": repo,
                "shas": [c["sha"][:7] for c in cs if c.get("sha")], "first": dates[0] if dates else None,
                "last": dates[-1] if dates else None, "commits": len(cs),
                "lines_added": sum(int(c.get("lines_added") or 0) for c in cs),
                "tests_added": sum(int(c.get("tests_added") or 0) for c in cs),
            })
    return {
        "source": {"name": "x3d_mcp fork masterclass", "files": sources},
        "grades": eco.get("grades", {}),
        "redaction": eco.get("redaction"),
        "notes": list(notes or []),
        "nodes": nodes,
        "ties": ties,
        "series": series,
    }
