"""Run the scheduled monitoring stages and persist non-secret runtime state."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from configparser import ConfigParser
from datetime import datetime, timezone
from pathlib import Path

import discord_queue
from runtime_utils import LockBusy, acquire_lock, default_runtime_dir

WINDOWLESS_SUBPROCESS_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_STAGES = ("youtube-channel", "youtube-search", "worrychefs", "discord-channel")
STAGE_SCRIPTS = {
    "youtube-channel": "youtube_channel.py",
    "youtube-search": "youtube_search.py",
    "worrychefs": "worrychefs.py",
    "discord-channel": "discord_channel.py",
}


def _unique_queued_posts(items):
    """Collapse one logical post duplicated for multiple destination guilds."""
    unique = []
    seen = set()
    for item in items:
        key = (item.get("channel_key", ""), item.get("content", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _queue_post_summary(queue_path, created_after):
    """Summarize this run's queued posts before the queue is drained."""
    queued_posts = discord_queue.queued_post_items(
        path=queue_path,
        created_after=created_after,
    )
    posts = _unique_queued_posts(queued_posts)
    if not posts:
        return False

    # Keep the existing summary formatter as the single source of formatting
    # rules; queue rows already contain the exact text about to be sent.
    import discord_utils
    from discord_channel import _build_post_summary

    discord_utils.notify_summary_to_configured_guilds(
        _build_post_summary(posts),
        path=queue_path,
    )
    print(f"Discord投稿サマリーを作成: 対象{len(posts)}件")
    return True


def _monitor_config_value(key, fallback=None):
    config = ConfigParser()
    config_path = ROOT_DIR / "config.ini"
    if config_path.exists():
        config.read(config_path, encoding="utf-8")
    value = config.get("monitor", key, fallback=fallback)
    return value.strip() if value else fallback


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
    stages = tuple(
        dict.fromkeys(item.strip() for item in value.split(",") if item.strip())
    )
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
    queue_path=None,
    shared_runtime_dir=None,
):
    runtime_dir = Path(runtime_dir).expanduser().resolve()
    shared_runtime_dir = Path(shared_runtime_dir or runtime_dir).expanduser().resolve()
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

    queue_path = (
        Path(queue_path).expanduser().resolve()
        if queue_path is not None
        else runtime_dir / "discord_queue.sqlite3"
    )
    run_started_at = time.time()

    for stage in stages:
        store.update(status="running", stage=stage, failure_stage=None)
        command = [python_executable, str(Path(root_dir) / STAGE_SCRIPTS[stage])]
        try:
            child_environment = os.environ.copy()
            child_environment["PRICONNER_MONITOR_RUNTIME_DIR"] = str(runtime_dir)
            child_environment["PRICONNER_DISCORD_QUEUE_DB"] = str(queue_path)
            child_environment["PRICONNER_YOUTUBE_HANDOFF_PATH"] = str(
                shared_runtime_dir / "youtube_url_handoff.json"
            )
            command_options = {
                "cwd": root_dir,
                "check": False,
                "env": child_environment,
            }
            if command_runner is subprocess.run:
                command_options["creationflags"] = WINDOWLESS_SUBPROCESS_FLAGS
            result = command_runner(command, **command_options)
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

    try:
        _queue_post_summary(queue_path, run_started_at)
    except Exception as error:  # noqa: BLE001 - summary failure is a run failure
        print(f"Discord投稿サマリー作成に失敗: {error}", file=sys.stderr)
        store.update(
            status="failed",
            stage="discord-summary",
            failure_stage="discord-summary",
            finished_at=store.clock().isoformat(),
            exit_code=1,
        )
        return 1

    try:
        queue_result = discord_queue.drain(path=queue_path)
    except Exception as error:  # noqa: BLE001 - keep queue failures in monitor state
        print(f"Discordキュー送信に失敗: {error}", file=sys.stderr)
        store.update(
            status="failed",
            stage="discord-queue",
            failure_stage="discord-queue",
            finished_at=store.clock().isoformat(),
            exit_code=1,
        )
        return 1
    print(
        "Discordキュー: "
        f"送信{queue_result['sent']}件 再試行待ち{queue_result['retried']}件 "
        f"失敗{queue_result['failed']}件 残り{queue_result['remaining']}件"
    )
    if queue_result["failed"]:
        store.update(
            status="failed",
            stage="discord-queue",
            failure_stage="discord-queue",
            finished_at=store.clock().isoformat(),
            exit_code=1,
        )
        return 1

    store.update(
        status="success",
        stage="completed",
        failure_stage=None,
        finished_at=store.clock().isoformat(),
        exit_code=0,
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", type=Path, default=None)
    parser.add_argument(
        "--stages",
        default=None,
    )
    parser.add_argument("--period-month", default=None,
                        help="試験用のYouTube公開対象月 (YYYY-MM)")
    parser.add_argument("--content-month", default=None,
                        help="試験用のクラバト内容月 (YYYY-MM)")
    args = parser.parse_args(argv)
    runtime_dir = args.runtime_dir
    if runtime_dir is None:
        runtime_dir = Path(
            os.environ.get("PRICONNER_MONITOR_RUNTIME_DIR")
            or _monitor_config_value("runtime_dir")
            or default_runtime_dir()
        ).expanduser()
    stages_value = args.stages
    if stages_value is None:
        stages_value = os.environ.get("PRICONNER_MONITOR_STAGES") or _monitor_config_value(
            "stages", ",".join(DEFAULT_STAGES)
        )
    try:
        stages = parse_stages(stages_value)
        common_runtime_dir = runtime_dir.resolve()
        state_runtime_dir = common_runtime_dir
        if len(stages) == 1:
            state_runtime_dir = common_runtime_dir / stages[0]
        queue_path = common_runtime_dir / "discord_queue.sqlite3"
        lock_name = "monitor_runner-" + "-".join(stages) + ".lock"
        with acquire_lock(state_runtime_dir / lock_name):
            legacy_queue_path = state_runtime_dir / "discord_queue.sqlite3"
            if legacy_queue_path != queue_path:
                migrated = discord_queue.migrate_queue(legacy_queue_path, queue_path)
                if migrated["migrated"]:
                    print(
                        "Discordキュー移行: "
                        f"{migrated['migrated']}件を共通DBへ移行"
                    )
            if args.period_month:
                os.environ["PRICONNER_YOUTUBE_PERIOD_MONTH"] = args.period_month
            if args.content_month:
                os.environ["PRICONNER_YOUTUBE_CONTENT_MONTH"] = args.content_month
            return run_stages(
                stages,
                state_runtime_dir,
                queue_path=queue_path,
                shared_runtime_dir=common_runtime_dir,
            )
    except LockBusy:
        print("Another monitor run is already in progress; skipping.")
        return 0
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
