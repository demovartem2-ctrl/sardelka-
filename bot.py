import asyncio
import html
import logging
import os
import random
import time
from datetime import date

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.methods import SendGift
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, CallbackQuery, ForceReply
from aiohttp import web

# ---------- Конфиг ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = os.getenv("API_BASE", "https://misugram.cloud-ip.cc")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "8080"))

DAILY_LIMIT = 50
COOLDOWN_SEC = 30
DROP_CHANCE = 3.0  # Твой шанс 3%
GIFT_ID = "9000000000000200"  # ID мишки

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nft-bot")

session = AiohttpSession(api=TelegramAPIServer.from_base(API_BASE))
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()
db_pool = None

# ---------- БД ----------
async def init_db():
    global db_pool
    if not DATABASE_URL:
        log.error("DATABASE_URL не задан!")
        return
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        async with db_pool.acquire() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS drops (id SERIAL PRIMARY KEY, user_id BIGINT, username TEXT, chat_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            await conn.execute("CREATE TABLE IF NOT EXISTS gift_counter (day TEXT PRIMARY KEY, used INTEGER DEFAULT 0)")
            await conn.execute("CREATE TABLE IF NOT EXISTS cooldown (user_id BIGINT PRIMARY KEY, last_ts BIGINT)")
        log.info("База данных Supabase подключена.")
    except Exception as e:
        log.error(f"Ошибка подключения к БД: {e}")

async def get_remaining_today() -> int:
    if not db_pool: return 50
    today = date.today().isoformat()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT used FROM gift_counter WHERE day = $1", today)
        used = row['used'] if row else 0
    return max(DAILY_LIMIT - used, 0)

async def inc_used_today():
    if not db_pool: return
    today = date.today().isoformat()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO gift_counter (day, used) VALUES ($1, 1) ON CONFLICT (day) DO UPDATE SET used = gift_counter.used + 1", today)

async def record_drop(user_id: int, username: str | None, chat_id: int):
    if not db_pool: return
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO drops (user_id, username, chat_id) VALUES ($1, $2, $3)", user_id, username, chat_id)

async def check_and_set_cooldown(user_id: int) -> int:
    if not db_pool: return 0
    now = int(time.time())
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_ts FROM cooldown WHERE user_id = $1", user_id)
        if row:
            elapsed = now - row['last_ts']
            if elapsed < COOLDOWN_SEC:
                return COOLDOWN_SEC - elapsed
        await conn.execute("INSERT INTO cooldown (user_id, last_ts) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET last_ts = $2", user_id, now)
    return 0

# ---------- Логика сообщений ----------
@dp.message(F.text)
async def on_any_message(message: Message) -> None:
    if message.from_user.is_bot:
        return

    # ЛИЧКА
    if message.chat.type == "private":
        if message.text.isdigit():
            amount = int(message.text)
            if amount < 1 or amount > 10000:
                await message.answer("❌ Сумма от 1 до 10 000 звёзд.")
                return
            try:
                await bot.send_invoice(
                    chat_id=message.from_user.id, title="Поддержка", description=f"Донат {amount} звёзд",
                    payload=f"donate_{amount}", provider_token="", currency="XTR", prices=[LabeledPrice(label="Донат", amount=amount)]
                )
            except Exception as e:
                await message.answer(f"Ошибка: {e}")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐ Пожертвовать", callback_data="donate_start")]])
        await message.answer("Привет! Я бот сардельки. В группах я выдаю 🧟 Зомби Мишку с шансом 3%.", reply_markup=kb)
        return

    # ГРУППЫ
    remaining = await get_remaining_today()
    if remaining <= 0:
        return 

    roll = random.uniform(0, 100)
    if roll >= DROP_CHANCE:
        return 

    wait = await check_and_set_cooldown(message.from_user.id)
    if wait > 0:
        return 

    # ОТПРАВКА ПОДАРКА (через официальный метод aiogram)
    try:
        await bot(SendGift(
            user_id=message.from_user.id,
            gift_id=GIFT_ID,
            text="Тебе выпал 🧟 Зомби Мишка!"
        ))
    except Exception as e:
        log.error(f"Ошибка отправки подарка: {e}")
        await message.reply(f"⚠️ Ошибка подарка (баланс или gift_id):\n{e}")
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
    await callback.message.answer("Напиши, сколько звёзд ты хочешь пожертвовать (от 1 до 10000):", reply_markup=ForceReply(selective=True))
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    await message.answer("❤️ Спасибо за поддержку!")

# ---------- Запуск ----------
async def health(request): return web.Response(text="Bot is running!")

async def main() -> None:
    await init_db()
    
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info(f"Web server started on port {PORT}")

    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Starting polling...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "pre_checkout_query"])

if __name__ == "__main__":
    asyncio.run(main())
