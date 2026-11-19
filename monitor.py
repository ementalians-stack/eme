#!/usr/bin/env python3
"""Monitor Arena's raw and style-controlled Text / Overall leaderboards."""
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

LEADERBOARD_URL = "https://arena.ai/leaderboard/chat/text"
DATASET_URL = "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset"
DATASET_BASE_URL = (
    "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset/"
    "resolve/main"
)
LEADERBOARDS = {
    "none": {
        "label": "Style control: NONE (raw)",
        "subset": "text",
        "parquet_url": f"{DATASET_BASE_URL}/text/latest-00000-of-00001.parquet",
    },
    "style_control": {
        "label": "Style control: ON",
        "subset": "text_style_control",
        "parquet_url": f"{DATASET_BASE_URL}/text_style_control/latest-00000-of-00001.parquet",
    },
}
STATE_PATH = Path("state/models.json")
ALERT_LOG_PATH = Path("state/alerts.jsonl")
USER_AGENT = "ArenaTextLeaderboardMonitor/3.0 (+https://github.com/)"


def _utc_iso(value: datetime | None = None) -> str:
    """Return a timezone-aware UTC timestamp in an unambiguous ISO-8601 format."""
    stamp = value or datetime.now(timezone.utc)
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


def fetch_leaderboard(variant: str) -> tuple[str | None, dict[str, dict[str, Any]], str]:
    """Download one published Text leaderboard snapshot and select Overall rows.

    Arena publishes separate `text` and `text_style_control` subsets.
    Arena currently supplies a publication DATE, not an exact publication time.
    The returned timestamp records when this monitor first downloaded the
    snapshot containing the rows; it is not the exact site publication time.
    """
    if variant not in LEADERBOARDS:
        raise ValueError(f"Unsupported leaderboard variant: {variant}")

    config = LEADERBOARDS[variant]
    response = requests.get(
        config["parquet_url"],
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
        timeout=90,
    )
    response.raise_for_status()
    snapshot_fetched_at = _utc_iso()

    if len(response.content) < 100:
        raise RuntimeError(
            f"Arena's published {config['label']} dataset download was unexpectedly small."
        )

    try:
        table = pq.read_table(
            io.BytesIO(response.content),
            columns=["model_name", "rank", "rating", "category", "leaderboard_publish_date"],
        )
    except Exception as exc:
        raise RuntimeError(
            f"Could not read Arena's {config['label']} Parquet dataset or its expected columns."
        ) from exc

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
            f"No models were found in Arena's {config['label']} Overall dataset. "
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

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API rejected the message: {result}")

    message_date = result.get("result", {}).get("date")
    if isinstance(message_date, (int, float)):
        return _utc_iso(datetime.fromtimestamp(message_date, tz=timezone.utc))
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
    new_models_by_variant: dict[str, dict[str, dict[str, Any]]],
    leaderboard_metadata: dict[str, dict[str, Any]],
) -> str:
    """Format one alert, keeping raw and style-controlled scores distinct."""
    lines = ["🆕 New model(s) detected on Arena Text / Overall"]
    for variant in ("none", "style_control"):
        new_models = new_models_by_variant.get(variant, {})
        if not new_models:
            continue

        config = LEADERBOARDS[variant]
        metadata = leaderboard_metadata[variant]
        lines.extend(["", config["label"]])
        if metadata.get("publish_date"):
            lines.append(f"Arena dataset publication date: {metadata['publish_date']} (date only)")
        lines.append(
            f"First seen in downloaded dataset (UTC): "
            f"{metadata['snapshot_first_observed_at_utc']}"
        )

        for name, details in sorted(
            new_models.items(),
            key=lambda item: (
                item[1].get("rank") is None,
                item[1].get("rank") or 9999,
                item[0].lower(),
            ),
        ):
            rank = f"#{details['rank']}" if details.get("rank") is not None else "New entry"
            score = f" — score {details['score']}" if details.get("score") is not None else ""
            lines.append(f"• {name} ({rank}){score}")

    lines.extend(
        [
            "",
            f"Leaderboard: {LEADERBOARD_URL}",
            f"Raw dataset (style control NONE): {DATASET_URL}/tree/main/text",
            f"Style-control dataset (style control ON): {DATASET_URL}/tree/main/text_style_control",
        ]
    )
    return "\n".join(lines)


