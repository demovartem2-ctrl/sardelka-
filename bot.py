import asyncio
import html
import logging
import os
import random
import time

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

MAX_DONATE = 1000000
DROP_CHANCE = 100.0  # ВРЕМЕННО 100% ДЛЯ ТЕСТА
GLOBAL_COOLDOWN = 0  # ВРЕМЕННО 0 СЕКУНД
DAILY_LIMIT = 1000   # ВРЕМЕННО 1000 ЛИМИТ

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
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, statement_cache_size=0)
        async with db_pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS gift_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1), 
                    used_today INTEGER DEFAULT 0, 
                    last_reset_ts BIGINT DEFAULT 0,
                    last_gift_ts BIGINT DEFAULT 0
                )
            """)
            await conn.execute("INSERT INTO gift_state (id, used_today, last_reset_ts, last_gift_ts) VALUES (1, 0, 0, 0) ON CONFLICT DO NOTHING")
        log.info("База данных подключена.")
    except Exception as e:
        log.error(f"Ошибка БД: {e}")

async def check_status() -> tuple[int, int]:
    if not db_pool: return DAILY_LIMIT, 0
    now = int(time.time())
    COOLDOWN_RESET = 54000 
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT used_today, last_reset_ts, last_gift_ts FROM gift_state WHERE id = 1")
            used = row['used_today']
            last_reset = row['last_reset_ts']
            last_gift = row['last_gift_ts']

            if now - last_reset >= COOLDOWN_RESET:
                await conn.execute("UPDATE gift_state SET used_today = 0, last_reset_ts = $1 WHERE id = 1", now)
                used = 0
            
            remaining = max(DAILY_LIMIT - used, 0)
            time_since_last = now - last_gift
            wait_time = max(GLOBAL_COOLDOWN - time_since_last, 0)

            return remaining, wait_time
    except Exception as e:
        log.error(f"Ошибка проверки статуса: {e}")
        return 0, 0

async def inc_gift_used():
    if not db_pool: return
    now = int(time.time())
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE gift_state SET used_today = used_today + 1, last_gift_ts = $1 WHERE id = 1", now)
    except: pass

# ---------- Логика ----------
@dp.message(F.text)
async def on_any_message(message: Message) -> None:
    # ЛОГ: СРАЗУ ПИШЕМ В КОНСОЛЬ, ЧТО БОТ УВИДЕЛ СООБЩЕНИЕ
    log.info(f"Получено сообщение от {message.from_user.id} в чате {message.chat.id} (тип: {message.chat.type})")

    if message.from_user.is_bot: return

    # --- ЛИЧКА ---
    if message.chat.type == "private":
        if message.text.isdigit():
            amount = int(message.text)
            if amount < 1 or amount > MAX_DONATE:
                await message.answer(f"❌ Сумма должна быть от 1 до {MAX_DONATE:,} звёзд.".replace(",", " "))
                return
            try:
                await bot.send_invoice(
                    chat_id=message.from_user.id, title="Поддержка", description=f"Донат {amount} звёзд",
                    payload=f"donate_{amount}", provider_token="", currency="XTR", prices=[LabeledPrice(label="Донат", amount=amount)]
                )
            except Exception as e:
                await message.answer(f"Ошибка от Telegram:\n{e}")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐ Пожертвовать", callback_data="donate_start")]])
        await message.answer(f"Привет! Я бот. В группах я выдаю Крутой NFT.", reply_markup=kb)
        return

    # --- ГРУППЫ ---
    remaining, wait_time = await check_status()
    
    if remaining <= 0: 
        log.info("Подарки закончились")
        return
    if wait_time > 0: 
        log.info(f"Кулдаун: {wait_time} сек")
        return
    if random.uniform(0, 100) >= DROP_CHANCE: 
        log.info("Шанс не выпал")
        return

    # Выдаем
    await inc_gift_used()
    safe_name = html.escape(message.from_user.full_name)
    
    text = (
        f'<a href="tg://user?id={message.from_user.id}">{safe_name}</a>, поздравляю, ты выиграл <b>Крутой NFT</b> от @sardelka.\n'
        f"Напиши ему, чтобы он тебе его выдал.\n\n"
        f"🎉 Шанс был: {DROP_CHANCE:g}%"
    )
    
    try:
        await message.reply(text, parse_mode="HTML")
        log.info(f"УСПЕХ! Отправлено сообщение в чат {message.chat.id}")
    except Exception as e:
        log.error(f"ОШИБКА ОТПРАВКИ СООБЩЕНИЯ: {e}")

# ---------- Донат ----------
@dp.callback_query(F.data == "donate_start")
async def process_donate_start(callback: CallbackQuery):
    await callback.message.answer(
        f"Напиши, сколько звёзд ты хочешь пожертвовать (от 1 до {MAX_DONATE:,}):".replace(",", " "),
        reply_markup=ForceReply(selective=True)
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    await message.answer("❤️ Спасибо за поддержку!")

# ---------- Запуск ----------
async def health(request): return web.Response(text="NFT Bot is running!")

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
