# Arena Text raw and style-controlled new-model notifications

A GitHub Actions monitor for new models on both Arena **Text / Overall** leaderboards:
- **Style control: NONE** (raw ratings; dataset subset: `text`)
- **Style control: ON** (style-controlled ratings; dataset subset: `text_style_control`)

- Official leaderboard: <https://arena.ai/leaderboard/chat/text>
- Official published dataset: <https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset>

The monitor downloads Arena's two officially published Parquet snapshots and selects the `overall` rows locally. It does not scrape the live Arena webpage or depend on the dataset-viewer filter API. It checks both snapshots every five minutes and sends a Telegram message, a Discord message, or both when a previously unseen model appears in either scoring mode. Your computer does not need to be on.

## What it does and does not do

- Tracks **Text Arena / Overall / Style control NONE** and **Text Arena / Overall / Style control ON** as separate leaderboards.
- Uses the official `text` and `text_style_control` dataset subsets, filtered to the `overall` category.
- Every alert clearly labels the score type as **Style control: NONE (raw)** or **Style control: ON**.
- Tracks the two leaderboards independently. A model already seen with style control off can still trigger a separate alert when it first appears with style control on, and vice versa.
- Alerts only on **new model names within each scoring mode**; rank and score changes do not create alerts.
- The first run saves a baseline and does not spam you with all existing models.
- The existing raw baseline is preserved in `state/models.json`. On the first successful run after this upgrade, the current style-control models are added as a quiet baseline; already-listed style-control models will not generate a flood of old alerts.
- Arena updates its public dataset on days when it publishes leaderboard updates. The five-minute check detects updates after they appear in each dataset; this is not a live feed and cannot alert before Arena publishes the data.
- The monitor also runs automatically when its code, dependencies, or workflow file are updated on the `main` branch.
- The public dataset is licensed CC-BY-4.0. Alerts link to both official dataset subsets.

## Alert timing and audit history

For each successfully handled new-model alert, the monitor appends one record to [`state/alerts.jsonl`](state/alerts.jsonl). Each record includes:

- `github_check_started_at_utc`: when the monitor script started its GitHub Actions check.
- `leaderboards.none`: publication date and first-observed timestamp for the raw (style control off) dataset.
- `leaderboards.style_control`: publication date and first-observed timestamp for the style-controlled dataset.
- `telegram_sent_at_utc`: the message timestamp returned by Telegram's API after it accepted the alert.
- `github_run_id` and `github_run_url`: the corresponding GitHub Actions run.
- `new_models`: model names, ranks, and scores grouped by scoring mode.

**Important timing limitation:** Arena's dataset supplies a publication date, not an exact publication time. The tracker therefore cannot prove the exact minute a model first appeared on Arena's website. Each `snapshot_first_observed_at_utc` is when the monitor first downloaded a dataset snapshot containing that model. Telegram's timestamp is returned by Telegram's server and is recorded to the nearest second.

All timestamps use UTC to make comparisons reliable across time zones. The JSONL log is committed to the public repository alongside the model baselines; it contains public leaderboard data and timestamps, never Telegram credentials or chat IDs.

## Deploy it (free on a public GitHub repository)

### 1. Create a repository

1. Sign in to GitHub and create a **new public repository** (for example, `arena-text-alerts`).
2. Upload the contents of this folder, including the `.github/workflows/arena-monitor.yml` file.
3. In the repository, open **Settings → Actions → General → Workflow permissions** and select **Read and write permissions**. Save.
4. Open the **Actions** tab and enable workflows if GitHub prompts you.

Public repositories can use standard GitHub-hosted runners without charge. GitHub's schedule minimum is five minutes. Private repository usage may count against included minutes.

### 2. Choose Telegram, Discord, or both

#### Telegram

1. In Telegram, message **@BotFather** and send `/newbot`.
2. Follow its prompts. Copy the bot token.
3. Open your new bot and press **Start** (or send it `/start`).
4. Get your chat ID by opening this URL in a browser, replacing `YOUR_TOKEN` with the bot token:
   `https://api.telegram.org/botYOUR_TOKEN/getUpdates`
5. Find `message` → `chat` → `id` in the response. Keep the token private.

In GitHub, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
- `TELEGRAM_BOT_TOKEN` = the BotFather token
- `TELEGRAM_CHAT_ID` = your chat ID

#### Discord

1. In Discord, open the channel where you want alerts.
2. Open **Edit Channel → Integrations → Webhooks → New Webhook**.
3. Copy the webhook URL.
4. Add a GitHub Actions repository secret named `DISCORD_WEBHOOK_URL` with that URL.

You can configure Telegram, Discord, or both. Leave the unused secret(s) unset.

### 3. Test Telegram delivery

After adding both Telegram secrets, open **Actions → Arena Text raw and style-control leaderboard monitor → Run workflow**. Set **Send a Telegram test message before checking the leaderboard** to **true**, then run it. You should receive a message saying that Telegram notifications are connected.

The test option is manual-only. Scheduled runs do not send test messages.

### 4. Start the monitor

1. Open the repository's **Actions** tab.
2. Select **Arena Text raw and style-control leaderboard monitor**.
3. Click **Run workflow** once. Leave the Telegram test option off unless you want another test message.
4. The first successful run creates the raw baseline. The first upgraded run also silently creates the style-control baseline without sending alerts for models already listed there.
5. Subsequent checks run every five minutes and notify you when a new model appears in either published raw Text / Overall dataset.

Alerts depend on when Arena publishes each dataset snapshot, so checking every five minutes does not mean Arena publishes new data every five minutes.

## Automatic repository activity refresh

The separate **Keep repository active** workflow adds a small dated commit on the 1st and 15th of each month. This provides regular repository activity so the scheduled monitoring workflows are less likely to be disabled by GitHub's 60-day inactivity policy. You do not need to do this manually.

## Troubleshooting

- Check **Actions → latest run → Check Arena Text Overall datasets (style control off and on)** for errors.
- If either published Parquet file is temporarily unavailable or its schema changes, the job fails visibly and retains both existing model baselines.
- GitHub may delay scheduled workflows during high-load periods; the five-minute cron is a polling target, not a guaranteed delivery time.
- Public repositories can have scheduled workflows disabled after 60 days without repository activity. The keep-alive workflow adds regular commits, but check the Actions tab occasionally to make sure both workflows remain enabled.

## Data and security

The BotFather token and Discord webhook belong in GitHub Actions secrets, never in source files. The tracked state files contain public model names, ranks, and displayed scores only. The alert timing log contains timestamps and public model information only.

Dataset attribution: Arena Team, [Arena Leaderboard Dataset](https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset), licensed under CC-BY-4.0.
