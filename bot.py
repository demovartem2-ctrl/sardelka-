import asyncio
import logging
import os
import random
from datetime import date

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web

# ---------- Конфиг ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = os.getenv("API_BASE", "https://misugram.cloud-ip.cc")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change_me")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "96"))
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "1") == "1"
PORT = int(os.getenv("PORT", "8080"))

DB_PATH = os.getenv("DB_PATH", "bot.db")
DROP_CHANCE = 1.0  # 1% шанс

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nft-bot")

# ---------- Бот через ваш шлюз ----------
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

# ---------- Логика дропа ----------
async def try_drop(message: Message, target) -> None:
    """
    target — объект User (получатель подарка), message — контекст (команда/reply).
    """
    remaining = await get_remaining_today()
    if remaining <= 0:
        await message.reply("🎁 Подарки на сегодня закончились. Возвращайтесь завтра!")
        return

    roll = random.uniform(0, 100)
    if roll >= DROP_CHANCE:
        await message.reply("😔 В этот раз не повезло. Попробуйте ещё!")
        return

    # Выиграл
    await inc_used_today()
    await record_drop(target.id, target.username, message.chat.id)

    remaining_after = await get_remaining_today()

    text = (
        f"<a href=\"tg://user?id={target.id}\">{target.full_name}</a> получает "
        f"🧟 <b>Крутой NFT</b> от @sardelka!\n\n"
        f"🎉 Шанс был: {int(DROP_CHANCE)}%\n"
        f"🎁 Осталось подарков на сегодня: {remaining_after}"
    )
    await message.reply(text, parse_mode="HTML")

# ---------- Хендлеры ----------
@dp.message(Command("drop"))
async def cmd_drop(message: Message) -> None:
    """Reply на сообщение пользователя: /drop — дроп ему."""
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply(
            "Ответьте командой /drop на сообщение пользователя, которому хотите подарить NFT."
        )
        return
    await try_drop(message, message.reply_to_message.from_user)

@dp.message(Command("dropme"))
async def cmd_dropme(message: Message) -> None:
    """Для теста: /dropme — дроп себе."""
    await try_drop(message, message.from_user)

# ---------- Webhook / Polling ----------
async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")

async def main() -> None:
    await init_db()

    if not USE_WEBHOOK:
        log.info("Starting polling...")
        await dp.start_polling(bot)
        return

    if not WEBHOOK_BASE_URL:
        raise RuntimeError("WEBHOOK_BASE_URL пустой, а USE_WEBHOOK=1")

    webhook_path = f"/webhook/{WEBHOOK_SECRET}"
    full_url = f"{WEBHOOK_BASE_URL}{webhook_path}"

    await bot.set_webhook(full_url, allowed_updates=dp.resolve_used_update_types())
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
        await bot.delete_webhook()
        await session.close()

if __name__ == "__main__":
    asyncio.run(main())
