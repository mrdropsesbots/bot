import os
import datetime
import asyncio
import logging
import csv
import io
import aiosqlite
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile,
)
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)
from aiohttp import web

# ========== SETTINGS ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE = "/data/minsk.db" if Path("/data").exists() else "minsk.db"
RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_URL = f"https://{RENDER_HOST}/webhook" if RENDER_HOST else None
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(level=logging.INFO)

# ========== DATABASE ==========
async def get_conn():
    return await aiosqlite.connect(DATABASE)

async def init_db():
    Path(DATABASE).parent.mkdir(parents=True, exist_ok=True)
    conn = await get_conn()
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS restaurants ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, cuisine TEXT, address TEXT, "
        "lat REAL, lon REAL, phone TEXT, work_hours TEXT, "
        "avg_check REAL DEFAULT 0)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS menu ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "restaurant_id INTEGER NOT NULL, category TEXT NOT NULL, "
        "dish TEXT NOT NULL, description TEXT, price REAL NOT NULL)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "user_id INTEGER PRIMARY KEY, username TEXT, "
        "first_name TEXT, first_seen TEXT NOT NULL, "
        "last_seen TEXT NOT NULL, search_count INTEGER DEFAULT 0, "
        "loc_count INTEGER DEFAULT 0, source TEXT DEFAULT 'direct', "
        "lat REAL, lon REAL)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS favorites ("
        "user_id INTEGER, restaurant_id INTEGER, added_at TEXT, "
        "PRIMARY KEY (user_id, restaurant_id))"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS reviews ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
        "restaurant_id INTEGER, rating INTEGER, text TEXT, "
        "created_at TEXT)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_menu_dish ON menu(LOWER(dish))"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rest_name ON restaurants(LOWER(name))"
    )
    await conn.commit()
    await conn.close()

# ========== BOT ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

pending_reviews = {}


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


async def update_user(
    user_id, username, first_name, search=0, loc=0,
    source="direct", lat=None, lon=None
):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = await get_conn()
    c = await conn.execute(
        "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
    )
    row = await c.fetchone()
    if row:
        await conn.execute(
            "UPDATE users SET username = ?, first_name = ?, "
            "last_seen = ?, search_count = search_count + ?, "
            "loc_count = loc_count + ?, lat = COALESCE(?, lat), "
            "lon = COALESCE(?, lon) WHERE user_id = ?",
            (username, first_name, now, search, loc, lat, lon, user_id),
        )
    else:
        await conn.execute(
            "INSERT INTO users (user_id, username, first_name, "
            "first_seen, last_seen, search_count, loc_count, "
            "source, lat, lon) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, first_name, now, now,
             search, loc, source, lat, lon),
        )
    await conn.commit()
    await conn.close()


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


# ========== USER COMMANDS ==========
@dp.message(Command("start"))
async def start_cmd(message: Message):
    user = message.from_user
    await update_user(user.id, user.username or "", user.first_name or "")
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
    text = (
        "📌 *Как пользоваться:*\n\n"
        "1️⃣ Отправь геолокацию, чтобы искать рядом\n"
        "2️⃣ Нажми *Найти ресторан* и введи название\n"
        "3️⃣ Нажми *Меню ресторана* и введи название\n"
        "4️⃣ Сохраняй рестораны в избранное ⭐\n"
        "5️⃣ Оставляй отзывы 📝\n\n"
        "Маршрут строится через Яндекс.Карты."
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=main_kb())


@dp.message(F.text == "🔍 Найти ресторан")
async def prompt_find(message: Message):
    await message.answer("Введи название ресторана или тип кухни:")


@dp.message(F.text == "🍽️ Меню ресторана")
async def prompt_menu(message: Message):
    await message.answer("Введи название ресторана:")


