"""Smoke tests for arlo.slack_halt (patches #1 + #2).

Three cases:
  - flag-present (no halt-keyword text) → posts "halted" reply, returns
    (False, None); no flag mutation.
  - resume → flag file removed, posts "resumed" ack, returns (False, None).
  - stop → writes flag file, denies all pending approvals via fake
    tools.approval, posts "halted" ack, returns (False, None).
"""
from __future__ import annotations

import sys
import threading
from typing import Any

import pytest

from arlo import slack_halt


# --- Fake tools.approval (sys.modules-injected) --------------------------


def _install_fake_approval(monkeypatch, queue_keys: list[str]) -> dict[str, Any]:
    """Inject a fake ``tools.approval`` so the import inside slack_halt
    finds our stub instead of the real Hermes module.

    Returns a dict the test can inspect:
      - ``resolved``: list[(key, choice, resolve_all)] in call order
    """
    state: dict[str, Any] = {"resolved": []}

    def _resolve(key: str, choice: str, resolve_all: bool = False) -> int:
        state["resolved"].append((key, choice, resolve_all))
        # Simulate "1 turn unblocked per key" so caller's denied counter rolls.
        return 1

    fake = type(sys)("tools.approval")
    fake._gateway_queues = {k: object() for k in queue_keys}
    fake.resolve_gateway_approval = _resolve
    fake._lock = threading.Lock()

    # ``tools`` parent package must also exist for ``from tools.approval ...``
    # Pyright resolves the leaf via sys.modules; provide both.
    parent = sys.modules.get("tools") or type(sys)("tools")
    parent.approval = fake
    monkeypatch.setitem(sys.modules, "tools", parent)
    monkeypatch.setitem(sys.modules, "tools.approval", fake)
    return state


# --- Tests ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_halt_flag_present_blocks_dispatch_and_posts_reply(
    slack_client, tmp_path
):
    flag = tmp_path / "arlo-halted"
    flag.write_text("U_PRIOR\n1700000000.0\n")  # halt was set previously

    should_dispatch, halt_msg = await slack_halt.check(
        text="hello there",
        user_id="U_USER",
        channel_id="C_CHANNEL",
        thread_ts="1700000000.000100",
        client=slack_client,
        halt_flag_path=str(flag),
    )

    assert (should_dispatch, halt_msg) == (False, None)
    # Flag still on disk — only `resume` clears it.
    assert flag.exists()
    # Slack: one chat_postMessage with the halted reply.
    names = [name for name, _ in slack_client.calls]
    assert names == ["chat_postMessage"]
    _, kwargs = slack_client.calls[0]
    assert kwargs["channel"] == "C_CHANNEL"
    assert kwargs["thread_ts"] == "1700000000.000100"
    assert "halted" in kwargs["text"]
    assert "resume" in kwargs["text"]


@pytest.mark.asyncio
async def test_resume_clears_flag_and_posts_ack(slack_client, tmp_path):
    flag = tmp_path / "arlo-halted"
    flag.write_text("U_PRIOR\n1700000000.0\n")

    should_dispatch, halt_msg = await slack_halt.check(
        text="resume",
        user_id="U_USER",
        channel_id="C_CHANNEL",
        thread_ts="1700000000.000100",
        client=slack_client,
        halt_flag_path=str(flag),
    )

    assert (should_dispatch, halt_msg) == (False, None)
    # Flag removed.
    assert not flag.exists()
    # Slack: one chat_postMessage with the resumed ack.
    names = [name for name, _ in slack_client.calls]
    assert names == ["chat_postMessage"]
    _, kwargs = slack_client.calls[0]
    assert "resumed" in kwargs["text"]


@pytest.mark.asyncio
async def test_stop_denies_pending_approvals_writes_flag_and_posts_ack(
    slack_client, tmp_path, monkeypatch
):
    flag = tmp_path / "arlo-halted"
    state = _install_fake_approval(
        monkeypatch, queue_keys=["sess-A", "sess-B"]
    )
    assert not flag.exists()  # precondition: no prior halt

    should_dispatch, halt_msg = await slack_halt.check(
        text="STOP",  # case-insensitive normalization
        user_id="U_USER",
        channel_id="C_CHANNEL",
        thread_ts="1700000000.000100",
        client=slack_client,
        halt_flag_path=str(flag),
    )

    assert (should_dispatch, halt_msg) == (False, None)
    # Each gateway-queue key was deny'd with resolve_all=True.
    assert state["resolved"] == [
        ("sess-A", "deny", True),
        ("sess-B", "deny", True),
    ]
    # Flag written; first line is the stopping user's id.
    assert flag.exists()
    body = flag.read_text().splitlines()
    assert body[0] == "U_USER"
    # Slack ack posted.
    names = [name for name, _ in slack_client.calls]
    assert names == ["chat_postMessage"]
    _, kwargs = slack_client.calls[0]
    assert "halted" in kwargs["text"]
