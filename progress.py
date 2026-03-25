"""
progress.py — Telegram upload/download progress callback builder.
Reuses the same message (edit_text) to avoid spam.
"""

import time

PROGRESS_BAR_LEN = 14

def make_progress_bar(pct: float) -> str:
    filled = int(PROGRESS_BAR_LEN * pct / 100)
    bar = "█" * filled + "░" * (PROGRESS_BAR_LEN - filled)
    return f"[{bar}] {pct:.1f}%"


def make_upload_callback(msg, label: str = "📤 Uploading"):
    """Returns a Pyrogram-compatible progress callback that edits `msg`."""
    last = [0.0]

    async def cb(current, total):
        if total == 0:
            return
        pct = current / total * 100
        # throttle: update only every 5%
        if pct - last[0] < 5 and pct < 99.9:
            return
        last[0] = pct
        size_mb = total / 1024 / 1024
        done_mb = current / 1024 / 1024
        try:
            await msg.edit_text(
                f"{label}\n"
                f"{make_progress_bar(pct)}\n"
                f"{done_mb:.1f} / {size_mb:.1f} MB"
            )
        except Exception:
            pass

    return cb


def make_download_callback(msg, label: str = "📥 Downloading"):
    """Same pattern for file download from Telegram."""
    return make_upload_callback(msg, label)
