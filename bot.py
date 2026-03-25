"""
bot.py — Entry point for Zilong Converter Bot.
Run: python bot.py
"""

from pyrogram import Client
from config import Config
from handlers import register_handlers

app = Client(
    "zilong_converter",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
)

register_handlers(app)

if __name__ == "__main__":
    print("🤖 Zilong Converter Bot starting…")
    app.run()
