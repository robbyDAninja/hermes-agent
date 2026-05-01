"""Cost rollup helpers for the Slack Home tab."""

from __future__ import annotations

from collections import defaultdict
import datetime
import logging
import os
import sqlite3
import time
from zoneinfo import ZoneInfo


logger = logging.getLogger(__name__)


# USD per 1,000,000 tokens.
MODEL_PRICES_USD_PER_MTOK = {
    "claude-opus-4-7": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25},
}


def normalize_model(model: str | None) -> str | None:
    """Return the canonical model key, or None if model is null/empty."""
    if not model:
        return None
    if model.startswith("anthropic/"):
        return model[len("anthropic/"):]
    return model


def session_cost_usd(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> float | None:
    """Compute USD cost for one session, or None if the model is unknown."""
    normalized_model = normalize_model(model)
    if normalized_model not in MODEL_PRICES_USD_PER_MTOK:
        return None

    prices = MODEL_PRICES_USD_PER_MTOK[normalized_model]
    return (
        (input_tokens or 0) * prices["input"]
        + (output_tokens or 0) * prices["output"]
        + (cache_read_tokens or 0) * prices["cache_read"]
        + (cache_write_tokens or 0) * prices["cache_write"]
    ) / 1_000_000


def compute_daily_cost_rollup(
    db_path: str,
    *,
    now_epoch: float | None = None,
    tz_name: str = "America/New_York",
    days: int = 7,
) -> list[dict]:
    """Return daily cost rollup rows, ordered most-recent-first."""
    if days <= 0:
        return []

    tz = ZoneInfo(tz_name)
    now = datetime.datetime.fromtimestamp(now_epoch if now_epoch is not None else time.time(), tz)
    today = now.date()
    dates = [today - datetime.timedelta(days=offset) for offset in range(days)]
    rows_by_date = {
        date: {
            "date": date,
            "total_usd": 0.0,
            "dominant_model": None,
            "dominant_share_usd": 0.0,
            "session_count": 0,
        }
        for date in dates
    }

    expanded_db_path = os.path.expanduser(db_path)
    if not os.path.exists(expanded_db_path):
        return [rows_by_date[date] for date in dates]

    oldest = dates[-1]
    start = datetime.datetime.combine(oldest, datetime.time(0, 0), tzinfo=tz).timestamp()
    end = (
        datetime.datetime.combine(today, datetime.time(0, 0), tzinfo=tz)
        + datetime.timedelta(days=1)
    ).timestamp()
    model_costs_by_date: dict[datetime.date, defaultdict[str, float]] = {
        date: defaultdict(float) for date in dates
    }
    warned_unknown_models: set[str | None] = set()

    conn = sqlite3.connect(f"file:{expanded_db_path}?mode=ro", uri=True)
    try:
        cursor = conn.execute(
            "SELECT model, "
            "COALESCE(input_tokens, 0), "
            "COALESCE(output_tokens, 0), "
            "COALESCE(cache_read_tokens, 0), "
            "COALESCE(cache_write_tokens, 0), "
            "started_at "
            "FROM sessions "
            "WHERE started_at >= ? AND started_at < ?",
            (start, end),
        )
        for model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, started_at in cursor:
            normalized_model = normalize_model(model)
            cost = session_cost_usd(
                model,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
            )
            if cost is None:
                if normalized_model not in warned_unknown_models:
                    logger.warning("[home_cost] skipping unknown model: %s", normalized_model)
                    warned_unknown_models.add(normalized_model)
                continue

            session_date = datetime.datetime.fromtimestamp(started_at, tz).date()
            if session_date not in rows_by_date:
                continue

            rows_by_date[session_date]["total_usd"] += cost
            rows_by_date[session_date]["session_count"] += 1
            model_costs_by_date[session_date][normalized_model] += cost
    finally:
        conn.close()

    for date in dates:
        model_costs = model_costs_by_date[date]
        if model_costs:
            dominant_model, dominant_share = max(model_costs.items(), key=lambda item: item[1])
            rows_by_date[date]["dominant_model"] = dominant_model
            rows_by_date[date]["dominant_share_usd"] = dominant_share

    return [rows_by_date[date] for date in dates]


def build_cost_rollup_blocks(rows: list[dict]) -> list[dict]:
    """Build Slack Block Kit blocks for the cost rollup section."""
    lines = []
    for row in rows:
        date_label = row["date"].strftime("%a %b %-d")
        total_usd = row.get("total_usd", 0.0)
        if total_usd == 0:
            lines.append(f"`{date_label}`  _no spend_")
            continue

        dominant_model = row.get("dominant_model") or "unknown"
        model_label = dominant_model.removeprefix("claude-")
        dominant_share_usd = row.get("dominant_share_usd", 0.0)
        percent = round(dominant_share_usd / total_usd * 100)
        lines.append(f"`{date_label}`  *${total_usd:.2f}*  · {model_label} ({percent}%)")

    return [
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Last 7 days · spend*"}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines)},
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "_Source: ~/.hermes/state.db · ET ·  totals reconcile to ±$0.01_",
            }],
        },
    ]
