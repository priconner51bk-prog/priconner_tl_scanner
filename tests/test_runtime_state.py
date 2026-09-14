"""Tests for runtime state persistence and the scheduled-window wrapper."""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import monitor_runner
import runtime_utils
import scheduled_monitor


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


def test_scheduled_monitor_main_delegates_when_in_window():
    with patch.object(scheduled_monitor, "monitor_runner") as monitor_runner:
        result = scheduled_monitor.main(["--date", "2026-10-25"])
    assert result == monitor_runner.main.return_value
    monitor_runner.main.assert_called_once()


def test_scheduled_monitor_main_skips_when_outside_window():
    with patch.object(scheduled_monitor, "monitor_runner") as monitor_runner:
        result = scheduled_monitor.main(["--date", "2026-10-20"])
    assert result == 0
    monitor_runner.main.assert_not_called()


