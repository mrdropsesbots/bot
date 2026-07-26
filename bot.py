import os
import datetime
import asyncio
import logging
import csv
import io
import html
import secrets
import aiosqlite
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_ОТ_BOTFATHER")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

DATABASE = "/data/minsk.db" if Path("/data").exists() else "minsk.db"
RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_URL = f"https://{RENDER_HOST}/webhook" if RENDER_HOST else None
PORT = int(os.getenv("PORT", "10000"))

# ========== БАЗА ДАННЫХ ==========
async def get_conn():
    return await aiosqlite.connect(DATABASE)

async def init_db():
    Path(DATABASE).parent.mkdir(parents=True, exist_ok=True)
    conn = await get_conn()
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS restaurants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        cuisine TEXT,
        address TEXT,
        lat REAL,
        lon REAL,
        phone TEXT,
        work_hours TEXT,
        avg_check REAL DEFAULT 0
    )""")
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS menu (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        restaurant_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        dish TEXT NOT NULL,
        description TEXT,
        price REAL NOT NULL,
        FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE
    )""")
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        search_count INTEGER DEFAULT 0,
        loc_count INTEGER DEFAULT 0,
        source TEXT DEFAULT 'direct',
        lat REAL,
        lon REAL
    )""")
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS favorites (
        user_id INTEGER,
        restaurant_id INTEGER,
        added_at TEXT,
        PRIMARY KEY (user_id, restaurant_id)
    )""")
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        restaurant_id INTEGER,
        rating INTEGER,
        text TEXT,
        created_at TEXT
    )""")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_menu_dish ON menu(LOWER(dish))")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_menu_rest ON menu(restaurant_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rest_name ON restaurants(LOWER(name))")
    await conn.commit()
    await conn.close()

# ========== БОТ ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Клавиатуры
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Найти ресторан")],
            [KeyboardButton(text="🍽️ Меню ресторана")],
            [KeyboardButton(text="📍 Рядом со мной", request_location=True)],
            [KeyboardButton(text="⭐ Избранное")],
            [KeyboardButton(text="📝 Оставить отзыв")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )

def admin_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить ресторан")],
            [KeyboardButton(text="🍕 Добавить блюдо")],
            [KeyboardButton(text="📤 Импорт CSV")],
            [KeyboardButton(text="📥 Экспорт CSV")],
            [KeyboardButton(text="💰 Массовое изменение цен")],
            [KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="🔙 Назад")],
        ],
        resize_keyboard=True,
    )

# ========== ПОМОЩНИКИ ==========
async def update_user(user_id, username, first_name, search=0, loc=0, source="direct", lat=None, lon=None):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = await get_conn()
    c = await conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    row = await c.fetchone()
    if row:
        await conn.execute("""
            UPDATE users SET username = ?, first_name = ?, last_seen = ?,
                search_count = search_count + ?,
                loc_count = loc_count + ?,
                lat = COALESCE(?, lat),
                lon = COALESCE(?, lon)
            WHERE user_id = ?
        """, (username, first_name, now, search, loc, lat, lon, user_id))
    else:
        await conn.execute("""
            INSERT INTO users (user_id, username, first_name, first_seen, last_seen,
                               search_count, loc_count, source, lat, lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, first_name, now, now, search, loc, source, lat, lon))
    await conn.commit()
    await conn.close()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

# ========== КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ==========
@dp.message(Command("start"))
async def start_cmd(message: Message, command: CommandObject):
    user = message.from_user
    ref = command.args if command.args else None
    source = f"ref_{ref}" if ref else "direct"
    await update_user(user.id, user.username or "", user.first_name or "", 0, 0, source)
    text = (
        "🍽️ *Минск.Рестораны.Маршруты*\n\n"
        "🔹 *Найти ресторан* — по названию или кухне\n"
        "🔹 *Меню ресторана* — посмотреть блюда\n"
        "🔹 *Рядом со мной* — 5 ближайших ресторанов\n"
        "🔹 *Избранное* — сохранённые рестораны\n"
        "🔹 *Отзыв* — оценить ресторан\n\n"
        "Админ: /admin"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=main_kb())

@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ Помощь")
async def help_cmd(message: Message):
    await message.answer(
        "📌 *Как пользоваться:*\n\n"
        "1️⃣ Отправь геолокацию, чтобы искать рядом\n"
        "2️⃣ Нажми *Найти ресторан* и введи название\n"
        "3️⃣ Нажми *Меню ресторана* и введи название ресторана\n"
        "4️⃣ Сохраняй рестораны в избранное ⭐\n"
        "5️⃣ Оставляй отзывы 📝\n\n"
        "Маршрут строится через Яндекс.Карты.",
        parse_mode="Markdown", reply_markup=main_kb()
    )

