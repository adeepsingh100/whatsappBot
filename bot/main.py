"""FastAPI app: /health, /webhook (Evolution Go events), /qr, and a reverse proxy to Evolution Go for everything else."""
import asyncio
import html
import json
import logging
import os
import random
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from bot import llm
from bot.db import DB
from bot.rules import (BATCH_WAIT, BOT_SUSPECT, QUIET_FOR, addressed_to_me, is_echo, is_group_chat, strip_mentions, user, clean_reply, command, human_delay, is_direct_chat, is_ignored,
                       is_self_chat, phone_key,
                       is_sensitive, parse_message, too_old)
from bot.style import build_messages, enforce_style

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

EVO_URL = os.getenv("EVO_URL", "http://127.0.0.1:8080")
GLOBAL_API_KEY = os.getenv("GLOBAL_API_KEY", "")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "twin")
INSTANCE_TOKEN = os.getenv("INSTANCE_TOKEN", "")
PORT = os.getenv("PORT", "10000")
MY_NUMBER = os.getenv("MY_NUMBER", "")
SAFE_MODE = os.getenv("SAFE_MODE", "true").lower() not in ("0", "false", "no", "off")
STYLE_PATH = os.getenv("STYLE_PATH", "data/style.json")
HOP_HEADERS = {"host", "content-length", "content-encoding", "transfer-encoding", "connection", "keep-alive"}


class State:
    """In-memory, per-process. Lost on restart, which is fine: anything older than 30 min is skipped anyway."""
    def __init__(self):
        self.seen: dict[str, float] = {}          # message IDs already handled (webhook retries)
        self.bot_sent: dict[str, float] = {}      # IDs of messages the bot sent
        self.quiet: dict[str, float] = {}         # chat -> stay quiet until
        self.history: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))  # chat -> (from_me, text)
        self.pending: dict[str, list] = defaultdict(list)  # chat -> Incoming waiting for a reply
        self.names: dict[str, str] = {}           # chat -> push name
        self.tasks: dict[str, asyncio.Task] = {}
        self.sending: set[str] = set()            # chats past the point of no return
        self.qr: str | None = None
        self.me: set[str] = set()                 # my JID users (phone number + LID), learned from my messages
        self.style: dict = {}


S = State()
db: DB | None = None


def _remember(d: dict, key: str, now: float) -> None:
    d[key] = now
    if len(d) > 5000:  # prune entries older than an hour
        for k in [k for k, t in d.items() if now - t > 3600]:
            del d[k]


def load_style() -> dict:
    raw = db.get("style") if db else None
    if not raw and Path(STYLE_PATH).exists():
        raw = Path(STYLE_PATH).read_text(encoding="utf-8")
    if not raw:
        log.warning("No style found (DB key 'style' or %s): replies will not sound like you", STYLE_PATH)
        return {}
    style = json.loads(raw)
    log.info("Style loaded: %s messages, %s pairs", style.get("profile", {}).get("messages"), len(style.get("pairs", [])))
    return style


async def evo(path: str, body: dict | None = None, key: str | None = None, method: str = "POST",
              timeout: float = 30) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.request(method, EVO_URL + path, json=body, headers={"apikey": key or INSTANCE_TOKEN})
        r.raise_for_status()
        return r.json() if r.content else {}


async def send_text(chat: str, text: str) -> None:
    r = await evo("/send/text", {"number": chat, "text": text})
    msg_id = ((r.get("data") or {}).get("Info") or {}).get("ID")
    if msg_id:
        _remember(S.bot_sent, msg_id, time.time())


