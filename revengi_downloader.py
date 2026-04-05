#!/usr/bin/env python3
"""
RevEngiBot APK Downloader - by 0xbartita
Downloads APKs via @RevEngiBot Telegram bot using /apkdl command.

Usage:
  source .venv/bin/activate
  export TELEGRAM_API_ID=your_api_id
  export TELEGRAM_API_HASH=your_api_hash
  python3 revengi_downloader.py -i apks/still_failed_packages.txt -o apks/
"""

import sys, os, json, asyncio, argparse, time, signal
from pathlib import Path
from telethon import TelegramClient, events

signal.signal(signal.SIGINT, lambda *_: (print("\n\033[91m[!] Interrupted\033[0m"), os._exit(1)))

BOT_USERNAME = "RevEngiBot"
SESSION_FILE = os.path.expanduser("~/.revengi_session")


def log(msg, level="INFO"):
    colors = {"INFO": "\033[94m", "OK": "\033[92m", "WARN": "\033[93m", "ERR": "\033[91m", "STEP": "\033[96m"}
    print(f"{colors.get(level, '')}[{level}]\033[0m {msg}", flush=True)


async def download_apk(client, bot, pkg_name, outdir):
    """Send /apkdl to RevEngiBot, handle response, download APK."""
    try:
        # Send command
        await client.send_message(bot, f"/apkdl id={pkg_name}")

        # Bot sends "Searching your apk..." first, then edits/sends the real result
        # Wait for first message (searching...)
        try:
            msg1 = await asyncio.wait_for(
                wait_for_bot_reply(client, bot), timeout=15
            )
        except asyncio.TimeoutError:
            return {"package": pkg_name, "success": False, "reason": "bot timeout"}

        # If first msg is "searching", wait for edit or new message with the real result
        response = msg1
        if msg1 and msg1.text and "searching" in msg1.text.lower():
            try:
                response = await asyncio.wait_for(
                    wait_for_bot_reply_or_edit(client, bot), timeout=30
                )
            except asyncio.TimeoutError:
                # Check if the original message was edited while we waited
                try:
                    updated = await client.get_messages(bot, ids=msg1.id)
                    if updated and updated.text and "searching" not in updated.text.lower():
                        response = updated
                    else:
                        return {"package": pkg_name, "success": False, "reason": "bot search timeout"}
                except:
                    return {"package": pkg_name, "success": False, "reason": "bot search timeout"}

        if not response:
            return {"package": pkg_name, "success": False, "reason": "no response"}

        # Check "No APK found!"
        if response.text and "no apk found" in response.text.lower():
            return {"package": pkg_name, "success": False, "reason": "No APK found"}

        if response.text and "not found" in response.text.lower():
            return {"package": pkg_name, "success": False, "reason": "not found"}

        # Check for Download button and click it
        if response.buttons:
            clicked = False
            for row in response.buttons:
                for button in row:
                    if button.text and "download" in button.text.lower():
                        await button.click()
                        clicked = True
                        break
                if clicked:
                    break

            if not clicked:
                # Maybe "Cancel" only = no APK
                return {"package": pkg_name, "success": False, "reason": "no Download button"}

            # Wait for file
            try:
                file_msg = await asyncio.wait_for(
                    wait_for_file(client, bot), timeout=90
                )
            except asyncio.TimeoutError:
                return {"package": pkg_name, "success": False, "reason": "file download timeout"}

            if file_msg and (file_msg.document or file_msg.file):
                filename = ""
                if file_msg.document:
                    for attr in file_msg.document.attributes:
                        if hasattr(attr, "file_name"):
                            filename = attr.file_name
                            break
                if not filename:
                    filename = f"{pkg_name}.apk"

                # Save under the actual package name from filename if possible
                actual_pkg = filename.split("_")[0] if "_" in filename else pkg_name
                save_pkg = actual_pkg if actual_pkg.count(".") >= 2 else pkg_name

                pkg_dir = Path(outdir) / save_pkg
                pkg_dir.mkdir(parents=True, exist_ok=True)
                filepath = pkg_dir / filename
                await client.download_media(file_msg, file=str(filepath))

                size_mb = filepath.stat().st_size / 1024 / 1024
                if size_mb < 0.01:
                    filepath.unlink()
                    if not any(pkg_dir.iterdir()):
                        pkg_dir.rmdir()
                    return {"package": pkg_name, "success": False, "reason": "invalid file"}

                if save_pkg != pkg_name:
                    return {"package": pkg_name, "success": False, "reason": f"got {save_pkg} instead (saved)"}
                return {"package": pkg_name, "success": True, "file": filename, "size": f"{size_mb:.1f}MB"}
            else:
                # Check if bot sent a text error instead of file
                if file_msg and file_msg.text:
                    return {"package": pkg_name, "success": False, "reason": file_msg.text[:100]}
                return {"package": pkg_name, "success": False, "reason": "no file received"}
        else:
            # No buttons - might be an error or direct file
            if response.document or response.file:
                pkg_dir = Path(outdir) / pkg_name
                pkg_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{pkg_name}.apk"
                filepath = pkg_dir / filename
                await client.download_media(response, file=str(filepath))
                size_mb = filepath.stat().st_size / 1024 / 1024
                return {"package": pkg_name, "success": True, "file": filename, "size": f"{size_mb:.1f}MB"}

            return {"package": pkg_name, "success": False, "reason": response.text[:100] if response.text else "unknown"}

    except Exception as e:
        return {"package": pkg_name, "success": False, "reason": str(e)[:150]}


