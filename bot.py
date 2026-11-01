import asyncio
import html
import logging
import os
import random
import time
from datetime import date

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import Message
from aiohttp import web

# ---------- Конфиг ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = os.getenv("API_BASE", "https://misugram.cloud-ip.cc")

# Если WEBHOOK_BASE_URL не задан вручную, берем системную переменную Render
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL", "")
WEBHOOK_BASE_URL = WEBHOOK_BASE_URL.rstrip("/")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change_me")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "50"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "30"))
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "1") == "1"
PORT = int(os.getenv("PORT", "8080"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

DROP_CHANCE = 0.5 

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nft-bot")

session = AiohttpSession(api=TelegramAPIServer.from_base(API_BASE))
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# ---------- БД ----------
async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS drops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                chat_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS gift_counter (
                day TEXT PRIMARY KEY,
                used INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS cooldown (
                user_id INTEGER PRIMARY KEY,
                last_ts INTEGER NOT NULL
            )
            """
        )
        await db.commit()

async def get_remaining_today() -> int:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT used FROM gift_counter WHERE day = ?", (today,)
        ) as cur:
            row = await cur.fetchone()
        used = row[0] if row else 0
    return max(DAILY_LIMIT - used, 0)

async def inc_used_today() -> None:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO gift_counter (day, used) VALUES (?, 1)
            ON CONFLICT(day) DO UPDATE SET used = used + 1
            """,
            (today,),
        )
        await db.commit()

async def record_drop(user_id: int, username: str | None, chat_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO drops (user_id, username, chat_id, created_at) VALUES (?, ?, ?, datetime('now'))",
            (user_id, username, chat_id),
        )
        await db.commit()

async def check_and_set_cooldown(user_id: int) -> int:
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT last_ts FROM cooldown WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            elapsed = now - row[0]
            if elapsed < COOLDOWN_SEC:
                return COOLDOWN_SEC - elapsed
        await db.execute(
            """
            INSERT INTO cooldown (user_id, last_ts) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_ts = excluded.last_ts
            """,
            (user_id, now),
        )
        await db.commit()
    return 0

# ---------- Логика ----------
@dp.message(F.text)
async def on_any_message(message: Message) -> None:
    if message.from_user.is_bot:
        return

    remaining = await get_remaining_today()
    if remaining <= 0:
        return 

    roll = random.uniform(0, 100)
    if roll >= DROP_CHANCE:
        return 

    wait = await check_and_set_cooldown(message.from_user.id)
    if wait > 0:
        return 

    await inc_used_today()
    await record_drop(message.from_user.id, message.from_user.username, message.chat.id)
    
    remaining_after = await get_remaining_today()
    safe_name = html.escape(message.from_user.full_name)
    chance_str = f"{DROP_CHANCE:g}"

    text = (
        f'<a href="tg://user?id={message.from_user.id}">{safe_name}</a> получает '
        f"🧟 <b>Крутой NFT</b> от @sardelka!\n\n"
        f"🎉 Шанс был: {chance_str}%\n"
        f"🎁 Осталось подарков на сегодня: {remaining_after}"
    )
    await message.reply(text, parse_mode="HTML")

# ---------- Webhook / Polling ----------
async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok")

async def main() -> None:
    await init_db()

    if not USE_WEBHOOK:
        log.info("Starting polling...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        return

    if not WEBHOOK_BASE_URL:
        raise RuntimeError("Нет ни WEBHOOK_BASE_URL, ни RENDER_EXTERNAL_URL. Проверь настройки Render.")

    webhook_path = f"/webhook/{WEBHOOK_SECRET}"
    full_url = f"{WEBHOOK_BASE_URL}{webhook_path}"

    await bot.set_webhook(full_url, drop_pending_updates=True, allowed_updates=dp.resolve_used_update_types())
    log.info("Webhook set to %s", full_url)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_post(webhook_path, dp.webhook_handler(bot))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Listening on 0.0.0.0:%s", PORT)

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await session.close()

if __name__ == "__main__":
    asyncio.run(main())