@dp.message(F.text == "🔍 Найти ресторан")
async def prompt_find_restaurant(message: Message):
    await message.answer("Введи название ресторана или тип кухни (итальянская, суши, кафе):")

@dp.message(F.text == "🍽️ Меню ресторана")
async def prompt_menu(message: Message):
    await message.answer("Введи название ресторана, меню которого хочешь посмотреть:")

@dp.message(F.text == "⭐ Избранное")
async def show_favorites(message: Message):
    uid = message.from_user.id
    conn = await get_conn()
    rows = await conn.execute_fetchall("""
        SELECT r.id, r.name, r.cuisine, r.address, r.lat, r.lon
        FROM restaurants r
        JOIN favorites f ON r.id = f.restaurant_id
        WHERE f.user_id = ?
    """, (uid,))
    await conn.close()
    if not rows:
        return await message.answer("⭐ У тебя пока нет избранных ресторанов.")
    text = "⭐ *Твоё избранное:*\n\n"
    for r in rows:
        rid, name, cuisine, address, lat, lon = r
        text += f"🍴 *{name}* ({cuisine or 'нет данных'})\n📍 {address}\n"
        # Маршрут
        conn2 = await get_conn()
        c = await conn2.execute("SELECT lat, lon FROM users WHERE user_id = ?", (uid,))
        uloc = await c.fetchone()
        await conn2.close()
        if uloc and uloc[0]:
            url = f"https://yandex.by/maps/?rtext={uloc[0]},{uloc[1]}~{lat},{lon}&rtt=auto"
            text += f"🗺️ [Маршрут]({url})\n"
        else:
            url = f"https://yandex.by/maps/?mode=search&text={lat},{lon}"
            text += f"🗺️ [На карте]({url})\n"
        text += "\n"
    await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)

@dp.message(F.text == "📝 Оставить отзыв")
async def prompt_review(message: Message):
    await message.answer("Введи название ресторана, который хочешь оценить:")

@dp.message(F.location)
async def handle_location(message: Message):
    uid = message.from_user.id
    lat = message.location.latitude
    lon = message.location.longitude
    await update_user(uid, message.from_user.username or "", message.from_user.first_name or "", 0, 1, lat=lat, lon=lon)
    conn = await get_conn()
    rows = await conn.execute_fetchall("SELECT id, name, cuisine, address, lat, lon FROM restaurants")
    await conn.close()
    if not rows:
        return await message.answer("❌ В базе пока нет ресторанов.")
    nearby = []
    for r in rows:
        rid, name, cuisine, address, rlat, rlon = r
        d = haversine(lat, lon, rlat, rlon)
        nearby.append((d, rid, name, cuisine, address, rlat, rlon))
    nearby.sort(key=lambda x: x[0])
    text = f"📍 *5 ближайших ресторанов:*\n\n"
    for d, rid, name, cuisine, address, rlat, rlon in nearby[:5]:
        text += f"🍴 *{name}* ({cuisine or 'нет данных'})\n"
        text += f"📍 {address} ({d:.1f} км)\n"
        url = f"https://yandex.by/maps/?rtext={lat},{lon}~{rlat},{rlon}&rtt=auto"
        text += f"🗺️ [Маршрут]({url})\n\n"
    await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)

