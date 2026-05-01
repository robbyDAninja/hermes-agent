"""Block-actions shim — bridge-ninja patches #4 (sha af988f209) + #11 (sha 063b0cd0c).

Upstream callsite: gateway/platforms/slack.py inside SlackAdapter.__init__,
the @app.action handler registered against the regex
``^arlo_write_(approve|deny|approve_thread):[0-9a-f-]+$``.

When a user taps Approve / Deny / Approve-all-in-thread on an arlo-write
proposal card, this handler resolves the corresponding row in
``arlo.pending_writes`` and updates the Slack message. The arlo-write
skill polls that table and acts on the new status.

Patch #11 folds in stale-click handling: if the row is no longer
``pending`` (already approved/denied/expired), we re-fetch the actual
status and rewrite the card to an "Already resolved" view rather than
silently swallowing the click.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def handle_action(
    ack: Any,
    body: dict,
    action: dict,
    client: Any,
    dsn: str | None,
) -> None:
    """Resolve a row in arlo.pending_writes when a user taps
    [Approve] / [Deny] / [Approve all in thread] on an Arlo edit
    proposal. The arlo-write skill polls the row and acts on the new
    status."""
    await ack()

    try:
        import psycopg  # provided by the hermes venv
    except Exception as e:
        logger.error("[Slack/arlo-write] psycopg unavailable: %s", e)
        return

    action_id = action.get("action_id", "")
    try:
        verb, pending_id = action_id.split(":", 1)
        verb = verb.replace("arlo_write_", "")
    except Exception:
        logger.warning("[Slack/arlo-write] bad action_id: %s", action_id)
        return

    user_id = (body.get("user") or {}).get("id", "unknown")
    channel_id = (body.get("channel") or {}).get("id", "")
    message = body.get("message") or {}
    thread_ts = message.get("thread_ts") or message.get("ts")
    message_ts = message.get("ts")

    if not dsn:
        logger.error("[Slack/arlo-write] ARLO_SUPABASE_DSN not set")
        return

    new_status = "approved" if verb in ("approve", "approve_thread") else "denied"

    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE arlo.pending_writes
                   SET status = %s,
                       resolved_by = %s,
                       resolved_at = now()
                 WHERE id = %s
                   AND status = 'pending'
                RETURNING summary, channel_id, thread_ts
                """,
                (new_status, user_id, pending_id),
            )
            row = cur.fetchone()
            if row is None:
                # Row exists but is no longer 'pending' — already approved,
                # denied, expired, or resolved out-of-band (e.g. cleanup).
                # Don't silently swallow the click; tell the user the card
                # is stale and update it to a resolved view.
                cur.execute(
                    "SELECT status, resolved_by, resolved_at, summary "
                    "  FROM arlo.pending_writes WHERE id = %s",
                    (pending_id,),
                )
                existing = cur.fetchone()
                logger.info(
                    "[Slack/arlo-write] %s click ignored — row not pending "
                    "(current status: %s)",
                    pending_id,
                    existing[0] if existing else "missing",
                )
                if existing:
                    ex_status, ex_by, ex_at, ex_summary = existing
                    stale_text = (
                        f":lock: *Already resolved* — this proposal was "
                        f"`{ex_status}` by `{ex_by or 'system'}` "
                        f"at {ex_at:%Y-%m-%d %H:%M UTC}. "
                        f"Your tap had no effect."
                    )
                else:
                    stale_text = (
                        ":warning: This proposal no longer exists. "
                        "Your tap had no effect."
                    )
                try:
                    if message_ts:
                        await client.chat_update(
                            channel=channel_id,
                            ts=message_ts,
                            text=stale_text,
                            blocks=[{
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": stale_text},
                            }],
                        )
                    else:
                        await client.chat_postMessage(
                            channel=channel_id,
                            thread_ts=thread_ts,
                            text=stale_text,
                        )
                except Exception as e:
                    logger.warning(
                        "[Slack/arlo-write] stale-card update failed: %s",
                        e, exc_info=True,
                    )
                return
            summary, row_channel, row_thread = row

            if verb == "approve_thread" and row_channel and row_thread:
                cur.execute(
                    """
                    INSERT INTO arlo.thread_write_policies
                        (channel_id, thread_ts, approved_all, approved_by)
                    VALUES (%s, %s, true, %s)
                    ON CONFLICT (channel_id, thread_ts)
                    DO UPDATE SET approved_all = true,
                                  approved_by = EXCLUDED.approved_by,
                                  approved_at = now()
                    """,
                    (row_channel, row_thread, user_id),
                )
    except Exception as e:
        logger.error("[Slack/arlo-write] db error: %s", e, exc_info=True)
        return

    ack_text = {
        "approve":        f"Approved by <@{user_id}>. Making the edit now.",
        "approve_thread": f"Approved by <@{user_id}> — and I'll skip the prompt for further edits in this thread.",
        "deny":           f"Denied by <@{user_id}>. No changes made.",
    }.get(verb, f"Recorded by <@{user_id}>.")

    try:
        if message_ts:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=ack_text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": ack_text}}],
            )
        else:
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=ack_text,
            )
    except Exception as e:
        logger.warning("[Slack/arlo-write] could not post ack: %s", e)
