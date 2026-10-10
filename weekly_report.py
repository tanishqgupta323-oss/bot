#!/usr/bin/env python3
"""Send the weekly source-accuracy report to Telegram. Use --dry to only print it."""
from __future__ import annotations

import json
import sys

from bot import ALERTS_LOG_FILE, Config, HttpClient, Telegram, configure_logging
from tracker import build_telegram_report


def main() -> int:
    configure_logging()
    if not ALERTS_LOG_FILE.exists():
        print("weekly report: no alerts_log.json yet")
        return 0
    try:
        loaded = json.loads(ALERTS_LOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("weekly report: could not read alerts log:", type(exc).__name__)
        return 1
    rows = loaded if isinstance(loaded, list) else []
    message = build_telegram_report(rows, days=7)

    if "--dry" in sys.argv:
        print(message)
        return 0

    config = Config.from_env()
    telegram = Telegram(HttpClient(config.request_timeout), config.telegram_token, config.telegram_chat_id)
    if telegram.send(message):
        print("weekly report sent")
        return 0
    print("weekly report: Telegram send failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())