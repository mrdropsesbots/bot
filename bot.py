import os
import io
import csv
import sqlite3
import asyncio
from math import radians, cos, sin, asin, sqrt

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command
from aiogram.types import FSInputFile  # если захотите отправлять локальные фото

# --------------------- Настройки ---------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = list(map(int, os.environ.get("ADMIN_IDS", "").split(","))) if os.environ.get("ADMIN_IDS") else []
DB_PATH = os.environ.get("DB_PATH", "places.db")  # на Render используйте /data/places.db

# --------------------- Инициализация БД ---------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS venues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category_id INTEGER REFERENCES categories(id),
            address TEXT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            photo TEXT,
            description TEXT,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        );
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            category TEXT,
            FOREIGN KEY (venue_id) REFERENCES venues(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()

# --------------------- Клавиатуры ---------------------
def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Найти заведения рядом")],
            [KeyboardButton(text="📋 Меню заведений"), KeyboardButton(text="📍 Мои маршруты")],
            [KeyboardButton(text="ℹ️ О боте"), KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )

def request_location_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
            [KeyboardButton(text="🔙 Главная")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# --------------------- Вспомогательные функции ---------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * asin(sqrt(a))

def get_nearby_venues(user_lat, user_lon, category=None, limit=5):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if category and category != 'all':
        cur.execute("SELECT id FROM categories WHERE name=?", (category,))
        cat_row = cur.fetchone()
        if not cat_row:
            conn.close()
            return []
        cat_id = cat_row[0]
        cur.execute("SELECT id, name, latitude, longitude FROM venues WHERE category_id=?", (cat_id,))
    else:
        cur.execute("SELECT id, name, latitude, longitude FROM venues")
    venues = cur.fetchall()
    conn.close()

    nearby = []
    for vid, vname, lat, lon in venues:
        dist = haversine(user_lat, user_lon, lat, lon)
        nearby.append((vid, vname, dist))
    nearby.sort(key=lambda x: x[2])
    return nearby[:limit]

def get_venue_details(venue_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name, address, description, latitude, longitude, photo FROM venues WHERE id=?", (venue_id,))
    venue = cur.fetchone()
    if not venue:
        conn.close()
        return None
    name, address, desc, lat, lon, photo = venue
    # Топ-5 позиций меню
    cur.execute("SELECT name, price FROM menu_items WHERE venue_id=? LIMIT 5", (venue_id,))
    menu = cur.fetchall()
    conn.close()
    return {
        "id": venue_id,
        "name": name,
        "address": address,
        "desc": desc,
        "lat": lat,
        "lon": lon,
        "photo": photo,
        "menu": menu
    }

def get_full_menu(venue_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name, price, category FROM menu_items WHERE venue_id=?", (venue_id,))
    items = cur.fetchall()
    conn.close()
    # Группируем по категориям
    menu_by_cat = {}
    for name, price, cat in items:
        cat = cat or "Без категории"
        menu_by_cat.setdefault(cat, []).append((name, price))
    return menu_by_cat

# --------------------- Бот и роутеры ---------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# --------------------- Админ-фильтр ---------------------
def is_admin(msg: Message) -> bool:
    return msg.from_user.id in ADMIN_IDS

# --------------------- Команды и кнопки ---------------------
@router.message(Command("start"))
async def start(msg: Message):
    await msg.answer(
        "👋 <b>Добро пожаловать!</b>\nЯ помогу найти лучшие рестораны, кофейни и бары рядом с вами.",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )

@router.message(F.text == "🔙 Главная")
@router.message(Command("menu"))
async def show_menu(msg: Message):
    await msg.answer("Главное меню:", reply_markup=main_menu())

@router.message(F.text == "ℹ️ О боте")
async def about(msg: Message):
    text = (
        "🍽️ <b>Гид по заведениям</b>\n\n"
        "🔍 Найдите ближайшие кафе, рестораны и бары по геолокации.\n"
        "📋 Просматривайте меню с ценами.\n"
        "🗺 Прокладывайте маршрут в Яндекс.Картах.\n\n"
        "📌 Администраторы могут загружать базу через CSV."
    )
    await msg.answer(text, parse_mode="HTML")

@router.message(F.text == "⚙️ Настройки")
async def settings(msg: Message):
    await msg.answer("🔧 Здесь будут настройки (язык, радиус поиска). Пока в разработке.")

@router.message(F.text == "📍 Мои маршруты")
async def my_routes(msg: Message):
    await msg.answer("📌 Здесь будут сохраняться последние построенные маршруты.")

# --------------------- Сценарий "Найти рядом" ---------------------
@router.message(F.text == "🔍 Найти заведения рядом")
async def find_nearby(msg: Message):
    await msg.answer(
        "📍 Чтобы показать ближайшие места, отправьте мне вашу геопозицию, нажав кнопку ниже.",
        reply_markup=request_location_kb()
    )

@router.message(F.location)
async def handle_location(msg: Message):
    user_lat = msg.location.latitude
    user_lon = msg.location.longitude
    await msg.answer(
        "🎯 <b>Выберите категорию заведений:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="☕️ Кофейни", callback_data=f"cat:Кофейня:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="🍽️ Рестораны", callback_data=f"cat:Ресторан:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="🍻 Бары", callback_data=f"cat:Бар:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="⭐ Показать всё", callback_data=f"cat:all:{user_lat}:{user_lon}")],
        ]),
        parse_mode="HTML"
    )
    # возвращаем главное меню
    await msg.answer("Ожидаю выбор...", reply_markup=main_menu())

