#!/usr/bin/env python3
"""Uploads the rendered slides to the repo's `media` branch so Instagram can fetch them.

Why a branch: Buffer/Instagram need a public https URL for every image, and a
public GitHub repo serves branch files for free at
raw.githubusercontent.com/<owner>/<repo>/<branch>/<path>.

The branch is rebuilt as ONE fresh commit on every run (old folders kept for
KEEP_DAYS, then dropped), so the repository never grows with image history.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
PENDING_FILE = ROOT / "pending_post.json"
BRANCH = "media"
KEEP_DAYS = 30
DATE_PREFIX = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-")


def _git(args: list, cwd: Path, secret: str = "") -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip()
        if secret:
            msg = msg.replace(secret, "***")
        raise RuntimeError(f"git {args[0]} failed: {msg}")
    return proc.stdout


def prune_old(root: Path, keep_days: int, now: datetime) -> list:
    """Delete dated folders (YYYY-MM-DD-slug) older than keep_days. Returns the names removed."""
    removed = []
    cutoff = now - timedelta(days=keep_days)
    for child in root.iterdir():
        m = DATE_PREFIX.match(child.name)
        if child.is_dir() and m:
            day = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
            if day < cutoff:
                shutil.rmtree(child)
                removed.append(child.name)
    return removed


def publish_media(src_root: Path, ids: list, remote_url: str, *, keep_days: int = KEEP_DAYS,
                  now: datetime | None = None, token: str = "") -> None:
    """Add src_root/<id>/* to the media branch of `remote_url` (force-pushed as a single commit)."""
    now = now or datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "media"
        work.mkdir()
        _git(["init", "-q", "-b", BRANCH], work)
        _git(["remote", "add", "origin", remote_url], work, token)
        try:   # keep what earlier runs published (the branch may not exist yet)
            _git(["fetch", "-q", "--depth=1", "origin", BRANCH], work, token)
            _git(["checkout", "-q", "-f", "FETCH_HEAD", "--", "."], work, token)
        except RuntimeError:
            pass
        prune_old(work, keep_days, now)
        for cid in ids:
            dest = work / cid
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src_root / cid, dest)
        _git(["checkout", "-q", "--orphan", "fresh"], work)
        _git(["add", "-A"], work)
        _git(["-c", "user.name=gamedev-bot", "-c", "user.email=actions@github.com",
              "commit", "-q", "-m", "Publish carousel media"], work)
        _git(["push", "-q", "--force", "origin", f"fresh:{BRANCH}"], work, token)


def main() -> int:
    if not PENDING_FILE.exists():
        print("[info] no prepared carousel; nothing to upload")
        return 0
    pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repo or not token:
        print("[error] GITHUB_REPOSITORY and GITHUB_TOKEN are required", file=sys.stderr)
        return 1
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
    try:
        publish_media(ROOT / "out", [pending["id"]], remote, token=token)
    except (RuntimeError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(f"[ok] uploaded {len(pending['files'])} slides to the '{BRANCH}' branch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
