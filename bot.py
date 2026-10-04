import os
import json
import html
import email.utils
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
CURRENCIES = ["USD", "EUR"]
TEST_MODE = True   # True karoge to har run par "hi telegram ok" test message aayega

ALERT_MINUTES = [60, 30, 10]
RESOLVE_WINDOW_MIN = 90   # release ke baad itne minute tak actual dhundhna
MAX_ATTEMPTS = 8
CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
RSS_FEEDS = [
    "https://www.forexlive.com/feed",
    "https://www.fxstreet.com/rss/news",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]
STATE_FILE = "state.json"
IST = timezone(timedelta(hours=5, minutes=30))
HEADERS = {"User-Agent": "Mozilla/5.0 (news-alert-bot)"}

AFFECTS = {
    "USD": ["EURUSD", "XAUUSD", "BTCUSD"],
    "EUR": ["EURUSD"],
}
LINE = "━━━━━━━━━━━━━━━━━━"
ARROW = {"BULLISH": "▲", "BEARISH": "▼", "NEUTRAL": "▬"}
IMPACT_ICON = {"HIGH": "🔥", "MEDIUM": "⚡", "LOW": "💧"}


# ---------- helpers ----------
def load_state():
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
    state.setdefault("sent", [])
    state.setdefault("resolved", [])
    state.setdefault("tries", {})
    state.setdefault("seen", None)
    return state


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def send_telegram(text):
    # Public repo hai, isliye logs mein token ya chat id kabhi print nahi karte
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            timeout=15,
        )
        print("telegram status:", r.status_code)
    except Exception as e:
        print("telegram error:", type(e).__name__)


def ask_gemini(prompt):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
        }
        r = requests.post(url, headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=60)
        if r.status_code != 200:
            print("gemini HTTP status:", r.status_code)
            print("gemini response:", r.text[:300])
            return None
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except Exception as e:
        print("gemini error:", type(e).__name__)
        return None


def esc(x):
    return html.escape(str(x))


def fmt_ist(dt):
    return dt.astimezone(IST).strftime("%d %b, %I:%M %p") + " IST"


def norm(bias):
    b = str(bias).upper()
    return b if b in ARROW else "NEUTRAL"


def ico(bias):
    return {"BULLISH": "🟢", "BEARISH": "🔴"}.get(norm(bias), "⚪")


def bias_line(pair, bias):
    b = norm(bias)
    return f"{ico(b)} <b>{pair}</b>   {b} {ARROW[b]}"


def bias_block(item):
    return "\n".join([
        bias_line("EURUSD", item.get("eurusd")),
        bias_line("XAUUSD", item.get("xauusd")),
        bias_line("BTCUSD", item.get("btcusd")),
    ])


# ---------- calendar ----------
def get_events(state, now):
    tried = state.get("cal_tried")
    has_cal = bool(state.get("calendar"))
    wait = 3 * 3600 if has_cal else 600
    stale = (not tried) or (now - datetime.fromisoformat(tried)).total_seconds() > wait
    if stale:
        state["cal_tried"] = now.isoformat()
        try:
            data = requests.get(CAL_URL, headers=HEADERS, timeout=20).json()
            if isinstance(data, list):
                state["calendar"] = data
            else:
                print("calendar: unexpected response")
        except Exception as e:
            print("calendar error:", type(e).__name__)

    events = []
    for e in state.get("calendar", []):
        if e.get("impact") != "High":
            continue
        if CURRENCIES and e.get("country", "").upper() not in CURRENCIES:
            continue
        try:
            t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        except Exception:
            continue
        events.append({
            "id": f"{e['date']}|{e.get('title')}|{e.get('country')}",
            "title": e.get("title", "?"),
            "country": e.get("country", "?"),
            "time": t,
            "forecast": e.get("forecast") or "-",
            "previous": e.get("previous") or "-",
        })
    return events


