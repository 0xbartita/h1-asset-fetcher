#!/usr/bin/env python3
"""
One-time Telegram login — creates session file for telegram_bot.py

Usage:
  source .venv/bin/activate
  export TELEGRAM_API_ID=your_api_id
  export TELEGRAM_API_HASH=your_api_hash
  export TELEGRAM_PHONE=+1234567890   # optional; prompted interactively if unset
  python3 -m h1_asset_fetcher.download.login

Credentials are read from the TELEGRAM_API_ID / TELEGRAM_API_HASH /
TELEGRAM_PHONE environment variables, or overridden with the matching
CLI flags. Get an api_id/api_hash at https://my.telegram.org.
"""
import sys, os, asyncio, argparse
from telethon import TelegramClient

SESSION = os.path.expanduser("~/.revengi_session")


async def main():
    parser = argparse.ArgumentParser(description="One-time Telegram login")
    parser.add_argument("--api-id", default=os.environ.get("TELEGRAM_API_ID", ""))
    parser.add_argument("--api-hash", default=os.environ.get("TELEGRAM_API_HASH", ""))
    parser.add_argument("--phone", default=os.environ.get("TELEGRAM_PHONE", ""))
    args = parser.parse_args()

    if not args.api_id or not args.api_hash:
        print("[ERR] Set TELEGRAM_API_ID and TELEGRAM_API_HASH "
              "(env vars or --api-id/--api-hash). Get them at https://my.telegram.org",
              file=sys.stderr)
        sys.exit(1)

    client = TelegramClient(SESSION, int(args.api_id), args.api_hash)
    # phone is optional: telethon prompts interactively when it is empty
    await client.start(phone=(args.phone or None))
    me = await client.get_me()
    print(f"\nLogged in as: {me.first_name} (@{me.username})")
    print(f"Session saved to: {SESSION}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
