import os
import io
import csv
import sqlite3
import asyncio
import requests
from math import radians, cos, sin, asin, sqrt

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --------------------- Настройки ---------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = list(map(int, os.environ.get("ADMIN_IDS", "").split(","))) if os.environ.get("ADMIN_IDS") else []
DB_PATH = os.environ.get("DB_PATH", "/data/places.db")   # для Render

# Ссылки на опубликованные листы Google Sheets (CSV)
SHEET_URL_VENUES = os.environ.get("SHEET_URL_VENUES", "")
SHEET_URL_MENU   = os.environ.get("SHEET_URL_MENU", "")

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

# --------------------- Состояния для FSM ---------------------
class AddVenue(StatesGroup):
    name = State()
    category = State()
    address = State()
    latitude = State()
    longitude = State()
    description = State()

class AddItem(StatesGroup):
    venue_name = State()
    item_name = State()
    price = State()
    category = State()

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
    cur.execute("SELECT name, address, description, latitude, longitude FROM venues WHERE id=?", (venue_id,))
    venue = cur.fetchone()
    if not venue:
        conn.close()
        return None
    name, address, desc, lat, lon = venue
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
        "menu": menu
    }

def get_full_menu(venue_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name, price, category FROM menu_items WHERE venue_id=?", (venue_id,))
    items = cur.fetchall()
    conn.close()
    menu_by_cat = {}
    for name, price, cat in items:
        cat = cat or "Без категории"
        menu_by_cat.setdefault(cat, []).append((name, price))
    return menu_by_cat

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

def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔄 Синхронизировать из Google Таблиц")],
            [KeyboardButton(text="➕ Добавить заведение"), KeyboardButton(text="🍽️ Добавить позицию меню")],
            [KeyboardButton(text="📊 Статус базы"), KeyboardButton(text="🔙 Главное меню")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Админ-панель"
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

# --------------------- Инициализация бота ---------------------
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# --------------------- Админ-фильтр ---------------------
def is_admin(msg: Message) -> bool:
    return msg.from_user.id in ADMIN_IDS

# --------------------- Команды и кнопки пользователя ---------------------
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
        "🗺 Прокладывайте маршрут в Яндекс.Картах."
    )
    await msg.answer(text, parse_mode="HTML")

@router.message(F.text == "⚙️ Настройки")
async def settings(msg: Message):
    await msg.answer("🔧 Здесь будут настройки (язык, радиус поиска). Пока в разработке.")

@router.message(F.text == "📍 Мои маршруты")
async def my_routes(msg: Message):
    await msg.answer("📌 Здесь будут сохраняться последние построенные маршруты.")

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
    await msg.answer("Ожидаю выбор...", reply_markup=main_menu())

# ... (оставьте уже готовые обработчики cat, venue, full_menu, back_to_list, menu_list, full_menu_only – они не меняются)

# --------------------- АДМИН-ПАНЕЛЬ ---------------------
@router.message(Command("admin"))
async def admin_cmd(msg: Message):
    if not is_admin(msg):
        await msg.answer("⛔ Доступ запрещён.")
        return
    await msg.answer("🔧 <b>Админ-панель</b>", reply_markup=admin_menu(), parse_mode="HTML")

@router.message(F.text == "🔙 Главное меню", is_admin)
async def admin_back(msg: Message):
    await msg.answer("Возврат в главное меню.", reply_markup=main_menu())

# --- Синхронизация с Google Таблицами ---
@router.message(F.text == "🔄 Синхронизировать из Google Таблиц")
async def sync_button(msg: Message):
    if not is_admin(msg):
        return
    if not SHEET_URL_VENUES or not SHEET_URL_MENU:
        await msg.answer("❌ Не заданы ссылки на Google Таблицы. Добавьте переменные SHEET_URL_VENUES и SHEET_URL_MENU.")
        return
    await msg.answer("🔄 Запускаю синхронизацию...")
    errors = []
    try:
        resp = requests.get(SHEET_URL_VENUES, timeout=15)
        resp.encoding = 'utf-8'
        reader = csv.DictReader(io.StringIO(resp.text))
        await process_venues_csv(msg, reader)
    except Exception as e:
        errors.append(f"Заведения: {e}")
    try:
        resp = requests.get(SHEET_URL_MENU, timeout=15)
        resp.encoding = 'utf-8'
        reader = csv.DictReader(io.StringIO(resp.text))
        await process_menu_csv(msg, reader)
    except Exception as e:
        errors.append(f"Меню: {e}")
    if errors:
        await msg.answer("⚠️ Ошибки:\n" + "\n".join(errors))
    else:
        await msg.answer("✅ Данные успешно обновлены!")

# --- Добавление заведения (FSM) ---
@router.message(StateFilter(None), F.text == "➕ Добавить заведение", is_admin)
async def add_venue_start(msg: Message, state: FSMContext):
    await msg.answer("Введите <b>название</b> заведения:", parse_mode="HTML")
    await state.set_state(AddVenue.name)

@router.message(AddVenue.name)
async def add_venue_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await msg.answer("Введите <b>категорию</b> (например, Кофейня, Ресторан, Бар):", parse_mode="HTML")
    await state.set_state(AddVenue.category)

@router.message(AddVenue.category)
async def add_venue_category(msg: Message, state: FSMContext):
    await state.update_data(category=msg.text)
    await msg.answer("Введите <b>адрес</b> (улица, дом):", parse_mode="HTML")
    await state.set_state(AddVenue.address)

@router.message(AddVenue.address)
async def add_venue_address(msg: Message, state: FSMContext):
    await state.update_data(address=msg.text)
    await msg.answer("Введите <b>широту</b> (например, 55.7652):", parse_mode="HTML")
    await state.set_state(AddVenue.latitude)

@router.message(AddVenue.latitude)
async def add_venue_lat(msg: Message, state: FSMContext):
    try:
        float(msg.text)
    except ValueError:
        await msg.answer("❌ Введите число (например, 55.7652)")
        return
    await state.update_data(latitude=msg.text)
    await msg.answer("Введите <b>долготу</b> (например, 37.6050):", parse_mode="HTML")
    await state.set_state(AddVenue.longitude)

@router.message(AddVenue.longitude)
async def add_venue_lon(msg: Message, state: FSMContext):
    try:
        float(msg.text)
    except ValueError:
        await msg.answer("❌ Введите число")
        return
    await state.update_data(longitude=msg.text)
    await msg.answer("Введите <b>описание</b> (можно оставить пустым, отправьте любой текст или прочерк):", parse_mode="HTML")
    await state.set_state(AddVenue.description)

@router.message(AddVenue.description)
async def add_venue_desc(msg: Message, state: FSMContext):
    data = await state.get_data()
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cat_name = data['category'].strip()
        cur.execute("SELECT id FROM categories WHERE name=?", (cat_name,))
        cat = cur.fetchone()
        if not cat:
            cur.execute("INSERT INTO categories (name) VALUES (?)", (cat_name,))
            cat_id = cur.lastrowid
        else:
            cat_id = cat[0]
        cur.execute("""
            INSERT INTO venues (name, category_id, address, latitude, longitude, description)
            VALUES (?,?,?,?,?,?)
        """, (
            data['name'], cat_id, data['address'],
            float(data['latitude']), float(data['longitude']),
            msg.text if msg.text != "-" else ""
        ))
        conn.commit()
        conn.close()
        await msg.answer(f"✅ Заведение «{data['name']}» добавлено!", reply_markup=admin_menu())
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
    await state.clear()

# --- Добавление позиции меню (FSM) ---
@router.message(StateFilter(None), F.text == "🍽️ Добавить позицию меню", is_admin)
async def add_item_start(msg: Message, state: FSMContext):
    await msg.answer("Введите <b>название заведения</b>, к которому относится блюдо:", parse_mode="HTML")
    await state.set_state(AddItem.venue_name)

@router.message(AddItem.venue_name)
async def add_item_venue(msg: Message, state: FSMContext):
    await state.update_data(venue_name=msg.text)
    await msg.answer("Введите <b>название блюда/напитка</b>:", parse_mode="HTML")
    await state.set_state(AddItem.item_name)

@router.message(AddItem.item_name)
async def add_item_name(msg: Message, state: FSMContext):
    await state.update_data(item_name=msg.text)
    await msg.answer("Введите <b>цену</b> (только число, например 250):", parse_mode="HTML")
    await state.set_state(AddItem.price)

@router.message(AddItem.price)
async def add_item_price(msg: Message, state: FSMContext):
    try:
        float(msg.text)
    except ValueError:
        await msg.answer("❌ Введите число")
        return
    await state.update_data(price=msg.text)
    await msg.answer("Введите <b>категорию блюда</b> (например, Напитки, Десерты). Можете пропустить, отправив прочерк:", parse_mode="HTML")
    await state.set_state(AddItem.category)

@router.message(AddItem.category)
async def add_item_cat(msg: Message, state: FSMContext):
    data = await state.get_data()
    category = msg.text if msg.text != "-" else "Без категории"
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id FROM venues WHERE name=?", (data['venue_name'],))
        v = cur.fetchone()
        if not v:
            await msg.answer(f"❌ Заведение «{data['venue_name']}» не найдено. Сначала добавьте его.")
            await state.clear()
            return
        venue_id = v[0]
        cur.execute("""
            INSERT INTO menu_items (venue_id, name, price, category)
            VALUES (?,?,?,?)
        """, (venue_id, data['item_name'], float(data['price']), category))
        conn.commit()
        conn.close()
        await msg.answer(f"🍽️ «{data['item_name']}» добавлено в меню «{data['venue_name']}».", reply_markup=admin_menu())
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
    await state.clear()

# --- Статус базы ---
@router.message(F.text == "📊 Статус базы", is_admin)
async def status_cmd(msg: Message):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM venues")
        venues_cnt = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM menu_items")
        items_cnt = cur.fetchone()[0]
        conn.close()
        await msg.answer(f"📊 Заведений: {venues_cnt}\n🍽️ Позиций меню: {items_cnt}")
    except Exception as e:
        await msg.answer(f"❌ Ошибка доступа к базе: {e}")

# --- Обработчики CSV (остаются без изменений) ---
async def process_venues_csv(msg: Message, reader):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    count = 0
    errors = []
    for row_num, row in enumerate(reader, start=2):
        try:
            cat_name = row['category'].strip()
            cur.execute("SELECT id FROM categories WHERE name=?", (cat_name,))
            cat = cur.fetchone()
            if not cat:
                cur.execute("INSERT INTO categories (name) VALUES (?)", (cat_name,))
                cat_id = cur.lastrowid
            else:
                cat_id = cat[0]
            cur.execute("""
                INSERT INTO venues (name, category_id, address, latitude, longitude, description)
                VALUES (?,?,?,?,?,?)
            """, (
                row['name'], cat_id, row.get('address',''),
                float(row['latitude']), float(row['longitude']),
                row.get('description','')
            ))
            count += 1
        except Exception as e:
            errors.append(f"Строка {row_num}: {e}")
    conn.commit()
    conn.close()
    reply = f"✅ Добавлено заведений: {count}"
    if errors:
        reply += f"\n⚠️ Ошибки в {len(errors)} строках:\n" + "\n".join(errors[:5])
    await msg.answer(reply)

async def process_menu_csv(msg: Message, reader):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    count = 0
    skipped = 0
    errors = []
    for row_num, row in enumerate(reader, start=2):
        try:
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
        except Exception as e:
            errors.append(f"Строка {row_num}: {e}")
    conn.commit()
    conn.close()
    msg_text = f"🍽️ Добавлено позиций меню: {count}"
    if skipped:
        msg_text += f"\n⚠️ Пропущено (заведение не найдено): {skipped}"
    if errors:
        msg_text += f"\n🚫 Ошибки:\n" + "\n".join(errors[:5])
    await msg.answer(msg_text)

# --------------------- Запуск ---------------------
async def main():
    init_db()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())