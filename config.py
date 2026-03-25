import os

class Config:
    # --- Telegram ---
    API_ID       = int(os.environ.get("API_ID", 0))
    API_HASH     = os.environ.get("API_HASH", "")
    BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")

    # --- Optional: BIN_CHANNEL for large file storage (like Zilong_FileToLink) ---
    BIN_CHANNEL  = int(os.environ.get("BIN_CHANNEL", 0))   # set to 0 to disable

    # --- Paths ---
    WORK_DIR     = os.environ.get("WORK_DIR", "/tmp/zilong_work")

    # --- Limits ---
    MAX_FILE_MB  = int(os.environ.get("MAX_FILE_MB", 2000))   # 2 GB (MTProto limit)
    MAX_DURATION = int(os.environ.get("MAX_DURATION", 7200))  # 2 hours

    # --- yt-dlp cookie file (optional, for age-restricted content) ---
    COOKIES_FILE = os.environ.get("COOKIES_FILE", "")

    os.makedirs(WORK_DIR, exist_ok=True)