@router.callback_query(F.data.startswith("cat:"))
async def list_venues(call: CallbackQuery):
    parts = call.data.split(":")
    category = parts[1]
    user_lat = float(parts[2])
    user_lon = float(parts[3])

    venues = get_nearby_venues(user_lat, user_lon, category)
    if not venues:
        await call.message.edit_text("😔 В этой категории пока ничего нет.", reply_markup=None)
        await call.answer()
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for v_id, v_name, dist in venues:
        emoji = "🚶" if dist < 0.5 else "🚗"
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{emoji} {v_name} — {dist:.2f} км",
                callback_data=f"venue:{v_id}:{user_lat}:{user_lon}"
            )
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Выбрать другую категорию", callback_data=f"cat_reselect:{user_lat}:{user_lon}")
    ])

    cat_display = category if category != 'all' else 'все'
    await call.message.edit_text(
        f"<b>Ближайшие заведения ({cat_display}):</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("cat_reselect:"))
async def reselect_category(call: CallbackQuery):
    parts = call.data.split(":")
    user_lat = float(parts[1])
    user_lon = float(parts[2])
    await call.message.edit_text(
        "🎯 <b>Выберите категорию заведений:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="☕️ Кофейни", callback_data=f"cat:Кофейня:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="🍽️ Рестораны", callback_data=f"cat:Ресторан:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="🍻 Бары", callback_data=f"cat:Бар:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="⭐ Показать всё", callback_data=f"cat:all:{user_lat}:{user_lon}")],
        ]),
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("venue:"))
async def show_venue(call: CallbackQuery):
    parts = call.data.split(":")
    venue_id = int(parts[1])
    user_lat = float(parts[2])
    user_lon = float(parts[3])

    venue = get_venue_details(venue_id)
    if not venue:
        await call.answer("Заведение не найдено.")
        return

    text = f"<b>{venue['name']}</b>\n📍 {venue['address']}\n📝 {venue['desc']}\n\n"
    if venue['menu']:
        text += "🍴 <b>Меню (первые 5 позиций):</b>\n"
        for item, price in venue['menu']:
            text += f"• {item} — {price} ₽\n"
    else:
        text += "🍽️ Меню пока не загружено."

    route_url = f"https://yandex.ru/maps/?rtext={user_lat},{user_lon}~{venue['lat']},{venue['lon']}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗺 Проложить маршрут", url=route_url)],
        [InlineKeyboardButton(text="📋 Полное меню", callback_data=f"full_menu:{venue_id}:{user_lat}:{user_lon}")],
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data=f"back_to_list:{user_lat}:{user_lon}")],
    ])

    if venue['photo']:
        try:
            await call.message.answer_photo(photo=venue['photo'], caption=text, reply_markup=kb, parse_mode="HTML")
        except:
            await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("full_menu:"))
async def show_full_menu(call: CallbackQuery):
    parts = call.data.split(":")
    venue_id = int(parts[1])
    user_lat = float(parts[2])
    user_lon = float(parts[3])

    menu = get_full_menu(venue_id)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM venues WHERE id=?", (venue_id,))
    vname = cur.fetchone()
    conn.close()
    venue_name = vname[0] if vname else "Заведение"

    if not menu:
        await call.answer("Меню отсутствует.")
        return

    text = f"<b>📋 Полное меню — {venue_name}</b>\n"
    for cat, items in menu.items():
        text += f"\n<i>{cat}</i>\n"
        for item, price in items:
            text += f"  • {item} — {price} ₽\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к карточке", callback_data=f"venue:{venue_id}:{user_lat}:{user_lon}")]
    ])

    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("back_to_list:"))
async def back_to_list(call: CallbackQuery):
    parts = call.data.split(":")
    user_lat = float(parts[1])
    user_lon = float(parts[2])
    # Отправляем заново выбор категории
    await call.message.edit_text(
        "🎯 <b>Выберите категорию заведений:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="☕️ Кофейни", callback_data=f"cat:Кофейня:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="🍽️ Рестораны", callback_data=f"cat:Ресторан:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="🍻 Бары", callback_data=f"cat:Бар:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="⭐ Показать всё", callback_data=f"cat:all:{user_lat}:{user_lon}")],
        ]),
        parse_mode="HTML"
    )
    await call.answer()

