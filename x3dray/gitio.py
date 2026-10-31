"""Thin, deterministic wrappers around git plumbing."""

from __future__ import annotations

import os
import subprocess

SEP_REC = "\x1e"
SEP_FLD = "\x1f"
SEP_LIST = "\x1d"


def git(repo: str, *args: str, timeout: int = 600, check: bool = True) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        env=env,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()[:400]}")
    return proc.stdout


def default_ref(repo: str) -> str:
    """The remote default branch (origin/HEAD), falling back to HEAD."""
    out = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False).strip()
    return out or "HEAD"


def rev_set(repo: str, *refs: str) -> set[str]:
    out = git(repo, "rev-list", *refs, check=False)
    return {line.strip() for line in out.splitlines() if line.strip()}


LOG_FORMAT = SEP_REC + SEP_FLD.join(
    [
        "%H",
        "%P",
        "%at",
        "%an",
        "%ae",
        "%s",
        "%(trailers:key=Co-Authored-By,valueonly,separator=%x1d)",
        "%(trailers:key=Claude-Session,valueonly,separator=%x1d)",
    ]
)


def log_with_numstat(repo: str, refs: list[str], author_regex: str | None = None, timeout: int = 900):
    """Yield one dict per commit with its numstat rows.

    Rows are (adds, dels, path); adds/dels are None for binary files.
    """
    args = ["log", *refs, "--no-renames", "--numstat", "--date-order", f"--format={LOG_FORMAT}"]
    if author_regex:
        args += ["--perl-regexp", f"--author={author_regex}"]
    out = git(repo, *args, timeout=timeout)
    for chunk in out.split(SEP_REC):
        if not chunk.strip():
            continue
        head, _, rest = chunk.partition("\n")
        fields = head.split(SEP_FLD)
        if len(fields) < 8:
            continue
        sha, parents, at, an, ae, subject, coauth, session = fields[:8]
        rows = []
        for line in rest.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            rows.append((None if a == "-" else int(a), None if d == "-" else int(d), path))
        yield {
            "sha": sha,
            "parents": parents.split() if parents else [],
            "t": int(at),
            "author_name": an,
            "author_email": ae,
            "subject": subject,
            "coauthors": [c.strip() for c in coauth.split(SEP_LIST) if c.strip()],
            "sessions": [s.strip() for s in session.split(SEP_LIST) if s.strip()],
            "numstat": rows,
        }


def ls_tree(repo: str, ref: str):
    """Yield (blob_sha, size, path) for every blob in the tree of ref."""
    out = git(repo, "ls-tree", "-r", "-l", "-z", ref)
    for rec in out.split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        parts = meta.split()
        if len(parts) < 4 or parts[1] != "blob":
            continue
        size = int(parts[3]) if parts[3].isdigit() else 0
        yield parts[2], size, path


def cat_blob(repo: str, blob: str, timeout: int = 120) -> bytes:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    proc = subprocess.run(
        ["git", "-C", repo, "cat-file", "blob", blob], capture_output=True, timeout=timeout, env=env
    )
    return proc.stdout if proc.returncode == 0 else b""


def branches(repo: str):
    out = git(
        repo,
        "for-each-ref",
        "refs/remotes/origin",
        f"--format=%(refname:short){SEP_FLD}%(committerdate:unix){SEP_FLD}%(objectname)",
    )
    for line in out.splitlines():
        name, ts, obj = (line.split(SEP_FLD) + ["", "", ""])[:3]
        if name.endswith("/HEAD") or name == "origin":
            continue
        yield name.removeprefix("origin/"), int(ts or 0), obj