@dp.message(F.text == "⭐ Избранное")
async def show_favorites(message: Message):
    uid = message.from_user.id
    conn = await get_conn()
    rows = await conn.execute_fetchall(
        "SELECT r.id, r.name, r.cuisine, r.address, r.lat, r.lon "
        "FROM restaurants r JOIN favorites f ON r.id = f.restaurant_id "
        "WHERE f.user_id = ?",
        (uid,),
    )
    await conn.close()
    if not rows:
        return await message.answer(
            "⭐ У тебя пока нет избранных ресторанов."
        )
    text = "⭐ *Твоё избранное:*\n\n"
    for r in rows:
        rid, name, cuisine, address, lat, lon = r
        text += f"🍴 *{name}* ({cuisine or 'нет данных'})\n📍 {address}\n"
        conn2 = await get_conn()
        c = await conn2.execute(
            "SELECT lat, lon FROM users WHERE user_id = ?", (uid,)
        )
        uloc = await c.fetchone()
        await conn2.close()
        if uloc and uloc[0]:
            url = (
                f"https://yandex.by/maps/"
                f"?rtext={uloc[0]},{uloc[1]}~{lat},{lon}&rtt=auto"
            )
            text += f"🗺️ [Маршрут]({url})\n"
        else:
            url = f"https://yandex.by/maps/?mode=search&text={lat},{lon}"
            text += f"🗺️ [На карте]({url})\n"
        text += "\n"
    await message.answer(
        text, parse_mode="Markdown", disable_web_page_preview=True
    )


@dp.message(F.text == "📝 Оставить отзыв")
async def prompt_review(message: Message):
    await message.answer("Введи название ресторана:")


@dp.message(F.location)
async def handle_location(message: Message):
    uid = message.from_user.id
    lat = message.location.latitude
    lon = message.location.longitude
    await update_user(
        uid,
        message.from_user.username or "",
        message.from_user.first_name or "",
        0,
        1,
        lat=lat,
        lon=lon,
    )
    conn = await get_conn()
    rows = await conn.execute_fetchall(
        "SELECT id, name, cuisine, address, lat, lon FROM restaurants"
    )
    await conn.close()
    if not rows:
        return await message.answer("❌ В базе пока нет ресторанов.")
    nearby = []
    for r in rows:
        rid, name, cuisine, address, rlat, rlon = r
        d = haversine(lat, lon, rlat, rlon)
        nearby.append((d, rid, name, cuisine, address, rlat, rlon))
    nearby.sort(key=lambda x: x[0])
    text = "📍 *5 ближайших ресторанов:*\n\n"
    for d, rid, name, cuisine, address, rlat, rlon in nearby[:5]:
        text += f"🍴 *{name}* ({cuisine or 'нет данных'})\n"
        text += f"📍 {address} ({d:.1f} км)\n"
        url = (
            f"https://yandex.by/maps/"
            f"?rtext={lat},{lon}~{rlat},{rlon}&rtt=auto"
        )
        text += f"🗺️ [Маршрут]({url})\n\n"
    await message.answer(
        text, parse_mode="Markdown", disable_web_page_preview=True
    )


