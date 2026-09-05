# Ultimate Music Bot
#
# Python 3.12: ntgcalls, TgCrypto and pydantic-core all ship mature wheels for
# it. 3.14 is the current Render default but is far newer than these native
# dependencies target.
FROM python:3.12-slim

# FFmpeg is not optional — it is what actually encodes the audio stream and
# performs the MP3 transcode for /song.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

# Fail fast and legibly if the wrong MTProto client ended up installed.
RUN python -c "from pyrogram.errors import GroupcallForbidden" \
    || (echo 'ERROR: incompatible pyrogram — install kurigram' && exit 1)

CMD ["python", "main.py"]
