import asyncio
import logging
import os
from dotenv import load_dotenv

# bet_manager DATABASE_URL'i import anında okur, bu yüzden önce .env yüklenmeli
load_dotenv()
from bet_manager import revalidate_resolved_bets

logging.basicConfig(level=logging.INFO)

async def trigger():
    if not os.getenv("DATABASE_URL"):
        print("❌ DATABASE_URL tanımlı değil (.env veya ortam değişkeni).")
        return
    print("🚀 Forcing a one-off revalidation cycle...")
    try:
        await revalidate_resolved_bets()
        print("✅ Revalidation complete.")
    except Exception as e:
        print(f"❌ Error during revalidation: {e}")

if __name__ == "__main__":
    asyncio.run(trigger())
