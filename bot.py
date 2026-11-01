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
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, CallbackQuery, ForceReply
from aiohttp import web

# ---------- Конфиг ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = os.getenv("API_BASE", "https://misugram.cloud-ip.cc")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "50"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "30"))
DB_PATH = os.getenv("DB_PATH", "bot.db") 
PORT = int(os.getenv("PORT", "8080"))

DROP_CHANCE = 2.0
GIFT_ID = "9000000000000200"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nft-bot")

session = AiohttpSession(api=TelegramAPIServer.from_base(API_BASE))
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# ---------- БД ----------
async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS drops (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, chat_id INTEGER, created_at TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS gift_counter (day TEXT PRIMARY KEY, used INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS cooldown (user_id INTEGER PRIMARY KEY, last_ts INTEGER)")
        await db.commit()

async def get_remaining_today() -> int:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT used FROM gift_counter WHERE day = ?", (today,)) as cur:
            row = await cur.fetchone()
        used = row[0] if row else 0
    return max(DAILY_LIMIT - used, 0)

async def inc_used_today() -> None:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO gift_counter (day, used) VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET used = used + 1", (today,))
        await db.commit()

async def record_drop(user_id: int, username: str | None, chat_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO drops (user_id, username, chat_id, created_at) VALUES (?, ?, ?, datetime('now'))", (user_id, username, chat_id))
        await db.commit()

async def check_and_set_cooldown(user_id: int) -> int:
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_ts FROM cooldown WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        if row:
            elapsed = now - row[0]
            if elapsed < COOLDOWN_SEC:
                return COOLDOWN_SEC - elapsed
        await db.execute("INSERT INTO cooldown (user_id, last_ts) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET last_ts = excluded.last_ts", (user_id, now))
        await db.commit()
    return 0

# ---------- Логика сообщений ----------
@dp.message(F.text)
async def on_any_message(message: Message) -> None:
    if message.from_user.is_bot:
        return

    if message.chat.type == "private":
        # Если пользователь ввел число (сумму доната)
        if message.text.isdigit():
            amount = int(message.text)
            if amount < 1 or amount > 10000:
                await message.answer("❌ Сумма должна быть от 1 до 10 000 звёзд.")
                return
            
            try:
                await bot.send_invoice(
                    chat_id=message.from_user.id,
                    title="Поддержка сардельки",
                    description=f"Пожертвование на {amount} звёзд",
                    payload=f"donate_{amount}",
                    provider_token="",
                    currency="XTR",
                    prices=[LabeledPrice(label="Донат", amount=amount)]
                )
            except Exception as e:
                await message.answer(f"Ошибка при создании счета: {e}")
            return

        # Если просто написал текст (приветствие)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Пожертвовать Stars", callback_data="donate_start")]
        ])
        await message.answer(
            "Привет! Я личный бот сардельки для розыгрышей.\n\n"
            "В группах я выдаю 🧟 Зомби Мишку с шансом 2%.",
            reply_markup=kb
        )
        return

    # --- Логика для ГРУПП ---
    remaining = await get_remaining_today()
    if remaining <= 0:
        return 

    roll = random.uniform(0, 100)
    if roll >= DROP_CHANCE:
        return 

    wait = await check_and_set_cooldown(message.from_user.id)
    if wait > 0:
        return 

    try:
        await bot.send_gift(
            user_id=message.from_user.id,
            gift_id=GIFT_ID,
            text=f"Тебе выпал 🧟 Зомби Мишка! Шанс: {DROP_CHANCE}%"
        )
    except Exception as e:
        log.error(f"Ошибка отправки подарка: {e}")
        await message.reply(f"⚠️ Ошибка: не удалось выдать подарок. (Баланс бота или неверный gift_id)\n{e}")
        return

    await inc_used_today()
    await record_drop(message.from_user.id, message.from_user.username, message.chat.id)
    remaining_after = await get_remaining_today()
    safe_name = html.escape(message.from_user.full_name)

    text = (
        f'<a href="tg://user?id={message.from_user.id}">{safe_name}</a> получает '
        f"🧟 <b>Зомби Мишку</b> от @sardelka!\n\n"
        f"🎉 Шанс был: {DROP_CHANCE:g}%\n"
        f"🎁 Осталось подарков на сегодня: {remaining_after}"
    )
    await message.reply(text, parse_mode="HTML")

# ---------- Донат ----------
@dp.callback_query(F.data == "donate_start")
async def process_donate_start(callback: CallbackQuery):
    await callback.message.answer(
        "Напиши, сколько звёзд ты хочешь пожертвовать (от 1 до 10 000):",
        reply_markup=ForceReply(selective=True)
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    await message.answer("❤️ Спасибо за поддержку! Твои звёзды улетели сардельке.")

# ---------- Веб-сервер ----------
async def health(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info(f"Web server started on port {PORT}")

# ---------- Запуск ----------
async def main() -> None:
    await init_db()
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Starting polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