# ========== TEXT HANDLER ==========
@dp.message()
async def text_handler(message: Message):
    if not message.text:
        return
    uid = message.from_user.id
    text = message.text.strip()

    # Admin buttons
    if uid == ADMIN_ID:
        if text == "➕ Добавить ресторан":
            return await message.answer(
                "Формат:\n"
                "`/add_restaurant Название|Кухня|Адрес|lat|lon|"
                "Телефон|Часы|Средний чек`",
                parse_mode="Markdown",
            )
        if text == "🍕 Добавить блюдо":
            return await message.answer(
                "Формат:\n"
                "`/add_dish Ресторан|Категория|Блюдо|Описание|Цена`",
                parse_mode="Markdown",
            )
        if text == "📤 Импорт CSV":
            return await message.answer("Отправь CSV-файл как документ.")
        if text == "📥 Экспорт CSV":
            return await export_csv(message)
        if text == "💰 Массовое изменение цен":
            return await message.answer(
                "Формат:\n`/bulk_price Ресторан|Категория|+10%`",
                parse_mode="Markdown",
            )
        if text == "📊 Статистика":
            return await stats_cmd(message)
        if text == "🔙 Назад":
            return await message.answer(
                "Главное меню", reply_markup=main_kb()
            )

    # Search restaurant
    conn = await get_conn()
    rows = await conn.execute_fetchall(
        "SELECT id, name, cuisine, address, lat, lon, phone, "
        "work_hours, avg_check FROM restaurants "
        "WHERE LOWER(name) LIKE ? OR LOWER(cuisine) LIKE ? "
        "OR LOWER(address) LIKE ? LIMIT 10",
        (f"%{text.lower()}%", f"%{text.lower()}%", f"%{text.lower()}%"),
    )
    if rows:
        await conn.close()
        await update_user(
            uid,
            message.from_user.username or "",
            message.from_user.first_name or "",
            1,
            0,
        )
        for r in rows:
            rid, name, cuisine, address, lat, lon, phone, wh, avg = r
            answer = f"🍴 *{name}*\n"
            if cuisine:
                answer += f"🍳 Кухня: {cuisine}\n"
            answer += f"📍 {address}\n"
            if phone:
                answer += f"📞 {phone}\n"
            if wh:
                answer += f"🕐 {wh}\n"
            if avg:
                answer += f"💰 Средний чек: {avg} BYN\n"
            conn2 = await get_conn()
            c = await conn2.execute(
                "SELECT lat, lon FROM users WHERE user_id = ?", (uid,)
            )
            uloc = await c.fetchone()
            await conn2.close()
            if uloc and uloc[0]:
                url = (
                    f"https://yandex.by/maps/"
                    f"?rtext={uloc[0]},{uloc[1]}~{lat},{lon}&rtt=auto"
                )
                answer += f"🗺️ [Маршрут]({url})\n"
            else:
                url = (
                    f"https://yandex.by/maps/"
                    f"?mode=search&text={lat},{lon}"
                )
                answer += f"🗺️ [На карте]({url})\n"
            answer += "\n"
            await message.answer(
                answer,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        return

    # Menu by restaurant name
    rows = await conn.execute_fetchall(
        "SELECT id, name FROM restaurants "
        "WHERE LOWER(name) LIKE ? LIMIT 5",
        (f"%{text.lower()}%",),
    )
    if rows:
        await conn.close()
        for r in rows:
            rid, rname = r
            conn2 = await get_conn()
            dishes = await conn2.execute_fetchall(
                "SELECT category, dish, description, price FROM menu "
                "WHERE restaurant_id = ? ORDER BY category, price",
                (rid,),
            )
            await conn2.close()
            if not dishes:
                await message.answer(
                    f"📋 В ресторане *{rname}* пока нет блюд.",
                    parse_mode="Markdown",
                )
                continue
            answer = f"📋 *Меню: {rname}*\n\n"
            current_cat = ""
            for d in dishes:
                cat, dish, desc, price = d
                if cat != current_cat:
                    answer += f"\n*{cat}*\n"
                    current_cat = cat
                answer += f"• {dish} — {price} BYN"
                if desc:
                    answer += f" ({desc})"
                answer += "\n"
            await message.answer(answer, parse_mode="Markdown")
        return

    # Review
    rows = await conn.execute_fetchall(
        "SELECT id, name FROM restaurants "
        "WHERE LOWER(name) LIKE ? LIMIT 1",
        (f"%{text.lower()}%",),
    )
    if rows:
        rid, rname = rows[0]
        await conn.close()
        await message.answer(
            f"Оцени ресторан *{rname}* (1-5):",
            parse_mode="Markdown",
        )
        pending_reviews[uid] = {
            "restaurant_id": rid,
            "step": "rating",
        }
        return

    await conn.close()
    await message.answer(f"❌ По запросу «{text}» ничего не найдено.")


# ========== REVIEW HANDLER ==========
@dp.message()
async def review_handler(message: Message):
    uid = message.from_user.id
    if uid not in pending_reviews:
        return
    info = pending_reviews[uid]
    if info.get("step") == "rating":
        try:
            rating = int(message.text.strip())
            if rating < 1 or rating > 5:
                raise ValueError
            pending_reviews[uid]["rating"] = rating
            pending_reviews[uid]["step"] = "text"
            await message.answer(
                "Напиши текст отзыва (или отправь '-' чтобы пропустить):"
            )
        except ValueError:
            await message.answer("Введи число от 1 до 5:")
        return
    if info.get("step") == "text":
        text = message.text.strip()
        if text == "-":
            text = ""
        conn = await get_conn()
        await conn.execute(
            "INSERT INTO reviews (user_id, restaurant_id, rating, "
            "text, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                uid,
                info["restaurant_id"],
                info["rating"],
                text,
                datetime.datetime.now().isoformat(),
            ),
        )
        await conn.commit()
        await conn.close()
        del pending_reviews[uid]
        await message.answer("✅ Спасибо за отзыв!")


