import os
import datetime
import asyncio
import logging

import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ========== НАСТРОЙКИ ПРЯМО В КОДЕ ==========
BOT_TOKEN = "ВСТАВЬ_СЮДА_ТОКЕН_ОТ_BOTFATHER"  # ← замени
ADMIN_ID = 123456789                            # ← замени на свой Telegram ID

# Render сам подставит хост, ничего менять не нужно
RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_URL = f"https://{RENDER_HOST}/webhook" if RENDER_HOST else None
PORT = 10000

# База данных из переменной окружения (пароль нельзя светить в коде)
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/minsk")

# ========== БАЗА ДАННЫХ ==========
_pool = None

async def get_db():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _pool

async def init_db():
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS menu (
                id SERIAL PRIMARY KEY,
                restaurant TEXT NOT NULL,
                dish TEXT NOT NULL,
                price REAL NOT NULL,
                address TEXT,
                lat REAL,
                lon REAL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                search_count INTEGER DEFAULT 0,
                loc_count INTEGER DEFAULT 0,
                source TEXT DEFAULT 'direct'
            )
        """)

# ========== БОТ ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
user_location = {}

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🍕 Найти блюдо")],
        [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
        [KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True,
)

async def update_user(user_id, username, first_name, search=0, loc=0, source="direct"):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user_id)
        if row:
            await conn.execute("""
                UPDATE users SET username = $1, first_name = $2, last_seen = $3,
                    search_count = search_count + $4,
                    loc_count = loc_count + $5
                WHERE user_id = $6
            """, username, first_name, now, search, loc, user_id)
        else:
            await conn.execute("""
                INSERT INTO users (user_id, username, first_name, first_seen, last_seen,
                                   search_count, loc_count, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, user_id, username, first_name, now, now, search, loc, source)

# ========== КОМАНДЫ ==========
@dp.message(Command("start"))
async def start_cmd(message: Message, command: CommandObject):
    user = message.from_user
    ref = command.args if command.args else None
    source = f"ref_{ref}" if ref else "direct"
    await update_user(user.id, user.username or "", user.first_name or "", 0, 0, source)
    await message.answer(
        "🍽️ *Минск.Цены.Маршрут*\n\n"
        "🔹 Отправь геолокацию\n"
        "🔹 Напиши название блюда (драники, пицца, стейк)\n"
        "🔹 Получи цены и маршрут\n\n"
        "Админ: `/add`, `/del`, `/list`",
        parse_mode="Markdown",
        reply_markup=main_kb,
    )

@dp.message(Command("add"))
async def add_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.split(maxsplit=1)[1].split("|")
        if len(parts) != 6:
            raise ValueError
        name, dish, price, address, lat, lon = [p.strip() for p in parts]
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO menu (restaurant, dish, price, address, lat, lon) VALUES ($1,$2,$3,$4,$5,$6)",
                name, dish, float(price), address, float(lat), float(lon)
            )
        await message.answer(f"✅ Добавлено: {dish} в {name}")
    except Exception as e:
        logging.error(f"/add error: {e}")
        await message.answer(
            "❌ Формат:\n`/add Ресторан|Блюдо|Цена|Адрес|lat|lon`",
            parse_mode="Markdown",
        )

@dp.message(Command("del"))
async def del_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        row_id = int(message.text.split()[1])
        pool = await get_db()
        async with pool.acquire() as conn:
            result = await conn.execute("DELETE FROM menu WHERE id = $1", row_id)
        await message.answer(f"🗑️ Удалено id={row_id}")
    except Exception:
        await message.answer("Использование: /del id")

@dp.message(Command("list"))
async def list_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, restaurant, dish, price FROM menu ORDER BY id DESC LIMIT 20"
        )
    if not rows:
        return await message.answer("База пуста.")
    text = "📋 <b>Последние 20:</b>\n"
    for r in rows:
        text += f"{r['id']}. {r['restaurant']} | {r['dish']} | {r['price']} BYN\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("check"))