async def setup_instance() -> None:
    """Wait for Evolution Go, create the instance if missing, then connect it with our webhook. Retries forever
    (the API answers 503 until the license is activated)."""
    webhook = f"http://127.0.0.1:{PORT}/webhook"
    while True:
        try:
            instances = (await evo("/instance/all", key=GLOBAL_API_KEY, method="GET")).get("data") or []
            if not any(i.get("token") == INSTANCE_TOKEN for i in instances):
                await evo("/instance/create", {"name": INSTANCE_NAME, "token": INSTANCE_TOKEN}, key=GLOBAL_API_KEY)
                log.info("Created Evolution instance %r", INSTANCE_NAME)
            await evo("/instance/connect", {"webhookUrl": webhook, "immediate": True,
                                            "subscribe": ["MESSAGE", "SEND_MESSAGE", "CONNECTION", "QRCODE"]})
            log.info("Instance connected, webhook -> %s", webhook)
            return
        except Exception as e:  # noqa: BLE001 - keep retrying whatever goes wrong
            hint = " (license not activated? open /manager/login)" if "503" in str(e) else ""
            log.warning("Evolution Go not ready: %s%s; retrying in 15s", e, hint)
            await asyncio.sleep(15)


def ignored_numbers() -> set[str]:
    return set(filter(None, (db.get("ignore") or "").split(",")))


async def run_command(cmd: str, arg: str, chat: str) -> None:
    if cmd in ("/ignore", "/unignore", "/ignored"):
        nums = await asyncio.to_thread(ignored_numbers)
        key = phone_key(arg)
        if cmd != "/ignored" and len(key) < 10:
            return await send_text(chat, f"🤖 usage: {cmd} 919812345678")
        if cmd == "/ignore":
            nums.add(key)
            for c in [c for c in S.pending if phone_key(c.split("@")[0]) == key]:  # drop replies already scheduled
                if (t := S.tasks.get(c)) and not t.done() and c not in S.sending:
                    t.cancel()
                S.pending.pop(c, None)
        elif cmd == "/unignore":
            nums.discard(key)
        await asyncio.to_thread(db.set, "ignore", ",".join(sorted(nums)))
        return await send_text(chat, "🤖 ignoring: " + (", ".join(sorted(nums)) or "nobody"))
    if cmd in ("/on", "/off"):
        await asyncio.to_thread(db.set, "switch", cmd[1:])
        if cmd == "/off":
            for t in S.tasks.values():
                t.cancel()
            S.pending.clear()
    on = await asyncio.to_thread(db.is_on)
    n = await asyncio.to_thread(db.replies_since, 24 * 3600)
    await send_text(chat, f"🤖 bot {'ON' if on else 'OFF'} · {n} replies in last 24h")


def schedule(chat: str) -> None:
    if chat in S.sending:
        return  # current reply is going out; reply_task reschedules for the new messages
    if (t := S.tasks.get(chat)) and not t.done():
        t.cancel()  # restart the batch window / regenerate with the new message included
    S.tasks[chat] = asyncio.create_task(reply_task(chat))


async def reply_task(chat: str) -> None:
    cancelled = False
    try:
        await asyncio.sleep(BATCH_WAIT)
        batch = list(S.pending.get(chat, []))
        if not batch:
            return
        texts = [m.text for m in batch]
        if any(BOT_SUSPECT.search(t) for t in texts):  # never argue about being a bot: skip just this batch
            S.pending.pop(chat, None)
            log.warning("%s asked about a bot: not replying to that message", chat)
            return
        msgs = build_messages(S.style, S.names.get(chat), list(S.history[chat]), SAFE_MODE,
                              SAFE_MODE and is_sensitive(texts), group=chat.endswith("@g.us"))
        reply = await llm.complete(msgs, accept=lambda t: not is_echo(t, texts))
        out = enforce_style(clean_reply(reply), S.style.get("profile", {}))
        if not out:
            log.warning("Empty/unsafe LLM reply for %s, skipping", chat)
            S.pending.pop(chat, None)
            return
        delay = human_delay(" ".join(out))
        await asyncio.sleep(delay * 0.35)  # "reading"
        typing = delay * 0.65
        try:  # cosmetic: blue ticks + "typing…" (Evolution holds the request for `delay` ms)
            await evo("/message/markread", {"number": chat, "id": [m.id for m in batch]})
            await evo("/message/presence", {"number": chat, "state": "composing", "isAudio": False,
                                            "delay": int(typing * 1000)}, timeout=typing + 30)
        except httpx.HTTPError as e:
            log.warning("read/typing for %s failed: %s", chat, e)
            await asyncio.sleep(typing)
        if (S.quiet.get(chat, 0) > time.time() or not await asyncio.to_thread(db.is_on)
                or is_ignored(batch[-1], await asyncio.to_thread(ignored_numbers))):  # re-check right before sending
            S.pending.pop(chat, None)
            return
        S.sending.add(chat)
        S.pending.pop(chat, None)
        for i, line in enumerate(out):
            if i:
                await asyncio.sleep(random.uniform(1.5, 4))
            await send_text(chat, line)
            S.history[chat].append((True, line))
        await asyncio.to_thread(db.log_reply, chat, "\n".join(texts), "\n".join(out), S.names.get(chat, ""))
        log.info("Replied to %s: %r", chat, out)
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception:  # noqa: BLE001
        log.exception("Reply to %s failed", chat)
        S.pending.pop(chat, None)
    finally:
        S.sending.discard(chat)
        if not cancelled and S.pending.get(chat):
            S.tasks[chat] = asyncio.create_task(reply_task(chat))


