"""
handlers.py — Button-driven handlers for Zilong Converter Bot.

All entry points are via inline keyboards.
Progress panel matches Zilong_multiusage style (status_bar + sysINFO).
"""

import os
import shutil
from pathlib import Path
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery

from config import Config
from state import (
    get_session, reset_session,
    IDLE, SUB_WAIT_VIDEO, SUB_WAIT_FILE, SUB_WAIT_CHOICE, COMP_WAIT_TARGET,
)
from ffmpeg_ops import (
    probe_video, normalise_subtitle,
    burn_subtitles, mux_subtitles,
    compress_to_size, compress_to_res,
    burn_sub_and_compress, RESOLUTION_MAP,
)
from downloader import download_video
from progress import (
    status_bar, make_transfer_cb, make_ytdlp_cb, sysINFO, _size, _fmt_time,
)
from ui import (
    MSG_START, KB_MAIN,
    MSG_HELP, KB_HELP_BACK,
    MSG_SUB_INTRO, MSG_SUB_WAIT_FILE, MSG_SUB_WAIT_VIDEO,
    KB_SUB_TYPE, KB_BURN_SCALE,
    MSG_COMP_INTRO, KB_COMP_TYPE,
    KB_RESOLUTION,
    MSG_SUBCOMP_INTRO, KB_SUBCOMP_ALSO_COMPRESS,
    KB_CANCEL, KB_MAIN_BACK, KB_DONE,
)

SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".txt"}


# ── Utility ───────────────────────────────────────────────────────────────────
def _work_dir(user_id: int) -> str:
    d = os.path.join(Config.WORK_DIR, str(user_id))
    os.makedirs(d, exist_ok=True)
    return d

def _cleanup(user_id: int):
    shutil.rmtree(_work_dir(user_id), ignore_errors=True)

def _video_info_line(info: dict) -> str:
    return (
        f"📐  <code>{info['width']}×{info['height']}</code>  "
        f"⏱  <code>{_fmt_time(info['duration'])}</code>  "
        f"💾  <code>{_size(info['size_bytes'])}</code>"
    )


