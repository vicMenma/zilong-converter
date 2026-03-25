"""
ui.py — All inline keyboards and message templates for Zilong Converter Bot.
Button-driven UI: no slash commands needed after /start.
"""

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton


# ── Helper ────────────────────────────────────────────────────────────────────
def _kb(*rows):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=data) for label, data in row]
        for row in rows
    ])


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN MENU
# ══════════════════════════════════════════════════════════════════════════════

MSG_START = (
    "👋 <b>Zilong Converter Bot</b>\n"
    "──────────────────\n\n"
    "🎬  Add subtitles to any video\n"
    "📦  Compress by resolution or size\n"
    "🔥  Subtitles + compression in one shot\n\n"
    "<i>Choose an operation below, then send\n"
    "your video file or paste a URL.</i>"
)

KB_MAIN = _kb(
    [("🎬  Add Subtitles",          "menu:sub"),
     ("📦  Compress Video",         "menu:compress")],
    [("🔥  Subtitles + Compress",   "menu:subcompress")],
    [("❓  Help",                   "menu:help"),
     ("❌  Cancel",                 "cancel")],
)


# ══════════════════════════════════════════════════════════════════════════════
#  HELP
# ══════════════════════════════════════════════════════════════════════════════

MSG_HELP = (
    "📖 <b>HOW TO USE</b>\n"
    "──────────────────\n\n"
    "1️⃣  Press a button from the main menu\n"
    "2️⃣  Send a <b>video file</b> or paste a <b>URL</b>\n"
    "3️⃣  For subtitle operations, also send\n"
    "    your subtitle file\n"
    "4️⃣  Follow the on-screen prompts\n\n"
    "──────────────────\n"
    "📎 <b>Supported subtitle formats</b>\n"
    "  <code>.srt  .ass  .ssa  .vtt  .txt</code>\n\n"
    "🎬 <b>Subtitle modes</b>\n"
    "  🔥 <b>Burn-in</b>  — baked into picture\n"
    "  📦 <b>Embed</b>   — soft track in MKV\n\n"
    "📐 <b>Compression modes</b>\n"
    "  By <b>resolution</b> — scale + CRF encode\n"
    "  By <b>file size</b>  — 2-pass CBR to hit MB target\n\n"
    "⚡ <b>URL sources</b>\n"
    "  YouTube, seedr, direct HTTP links,\n"
    "  and any site supported by yt-dlp"
)

KB_HELP_BACK = _kb([("⏎  Back to Menu", "menu:main")])


# ══════════════════════════════════════════════════════════════════════════════
#  SUB FLOW
# ══════════════════════════════════════════════════════════════════════════════

MSG_SUB_INTRO = (
    "🎬 <b>ADD SUBTITLES</b>\n"
    "──────────────────\n\n"
    "Send me:\n"
    "  📹  The <b>video</b> (file or URL)\n"
    "  📄  The <b>subtitle</b> file\n\n"
    "<i>You can send them in either order.</i>"
)

MSG_SUB_WAIT_FILE = (
    "✅ <b>Video received</b>\n"
    "──────────────────\n\n"
    "Now send the <b>subtitle file</b>\n"
    "<code>.srt  .ass  .ssa  .vtt  .txt</code>"
)

MSG_SUB_WAIT_VIDEO = (
    "✅ <b>Subtitle received</b>\n"
    "──────────────────\n\n"
    "Now send the <b>video</b> (file or URL)"
)

KB_SUB_TYPE = _kb(
    [("🔥  Burn-in (hard subs)",    "sub:burn"),
     ("📦  Embed (soft, MKV)",      "sub:mux")],
    [("❌  Cancel",                 "cancel")],
)

KB_BURN_SCALE = _kb(
    [("📐  Also scale resolution",  "burn:scale"),
     ("⏩  Keep original size",     "burn:noscale")],
    [("❌  Cancel",                 "cancel")],
)


# ══════════════════════════════════════════════════════════════════════════════
#  COMPRESS FLOW
# ══════════════════════════════════════════════════════════════════════════════

MSG_COMP_INTRO = (
    "📦 <b>VIDEO COMPRESSION</b>\n"
    "──────────────────\n\n"
    "Send me the <b>video</b> (file or URL)\n"
    "and I'll ask for your target."
)

KB_COMP_TYPE = _kb(
    [("📐  By resolution",          "comp:res"),
     ("💾  By file size (MB)",      "comp:size")],
    [("❌  Cancel",                 "cancel")],
)


# ══════════════════════════════════════════════════════════════════════════════
#  RESOLUTION PICKER
# ══════════════════════════════════════════════════════════════════════════════

KB_RESOLUTION = _kb(
    [("4K  (2160p)",  "res:4K"),     ("1080p",         "res:1080p")],
    [("720p",         "res:720p"),   ("480p",          "res:480p")],
    [("360p",         "res:360p"),   ("240p",          "res:240p")],
    [("❌  Cancel",   "cancel")],
)


# ══════════════════════════════════════════════════════════════════════════════
#  SUBCOMPRESS FLOW
# ══════════════════════════════════════════════════════════════════════════════

MSG_SUBCOMP_INTRO = (
    "🔥 <b>SUBTITLES + COMPRESSION</b>\n"
    "──────────────────\n\n"
    "Send me the <b>video</b> (file or URL)\n"
    "and the <b>subtitle file</b> in any order."
)

KB_SUBCOMP_ALSO_COMPRESS = _kb(
    [("✅  Yes, also compress",     "subcomp:yes"),
     ("⏩  No, just subtitles",     "subcomp:no")],
    [("❌  Cancel",                 "cancel")],
)


# ══════════════════════════════════════════════════════════════════════════════
#  STATUS / MISC
# ══════════════════════════════════════════════════════════════════════════════

KB_CANCEL = _kb([("❌  Cancel", "cancel")])
KB_MAIN_BACK = _kb([("🏠  Main Menu", "menu:main")])
KB_DONE = _kb([("🏠  Main Menu", "menu:main"), ("❌  Close", "close")])
