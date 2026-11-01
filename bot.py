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
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, CallbackQuery
from aiohttp import web

# ---------- Конфиг ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = os.getenv("API_BASE", "https://misugram.cloud-ip.cc")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "50"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "30"))
DB_PATH = os.getenv("DB_PATH", "bot.db")
PORT = int(os.getenv("PORT", "8080"))

DROP_CHANCE = 2.0
GIFT_ID = "9000000000000200"  # Твой ID подарка
DONATE_STARS = 10  # Сумма доната в звёздах

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nft-bot")

session = AiohttpSession(api=TelegramAPIServer.from_base(API_BASE))
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# ---------- БД (без изменений) ----------
# ... (код init_db, get_remaining_today, inc_used_today, record_drop, check_and_set_cooldown оставляем как был) ...

# ---------- Логика ----------
@dp.message(F.text)
async def on_any_message(message: Message) -> None:
    if message.from_user.is_bot:
        return

    # ЛИЧКА
    if message.chat.type == "private":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"⭐ Пожертвовать {DONATE_STARS} Stars", callback_data="donate")]
        ])
        await message.answer(
            "Привет! Я личный бот сардельки для розыгрышей.\n\n"
            "В группах я выдаю 🧟 Зомби Мишку с шансом 2%.",
            reply_markup=kb
        )
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

    # Пытаемся отправить подарок
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

# ---------- Донат (Инвойс) ----------
@dp.callback_query(F.data == "donate")
async def process_donate(callback: CallbackQuery):
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Поддержка сардельки",
        description="Пожертвование на развитие бота",
        payload="donate_payload",
        provider_token="",  # Для Stars оставляем пустым
        currency="XTR",
        prices=[LabeledPrice(label="Донат", amount=DONATE_STARS)]
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    await message.answer(f"❤️ Спасибо за поддержку! Ты задонатил {DONATE_STARS} звёзд.")

# ---------- Веб-сервер и запуск (без изменений) ----------
# ... (код health, start_web_server, main оставляем как был) ...
