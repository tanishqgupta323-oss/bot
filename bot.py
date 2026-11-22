#!/usr/bin/env python3
"""Major US data alert bot: CPI, PPI, NFP and FOMC heads-up messages on Telegram.

Sends three alerts before each release: 1 hour, 10 minutes and 3 minutes to go.
No news feeds, no AI and no Bullish/Bearish calls. Times come from a free calendar feed.

Usage:
  python bot.py          normal run (the GitHub Actions workflow runs this every 5 minutes)
  python bot.py --plan   print the upcoming releases and when alerts will fire (sends nothing)
  python bot.py --test   send one sample message to Telegram
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ----------------------------- settings -----------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
CALENDAR_URL = os.getenv("CALENDAR_URL", "https://nfs.faireconomy.media/ff_calendar_thisweek.json").strip()
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))

CURRENCY = "USD"
ALERT_MINUTES = (60, 10, 3)           # alerts before each release
LABELS = {60: "1 HOUR TO GO", 10: "10 MIN TO GO", 3: "3 MIN TO GO"}
LOOKAHEAD_SECONDS = 330               # a run waits up to this long to send an alert at its exact minute
CALENDAR_REFRESH_SECONDS = 3 * 3600   # normal calendar refresh
EMPTY_REFRESH_SECONDS = 3600          # refresh sooner when no release is known (week roll-over)

# Which calendar events count. Edit these patterns to add or remove events.
FAMILIES = {
    "CPI": {"name": "US CPI (inflation)", "include": r"\bCPI\b"},
    "PPI": {"name": "US PPI (producer prices)", "include": r"\bPPI\b"},
    "NFP": {"name": "US NFP (jobs report)",
            "include": r"non-?farm|\bNFP\b|^unemployment rate$|^average hourly earnings"},
    "FOMC": {"name": "FOMC (Fed rate decision)", "include": r"\bFOMC\b|federal funds rate"},
}
EXCLUDE = r"\bADP\b|minutes|speaks|member|revis|press conference|stability|testif"

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
LINE = "━━━━━━━━━━━━━━━━━━"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MajorDataAlertBot/3.0)"}
LOG = logging.getLogger("databot")


def utc_now() -> datetime:
    return datetime.now(UTC)


def esc(value: Any) -> str:
    return html.escape(" ".join(str(value if value is not None else "-").split()), quote=True)


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO timestamp with a timezone offset into UTC; naive timestamps are rejected."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def fmt_ist(value: datetime) -> str:
    return value.astimezone(IST).strftime("%a %d %b, %I:%M %p") + " IST"


# ----------------------------- releases -----------------------------
@dataclass
class Release:
    family: str
    when: datetime
    lines: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return FAMILIES[self.family]["name"]

    def key(self, minutes: int) -> str:
        return f"{self.family}|{self.when.isoformat()}|{minutes}"


def build_releases(rows: list[Any]) -> list[Release]:
    """Group matching calendar rows by release (family and exact time)."""
    groups: dict[tuple[str, datetime], Release] = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("country", "")).upper() != CURRENCY:
            continue
        title = str(row.get("title") or "").strip()
        if not title or re.search(EXCLUDE, title, re.IGNORECASE):
            continue
        family = next((f for f, spec in FAMILIES.items() if re.search(spec["include"], title, re.IGNORECASE)), None)
        when = parse_time(row.get("date"))
        if family is None or when is None:
            continue
        release = groups.setdefault((family, when), Release(family, when))
        release.lines.append((title, str(row.get("forecast") or "-"), str(row.get("previous") or "-")))
    return sorted(groups.values(), key=lambda r: r.when)


# ----------------------------- state -----------------------------
def load_state() -> dict[str, Any]:
    state: dict[str, Any] = {"version": 3, "calendar_raw": [], "fetched_at": None, "sent": []}
    if STATE_FILE.exists():
        try:
            loaded = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                if isinstance(loaded.get("calendar_raw"), list):
                    state["calendar_raw"] = loaded["calendar_raw"]
                state["fetched_at"] = loaded.get("fetched_at")
                if isinstance(loaded.get("sent"), list):
                    state["sent"] = [str(x) for x in loaded["sent"]]
        except (OSError, ValueError) as exc:
            LOG.error("Could not read state file (%s); starting fresh", type(exc).__name__)
    return state


def save_state(state: dict[str, Any]) -> None:
    state["sent"] = state["sent"][-500:]
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=STATE_FILE.name + ".", suffix=".tmp", dir=str(STATE_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, STATE_FILE)
    finally:
        if os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass


# ----------------------------- calendar -----------------------------
def fetch_calendar() -> list[Any] | None:
    for attempt in range(3):
        try:
            response = requests.get(CALENDAR_URL, headers=HEADERS, timeout=20)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    return data
                LOG.warning("Calendar returned an unexpected format")
                return None
            LOG.warning("Calendar HTTP %s", response.status_code)
        except (requests.RequestException, ValueError) as exc:
            LOG.warning("Calendar request failed (%s)", type(exc).__name__)
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    return None


def ensure_calendar(state: dict[str, Any], now: datetime, force: bool = False) -> list[Release]:
    fetched = parse_time(state.get("fetched_at"))
    age = (now - fetched).total_seconds() if fetched else None
    known = [r for r in build_releases(state["calendar_raw"]) if r.when > now]
    stale = age is None or age > CALENDAR_REFRESH_SECONDS or (not known and age > EMPTY_REFRESH_SECONDS)
    if force or stale:
        rows = fetch_calendar()
        if rows is not None:
            state["calendar_raw"] = rows
            state["fetched_at"] = now.isoformat()
            LOG.info("Calendar refreshed: %d rows", len(rows))
        else:
            state["fetched_at"] = state.get("fetched_at") or None
            LOG.warning("Calendar refresh failed; using the saved copy")
    return build_releases(state["calendar_raw"])


# ----------------------------- telegram -----------------------------
def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        LOG.error("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID missing")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(2):
        try:
            response = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4000], "parse_mode": "HTML",
                      "disable_web_page_preview": "true"},
                timeout=20,
            )
            if response.status_code == 200 and response.json().get("ok") is True:
                return True
            LOG.error("Telegram HTTP %s", response.status_code)
        except (requests.RequestException, ValueError) as exc:
            LOG.error("Telegram error (%s)", type(exc).__name__)
        time.sleep(2)
    return False


def build_message(release: Release, minutes: int, now: datetime) -> str:
    left = max(1, round((release.when - now).total_seconds() / 60))
    rows = "\n".join(f"• {esc(t)}  |  forecast {esc(f)}  |  previous {esc(p)}" for t, f, p in release.lines)
    return (
        f"⏰ <b>{esc(release.name)} · {LABELS[minutes]}</b>\n{LINE}\n"
        f"🕒 {esc(fmt_ist(release.when))} (in about {left} min)\n\n"
        f"{rows}\n\n"
        "⚠️ Expect sharp, fast moves and wider spreads around the release."
    )


# ----------------------------- alert logic -----------------------------
def plan_alerts(releases: list[Release], sent: set[str], now: datetime) -> list[tuple[datetime, Release, int, list[int]]]:
    """Return (send_at, release, minutes, skipped_minutes) for alerts due now or within the look-ahead."""
    plan = []
    horizon = now + timedelta(seconds=LOOKAHEAD_SECONDS)
    for release in releases:
        if (release.when - now).total_seconds() <= 20:
            continue                                   # already released
        pending = [m for m in ALERT_MINUTES if release.key(m) not in sent]
        if not pending:
            continue
        due_now = [m for m in pending if release.when - timedelta(minutes=m) <= now]
        upcoming = [m for m in pending if now < release.when - timedelta(minutes=m) <= horizon]
        if due_now:
            tightest = min(due_now)                    # first seen late: send only the closest one
            plan.append((now, release, tightest, [m for m in due_now if m != tightest]))
        for m in upcoming:
            plan.append((release.when - timedelta(minutes=m), release, m, []))
    plan.sort(key=lambda item: item[0])
    return plan


def run() -> int:
    state = load_state()
    now = utc_now()
    releases = ensure_calendar(state, now)
    sent = set(state["sent"])
    for send_at, release, minutes, skipped in plan_alerts(releases, sent, now):
        for m in skipped:
            sent.add(release.key(m))
        wait = (send_at - utc_now()).total_seconds()
        if wait > 0:
            LOG.info("Waiting %.0fs to send the %d-minute alert for %s", wait, minutes, release.family)
            time.sleep(wait)
        current = utc_now()
        if (release.when - current).total_seconds() <= 10:
            continue
        if send_telegram(build_message(release, minutes, current)):
            sent.add(release.key(minutes))
            LOG.info("Sent %s %d-minute alert", release.family, minutes)
        state["sent"] = sorted(sent)
        save_state(state)
    state["sent"] = sorted(sent)
    save_state(state)
    return 0


def plan_command() -> int:
    state = load_state()
    now = utc_now()
    releases = [r for r in ensure_calendar(state, now, force=True) if r.when > now]
    if not releases:
        print("No CPI / PPI / NFP / FOMC release is in the calendar yet (the feed may not have rolled to the new week).")
        return 0
    print("Upcoming releases and alert times (IST):")
    for release in releases:
        times = ", ".join(f"{LABELS[m]} at {fmt_ist(release.when - timedelta(minutes=m)).split(', ')[1]}" for m in ALERT_MINUTES)
        print(f"\n{release.name} - {fmt_ist(release.when)}")
        for title, forecast, previous in release.lines:
            print(f"   {title} (forecast {forecast}, previous {previous})")
        print(f"   alerts: {times}")
    return 0


def test_command() -> int:
    sample = Release("CPI", utc_now() + timedelta(minutes=60), [("CPI m/m", "0.3%", "0.4%"), ("Core CPI m/m", "0.3%", "0.3%")])
    ok = send_telegram("🧪 <b>Test message</b> - formatting preview below.\n\n" + build_message(sample, 60, utc_now()))
    print("test message sent" if ok else "test message FAILED")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    if "--plan" in argv:
        return plan_command()
    if "--test" in argv:
        return test_command()
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
