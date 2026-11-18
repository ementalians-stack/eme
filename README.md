# Arena Text (raw) new-model notifications

A GitHub Actions monitor for new models on Arena's **Text / Overall / raw-score** leaderboard.

- Official leaderboard: <https://arena.ai/leaderboard/chat/text?scoring=raw>
- Official published dataset: <https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset>

The monitor downloads Arena's small, officially published Parquet snapshot and selects the `overall` rows locally. It does not scrape the live Arena webpage or depend on the dataset-viewer filter API. It checks every five minutes and sends a Telegram message, a Discord message, or both when a previously unseen model appears. Your computer does not need to be on.

## What it does and does not do

- Tracks the **Text Arena / Overall / raw-score** leaderboard.
- Uses the dataset's `text` subset, which Arena documents as raw (non-style-controlled) Text ratings, filtered to the `overall` category.
- Alerts only on **new model names**; rank and score changes do not create alerts.
- The first run saves a baseline and does not spam you with all existing models.
- The state is kept in `state/models.json` in this repository.
- Arena updates the public dataset on days when it publishes leaderboard updates. The five-minute check detects updates after they appear in that dataset; this is not a live feed and cannot alert before Arena publishes the data.
- The monitor also runs automatically when its code, dependencies, or workflow file are updated on the `main` branch.
- The public dataset is licensed CC-BY-4.0. Alerts link to both the official leaderboard and dataset.

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

After adding both Telegram secrets, open **Actions → Arena Text leaderboard monitor → Run workflow**. Set **Send a Telegram test message before checking the leaderboard** to **true**, then run it. You should receive a message saying that Telegram notifications are connected.

The test option is manual-only. Scheduled runs do not send test messages.

### 4. Start the monitor

1. Open the repository's **Actions** tab.
2. Select **Arena Text leaderboard monitor**.
3. Click **Run workflow** once. Leave the Telegram test option off unless you want another test message.
4. The first successful run creates a baseline of current models and does not send a new-model alert by default.
5. Subsequent checks run every five minutes and notify you when a new model appears in Arena's published raw Text / Overall dataset.

Alerts depend on when Arena publishes a new dataset snapshot, so checking every five minutes does not mean Arena publishes new data every five minutes.

## Automatic repository activity refresh

The separate **Keep repository active** workflow adds a small dated commit on the 1st and 15th of each month. This provides regular repository activity so the scheduled monitoring workflows are less likely to be disabled by GitHub's 60-day inactivity policy. You do not need to do this manually.

## Optional: notify about all current models on first run

In `.github/workflows/arena-monitor.yml`, change:

```yaml
NOTIFY_ON_FIRST_RUN: 'false'
```

to `true`, commit the change, and run the workflow manually. This can send a long initial alert, so it is off by default.

## Troubleshooting

- Check **Actions → latest run → Check Arena Text raw leaderboard dataset** for errors.
- If the published Parquet file is temporarily unavailable or its schema changes, the job fails visibly and retains the existing state.
- GitHub may delay scheduled workflows during high-load periods; the five-minute cron is a polling target, not a guaranteed delivery time.
- Public repositories can have scheduled workflows disabled after 60 days without repository activity. The keep-alive workflow adds regular commits, but check the Actions tab occasionally to make sure both workflows remain enabled.

## Data and security

The BotFather token and Discord webhook belong in GitHub Actions secrets, never in source files. The tracked state file contains public model names, ranks, and displayed scores only.

Dataset attribution: Arena Team, [Arena Leaderboard Dataset](https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset), licensed under CC-BY-4.0.
