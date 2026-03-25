# Zilong Converter Bot

A focused Telegram bot for:
- 🎬 Adding subtitles to videos (burn-in or soft embed)
- 📦 Compressing video to a target file size or resolution
- 🔥 Combining both in one shot

Supports subtitle formats: `.srt`, `.ass`, `.ssa`, `.vtt`, `.txt`

---

## Features

### `/sub` — Add Subtitles
- Send a video (file upload or URL) + a subtitle file in any order
- Choose **Burn-in** (hard subs, baked into picture) or **Embed** (soft track in MKV)
- Optionally scale resolution while burning

### `/compress` — Compress Video
- **By resolution** — scale to 4K / 1080p / 720p / 480p / 360p / 240p with H.264/CRF
- **By file size** — 2-pass CBR encode targeting an exact MB value (e.g. `50`)

### `/subcompress` — Subtitles + Compression
- All of the above in one flow

---

## Bot Commands

```
/start        — Welcome message
/sub          — Start subtitle flow
/compress     — Start compression flow
/subcompress  — Start subtitle + compression flow
/cancel       — Cancel and clear temp files
```

---

## Setup

### 1. Clone

```bash
git clone https://github.com/YOUR_USERNAME/zilong_converter
cd zilong_converter
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API_ID, API_HASH, BOT_TOKEN
```

Get credentials at: https://my.telegram.org

### 3. Install

```bash
# System: ffmpeg must be installed
sudo apt install ffmpeg        # Ubuntu/Debian
brew install ffmpeg            # macOS

# Python
pip install -r requirements.txt
```

### 4. Run

```bash
python bot.py
```

---

## Deploy

### Docker (AWS EC2 / any VPS)

```bash
docker build -t zilong-converter .
docker run -d --env-file .env zilong-converter
```

### Railway

1. Push to GitHub
2. New Railway project → Deploy from GitHub
3. Add environment variables in Railway dashboard
4. Done

### Koyeb

1. Push to GitHub
2. New Koyeb app → GitHub source
3. Set env vars, Dockerfile detected automatically
4. Deploy

### Google Colab

See `colab_launcher.py` — run cells in order.

---

## Architecture

```
User sends video + subtitle
         ↓
handlers.py  ← manages conversation state (state.py)
         ↓
ffmpeg_ops.py  ← runs FFmpeg subprocesses
    normalise_subtitle()   (.vtt/.txt → .srt)
    burn_subtitles()       (hard subs)
    mux_subtitles()        (soft embed, MKV)
    compress_to_size()     (2-pass CBR)
    compress_to_res()      (CRF + scale)
    burn_sub_and_compress() (combined)
         ↓
downloader.py  ← yt-dlp for URL downloads
         ↓
progress.py  ← Telegram progress bar callbacks
```

---

## Notes

- Max file size: 2 GB (MTProto / Pyrofork)
- For files > 50 MB, bot uses Pyrofork directly (bypasses Bot API limit)
- 2-pass compression accuracy: ±5% of target (FFmpeg CBR inherent variance)
- `.txt` subtitles are treated as one line per 3-second cue
- `.vtt` is converted to `.srt` before burning
- Soft-mux output is always `.mkv` for full codec compatibility
