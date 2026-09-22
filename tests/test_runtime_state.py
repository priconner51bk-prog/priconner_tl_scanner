"""Tests for runtime state persistence and the scheduled-window wrapper."""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import monitor_runner
import runtime_utils


def _fake_command_runner(returncode=0):
    calls = []

    def runner(command, cwd=None, check=False):
        calls.append(command)
        return SimpleNamespace(returncode=returncode)

    return runner, calls


def test_state_store_writes_sorted_json_with_updated_at():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        store = monitor_runner.StateStore(path)
        store.update(status="running", stage="worrychefs")
        data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "running"
    assert data["stage"] == "worrychefs"
    assert data["updated_at"].endswith("+00:00")


def test_state_store_overwrites_on_success():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        store = monitor_runner.StateStore(path)
        store.update(status="running", stage="worrychefs")
        store.update(status="success", stage="completed", exit_code=0)
        data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "success"
    assert data["exit_code"] == 0


def test_run_stages_records_failure_and_stops_later_stages():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        result = monitor_runner.run_stages(
            ("worrychefs", "youtube-search"),
            directory,
            command_runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=3),
            root_dir=Path.cwd(),
        )
        data = json.loads(path.read_text(encoding="utf-8"))
    assert result == 3
    assert data["status"] == "failed"
    assert data["failure_stage"] == "worrychefs"
    assert data["exit_code"] == 3


def test_default_runtime_dir_prefers_explicit_env():
    with patch.dict(os.environ, {"PRICONNER_MONITOR_RUNTIME_DIR": "/tmp/rt"}):
        assert runtime_utils.default_runtime_dir() == Path("/tmp/rt")


def test_default_runtime_dir_prefers_xdg_state_home():
    with patch.dict(
        os.environ,
        {"XDG_STATE_HOME": "/tmp/state"},
        clear=False,
    ):
        os.environ.pop("PRICONNER_MONITOR_RUNTIME_DIR", None)
        assert runtime_utils.default_runtime_dir() == Path("/tmp/state") / "priconner-tl-scanner"


def test_monitor_runner_main_returns_zero_on_success():
    with tempfile.TemporaryDirectory() as directory, patch.object(
        monitor_runner, "run_stages", return_value=0
    ) as run_stages:
        result = monitor_runner.main(
            ["--runtime-dir", directory, "--stages", "worrychefs"]
        )
    assert result == 0
    run_stages.assert_called_once()


def test_monitor_runner_month_options_set_trial_environment():
    with tempfile.TemporaryDirectory() as directory, patch.object(
        monitor_runner, "run_stages", return_value=0
    ), patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PRICONNER_YOUTUBE_PERIOD_MONTH", None)
        os.environ.pop("PRICONNER_YOUTUBE_CONTENT_MONTH", None)
        result = monitor_runner.main([
            "--runtime-dir", directory,
            "--stages", "youtube-search",
            "--period-month", "2026-08",
            "--content-month", "2026-08",
        ])
        assert result == 0
        assert os.environ["PRICONNER_YOUTUBE_PERIOD_MONTH"] == "2026-08"
        assert os.environ["PRICONNER_YOUTUBE_CONTENT_MONTH"] == "2026-08"


def test_run_stages_passes_shared_queue_path_to_children(tmp_path):
    calls = []

    def command_runner(command, cwd=None, check=False, env=None):
        calls.append(env)
        return SimpleNamespace(returncode=0)

    queue_path = Path(tmp_path) / "discord_queue.sqlite3"
    with patch.object(
        monitor_runner.discord_queue,
        "drain",
        return_value={
            "sent": 0,
            "retried": 0,
            "failed": 0,
            "remaining": 0,
        },
    ):
        assert monitor_runner.run_stages(
            ("discord-channel",),
            Path(tmp_path) / "discord-channel",
            command_runner=command_runner,
            root_dir=Path.cwd(),
            queue_path=queue_path,
            shared_runtime_dir=Path(tmp_path),
        ) == 0

    assert calls[0]["PRICONNER_DISCORD_QUEUE_DB"] == str(queue_path.resolve())
    assert calls[0]["PRICONNER_YOUTUBE_HANDOFF_PATH"] == str(
        (Path(tmp_path) / "youtube_url_handoff.json").resolve()
    )


def test_queue_summary_is_created_before_drain(tmp_path, monkeypatch):
    events = []
    queued_posts = [
        {
            "channel_key": "boss1_tl",
            "content": "動画タイトル: 検索TL\n動画URL: https://youtu.be/search",
        },
        {
            "channel_key": "boss1_tl",
            "content": "動画タイトル: 検索TL\n動画URL: https://youtu.be/search",
        },
    ]
    monkeypatch.setattr(
        monitor_runner.discord_queue,
        "queued_post_items",
        lambda **_kwargs: queued_posts,
    )
    monkeypatch.setattr(
        monitor_runner.discord_queue,
        "drain",
        lambda **_kwargs: events.append("drain") or {
            "sent": 0, "retried": 0, "failed": 0, "remaining": 0,
        },
    )
    import discord_utils

    monkeypatch.setattr(
        discord_utils,
        "notify_summary_to_configured_guilds",
        lambda content, **_kwargs: events.append(content),
    )

    result = monitor_runner.run_stages(
        ("youtube-search",),
        tmp_path,
        command_runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
        root_dir=Path.cwd(),
        queue_path=tmp_path / "queue.sqlite3",
    )

    assert result == 0
    assert len(events) == 2
    assert "ボス1: 1件" in events[0]
    assert events[1] == "drain"


def test_monitor_runner_main_skips_when_lock_busy():
    with tempfile.TemporaryDirectory() as directory, patch.object(
        monitor_runner, "acquire_lock", side_effect=runtime_utils.LockBusy()
    ):
        result = monitor_runner.main(
            ["--runtime-dir", directory, "--stages", "worrychefs"]
        )
    assert result == 0


def test_monitor_runner_main_returns_two_for_unknown_stage():
    with tempfile.TemporaryDirectory() as directory:
        result = monitor_runner.main(
            ["--runtime-dir", directory, "--stages", "bogus"]
        )
    assert result == 2
