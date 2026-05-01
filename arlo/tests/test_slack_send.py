"""Smoke tests for arlo.slack_send (patch #3 — clear assistant status)."""
from __future__ import annotations

import pytest

from arlo import slack_send


@pytest.mark.asyncio
async def test_calls_setStatus_when_thread_ts_truthy(slack_client):
    await slack_send.clear_assistant_status(
        client=slack_client, chat_id="C123", thread_ts="1700000000.000100"
    )
    names = [name for name, _ in slack_client.calls]
    assert names == ["assistant_threads_setStatus"]
    _, kwargs = slack_client.calls[0]
    assert kwargs == {
        "channel_id": "C123",
        "thread_ts": "1700000000.000100",
        "status": "",
    }


@pytest.mark.asyncio
async def test_noop_when_thread_ts_missing(slack_client):
    await slack_send.clear_assistant_status(
        client=slack_client, chat_id="C123", thread_ts=None
    )
    assert slack_client.calls == []


@pytest.mark.asyncio
async def test_silent_on_setStatus_failure():
    class BoomClient:
        async def assistant_threads_setStatus(self, **kwargs):
            raise RuntimeError("slack 500")

    # Must not raise — silent fail (debug-log only) is the contract.
    await slack_send.clear_assistant_status(
        client=BoomClient(), chat_id="C123", thread_ts="1700000000.000100"
    )