# ── Register all handlers ─────────────────────────────────────────────────────
def register_handlers(app: Client):

    # ══════════════════════════════════════════════════════════════════════════
    #  /start  — show main menu
    # ══════════════════════════════════════════════════════════════════════════
    # track the last menu message per user to avoid stacking
    _menu_msg: dict[int, int] = {}   # uid → message_id

    @app.on_message(filters.command("start"))
    async def cmd_start(client: Client, msg: Message):
        uid = msg.from_user.id
        reset_session(uid)
        # delete old menu message if it exists
        old_id = _menu_msg.get(uid)
        if old_id:
            try:
                await client.delete_messages(msg.chat.id, old_id)
            except Exception:
                pass
        sent = await msg.reply_text(MSG_START, reply_markup=KB_MAIN)
        _menu_msg[uid] = sent.id

    # ══════════════════════════════════════════════════════════════════════════
    #  /cancel  — hard reset
    # ══════════════════════════════════════════════════════════════════════════
    @app.on_message(filters.command("cancel"))
    async def cmd_cancel(client: Client, msg: Message):
        reset_session(msg.from_user.id)
        _cleanup(msg.from_user.id)
        await msg.reply_text(
            "✅ <b>Cancelled</b>\n"
            "──────────────────\n"
            "All temp files cleared.",
            reply_markup=KB_MAIN_BACK,
        )


    # ══════════════════════════════════════════════════════════════════════════
    #  Incoming TEXT (URL or MB target)
    # ══════════════════════════════════════════════════════════════════════════
    @app.on_message(
        filters.text & ~filters.command(["start", "cancel"])
    )
    async def on_text(client: Client, msg: Message):
        if not msg.from_user:
            return  # channel post / anonymous — ignore
        uid  = msg.from_user.id
        s    = get_session(uid)
        text = msg.text.strip()

        # ── URL ──────────────────────────────────────────────────────────────
        if text.startswith("http://") or text.startswith("https://"):
            if s.mode not in (SUB_WAIT_VIDEO, SUB_WAIT_FILE, COMP_WAIT_TARGET):
                await msg.reply_text(
                    "❓ Use the menu first.",
                    reply_markup=KB_MAIN,
                )
                return
            status = await msg.reply_text(
                "⬇️ <b>DOWNLOADING</b>\n"
                "──────────────────\n"
                f"<code>{text[:80]}{'…' if len(text)>80 else ''}</code>\n\n"
                "⏳ <i>Starting…</i>",
                reply_markup=KB_CANCEL,
            )
            try:
                work = _work_dir(uid)
                cb   = make_ytdlp_cb(
                    status,
                    "⬇️ <b>DOWNLOADING</b>\n"
                    "──────────────────\n"
                    f"<code>{text[:60]}…</code>\n",
                )
                path = await download_video(text, work, cb)
                info = await probe_video(path)
                s.video_path = path
                s.duration   = info["duration"]
                await status.edit_text(
                    "✅ <b>Video downloaded</b>\n"
                    "──────────────────\n"
                    + _video_info_line(info)
                )
                await _check_both_ready(client, msg, uid, status)
            except Exception as e:
                await status.edit_text(
                    f"❌ <b>Download failed</b>\n"
                    f"──────────────────\n"
                    f"<code>{str(e)[-300:]}</code>",
                    reply_markup=KB_MAIN_BACK,
                )
            return

        # ── MB target input ───────────────────────────────────────────────────
        if s.mode == COMP_WAIT_TARGET and s.extra.get("waiting_mb"):
            try:
                mb = float(text)
                assert 1 <= mb <= 4000
            except Exception:
                await msg.reply_text("❌ Enter a valid size in MB (e.g. <code>50</code>).")
                return
            s.extra["target_mb"] = mb
            s.extra.pop("waiting_mb")
            await _run_compression(client, msg, uid)
            return

        # Don't reply to random messages — avoids menu spam
        return


    # ══════════════════════════════════════════════════════════════════════════
    #  Incoming FILE (video or subtitle)
    # ══════════════════════════════════════════════════════════════════════════
    @app.on_message(filters.document | filters.video)
    async def on_file(client: Client, msg: Message):
        if not msg.from_user:
            return  # channel post / anonymous — ignore
        uid   = msg.from_user.id
        s     = get_session(uid)

        if s.mode == IDLE:
            await msg.reply_text(
                "❓ Choose an operation first.",
                reply_markup=KB_MAIN,
            )
            return

        media = msg.document or msg.video
        fname = getattr(media, "file_name", None) or "file"
        ext   = Path(fname).suffix.lower()

        # ── Subtitle file ─────────────────────────────────────────────────────
        if ext in SUB_EXTS:
            if s.sub_path:
                await msg.reply_text("⚠️ Already have a subtitle. Send the video.")
                return
            status = await msg.reply_text(
                "⬇️ <b>DOWNLOADING SUBTITLE</b>\n"
                "──────────────────\n"
                f"<code>{fname}</code>\n\n"
                "⏳ <i>Receiving…</i>"
            )
            work = _work_dir(uid)
            raw  = await client.download_media(
                msg,
                file_name=os.path.join(work, "sub_raw" + ext),
                progress=make_transfer_cb(status,
                    "⬇️ <b>DOWNLOADING SUBTITLE</b>\n"
                    "──────────────────\n"
                    f"<code>{fname}</code>\n",
                    "Pyrogram 💥"),
            )
            norm     = normalise_subtitle(raw, work)
            s.sub_path = norm
            s.sub_ext  = Path(norm).suffix.lower()
            await status.edit_text(
                "✅ <b>Subtitle received</b>\n"
                "──────────────────\n"
                f"📄  <code>{Path(norm).name}</code>"
            )
            await _check_both_ready(client, msg, uid, status)
            return

        # ── Video file ────────────────────────────────────────────────────────
        if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".flv", "") or msg.video:
            if s.video_path:
                await msg.reply_text("⚠️ Already have a video. Use ❌ Cancel to reset.")
                return
            status = await msg.reply_text(
                "⬇️ <b>DOWNLOADING VIDEO</b>\n"
                "──────────────────\n"
                f"<code>{fname}</code>\n\n"
                "⏳ <i>Starting…</i>",
                reply_markup=KB_CANCEL,
            )
            try:
                work = _work_dir(uid)
                path = await client.download_media(
                    msg,
                    file_name=os.path.join(work, "input_video"),
                    progress=make_transfer_cb(status,
                        "⬇️ <b>DOWNLOADING VIDEO</b>\n"
                        "──────────────────\n"
                        f"<code>{fname}</code>\n",
                        "Pyrogram 💥"),
                )
                info = await probe_video(path)
                s.video_path = path
                s.duration   = info["duration"]
                await status.edit_text(
                    "✅ <b>Video received</b>\n"
                    "──────────────────\n"
                    + _video_info_line(info)
                )
                await _check_both_ready(client, msg, uid, status)
            except Exception as e:
                await status.edit_text(
                    f"❌ <b>Error</b>\n──────────────────\n<code>{e}</code>",
                    reply_markup=KB_MAIN_BACK,
                )
            return

        await msg.reply_text(f"❓ Unsupported file type: <code>{ext}</code>")


    # ══════════════════════════════════════════════════════════════════════════
    #  Check if both assets are ready → prompt next step
    # ══════════════════════════════════════════════════════════════════════════
    async def _check_both_ready(client, msg, uid, status_msg=None):
        s = get_session(uid)

        async def _reply(text, kb=None):
            if status_msg:
                await status_msg.reply_text(text, reply_markup=kb)
            else:
                await msg.reply_text(text, reply_markup=kb)

        if s.mode in (SUB_WAIT_VIDEO, SUB_WAIT_FILE, SUB_WAIT_CHOICE):
            if s.video_path and s.sub_path:
                s.mode = SUB_WAIT_CHOICE
                await _reply(
                    "✅ <b>Both assets ready!</b>\n"
                    "──────────────────\n\n"
                    "🔥 <b>Burn-in</b>   — subs baked into picture\n"
                    "   (plays everywhere, can't be toggled off)\n\n"
                    "📦 <b>Embed</b>     — selectable track in MKV\n"
                    "   (no re-encode, original quality preserved)\n\n"
                    "Choose subtitle mode:",
                    KB_SUB_TYPE,
                )
            elif s.video_path and not s.sub_path:
                s.mode = SUB_WAIT_FILE
                await _reply(MSG_SUB_WAIT_FILE)
            elif s.sub_path and not s.video_path:
                s.mode = SUB_WAIT_VIDEO
                await _reply(MSG_SUB_WAIT_VIDEO)

        elif s.mode == COMP_WAIT_TARGET:
            if s.video_path:
                await _reply(
                    "✅ <b>Video ready</b>\n"
                    "──────────────────\n\n"
                    "How do you want to compress?",
                    KB_COMP_TYPE,
                )


    # ══════════════════════════════════════════════════════════════════════════
    #  Callback query router
    # ══════════════════════════════════════════════════════════════════════════
    @app.on_callback_query()
    async def on_cb(client: Client, cb: CallbackQuery):
        if not cb.from_user:
            return
        uid  = cb.from_user.id
        s    = get_session(uid)
        data = cb.data
        await cb.answer()

        # ── Menu navigation ───────────────────────────────────────────────────
        if data == "menu:main":
            reset_session(uid)
            _cleanup(uid)
            await cb.message.edit_text(MSG_START, reply_markup=KB_MAIN)
            return

        if data == "menu:help":
            await cb.message.edit_text(MSG_HELP, reply_markup=KB_HELP_BACK)
            return

        if data == "close":
            await cb.message.delete()
            return

        if data == "cancel":
            reset_session(uid)
            _cleanup(uid)
            await cb.message.edit_text(
                "❌ <b>Cancelled</b>\n"
                "──────────────────\n"
                "All temp files cleared.",
                reply_markup=KB_MAIN_BACK,
            )
            return

        # ── Operation selection ───────────────────────────────────────────────
        if data == "menu:sub":
            s.reset()
            s.mode = SUB_WAIT_VIDEO
            await cb.message.edit_text(MSG_SUB_INTRO, reply_markup=KB_CANCEL)
            return

        if data == "menu:compress":
            s.reset()
            s.mode = COMP_WAIT_TARGET
            await cb.message.edit_text(MSG_COMP_INTRO, reply_markup=KB_CANCEL)
            return

        if data == "menu:subcompress":
            s.reset()
            s.mode = SUB_WAIT_VIDEO
            s.extra["subcompress"] = True
            await cb.message.edit_text(MSG_SUBCOMP_INTRO, reply_markup=KB_CANCEL)
            return

        # ── Subtitle type chosen ──────────────────────────────────────────────
        if data in ("sub:burn", "sub:mux"):
            s.extra["sub_type"] = data
            if data == "sub:mux":
                await _run_subtitle(client, cb.message, uid)
            else:
                await cb.message.edit_text(
                    "🔥 <b>BURN-IN MODE</b>\n"
                    "──────────────────\n\n"
                    "Do you also want to scale the resolution\n"
                    "while burning in subtitles?",
                    reply_markup=KB_BURN_SCALE,
                )
            return

        if data == "burn:noscale":
            s.extra["scale_res"] = None
            await _run_subtitle(client, cb.message, uid)
            return

        if data == "burn:scale":
            s.extra["burn_then_res"] = True
            await cb.message.edit_text(
                "📐 <b>Pick target resolution</b>",
                reply_markup=KB_RESOLUTION,
            )
            return

        # ── Resolution chosen ─────────────────────────────────────────────────
        if data.startswith("res:"):
            res = data[4:]
            if s.extra.get("burn_then_res"):
                s.extra["scale_res"] = res
                s.extra.pop("burn_then_res", None)
                await _run_subtitle(client, cb.message, uid)
            elif s.comp_mode == "resolution":
                s.extra["target_res"] = res
                if s.extra.get("subcomp_compress"):
                    await _run_subcompress(client, cb.message, uid)
                else:
                    await _run_compression(client, cb.message, uid)
            return

        # ── Compression type chosen ───────────────────────────────────────────
        if data == "comp:res":
            s.comp_mode = "resolution"
            await cb.message.edit_text(
                "📐 <b>Pick target resolution</b>",
                reply_markup=KB_RESOLUTION,
            )
            return

        if data == "comp:size":
            s.comp_mode = "size"
            s.extra["waiting_mb"] = True
            await cb.message.edit_text(
                "💾 <b>TARGET FILE SIZE</b>\n"
                "──────────────────\n\n"
                "Type the target size in <b>MB</b>:\n"
                "<i>e.g. <code>50</code> for 50 MB</i>"
            )
            return

        # ── Subcompress: also compress? ───────────────────────────────────────
        if data == "subcomp:yes":
            s.extra["subcomp_compress"] = True
            await cb.message.edit_text(
                "📦 <b>Compression mode</b>",
                reply_markup=KB_COMP_TYPE,
            )
            return

        if data == "subcomp:no":
            s.extra.pop("subcompress", None)
            await _run_subtitle(client, cb.message, uid)
            return


    # ══════════════════════════════════════════════════════════════════════════
    #  Run: subtitle only
    # ══════════════════════════════════════════════════════════════════════════
    async def _run_subtitle(client: Client, msg: Message, uid: int):
        s        = get_session(uid)
        work     = _work_dir(uid)
        sub_type = s.extra.get("sub_type", "sub:burn")
        scale_res= s.extra.get("scale_res")
        out_ext  = ".mkv" if sub_type == "sub:mux" else ".mp4"
        out_path = os.path.join(work, f"output{out_ext}")
        t_start  = datetime.now()
        duration = s.duration or 0

        mode_label = "🔥 <b>BURNING SUBTITLES</b>" if sub_type == "sub:burn" else "📦 <b>EMBEDDING SUBTITLES</b>"
        header = (
            f"{mode_label}\n"
            "──────────────────\n"
            f"📄  <code>{Path(s.sub_path).name}</code>\n"
        )

        status = await msg.reply_text(
            f"{header}\n⚙️ <i>Starting FFmpeg…</i>",
            reply_markup=KB_CANCEL,
        )

        def _ffmpeg_cb(status_msg, hdr):
            async def cb(pct, fps, speed, size, bitrate, eta, elapsed):
                await status_bar(
                    msg=status_msg, header=hdr, pct=pct,
                    speed=f"{speed}  ({bitrate})", eta=eta,
                    done=size, total="—",
                    engine=f"FFmpeg ⚙️  {fps:.0f}fps", elapsed=elapsed,
                )
            return cb

        ffmpeg_cb = _ffmpeg_cb(status, header)

        try:
            if sub_type == "sub:mux":
                result = await mux_subtitles(
                    s.video_path, s.sub_path, out_path,
                    progress_cb=ffmpeg_cb, duration=duration,
                )
            else:
                if scale_res:
                    result = await burn_sub_and_compress(
                        s.video_path, s.sub_path, out_path,
                        res_label=scale_res,
                        progress_cb=ffmpeg_cb, duration=duration,
                    )
                else:
                    result = await burn_subtitles(
                        s.video_path, s.sub_path, out_path,
                        progress_cb=ffmpeg_cb, duration=duration,
                    )

            elapsed = _fmt_time((datetime.now() - t_start).total_seconds())
            await status.edit_text(
                f"✅ <b>Done!</b>  <code>{elapsed}</code>\n"
                "──────────────────\n"
                f"💾  <code>{_size(os.path.getsize(result))}</code>  →  uploading…"
            )
            await _send_result(client, msg, uid, result, status, t_start)
        except Exception as e:
            await status.edit_text(
                f"❌ <b>FFmpeg failed</b>\n"
                f"──────────────────\n"
                f"<code>{str(e)[-400:]}</code>",
                reply_markup=KB_MAIN_BACK,
            )
            reset_session(uid)


    # ══════════════════════════════════════════════════════════════════════════
    #  Run: compression only
    # ══════════════════════════════════════════════════════════════════════════
    async def _run_compression(client: Client, msg: Message, uid: int):
        s        = get_session(uid)
        work     = _work_dir(uid)
        out_path = os.path.join(work, "output_compressed.mp4")
        t_start  = datetime.now()

        if s.comp_mode == "resolution":
            res     = s.extra.get("target_res", "720p")
            header  = (
                "📐 <b>COMPRESSING</b>\n"
                "──────────────────\n"
                f"🎯  Target: <code>{res}</code>\n"
            )
        else:
            mb      = s.extra.get("target_mb", 50)
            header  = (
                "💾 <b>COMPRESSING (2-pass)</b>\n"
                "──────────────────\n"
                f"🎯  Target: <code>{mb} MB</code>\n"
            )

        status = await msg.reply_text(
            f"{header}\n⚙️ <i>Starting FFmpeg…</i>",
            reply_markup=KB_CANCEL,
        )

        def _ffmpeg_cb2(status_msg, hdr):
            async def cb(pct, fps, speed, size, bitrate, eta, elapsed):
                await status_bar(
                    msg=status_msg, header=hdr, pct=pct,
                    speed=f"{speed}  ({bitrate})", eta=eta,
                    done=size, total="—",
                    engine=f"FFmpeg ⚙️  {fps:.0f}fps", elapsed=elapsed,
                )
            return cb

        ffmpeg_cb = _ffmpeg_cb2(status, header)
        duration  = get_session(uid).duration or 0

        try:
            if s.comp_mode == "resolution":
                result = await compress_to_res(
                    s.video_path, res, out_path,
                    progress_cb=ffmpeg_cb, duration=duration,
                )
            else:
                result = await compress_to_size(
                    s.video_path, mb, out_path,
                    progress_cb=ffmpeg_cb, duration=duration,
                )

            elapsed = _fmt_time((datetime.now() - t_start).total_seconds())
            await status.edit_text(
                f"✅ <b>Done!</b>  <code>{elapsed}</code>\n"
                "──────────────────\n"
                f"💾  <code>{_size(os.path.getsize(result))}</code>  →  uploading…"
            )
            await _send_result(client, msg, uid, result, status, t_start)
        except Exception as e:
            await status.edit_text(
                f"❌ <b>Compression failed</b>\n"
                f"──────────────────\n"
                f"<code>{str(e)[-400:]}</code>",
                reply_markup=KB_MAIN_BACK,
            )
            reset_session(uid)


    # ══════════════════════════════════════════════════════════════════════════
    #  Run: subtitle + compression combined
    # ══════════════════════════════════════════════════════════════════════════
    async def _run_subcompress(client: Client, msg: Message, uid: int):
        s        = get_session(uid)
        work     = _work_dir(uid)
        out_path = os.path.join(work, "output_subcomp.mp4")
        res_label= s.extra.get("target_res")
        target_mb= s.extra.get("target_mb")
        t_start  = datetime.now()

        comp_str = f"  +  {res_label}" if res_label else (f"  +  {target_mb} MB" if target_mb else "")
        header   = (
            "🔥 <b>BURN + COMPRESS</b>\n"
            "──────────────────\n"
            f"📄  <code>{Path(s.sub_path).name}</code>{comp_str}\n"
        )

        status = await msg.reply_text(
            f"{header}\n⚙️ <i>Starting FFmpeg…</i>",
            reply_markup=KB_CANCEL,
        )

        def _ffmpeg_cb3(status_msg, hdr):
            async def cb(pct, fps, speed, size, bitrate, eta, elapsed):
                await status_bar(
                    msg=status_msg, header=hdr, pct=pct,
                    speed=f"{speed}  ({bitrate})", eta=eta,
                    done=size, total="—",
                    engine=f"FFmpeg ⚙️  {fps:.0f}fps", elapsed=elapsed,
                )
            return cb

        ffmpeg_cb = _ffmpeg_cb3(status, header)
        duration  = get_session(uid).duration or 0

        try:
            result = await burn_sub_and_compress(
                s.video_path, s.sub_path, out_path,
                res_label=res_label, target_mb=target_mb,
                progress_cb=ffmpeg_cb, duration=duration,
            )
            elapsed = _fmt_time((datetime.now() - t_start).total_seconds())
            await status.edit_text(
                f"✅ <b>Done!</b>  <code>{elapsed}</code>\n"
                "──────────────────\n"
                f"💾  <code>{_size(os.path.getsize(result))}</code>  →  uploading…"
            )
            await _send_result(client, msg, uid, result, status, t_start)
        except Exception as e:
            await status.edit_text(
                f"❌ <b>Failed</b>\n──────────────────\n<code>{str(e)[-400:]}</code>",
                reply_markup=KB_MAIN_BACK,
            )
            reset_session(uid)


    # ══════════════════════════════════════════════════════════════════════════
    #  Send result with rich upload progress
    # ══════════════════════════════════════════════════════════════════════════
    async def _send_result(
        client: Client, msg: Message, uid: int,
        path: str, status_msg: Message, t_start: datetime,
    ):
        size_bytes = os.path.getsize(path)
        fname      = Path(path).name

        upload_header = (
            "📤 <b>UPLOADING</b>\n"
            "──────────────────\n"
            f"<code>{fname}</code>\n"
        )

        cb = make_transfer_cb(
            status_msg,
            upload_header,
            engine="Pyrofork 💥",
            total_bytes=size_bytes,
        )

        total_elapsed = _fmt_time((datetime.now() - t_start).total_seconds())
        caption = (
            f"✅ <b>{fname}</b>\n"
            f"💾  <code>{_size(size_bytes)}</code>  ·  ⏱  <code>{total_elapsed}</code>"
        )

        try:
            await client.send_video(
                msg.chat.id,
                video=path,
                caption=caption,
                supports_streaming=True,
                progress=cb,
            )
        except Exception:
            # Fallback for MKV or oversized
            await client.send_document(
                msg.chat.id,
                document=path,
                caption=caption,
                progress=cb,
            )
        finally:
            try:
                await status_msg.delete()
            except Exception:
                pass
            reset_session(uid)
            _cleanup(uid)
