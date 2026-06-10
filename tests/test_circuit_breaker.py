"""Tests for core.circuit_breaker — crash-loop protection."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.circuit_breaker import (
    BACKOFF_SCHEDULE_S,
    RESET_WINDOW_S,
    _cb_path,
    _get_delay,
    _read_state,
    _write_state,
    enforce_startup_backoff,
    reset,
)


@pytest.fixture(autouse=True)
def _clean_breaker(tmp_path, monkeypatch):
    """Redirect the circuit breaker state to a temp dir."""
    fake_path = tmp_path / ".pulse" / "circuit-breaker.json"
    monkeypatch.setattr("core.circuit_breaker._cb_path", lambda: fake_path)
    yield
    fake_path.unlink(missing_ok=True)


class TestGetDelay:
    def test_first_attempt_no_delay(self):
        assert _get_delay(1) == 0

    def test_second_attempt_no_delay(self):
        assert _get_delay(2) == 0

    def test_third_attempt_10s(self):
        assert _get_delay(3) == 10

    def test_sixth_attempt_300s(self):
        assert _get_delay(6) == 300

    def test_beyond_schedule_caps_at_last(self):
        assert _get_delay(100) == BACKOFF_SCHEDULE_S[-1]


class TestStateReadWrite:
    def test_read_missing_returns_none(self):
        assert _read_state() is None

    def test_round_trip(self, tmp_path):
        _write_state(3, 1000.0)
        state = _read_state()
        assert state is not None
        assert state["attempt"] == 3
        assert state["timestamp"] == 1000.0


class TestEnforceStartupBackoff:
    @patch("core.circuit_breaker.time.sleep")
    def test_first_launch_no_delay(self, mock_sleep):
        attempt = enforce_startup_backoff()
        assert attempt == 1
        mock_sleep.assert_not_called()

    @patch("core.circuit_breaker.time.sleep")
    @patch("core.circuit_breaker.time.time")
    def test_rapid_crashes_increment_and_delay(self, mock_time, mock_sleep):
        # Simulate 3 rapid crashes within the reset window
        base = 1_000_000.0
        mock_time.return_value = base
        enforce_startup_backoff()  # attempt 1

        mock_time.return_value = base + 5  # 5s later
        enforce_startup_backoff()  # attempt 2

        mock_time.return_value = base + 10  # 10s later
        attempt = enforce_startup_backoff()  # attempt 3
        assert attempt == 3
        mock_sleep.assert_called_with(10)  # BACKOFF_SCHEDULE[2] = 10

    @patch("core.circuit_breaker.time.sleep")
    @patch("core.circuit_breaker.time.time")
    def test_crash_after_reset_window_resets_counter(self, mock_time, mock_sleep):
        # First crash
        base = 1_000_000.0
        mock_time.return_value = base
        enforce_startup_backoff()

        # Second crash way later — beyond RESET_WINDOW_S
        mock_time.return_value = base + RESET_WINDOW_S + 100
        attempt = enforce_startup_backoff()
        assert attempt == 1  # Counter reset
        mock_sleep.assert_not_called()


class TestReset:
    def test_reset_clears_state(self):
        _write_state(5, time.time())
        assert _read_state() is not None
        reset()
        assert _read_state() is None

    def test_reset_on_missing_file_is_noop(self):
        # Should not raise
        reset()
