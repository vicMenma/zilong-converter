"""
handlers.py — All Pyrogram message & callback handlers.

Conversation flows:

  /sub  ─────────────────────────────────────────────────────────────
  1. User sends video file OR URL       → state = SUB_WAIT_FILE
     User sends subtitle file first     → state = SUB_WAIT_VIDEO
  2. Both received → ask: Burn-in or Soft-mux?
     (also ask: scale resolution? optional)
  3. Process → send result

  /compress ──────────────────────────────────────────────────────────
  1. User sends video file OR URL       → state = COMP_WAIT_TARGET
  2. Bot asks: By size (MB) or resolution?
  3. User picks / inputs target         → process → send result

  /subcompress ───────────────────────────────────────────────────────
  Combined: subtitle + compression in one flow.
"""

import os
import uuid
import asyncio
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)

from config import Config
from state import (
    get_session, reset_session,
    IDLE, SUB_WAIT_VIDEO, SUB_WAIT_FILE, SUB_WAIT_CHOICE, COMP_WAIT_TARGET
)
from ffmpeg_ops import (
    probe_video, normalise_subtitle,
    burn_subtitles, mux_subtitles,
    compress_to_size, compress_to_res,
    burn_sub_and_compress, RESOLUTION_MAP
)
from downloader import download_video
from progress import make_upload_callback, make_download_callback

# ── Supported subtitle extensions ─────────────────────────────────────────────
SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".txt"}

# ── Inline keyboard helpers ────────────────────────────────────────────────────
def _kb(*rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])

KB_SUB_TYPE = _kb(
    [("🔥 Burn-in (hard)", "sub:burn"), ("📦 Embed (soft, MKV)", "sub:mux")],
    [("❌ Cancel", "cancel")]
)

KB_COMP_TYPE = _kb(
    [("📐 By resolution", "comp:res"), ("💾 By file size (MB)", "comp:size")],
    [("❌ Cancel", "cancel")]
)

KB_RESOLUTION = _kb(
    [("4K", "res:4K"), ("1080p", "res:1080p")],
    [("720p", "res:720p"), ("480p", "res:480p")],
    [("360p", "res:360p"), ("240p", "res:240p")],
    [("❌ Cancel", "cancel")]
)

KB_SUB_ALSO_COMPRESS = _kb(
    [("✅ Yes, also compress", "subcomp:yes"), ("⏩ No, just subtitles", "subcomp:no")],
    [("❌ Cancel", "cancel")]
)


# ── Utility: make a per-user work dir ─────────────────────────────────────────
def _work_dir(user_id: int) -> str:
    d = os.path.join(Config.WORK_DIR, str(user_id))
    os.makedirs(d, exist_ok=True)
    return d


# ── Utility: clean up work dir ────────────────────────────────────────────────
def _cleanup(user_id: int):
    import shutil
    d = _work_dir(user_id)
    shutil.rmtree(d, ignore_errors=True)


# ── Utility: download video from Telegram message ─────────────────────────────
async def _dl_video_from_msg(client: Client, msg: Message, status_msg: Message, user_id: int) -> str:
    work = _work_dir(user_id)
    cb = make_download_callback(status_msg)
    path = await client.download_media(msg, file_name=os.path.join(work, "input_video"), progress=cb)
    return path


