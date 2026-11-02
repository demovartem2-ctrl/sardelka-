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

GIFT_ID = "9000000000000158"  # ID подарка (Зомби Мишка)
MAX_DONATE = 1000000          # Лимит доната (1 миллион звезд)
DROP_CHANCE = 3.0             # Шанс выдачи 3%

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gift-bot")

session = AiohttpSession(api=TelegramAPIServer.from_base(API_BASE))
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()
db_pool = None

# ---------- БД ----------
async def init_db():
    global db_pool
    if not DATABASE_URL: 
        log.warning("DATABASE_URL не задан. Лимиты не будут сохраняться в БД!")
        return
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, statement_cache_size=0)
        async with db_pool.acquire() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS gift_state (id INTEGER PRIMARY KEY CHECK (id = 1), used_today INTEGER DEFAULT 0, last_reset_ts BIGINT DEFAULT 0)")
            await conn.execute("INSERT INTO gift_state (id, used_today, last_reset_ts) VALUES (1, 0, 0) ON CONFLICT DO NOTHING")
        log.info("База данных подключена.")
    except Exception as e:
        log.error(f"Ошибка БД: {e}")

async def check_gift_status() -> int:
    if not db_pool: return 100  
    now = int(time.time())
    GLOBAL_COOLDOWN = 54000 # 15 часов
    DAILY_LIMIT = 100       # <--- ТУТ ИЗМЕНЕНО НА 100
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT used_today, last_reset_ts FROM gift_state WHERE id = 1")
            used = row['used_today']
            last_reset = row['last_reset_ts']
            if now - last_reset >= GLOBAL_COOLDOWN:
                await conn.execute("UPDATE gift_state SET used_today = 0, last_reset_ts = $1 WHERE id = 1", now)
                used = 0
            return max(DAILY_LIMIT - used, 0)
    except: return 0

async def inc_gift_used():
    if not db_pool: return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE gift_state SET used_today = used_today + 1 WHERE id = 1")
    except: pass

# ---------- Логика ----------
@dp.message(F.text)
async def on_any_message(message: Message) -> None:
    if message.from_user.is_bot: return

    # --- ЛИЧКА (Донат) ---
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
                await message.answer(f"Ошибка от Telegram (возможно, сумма слишком большая для одного платежа):\n{e}")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐ Пожертвовать", callback_data="donate_start")]])
        await message.answer(f"Привет! Я бот для выдачи подарков. В группах я выдаю подарок с шансом {DROP_CHANCE}%.", reply_markup=kb)
        return

    # --- ГРУППЫ (Выдача подарка) ---
    remaining = await check_gift_status()
    if remaining <= 0: return

    if random.uniform(0, 100) >= DROP_CHANCE: return

    # Пытаемся отправить реальный подарок от бота
    try:
        await bot.send_gift(
            user_id=message.from_user.id,
            gift_id=GIFT_ID,
            text="Тебе выпал подарок от бота!"
        )
    except Exception as e:
        log.error(f"Ошибка отправки подарка: {e}")
        await message.reply(f"⚠️ Ошибка выдачи подарка (проверь баланс бота или gift_id):\n{e}")
        return

    await inc_gift_used()
    safe_name = html.escape(message.from_user.full_name)
    
    # Твой кастомный текст
    await message.reply(
        f'<a href="tg://user?id={message.from_user.id}">{safe_name}</a> получает по еблу от @sardelka!\n\n'
        f"🎁 В подарок: 🧟 Зомби Мишка\n"
        f"🎉 Шанс был: {DROP_CHANCE}%",
        parse_mode="HTML"
    )

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
async def health(request): return web.Response(text="Gift Bot is running!")

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
