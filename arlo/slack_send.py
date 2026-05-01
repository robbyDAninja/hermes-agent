"""Status-clear shim — bridge-ninja patch #3 (sha 477d26830).

Upstream callsite: gateway/platforms/slack.py inside SlackAdapter.send,
immediately after the bot-ts tracking block that follows chat_postMessage.

Slack auto-surfaces an "Generating response..." assistant status for bots
configured as AI Assistants. Without this call the status outlives the
reply because handle_message still has post-turn work (compression,
logging, etc.). We clear it the instant the reply is sent.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def clear_assistant_status(
    client: Any,
    chat_id: str,
    thread_ts: str | None,
) -> None:
    """Clear Slack's assistant 'Generating response...' status for an
    assistant thread. No-op when thread_ts is falsy. Silent on
    failure (debug-log only) so we never break the send path."""
    if not thread_ts:
        return
    try:
        await client.assistant_threads_setStatus(
            channel_id=chat_id,
            thread_ts=thread_ts,
            status="",
        )
    except Exception as _e:
        logger.debug("[Slack] Could not clear assistant status: %s", _e)
