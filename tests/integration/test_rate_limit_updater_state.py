"""Integration tests for updater state.

Covers:
  - updater/state.py       — UpdateState, UpdateStateStore
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from file_organizer.updater.state import UpdateState, UpdateStateStore

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# UpdateState
# ---------------------------------------------------------------------------


class TestUpdateState:
    def test_created(self) -> None:
        state = UpdateState()
        assert state is not None

    def test_last_checked_at_empty_returns_none(self) -> None:
        state = UpdateState()
        assert state.last_checked_at() is None

    def test_last_checked_at_valid_iso(self) -> None:
        ts = datetime.now(UTC).isoformat()
        state = UpdateState(last_checked=ts)
        result = state.last_checked_at()
        assert result is not None
        assert isinstance(result, datetime)

    def test_last_checked_at_invalid_returns_none(self) -> None:
        state = UpdateState(last_checked="not-a-date")
        assert state.last_checked_at() is None

    def test_due_no_interval_returns_true(self) -> None:
        state = UpdateState()
        assert state.due(0) is True

    def test_due_never_checked_returns_true(self) -> None:
        state = UpdateState()
        assert state.due(24) is True

    def test_due_recently_checked_returns_false(self) -> None:
        now = datetime.now(UTC)
        state = UpdateState(last_checked=now.isoformat())
        assert state.due(24, now=now) is False

    def test_due_old_check_returns_true(self) -> None:
        old = datetime.now(UTC) - timedelta(hours=25)
        state = UpdateState(last_checked=old.isoformat())
        assert state.due(24) is True


# ---------------------------------------------------------------------------
# UpdateStateStore
# ---------------------------------------------------------------------------


class TestUpdateStateStore:
    def test_created(self, tmp_path: Path) -> None:
        store = UpdateStateStore(state_path=tmp_path / "state.json")
        assert store is not None

    def test_state_path_property(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        store = UpdateStateStore(state_path=p)
        assert store.state_path == p

    def test_load_missing_returns_default(self, tmp_path: Path) -> None:
        store = UpdateStateStore(state_path=tmp_path / "missing.json")
        state = store.load()
        assert isinstance(state, UpdateState)
        assert state.last_checked == ""

    def test_save_and_load(self, tmp_path: Path) -> None:
        store = UpdateStateStore(state_path=tmp_path / "state.json")
        now_ts = datetime.now(UTC).isoformat()
        original = UpdateState(last_checked=now_ts, last_version="1.2.3")
        store.save(original)
        loaded = store.load()
        assert loaded.last_version == "1.2.3"

    def test_load_corrupt_returns_default(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        p.write_text("not json", encoding="utf-8")
        store = UpdateStateStore(state_path=p)
        state = store.load()
        assert state.last_checked == ""

    def test_load_non_dict_returns_default(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        store = UpdateStateStore(state_path=p)
        state = store.load()
        assert state.last_checked == ""

    def test_record_check_saves_state(self, tmp_path: Path) -> None:
        store = UpdateStateStore(state_path=tmp_path / "state.json")
        now = datetime.now(UTC)
        state = store.record_check("2.0.0", now=now)
        assert state.last_version == "2.0.0"

    def test_record_check_persists(self, tmp_path: Path) -> None:
        store = UpdateStateStore(state_path=tmp_path / "state.json")
        store.record_check("1.5.0")
        loaded = store.load()
        assert loaded.last_version == "1.5.0"

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep_path = tmp_path / "nested" / "dir" / "state.json"
        store = UpdateStateStore(state_path=deep_path)
        store.save(UpdateState(last_version="1.0.0"))
        assert deep_path.exists()
