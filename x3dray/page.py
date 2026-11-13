"""Assemble the Artifact folder: the page, its data bundle, the scene, vendored X_ITE and the label font. No model.

The folder is exactly the file set a claude.ai Artifact serves: index.html has no document shell (the host wraps it),
X_ITE ships beside the page (the Artifact's policy lets a CDN serve scripts only, and X_ITE fetches its components
and assets from beside its script), and the labels use a TTF (X_ITE's WOFF2 path needs a decoder it builds with
new Function, which the policy refuses). publish.json maps each published path to its source and content type.
Before anything is written, the bundle is scanned for addresses, session URLs and secret-shaped strings.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil

from . import __version__
from . import publication
from .publication import TYPES

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SESSION_URL = re.compile(r"claude\.ai/code/session_[A-Za-z0-9]+")
SECRET = re.compile(r"AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY")


def _j(d, n, default=None):
    p = os.path.join(d, n)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _github(cfg_repo: dict | None, owner: str, name: str, visibility: str) -> str | None:
    if visibility != "public":
        return None
    d = (cfg_repo or {}).get("dir", "")
    if d.startswith(owner + "/"):
        return "https://github.com/" + d
    return f"https://github.com/{owner}/{name}"


def make_bundle(data_dir: str, scene_dir: str, family_cfg: dict | None = None) -> dict:
    ds, ti, prof = _j(data_dir, "dataset.json"), _j(data_dir, "ties.json"), _j(data_dir, "profile.json")
    spec, selfd, cur = _j(data_dir, "spec.rendered.json"), _j(data_dir, "self.json"), _j(data_dir, "curated.json", {}) or {}
    con, bri = _j(data_dir, "concepts.json"), _j(data_dir, "bridges.json")
    layout = _j(scene_dir, "layout.json")
    owner = (family_cfg or {}).get("owner") or ds.get("owner") or "hagopjay"
    cfg_repos = {r["name"]: r for r in (family_cfg or {}).get("repos", [])}
    repos = []
    for name, pr in prof["per_repo"].items():
        r = {"name": name, **{k: v for k, v in pr.items() if k != "by_kind"}}
        r["github"] = _github(cfg_repos.get(name), owner, name, pr.get("visibility", "public"))
        r["aliases"] = cfg_repos.get(name, {}).get("aliases", [])
        repos.append(r)
    commits, upstream = [], []
    rows = sorted(((r["name"], c) for r in ds["repos"] for c in r.get("commits", []) if not c.get("upstream")), key=lambda rc: (rc[1]["t"], rc[0]))
    for i, (name, c) in enumerate(rows):
        row = {"i": i, "r": name, "t": c["t"], "c": c["cls"], "w": c["who"], "a": c["add"], "d": c["del"], "f": c["files"],
               "m": 1 if c.get("merge") else 0, "b": 1 if c.get("default", True) else 0, "tr": 1 if c.get("ai_trailer") else 0}
        if c.get("sessions"):
            row["s"] = c["sessions"]
        if c.get("sha"):
            row["h"] = c["sha"]
        if c.get("subject"):
            row["sub"] = c["subject"]
        commits.append(row)
    for r in ds["repos"]:
        up = [c for c in r.get("commits", []) if c.get("upstream")]
        if up:
            import datetime as dt
            d = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")
            upstream.append({"repo": r["name"], "commits": len(up), "first": d(min(c["t"] for c in up)), "last": d(max(c["t"] for c in up))})
    files = {}
    for r in ds["repos"]:
        fs = r.get("files") or []
        if not fs:
            continue
        by_churn = sorted(fs, key=lambda f: -(f.get("churn") or 0))[:60]
        by_lines = sorted(fs, key=lambda f: -(f.get("lines") or 0))[:30]
        seen, keep = set(), []
        for f in by_churn + by_lines:
            if f["path"] in seen:
                continue
            seen.add(f["path"])
            keep.append({"p": f["path"], "k": f["kind"], "l": f.get("lines"), "by": f.get("bytes"), "n": f.get("commits"), "ch": f.get("churn"),
                         "first": f.get("first"), "last": f.get("last"), "sh": f.get("share")})
        files[r["name"]] = keep
    machine = []
    for p in ti.get("pairs", []):
        ev = [e for e in p.get("evidence", []) if e.get("path") or e.get("text")]
        if not ev:
            continue  # no witness, no tie
        machine.append({"from": p["from"], "to": p["to"], "grade": p["grade"], "reason": p["grade_reason"], "kinds": p["kinds"],
                        "pins": p["kinds"].get("pin", 0) or None, "pin_targets": dict(list((p.get("pin_targets") or {}).items())[:3]) or None,
                        "evidence_records": {**p.get("evidence_records", {}),
                                             "total": p.get("evidence_records", {}).get("total", len(p.get("evidence", []))),
                                             "returned": min(4, len(ev)),
                                             "omitted": max(0, p.get("evidence_records", {}).get("total", len(p.get("evidence", []))) - min(4, len(ev))),
                                             "page_limit": 4},
                        "evidence": [{k: (v[:160] if isinstance(v, str) else v) for k, v in e.items() if v is not None} for e in ev[:4]]})
    curated_ties = [t for t in cur.get("ties", []) if t.get("evidence")]
    vocab = {}
    for r in repos:
        for a in [r["name"]] + r.get("aliases", []):
            vocab[a.lower()] = r["name"]
    for k, v in ((family_cfg or {}).get("vocabulary") or {}).items():
        vocab[k.lower()] = v
    sd = dict(selfd)
    sd["modules"] = [{"path": m["path"], "sha256": m["sha256"][:16], "lines": m["lines"], "doc": m["doc"],
                      "defs": [f"{d['name']}:{d['line']}" for d in m["defs"]]} for m in selfd["modules"]]
    privacy = ("Private repos show only commit times, identity classes and sizes. Emails never leave the harvester; "
               "Claude-Session URLs are reduced to short hashes; personal names in the family config are hashes.")
    bundle = {
        "meta": {"family": ds.get("family"), "title": spec.get("title", "x3d-ray"), "subtitle": spec.get("subtitle", ""),
                 "harvested_at": ds.get("harvested_at"), "built_at": selfd["tool"].get("built_at"), "version": __version__,
                 "head": selfd["tool"]["git"].get("head"), "privacy": privacy, "x_ite": "16.7.0"},
        "vocab": vocab,
        "vocab_notes": (family_cfg or {}).get("vocabulary_notes", {}),
        "repos": repos,
        "commits": commits,
        "upstream": upstream,
        "files": files,
        "ties": {"machine": machine, "curated": curated_ties, "series": cur.get("series", []), "grades": cur.get("grades") or
                 {"C": "Confirmed", "L": "Likely", "S": "Suspected"}, "dropped_without_witness": len(ti.get("pairs", [])) - len(machine)},
        "concepts": con,    # x3dray/structure.py: formal concept analysis (None when the stage did not run)
        "bridges": bri,     # x3dray/structure.py: betweenness and passage points
        "signals": prof["signals"],
        "spec": {k: spec.get(k) for k in ("title", "subtitle", "author", "views", "tour", "questions")},
        "self": sd,
        "layout": layout,
    }
    return bundle


def privacy_scan(text: str) -> list[str]:
    problems = []
    for m in EMAIL.findall(text):
        problems.append("address-shaped text: " + m[:3] + "…")
    if SESSION_URL.search(text):
        problems.append("a Claude session URL")
    if SECRET.search(text):
        problems.append("a secret-shaped string")
    return problems


def page_html(css: str, xite_version: str = "local distribution") -> str:
    return f"""<title>x3d-ray</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="loom:xite" content="x_ite/x_ite.min.js">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@400;500;600&display=swap">