# --------------------- Кнопка "Меню заведений" (без гео) ---------------------
@router.message(F.text == "📋 Меню заведений")
async def menu_list(msg: Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM venues ORDER BY name")
    venues = cur.fetchall()
    conn.close()
    if not venues:
        await msg.answer("В базе пока нет заведений.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"full_menu_only:{vid}")] for vid, name in venues
    ])
    await msg.answer("Выберите заведение для просмотра меню:", reply_markup=kb)

@router.callback_query(F.data.startswith("full_menu_only:"))
async def full_menu_only(call: CallbackQuery):
    venue_id = int(call.data.split(":")[1])
    menu = get_full_menu(venue_id)
    if not menu:
        await call.answer("Меню отсутствует.")
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM venues WHERE id=?", (venue_id,))
    vname = cur.fetchone()
    conn.close()
    venue_name = vname[0] if vname else "Заведение"
    text = f"<b>📋 Меню — {venue_name}</b>\n"
    for cat, items in menu.items():
        text += f"\n<i>{cat}</i>\n"
        for item, price in items:
            text += f"  • {item} — {price} ₽\n"
    await call.message.edit_text(text, parse_mode="HTML")
    await call.answer()

# --------------------- Админка: загрузка CSV ---------------------
@router.message(Command("upload_venues"))
async def ask_venues_csv(msg: Message):
    if not is_admin(msg):
        await msg.answer("⛔ Доступ запрещён.")
        return
    await msg.answer("📎 Пришлите CSV-файл с заведениями (колонки: name,category,address,latitude,longitude,description,photo_url)")

@router.message(Command("upload_menu"))
async def ask_menu_csv(msg: Message):
    if not is_admin(msg):
        await msg.answer("⛔ Доступ запрещён.")
        return
    await msg.answer("📎 Пришлите CSV-файл с меню (колонки: venue_name,item_name,price,category)")

@router.message(F.document)
async def handle_csv_upload(msg: Message):
    if not is_admin(msg):
        return
    doc = msg.document
    if not doc.file_name.endswith('.csv'):
        await msg.answer("❌ Ожидается файл .csv")
        return

    file = await bot.get_file(doc.file_id)
    file_bytes = await bot.download_file(file.file_path)
    content = file_bytes.read().decode('utf-8')
    reader = csv.DictReader(io.StringIO(content))

    fieldnames = reader.fieldnames or []
    if 'latitude' in fieldnames:
        await process_venues_csv(msg, reader)
    elif 'price' in fieldnames and 'item_name' in fieldnames:
        await process_menu_csv(msg, reader)
    else:
        await msg.answer("⚠️ Неизвестный формат CSV. Ожидаются колонки для заведений или меню.")

async def process_venues_csv(msg: Message, reader):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    count = 0
    for row in reader:
        cat_name = row['category'].strip()
        cur.execute("SELECT id FROM categories WHERE name=?", (cat_name,))
        cat = cur.fetchone()
        if not cat:
            cur.execute("INSERT INTO categories (name) VALUES (?)", (cat_name,))
            cat_id = cur.lastrowid
        else:
            cat_id = cat[0]

        cur.execute("""
            INSERT INTO venues (name, category_id, address, latitude, longitude, description, photo)
            VALUES (?,?,?,?,?,?,?)
        """, (
            row['name'], cat_id, row.get('address',''),
            float(row['latitude']), float(row['longitude']),
            row.get('description',''), row.get('photo_url','')
        ))
        count += 1
    conn.commit()
    conn.close()
    await msg.answer(f"✅ Добавлено заведений: {count}")

async def process_menu_csv(msg: Message, reader):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    count = 0
    skipped = 0
    for row in reader:
        venue_name = row['venue_name'].strip()
        cur.execute("SELECT id FROM venues WHERE name=?", (venue_name,))
        v = cur.fetchone()
        if not v:
            skipped += 1
            continue
        venue_id = v[0]
        cur.execute("""
            INSERT INTO menu_items (venue_id, name, price, category)
            VALUES (?,?,?,?)
        """, (venue_id, row['item_name'], float(row['price']), row.get('category','')))
        count += 1
    conn.commit()
    conn.close()
    msg_text = f"🍽️ Добавлено позиций меню: {count}"
    if skipped:
        msg_text += f"\n⚠️ Пропущено (заведение не найдено): {skipped}"
    await msg.answer(msg_text)

# --------------------- Запуск ---------------------
async def main():
    init_db()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())