async def learn_me(m) -> None:
    """Remember my own JID users (phone + LID) so group @mentions of me are recognised."""
    new = {user(j) for j in (m.sender, m.sender_alt) if j} - S.me
    if new:
        S.me |= new
        await asyncio.to_thread(db.set, "me_ids", ",".join(sorted(S.me)))


async def handle(payload: dict) -> None:
    event, now = payload.get("event"), time.time()
    if event == "QRCode":
        S.qr = (payload.get("data") or {}).get("qrcode")
        log.info("New QR code: open /qr?key=GLOBAL_API_KEY")
        return
    if event in ("Connected", "PairSuccess", "LoggedOut", "TemporaryBan", "ConnectFailure"):
        if event in ("Connected", "PairSuccess"):
            S.qr = None
        if event == "PairSuccess":  # carries my phone JID and LID
            data = payload.get("data") or {}
            S.me |= {user(j) for j in (data.get("ID"), data.get("LID"), data.get("jid")) if j}
            await asyncio.to_thread(db.set, "me_ids", ",".join(sorted(S.me)))
        log.warning("WhatsApp: %s %s", event, payload.get("data"))
        return
    m = parse_message(payload)
    if not m or m.id in S.seen or m.id in S.bot_sent:
        return
    _remember(S.seen, m.id, now)
    log.info("msg %s chat=%s sender=%s alt=%s from_me=%s text=%s", m.id, m.chat, m.sender, m.sender_alt,
             m.from_me, m.text is not None)
    if m.from_me:
        await learn_me(m)
    if is_self_chat(m, MY_NUMBER):
        if cmd := command(m.text):
            await run_command(*cmd, m.chat)
        return
    group = is_group_chat(m)
    if not (is_direct_chat(m) or group):
        return
    if m.text:  # in groups, keep who said what so the model sees the conversation
        S.history[m.chat].append((m.from_me, strip_mentions(m.text) if m.from_me or not group
                                  else f"{m.push_name or user(m.sender)}: {strip_mentions(m.text)}"))
    if m.from_me:  # I typed here myself: back off
        S.quiet[m.chat] = now + QUIET_FOR
        if (t := S.tasks.get(m.chat)) and not t.done() and m.chat not in S.sending:
            t.cancel()
        S.pending.pop(m.chat, None)
        return
    if not m.text or too_old(m, now) or S.quiet.get(m.chat, 0) > now:
        return
    if group and not addressed_to_me(m, S.me):  # in groups, only answer when someone talks to me
        return
    if not await asyncio.to_thread(db.is_on) or is_ignored(m, await asyncio.to_thread(ignored_numbers)):
        return
    if m.push_name:
        S.names[m.chat] = m.push_name
    S.pending[m.chat].append(m)
    schedule(m.chat)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db
    db = DB()
    S.style = load_style()
    S.me = set(filter(None, (db.get("me_ids") or "").split(","))) | ({phone} if (phone := re.sub(r"\D", "", MY_NUMBER)) else set())
    setup = asyncio.create_task(setup_instance()) if INSTANCE_TOKEN else None
    if not INSTANCE_TOKEN:
        log.warning("INSTANCE_TOKEN not set: not connecting to Evolution Go")
    yield
    if setup:
        setup.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request):
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": "bad json"}, 400)
    if not INSTANCE_TOKEN or payload.get("instanceToken") != INSTANCE_TOKEN:
        return JSONResponse({"error": "unauthorized"}, 401)
    asyncio.create_task(handle(payload))  # answer within Evolution's 30 s window, work in background
    return {"ok": True}


