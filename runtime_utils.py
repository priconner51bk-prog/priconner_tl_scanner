"""Shared runtime helpers for safe monitor execution."""

import os
import sys
from contextlib import contextmanager
from pathlib import Path

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class LockBusy(Exception):
    """Raised when another local monitor process owns the lock."""


RUNTIME_DIR_ENV = "PRICONNER_MONITOR_RUNTIME_DIR"


def default_runtime_dir():
    configured_runtime_dir = os.environ.get(RUNTIME_DIR_ENV)
    if configured_runtime_dir:
        return Path(configured_runtime_dir).expanduser()
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "priconner-tl-movie-scanner"
    return Path.home() / ".local" / "state" / "priconner-tl-movie-scanner"


@contextmanager
def acquire_lock(lock_path):
    """Acquire a non-blocking process lock and release it on every exit path."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_file = lock_path.open("w")
    except OSError as error:
        # Windows may reject a second open of an already locked file before
        # msvcrt.locking() gets a chance to report the contention.
        raise LockBusy from error

    locked = False
    try:
        try:
            if sys.platform == "win32":
                # msvcrt.locking() locks a byte range and requires at least
                # one byte to exist in the file.
                lock_file.write("0")
                lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except (BlockingIOError, OSError) as error:
            raise LockBusy from error
        try:
            yield
        finally:
            if locked and sys.platform == "win32":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            elif locked:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        try:
            lock_file.close()
        except OSError:
            # A contended Windows handle can reject close() after the failed
            # non-blocking lock attempt; the OS releases it with the process.
            pass


def run_locked(callback, runtime_dir=None, lock_name="monitor.lock"):
    """Run a standalone stage under its own non-blocking process lock."""
    try:
        with acquire_lock(Path(runtime_dir or default_runtime_dir()) / lock_name):
            return callback()
    except LockBusy:
        print("Another monitor run is already in progress; skipping.")
        return 0