async def wait_for_bot_reply(client, bot):
    """Wait for next NEW message from the bot."""
    future = asyncio.get_event_loop().create_future()

    @client.on(events.NewMessage(from_users=bot))
    async def handler(event):
        if not future.done():
            future.set_result(event.message)
        client.remove_event_handler(handler)

    try:
        return await future
    except:
        client.remove_event_handler(handler)
        raise


async def wait_for_bot_reply_or_edit(client, bot):
    """Wait for next new message OR edit from the bot."""
    future = asyncio.get_event_loop().create_future()

    @client.on(events.NewMessage(from_users=bot))
    async def handler(event):
        if not future.done():
            future.set_result(event.message)
        client.remove_event_handler(handler)
        try:
            client.remove_event_handler(edit_handler)
        except:
            pass

    @client.on(events.MessageEdited(from_users=bot))
    async def edit_handler(event):
        # Only trigger if the edit has real content (not just "searching")
        if event.message.text and "searching" not in event.message.text.lower():
            if not future.done():
                future.set_result(event.message)
            client.remove_event_handler(edit_handler)
            try:
                client.remove_event_handler(handler)
            except:
                pass

    try:
        return await future
    except:
        try:
            client.remove_event_handler(handler)
        except:
            pass
        try:
            client.remove_event_handler(edit_handler)
        except:
            pass
        raise


async def wait_for_file(client, bot):
    """Wait for a file or next message from bot after clicking Download."""
    future = asyncio.get_event_loop().create_future()

    @client.on(events.NewMessage(from_users=bot))
    async def handler(event):
        if not future.done():
            future.set_result(event.message)
        client.remove_event_handler(handler)
        try:
            client.remove_event_handler(edit_handler)
        except:
            pass

    @client.on(events.MessageEdited(from_users=bot))
    async def edit_handler(event):
        if event.message.document or event.message.file:
            if not future.done():
                future.set_result(event.message)
            client.remove_event_handler(edit_handler)
            try:
                client.remove_event_handler(handler)
            except:
                pass

    try:
        return await future
    except:
        try:
            client.remove_event_handler(handler)
        except:
            pass
        try:
            client.remove_event_handler(edit_handler)
        except:
            pass
        raise


