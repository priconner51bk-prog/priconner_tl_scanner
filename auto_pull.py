"""Safely fast-forward the local checkout from GitHub.

The checkout is never overwritten: local changes cause the update to be
skipped, and non-fast-forward histories are reported as an error.
"""

import os
import subprocess
import sys
from pathlib import Path

from runtime_utils import LockBusy, acquire_lock, default_runtime_dir

ROOT_DIR = Path(__file__).resolve().parent


def run_git(*args, git_runner=subprocess.run):
    return git_runner(
        ["git", *args],
        cwd=ROOT_DIR,
        check=False,
        capture_output=True,
        text=True,
    )


def current_branch(git_runner=subprocess.run):
    result = run_git("symbolic-ref", "--short", "HEAD", git_runner=git_runner)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to determine current branch")
    return result.stdout.strip()


def update_checkout(git_runner=subprocess.run, branch=None):
    branch = branch or os.environ.get("PRICONNER_GIT_BRANCH") or current_branch(git_runner)
    status = run_git("status", "--porcelain", git_runner=git_runner)
    if status.returncode != 0:
        print(status.stderr.strip(), file=sys.stderr)
        return 1
    if status.stdout:
        print("Skipping GitHub update: local changes are present.")
        return 0

    fetched = run_git("fetch", "--prune", "origin", branch, git_runner=git_runner)
    if fetched.returncode != 0:
        print(fetched.stderr.strip() or "git fetch failed", file=sys.stderr)
        return fetched.returncode or 1

    remote_ref = f"origin/{branch}"
    behind = run_git("rev-list", "--count", f"HEAD..{remote_ref}", git_runner=git_runner)
    if behind.returncode != 0:
        print(behind.stderr.strip() or f"Remote branch {remote_ref} was not found", file=sys.stderr)
        return behind.returncode or 1
    count = int(behind.stdout.strip() or "0")
    if count == 0:
        print(f"Already up to date with {remote_ref}.")
        return 0

    pulled = run_git("merge", "--ff-only", remote_ref, git_runner=git_runner)
    if pulled.returncode != 0:
        print(pulled.stderr.strip() or "Fast-forward update failed", file=sys.stderr)
        return pulled.returncode or 1
    print(f"Updated from {remote_ref}: {count} commit(s).")
    return 0


def main(argv=None):
    runtime_dir = default_runtime_dir()
    try:
        with acquire_lock(Path(runtime_dir) / "git_pull.lock"):
            return update_checkout()
    except LockBusy:
        print("Another GitHub update is already in progress; skipping.")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
