"""Tiny aiohttp status server.

Gives hosting platforms (Render, Koyeb, Railway…) something to health-check and
provides a live at-a-glance dashboard of what the bot is streaming.
"""

from __future__ import annotations

import logging
import time

from aiohttp import web

from bot.config import config
from bot.services.database import database
from bot.services.queue import queue_manager
from bot.services.stats import bot_stats
from bot.services.stream import stream_manager

logger = logging.getLogger(__name__)

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} — status</title>
<style>
 :root {{ color-scheme: dark; }}
 * {{ box-sizing: border-box; }}
 body {{ margin:0; min-height:100vh; font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
        background:radial-gradient(1200px 600px at 20% -10%,#1e2a4a,#0b0f19 60%); color:#e8ecf5;
        display:flex; align-items:center; justify-content:center; padding:32px; }}
 .card {{ width:min(760px,100%); background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.09);
          border-radius:20px; padding:32px; backdrop-filter:blur(12px); box-shadow:0 24px 60px rgba(0,0,0,.45); }}
 h1 {{ margin:0 0 4px; font-size:26px; letter-spacing:-.02em; }}
 .sub {{ color:#93a1bd; font-size:14px; margin-bottom:26px; }}
 .pill {{ display:inline-flex; align-items:center; gap:7px; padding:5px 13px; border-radius:999px;
          background:rgba(43,209,124,.13); color:#4ade80; font-size:13px; font-weight:600; }}
 .dot {{ width:7px; height:7px; border-radius:50%; background:#4ade80; box-shadow:0 0 0 4px rgba(74,222,128,.18); }}
 .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin-top:24px; }}
 .stat {{ background:rgba(255,255,255,.045); border:1px solid rgba(255,255,255,.07);
          border-radius:14px; padding:16px; }}
 .stat b {{ display:block; font-size:24px; font-weight:650; letter-spacing:-.02em; }}
 .stat span {{ color:#8ba0c4; font-size:12px; text-transform:uppercase; letter-spacing:.08em; }}
 table {{ width:100%; border-collapse:collapse; margin-top:26px; font-size:14px; }}
 th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid rgba(255,255,255,.07); }}
 th {{ color:#8ba0c4; font-size:11px; text-transform:uppercase; letter-spacing:.08em; font-weight:600; }}
 td.t {{ color:#cdd8ec; }}
 footer {{ margin-top:26px; color:#6c7f9e; font-size:12px; }}
 .empty {{ color:#7d8fae; font-size:14px; margin-top:24px; }}
</style></head>
<body><div class="card">
  <span class="pill"><span class="dot"></span>Online</span>
  <h1>{name}</h1>
  <div class="sub">Music streaming &amp; group management for Telegram</div>
  <div class="grid">
    <div class="stat"><b>{uptime}</b><span>Uptime</span></div>
    <div class="stat"><b>{active}</b><span>Active VCs</span></div>
    <div class="stat"><b>{plays}</b><span>Total plays</span></div>
    <div class="stat"><b>{chats}</b><span>Chats</span></div>
    <div class="stat"><b>{users}</b><span>Users</span></div>
    <div class="stat"><b>{backend}</b><span>Storage</span></div>
  </div>
  {table}
  <footer>Updated {ts} · health endpoint at <code>/health</code></footer>
</div></body></html>"""


async def _index(request: web.Request) -> web.Response:
    stats = await bot_stats.summary()
    counters = await database.global_counters()

    rows = ""
    for chat_id in stream_manager.active_chats[:12]:
        current = await queue_manager.get_current(chat_id)
        profile = await database.get_chat(chat_id)
        title = _escape(profile.get("title") or str(chat_id))
        track = _escape((current or {}).get("title", "—"))
        qlen = await queue_manager.size(chat_id)
        rows += f"<tr><td class='t'>{title}</td><td class='t'>{track}</td><td>{qlen}</td></tr>"

    table = (
        f"<table><thead><tr><th>Chat</th><th>Now playing</th><th>Queue</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        if rows
        else "<div class='empty'>No active voice chats right now.</div>"
    )

    html = _PAGE.format(
        name=_escape(config.bot_name),
        uptime=stats.get("uptime", "—"),
        active=len(stream_manager.active_chats),
        plays=counters.get("total_plays", 0),
        chats=counters.get("served_chats", 0),
        users=counters.get("served_users", 0),
        backend=database.backend,
        table=table,
        ts=time.strftime("%H:%M:%S UTC", time.gmtime()),
    )
    return web.Response(text=html, content_type="text/html")


async def _health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "bot": config.bot_name,
            "active_voice_chats": len(stream_manager.active_chats),
            "storage": database.backend,
            "uptime": (await bot_stats.summary()).get("uptime"),
        }
    )


async def _stats_api(request: web.Request) -> web.Response:
    stats = await bot_stats.summary()
    counters = await database.global_counters()
    return web.json_response({**stats, **counters, "storage": database.backend})


def _escape(text: str) -> str:
    import html

    return html.escape(str(text), quote=True)


async def start_web_server(bot) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", _index)
    app.router.add_get("/health", _health)
    app.router.add_get("/api/stats", _stats_api)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.web_host, config.web_port)
    await site.start()
    logger.info("Status page on http://%s:%s", config.web_host, config.web_port)
    return runner
