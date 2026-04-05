#!/usr/bin/env python3
"""One-time Telegram login — creates session file for revengi_downloader.py"""
import asyncio, os
from telethon import TelegramClient

SESSION = os.path.expanduser("~/.revengi_session")

async def main():
    client = TelegramClient(SESSION, REDACTED-TELEGRAM-API-ID, "REDACTED-TELEGRAM-API-HASH")
    await client.start(phone="+REDACTED-TELEGRAM-PHONE")
    me = await client.get_me()
    print(f"\nLogged in as: {me.first_name} (@{me.username})")
    print(f"Session saved to: {SESSION}")
    await client.disconnect()

asyncio.run(main())
