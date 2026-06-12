"""APK downloaders. Each module is runnable standalone:

    python3 -m h1_asset_fetcher.download.apkeep   -i packages.txt -o apks/
    python3 -m h1_asset_fetcher.download.browser  -i failed.txt   -o apks/   # [browser] extra
    python3 -m h1_asset_fetcher.download.web       -i failed.txt   -o apks/
    python3 -m h1_asset_fetcher.download.telegram_bot -i failed.txt -o apks/  # [telegram] extra
    python3 -m h1_asset_fetcher.download.login                                # one-time Telegram login
"""