async def main():
    parser = argparse.ArgumentParser(description="RevEngiBot APK Downloader - by 0xbartita")
    parser.add_argument("-i", "--input", default="apks/still_failed_packages.txt")
    parser.add_argument("-o", "--outdir", default="apks")
    parser.add_argument("--api-id", default=os.environ.get("TELEGRAM_API_ID", ""))
    parser.add_argument("--api-hash", default=os.environ.get("TELEGRAM_API_HASH", ""))
    parser.add_argument("--sleep", type=int, default=2, help="Sleep between requests (default: 2s)")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    print("")
    print("  \033[96m╔════════════════════════════════════════════════════════════╗\033[0m")
    print("  \033[96m║\033[93m     RevEngiBot APK Downloader  |  by 0xbartita            \033[96m║\033[0m")
    print("  \033[96m║\033[0m  Downloads via @RevEngiBot /apkdl | FAST MODE              \033[96m║\033[0m")
    print("  \033[96m╚════════════════════════════════════════════════════════════╝\033[0m")
    print("")

    api_id = args.api_id
    api_hash = args.api_hash
    if not api_id or not api_hash:
        log("Set TELEGRAM_API_ID and TELEGRAM_API_HASH!", "ERR")
        sys.exit(1)

    # Load packages
    packages = []
    for line in open(args.input):
        pkg = line.strip()
        if pkg and not pkg.startswith("#"):
            packages.append(pkg)

    # Skip already downloaded
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    to_download = []
    already = 0
    for pkg in packages:
        pkg_dir = outdir / pkg
        existing = (list(pkg_dir.glob("*.apk")) + list(pkg_dir.glob("*.xapk")) + list(pkg_dir.glob("*.apkm"))) if pkg_dir.exists() else []
        if existing:
            already += 1
        else:
            to_download.append(pkg)

    if args.start > 0:
        to_download = to_download[args.start:]
    if args.limit > 0:
        to_download = to_download[:args.limit]

    log(f"Total: {len(packages)} | Skip: {already} | Download: {len(to_download)}", "STEP")

    # Connect (reuse existing session if available)
    client = TelegramClient(SESSION_FILE, int(api_id), api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        log("Session expired. Run: python3 telegram_login.py", "ERR")
        sys.exit(1)
    me = await client.get_me()
    log(f"Logged in as: {me.first_name} (@{me.username})", "OK")

    bot = await client.get_entity(BOT_USERNAME)
    log(f"Bot: @{BOT_USERNAME} ready", "OK")

    succeeded = []
    failed = []
    start_time = time.time()

    for i, pkg in enumerate(to_download, 1):
        result = await download_apk(client, bot, pkg, args.outdir)

        if result["success"]:
            succeeded.append(result)
            log(f"[{i}/{len(to_download)}] \033[92m✓\033[0m {pkg} -> {result.get('file','')} ({result.get('size','')})", "OK")
        else:
            failed.append(result)
            log(f"[{i}/{len(to_download)}] \033[91m✗\033[0m {pkg} -> {result.get('reason','?')}", "ERR")

        if i < len(to_download):
            await asyncio.sleep(args.sleep)

    elapsed = time.time() - start_time

    print(f"\n{'='*70}")
    log("SUMMARY", "STEP")
    print(f"{'='*70}")
    log(f"Downloaded: {len(succeeded)} | Failed: {len(failed)} | Time: {elapsed:.0f}s", "INFO")

    if failed:
        fp = outdir / "revengi_failed.txt"
        with open(fp, "w") as f:
            for r in failed:
                f.write(f"{r['package']}\n")
        fj = outdir / "revengi_failed.json"
        with open(fj, "w") as f:
            json.dump(failed, f, indent=2)
        log(f"Failed: {fp}", "WARN")

    if succeeded:
        with open(outdir / "revengi_success.json", "w") as f:
            json.dump(succeeded, f, indent=2)

    await client.disconnect()
    log("Done!", "OK")


if __name__ == "__main__":
    asyncio.run(main())
