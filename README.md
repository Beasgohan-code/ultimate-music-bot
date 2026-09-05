<h1 align="center">🎵 Ultimate Music Bot</h1>

<p align="center">
  <b>A Telegram voice-chat music player and full group-management suite in one bot.</b><br>
  <sub>aiogram 3 · Pyrogram assistant · PyTgCalls · yt-dlp · Bot API rich messages</sub>
</p>

---

## What this is

Most Telegram bots do one thing. This one merges the two most-requested roles in a
single process:

- **A voice-chat music player** — YouTube search & links, direct files, radio/live
  streams, video streams, queues, loops, seek, per-chat volume, saved playlists,
  lyrics, and downloads.
- **A Miss-Rose-class group manager** — warns, mutes, bans, locks, antiflood,
  notes, filters, blacklists, welcome/goodbye, rules, AFK, reports, and
  per-command disabling.

Everything the bot says is rendered with the **Bot API rich-message system**
(`sendRichMessage` / `InputRichMessage`), so player cards, help pages and settings
panels use real headings, tables, checklists, collapsible blockquotes and dividers
instead of emoji-and-newline soup — with an automatic HTML fallback for clients
that don't support it yet.

---

## Feature tour

### Music

| | |
|---|---|
| **Play anything** | `/play <song or url>`, `/vplay` (video), `/cplay` (channel), `/playnow`, `/playnext`, `/radio`, or reply to any audio/video file |
| **Queue control** | `/queue` (paginated), `/skip [n]`, `/skipto <n>`, `/move <a> <b>`, `/remove <n>`, `/shuffle`, `/clear`, `/stop` |
| **Precision playback** | `/seek 1:30`, `/seekback 15`, `/position`, `/speed 1.25`, `/volume 80`, `/mutevc`, `/unmutevc` |
| **Looping** | `/loop off\|single\|all\|1-10` |
| **Playlists** | `/saveplaylist <name>`, `/playlists`, `/playplaylist <name>`, `/delplaylist <name>`, `/fav`, `/favs` |
| **Extras** | `/lyrics`, `/song` (cached MP3 download), `/history`, `/top`, `/mood <vibe>`, `/player` panel, inline mode |
| **Fair play** | `/voteskip` — non-admins vote to skip; admins and the requester skip instantly |
| **Scheduling** | `/schedule 07:00 lofi`, `/schedule daily 8pm jazz`, `/schedule in 30m rock`, `/schedules`, `/unschedule` |

The **player panel** is an inline keyboard that updates in place — pause, resume,
skip, loop, shuffle, volume, lyrics and queue without typing a command.

Now-playing announcements render as a **generated image card** — blurred cover
art, title, artist, requester and a progress bar. Turn it off per chat if you
prefer plain text.

**Scheduled playback** starts music on a timer — once, or every day at the same
time. Jobs are persisted, so a restart doesn't lose them. Set `/timezone +5:30`
once per chat and clock times mean local time.

Downloaded tracks are **cached by Telegram `file_id`**. The first request for a
song downloads and uploads it; every later request for the same song is a single
API call with no download at all. Point `STORAGE_CHAT_ID` at a private channel to
keep those ids valid across restarts.

### Group management

| | |
|---|---|
| **Warns** | `/warn`, `/dwarn`, `/warns`, `/resetwarn`, `/warnlimit <n>`, `/warnmode ban\|mute\|kick` |
| **Restrictions** | `/ban`, `/tban 2h`, `/unban`, `/kick`, `/mute`, `/tmute 30m`, `/unmute` |
| **Locks** | `/lock <type>`, `/unlock`, `/locks`, `/locktypes` — 20 types incl. `url`, `forward`, `sticker`, `all` |
| **Antiflood** | `/setflood <n>`, `/flood`, with mute/kick/ban escalation |
| **Notes & filters** | `/save`, `/get`, `#note`, `/notes`, `/clearnote`, `/filter`, `/stop <word>`, `/filters` |
| **Blacklist** | `/addblacklist <word>`, `/blacklist`, `/rmblacklist` — word-boundary safe, supports `word*` |
| **Greetings** | `/setwelcome`, `/setgoodbye`, `/welcome on\|off`, `/cleanwelcome` with `{first} {mention} {chatname}` placeholders |
| **Misc** | `/rules`, `/setrules`, `/afk`, `/report`, `/purge`, `/pin`, `/promote`, `/settitle`, `/admins`, `/id`, `/disable <cmd>` |

