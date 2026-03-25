FROM python:3.11-slim

# Install FFmpeg + yt-dlp system deps
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create work directory
RUN mkdir -p /tmp/zilong_work

CMD ["python", "bot.py"]