async def check_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM menu ORDER BY id DESC LIMIT 5")
    text = "📋 <b>Последние 5:</b>\n"
    for r in rows:
        text += f"<code>{dict(r)}</code>\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    pool = await get_db()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM users")
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        dau = await conn.fetchval("SELECT COUNT(*) FROM users WHERE last_seen >= $1", today)
        week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        wau = await conn.fetchval("SELECT COUNT(*) FROM users WHERE last_seen >= $1", week_ago)
        searches = await conn.fetchval("SELECT COALESCE(SUM(search_count),0) FROM users")
        locs = await conn.fetchval("SELECT COALESCE(SUM(loc_count),0) FROM users")
    text = (
        f"📊 <b>Статистика:</b>\n"
        f"👥 Всего: {total}\n"
        f"📅 DAU: {dau} | WAU: {wau}\n"
        f"🔍 Поисков: {searches}\n"
        f"📍 Геолокаций: {locs}"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(lambda msg: msg.location is not None)
async def handle_location(message: Message):
    uid = message.from_user.id
    user_location[uid] = (message.location.latitude, message.location.longitude)
    await update_user(uid, message.from_user.username or "", message.from_user.first_name or "", 0, 1)
    await message.answer("✅ Геолокация сохранена! Теперь напиши название блюда.")

@dp.message(lambda msg: msg.text == "🍕 Найти блюдо")
async def prompt_search(message: Message):
    await message.answer("Введи название блюда (драники, пицца, стейк)")

@dp.message(lambda msg: msg.text == "ℹ️ Помощь")
async def help_cmd(message: Message):
    await message.answer(
        "📌 <b>Как пользоваться:</b>\n"
        "1. Отправь геолокацию.\n"
        "2. Напиши название блюда.\n"
        "3. Получи цены и маршрут.",
        parse_mode="HTML",
    )

@dp.message()
async def search_food(message: Message):
    if not message.text or message.text.startswith('/'):
        return
    if message.text in ["🍕 Найти блюдо", "📍 Отправить геолокацию", "ℹ️ Помощь"]:
        return

    query = message.text.strip().lower()
    if not query:
        return

    uid = message.from_user.id
    await update_user(uid, message.from_user.username or "", message.from_user.first_name or "", 1, 0)

    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, restaurant, dish, price, address, lat, lon FROM menu "
            "WHERE LOWER(dish) LIKE $1 ORDER BY price ASC LIMIT 5",
            f"%{query}%"
        )

    if not rows:
        await message.answer(f"❌ По запросу «{query}» ничего не найдено.")
        return

    answer = f"🍽️ *Результаты по «{query}»:*\n\n"
    for r in rows:
        answer += f"🍴 *{r['restaurant']}* — {r['dish']} {r['price']} BYN\n"
        if uid in user_location:
            u_lat, u_lon = user_location[uid]
            url = f"https://yandex.by/maps/?rtext={u_lat},{u_lon}~{r['lat']},{r['lon']}&rtt=auto"
            answer += f"   🗺️ [Маршрут]({url})\n"
        else:
            url = f"https://yandex.by/maps/?mode=search&text={r['lat']},{r['lon']}"
            answer += f"   📍 [На карте]({url})\n"
        answer += "\n"

    if uid not in user_location:
        answer += "💡 *Отправь геолокацию, чтобы построить маршрут от тебя!*"
    else:
        answer += "🚀 Нажми ссылку для Яндекс.Карт."
    await message.answer(answer, parse_mode="Markdown", disable_web_page_preview=True)

# ========== WEBHOOK / POLLING ==========
async def health(request):
    return web.Response(text="OK")

async def on_startup(bot: Bot):
    await init_db()
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    if WEBHOOK_URL:
        await bot.delete_webhook()

dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)

async def run_polling():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

def main():
    logging.basicConfig(level=logging.INFO)
    if WEBHOOK_URL:
        app = web.Application()
        app.router.add_get("/health", health)
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
        setup_application(app, dp, bot=bot)
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        asyncio.run(run_polling())

if __name__ == "__main__":
    main()