REPLIES_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Twin replies</title>
<style>
body{font:15px/1.4 system-ui,sans-serif;margin:0;padding:16px;background:#f0f2f5;color:#111}
h1{font-size:18px;margin:0 0 4px}.sub{color:#667;margin-bottom:16px}
.card{background:#fff;border-radius:10px;padding:12px 14px;margin-bottom:10px;box-shadow:0 1px 2px #0001}
.top{display:flex;justify-content:space-between;gap:8px;color:#556;font-size:13px;margin-bottom:6px}
.who{font-weight:600;color:#111}.b{white-space:pre-wrap;padding:6px 10px;border-radius:8px;margin:4px 0;max-width:85%%}
.in{background:#eef}.out{background:#d9fdd3;margin-left:auto}.on{color:#080}.off{color:#a00}
@media(prefers-color-scheme:dark){body{background:#111b21;color:#e9edef}.card{background:#202c33}
.who{color:#e9edef}.top,.sub{color:#8696a0}.in{background:#2a3942}.out{background:#005c4b}}
</style><h1>🤖 Twin replies</h1><div class="sub">Bot is <b class="%s">%s</b> · %d replies in last 24h ·
auto-refresh 30s</div>%s"""


@app.get("/replies")
async def replies(key: str = ""):
    if not GLOBAL_API_KEY or key != GLOBAL_API_KEY:
        return JSONResponse({"error": "forbidden"}, 403)
    rows = await asyncio.to_thread(db.recent_replies)
    on = await asyncio.to_thread(db.is_on)
    n = await asyncio.to_thread(db.replies_since, 24 * 3600)
    e = html.escape
    cards = "".join(
        f'<div class="card"><div class="top"><span class="who">{e(name or chat.split("@")[0])}</span>'
        f'<span>+{e(chat.split("@")[0]) if chat.endswith("@s.whatsapp.net") else ""} '
        f'{time.strftime("%d %b %H:%M", time.localtime(ts))}</span></div>'
        f'<div class="b in">{e(incoming)}</div><div class="b out">{e(reply)}</div></div>'
        for ts, chat, name, incoming, reply in rows) or '<div class="card">No replies yet.</div>'
    return HTMLResponse(REPLIES_PAGE % ("on" if on else "off", "ON" if on else "OFF", n, cards))


@app.get("/qr")
async def qr(key: str = ""):
    if not GLOBAL_API_KEY or key != GLOBAL_API_KEY:
        return JSONResponse({"error": "forbidden"}, 403)
    body = (f'<img src="{html.escape(S.qr)}" width="320"><p>WhatsApp → Linked devices → Link a device</p>'
            if S.qr else "<p>No QR right now (already connected, or not requested yet).</p>")
    return HTMLResponse(f'<meta http-equiv="refresh" content="10"><body style="font-family:sans-serif">{body}</body>')


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(path: str, request: Request):
    """Everything else goes to Evolution Go (manager UI, API, swagger)."""
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS - {"host"}}
    async with httpx.AsyncClient(timeout=60) as c:
        try:
            url = f"{EVO_URL}/{path}" + (f"?{request.url.query}" if request.url.query else "")
            r = await c.request(request.method, url, headers=headers, content=await request.body())
        except httpx.HTTPError as e:
            return JSONResponse({"error": f"Evolution Go unreachable: {e}"}, 502)
    return Response(r.content, r.status_code,
                    headers={k: v for k, v in r.headers.items() if k.lower() not in HOP_HEADERS})
