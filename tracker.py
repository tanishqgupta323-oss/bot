#!/usr/bin/env python3
"""Tracker: checks how price moved 15 and 60 minutes after each logged alert."""
from __future__ import annotations

import bisect
import json
import sys
from datetime import datetime, timedelta
from typing import Any

import requests

from bot import ALERTS_LOG_FILE, parse_datetime, story_words, utc_now

HEADERS = {"User-Agent": "Mozilla/5.0"}
SYMBOLS = {"EURUSD": "EURUSD=X", "XAUUSD": "GC=F", "BTCUSD": "BTC-USD"}
PAIR_KEYS = {"EURUSD": "eurusd", "XAUUSD": "xauusd", "BTCUSD": "btcusd"}
HORIZONS = (15, 60)
# A move smaller than this (in %) counts as "flat" and is not scored as hit or miss.
NOISE_PCT = {"EURUSD": 0.03, "XAUUSD": 0.08, "BTCUSD": 0.15}
MAX_GAP_MIN = 10   # if no candle within 10 minutes, the market was closed -> no price


def fetch_candles(pair: str):
    """Return (timestamps, closes) of 1-minute candles for the last 7 days, or None."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOLS[pair]}"
    try:
        response = requests.get(url, params={"interval": "1m", "range": "7d"}, headers=HEADERS, timeout=20)
        if response.status_code != 200:
            print(f"{pair}: HTTP {response.status_code}")
            return None
        result = response.json()["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        points = sorted((float(t), float(c)) for t, c in zip(stamps, closes) if c is not None)
        return [p[0] for p in points], [p[1] for p in points]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"{pair}: {type(exc).__name__}")
        return None


def price_at(candles, when: datetime):
    """Last close at or before `when`; None if the market had no candle near that time."""
    times, closes = candles
    ts = when.timestamp()
    index = bisect.bisect_right(times, ts) - 1
    if index < 0 or ts - times[index] > MAX_GAP_MIN * 60:
        return None
    return closes[index]


def verdict(bias: str, pct: float | None, pair: str) -> str | None:
    if pct is None or bias not in ("BULLISH", "BEARISH"):
        return None
    if abs(pct) < NOISE_PCT[pair]:
        return "flat"
    went_up = pct > 0
    return "hit" if (bias == "BULLISH") == went_up else "miss"


def pending(rows: list[Any], now: datetime) -> bool:
    for row in rows:
        if not isinstance(row, dict):
            continue
        sent = parse_datetime(row.get("sent_at"))
        if sent is None:
            continue
        checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
        for horizon in HORIZONS:
            if str(horizon) not in checks and now >= sent + timedelta(minutes=horizon + 2):
                return True
    return False


def update_checks(rows: list[Any], now: datetime, candles: dict[str, Any]) -> int:
    changed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        sent = parse_datetime(row.get("sent_at"))
        if sent is None:
            continue
        if not isinstance(row.get("checks"), dict):
            row["checks"] = {}
        checks = row["checks"]
        for horizon in HORIZONS:
            key = str(horizon)
            if key in checks or now < sent + timedelta(minutes=horizon + 2):
                continue
            result: dict[str, float | None] = {}
            incomplete = False
            for pair in SYMBOLS:
                data = candles.get(pair)
                if data is None:
                    incomplete = True
                    result[pair] = None
                    continue
                start = price_at(data, sent)
                end = price_at(data, sent + timedelta(minutes=horizon))
                result[pair] = round((end - start) / start * 100, 4) if start and end else None
            if incomplete and now < sent + timedelta(hours=6):
                continue   # price data unavailable right now; retry on the next run
            checks[key] = result
            changed += 1
    return changed


def build_report(rows: list[Any]) -> str:
    """Per-source accuracy over unique stories (same story from many sites counts once)."""
    valid = [r for r in rows if isinstance(r, dict) and parse_datetime(r.get("sent_at"))]
    valid.sort(key=lambda r: parse_datetime(r["sent_at"]))
    recent: list[tuple[datetime, set[str]]] = []
    stats: dict[str, dict[str, Any]] = {}
    duplicates = 0
    unique_total = 0

    for row in valid:
        sent = parse_datetime(row["sent_at"])
        words = set(story_words(str(row.get("title", ""))))
        recent = [(t, w) for t, w in recent if t >= sent - timedelta(hours=8)]
        if len(words) >= 3 and any(
            len(words & other) >= 3 and len(words & other) / max(1, min(len(words), len(other))) >= 0.6
            for _, other in recent
        ):
            duplicates += 1
            continue
        recent.append((sent, words))
        unique_total += 1

        source = str(row.get("source") or "unknown")
        entry = stats.setdefault(source, {"stories": 0, "delays": [],
                                          "15": {"hit": 0, "miss": 0, "flat": 0},
                                          "60": {"hit": 0, "miss": 0, "flat": 0}})
        entry["stories"] += 1
        published = parse_datetime(row.get("published_at"))
        if published is not None:
            entry["delays"].append((sent - published).total_seconds() / 60)
        checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
        for horizon in ("15", "60"):
            moves = checks.get(horizon)
            if not isinstance(moves, dict):
                continue
            for pair, key in PAIR_KEYS.items():
                outcome = verdict(str(row.get(key, "")).upper(), moves.get(pair), pair)
                if outcome:
                    entry[horizon][outcome] += 1

    def hit_rate(counts: dict[str, int]) -> str:
        decided = counts["hit"] + counts["miss"]
        return f"{counts['hit'] / decided * 100:.0f}% ({counts['hit']}/{decided})" if decided else "-"

    lines = [
        "SOURCE ACCURACY REPORT",
        f"Alerts logged: {len(valid)} | unique stories: {unique_total} | duplicate alerts ignored: {duplicates}",
        "A pair counts as a hit if price moved the way the bias said, beyond a small noise band.",
        "-" * 78,
        f"{'SOURCE':<18}{'STORIES':>8}  {'15 MIN HIT RATE':<20}{'60 MIN HIT RATE':<20}{'AVG DELAY':>10}",
        "-" * 78,
    ]
    for source, entry in sorted(stats.items(), key=lambda item: -item[1]["stories"]):
        delay = f"{sum(entry['delays']) / len(entry['delays']):.0f} min" if entry["delays"] else "n/a"
        lines.append(
            f"{source:<18}{entry['stories']:>8}  {hit_rate(entry['15']):<20}{hit_rate(entry['60']):<20}{delay:>10}"
        )
    lines.append("-" * 78)
    lines.append("Small samples are noise. Treat results as hints until you have 50-100 stories per source.")
    return "\n".join(lines)


def main() -> int:
    dry = "--dry" in sys.argv
    report_only = "--report" in sys.argv
    if not ALERTS_LOG_FILE.exists():
        print("tracker: no alerts_log.json yet")
        return 0
    try:
        loaded = json.loads(ALERTS_LOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("tracker: could not read alerts log:", type(exc).__name__)
        return 0
    rows = loaded if isinstance(loaded, list) else []

    now = utc_now()
    if not report_only:
        if pending(rows, now):
            candles = {pair: fetch_candles(pair) for pair in SYMBOLS}
            changed = update_checks(rows, now, candles)
            if changed and not dry:
                ALERTS_LOG_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"tracker: {changed} checks " + ("computed (dry run, not saved)" if dry else "saved"))
        else:
            print("tracker: nothing due")
    if dry or report_only:
        print(build_report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())