# ── /start ─────────────────────────────────────────────────────────────────────
def register_handlers(app: Client):

    @app.on_message(filters.command("start"))
    async def cmd_start(client: Client, msg: Message):
        await msg.reply_text(
            "👋 **Zilong Converter Bot**\n\n"
            "**Commands:**\n"
            "• `/sub` — Add subtitles to a video\n"
            "• `/compress` — Compress video (by size or resolution)\n"
            "• `/subcompress` — Subtitles + compression in one shot\n"
            "• `/cancel` — Cancel current operation\n\n"
            "📎 Send a video file or paste a URL after any command."
        )


    @app.on_message(filters.command("cancel"))
    async def cmd_cancel(client: Client, msg: Message):
        reset_session(msg.from_user.id)
        _cleanup(msg.from_user.id)
        await msg.reply_text("✅ Cancelled. All temporary files cleared.")


    # ── /sub ──────────────────────────────────────────────────────────────────
    @app.on_message(filters.command("sub"))
    async def cmd_sub(client: Client, msg: Message):
        s = get_session(msg.from_user.id)
        s.reset()
        s.mode = SUB_WAIT_VIDEO
        await msg.reply_text(
            "🎬 **Add Subtitles**\n\n"
            "Send me:\n"
            "1️⃣ The **video** (file or URL)\n"
            "2️⃣ The **subtitle file** (.srt / .ass / .vtt / .txt)\n\n"
            "You can send them in either order."
        )


    # ── /compress ─────────────────────────────────────────────────────────────
    @app.on_message(filters.command("compress"))
    async def cmd_compress(client: Client, msg: Message):
        s = get_session(msg.from_user.id)
        s.reset()
        s.mode = COMP_WAIT_TARGET
        await msg.reply_text(
            "📦 **Video Compression**\n\n"
            "Send me the **video** (file or URL) and I'll ask for your target."
        )


    # ── /subcompress ──────────────────────────────────────────────────────────
    @app.on_message(filters.command("subcompress"))
    async def cmd_subcompress(client: Client, msg: Message):
        s = get_session(msg.from_user.id)
        s.reset()
        s.mode = SUB_WAIT_VIDEO
        s.extra["subcompress"] = True
        await msg.reply_text(
            "🎬📦 **Subtitles + Compression**\n\n"
            "Send me the **video** (file or URL) and your **subtitle file** in any order."
        )


    # ── Incoming: text message (URL or target size) ───────────────────────────
    @app.on_message(filters.text & ~filters.command(["start", "sub", "compress", "subcompress", "cancel"]))
    async def on_text(client: Client, msg: Message):
        user_id = msg.from_user.id
        s = get_session(user_id)
        text = msg.text.strip()

        # --- URL input ---
        if text.startswith("http://") or text.startswith("https://"):
            if s.mode not in (SUB_WAIT_VIDEO, COMP_WAIT_TARGET):
                await msg.reply_text("Use /sub or /compress first.")
                return
            status = await msg.reply_text("⬇️ Downloading from URL…")
            try:
                work = _work_dir(user_id)
                async def progress_cb(pct, speed, eta):
                    try:
                        await status.edit_text(f"⬇️ Downloading… {pct:.0f}% | {speed} | ETA {eta}")
                    except: pass
                path = await download_video(text, work, progress_cb)
                info = await probe_video(path)
                s.video_path = path
                s.duration   = info["duration"]
                size_mb      = info["size_bytes"] / 1024 / 1024
                await status.edit_text(
                    f"✅ Video downloaded\n"
                    f"📐 {info['width']}×{info['height']} | "
                    f"⏱ {int(info['duration'])}s | 💾 {size_mb:.1f} MB"
                )
                await _check_both_ready(client, msg, user_id)
            except Exception as e:
                await status.edit_text(f"❌ Download failed:\n`{e}`")
            return

        # --- Numeric input for target size ---
        if s.mode == COMP_WAIT_TARGET and s.extra.get("waiting_mb"):
            try:
                mb = float(text)
                assert 1 <= mb <= 4000
            except:
                await msg.reply_text("❌ Enter a valid size in MB (e.g. `50`).")
                return
            s.extra["target_mb"] = mb
            s.extra.pop("waiting_mb")
            await _run_compression(client, msg, user_id)
            return

        await msg.reply_text("Send a video/subtitle file, a URL, or use /sub, /compress.")


    # ── Incoming: document or video ───────────────────────────────────────────
    @app.on_message(filters.document | filters.video)
    async def on_file(client: Client, msg: Message):
        user_id = msg.from_user.id
        s = get_session(user_id)

        if s.mode == IDLE:
            await msg.reply_text("Use /sub, /compress, or /subcompress first.")
            return

        # determine file type
        media = msg.document or msg.video
        fname = getattr(media, "file_name", None) or "file"
        ext   = Path(fname).suffix.lower()

        # --- Subtitle file ---
        if ext in SUB_EXTS:
            if s.sub_path:
                await msg.reply_text("⚠️ Already have a subtitle. Send the video.")
                return
            status = await msg.reply_text("⬇️ Downloading subtitle…")
            work  = _work_dir(user_id)
            raw   = await client.download_media(msg, file_name=os.path.join(work, "sub_raw" + ext))
            norm  = normalise_subtitle(raw, work)
            s.sub_path = norm
            s.sub_ext  = Path(norm).suffix.lower()
            await status.edit_text(f"✅ Subtitle received: `{Path(norm).name}`")
            await _check_both_ready(client, msg, user_id)
            return

        # --- Video file ---
        if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".flv", "") or msg.video:
            if s.video_path:
                await msg.reply_text("⚠️ Already have a video. Send the subtitle or use /cancel.")
                return
            status = await msg.reply_text("⬇️ Downloading video…")
            try:
                path = await _dl_video_from_msg(client, msg, status, user_id)
                info = await probe_video(path)
                s.video_path = path
                s.duration   = info["duration"]
                size_mb      = info["size_bytes"] / 1024 / 1024
                await status.edit_text(
                    f"✅ Video received\n"
                    f"📐 {info['width']}×{info['height']} | "
                    f"⏱ {int(info['duration'])}s | 💾 {size_mb:.1f} MB"
                )
                await _check_both_ready(client, msg, user_id)
            except Exception as e:
                await status.edit_text(f"❌ Error: `{e}`")
            return

        await msg.reply_text(f"❓ Unsupported file type: `{ext}`")


    # ── Check if both video + sub are ready → ask action ─────────────────────
    async def _check_both_ready(client: Client, msg: Message, user_id: int):
        s = get_session(user_id)

        if s.mode in (SUB_WAIT_VIDEO, SUB_WAIT_FILE, SUB_WAIT_CHOICE):
            if s.video_path and s.sub_path:
                s.mode = SUB_WAIT_CHOICE
                if s.extra.get("subcompress"):
                    await msg.reply_text(
                        "✅ Both received! How do you want the subtitles?",
                        reply_markup=KB_SUB_TYPE
                    )
                else:
                    await msg.reply_text(
                        "✅ Both received!\n\n"
                        "**🔥 Burn-in** — subtitles baked into the picture (compatible everywhere)\n"
                        "**📦 Embed** — selectable subtitle track in MKV (no re-encode)\n\n"
                        "Choose:",
                        reply_markup=KB_SUB_TYPE
                    )
            elif s.video_path and not s.sub_path:
                s.mode = SUB_WAIT_FILE
                await msg.reply_text("👍 Video ready. Now send the subtitle file (.srt / .ass / .vtt / .txt).")
            elif s.sub_path and not s.video_path:
                s.mode = SUB_WAIT_VIDEO
                await msg.reply_text("👍 Subtitle ready. Now send the video (file or URL).")

        elif s.mode == COMP_WAIT_TARGET:
            if s.video_path:
                await msg.reply_text(
                    "✅ Video ready. How do you want to compress it?",
                    reply_markup=KB_COMP_TYPE
                )


    # ── Callbacks ─────────────────────────────────────────────────────────────
    @app.on_callback_query()
    async def on_callback(client: Client, cb: CallbackQuery):
        user_id = cb.from_user.id
        s = get_session(user_id)
        data = cb.data
        await cb.answer()

        if data == "cancel":
            reset_session(user_id)
            _cleanup(user_id)
            await cb.message.edit_text("❌ Cancelled.")
            return

        # ── Subtitle type chosen ──────────────────────────────────────────────
        if data in ("sub:burn", "sub:mux"):
            s.extra["sub_type"] = data
            if s.extra.get("subcompress"):
                # Ask compression type too
                await cb.message.edit_text(
                    "Do you also want to compress the output?",
                    reply_markup=KB_SUB_ALSO_COMPRESS
                )
            else:
                if data == "sub:burn":
                    # Optionally offer resolution choice
                    await cb.message.edit_text(
                        "Want to also scale the resolution while burning?",
                        reply_markup=_kb(
                            [("🔡 Keep original", "burn:noscale"), ("📐 Pick resolution", "burn:scale")],
                            [("❌ Cancel", "cancel")]
                        )
                    )
                else:
                    await _run_subtitle(client, cb.message, user_id)
            return

        if data == "burn:noscale":
            s.extra["scale_res"] = None
            await _run_subtitle(client, cb.message, user_id)
            return

        if data == "burn:scale":
            await cb.message.edit_text("Pick target resolution:", reply_markup=KB_RESOLUTION)
            s.extra["burn_then_pick_res"] = True
            return

        # ── Resolution chosen (from burn flow) ───────────────────────────────
        if data.startswith("res:") and s.extra.get("burn_then_pick_res"):
            s.extra["scale_res"] = data[4:]
            s.extra.pop("burn_then_pick_res")
            await _run_subtitle(client, cb.message, user_id)
            return

        # ── Compression type chosen ───────────────────────────────────────────
        if data == "comp:res":
            s.comp_mode = "resolution"
            await cb.message.edit_text("Pick target resolution:", reply_markup=KB_RESOLUTION)
            return

        if data == "comp:size":
            s.comp_mode = "size"
            s.extra["waiting_mb"] = True
            await cb.message.edit_text(
                "💾 Enter target file size in **MB**:\n"
                "_(e.g. `50` for 50 MB)_"
            )
            return

        # ── Resolution chosen (compression flow) ─────────────────────────────
        if data.startswith("res:") and s.comp_mode == "resolution":
            s.extra["target_res"] = data[4:]
            await _run_compression(client, cb.message, user_id)
            return

        # ── Subcompress: also compress? ───────────────────────────────────────
        if data == "subcomp:yes":
            await cb.message.edit_text("Pick compression type:", reply_markup=KB_COMP_TYPE)
            s.extra["subcomp_compress"] = True
            return

        if data == "subcomp:no":
            s.extra.pop("subcompress", None)
            await _run_subtitle(client, cb.message, user_id)
            return

        # ── Compression type in subcompress flow ──────────────────────────────
        if data == "comp:res" and s.extra.get("subcomp_compress"):
            s.comp_mode = "resolution"
            await cb.message.edit_text("Pick target resolution:", reply_markup=KB_RESOLUTION)
            return

        if data == "comp:size" and s.extra.get("subcomp_compress"):
            s.comp_mode = "size"
            s.extra["waiting_mb"] = True
            await cb.message.edit_text("💾 Enter target file size in **MB**:")
            return

        # ── Resolution chosen in subcompress flow ─────────────────────────────
        if data.startswith("res:") and s.extra.get("subcomp_compress"):
            s.extra["target_res"] = data[4:]
            await _run_subcompress(client, cb.message, user_id)
            return


    # ── Run: subtitle only ────────────────────────────────────────────────────
    async def _run_subtitle(client: Client, msg: Message, user_id: int):
        s = get_session(user_id)
        work = _work_dir(user_id)
        sub_type  = s.extra.get("sub_type", "sub:burn")
        scale_res = s.extra.get("scale_res")
        out_ext   = ".mkv" if sub_type == "sub:mux" else ".mp4"
        out_path  = os.path.join(work, f"output{out_ext}")

        status = await msg.reply_text("⚙️ Processing subtitles…")
        try:
            if sub_type == "sub:mux":
                result = await mux_subtitles(s.video_path, s.sub_path, out_path)
            else:
                if scale_res:
                    result = await burn_sub_and_compress(
                        s.video_path, s.sub_path, out_path,
                        res_label=scale_res
                    )
                else:
                    result = await burn_subtitles(s.video_path, s.sub_path, out_path)

            await _send_result(client, msg, user_id, result, status)
        except Exception as e:
            await status.edit_text(f"❌ Failed:\n`{e}`")
            reset_session(user_id)


    # ── Run: compression only ─────────────────────────────────────────────────
    async def _run_compression(client: Client, msg: Message, user_id: int):
        s = get_session(user_id)
        work = _work_dir(user_id)
        out_path = os.path.join(work, "output_compressed.mp4")

        status = await msg.reply_text("⚙️ Compressing…")
        try:
            if s.comp_mode == "resolution":
                res = s.extra.get("target_res", "720p")
                result = await compress_to_res(s.video_path, res, out_path)
            else:
                mb = s.extra.get("target_mb", 50)
                result = await compress_to_size(s.video_path, mb, out_path)

            await _send_result(client, msg, user_id, result, status)
        except Exception as e:
            await status.edit_text(f"❌ Compression failed:\n`{e}`")
            reset_session(user_id)


    # ── Run: subtitle + compress ──────────────────────────────────────────────
    async def _run_subcompress(client: Client, msg: Message, user_id: int):
        s = get_session(user_id)
        work = _work_dir(user_id)
        out_path = os.path.join(work, "output_subcomp.mp4")

        res_label = s.extra.get("target_res")
        target_mb = s.extra.get("target_mb")

        status = await msg.reply_text("⚙️ Burning subtitles + compressing…")
        try:
            result = await burn_sub_and_compress(
                s.video_path, s.sub_path, out_path,
                res_label=res_label,
                target_mb=target_mb
            )
            await _send_result(client, msg, user_id, result, status)
        except Exception as e:
            await status.edit_text(f"❌ Failed:\n`{e}`")
            reset_session(user_id)


    # ── Send result ───────────────────────────────────────────────────────────
    async def _send_result(client: Client, msg: Message, user_id: int, path: str, status_msg: Message):
        size_mb = os.path.getsize(path) / 1024 / 1024
        await status_msg.edit_text(f"📤 Uploading result… ({size_mb:.1f} MB)")
        cb = make_upload_callback(status_msg)
        try:
            await client.send_video(
                msg.chat.id,
                video=path,
                caption=f"✅ Done! `{Path(path).name}` — {size_mb:.1f} MB",
                progress=cb,
                supports_streaming=True,
            )
            await status_msg.delete()
        except Exception:
            # fallback: send as document (large files, MKV)
            await client.send_document(
                msg.chat.id,
                document=path,
                caption=f"✅ Done! — {size_mb:.1f} MB",
                progress=cb,
            )
            await status_msg.delete()
        finally:
            reset_session(user_id)
            _cleanup(user_id)