# ========== ADMIN COMMANDS ==========
@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🔧 Админ-панель", reply_markup=admin_kb())


@dp.message(Command("add_restaurant"))
async def add_restaurant_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.split(maxsplit=1)[1].split("|")
        if len(parts) < 5:
            raise ValueError
        name = parts[0].strip()
        cuisine = parts[1].strip()
        address = parts[2].strip()
        lat = float(parts[3].strip())
        lon = float(parts[4].strip())
        phone = parts[5].strip() if len(parts) > 5 else ""
        wh = parts[6].strip() if len(parts) > 6 else ""
        avg = float(parts[7]) if len(parts) > 7 else 0
        conn = await get_conn()
        c = await conn.execute(
            "INSERT INTO restaurants (name, cuisine, address, lat, "
            "lon, phone, work_hours, avg_check) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, cuisine, address, lat, lon, phone, wh, avg),
        )
        await conn.commit()
        rid = c.lastrowid
        await conn.close()
        await message.answer(
            f"✅ Ресторан добавлен: *{name}* (id={rid})",
            parse_mode="Markdown",
        )
    except Exception as e:
        logging.error(f"/add_restaurant error: {e}")
        await message.answer(
            "❌ Формат:\n"
            "`/add_restaurant Название|Кухня|Адрес|lat|lon|"
            "Телефон|Часы|Средний чек`",
            parse_mode="Markdown",
        )


@dp.message(Command("add_dish"))
async def add_dish_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.split(maxsplit=1)[1].split("|")
        if len(parts) != 5:
            raise ValueError
        rname = parts[0].strip()
        category = parts[1].strip()
        dish = parts[2].strip()
        desc = parts[3].strip()
        price = float(parts[4].strip())
        conn = await get_conn()
        rows = await conn.execute_fetchall(
            "SELECT id FROM restaurants WHERE LOWER(name) = ?",
            (rname.lower(),),
        )
        if not rows:
            await conn.close()
            return await message.answer(
                f"❌ Ресторан «{rname}» не найден."
            )
        rid = rows[0][0]
        await conn.execute(
            "INSERT INTO menu (restaurant_id, category, dish, "
            "description, price) VALUES (?, ?, ?, ?, ?)",
            (rid, category, dish, desc, price),
        )
        await conn.commit()
        await conn.close()
        await message.answer(
            f"✅ Добавлено: {dish} в {rname} — {price} BYN"
        )
    except Exception as e:
        logging.error(f"/add_dish error: {e}")
        await message.answer(
            "❌ Формат:\n"
            "`/add_dish Ресторан|Категория|Блюдо|Описание|Цена`",
            parse_mode="Markdown",
        )


@dp.message(Command("edit"))
async def edit_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.split(maxsplit=3)
        if len(parts) != 4:
            raise ValueError
        table = parts[1]
        row_id = int(parts[2])
        rest = parts[3]
        field, value = rest.split(" ", 1)
        allowed = {
            "restaurant": {
                "name", "cuisine", "address", "phone",
                "work_hours", "avg_check",
            },
            "menu": {"category", "dish", "description", "price"},
        }
        if table not in allowed or field not in allowed[table]:
            return await message.answer(
                f"❌ Поля: ресторан {allowed['restaurant']}, "
                f"меню {allowed['menu']}"
            )
        if field in ("avg_check", "price"):
            value = float(value)
        conn = await get_conn()
        await conn.execute(
            f"UPDATE {table} SET {field} = ? WHERE id = ?",
            (value, row_id),
        )
        await conn.commit()
        await conn.close()
        await message.answer(
            f"✅ Обновлено: {table}.{field} = {value}"
        )
    except Exception as e:
        logging.error(f"/edit error: {e}")
        await message.answer(
            "❌ Формат:\n"
            "`/edit restaurant 5 name Новое название`\n"
            "`/edit menu 12 price 18.50`",
            parse_mode="Markdown",
        )


