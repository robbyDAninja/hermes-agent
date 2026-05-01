"""Smoke tests for arlo.slack_write_approval (patches #4 + #11).

Three cases:
  - approve path: row UPDATEs to 'approved'; ack card posted via chat_update.
  - deny path:    row UPDATEs to 'denied';   ack card posted via chat_update.
  - stale click:  UPDATE returns no row; re-fetch yields existing row;
                  "Already resolved" stale card posted via chat_update.
"""
from __future__ import annotations

import datetime as _dt
import sys
from typing import Any

import pytest

from arlo import slack_write_approval


# --- Fake psycopg ---------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows_in_order: list):
        self._rows = list(rows_in_order)
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params: tuple = ()):
        self.executed.append((sql, params))

    def fetchone(self):
        if not self._rows:
            return None
        return self._rows.pop(0)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor


def _install_fake_psycopg(monkeypatch, rows_in_order: list) -> _FakeCursor:
    """Inject a fake psycopg module so ``import psycopg`` inside the shim
    returns our stub. Returns the cursor so the test can inspect what SQL
    was issued."""
    cursor = _FakeCursor(rows_in_order)

    class _Mod:
        @staticmethod
        def connect(dsn: str, autocommit: bool = False):
            assert dsn == "postgres://fake"
            return _FakeConn(cursor)

    monkeypatch.setitem(sys.modules, "psycopg", _Mod)
    return cursor


# --- Helpers --------------------------------------------------------------


def _body(message_ts: str = "1700000000.000200") -> dict:
    return {
        "user": {"id": "U_USER"},
        "channel": {"id": "C_CHANNEL"},
        "message": {"ts": message_ts, "thread_ts": "1700000000.000100"},
    }


# --- Tests ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_path_updates_row_and_posts_ack(slack_client, monkeypatch):
    cursor = _install_fake_psycopg(
        monkeypatch,
        rows_in_order=[("Edit summary text", "C_CHANNEL", "1700000000.000100")],
    )
    ack_calls: list = []

    async def _ack():
        ack_calls.append(True)

    await slack_write_approval.handle_action(
        ack=_ack,
        body=_body(),
        action={"action_id": "arlo_write_approve:abc-123"},
        client=slack_client,
        dsn="postgres://fake",
    )

    assert ack_calls == [True]
    # First DB call: UPDATE arlo.pending_writes -> 'approved'.
    update_sql, update_params = cursor.executed[0]
    assert "UPDATE arlo.pending_writes" in update_sql
    assert update_params == ("approved", "U_USER", "abc-123")
    # No INSERT into thread_write_policies for plain approve.
    assert not any(
        "thread_write_policies" in sql for sql, _ in cursor.executed
    )
    # Slack: chat_update with the approve ack text.
    names = [name for name, _ in slack_client.calls]
    assert names == ["chat_update"]
    _, kwargs = slack_client.calls[0]
    assert kwargs["channel"] == "C_CHANNEL"
    assert kwargs["ts"] == "1700000000.000200"
    assert "Approved by <@U_USER>" in kwargs["text"]


@pytest.mark.asyncio
async def test_deny_path_updates_row_and_posts_ack(slack_client, monkeypatch):
    cursor = _install_fake_psycopg(
        monkeypatch,
        rows_in_order=[("Edit summary text", "C_CHANNEL", "1700000000.000100")],
    )

    async def _ack():
        pass

    await slack_write_approval.handle_action(
        ack=_ack,
        body=_body(),
        action={"action_id": "arlo_write_deny:abc-123"},
        client=slack_client,
        dsn="postgres://fake",
    )

    update_sql, update_params = cursor.executed[0]
    assert "UPDATE arlo.pending_writes" in update_sql
    assert update_params == ("denied", "U_USER", "abc-123")
    names = [name for name, _ in slack_client.calls]
    assert names == ["chat_update"]
    _, kwargs = slack_client.calls[0]
    assert "Denied by <@U_USER>" in kwargs["text"]


@pytest.mark.asyncio
async def test_stale_click_posts_already_resolved(slack_client, monkeypatch):
    """When UPDATE returns no row (already approved/denied/expired),
    the shim re-fetches and posts an 'Already resolved' card."""
    resolved_at = _dt.datetime(2026, 5, 1, 17, 30, 0)
    cursor = _install_fake_psycopg(
        monkeypatch,
        rows_in_order=[
            None,  # UPDATE RETURNING -> no row (not pending)
            ("approved", "U_OTHER", resolved_at, "Edit summary"),  # SELECT existing
        ],
    )

    async def _ack():
        pass

    await slack_write_approval.handle_action(
        ack=_ack,
        body=_body(),
        action={"action_id": "arlo_write_approve:abc-123"},
        client=slack_client,
        dsn="postgres://fake",
    )

    # Two SQL statements: UPDATE + follow-up SELECT.
    assert len(cursor.executed) == 2
    assert "UPDATE arlo.pending_writes" in cursor.executed[0][0]
    assert "SELECT status, resolved_by" in cursor.executed[1][0]
    # Slack: chat_update with stale text.
    names = [name for name, _ in slack_client.calls]
    assert names == ["chat_update"]
    _, kwargs = slack_client.calls[0]
    assert "Already resolved" in kwargs["text"]
    assert "U_OTHER" in kwargs["text"]