<style>
{css}
</style>
<header class="top">
  <div class="brand"><h1>x3d<span>-</span>ray</h1><p id="subtitle">HagopJay's X3D repo family</p></div>
  <div id="build" class="build" aria-label="Build"></div>
</header>
<nav class="bar" aria-label="Views, tour and scene controls">
  <div id="views" class="views"></div>
  <div class="tour"><button id="tourPrev" type="button" aria-label="Previous tour stop">◀</button><span id="tourN" class="mono">0/0</span><button id="tourNext" type="button" aria-label="Next tour stop">▶</button><button id="tourPlay" type="button" aria-pressed="false">Play tour</button></div>
  <div class="tools"><button id="xray" type="button" aria-pressed="false" title="Dim everything but the lit commits">X-ray</button>
    <label for="mode">colour <select id="mode"><option value="who">who</option><option value="repo">repo</option><option value="size">lines changed</option><option value="branch">branch</option><option value="age">age</option></select></label>
    <button id="clear" type="button">Clear</button></div>
</nav>
<main class="grid">
  <section class="left" aria-label="Readout">
    <h2>Readout</h2>
    <div id="kpis" class="kpis"></div>
    <h2>Signals <span class="hint">computed by code; click one to see it</span></h2>
    <ol id="signals" class="signals"></ol>
  </section>
  <section class="center">
    <div id="stage" class="stage">
      <x3d-canvas id="scene" splashScreen="false" contextMenu="false" notifications="false" timings="false" cache="true"></x3d-canvas>
      <p id="status" class="status" hidden></p>
      <div id="tag" class="tag" hidden></div>
      <aside id="card" class="card" hidden aria-live="polite"><button id="cardClose" type="button" aria-label="Close">×</button><div id="cardBody"></div><button id="cardAsk" class="askthis" type="button" hidden>Ask Claude about this</button></aside>
      <div class="legend" aria-hidden="true">
        <span><i style="background:#f0b443"></i>HagopJay</span><span><i style="background:#e8795d"></i>Claude</span><span><i style="background:#a4bf7a"></i>bots</span><span><i style="background:#a68fdc"></i>other</span>
        <span><i style="background:#5cc28a"></i>C</span><span><i style="background:#e6c75a"></i>L</span><span><i style="background:#e8875a"></i>S</span>
        <span>box commit · ball merge · dot Claude co-author · faint side branch</span>
        <b id="legendMode">colour: who</b><b id="legendLit"></b><b id="legendNotes"></b>
      </div>
    </div>
    <div class="caption" aria-live="polite"><h3 id="capTitle">Overview</h3><p id="capClaim" class="claim" hidden><b id="capSays"></b> <span id="capClaimText"></span></p><p id="capText"></p></div>
    <div class="charts">
      <figure><figcaption>Commits per active day, by who</figcaption><div id="chartDays"></div></figure>
      <figure><figcaption>Hour of day, UTC</figcaption><div id="chartHours"></div></figure>
    </div>
    <div id="repos" class="repos"></div>
  </section>
  <section class="right" aria-label="Ask Claude">
    <div class="askhead"><h2>Ask Claude</h2>
      <fieldset class="tier"><legend class="sr">Answer style</legend><label><input type="radio" name="tier" value="default" checked> Thorough</label><label><input type="radio" name="tier" value="quick"> Fast</label></fieldset>
      <button id="newChat" type="button">New</button></div>
    <p class="askwhat">Claude reads a digest of this data, queries it with the page's tools, and can move the camera, light commits and pin notes in the scene. It runs on your own Claude account; your first question asks you to allow it.</p>
    <p id="askState" class="askstate" hidden></p>
    <div id="qchips" class="qchips"></div>
    <div id="chat" class="chat" aria-live="polite"></div>
    <form id="askForm" class="askform"><label for="q" class="sr">Your question</label><textarea id="q" rows="2" placeholder="Ask about these repos, or how this page was made"></textarea>
      <button id="stopBtn" type="button" hidden>Stop</button><button id="askBtn" type="submit">Ask</button></form>
  </section>