@dp.message(Command("edit_price"))
async def edit_price_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.split()
        if len(parts) != 3:
            raise ValueError
        row_id = int(parts[1])
        new_price = float(parts[2])
        conn = await get_conn()
        c = await conn.execute(
            "SELECT dish, restaurant_id FROM menu WHERE id = ?",
            (row_id,),
        )
        row = await c.fetchone()
        if not row:
            await conn.close()
            return await message.answer(
                f"❌ Блюдо с id={row_id} не найдено."
            )
        dish, rid = row
        rname = (
            await conn.execute_fetchall(
                "SELECT name FROM restaurants WHERE id = ?", (rid,)
            )
        )[0][0]
        await conn.execute(
            "UPDATE menu SET price = ? WHERE id = ?",
            (new_price, row_id),
        )
        await conn.commit()
        await conn.close()
        await message.answer(
            f"💰 Цена обновлена:\n{rname} — {dish}\n"
            f"Новая цена: {new_price} BYN"
        )
    except Exception:
        await message.answer(
            "Использование:\n`/edit_price id новая_цена`",
            parse_mode="Markdown",
        )


@dp.message(Command("delete"))
async def delete_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.split()
        if len(parts) != 3:
            raise ValueError
        table = parts[1]
        row_id = int(parts[2])
        if table not in ("restaurant", "menu"):
            raise ValueError
        conn = await get_conn()
        if table == "restaurant":
            await conn.execute(
                "DELETE FROM restaurants WHERE id = ?", (row_id,)
            )
        else:
            await conn.execute(
                "DELETE FROM menu WHERE id = ?", (row_id,)
            )
        await conn.commit()
        await conn.close()
        await message.answer(f"🗑️ Удалено из {table}: id={row_id}")
    except Exception:
        await message.answer(
            "Использование:\n"
            "`/delete restaurant 5`\n"
            "`/delete menu 12`",
            parse_mode="Markdown",
        )


@dp.message(Command("bulk_price"))
async def bulk_price_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.split(maxsplit=1)[1].split("|")
        if len(parts) != 3:
            raise ValueError
        rname = parts[0].strip()
        category = parts[1].strip()
        action = parts[2].strip()
        conn = await get_conn()
        query = "SELECT id, price FROM menu WHERE 1=1"
        params = []
        if rname.lower() != "все":
            rows = await conn.execute_fetchall(
                "SELECT id FROM restaurants WHERE LOWER(name) = ?",
                (rname.lower(),),
            )
            if not rows:
                await conn.close()
                return await message.answer(
                    f"❌ Ресторан «{rname}» не найден."
                )
            query += " AND restaurant_id = ?"
            params.append(rows[0][0])
        if category.lower() != "все":
            query += " AND LOWER(category) = ?"
            params.append(category.lower())
        rows = await conn.execute_fetchall(query, tuple(params))
        if not rows:
            await conn.close()
            return await message.answer("❌ Блюда не найдены.")
        updated = 0
        for mid, price in rows:
            if action.startswith("+"):
                pct = float(action[1:-1]) / 100
                new_price = round(price * (1 + pct), 2)
            elif action.startswith("-"):
                pct = float(action[1:-1]) / 100
                new_price = round(price * (1 - pct), 2)
            elif action.startswith("="):
                new_price = float(action[1:])
            else:
                raise ValueError
            await conn.execute(
                "UPDATE menu SET price = ? WHERE id = ?",
                (new_price, mid),
            )
            updated += 1
        await conn.commit()
        await conn.close()
        await message.answer(
            f"✅ Обновлено {updated} блюд.\n"
            f"Фильтр: {rname} / {category}\n"
            f"Действие: {action}"
        )
    except Exception as e:
        logging.error(f"/bulk_price error: {e}")
        await message.answer(
            "❌ Формат:\n"
            "`/bulk_price Ресторан|Категория|+10%`\n"
            "Примеры:\n"
            "`/bulk_price Васильки|все|+10%`\n"
            "`/bulk_price все|Пицца|-5%`\n"
            "`/bulk_price все|все|=15.00`",
            parse_mode="Markdown",
        )


