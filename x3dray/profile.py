"""Profile a harvested family with no model: counts, shares, rhythms, outliers, hotspots.

Every signal carries its numbers and a sentence built from them by code, so any caption
that quotes a signal is grounded by construction. The fact gate re-checks this later.
"""

from __future__ import annotations

import collections
import datetime as dt
import math
import statistics

CLASSES = ["you", "ai", "bot", "upstream", "other"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _d(t: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc)


def _day(t: int) -> str:
    return _d(t).strftime("%Y-%m-%d")


def _pct(a: float, b: float) -> int:
    return round(100 * a / b) if b else 0


CLASS_KEYS = ("you", "ai", "bot", "other", "upstream")


def _and(xs: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def _human_date(iso_day: str) -> str:
    d = dt.date.fromisoformat(iso_day[:10])
    return f"{d.day} {d.strftime('%b')} {d.year}"


def _short(path: str, n: int = 36) -> str:
    """A path short enough for a sentence: the whole path if it fits, else its file name, cut in the middle if need be."""
    if len(path) <= n:
        return path
    base = path.rsplit("/", 1)[-1]
    return base if len(base) <= n else base[: n // 2 - 1] + "…" + base[-(n // 2):]


BRIDGE_ROWS = 7   # rows in the scene's bridge-file panel; the caption says "up to" this many, and the panel names the rest


def bridge_rows(hubs: dict, bridges: dict) -> list[str]:
    """The repos the bridge-file panel draws, in order: those with a bridge (high churn set aside), then those with only a hub."""
    return list(bridges) + [r for r in hubs if r not in bridges]


def top_bridges(variant: dict, min_component: int = 10) -> dict:
    """Per repo, the file with the most shortest paths through it (raw betweenness) in a co-change cluster of at least
    min_component files. Files of one repo only ever share commits with files of the same repo, so clusters never span repos."""
    best: dict[str, tuple] = {}
    for fid, v in variant.get("per_file", {}).items():
        if v["component"] < min_component or v["betweenness"] <= 0:
            continue
        repo, path = fid.split(":", 1)
        key = (v["betweenness"], path)
        if repo not in best or key > best[repo][0]:
            best[repo] = (key, path, v)
    return {r: {"file": p, "betweenness": v["betweenness"], "share_pct": round(100 * v["share"]), "cluster": v["component"],
                "cut_vertex": v["cut_vertex"], "commits": v["commits"]} for r, (_, p, v) in sorted(best.items(), key=lambda kv: -kv[1][0][0])}


def profile_family(ds: dict, ties: dict, curated: dict | None = None, concepts: dict | None = None, bridges: dict | None = None) -> dict:
    repos = [r for r in ds["repos"] if r.get("present")]
    own = []  # (repo, commit) for commits that are the family's own work
    for r in repos:
        for c in r.get("commits", []):
            if not c.get("upstream"):
                own.append((r["name"], c))
    own.sort(key=lambda rc: rc[1]["t"])
    signals: list[dict] = []

    def sig(sid: str, kind: str, text: str, **values):
        signals.append({"id": sid, "kind": kind, "text": text, "values": values})

    total = len(own)
    by_cls = collections.Counter(c["cls"] for _, c in own)
    first_t, last_t = own[0][1]["t"], own[-1][1]["t"]
    days_all = sorted({_day(c["t"]) for _, c in own})
    sig(
        "family_span", "totals",
        f"{total} commits of the family's own work across {len({n for n, _ in own})} repos, "
        f"from {_human_date(_day(first_t))} to {_human_date(_day(last_t))}, on {len(days_all)} different days.",
        commits=total, repos=len({n for n, _ in own}), first=_day(first_t), last=_day(last_t), active_days=len(days_all),
    )
    shares = {k: _pct(by_cls.get(k, 0), total) for k in CLASSES}
    sig(
        "who_commits", "identity",
        f"Claude wrote {by_cls.get('ai', 0)} of them ({shares['ai']}%), HagopJay's accounts {by_cls.get('you', 0)} "
        f"({shares['you']}%), bots {by_cls.get('bot', 0)} ({shares['bot']}%).",
        counts=dict(by_cls), shares=shares,
    )

    # Busiest month
    per_month = collections.Counter(_day(c["t"])[:7] for _, c in own)
    month, month_n = max(per_month.items(), key=lambda kv: (kv[1], kv[0]))
    y, m = int(month[:4]), int(month[5:])
    month_end = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()
    month_name = dt.date(y, m, 1).strftime("%B %Y")
    sig(
        "busiest_month", "time",
        f"{month_n} of the {total} commits ({_pct(month_n, total)}%) landed in {month_name}, the busiest month.",
        month=month, month_name=month_name, commits=month_n, total=total, share=_pct(month_n, total),
        **{"from": month + "-01", "to": month_end}, by_month=dict(sorted(per_month.items())),
    )

    # Busiest day
    per_day = collections.Counter(_day(c["t"]) for _, c in own)
    peak_day, peak_n = max(per_day.items(), key=lambda kv: (kv[1], kv[0]))
    peak_repos = collections.Counter(n for n, c in own if _day(c["t"]) == peak_day)
    sig(
        "busiest_day", "time",
        f"The busiest day was {_human_date(peak_day)}: {peak_n} commits in {len(peak_repos)} repos, "
        f"led by {peak_repos.most_common(1)[0][0]} ({peak_repos.most_common(1)[0][1]}).",
        day=peak_day, commits=peak_n, repos=dict(peak_repos),
        top_repo=peak_repos.most_common(1)[0][0], top_commits=peak_repos.most_common(1)[0][1],
        second_repo=(peak_repos.most_common(2)[1][0] if len(peak_repos) > 1 else None),
        second_commits=(peak_repos.most_common(2)[1][1] if len(peak_repos) > 1 else 0),
    )

    # Births in git order versus the lineage HagopJay stated
    births = []
    for r in repos:
        oc = [c for c in r.get("commits", []) if not c.get("upstream")]
        if oc:
            births.append((min(c["t"] for c in oc), r["name"]))
    births.sort()
    stated = [x for x in ds.get("lineage", []) if x not in {".prov"}]
    git_order = [n for _, n in births if n in stated]
    sig(
        "births", "lineage",
        "In git order the family's repos were born: "
        + ", ".join(f"{n} ({_human_date(_day(t))})" for t, n in births if n in stated)
        + ". HagopJay's own account orders them " + ", ".join(stated) + ".",
        git_order=git_order, stated=stated, births={n: _day(t) for t, n in births},
    )

    # AI share by repo
    ai_by_repo = {}
    for r in repos:
        oc = [c for c in r.get("commits", []) if not c.get("upstream")]
        if len(oc) >= 5:
            ai_by_repo[r["name"]] = (_pct(sum(1 for c in oc if c["cls"] == "ai"), len(oc)), len(oc))
    hi = max(ai_by_repo.items(), key=lambda kv: kv[1][0])
    lo = min(ai_by_repo.items(), key=lambda kv: kv[1][0])
    sig(
        "ai_share_by_repo", "identity",
        f"Claude's share of commits runs from {lo[1][0]}% in {lo[0]} to {hi[1][0]}% in {hi[0]}.",
        shares={k: v[0] for k, v in ai_by_repo.items()}, commits={k: v[1] for k, v in ai_by_repo.items()},
    )

    # Weekly AI share across the family (ISO weeks with at least 5 commits)
    weeks = collections.defaultdict(collections.Counter)
    for _, c in own:
        y, w, _ = _d(c["t"]).isocalendar()
        weeks[f"{y}-W{w:02d}"][c["cls"]] += 1
    wk = [(k, v) for k, v in sorted(weeks.items()) if sum(v.values()) >= 5]
    sig(
        "weekly_mix", "identity",
        "Week by week, Claude's share of commits: " + ", ".join(f"{k} {_pct(v['ai'], sum(v.values()))}% of {sum(v.values())}" for k, v in wk) + ".",
        weeks={k: dict(v) for k, v in wk},
    )

    # The bot identity
    bot = collections.Counter((n, c["who"]) for n, c in own if c["cls"] == "bot")
    if bot:
        (bn, bw), bc = bot.most_common(1)[0]
        sig("bot_identity", "identity", f"The busiest bot identity is {bw}, with {bc} commits in {bn}.", repo=bn, who=bw, commits=bc)

    # Hours and weekdays (UTC)
    hours = {k: [0] * 24 for k in CLASSES}
    wdays = {k: [0] * 7 for k in CLASSES}
    for _, c in own:
        d = _d(c["t"])
        hours[c["cls"]][d.hour] += 1
        wdays[c["cls"]][d.weekday()] += 1
    you_peak = max(range(24), key=lambda h: hours["you"][h])
    ai_peak = max(range(24), key=lambda h: hours["ai"][h])
    sig(
        "rhythm", "time",
        f"HagopJay's accounts commit most at {you_peak:02d}:00 UTC ({hours['you'][you_peak]} commits); "
        f"Claude's busiest hour is {ai_peak:02d}:00 UTC ({hours['ai'][ai_peak]}).",
        hours=hours, weekdays=wdays, you_peak=you_peak, ai_peak=ai_peak,
    )

    # Human commits carrying an AI co-author trailer
    paired = sum(1 for _, c in own if c["cls"] == "you" and c.get("ai_trailer"))
    sig(
        "pairing", "identity",
        f"{paired} commits under HagopJay's names also carry a Claude co-author trailer.",
        commits=paired,
    )

    # Largest commits (public repos only have subjects)
    big = sorted(((c["add"] + c["del"], n, c) for n, c in own), key=lambda x: -x[0])[:6]
    sig(
        "largest_commits", "outlier",
        "The largest commits by lines changed: "
        + "; ".join(f"{n} {c.get('sha', '')[:7] or '(private)'} {c['add'] + c['del']:,} lines" for _, n, c in big) + ".",
        commits=[{"repo": n, "sha": c.get("sha"), "lines": c["add"] + c["del"], "subject": c.get("subject"), "day": _day(c["t"])} for _, n, c in big],
    )

    # Multi-repo Claude sessions
    sess = collections.defaultdict(set)
    sess_n = collections.Counter()
    for n, c in own:
        for s in c.get("sessions", []):
            sess[s].add(n)
            sess_n[s] += 1
    multi = {s: sorted(v) for s, v in sess.items() if len(v) > 1}
    widest = max(multi.items(), key=lambda kv: (len(kv[1]), sess_n[kv[0]])) if multi else None
    sig(
        "sessions", "identity",
        f"{len(sess)} distinct Claude sessions left a session trailer; {len(multi)} of them committed to more than one repo"
        + (f", the widest touching {len(widest[1])}: {', '.join(widest[1])}." if widest else "."),
        sessions=len(sess), multi_repo=len(multi), widest=widest[1] if widest else [], multi={k: v for k, v in multi.items()},
    )

    # Tests, X3D assets, size
    tree = {r["name"]: r.get("tree", {}) for r in repos}
    tests = {n: t.get("tests", 0) for n, t in tree.items() if t.get("tests")}
    sig(
        "tests", "quality",
        "Test functions at each repo's tip: " + ", ".join(f"{n} {v:,}" for n, v in sorted(tests.items(), key=lambda kv: -kv[1])) + ".",
        tests=tests,
    )
    x3d = {n: t.get("x3d_files", 0) for n, t in tree.items() if t.get("x3d_files")}
    sig(
        "x3d_assets", "content",
        f"{sum(x3d.values()):,} X3D files sit at the repo tips, {x3d.get('x3d.ML', 0):,} of them in x3d.ML.",
        x3d_files=x3d, total=sum(x3d.values()),
    )

    # Churn hotspots (public repos)
    hot = []
    for r in repos:
        for f in r.get("files", []) or []:
            if f["churn"]:
                hot.append((f["churn"], r["name"], f["path"], f["commits"]))
    hot.sort(reverse=True)
    sig(
        "hotspots", "content",
        "Most rewritten files: " + "; ".join(f"{n}:{p} ({ch:,} lines over {k} commits)" for ch, n, p, k in hot[:5]) + ".",
        files=[{"repo": n, "path": p, "churn": ch, "commits": k} for ch, n, p, k in hot[:25]],
    )

    # Branches
    br = {r["name"]: r.get("branch_count", 0) for r in repos if r.get("branch_count")}
    unmerged = {}
    for r in repos:
        for b in r.get("branches", []) or []:
            if b.get("ahead_of_default"):
                unmerged.setdefault(r["name"], []).append(b)
    sig(
        "branches", "process",
        f"{sum(br.values())} remote branches in all; {sum(len(v) for v in unmerged.values())} carry commits their default branch lacks.",
        branches=br, unmerged={k: [b['name'] for b in v] for k, v in unmerged.items()},
    )

    # Pins: which x3d_mcp commits the family pins most
    pin_count = collections.Counter()
    pin_from = collections.defaultdict(collections.Counter)
    for p in ties.get("pairs", []):
        for sha, k in (p.get("pin_targets") or {}).items():
            pin_count[(p["to"], sha)] += k
            pin_from[(p["to"], sha)][p["from"]] += k
    subj = {}
    for r in repos:
        for c in r.get("commits", []):
            if c.get("sha"):
                subj[(r["name"], c["sha"][:12])] = (c.get("subject"), _day(c["t"]))
    top_pins = []
    for (repo, sha), k in pin_count.most_common(8):
        s, d = subj.get((repo, sha[:12]), (None, None))
        top_pins.append({"repo": repo, "sha": sha, "count": k, "subject": s, "day": d, "from": dict(pin_from[(repo, sha)])})
    by_target = dict(collections.Counter({}))
    for (repo, _sha), k in pin_count.items():
        by_target[repo] = by_target.get(repo, 0) + k
    if top_pins:
        tp = top_pins[0]
        sig(
            "pins", "ties",
            f"{sum(pin_count.values()):,} written commit hashes point across repos; the most cited is {tp['repo']} {tp['sha'][:7]}"
            + (f" ('{tp['subject'][:70]}', {_human_date(tp['day'])})" if tp["subject"] else "")
            + f", cited {tp['count']:,} times.",
            total=sum(pin_count.values()), top=top_pins,
            top_target=max(by_target, key=by_target.get), top_target_share=_pct(max(by_target.values()), sum(pin_count.values())),
            by_target=by_target,
        )

    # Ties
    grades = collections.Counter(p["grade"] for p in ties.get("pairs", []))
    obs = ties.get("observer") or {}
    aside = (obs.get("set_aside") or {}).get("mentions", 0)
    sig(
        "ties_machine", "ties",
        f"The machine found {len(ties.get('pairs', []))} tie pairs: {grades.get('C', 0)} Confirmed, {grades.get('L', 0)} Likely, {grades.get('S', 0)} Suspected."
        + (f" It set aside {aside} mentions in its own files, which name every repo it reads by construction." if obs else ""),
        grades=dict(grades), pairs=len(ties.get("pairs", [])), observer_set_aside=aside,
    )
    if curated:
        cg = collections.Counter(t["grade"] for t in curated.get("ties", []))
        sig(
            "ties_curated", "ties",
            f"The fork masterclass graded {len(curated.get('ties', []))} ties by hand: {cg.get('C', 0)} Confirmed, {cg.get('L', 0)} Likely, {cg.get('S', 0)} Suspected.",
            grades=dict(cg), ties=len(curated.get("ties", [])),
        )

    # Concepts: who touched which files, and which areas move together (x3dray/structure.py, formal concept analysis)
    if concepts:
        fc = concepts["files_by_author_class"]
        ex = concepts["excluded"]
        mass = concepts["mass_edits"]
        merges = sum(v for k, v in ex.items() if k.startswith("merges"))
        privc = sum(v for k, v in ex.items() if k.startswith("private"))
        nopath = sum(v for k, v in ex.items() if k.startswith("public commits with no paths"))
        # counted from the files themselves, not looked up as a concept: {you, ai} is a concept only when it is closed
        kinds = [set(rec["by"]) for rec in fc["files"].values()]
        only = {k: sum(1 for ks in kinds if ks == {k}) for k in CLASS_KEYS}
        shared = sum(1 for ks in kinds if len(ks) > 1)
        you_ai = sum(1 for ks in kinds if {"you", "ai"} <= ks)
        you_ai_exact = sum(1 for ks in kinds if ks == {"you", "ai"})
        aside = [f"{n} {what}" for n, what in ((mass["commits"], f"mass edits (more than {concepts['method']['params']['max_files_per_commit']} files)"),
                                                (merges, "merges"), (privc, "private commits"), (nopath, "public commits with no paths")) if n]
        sig(
            "concepts_files", "concepts",
            (f"Setting aside {_and(aside)}, " if aside else "")
            + f"{fc['n_commits']} commits touched {fc['n_files']} files: "
            f"{only['ai']} only by Claude, {only['you']} only by HagopJay's accounts, {only['bot']} only by bots, "
            f"{only['other'] + only['upstream']} only by other or unmapped identities, and "
            f"{shared} by more than one kind of author, {you_ai} of them by both HagopJay and Claude. The lattice has {fc['n_concepts']} concepts.",
            n_files=fc["n_files"], n_commits=fc["n_commits"], n_concepts=fc["n_concepts"], only=only, shared=shared,
            you_ai=you_ai, you_ai_exact=you_ai_exact, mass_edits=mass["commits"], merges=merges,
            mass_touch_pct=_pct(mass["file_touches"], mass["of_file_touches"]), mass_touches=mass["file_touches"],
            private_commits=privc, no_paths=nopath, from_stage="concepts",
        )
        rules = sorted(concepts["areas"]["rules"], key=lambda r: (-r["support"], -r["confidence"], r["if"], r["then"]))
        if rules:
            top = [{"if": r["if"], "then": r["then"], "both": r["both"], "support": r["support"], "pct": round(100 * r["confidence"]),
                    "exceptions": len(r["exceptions"])} for r in rules[:5]]
            sig(
                "concepts_rules", "concepts",
                "Areas that move together, as rules: " + "; ".join(f"{t['if']} => {t['then']} in {t['both']} of {t['support']} commits" for t in top[:3])
                + f". {len(rules)} rules hold for at least {round(100 * concepts['method']['params']['min_rule_confidence'])}% of their commits, each naming its exceptions.",
                rules=top, n_rules=len(rules), min_pct=round(100 * concepts["method"]["params"]["min_rule_confidence"]), from_stage="concepts",
            )

    # Bridges: what links the repos and the files (x3dray/structure.py, betweenness and passage points)
    if bridges:
        sb = bridges["sessions"]
        # prefer a pair whose only passage point is a repo: the plainest case of one repo standing between two others
        repo_pp = sorted(((k, v) for k, v in sb["passage_points"].items() if any(not x.startswith("session:") for x in v)),
                         key=lambda kv: (len(kv[1]), kv[0]))
        first_pp = None
        if repo_pp:
            k, v = repo_pp[0]
            a_, b_ = k.split(" -> ")
            first_pp = {"a": a_, "b": b_, "via": next(x for x in v if not x.startswith("session:"))}
        pairs = sb["repo_pairs"]
        sig(                                                  # under 320 characters, so the digest keeps every sentence
            "bridges_sessions", "bridges",
            f"{sb['n_multi_repo']} of {sb['n_sessions']} sessions that left a trailer committed to more than one repo, linking "
            f"{len(pairs)} pairs directly"
            + (f", most often {pairs[0]['a']} and {pairs[0]['b']} ({len(pairs[0]['sessions'])} sessions)" if pairs else "")
            + "."
            + (f" {first_pp['via']} lies on every session path between {first_pp['a']} and {first_pp['b']}." if first_pp else "")
            + f" {sb['commits_without_trailer']} own commits carry no session trailer, so no recorded session link is not no link.",
            sessions=sb["n_sessions"], multi=sb["n_multi_repo"], pairs=len(pairs), top_pair=(pairs[0] | {"sessions": len(pairs[0]["sessions"]), "shas": len(pairs[0]["shas"])}) if pairs else None,
            passage=first_pp, passage_points=sb["passage_points"], unlinked_pairs=len(sb["no_recorded_link"]), repo_pairs=sb.get("n_repo_pairs"),
            linked_through_others=len(sb.get("linked_through_others", [])),
            repos_without_session=sb.get("repos_without_session", []),
            without_trailer=sb["commits_without_trailer"], with_trailer=sb["commits_with_trailer"], from_stage="bridges",
        )
        fv = bridges["files"]["variants"]
        hubs, brid = top_bridges(fv["count"]), top_bridges(fv["jaccard"])
        big = sorted(r for r, n in fv["jaccard"].get("largest_cluster_by_repo", {}).items() if n >= 10) or sorted(brid)
        n_b, n_g = len(brid), len(big)                        # a repo can have a cluster of ten in which no file lies between two
        n_hc = len(bridges["files"]["high_churn"])
        rows = bridge_rows(hubs, brid)
        lead = list(brid.items())[:1]                         # one example: the bridges tool lists every repo's
        hub_lead = list(hubs.items())[:1]
        if brid:
            body = ((("the one repo" if n_g == 1 else f"all {n_g} repos") if n_b == n_g else f"{n_b} of the {n_g} repos")
                    + f" with a cluster of ten or more files {'has' if n_b == 1 else 'have'} a file between others in it; "
                    + "; ".join(f"in {r}, {_short(v['file'])} is on {v['share_pct']}% of the shortest paths between the other files of its "
                                f"cluster of {v['cluster']}" for r, v in lead) + ".")
        elif n_g:
            body = ("the one repo with a cluster of ten or more files has no file between others in it." if n_g == 1
                    else f"none of the {n_g} repos with a cluster of ten or more files has a file between others in it.")
        else:
            body = "no repo has a cluster of ten or more files, so no file is named as a bridge."
        # The two graphs differ in their files (high churn set aside) and in their distance (Jaccard, or shared commits), so with
        # no high-churn file the sentence names the distance, and it gives the hubs whenever they are not the same files.
        differs = any((hubs.get(r) or {}).get("file") != (brid.get(r) or {}).get("file") for r in set(hubs) | set(brid))
        sig(
            "bridges_files", "bridges",
            (f"With the {n_hc} high-churn files set aside, " if n_hc else "By Jaccard distance, ") + body
            + ((" With them kept, hubs such as " if n_hc else " Counting shared commits instead, hubs such as ")
               + "; ".join(f"{_short(v['file'])} in {r}" for r, v in hub_lead) + " rank first." if hub_lead and (n_hc or differs) else ""),
            per_repo=brid, hubs=hubs, high_churn=n_hc, repos_with_a_cluster_of_ten=big, with_bridge=n_b, with_cluster=n_g,
            rows=len(rows[:BRIDGE_ROWS]), rows_max=BRIDGE_ROWS, rows_left_out=rows[BRIDGE_ROWS:],
            **({"lead_repo": lead[0][0], "lead_file": lead[0][1]["file"], "lead_share": lead[0][1]["share_pct"],
                "lead_cluster": lead[0][1]["cluster"], "lead_cut": lead[0][1]["cut_vertex"]} if lead else {}),
            cut_vertices=fv["jaccard"]["cut_vertices"], from_stage="bridges",
        )

    # Idle forks and second account
    idle = [r["name"] for r in ds["repos"] if r.get("kind") == "fork-idle"]
    sig("idle_forks", "process", f"{len(idle)} forks have no commits from the family: {', '.join(idle)}.", forks=idle)
    hs = collections.Counter(n for n, c in own if c["who"] == "hagsmac")
    if hs:
        sig("second_account", "identity", "The second account hagsmac committed to " + ", ".join(f"{n} ({k})" for n, k in hs.items()) + ".", repos=dict(hs))

    # Daily series for the views
    daily = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for n, c in own:
        daily[_day(c["t"])][n][c["cls"]] += 1

    per_repo = {}
    for r in repos:
        oc = [c for c in r.get("commits", []) if not c.get("upstream")]
        per_repo[r["name"]] = {
            "kind": r.get("kind"),
            "visibility": r.get("visibility"),
            "role": r.get("role"),
            "own_commits": len(oc),
            "by_class": dict(collections.Counter(c["cls"] for c in oc)),
            "lines": {"add": sum(c["add"] for c in oc), "del": sum(c["del"] for c in oc)},
            "first": _day(min(c["t"] for c in oc)) if oc else None,
            "last": _day(max(c["t"] for c in oc)) if oc else None,
            "active_days": r.get("active_days"),
            "streak_days": r.get("streak_days"),
            "peak_day": r.get("peak_day"),
            "files": tree.get(r["name"], {}).get("files", 0),
            "lines_at_tip": tree.get(r["name"], {}).get("lines", 0),
            "tests": tree.get(r["name"], {}).get("tests", 0),
            "x3d_files": tree.get(r["name"], {}).get("x3d_files", 0),
            "branches": r.get("branch_count", 0),
            "by_kind": tree.get(r["name"], {}).get("by_kind", {}),
            "coverage": {
                "history": ("family commits only" if r.get("kind") == "fork-idle" else "all refs") if r.get("present") else "none",
                "tree": ("counts only (private)" if r.get("visibility") == "private" else
                         ("measured" if tree.get(r["name"], {}).get("files") else "not harvested")),
                "visibility": r.get("visibility"),
            },
        }
    return {
        "family": ds.get("family"),
        "signals": signals,
        "per_repo": per_repo,
        "daily": {d: {n: dict(cc) for n, cc in v.items()} for d, v in sorted(daily.items())},
    }
