#!/usr/bin/env python3
"""Monitor Arena's public Text Arena raw-score leaderboard for newly listed models."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

LEADERBOARD_URL = "https://arena.ai/leaderboard/chat/text?scoring=raw"
STATE_PATH = Path("state/models.json")
USER_AGENT = "Mozilla/5.0 (compatible; ArenaTextLeaderboardMonitor/1.0; +https://github.com/)"


def parse_leaderboard(html: str) -> tuple[str | None, dict[str, dict[str, Any]]]:
    """Extract model rows from the rendered leaderboard table.

    Fails closed if the page structure changes or no model rows are found, so a
    transient/block page cannot overwrite the saved baseline with an empty list.
    """
    soup = BeautifulSoup(html, "html.parser")
    models: dict[str, dict[str, Any]] = {}

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        headers = [cell.get_text(" ", strip=True).lower() for cell in rows[0].find_all(["th", "td"], recursive=False)]
        if "model" not in headers:
            continue
        model_index = headers.index("model")
        rank_index = headers.index("rank") if "rank" in headers else None
        score_index = headers.index("score") if "score" in headers else None

        for row in rows[1:]:
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) <= model_index:
                continue
            model_cell = cells[model_index]
            # In Arena's table the model name links to the model/provider page.
            anchors = model_cell.find_all("a")
            name = anchors[0].get_text(" ", strip=True) if anchors else model_cell.get_text(" ", strip=True)
            name = " ".join(name.split())
            if not name or name.lower() in {"model", "models"}:
                continue

            rank = None
            if rank_index is not None and len(cells) > rank_index:
                rank_text = cells[rank_index].get_text(" ", strip=True).replace(",", "")
                try:
                    rank = int(rank_text)
                except ValueError:
                    pass

            score = None
            if score_index is not None and len(cells) > score_index:
                score = cells[score_index].get_text(" ", strip=True)

            models[name] = {"rank": rank, "score": score}

    if not models:
        raise RuntimeError(
            "Could not find model rows in Arena's page. The page layout may have changed; "
            "saved state was not modified. Inspect the latest GitHub Actions log."
        )

    # The published leaderboard date is displayed near the title (e.g. Nov 11, 2026).
    page_text = soup.get_text(" ", strip=True)
    import re
    match = re.search(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b", page_text)
    leaderboard_date = match.group(0) if match else None
    return leaderboard_date, models


def send_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram credentials are not both configured.")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=20)
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API rejected the message: {result}")


def send_discord(text: str) -> None:
    webhook = os.environ["DISCORD_WEBHOOK_URL"].strip()
    if not webhook:
        raise RuntimeError("Discord webhook URL is not configured.")
    # Discord message content limit is 2,000 characters.
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
    for chunk in chunks:
        response = requests.post(webhook, json={"content": chunk}, timeout=20)
        response.raise_for_status()


def format_alert(new_models: dict[str, dict[str, Any]], leaderboard_date: str | None) -> str:
    lines = ["🆕 New model(s) detected on Arena Text Arena (raw score)"]
    if leaderboard_date:
        lines.append(f"Leaderboard data date: {leaderboard_date}")
    lines.append("")
    for name, details in sorted(new_models.items(), key=lambda item: (item[1].get("rank") is None, item[1].get("rank") or 9999, item[0].lower())):
        rank = f"#{details['rank']}" if details.get("rank") is not None else "New entry"
        score = f" — {details['score']}" if details.get("score") else ""
        lines.append(f"• {name} ({rank}){score}")
    lines.extend(["", LEADERBOARD_URL])
    return "\n".join(lines)


def main() -> int:
    response = requests.get(
        LEADERBOARD_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        timeout=30,
    )
    response.raise_for_status()

    leaderboard_date, current_models = parse_leaderboard(response.text)
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
            except Exception as exc:  # do not mark models as seen if delivery failed
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
        print(f"No new models. Current Text Arena raw leaderboard contains {len(current_models)} models.")

    # Keep only the set of seen names. Persisting scores or ranks would create
    # noisy commits whenever Arena refreshes model statistics without adding a model.
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
