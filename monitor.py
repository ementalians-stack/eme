#!/usr/bin/env python3
"""Monitor Arena's officially published raw Text / Overall leaderboard snapshot."""
from __future__ import annotations

import io
import json
import os
import sys
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
USER_AGENT = "ArenaTextLeaderboardMonitor/2.1 (+https://github.com/)"


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


def fetch_leaderboard() -> tuple[str | None, dict[str, dict[str, Any]]]:
    """Download Arena's small latest raw-Text Parquet snapshot and select overall rows.

    This avoids the dataset-viewer /filter service, which can return HTTP 500.
    A failed download, unreadable Parquet file, unexpected schema, or empty
    overall leaderboard fails closed so the saved baseline is never erased.
    """
    response = requests.get(
        PARQUET_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
        timeout=90,
    )
    response.raise_for_status()

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
            "The saved state was not modified."
        )

    leaderboard_date = max(published_dates) if published_dates else None
    return leaderboard_date, models


def send_telegram(text: str) -> None:
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


def send_discord(text: str) -> None:
    webhook = os.environ["DISCORD_WEBHOOK_URL"].strip()
    if not webhook:
        raise RuntimeError("Discord webhook URL is not configured.")
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
    for chunk in chunks:
        response = requests.post(webhook, json={"content": chunk}, timeout=20)
        response.raise_for_status()


def format_alert(new_models: dict[str, dict[str, Any]], leaderboard_date: str | None) -> str:
    lines = ["🆕 New model(s) detected on Arena Text Arena (raw score)"]
    if leaderboard_date:
        lines.append(f"Leaderboard data date: {leaderboard_date}")
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


def main() -> int:
    leaderboard_date, current_models = fetch_leaderboard()
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
        message = format_alert(new_models, leaderboard_date)
        errors: list[str] = []
        if telegram_enabled:
            try:
                send_telegram(message)
                print("Telegram notification sent.")
            except Exception as exc:
                errors.append(f"Telegram: {exc}")
        if discord_enabled:
            try:
                send_discord(message)
                print("Discord notification sent.")
            except Exception as exc:
                errors.append(f"Discord: {exc}")
        if errors:
            raise RuntimeError("Notification delivery failed; leaving state unchanged. " + "; ".join(errors))
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