# ========== ОБРАБОТКА ТЕКСТА (ПОИСК, МЕНЮ, ОТЗЫВЫ) ==========
@dp.message()
async def text_handler(message: Message):
    if not message.text or message.text.startswith('/'):
        return
    uid = message.from_user.id
    text = message.text.strip()

    # Админ-панель
    if text == "➕ Добавить ресторан" and uid == ADMIN_ID:
        return await message.answer("Введи данные ресторана в формате:\n`/add_restaurant Название|Кухня|Адрес|Широта|Долгота|Телефон|Часы работы|Средний чек`", parse_mode="Markdown")
    if text == "🍕 Добавить блюдо" and uid == ADMIN_ID:
        return await message.answer("Введи данные блюда:\n`/add_dish Название ресторана|Категория|Блюдо|Описание|Цена`", parse_mode="Markdown")
    if text == "📤 Импорт CSV" and uid == ADMIN_ID:
        return await message.answer("Отправь CSV-файл с ресторанами или меню. Бот определит тип по заголовкам.")
    if text == "📥 Экспорт CSV" and uid == ADMIN_ID:
        return await export_csv_cmd(message)
    if text == "💰 Массовое изменение цен" and uid == ADMIN_ID:
        return await message.answer("Введи:\n`/bulk_price Название ресторана (или 'все')|Категория (или 'все')|+10% или -5% или =15.00`", parse_mode="Markdown")
    if text == "📊 Статистика" and uid == ADMIN_ID:
        return await stats_cmd(message)
    if text == "🔙 Назад" and uid == ADMIN_ID:
        return await message.answer("Главное меню", reply_markup=main_kb())

    # Поиск ресторана
    conn = await get_conn()
    rows = await conn.execute_fetchall(
        "SELECT id, name, cuisine, address, lat, lon, phone, work_hours, avg_check FROM restaurants "
        "WHERE LOWER(name) LIKE ? OR LOWER(cuisine) LIKE ? OR LOWER(address) LIKE ? LIMIT 10",
        (f"%{text.lower()}%", f"%{text.lower()}%", f"%{text.lower()}%")
    )
    if rows:
        await conn.close()
        await update_user(uid, message.from_user.username or "", message.from_user.first_name or "", 1, 0)
        answer = f"🔍 *Найдено ресторанов:* {len(rows)}\n\n"
        for r in rows:
            rid, name, cuisine, address, lat, lon, phone, wh, avg = r
            answer += f"🍴 *{name}*\n"
            if cuisine: answer += f"🍳 Кухня: {cuisine}\n"
            answer += f"📍 {address}\n"
            if phone: answer += f"📞 {phone}\n"
            if wh: answer += f"🕐 {wh}\n"
            if avg: answer += f"💰 Средний чек: {avg} BYN\n"
            # Кнопки
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Меню", callback_data=f"menu_{rid}"),
                 InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav_{rid}")],
                [InlineKeyboardButton(text="🗺️ Маршрут", callback_data=f"route_{rid}"),
                 InlineKeyboardButton(text="📝 Отзывы", callback_data=f"reviews_{rid}")]
            ])
            # Маршрут от пользователя
            conn2 = await get_conn()
            c = await conn2.execute("SELECT lat, lon FROM users WHERE user_id = ?", (uid,))
            uloc = await c.fetchone()
            await conn2.close()
            if uloc and uloc[0]:
                url = f"https://yandex.by/maps/?rtext={uloc[0]},{uloc[1]}~{lat},{lon}&rtt=auto"
                answer += f"🗺️ [Маршрут]({url})\n"
            else:
                url = f"https://yandex.by/maps/?mode=search&text={lat},{lon}"
                answer += f"🗺️ [На карте]({url})\n"
            answer += "\n"
            await message.answer(answer, parse_mode="Markdown", reply_markup=kb, disable_web_page_preview=True)
            answer = ""
        return

    # Меню ресторана
    rows = await conn.execute_fetchall(
        "SELECT id, name FROM restaurants WHERE LOWER(name) LIKE ? LIMIT 5",
        (f"%{text.lower()}%",)
    )
    if rows:
        await conn.close()
        for r in rows:
            rid = r[0]
            rname = r[1]
            conn2 = await get_conn()
            dishes = await conn2.execute_fetchall(
                "SELECT category, dish, description, price FROM menu WHERE restaurant_id = ? ORDER BY category, price",
                (rid,)
            )
            await conn2.close()
            if not dishes:
                await message.answer(f"📋 В ресторане *{rname}* пока нет блюд в меню.", parse_mode="Markdown")
                continue
            answer = f"📋 *Меню: {rname}*\n\n"
            current_cat = ""
            for d in dishes:
                cat, dish, desc, price = d
                if cat != current_cat:
                    answer += f"\n*{cat}*\n"
                    current_cat = cat
                answer += f"• {dish} — {price} BYN"
                if desc: answer += f" ({desc})"
                answer += "\n"
            await message.answer(answer, parse_mode="Markdown")
        return

    # Отзыв — проверим, есть ли такой ресторан
    rows = await conn.execute_fetchall(
        "SELECT id, name FROM restaurants WHERE LOWER(name) LIKE ? LIMIT 1",
        (f"%{text.lower()}%",)
    )
    if rows:
        rid, rname = rows[0]
        await conn.close()
        await message.answer(
            f"Оцени ресторан *{rname}* от 1 до 5 звёзд:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐", callback_data=f"rate_{rid}_1"),
                 InlineKeyboardButton(text="⭐⭐", callback_data=f"rate_{rid}_2"),
                 InlineKeyboardButton(text="⭐⭐⭐", callback_data=f"rate_{rid}_3"),
                 InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data=f"rate_{rid}_4"),
                 InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data=f"rate_{rid}_5")]
            ])
        )
        return

    await conn.close()
    await message.answer(f"❌ По запросу «{text}» ничего не найдено.\n\nПопробуй написать иначе или отправь геолокацию 📍")

