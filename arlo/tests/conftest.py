"""Shared fixtures for arlo/* smoke tests.

Two fixture families:
  - fake Slack client: a thin async stub that records calls (chat_postMessage,
    assistant_threads_setStatus, views_publish, chat_update). Tests assert on
    the recorded calls instead of hitting Slack.
  - fake DSN: an env-var fixture that points psycopg.connect at a sqlite-
    backed shim or skips DB-touching tests when not available.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest


class RecordingSlackClient:
    """Async-callable stub mirroring the slack_sdk.web.async_client surface
    we touch from arlo/* shims. Each method appends to ``self.calls`` and
    returns a minimal-valid response shape."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, name: str):
        async def _call(**kwargs):
            self.calls.append((name, kwargs))
            return {"ok": True, "ts": "1700000000.000100", "channel": kwargs.get("channel")}
        return _call

    def __getattr__(self, name: str):
        # Any method we did not pre-define behaves as a recording stub.
        return self._record(name)


@pytest.fixture
def slack_client() -> RecordingSlackClient:
    return RecordingSlackClient()


@pytest.fixture
def fake_dsn(monkeypatch) -> str | None:
    """Return a DSN if a local arlo db is reachable; otherwise None.
    Tests that require a DB should ``pytest.skip`` when this is None.
    """
    dsn = os.environ.get("ARLO_TEST_DSN") or os.environ.get("ARLO_SUPABASE_DSN")
    return dsn


@pytest.fixture
def event_loop():
    """Per-test loop so tests can use ``asyncio.run`` semantics safely."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
