# Arena Text (raw) new-model notifications

A small GitHub Actions monitor for the exact Arena leaderboard URL:

<https://arena.ai/leaderboard/chat/text?scoring=raw>

It checks every five minutes from GitHub-hosted runners, compares model names with the previous run, and sends a Telegram message, a Discord message, or both when a previously unseen model appears. Your computer does not need to be on.

## What it does and does not do

- Tracks the **Text Arena / Overall / raw-score** leaderboard page.
- Alerts only on **new model names**; rank and score changes do not create alerts.
- The first run saves a baseline and does not spam you with all existing models.
- The state is kept in `state/models.json` in this repository.
- Checks every five minutes, but alerts cannot arrive before Arena itself publishes the changed page. GitHub scheduled runs can also start late.
- This is a community-style page monitor, not an official Arena integration. If Arena changes its page markup, the job fails visibly rather than replacing the saved model list with an empty list.

## Deploy it (free on a public GitHub repository)

### 1. Create a repository

1. Sign in to GitHub and create a **new public repository** (for example, `arena-text-alerts`).
2. Upload the contents of this folder, including the `.github/workflows/arena-monitor.yml` file and the `state/` folder.
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

### 3. Start the monitor

1. Open the repository's **Actions** tab.
2. Select **Arena Text leaderboard monitor**.
3. Click **Run workflow** once.
4. The first run creates a baseline of current models and does not send a notification by default.
5. Subsequent scheduled runs notify you when a new model name is added.

The workflow runs automatically every five minutes thereafter.

## Optional: notify about all current models on first run

In `.github/workflows/arena-monitor.yml`, change:

```yaml
NOTIFY_ON_FIRST_RUN: 'false'
```

to `true`, commit the change, and run the workflow manually. This can send a long initial alert, so it is off by default.

## Troubleshooting

- Check **Actions → latest run → Check Arena Text raw leaderboard** for errors.
- If Arena changes the page's HTML structure, `monitor.py` will stop with a parser error and retain the existing state. Update the parser before resuming.
- GitHub may delay scheduled workflows during high-load periods; the five-minute cron is a polling target, not a guaranteed delivery time.
- Public repositories can have scheduled workflows disabled after 60 days without repository activity. Re-enable them in Actions if that happens.

## Data and security

The bot token and Discord webhook belong in GitHub Actions secrets, never in source files. The tracked state file contains public model names, ranks, and displayed scores only.
