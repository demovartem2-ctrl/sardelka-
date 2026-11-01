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
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, CallbackQuery, ForceReply
from aiohttp import web

# ---------- Конфиг ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = os.getenv("API_BASE", "https://misugram.cloud-ip.cc")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "8080"))

DAILY_LIMIT = 50
# Глобальный кулдаун: 15 часов (15 * 60 * 60 = 54000 секунд)
GLOBAL_COOLDOWN = 54000
DROP_CHANCE = 3.0

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
            # Таблица истории подарков
            await conn.execute("CREATE TABLE IF NOT EXISTS drops (id SERIAL PRIMARY KEY, user_id BIGINT, username TEXT, chat_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            # Таблица для глобального счетчика и времени последнего сброса
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS global_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    used_today INTEGER DEFAULT 0,
                    last_reset_ts BIGINT DEFAULT 0
                )
            """)
            # Создаем единственную запись, если ее нет
            await conn.execute("INSERT INTO global_state (id, used_today, last_reset_ts) VALUES (1, 0, 0) ON CONFLICT DO NOTHING")
        log.info("База данных Supabase подключена.")
    except Exception as e:
        log.error(f"Ошибка подключения к БД: {e}")

async def check_global_status() -> tuple[int, int]:
    """Возвращает (осталось, секунд до сброса). Если осталось > 0, можно выдавать."""
    if not db_pool: return 0, 0
    now = int(time.time())
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT used_today, last_reset_ts FROM global_state WHERE id = 1")
        used = row['used_today']
        last_reset = row['last_reset_ts']

        # Если прошло 15 часов с момента последнего сброса — сбрасываем счетчик
        if now - last_reset >= GLOBAL_COOLDOWN:
            await conn.execute("UPDATE global_state SET used_today = 0, last_reset_ts = $1 WHERE id = 1", now)
            used = 0
            last_reset = now
            
        remaining = max(DAILY_LIMIT - used, 0)
        time_to_reset = max(GLOBAL_COOLDOWN - (now - last_reset), 0)
        
        return remaining, time_to_reset

async def inc_global_used():
    if not db_pool: return
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE global_state SET used_today = used_today + 1 WHERE id = 1")

async def record_drop(user_id: int, username: str | None, chat_id: int):
    if not db_pool: return
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO drops (user_id, username, chat_id) VALUES ($1, $2, $3)", user_id, username, chat_id)

# ---------- Логика сообщений ----------
@dp.message(F.text)
async def on_any_message(message: Message) -> None:
    if message.from_user.is_bot:
        return

    # ЛИЧКА (Всегда отвечает)
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
    remaining, time_to_reset = await check_global_status()
    if remaining <= 0:
        return # Если глобальный лимит исчерпан, бот молчит

    roll = random.uniform(0, 100)
    if roll >= DROP_CHANCE:
        return 

    # Выдаем подарок
    await inc_global_used()
    await record_drop(message.from_user.id, message.from_user.username, message.chat.id)
    
    # Получаем обновленные данные
    remaining_after, _ = await check_global_status()
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