### Owner & sudo

`/broadcast` (with `-users` / `-pin` flags), `/gban`, `/gbanlist`, `/blacklistchat`,
`/maintenance`, `/stopall`, `/activevc`, `/logs`, `/sysinfo`, `/sudolist`.

---

## Setup

### 1. Requirements

- Python **3.10+**
- **FFmpeg** on `PATH` (required — this is what actually encodes the stream)
- A Telegram **bot token** and a **user account** to act as the streaming assistant

```bash
# Debian/Ubuntu
sudo apt install ffmpeg python3-pip
```

### 2. Install

```bash
git clone https://github.com/Beasgohan-code/ultimate-music-bot
cd ultimate-music-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Fill in the four required values:

| Variable | Where to get it |
|---|---|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) |
| `API_ID` / `API_HASH` | [my.telegram.org](https://my.telegram.org) → API development tools |
| `SESSION_STRING` | run `python session_generator.py` and log in as the assistant account |

`.env.example` documents every other option — quality, limits, auto-leave, Spotify,
MongoDB, the web port and more.

> **Never commit your `.env`.** The session string is a full login to the assistant
> account. If one leaks, revoke it immediately in Telegram → Settings → Devices.

### 4. Run

```bash
python main.py
```

### 5. Use it

1. Add the **bot** to your group and promote it to admin (delete messages, ban
   users, manage voice chats).
2. Add the **assistant account** to the same group, or just run `/join` and it will
   invite itself.
3. Start a voice chat, then `/play never gonna give you up`.

---

## Storage

Works with **zero database setup** — state lives in `data/*.json`. Set `MONGO_URI`
and the same code paths switch to MongoDB automatically, which is what you want for
multi-instance or ephemeral-filesystem hosts. `/sysinfo` reports the active backend.

## Health checks

With `WEB_ENABLED=true` the bot serves a live status page on `WEB_PORT`:

- `GET /` — dashboard with uptime, active voice chats and what's playing where
- `GET /health` — JSON for uptime monitors and platform health checks
- `GET /api/stats` — JSON counters

## Testing

```bash
python -m pytest tests/ -q
```

58 tests covering rich rendering and HTML escaping, command-routing conflicts,
queue and loop semantics, warn escalation, word-boundary blacklist matching, lock
propagation, duration and schedule parsing, placeholder injection safety, locale
integrity, download-cache behaviour, and vote thresholds.

Three tests transcode a real generated tone through FFmpeg to prove the download
pipeline end to end; they skip automatically when FFmpeg is not installed.

---

## Project layout

```
main.py                  entry point — logging, routers, command scopes, shutdown
bot/
  config.py              env parsing, validation, warnings
  handlers/              play · controls · advanced · moderation · grouptools
                         settings · admin · callbacks · inline_mode · start · misc
  services/              stream (PyTgCalls) · queue · database · moderation
                         i18n · lyrics · history · stats · autoleave
  middlewares/           gatekeeper (bans, throttle, disabled cmds) · enforcement
  utils/                 rich (card builder) · cards · guards · formatters
  keyboards/             inline player, settings, help, moderation
  locales/               en · es · hi · ru
  web.py                 status page & health endpoints
tests/                   integration suite
```

## Built with

[aiogram](https://github.com/aiogram/aiogram) ·
[Pyrogram](https://github.com/pyrogram/pyrogram) ·
[PyTgCalls](https://github.com/pytgcalls/pytgcalls) ·
[yt-dlp](https://github.com/yt-dlp/yt-dlp) ·
[Pillow](https://python-pillow.org)
