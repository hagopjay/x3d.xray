"""Thin, deterministic wrappers around git plumbing."""

from __future__ import annotations

import os
import subprocess

SEP_REC = "\x1e"
SEP_FLD = "\x1f"
SEP_LIST = "\x1d"


def read_environment() -> dict:
    """Read the explicitly selected checkout, ignoring ambient repository overrides."""
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE"):
        env.pop(key, None)
    env.update(GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    return env


def git(repo: str, *args: str, timeout: int = 600, check: bool = True) -> str:
    env = read_environment()
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


def git_bytes(repo: str, *args: str, timeout: int = 600) -> bytes:
    """Git's NUL-delimited machine output, without text decoding or newline rewriting."""
    proc = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, timeout=timeout,
        env=read_environment(),
    )
    if proc.returncode:
        raise RuntimeError(f"git {args[0]} failed: {proc.stderr.decode('utf-8', errors='replace').strip()[:400]}")
    return proc.stdout


def local_git_bytes(repo: str, *args: str, timeout: int = 120) -> bytes:
    """Strict local object reads with replacements/lazy fetch disabled.

    Explicit --no-lazy-fetch fails closed on Git versions that do not support
    it, rather than silently allowing a partial clone to fetch remote objects.
    Path arguments are literal and commands are passed without a shell.
    """
    proc = subprocess.run(["git", "--no-lazy-fetch", "--no-replace-objects", "--literal-pathspecs", "-C", repo, *args],
                          capture_output=True, timeout=timeout, env=read_environment())
    if proc.returncode:
        raise RuntimeError("local Git object read failed")
    return proc.stdout


def is_repository(repo: str) -> bool:
    """A worktree root, including linked worktrees whose .git entry is a file.

    Bare repositories and arbitrary subdirectories are not checkout roots.
    """
    if not os.path.isdir(repo):
        return False
    top = git(repo, "rev-parse", "--show-toplevel", check=False).strip()
    return bool(top) and os.path.realpath(top) == os.path.realpath(repo)


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

# Fixed-count NUL fields let subjects contain the old separators and let paths
# contain any non-NUL byte, including tabs and newlines. "commit" cannot be a
# numstat token: those start with two decimal/binary counts and tabs.
LOG_FORMAT_NUL = "%x00commit%x00" + "%x00".join([
    "%H", "%P", "%at", "%an", "%ae", "%s",
    "%(trailers:key=Co-Authored-By,valueonly,separator=%x1d)",
    "%(trailers:key=Claude-Session,valueonly,separator=%x1d)",
]) + "%x00"


def log_with_numstat(repo: str, refs: list[str], author_regex: str | None = None, timeout: int = 900):
    """Yield one dict per commit with its numstat rows.

    Rows are (adds, dels, path); adds/dels are None for binary files.
    """
    args = ["log", *refs, "--no-renames", "--numstat", "-z", "--date-order", f"--format={LOG_FORMAT_NUL}"]
    if author_regex:
        args += ["--perl-regexp", f"--author={author_regex}"]
    tokens = git_bytes(repo, *args, timeout=timeout).split(b"\0")
    i = 0
    current = None
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token == b"commit":
            if current is not None:
                yield current
            if i + 8 > len(tokens):
                raise RuntimeError("truncated git log metadata")
            fields = [x.decode("utf-8", errors="replace") for x in tokens[i:i + 8]]
            i += 8
            sha, parents, at, an, ae, subject, coauth, session = fields
            current = {
                "sha": sha,
                "parents": parents.split() if parents else [],
                "t": int(at),
                "author_name": an,
                "author_email": ae,
                "subject": subject,
                "coauthors": [c.strip() for c in coauth.split(SEP_LIST) if c.strip()],
                "sessions": [s.strip() for s in session.split(SEP_LIST) if s.strip()],
                "numstat": [],
            }
        elif token.strip(b"\n"):
            if current is None:
                raise RuntimeError("git numstat row precedes commit metadata")
            parts = token.lstrip(b"\n").split(b"\t", 2)
            if len(parts) != 3:
                raise RuntimeError("invalid git numstat record")
            a, d, path = parts
            current["numstat"].append((None if a == b"-" else int(a), None if d == b"-" else int(d), os.fsdecode(path)))
    if current is not None:
        yield current


def ls_tree_entries(repo: str, ref: str):
    """Yield (mode, blob_oid, size, path), preserving literal Git path bytes."""
    out = git_bytes(repo, "ls-tree", "-r", "-l", "-z", ref)
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.partition(b"\t")
        parts = meta.split()
        if len(parts) < 4 or parts[1] != b"blob":
            continue
        size = int(parts[3]) if parts[3].isdigit() else 0
        yield parts[0].decode("ascii"), parts[2].decode("ascii"), size, os.fsdecode(path)


def ls_tree(repo: str, ref: str):
    """Yield (blob_oid, size, path) for every blob in the tree of ref."""
    for _mode, oid, size, path in ls_tree_entries(repo, ref):
        yield oid, size, path


def cat_blob(repo: str, blob: str, timeout: int = 120) -> bytes:
    env = read_environment()
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
