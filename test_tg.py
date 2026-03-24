import os
import urllib.request
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

def test_telegram():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    print(f"Token: {token[:10]}...")
    print(f"Chat ID: {chat_id}")
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": "🚀 <b>BetBot AI:</b> Sistem Bağlantı Testi Başarılı! (v1.0.2)",
        "parse_mode": "HTML"
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(url, data=payload)
        with urllib.request.urlopen(req, timeout=10) as response:
            print(f"Response: {response.read().decode('utf-8')}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_telegram()