def pre_alerts(events, state, now):
    for ev in events:
        left = (ev["time"] - now).total_seconds() / 60
        if left <= 0 or left > max(ALERT_MINUTES):
            continue
        w = min(x for x in ALERT_MINUTES if left <= x)
        if f"{ev['id']}|{w}" in state["sent"]:
            continue
        for x in ALERT_MINUTES:
            if x >= w:
                state["sent"].append(f"{ev['id']}|{x}")
        mins = max(1, round(left))
        pairs = " · ".join(AFFECTS.get(ev["country"], ["EURUSD"]))
        send_telegram(
            f"🔔 <b>NEWS ALERT · in about {mins} min</b>\n"
            f"{LINE}\n"
            f"📅 <b>{esc(ev['title'])}</b> ({esc(ev['country'])})\n"
            f"🔥 Impact: <b>HIGH</b>\n"
            f"🕒 {fmt_ist(ev['time'])}\n"
            f"📈 Forecast: {esc(ev['forecast'])}  |  Previous: {esc(ev['previous'])}\n\n"
            f"🎯 <b>Watch:</b> {pairs}\n"
            f"⚠️ Expect sharp moves and wider spreads around the release."
        )


# ---------- headlines ----------
def fetch_headlines():
    items = []
    for url in RSS_FEEDS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            print("feed error:", url, type(e).__name__)
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or title).strip()
            when = None
            try:
                when = email.utils.parsedate_to_datetime(it.findtext("pubDate"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except Exception:
                pass
            if title:
                items.append({"title": title, "link": link, "time": when})
    items.sort(key=lambda x: x["time"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items


def resolve_events(events, state, now):
    headlines = None
    for ev in events:
        age = (now - ev["time"]).total_seconds() / 60
        if age < 2 or age > RESOLVE_WINDOW_MIN:
            continue
        if ev["id"] in state["resolved"]:
            continue
        if state["tries"].get(ev["id"], 0) >= MAX_ATTEMPTS:
            continue
        if headlines is None:
            headlines = fetch_headlines()
        recent = [h for h in headlines
                  if h["time"] is None or h["time"] >= ev["time"] - timedelta(minutes=10)][:60]
        lines = "\n".join(f"- {h['title']}" for h in recent)
        prompt = (
            f"Event: {ev['title']} ({ev['country']}), released {fmt_ist(ev['time'])}. "
            f"Forecast: {ev['forecast']}. Previous: {ev['previous']}.\n"
            f"Headlines:\n{lines}\n\n"
            "Step 1: find a headline that reports the ACTUAL released figure for this exact event. "
            "Use only the headlines above, never guess or use memory. "
            "Step 2: if found, compare actual vs forecast (consider whether higher is good or bad "
            "for this indicator, e.g. unemployment) and judge the likely immediate impact on "
            "EURUSD, XAUUSD (gold) and BTCUSD, each one independently. "
            "A stronger USD is normally bearish for all three, a weaker USD is normally bullish "
            "for all three, and a stronger EUR is bullish for EURUSD. "
            'Return JSON: {"found": true or false, "actual": "...", '
            '"eurusd": "BULLISH"|"BEARISH"|"NEUTRAL", "xauusd": "BULLISH"|"BEARISH"|"NEUTRAL", '
            '"btcusd": "BULLISH"|"BEARISH"|"NEUTRAL", '
            '"reason": "one clear English sentence, max 25 words"}.'
        )
        res = ask_gemini(prompt)
        if isinstance(res, dict) and res.get("found"):
            send_telegram(
                f"📊 <b>DATA RELEASED</b>\n"
                f"{LINE}\n"
                f"📅 <b>{esc(ev['title'])}</b> ({esc(ev['country'])})\n"
                f"✅ Actual: <b>{esc(res.get('actual'))}</b>\n"
                f"📈 Forecast: {esc(ev['forecast'])}  |  Previous: {esc(ev['previous'])}\n\n"
                f"💡 <i>{esc(res.get('reason', ''))}</i>\n\n"
                f"{bias_block(res)}"
            )
            state["resolved"].append(ev["id"])
        else:
            state["tries"][ev["id"]] = state["tries"].get(ev["id"], 0) + 1


def live_news(state):
    headlines = fetch_headlines()
    if state["seen"] is None:      # pehli run: sirf yaad rakho, spam mat karo
        state["seen"] = [h["link"] for h in headlines][-500:]
        return
    seen = set(state["seen"])
    new = [h for h in headlines if h["link"] not in seen][:25]
    state["seen"] = (state["seen"] + [h["link"] for h in new])[-500:]
    if not new:
        return
    lines = "\n".join(f"{i}. {h['title']}" for i, h in enumerate(new))
    prompt = (
        f"Headlines:\n{lines}\n\n"
        "Pick ONLY headlines that can move EURUSD, XAUUSD (gold) or BTCUSD. "
        "Examples: Fed or ECB decisions and speeches, US or Eurozone inflation, jobs and GDP surprises, "
        "USD strength or weakness, real yields, safe-haven geopolitics, Bitcoin ETF flows, "
        "major crypto regulation. Skip everything else (other currencies, stocks, routine commentary). "
        "Judge each instrument independently and let the direction follow the news: "
        "positive or hawkish USD news is usually bearish for EURUSD, gold and BTC; "
        "negative or dovish USD news is usually bullish for them; "
        "crypto-specific news mainly affects BTCUSD; euro-positive news is bullish for EURUSD. "
        "Use NEUTRAL when an instrument is not clearly affected. "
        'Return a JSON list: [{"index": 0, "impact": "HIGH"|"MEDIUM"|"LOW", '
        '"eurusd": "BULLISH"|"BEARISH"|"NEUTRAL", "xauusd": "BULLISH"|"BEARISH"|"NEUTRAL", '
        '"btcusd": "BULLISH"|"BEARISH"|"NEUTRAL", '
        '"reason": "one clear English sentence, max 25 words"}]. Return [] if none.'
    )
    res = ask_gemini(prompt)
    if not isinstance(res, list):
        return
    for item in res:
        try:
            h = new[int(item["index"])]
        except Exception:
            continue
        biases = [norm(item.get(k)) for k in ("eurusd", "xauusd", "btcusd")]
        if all(b == "NEUTRAL" for b in biases):
            continue
        impact = str(item.get("impact", "MEDIUM")).upper()
        if impact not in IMPACT_ICON:
            impact = "MEDIUM"
        send_telegram(
            f"📰 <b>LIVE NEWS · {IMPACT_ICON[impact]} {impact} IMPACT</b>\n"
            f"{LINE}\n"
            f"{esc(h['title'])}\n\n"
            f"💡 <i>{esc(item.get('reason', ''))}</i>\n\n"
            f"{bias_block(item)}\n\n"
            f'🔗 <a href="{esc(h["link"])}">Read more</a>'
        )


# ---------- main ----------
def test_ping():
    res = ask_gemini('Reply with this JSON only: {"ok": true}')
    gemini_ok = isinstance(res, dict) and res.get("ok") is True
    if gemini_ok:
        send_telegram("hi telegram ok and gemini ok")
    else:
        send_telegram("hi telegram ok, but gemini FAILED (check the Actions log)")


def main():
    if not TOKEN or not CHAT_ID:
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID missing")
        return

    if TEST_MODE:
        test_ping()

    now = datetime.now(timezone.utc)
    state = load_state()
    events = get_events(state, now)
    pre_alerts(events, state, now)
    if GEMINI_KEY:
        resolve_events(events, state, now)
        live_news(state)
    else:
        print("GEMINI_API_KEY missing: sirf pre-news alerts chalenge")

    ids = {e["id"] for e in events}
    state["sent"] = [k for k in state["sent"] if k.rsplit("|", 1)[0] in ids]
    state["resolved"] = [k for k in state["resolved"] if k in ids]
    state["tries"] = {k: v for k, v in state["tries"].items() if k in ids}
    save_state(state)


if __name__ == "__main__":
    main()