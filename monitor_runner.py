"""Run the scheduled monitoring stages and persist non-secret runtime state."""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_STAGES = ("youtube-channel", "youtube-search", "worrychefs")
STAGE_SCRIPTS = {
    "youtube-channel": "youtube_channel.py",
    "youtube-search": "youtube_search.py",
    "worrychefs": "worrychefs.py",
}


class LockBusy(Exception):
    """Raised when another local monitor process owns the lock."""


@contextmanager
def acquire_lock(lock_path):
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LockBusy from error
        yield


class StateStore:
    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def update(self, **fields):
        state = getattr(self, "state", {}).copy()
        state.update(fields)
        state["updated_at"] = self.clock().isoformat()
        self.state = state
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
                json.dump(state, temporary_file, ensure_ascii=False, sort_keys=True)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)


def parse_stages(value):
    stages = tuple(item.strip() for item in value.split(",") if item.strip())
    invalid = [stage for stage in stages if stage not in STAGE_SCRIPTS]
    if invalid:
        raise ValueError(f"Unknown monitor stage: {invalid[0]}")
    if not stages:
        raise ValueError("At least one monitor stage is required")
    return stages


def run_stages(
    stages,
    runtime_dir,
    command_runner=subprocess.run,
    python_executable=sys.executable,
    root_dir=ROOT_DIR,
    clock=None,
):
    runtime_dir = Path(runtime_dir)
    store = StateStore(runtime_dir / "state.json", clock=clock)
    started_at = (clock or (lambda: datetime.now(timezone.utc)))().isoformat()
    store.update(
        status="running",
        stage="starting",
        started_at=started_at,
        finished_at=None,
        failure_stage=None,
        exit_code=None,
    )

    for stage in stages:
        store.update(status="running", stage=stage, failure_stage=None)
        command = [python_executable, str(Path(root_dir) / STAGE_SCRIPTS[stage])]
        try:
            result = command_runner(command, cwd=root_dir, check=False)
            exit_code = result.returncode
        except OSError:
            exit_code = 127
        if exit_code != 0:
            store.update(
                status="failed",
                stage=stage,
                failure_stage=stage,
                finished_at=store.clock().isoformat(),
                exit_code=exit_code,
            )
            return exit_code

    store.update(
        status="success",
        stage="completed",
        failure_stage=None,
        finished_at=store.clock().isoformat(),
        exit_code=0,
    )
    return 0


def default_runtime_dir():
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "priconner-tl-new-arrivals"
    return Path.home() / ".local" / "state" / "priconner-tl-new-arrivals"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", type=Path, default=default_runtime_dir())
    parser.add_argument(
        "--stages",
        default=os.environ.get("PRICONNER_MONITOR_STAGES", ",".join(DEFAULT_STAGES)),
    )
    args = parser.parse_args(argv)
    try:
        stages = parse_stages(args.stages)
        return run_stages(stages, args.runtime_dir)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
