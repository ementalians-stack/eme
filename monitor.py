#!/usr/bin/env python3
"""Monitor Arena's officially published raw Text / Overall leaderboard snapshot."""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import requests

LEADERBOARD_URL = "https://arena.ai/leaderboard/chat/text?scoring=raw"
DATASET_URL = "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset"
PARQUET_URL = (
    "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset/"
    "resolve/main/text/latest-00000-of-00001.parquet"
)
STATE_PATH = Path("state/models.json")
ALERT_LOG_PATH = Path("state/alerts.jsonl")
USER_AGENT = "ArenaTextLeaderboardMonitor/2.2 (+https://github.com/)"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime | None = None) -> str:
    """Return a timezone-aware UTC timestamp in an unambiguous ISO-8601 format."""
    stamp = value or _utc_now()
    return stamp.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _as_rank(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() else None
    except (TypeError, ValueError):
        return None


def _as_score(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def fetch_leaderboard() -> tuple[str | None, dict[str, dict[str, Any]], str]:
    """Download Arena's raw-Text snapshot and return its date, models, and fetch time.

    Arena currently supplies a publication DATE, not a publication timestamp.
    snapshot_fetched_at is therefore the first time this monitor can prove the
    model was present in the downloaded dataset; it is not the exact site
    publication time.
    """
    response = requests.get(
        PARQUET_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
        timeout=90,
    )
    response.raise_for_status()
    snapshot_fetched_at = _utc_iso()

    if len(response.content) < 100:
        raise RuntimeError("Arena's published leaderboard dataset download was unexpectedly small.")

    try:
        table = pq.read_table(
            io.BytesIO(response.content),
            columns=["model_name", "rank", "rating", "category", "leaderboard_publish_date"],
        )
    except Exception as exc:
        raise RuntimeError("Could not read Arena's published Parquet dataset or its expected columns.") from exc

    models: dict[str, dict[str, Any]] = {}
    published_dates: set[str] = set()

    for row in table.to_pylist():
        if str(row.get("category", "")).strip().lower() != "overall":
            continue

        name = " ".join(str(row.get("model_name") or "").split())
        if not name:
            continue

        published = str(row.get("leaderboard_publish_date") or "").strip()
        if published:
            published_dates.add(published)

        models[name] = {
            "rank": _as_rank(row.get("rank")),
            "score": _as_score(row.get("rating")),
        }

    if not models:
        raise RuntimeError(
            "No models were found in the official raw Text / Overall dataset. "
            "The saved baseline was not modified."
        )

    leaderboard_date = max(published_dates) if published_dates else None
    return leaderboard_date, models, snapshot_fetched_at


def send_telegram(text: str) -> str:
    """Send an alert and return Telegram's server-reported message timestamp."""
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram credentials are not both configured.")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API rejected the message: {result}")

    # Telegram includes the message's server-side Unix timestamp (seconds).
    message_date = result.get("result", {}).get("date")
    if isinstance(message_date, (int, float)):
        return _utc_iso(datetime.fromtimestamp(message_date, tz=timezone.utc))

    # Defensive fallback for an unexpected API response: record the time
    # immediately after the successful response was received.
    return _utc_iso()


def send_discord(text: str) -> None:
    webhook = os.environ["DISCORD_WEBHOOK_URL"].strip()
    if not webhook:
        raise RuntimeError("Discord webhook URL is not configured.")
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
    for chunk in chunks:
        response = requests.post(webhook, json={"content": chunk}, timeout=20)
        response.raise_for_status()


def format_alert(
    new_models: dict[str, dict[str, Any]],
    leaderboard_date: str | None,
    first_seen_at_utc: str,
) -> str:
    lines = ["🆕 New model(s) detected on Arena Text Arena (raw score)"]
    if leaderboard_date:
        lines.append(f"Arena dataset publication date: {leaderboard_date} (date only; no publication time is provided)")
    lines.append(f"First seen in downloaded dataset (UTC): {first_seen_at_utc}")
    lines.append("")
    for name, details in sorted(
        new_models.items(),
        key=lambda item: (
            item[1].get("rank") is None,
            item[1].get("rank") or 9999,
            item[0].lower(),
        ),
    ):
        rank = f"#{details['rank']}" if details.get("rank") is not None else "New entry"
        score = f" — {details['score']}" if details.get("score") is not None else ""
        lines.append(f"• {name} ({rank}){score}")
    lines.extend(["", LEADERBOARD_URL, f"Dataset: {DATASET_URL}"])
    return "\n".join(lines)


def append_alert_log(record: dict[str, Any]) -> None:
    """Append one JSONL audit record for a successfully handled new-model alert."""
    ALERT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    # This is the monitor process start time, i.e. when the GitHub job begins
    # executing the check script (not the exact moment the runner was allocated).
    check_started_at_utc = _utc_iso()
    leaderboard_date, current_models, snapshot_fetched_at_utc = fetch_leaderboard()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if STATE_PATH.exists():
        previous = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        previous_models = previous.get("models", {})
        first_run = False
    else:
        previous_models = {}
        first_run = True

    notify_on_first_run = os.getenv("NOTIFY_ON_FIRST_RUN", "false").lower() in {"1", "true", "yes"}
    new_models = current_models if first_run and notify_on_first_run else {
        name: details for name, details in current_models.items() if name not in previous_models
    }

    telegram_enabled = bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip())
    discord_enabled = bool(os.getenv("DISCORD_WEBHOOK_URL", "").strip())
    if not telegram_enabled and not discord_enabled:
        raise RuntimeError("Set Telegram secrets, a Discord webhook secret, or both.")

    if first_run:
        print(f"First run: establishing a baseline of {len(current_models)} models; no alert sent by default.")
    elif new_models:
        message = format_alert(new_models, leaderboard_date, snapshot_fetched_at_utc)
        errors: list[str] = []
        telegram_sent_at_utc: str | None = None
        discord_sent_at_utc: str | None = None

        if telegram_enabled:
            try:
                telegram_sent_at_utc = send_telegram(message)
                print(f"Telegram notification accepted by Telegram at {telegram_sent_at_utc}.")
            except Exception as exc:
                errors.append(f"Telegram: {exc}")
        if discord_enabled:
            try:
                send_discord(message)
                discord_sent_at_utc = _utc_iso()
                print(f"Discord notification request completed at {discord_sent_at_utc}.")
            except Exception as exc:
                errors.append(f"Discord: {exc}")
        if errors:
            raise RuntimeError("Notification delivery failed; leaving state unchanged. " + "; ".join(errors))

        record = {
            "recorded_at_utc": _utc_iso(),
            "github_check_started_at_utc": check_started_at_utc,
            "snapshot_first_observed_at_utc": snapshot_fetched_at_utc,
            "arena_leaderboard_publish_date": leaderboard_date,
            "telegram_sent_at_utc": telegram_sent_at_utc,
            "discord_request_completed_at_utc": discord_sent_at_utc,
            "github_run_id": os.getenv("GITHUB_RUN_ID") or None,
            "github_run_url": (
                f"{os.getenv('GITHUB_SERVER_URL', 'https://github.com')}/"
                f"{os.getenv('GITHUB_REPOSITORY', '')}/actions/runs/{os.getenv('GITHUB_RUN_ID', '')}"
                if os.getenv("GITHUB_RUN_ID") and os.getenv("GITHUB_REPOSITORY")
                else None
            ),
            "new_models": new_models,
            "timing_note": (
                "Arena supplies a publication date but no exact publication timestamp. "
                "snapshot_first_observed_at_utc is when this check first downloaded a snapshot containing the model."
            ),
        }
        append_alert_log(record)
        print("Alert timing record appended to state/alerts.jsonl.")
    else:
        print(f"No new models. Current raw Text / Overall leaderboard contains {len(current_models)} models.")

    state = {"models": {name: True for name in current_models}}
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if new_models:
        print(f"Detected {len(new_models)} new model(s): {', '.join(new_models)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
