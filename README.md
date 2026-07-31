# Ultimate Music Bot

A premium Telegram music & video bot with voice chat streaming, live streams, lyrics, song suggestions, and a modern HTML UI with styled buttons.

## Features

### Playback
- **Audio streaming** — YouTube search, URLs, uploaded files (MP3, M4A, OGG, FLAC)
- **Video streaming** — MKV, MP4, WebM up to 720p in voice chats
- **Live streams** — m3u8, YouTube Live via `/vstream`
- **Queue system** — Auto-advance, shuffle, loop (off/single/all)
- **Volume control** — 1–200% with live adjustment

### Commands
| Command | Description |
|---------|-------------|
| `/play` | Play audio in voice chat |
| `/song` | Search & play a song |
| `/cplay` | Channel/group play (queues if playing) |
| `/vplay` | Stream video (MKV/MP4) |
| `/vstream` | Live stream (m3u8/YouTube Live) |
| `/search` | Interactive search with buttons |
| `/lyrics` | Get song lyrics |
| `/suggest` | Song recommendations |
| `/pause` `/resume` `/skip` `/stop` | Playback controls |
| `/queue` `/shuffle` `/loop` `/clear` | Queue management |
| `/volume` | Set volume (1–200) |
| `/now` | Current track info |
| `/panel` | Open control panel |
| `/help` | Full command list |

### Premium UI
- HTML formatting with **bold**, *italic*, `code`, and blockquotes
- Styled inline buttons (primary/success/danger)
- Interactive control panel with full button controls
- Search picker & suggestion cards

## Requirements

- Python 3.10+
- FFmpeg installed and in PATH
- Telegram Bot Token ([@BotFather](https://t.me/BotFather))
- API ID & Hash ([my.telegram.org](https://my.telegram.org))
- A Telegram user account for the assistant (joins voice chats)

## Quick Start

### 1. Clone & install

```bash
cd ultimate-music-bot
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
BOT_TOKEN=your_bot_token
API_ID=12345678
API_HASH=your_api_hash
SESSION_STRING=your_session_string
ASSISTANT_USERNAME=your_assistant_username
SUDO_USERS=your_telegram_user_id
```

### 3. Generate session string

```bash
python session_generator.py
```

Log in with your **assistant account** (not the bot). Copy the output into `SESSION_STRING` in `.env`.

### 4. Run

```bash
python main.py
```

### 5. Use in a group

1. Add the bot and the assistant account to your group
2. Promote both with permission to manage voice chats
3. Start a voice chat
4. Send `/play never gonna give you up` or tap the buttons

## Project Structure

```
ultimate-music-bot/
├── main.py                 # Entry point
├── session_generator.py    # Generate assistant session
├── bot/
│   ├── config.py           # Configuration
│   ├── handlers/           # Command & callback handlers
│   ├── keyboards/          # Premium inline keyboards
│   ├── services/           # Music, queue, stream, lyrics
│   └── utils/              # Formatters & helpers
├── assistant/
│   └── client.py           # Pyrogram userbot
├── requirements.txt
└── .env.example
```

## Tech Stack

- [aiogram 3](https://docs.aiogram.dev/) — Modern async Telegram bot framework
- [Pyrogram](https://docs.pyrogram.org/) — MTProto userbot for voice chats
- [py-tgcalls](https://github.com/pytgcalls/pytgcalls) — WebRTC voice chat streaming
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Media extraction from YouTube & more
- FFmpeg — Audio/video transcoding

---

Built with [BrainDaemon](https://braindaemon.com)
