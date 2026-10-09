#!/usr/bin/env python3
"""Price Check: latest price and short-term moves for EURUSD, XAUUSD and BTCUSD."""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

import requests

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
HEADERS = {"User-Agent": "Mozilla/5.0"}
SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "XAUUSD": "GC=F",      # COMEX gold futures: close to spot gold, not identical
    "BTCUSD": "BTC-USD",
}
DECIMALS = {"EURUSD": 5, "XAUUSD": 2, "BTCUSD": 2}
SPARK = "▁▂▃▄▅▆▇█"
GREEN, RED, DIM, BOLD, RESET = "\033[92m", "\033[91m", "\033[90m", "\033[1m", "\033[0m"
WIDTH = 78


def fetch_candles(pair: str, retries: int = 2) -> list[tuple[datetime, float]]:
    """Return 1-minute (time, close) candles for the last day, or [] if unavailable."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOLS[pair]}"
    params = {"interval": "1m", "range": "1d"}
    for _ in range(retries):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=15)
            if response.status_code != 200:
                print(f"{DIM}{pair}: HTTP {response.status_code}{RESET}")
            else:
                result = response.json()["chart"]["result"][0]
                stamps = result["timestamp"]
                closes = result["indicators"]["quote"][0]["close"]
                return [
                    (datetime.fromtimestamp(t, UTC), float(c))
                    for t, c in zip(stamps, closes)
                    if c is not None
                ]
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            print(f"{DIM}{pair}: {type(exc).__name__}{RESET}")
        time.sleep(2)
    return []


def pct_change(candles: list[tuple[datetime, float]], minutes: int) -> float | None:
    """Percent move between the latest close and the close `minutes` earlier."""
    if not candles:
        return None
    target = candles[-1][0] - timedelta(minutes=minutes)
    earlier = [price for stamp, price in candles if stamp <= target]
    if not earlier or earlier[-1] == 0:
        return None
    return (candles[-1][1] - earlier[-1]) / earlier[-1] * 100


def sparkline(values: list[float], width: int = 24) -> str:
    if len(values) < 2:
        return ""
    step = max(1, len(values) // width)
    sample = values[::step][-width:]
    low, high = min(sample), max(sample)
    if high == low:
        return SPARK[0] * len(sample)
    scale = len(SPARK) - 1
    return "".join(SPARK[int((v - low) / (high - low) * scale)] for v in sample)


def colored_pct(value: float | None) -> str:
    if value is None:
        return f"{DIM}{'n/a':<10}{RESET}"
    arrow = "▲" if value > 0 else "▼" if value < 0 else "▬"
    color = GREEN if value > 0 else RED if value < 0 else DIM
    return f"{color}{arrow} {value:+.2f}%".ljust(10 + len(color)) + RESET


def render() -> None:
    now = datetime.now(IST)
    print(f"\n{BOLD}📈 PRICE CHECK{RESET}   {DIM}{now:%d %b %Y, %I:%M:%S %p} IST{RESET}")
    print("─" * WIDTH)
    print(f"{BOLD}{'PAIR':<8}{'PRICE':>12}   {'15 MIN':<10}  {'60 MIN':<10}  LAST HOUR{RESET}")
    print("─" * WIDTH)
    for pair in SYMBOLS:
        candles = fetch_candles(pair)
        if not candles:
            print(f"{BOLD}{pair:<8}{RESET}{DIM}{'no data':>12}{RESET}")
            continue
        last_time, last_price = candles[-1]
        age_minutes = (datetime.now(UTC) - last_time).total_seconds() / 60
        closes = [price for _, price in candles[-60:]]
        stale = "" if age_minutes <= 30 else f"  {DIM}(stale {age_minutes / 60:.1f}h, market closed?){RESET}"
        print(
            f"{BOLD}{pair:<8}{RESET}{last_price:>12.{DECIMALS[pair]}f}   "
            f"{colored_pct(pct_change(candles, 15))}  {colored_pct(pct_change(candles, 60))}  "
            f"{sparkline(closes)}{stale}"
        )
    print("─" * WIDTH)
    print(f"{DIM}XAUUSD uses COMEX gold futures (GC=F), which can differ slightly from spot gold.{RESET}\n")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if "--watch" in sys.argv:
        try:
            while True:
                print("\033[2J\033[H", end="")
                render()
                time.sleep(60)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        render()


if __name__ == "__main__":
    main()