# ========== CALLBACKS ==========
@dp.callback_query()
async def callbacks(callback: CallbackQuery):
    data = callback.data
    uid = callback.from_user.id

    if data.startswith("menu_"):
        rid = int(data.split("_")[1])
        conn = await get_conn()
        rname = (await conn.execute_fetchall("SELECT name FROM restaurants WHERE id = ?", (rid,)))[0][0]
        dishes = await conn.execute_fetchall(
            "SELECT category, dish, description, price FROM menu WHERE restaurant_id = ? ORDER BY category, price",
            (rid,)
        )
        await conn.close()
        if not dishes:
            return await callback.message.answer("📋 Меню пока пусто.")
        answer = f"📋 *Меню: {rname}*\n\n"
        current_cat = ""
        for d in dishes:
            cat, dish, desc, price = d
            if cat != current_cat:
                answer += f"\n*{cat}*\n"
                current_cat = cat
            answer += f"• {dish} — {price} BYN"
            if desc: answer += f" ({desc})"
            answer += "\n"
        await callback.message.answer(answer, parse_mode="Markdown")

    elif data.startswith("fav_"):
        rid = int(data.split("_")[1])
        conn = await get_conn()
        await conn.execute("INSERT OR IGNORE INTO favorites (user_id, restaurant_id, added_at) VALUES (?,?,?)",
                           (uid, rid, datetime.datetime.now().isoformat()))
        await conn.commit()
        await conn.close()
        await callback.answer("⭐ Добавлено в избранное!")

    elif data.startswith("route_"):
        rid = int(data.split("_")[1])
        conn = await get_conn()
        r = await conn.execute_fetchall("SELECT lat, lon, name FROM restaurants WHERE id = ?", (rid,))
        await conn.close()
        if not r: return await callback.answer("Ресторан не найден")
        rlat, rlon, rname = r[0]
        conn2 = await get_conn()
        c = await conn2.execute("SELECT lat, lon FROM users WHERE user_id = ?", (uid,))
        uloc = await c.fetchone()
        await conn2.close()
        if uloc and uloc[0]:
            url = f"https://yandex.by/maps/?rtext={uloc[0]},{uloc[1]}~{rlat},{rlon}&rtt=auto"
        else:
            url = f"https://yandex.by/maps/?mode=search&text={rlat},{rlon}"
        await callback.message.answer(f"🗺️ Маршрут до *{rname}*:\n{url}", parse_mode="Markdown", disable_web_page_preview=True)

    elif data.startswith("reviews_"):
        rid = int(data.split("_")[1])
        conn = await get_conn()
        rname = (await conn.execute_fetchall("SELECT name FROM restaurants WHERE id = ?", (rid,)))[0][0]
        reviews = await conn.execute_fetchall(
            "SELECT rating, text, created_at FROM reviews WHERE restaurant_id = ? ORDER BY id DESC LIMIT 10",
            (rid,)
        )
        await conn.close()
        if not reviews:
            return await callback.message.answer(f"📝 У ресторана *{rname}* пока нет отзывов.", parse_mode="Markdown")
        answer = f"📝 *Отзывы: {rname}*\n\n"
        for rev in reviews:
            rating, text, created = rev
            stars = "⭐" * rating
            answer += f"{stars}\n{text or 'Без текста'}\n_{created}_\n\n"
        await callback.message.answer(answer, parse_mode="Markdown")

    elif data.startswith("rate_"):
        parts = data.split("_")
        rid = int(parts[1])
        rating = int(parts[2])
        # Сохраняем во временное хранилище (просто спрашиваем текст)
        await callback.message.answer("Напиши текст отзыва (или отправь '-' чтобы пропустить):")
        # Сохраним в памяти ожидание текста отзыва — используем FSM через простой словарь
        pending_reviews[uid] = {"restaurant_id": rid, "rating": rating}
        await callback.answer()

# Хранилище ожидающих отзывов
pending_reviews = {}

@dp.message(F.text)
async def review_text_handler(message: Message):
    uid = message.from_user.id
    if uid in pending_reviews:
        info = pending_reviews.pop(uid)
        rid = info["restaurant_id"]
        rating = info["rating"]
        text = message.text if message.text != "-" else ""
        conn = await get_conn()
        await conn.execute(
            "INSERT INTO reviews (user_id, restaurant_id, rating, text, created_at) VALUES (?,?,?,?,?)",
            (uid, rid, rating, text, datetime.datetime.now().isoformat())
        )
        await conn.commit()
        await conn.close()
        await message.answer("✅ Спасибо за отзыв!")

# ==========