def append_alert_log(record: dict[str, Any]) -> None:
    """Append one JSONL audit record for a successfully handled new-model alert."""
    ALERT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    check_started_at_utc = _utc_iso()

    # Fetch both datasets before changing state. If either download or schema
    # check fails, this run fails closed and preserves both previous baselines.
    leaderboard_metadata: dict[str, dict[str, Any]] = {}
    current_models_by_variant: dict[str, dict[str, dict[str, Any]]] = {}
    for variant in ("none", "style_control"):
        publish_date, models, first_seen_at = fetch_leaderboard(variant)
        current_models_by_variant[variant] = models
        leaderboard_metadata[variant] = {
            "subset": LEADERBOARDS[variant]["subset"],
            "style_control": variant == "style_control",
            "publish_date": publish_date,
            "snapshot_first_observed_at_utc": first_seen_at,
        }

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        previous = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        previous_raw_models = previous.get("models", {})
        style_baseline_missing = "style_control_models" not in previous
        previous_style_models = previous.get("style_control_models", {})
        first_run = False
    else:
        previous_raw_models = {}
        previous_style_models = {}
        style_baseline_missing = True
        first_run = True

    notify_on_first_run = os.getenv("NOTIFY_ON_FIRST_RUN", "false").lower() in {
        "1", "true", "yes"
    }

    if first_run:
        if notify_on_first_run:
            new_models_by_variant = {
                variant: models
                for variant, models in current_models_by_variant.items()
            }
        else:
            new_models_by_variant = {"none": {}, "style_control": {}}
    else:
        new_models_by_variant = {
            "none": {
                name: details
                for name, details in current_models_by_variant["none"].items()
                if name not in previous_raw_models
            },
            # The first upgraded run quietly establishes the style-control
            # baseline, avoiding a flood of alerts for models already listed.
            "style_control": (
                {}
                if style_baseline_missing
                else {
                    name: details
                    for name, details in current_models_by_variant["style_control"].items()
                    if name not in previous_style_models
                }
            ),
        }

    has_new_models = any(new_models_by_variant.values())
    telegram_enabled = bool(
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        and os.getenv("TELEGRAM_CHAT_ID", "").strip()
    )
    discord_enabled = bool(os.getenv("DISCORD_WEBHOOK_URL", "").strip())
    if not telegram_enabled and not discord_enabled:
        raise RuntimeError("Set Telegram secrets, a Discord webhook secret, or both.")

    if style_baseline_missing:
        print(
            "Establishing the initial style-control baseline with "
            f"{len(current_models_by_variant['style_control'])} models; "
            "no alerts will be sent for these already-listed models."
        )

    if first_run:
        print(
            "First run: establishing baselines of "
            f"{len(current_models_by_variant['none'])} raw and "
            f"{len(current_models_by_variant['style_control'])} style-controlled models; "
            "no alert sent by default."
        )
    elif has_new_models:
        message = format_alert(new_models_by_variant, leaderboard_metadata)
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
            raise RuntimeError(
                "Notification delivery failed; leaving both model baselines unchanged. "
                + "; ".join(errors)
            )

        record = {
            "recorded_at_utc": _utc_iso(),
            "github_check_started_at_utc": check_started_at_utc,
            "leaderboards": leaderboard_metadata,
            "telegram_sent_at_utc": telegram_sent_at_utc,
            "discord_request_completed_at_utc": discord_sent_at_utc,
            "github_run_id": os.getenv("GITHUB_RUN_ID") or None,
            "github_run_url": (
                f"{os.getenv('GITHUB_SERVER_URL', 'https://github.com')}/"
                f"{os.getenv('GITHUB_REPOSITORY', '')}/actions/runs/{os.getenv('GITHUB_RUN_ID', '')}"
                if os.getenv("GITHUB_RUN_ID") and os.getenv("GITHUB_REPOSITORY")
                else None
            ),
            "new_models": new_models_by_variant,
            "timing_note": (
                "Arena supplies a publication date, not an exact publication timestamp. "
                "snapshot_first_observed_at_utc records when this run first downloaded a snapshot "
                "containing the model."
            ),
        }
        append_alert_log(record)
        print("Alert timing record appended to state/alerts.jsonl.")
    elif not first_run:
        print(
            "No new models. Current Overall counts: "
            f"{len(current_models_by_variant['none'])} with style control NONE; "
            f"{len(current_models_by_variant['style_control'])} with style control ON."
        )

    state = {
        "models": {
            name: True for name in current_models_by_variant["none"]
        },
        "style_control_models": {
            name: True for name in current_models_by_variant["style_control"]
        },
    }
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if has_new_models:
        for variant in ("none", "style_control"):
            names = list(new_models_by_variant.get(variant, {}))
            if names:
                print(f"New models ({LEADERBOARDS[variant]['label']}): {', '.join(names)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
