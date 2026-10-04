# 📡 Trading News Alert Bot

A serverless Telegram bot that monitors economic calendar events and live financial news for **EURUSD, XAUUSD, and BTCUSD**, delivering AI-powered Bullish/Bearish market sentiment using Gemini.

## ✨ Features

* 🔔 High-impact economic event alerts at 60, 30, and 10 minutes before release.
* 📰 Live market-moving news alerts.
* 🤖 Gemini-powered sentiment analysis for each trading pair.
* 📊 Actual vs. forecast updates when data is available in headlines.
* 💬 Instant Telegram notifications.
* ⚙️ Automated execution every 5 minutes using GitHub Actions.
* 🔐 Secure API key management with GitHub Secrets.
* 💸 Designed to run using free-tier services.

## 🛠️ Tech Stack

* **Language:** Python 3.12
* **Automation:** GitHub Actions + cron-job.org
* **AI:** Gemini API
* **Notifications:** Telegram Bot API
* **Data Sources:** Economic calendar JSON feed and RSS news feeds

## 🚀 Setup

1. Create a Telegram bot using [@BotFather](https://t.me/BotFather).
2. Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/).
3. Add these GitHub Actions secrets:

   * `TELEGRAM_TOKEN`
   * `TELEGRAM_CHAT_ID`
   * `GEMINI_API_KEY`
4. Configure a GitHub token with permission to trigger workflows and update repository state.
5. Set up cron-job.org to trigger the GitHub Actions workflow every 5 minutes.

### Run Locally

```bash
pip install requests python-dotenv
python bot.py
```

Configure the required environment variables before running.

## ⚠️ Limitations

* News feeds and AI responses may be delayed or rate-limited.
* Actual economic figures may not always be available in headlines.
* Bullish/Bearish sentiment is an AI interpretation, not a guaranteed prediction.

## 🗺️ Roadmap

* Add a fallback AI provider.
* Integrate structured economic data sources.
* Improve alert reliability and add weekly performance summaries.

## 📄 Disclaimer

This project is intended for educational and personal use. It provides market news and sentiment alerts, **not financial advice or guaranteed trading signals**.
