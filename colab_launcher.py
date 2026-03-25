# ╔══════════════════════════════════════════════════════════════════╗
# ║          ZILONG CONVERTER BOT — Google Colab Launcher           ║
# ╚══════════════════════════════════════════════════════════════════╝
# Run each cell in order.

# ── Cell 1: Install system deps ───────────────────────────────────
# !apt-get install -y ffmpeg > /dev/null 2>&1
# !echo "✅ ffmpeg installed"

# ── Cell 2: Install Python deps ──────────────────────────────────
# !pip install -q pyrofork TgCrypto yt-dlp

# ── Cell 3: Clone or upload the bot ──────────────────────────────
# !git clone https://github.com/YOUR_USERNAME/zilong_converter /content/zilong_converter
# %cd /content/zilong_converter

# ── Cell 4: Set environment variables ────────────────────────────
import os
os.environ["API_ID"]    = "12345678"         # ← your API ID
os.environ["API_HASH"]  = "your_api_hash"    # ← your API hash
os.environ["BOT_TOKEN"] = "your_bot_token"   # ← your bot token
os.environ["WORK_DIR"]  = "/tmp/zilong_work"

# ── Cell 5: Run the bot ───────────────────────────────────────────
# !python bot.py

# ── Keep-alive (optional, prevents Colab timeout) ─────────────────
# import time
# while True:
#     time.sleep(60)