async def export_csv(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = await get_conn()
    rests = await conn.execute_fetchall(
        "SELECT id, name, cuisine, address, lat, lon, phone, "
        "work_hours, avg_check FROM restaurants"
    )
    dishes = await conn.execute_fetchall(
        "SELECT m.id, r.name, m.category, m.dish, m.description, "
        "m.price FROM menu m JOIN restaurants r "
        "ON m.restaurant_id = r.id"
    )
    await conn.close()
    out1 = io.StringIO()
    w1 = csv.writer(out1)
    w1.writerow([
        "id", "name", "cuisine", "address", "lat", "lon",
        "phone", "work_hours", "avg_check",
    ])
    for r in rests:
        w1.writerow(r)
    out1.seek(0)
    out2 = io.StringIO()
    w2 = csv.writer(out2)
    w2.writerow([
        "id", "restaurant", "category", "dish", "description", "price",
    ])
    for d in dishes:
        w2.writerow(d)
    out2.seek(0)
    await message.answer_document(
        BufferedInputFile(out1.getvalue().encode("utf-8"), "restaurants.csv")
    )
    await message.answer_document(
        BufferedInputFile(out2.getvalue().encode("utf-8"), "menu.csv")
    )


@dp.message(Command("export_csv"))
async def export_csv_cmd(message: Message):
    await export_csv(message)


@dp.message(Command("import_csv"))
async def import_csv_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Отправь CSV-файл как документ.")


@dp.message(F.document)
async def handle_document(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    doc = message.document
    if not doc.file_name.endswith(".csv"):
        return await message.answer("❌ Отправь файл в формате CSV.")
    try:
        file = await bot.download(doc)
        content = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        headers = reader.fieldnames or []
        conn = await get_conn()
        added = 0
        if "restaurant" in headers or "name" in headers:
            for row in reader:
                name = row.get("name") or row.get("restaurant")
                if not name:
                    continue
                await conn.execute(
                    "INSERT INTO restaurants (name, cuisine, address, "
                    "lat, lon, phone, work_hours, avg_check) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        name,
                        row.get("cuisine", ""),
                        row.get("address", ""),
                        float(row.get("lat", 0)),
                        float(row.get("lon", 0)),
                        row.get("phone", ""),
                        row.get("work_hours", ""),
                        float(row.get("avg_check", 0) or 0),
                    ),
                )
                added += 1
        elif "dish" in headers:
            for row in reader:
                rname = row.get("restaurant") or row.get("restaurant_name")
                if not rname:
                    continue
                rows = await conn.execute_fetchall(
                    "SELECT id FROM restaurants WHERE LOWER(name) = ?",
                    (rname.lower(),),
                )
                if not rows:
                    continue
                rid = rows[0][0]
                await conn.execute(
                    "INSERT INTO menu (restaurant_id, category, dish, "
                    "description, price) VALUES (?, ?, ?, ?, ?)",
                    (
                        rid,
                        row.get("category", "Разное"),
                        row["dish"],
                        row.get("description", ""),
                        float(row["price"]),
                    ),
                )
                added += 1
        await conn.commit()
        await conn.close()
        await message.answer(f"✅ Импортировано записей: {added}")
    except Exception as e:
        logging.exception("import error")
        await message.answer(f"❌ Ошибка импорта: {e}")


@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = await get_conn()
    c = await conn.execute("SELECT COUNT(*) FROM users")
    total = (await c.fetchone())[0]
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    c = await conn.execute(
        "SELECT COUNT(*) FROM users WHERE last_seen >= ?", (today,)
    )
    dau = (await c.fetchone())[0]
    c = await conn.execute(
        "SELECT SUM(search_count), SUM(loc_count) FROM users"
    )
    searches, locs = await c.fetchone()
    c = await conn.execute("SELECT COUNT(*) FROM restaurants")
    rests = (await c.fetchone())[0]
    c = await conn.execute("SELECT COUNT(*) FROM menu")
    dishes = (await c.fetchone())[0]
    c = await conn.execute("SELECT COUNT(*) FROM reviews")
    reviews = (await c.fetchone())[0]
    await conn.close()
    text = (
        f"📊 <b>Статистика:</b>\n"
        f"👥 Пользователей: {total} (DAU: {dau})\n"
        f"🔍 Поисков: {searches or 0} | "
        f"📍 Геолокаций: {locs or 0}\n"
        f"🍴 Ресторанов: {rests} | 🍽️ Блюд: {dishes}\n"
        f"📝 Отзывов: {reviews}"
    )
    await message.answer(text, parse_mode="HTML")


# ========== STARTUP ==========
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
    if WEBHOOK_URL:
        app = web.Application()
        app.router.add_get("/health", lambda r: web.Response(text="OK"))
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(
            app, path="/webhook"
        )
        setup_application(app, dp, bot=bot)
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
