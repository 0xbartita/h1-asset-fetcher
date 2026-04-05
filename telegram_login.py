#!/usr/bin/env python3
"""One-time Telegram login — creates session file for revengi_downloader.py"""
import asyncio, os
from telethon import TelegramClient

SESSION = os.path.expanduser("~/.revengi_session")

async def main():
    client = TelegramClient(SESSION, 39038342, "405dc61e8d9083c842ad81642d4bfc98")
    await client.start(phone="+201014162356")
    me = await client.get_me()
    print(f"\nLogged in as: {me.first_name} (@{me.username})")
    print(f"Session saved to: {SESSION}")
    await client.disconnect()

asyncio.run(main())