</main>
<footer>
  <p><b>Data.</b> The git history and tip trees of the family's repos, harvested with no model. Lines changed come from git numstat, so vendored and generated files count in full.</p>
  <p><b>Privacy.</b> Private repos show only commit times, identity classes and sizes. Emails never leave the harvester, Claude-Session URLs become short hashes, and personal names in the config are hashes.</p>
  <p><b>Words.</b> Claude wrote the captions; every number in them is a slot that code fills from the profile, and a fact gate fails any stray digit. Ties marked E01 to E40 come from the x3d_mcp fork masterclass.</p>
  <p><b>Scene.</b> One X3D 4.0 file drawn by X_ITE {xite_version} from this page's own files. Drag to orbit, scroll or pinch to zoom, click anything for its card.</p>
</footer>
<script type="module" src="app.js"></script>
"""


def build_page(data_dir: str, out_dir: str, xite: str, font: str, family_cfg: dict | None = None) -> dict:
    # Validate the selected dependency before writing any page assets. Missing
    # components used to be silently omitted and produced a broken export.
    required = ["x_ite.min.js", "x_ite.css", "LICENSE.md", "assets/linetype/linetypes.png",
                "assets/lib/mikktspace_bg.wasm", "assets/images/logo.svg"]
    components = [os.path.relpath(p, xite).replace(os.sep, "/") for p in glob.glob(os.path.join(xite, "assets", "components", "*.min.js"))]
    want = required + sorted(components)
    for rel in want:
        if not publication.safe_path(xite, rel).is_file():
            raise ValueError(f"page: required X_ITE asset is missing: {rel}")
    if not os.path.isfile(font):
        raise ValueError("page: label font is missing")
    for rel in publication.GENERATED:
        publication.safe_path(out_dir, rel)
    for rel in ("x_ite", "fonts", "fonts/UbuntuMono-R.ttf", "fonts/UBUNTU-FONT-LICENCE.txt", "publish.json"):
        publication.safe_path(out_dir, rel)
    if family_cfg is None:
        fam = glob.glob(os.path.join(os.path.dirname(WEB), "..", "families", "*.json"))
        ds = _j(data_dir, "dataset.json")
        for f in fam:
            with open(f, encoding="utf-8") as fh:
                c = json.load(fh)
            if c.get("family") == ds.get("family"):
                family_cfg = c
    bundle = make_bundle(data_dir, out_dir, family_cfg)
    with open(os.path.join(xite, "x_ite.min.js"), encoding="utf-8", errors="replace") as fh:
        header = fh.read(4096)
    version = re.search(r"X_ITE\s+v?(\d+\.\d+\.\d+)", header)
    bundle["meta"]["x_ite"] = version.group(1) if version else "unverified distribution"
    text = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    problems = privacy_scan(text)
    if problems:
        raise SystemExit("page: privacy scan failed: " + "; ".join(sorted(set(problems))[:8]))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "bundle.json"), "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(WEB, "page.css"), encoding="utf-8") as fh:
        css = fh.read()
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page_html(css, bundle["meta"]["x_ite"]))
    for f in ("app.js", "tools.js"):
        shutil.copyfile(os.path.join(WEB, f), os.path.join(out_dir, f))
    # X_ITE: the script, its stylesheet, every component it may import, the line-type texture, the wasm it asks for, its licence
    xo = os.path.join(out_dir, "x_ite")
    if os.path.isdir(xo):
        shutil.rmtree(xo)
    # Not vendored: X_ITE's woff2 fonts and their decoder. The decoder evaluates strings as code, which the Artifact policy refuses, so
    # every label in the scene names the TTF above; X_ITE's preload of its default SERIF font then fails quietly and nothing uses it.
    for rel in want:
        src = os.path.join(xite, rel)
        dst = os.path.join(xo, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    fo = os.path.join(out_dir, "fonts")
    publication.safe_path(out_dir, "fonts/UbuntuMono-R.ttf")
    os.makedirs(fo, exist_ok=True)
    # Compiler labels and scene notes share this stable URL regardless of the
    # supplied font's filename.
    shutil.copyfile(font, os.path.join(fo, "UbuntuMono-R.ttf"))
    lic = os.path.join(os.path.dirname(font), "UBUNTU-FONT-LICENCE.txt")
    if os.path.exists(lic):
        shutil.copyfile(lic, os.path.join(fo, "UBUNTU-FONT-LICENCE.txt"))
    emitted = [publication.entry(rel) for rel in publication.GENERATED]
    emitted += [publication.entry("x_ite/" + rel, "vendored") for rel in want]
    emitted.append(publication.entry("fonts/UbuntuMono-R.ttf", "vendored"))
    if os.path.exists(lic):
        emitted.append(publication.entry("fonts/UBUNTU-FONT-LICENCE.txt", "vendored"))
    publication.register(out_dir, emitted)
    return write_publish_map(out_dir)


def write_publish_map(out_dir: str) -> dict:
    publication.safe_path(out_dir, "publish.json")
    pub, total = {}, 0
    selected = publication.assets(out_dir)
    for asset in selected:
        rel = asset["path"]
        p = publication.safe_path(out_dir, rel)
        total += p.stat().st_size
        if rel != "index.html":   # the Artifact host serves its entrypoint separately
            pub[rel] = {"from": str(p), "contentType": asset["contentType"]}
    temporary = publication.safe_path(out_dir, ".publish.json.tmp")
    with temporary.open("w", encoding="utf-8") as fh:
        json.dump(pub, fh, indent=1, sort_keys=True)
    temporary.replace(publication.safe_path(out_dir, "publish.json"))
    return {"files": len(selected), "bytes": total}
