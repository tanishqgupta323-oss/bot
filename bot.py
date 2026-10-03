import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("8991583026:AAEyOzocyOI8UAggTxz9yZuJM2v-d9v5n4w")
CHAT_ID = os.getenv("5885718440")


def send_telegram(text):
    url = f"https://api.telegram.org/bot8991583026:AAEyOzocyOI8UAggTxz9yZuJM2v-d9v5n4w/sendMessage"
    r = requests.post(url, data={"chat_id": 5885718440, "text": text}, timeout=15)
    print(r.status_code, r.text)


if __name__ == "__main__":
    send_telegram("Python se pehla message, bro!")