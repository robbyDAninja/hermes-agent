"""Halt-switch shim — bridge-ninja patches #1 (sha ddbcf40eb) + #2 (sha 90afced35).

Upstream callsite: gateway/platforms/slack.py inside SlackAdapter.handle_message,
immediately after thread-context resolution and before the MessageType
assignment.

Three text-level commands:
  - ``stop`` / ``halt`` / ``exit`` → write the halt flag, deny any
    in-flight sandbox approvals, post an ack, and signal "do not
    dispatch this turn".
  - ``resume``                    → clear the halt flag, post an ack,
    do not dispatch.
  - any other text while halted   → post a "still halted" reply,
    do not dispatch.

Patch #2 folds in the deny-in-flight-approvals loop. The
``tools.approval._gateway_queues`` import is encapsulated here BY
DESIGN — that upstream-internal coupling lives in this single ARLO
file so a future Hermes rename breaks one place loudly, not 13
upstream slack.py edits silently. (DESIGN.md §2.1; risk register R2.)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_HALT_FLAG_PATH = os.path.expanduser("~/.hermes/arlo-halted")


async def check(
    text: str,
    user_id: str,
    channel_id: str,
    thread_ts: str | None,
    client: Any,
    halt_flag_path: str | None = None,
) -> tuple[bool, str | None]:
    """Gate one inbound text message on halt state.

    Returns ``(should_dispatch, halt_msg)``:
      - ``should_dispatch=True`` → caller proceeds with normal message
        dispatch. ``halt_msg`` is ``None``.
      - ``should_dispatch=False`` → caller MUST early-return; this
        function has already posted any user-facing ack via ``client``
        and persisted halt-state to disk. ``halt_msg`` is reserved for
        future use (a caller-side post-hook); currently always ``None``.

    ``halt_flag_path`` overrides the module default; tests pass a
    tmp_path-scoped path here.
    """
    flag = halt_flag_path or DEFAULT_HALT_FLAG_PATH
    norm = (text or "").strip().lower()

    if norm in ("stop", "halt", "exit"):
        # Deny any in-flight sandbox approvals across all sessions so
        # stuck agent turns unblock immediately and return a deny result
        # to the loop (which then ends cleanly). [Patch #2]
        try:
            from tools.approval import _gateway_queues, resolve_gateway_approval, _lock
            with _lock:
                keys = list(_gateway_queues.keys())
            denied = 0
            for k in keys:
                denied += resolve_gateway_approval(k, "deny", resolve_all=True)
            if denied:
                logger.info("[Slack/arlo-halt] Denied %d pending approval(s)", denied)
        except Exception as _e:
            logger.warning("[Slack/arlo-halt] Could not deny pending approvals: %s", _e)
        try:
            with open(flag, "w") as _f:
                _f.write(f"{user_id}\n{time.time()}\n")
        except Exception as _e:
            logger.warning("[Slack/arlo-halt] Could not write halt flag: %s", _e)
        try:
            await client.chat_postMessage(
                channel=channel_id,
                text=":octagonal_sign: halted. send `resume` to continue.",
                thread_ts=thread_ts,
            )
        except Exception as _e:
            logger.warning("[Slack/arlo-halt] Could not send halt ack: %s", _e)
        return False, None

    if norm == "resume":
        try:
            if os.path.exists(flag):
                os.remove(flag)
        except Exception as _e:
            logger.warning("[Slack/arlo-halt] Could not remove halt flag: %s", _e)
        try:
            await client.chat_postMessage(
                channel=channel_id,
                text=":white_check_mark: resumed. taking new messages.",
                thread_ts=thread_ts,
            )
        except Exception as _e:
            logger.warning("[Slack/arlo-halt] Could not send resume ack: %s", _e)
        return False, None

    if os.path.exists(flag):
        try:
            await client.chat_postMessage(
                channel=channel_id,
                text=":octagonal_sign: halted. send `resume` to continue.",
                thread_ts=thread_ts,
            )
        except Exception as _e:
            logger.warning("[Slack/arlo-halt] Could not send halted reply: %s", _e)
        return False, None

    return True, None
