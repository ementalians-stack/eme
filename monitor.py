#!/usr/bin/env python3
"""Monitor Arena's officially published raw Text / Overall leaderboard snapshot."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

LEADERBOARD_URL = "https://arena.ai/leaderboard/chat/text?scoring=raw"
DATASET_URL = "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset"
DATASET_ROWS_URL = "https://datasets-server.huggingface.co/filter"
DATASET_NAME = "lmarena-ai/leaderboard-dataset"
DATASET_CONFIG = "text"  # Arena documents this as raw (non-style-controlled) Text ratings.
DATASET_CATEGORY = "overall"
PAGE_SIZE = 100
STATE_PATH = Path("state/models.json")
USER_AGENT = "ArenaTextLeaderboardMonitor/2.0 (+https://github.com/)"


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
    """Fetch the latest official raw Text / Overall snapshot from Hugging Face.

    The dataset is published by Arena and licensed CC-BY-4.0. The dataset-viewer
    filter endpoint is paginated; failures or unexpected responses fail closed
    so the saved model baseline is never replaced by an empty/partial result.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    models: dict[str, dict[str, Any]] = {}
    published_dates: set[str] = set()
    offset = 0
    total_rows: int | None = None

    while True:
        params = {
            "dataset": DATASET_NAME,
            "config": DATASET_CONFIG,
            "split": "latest",
            "where": '"category"=\'overall\'',
            "offset": offset,
            "length": PAGE_SIZE,
        }
        response = requests.get(DATASET_ROWS_URL, params=params, headers=headers, timeout=30)
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Hugging Face returned an unreadable dataset response.") from exc

        if payload.get("error"):
            raise RuntimeError(f"Hugging Face dataset service error: {payload['error']}")

        page_rows = payload.get("rows")
        if not isinstance(page_rows, list):
            raise RuntimeError("Hugging Face response did not contain a rows list.")

        if total_rows is None:
            reported_total = payload.get("num_rows_total")
            if not isinstance(reported_total, int) or reported_total < 1:
                raise RuntimeError("Hugging Face did not report any rows for the raw Text / Overall leaderboard.")
            total_rows = reported_total

        for item in page_rows:
            row = item.get("row", {}) if isinstance(item, dict) else {}
            if str(row.get("category", "")).strip().lower() != DATASET_CATEGORY:
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

        if not page_rows:
            break

        offset += len(page_rows)
        if offset >= total_rows:
            break
        if offset > 20000:
            raise RuntimeError("Hugging Face pagination exceeded the safety limit; saved state was not changed.")

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
