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

**Assistant account** — `/assistant` shows status; `/setname`, `/setbio`, `/setpfp`
and `/delpfp` edit its profile; `/leaveall` makes it leave every idle chat
(chats with a live stream are skipped); `/rmdownloads` clears cached audio and
thumbnails.

Broadcasts are paced under Telegram's rate limit and retry on `429`. Chats that
block the bot are recorded and skipped next time, and un-skipped automatically
if they talk to the bot again.

---

## Setup

### 1. Requirements

- Python **3.11–3.13** (3.12 recommended; pinned in `.python-version`)
- **FFmpeg** (required — it encodes the stream and transcodes `/song` downloads)
- A Telegram **bot token** and a **user account** to act as the streaming assistant

```bash
# Debian/Ubuntu
sudo apt install ffmpeg python3-pip
```

If your host has no apt (Render's native Python runtime, for example), the
bundled `imageio-ffmpeg` and `nodejs-wheel-binaries` wheels supply static
binaries and the bot puts them on `PATH` automatically at startup.

> **Node is a real dependency, not an optional one.** yt-dlp needs a
> JavaScript runtime to solve YouTube's player challenges. Without one it
> requests a single player client, which servers on datacenter IPs are
> routinely refused by — every query then fails with *"Failed to extract any
> player response"*, which looks like a broken search rather than a missing
> package.

> **MTProto client:** this project needs **kurigram**, not official `pyrogram`.
> Official pyrogram hasn't shipped since 2023 and lacks `GroupcallForbidden`,
> which py-tgcalls imports at load time — installing it makes the bot crash on
> boot. Kurigram is API-compatible and uses the identical session-string format,
> so an existing `SESSION_STRING` keeps working. Never install
> `py-tgcalls[pyrogram]`: that extra reinstalls official pyrogram over the fork.

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

## Deploying

| Host | Notes |
|---|---|
| **Docker** | `docker compose up -d` — the image installs FFmpeg and pins Python 3.12 |
| **Render** | `render.yaml` is committed. Set `PYTHON_VERSION=3.12.7`; Render's current default is 3.14, which is newer than `ntgcalls` and `TgCrypto` target |
| **Anything else** | `pip install -r requirements.txt && python main.py` |

Render reads `.python-version`, **not** `runtime.txt` (that file is ignored and
has been removed). `PYTHON_VERSION` as an env var overrides both.

On boot the bot runs a preflight check: it verifies the MTProto client provides
the symbols py-tgcalls needs, and locates or installs FFmpeg. A misconfigured
dependency produces an explicit message with the fix, rather than a traceback
from deep inside a library.

### Checking your cookies

Cookies expire. An expired jar is worse than no jar at all: yt-dlp silently
drops the dead entries, sends the request unauthenticated, and the failure
looks identical to having no cookies configured. The startup banner now says
which it is:

```
YouTube cookies: loaded (14 cookies, signed in, first expires in 61d)
YouTube cookies: PRESENT BUT UNUSABLE - every cookie has expired
YouTube cookies: PRESENT BUT UNUSABLE - no login cookies (SID / LOGIN_INFO)
```

A jar only works if it contains a live `SID`, `__Secure-1PSID` or `LOGIN_INFO`
cookie — those carry the login. A file full of `VISITOR_INFO1_LIVE`, `PREF` and
`YSC` is not signed in to anything, however long it is.

Set `COOKIES_DIR` to a folder of several jars to rotate between accounts, so no
single one carries every request.

**Never paste cookies into a chat, an issue or a commit.** They are equivalent
to your password and they do not need your 2FA. If you have posted them
anywhere, sign out of that Google account on all devices immediately, which
invalidates them.

## Startup reports

On boot the bot DMs `OWNER_ID` a readiness report: its identity, plus a health
table covering the assistant, ffmpeg, cookies, impersonation, storage and
thumbnails. Anything degraded is named with its reason, so a boot that looks
fine but cannot actually play is visible immediately.

It also sends a short notice on shutdown with the uptime — a restart loop is
invisible otherwise, since the boot message looks identical whether the bot ran
for a month or thirty seconds.

If the DM never arrives, open a chat with the bot from the owner account and
send `/start`: Telegram blocks bots from messaging users first.

## Operating the bot

**Unhandled errors.** Most handlers deliberately have no `try/except`; a single
global handler catches whatever they throw. The user gets a short apology with
a reference id, and the full traceback goes to `LOG_GROUP_ID` — once per
distinct bug, then rate-limited, so one broken handler in a busy group cannot
flood the channel.

The id is a hash of the traceback's *shape*, not its text, so the same bug
keeps the same id across restarts and chats. Quote it to find the traceback.

| Command | Who | Purpose |
| --- | --- | --- |
| `/errors` | sudo | Recent distinct errors, most frequent first |
| `/clearerrors` | sudo | Reset the tracker |

**Health endpoint.** `GET /health` returns `200 {"status":"ok"}` normally and
`503 {"status":"degraded"}` when something is actually wrong — extraction
blocked by the media host, or cookies present but unusable. Point an uptime
monitor at it; a check that can only ever say "ok" is not worth having.

```json
{
  "status": "degraded",
  "extraction_blocked": true,
  "cookies": "PRESENT BUT UNUSABLE - every cookie has expired",
  "problems": ["extraction blocked by the media host"]
}
```

## Keeping chats quiet

A music bot is chatty: every `/play` leaves a command, a status message and a
Now Playing card. Two per-chat toggles under `/settings` -> Clean Mode fix that,
and both are off by default:

| Toggle | Effect |
| --- | --- |
| Clean command messages | Deletes the `/play ...` message the moment it runs |
| Clean player messages | Deletes status and error replies after `CLEAN_MODE_SECONDS` (default 5 min) |

Now Playing cards are never auto-deleted — the player buttons live on them.
Commands in private chats are never deleted either.

Ending the voice chat from Telegram's own menu now clears the queue and stops
the stream, instead of leaving the bot holding state for a call that is gone.

## Music sources

Paste a link from any of these and the bot works out what to do:

| Source | Tracks | Albums / playlists | Notes |
|---|---|---|---|
| YouTube / YT Music | ✅ | ✅ | Streamed directly |
| SoundCloud, Bandcamp, Vimeo, Twitch | ✅ | ✅ | Streamed directly |
| **Spotify** | ✅ | ✅ | Metadata read, audio matched on YouTube |
| **Apple Music** | ✅ | ✅ albums | Via the public iTunes API |
| **Deezer** | ✅ | ✅ | Fully public API, no key needed |
| Direct URLs, m3u8, uploaded files | ✅ | — | Played as-is |

Spotify, Apple Music and Deezer stream DRM-protected audio, so **nothing** can
download the file itself — yt-dlp ships no Spotify extractor at all. What these
links *do* carry is metadata, so the bot reads the title and artist and matches
the recording on a streamable source. Paste an album or playlist link and it
plays the first track and queues the rest.

Deezer and Apple Music need no credentials. Spotify works without them too, via
the public oEmbed endpoint, but that only returns one title per link — set
`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` to expand full albums and
playlists.

Tidal and Amazon Music expose no public metadata, so the bot says so plainly
rather than failing with a generic error.

## When YouTube blocks your server

Cloud IPs get flagged, and then extraction fails for every query. The bot
distinguishes this from a genuine no-results and tells you which it hit. In
order of effectiveness:

| Setting | What it does |
|---|---|
| `COOKIES_FILE` | Cookies exported from a logged-in browser (Netscape format). Most effective, and also unlocks age-restricted videos. Use a throwaway account |
| `COOKIES_DATA` | The same jar inline, for hosts with nowhere to put a file. Raw or base64; written to `data/cookies.txt` at startup |
| `YTDLP_PROXY` | Routes extraction through a residential proxy |
| `YTDLP_JS_RUNTIME` | Pins the JS runtime. Blank autodetects and falls back to bundled Node; `none` disables |

Cookies and proxy apply to streaming, search *and* `/song` downloads.

If YouTube stays blocked, searches automatically fall back to **SoundCloud**,
which runs on unrelated infrastructure. A smaller catalogue beats a bot that
can never play anything. The fallback only triggers on a *block* — a genuine
"no such song" is reported as-is rather than returning an unrelated track from
another service. Set `SEARCH_BACKENDS=soundcloud` to skip YouTube entirely.

The startup log states what is active, so you never have to guess:

```
build caf0e5f | python 3.12
YouTube cookies: loaded
YouTube player clients: default, android_vr, tv, mweb, ios
yt-dlp JS runtime: node
```

The `build` line is the deployed commit. If it does not match what you just
pushed, the host is running older code and no amount of debugging the current
source will explain the behaviour.

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
                         settings · admin · assistant_admin · callbacks
                         inline_mode · start · misc
  services/              stream (PyTgCalls) · queue · database · moderation
                         i18n · lyrics · history · stats · autoleave
                         delivery (rate limits, retries, dead-chat pruning)
                         startup (boot readiness report to the owner)
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
