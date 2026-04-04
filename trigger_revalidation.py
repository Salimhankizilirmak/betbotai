import asyncio
import logging
import psycopg2
import os
from bet_manager import revalidate_resolved_bets

logging.basicConfig(level=logging.INFO)

# Hardcode DATABASE_URL from .env for this one-time fix
DB_URL = "postgresql://postgres:190719As1907.@db.cehnzawxqgvyvwrbgdde.supabase.co:5432/postgres"

async def trigger():
    print("🚀 Forcing a one-off revalidation cycle with hardcoded DB_URL...")
    os.environ['DATABASE_URL'] = DB_URL
    try:
        await revalidate_resolved_bets()
        print("✅ Revalidation complete.")
    except Exception as e:
        print(f"❌ Error during revalidation: {e}")

if __name__ == "__main__":
    asyncio.run(trigger())
