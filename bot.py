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
DB_PATH = os.environ.get("DB_PATH", "/data/places.db")
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

# --------------------- Состояния FSM ---------------------
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
    """Возвращает список (id, name, address, distance) с сортировкой по расстоянию."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if category and category != 'all':
        cur.execute("SELECT id FROM categories WHERE name=?", (category,))
        cat_row = cur.fetchone()
        if not cat_row:
            conn.close()
            return []
        cat_id = cat_row[0]
        cur.execute("SELECT id, name, address, latitude, longitude FROM venues WHERE category_id=?", (cat_id,))
    else:
        cur.execute("SELECT id, name, address, latitude, longitude FROM venues")
    venues = cur.fetchall()
    conn.close()

    nearby = []
    for vid, vname, addr, lat, lon in venues:
        dist = haversine(user_lat, user_lon, lat, lon)
        nearby.append((vid, vname, addr if addr else "", dist))
    nearby.sort(key=lambda x: x[3])  # сортировка по расстоянию (индекс 3)
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

# --------------------- Бот и роутер ---------------------
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# --------------------- Фильтр администратора ---------------------
def is_admin(msg: Message) -> bool:
    return msg.from_user.id in ADMIN_IDS

# ===================== ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ =====================
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
    await msg.answer(
        "🍽️ <b>Гид по заведениям</b>\n\n"
        "🔍 Найдите ближайшие кафе, рестораны и бары по геолокации.\n"
        "📋 Просматривайте меню с ценами.\n"
        "🗺 Прокладывайте маршрут в Яндекс.Картах.\n\n"
        "📌 Для администраторов: команда /admin",
        parse_mode="HTML"
    )

@router.message(F.text == "⚙️ Настройки")
async def settings(msg: Message):
    await msg.answer("🔧 Здесь будут настройки (язык, радиус поиска). Пока в разработке.")

@router.message(F.text == "📍 Мои маршруты")
async def my_routes(msg: Message):
    await msg.answer("📌 Здесь будут сохраняться последние построенные маршруты. Функция в разработке.")

# ===================== СЦЕНАРИЙ "НАЙТИ РЯДОМ" =====================
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
    for v_id, v_name, v_addr, dist in venues:
        emoji = "🚶" if dist < 0.5 else "🚗"
        # Формируем короткую подпись: название + улица (если есть)
        if v_addr:
            short_addr = v_addr.split(",")[0].strip()
            if len(short_addr) > 25:
                short_addr = short_addr[:22] + "..."
            label = f"{emoji} {v_name} ({short_addr}) — {dist:.2f} км"
        else:
            label = f"{emoji} {v_name} — {dist:.2f} км"
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"venue:{v_id}:{user_lat}:{user_lon}"
            )
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="🔙 Выбрать другую категорию",
            callback_data=f"cat_reselect:{user_lat}:{user_lon}"
        )
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

    text = (
        f"<b>{venue['name']}</b>\n"
        f"📍 {venue['address']}\n"
        f"📝 {venue['desc']}\n\n"
    )
    if venue['menu']:
        text += "🍴 <b>Меню (первые 5 позиций):</b>\n"
        for item, price in venue['menu']:
            text += f"• {item} — {price} ₽\n"
    else:
        text += "🍽️ Меню пока не загружено."

    route_url = f"https://yandex.ru/maps/?rtext={user_lat},{user_lon}~{venue['lat']},{venue['lon']}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗺 Проложить маршрут", url=route_url)],
        [
            InlineKeyboardButton(
                text="📋 Полное меню",
                callback_data=f"full_menu:{venue_id}:{user_lat}:{user_lon}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔙 Назад к списку",
                callback_data=f"back_to_list:{user_lat}:{user_lon}"
            )
        ],
    ])

    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
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
    cur.execute("SELECT name, address FROM venues WHERE id=?", (venue_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        venue_name, venue_addr = row
        full_name = f"{venue_name}, {venue_addr}" if venue_addr else venue_name
    else:
        full_name = "Заведение"

    if not menu:
        await call.answer("Меню отсутствует.")
        return

    text = f"<b>📋 Полное меню — {full_name}</b>\n"
    for cat, items in menu.items():
        text += f"\n<i>{cat}</i>\n"
        for item, price in items:
            text += f"  • {item} — {price} ₽\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔙 Назад к карточке",
            callback_data=f"venue:{venue_id}:{user_lat}:{user_lon}"
        )]
    ])

    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("back_to_list:"))
async def back_to_list(call: CallbackQuery):
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

# ===================== ПРОСМОТР МЕНЮ (БЕЗ ГЕОЛОКАЦИИ) =====================
@router.message(F.text == "📋 Меню заведений")
async def menu_list(msg: Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name, address FROM venues ORDER BY name")
    venues = cur.fetchall()
    conn.close()
    if not venues:
        await msg.answer("В базе пока нет заведений.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for vid, name, address in venues:
        if address:
            short_addr = address.split(",")[0].strip()
            if len(short_addr) > 25:
                short_addr = short_addr[:22] + "..."
            label = f"{name} ({short_addr})"
        else:
            label = name
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=label, callback_data=f"full_menu_only:{vid}")
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
    cur.execute("SELECT name, address FROM venues WHERE id=?", (venue_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        name, address = row
        full_name = f"{name}, {address}" if address else name
    else:
        full_name = "Заведение"

    text = f"<b>📋 Меню — {full_name}</b>\n"
    for cat, items in menu.items():
        text += f"\n<i>{cat}</i>\n"
        for item, price in items:
            text += f"  • {item} — {price} ₽\n"

    await call.message.edit_text(text, parse_mode="HTML")
    await call.answer()

# ===================== АДМИН-ПАНЕЛЬ =====================
@router.message(Command("admin"))
async def admin_cmd(msg: Message):
    if not is_admin(msg):
        await msg.answer("⛔ Доступ запрещён.")
        return
    await msg.answer("🔧 <b>Админ-панель</b>", reply_markup=admin_menu(), parse_mode="HTML")

@router.message(F.text == "🔙 Главное меню", is_admin)
async def admin_back(msg: Message):
    await msg.answer("Возврат в главное меню.", reply_markup=main_menu())

# Синхронизация
@router.message(F.text == "🔄 Синхронизировать из Google Таблиц")
async def sync_button(msg: Message):
    if not is_admin(msg):
        return
    if not SHEET_URL_VENUES or not SHEET_URL_MENU:
        await msg.answer("❌ Не заданы ссылки на Google Таблицы.")
        return
    await msg.answer("🔄 Запускаю синхронизацию...")
    errors = []
    try:
        resp = requests.get(SHEET_URL_VENUES, timeout=15)
        resp.encoding = 'utf-8'
        text = resp.text
        if text.startswith('\ufeff'):
            text = text[1:]
        reader = csv.DictReader(io.StringIO(text))
        reader.fieldnames = [fn.strip().lower() for fn in reader.fieldnames]
        await process_venues_csv(msg, reader)
    except Exception as e:
        errors.append(f"Заведения: {e}")
    try:
        resp = requests.get(SHEET_URL_MENU, timeout=15)
        resp.encoding = 'utf-8'
        text = resp.text
        if text.startswith('\ufeff'):
            text = text[1:]
        reader = csv.DictReader(io.StringIO(text))
        reader.fieldnames = [fn.strip().lower() for fn in reader.fieldnames]
        await process_menu_csv(msg, reader)
    except Exception as e:
        errors.append(f"Меню: {e}")
    if errors:
        await msg.answer("⚠️ Ошибки:\n" + "\n".join(errors))
    else:
        await msg.answer("✅ Данные успешно обновлены!")

# Добавление заведения
@router.message(StateFilter(None), F.text == "➕ Добавить заведение", is_admin)
async def add_venue_start(msg: Message, state: FSMContext):
    await msg.answer("Введите <b>название</b> заведения:", parse_mode="HTML")
    await state.set_state(AddVenue.name)

@router.message(AddVenue.name)
async def add_venue_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await msg.answer(
        "Введите <b>категорию</b> (например, Кофейня, Ресторан, Бар):",
        parse_mode="HTML"
    )
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
        await msg.answer("❌ Введите число")
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
    await msg.answer(
        "Введите <b>описание</b> (можно отправить прочерк '-'):",
        parse_mode="HTML"
    )
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
        cur.execute(
            """
            INSERT INTO venues (name, category_id, address, latitude, longitude, description)
            VALUES (?,?,?,?,?,?)
            """,
            (
                data['name'], cat_id, data['address'],
                float(data['latitude']), float(data['longitude']),
                msg.text if msg.text != "-" else ""
            )
        )
        conn.commit()
        conn.close()
        await msg.answer(
            f"✅ Заведение «{data['name']}» добавлено!",
            reply_markup=admin_menu()
        )
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
    await state.clear()

# Добавление позиции меню
@router.message(StateFilter(None), F.text == "🍽️ Добавить позицию меню", is_admin)
async def add_item_start(msg: Message, state: FSMContext):
    await msg.answer(
        "Введите <b>название заведения</b>, к которому относится блюдо:",
        parse_mode="HTML"
    )
    await state.set_state(AddItem.venue_name)

@router.message(AddItem.venue_name)
async def add_item_venue(msg: Message, state: FSMContext):
    await state.update_data(venue_name=msg.text)
    await msg.answer("Введите <b>название блюда/напитка</b>:", parse_mode="HTML")
    await state.set_state(AddItem.item_name)

@router.message(AddItem.item_name)
async def add_item_name(msg: Message, state: FSMContext):
    await state.update_data(item_name=msg.text)
    await msg.answer(
        "Введите <b>цену</b> (только число, например 250):",
        parse_mode="HTML"
    )
    await state.set_state(AddItem.price)

@router.message(AddItem.price)
async def add_item_price(msg: Message, state: FSMContext):
    try:
        float(msg.text)
    except ValueError:
        await msg.answer("❌ Введите число")
        return
    await state.update_data(price=msg.text)
    await msg.answer(
        "Введите <b>категорию блюда</b> (Напитки, Десерты и т.п.) или отправьте прочерк:",
        parse_mode="HTML"
    )
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
            await msg.answer(
                f"❌ Заведение «{data['venue_name']}» не найдено. Сначала добавьте его."
            )
            await state.clear()
            return
        venue_id = v[0]
        cur.execute(
            """
            INSERT INTO menu_items (venue_id, name, price, category)
            VALUES (?,?,?,?)
            """,
            (venue_id, data['item_name'], float(data['price']), category)
        )
        conn.commit()
        conn.close()
        await msg.answer(
            f"🍽️ «{data['item_name']}» добавлено в меню «{data['venue_name']}».",
            reply_markup=admin_menu()
        )
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
    await state.clear()

# Статус базы
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
        await msg.answer(
            f"📊 Заведений: {venues_cnt}\n🍽️ Позиций меню: {items_cnt}"
        )
    except Exception as e:
        await msg.answer(f"❌ Ошибка доступа к базе: {e}")

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ CSV =====================
async def process_venues_csv(msg: Message, reader):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    count = 0
    errors = []
    for row_num, row in enumerate(reader, start=2):
        try:
            name = row['name']
            category = row['category']
            address = row.get('address', '')
            lat = float(row['latitude'])
            lon = float(row['longitude'])
            desc = row.get('description', '')

            cur.execute("SELECT id FROM categories WHERE name=?", (category,))
            cat = cur.fetchone()
            if not cat:
                cur.execute("INSERT INTO categories (name) VALUES (?)", (category,))
                cat_id = cur.lastrowid
            else:
                cat_id = cat[0]
            cur.execute(
                """
                INSERT INTO venues (name, category_id, address, latitude, longitude, description)
                VALUES (?,?,?,?,?,?)
                """,
                (name, cat_id, address, lat, lon, desc)
            )
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
            venue_name = row['venue_name']
            item_name = row['item_name']
            price = float(row['price'])
            cat = row.get('category', '')

            cur.execute("SELECT id FROM venues WHERE name=?", (venue_name,))
            v = cur.fetchone()
            if not v:
                skipped += 1
                continue
            venue_id = v[0]
            cur.execute(
                """
                INSERT INTO menu_items (venue_id, name, price, category)
                VALUES (?,?,?,?)
                """,
                (venue_id, item_name, price, cat)
            )
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

# ===================== ЗАПУСК =====================
async def main():
    init_db()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())