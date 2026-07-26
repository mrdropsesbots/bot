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
            phone TEXT DEFAULT '',
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
    phone = State()

class AddItem(StatesGroup):
    venue_name = State()
    item_name = State()
    price = State()
    category = State()

class ClearConfirm(StatesGroup):
    confirm = State()

class SearchState(StatesGroup):
    waiting_for_location = State()
    category_prefilter = State()

# --------------------- Хранилище последних сообщений (для очистки чата) ---------------------
last_msg = {}  # {chat_id: message_id}

async def safe_send(chat_id, text, reply_markup=None, parse_mode=None):
    """Отправляет сообщение и удаляет предыдущее в этом чате."""
    if chat_id in last_msg:
        try:
            await bot.delete_message(chat_id, last_msg[chat_id])
        except:
            pass
    if reply_markup:
        msg = await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        msg = await bot.send_message(chat_id, text, parse_mode=parse_mode)
    last_msg[chat_id] = msg.message_id
    return msg

async def safe_edit(call: CallbackQuery, text, reply_markup=None, parse_mode=None):
    """Редактирует сообщение и обновляет его ID."""
    await call.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    last_msg[call.message.chat.id] = call.message.message_id
    await call.answer()

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
        cur.execute("SELECT id, name, address, latitude, longitude FROM venues WHERE category_id=?", (cat_id,))
    else:
        cur.execute("SELECT id, name, address, latitude, longitude FROM venues")
    venues = cur.fetchall()
    conn.close()

    nearby = []
    for vid, vname, addr, lat, lon in venues:
        dist = haversine(user_lat, user_lon, lat, lon)
        nearby.append((vid, vname, addr if addr else "", dist))
    nearby.sort(key=lambda x: x[3])
    return nearby[:limit]

def get_venue_details(venue_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name, address, description, latitude, longitude, phone FROM venues WHERE id=?", (venue_id,))
    venue = cur.fetchone()
    if not venue:
        conn.close()
        return None
    name, address, desc, lat, lon, phone = venue
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
        "phone": phone,
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
            [KeyboardButton(text="☕️ Кофейни"), KeyboardButton(text="🍽️ Рестораны")],
            [KeyboardButton(text="🍻 Бары"), KeyboardButton(text="🔍 Все заведения рядом")],
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
            [KeyboardButton(text="📊 Статус базы"), KeyboardButton(text="🔙 Главное меню")],
            [KeyboardButton(text="🧹 Очистить базу данных")],
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
    await safe_send(
        msg.chat.id,
        "👋 <b>Добро пожаловать!</b>\nЯ помогу найти лучшие рестораны, кофейни и бары рядом с вами.",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )

@router.message(F.text == "🔙 Главная")
@router.message(Command("menu"))
async def show_menu(msg: Message):
    await safe_send(msg.chat.id, "Главное меню:", reply_markup=main_menu())

@router.message(F.text == "ℹ️ О боте")
async def about(msg: Message):
    text = (
        "🍽️ <b>Гид по заведениям</b>\n\n"
        "☕️🍽️🍻 Отдельные кнопки для кофеен, ресторанов и баров.\n"
        "🔍 Найдите ближайшие места по геолокации.\n"
        "📋 Просматривайте меню с ценами (в белорусских рублях).\n"
        "🗺 Прокладывайте маршрут и смотрите на карте.\n"
        "📞 Бронируйте столик — телефон заведения в один клик.\n\n"
        "📌 Администраторам: команда /admin"
    )
    await safe_send(msg.chat.id, text, parse_mode="HTML")

@router.message(F.text == "⚙️ Настройки")
async def settings(msg: Message):
    await safe_send(msg.chat.id, "🔧 Здесь будут настройки (язык, радиус поиска). Пока в разработке.")

@router.message(F.text == "📍 Мои маршруты")
async def my_routes(msg: Message):
    await safe_send(msg.chat.id, "📌 Здесь будут сохраняться последние построенные маршруты. Функция в разработке.")

# ===================== БЫСТРЫЙ ПОИСК ПО КАТЕГОРИЯМ (отдельные кнопки) =====================
@router.message(F.text.in_({"☕️ Кофейни", "🍽️ Рестораны", "🍻 Бары", "🔍 Все заведения рядом"}))
async def quick_category(msg: Message, state: FSMContext):
    cat_map = {
        "☕️ Кофейни": "Кофейня",
        "🍽️ Рестораны": "Ресторан",
        "🍻 Бары": "Бар",
        "🔍 Все заведения рядом": "all"
    }
    category = cat_map[msg.text]
    await state.update_data(prefilter=category)
    await safe_send(
        msg.chat.id,
        "📍 Отправьте вашу геопозицию, чтобы увидеть ближайшие заведения.",
        reply_markup=request_location_kb()
    )
    await state.set_state(SearchState.waiting_for_location)

@router.message(SearchState.waiting_for_location, F.location)
async def process_quick_location(msg: Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("prefilter", "all")
    user_lat = msg.location.latitude
    user_lon = msg.location.longitude

    venues = get_nearby_venues(user_lat, user_lon, category)
    if not venues:
        await safe_send(msg.chat.id, "😔 В этой категории пока ничего рядом нет.")
        await state.clear()
        return

    await show_venues_list(msg.chat.id, venues, user_lat, user_lon, category)
    await state.clear()

@router.message(SearchState.waiting_for_location)
async def location_not_received(msg: Message, state: FSMContext):
    await safe_send(msg.chat.id, "Пожалуйста, используйте кнопку «📍 Отправить геолокацию».")
    await state.set_state(SearchState.waiting_for_location)

# Универсальная функция показа списка заведений (используется и в быстром, и в обычном поиске)
async def show_venues_list(chat_id, venues, user_lat, user_lon, category_display):
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for v_id, v_name, v_addr, dist in venues:
        emoji = "🚶" if dist < 0.5 else "🚗"
        if v_addr:
            short_addr = v_addr.split(",")[0].strip()
            if len(short_addr) > 25:
                short_addr = short_addr[:22] + "..."
            label = f"{emoji} {v_name} ({short_addr}) — {dist:.2f} км"
        else:
            label = f"{emoji} {v_name} — {dist:.2f} км"
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=label, callback_data=f"venue:{v_id}:{user_lat}:{user_lon}")
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")
    ])
    await safe_send(
        chat_id,
        f"<b>Ближайшие {category_display}:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )

# ===================== СТАРЫЙ СЦЕНАРИЙ "НАЙТИ ВСЕ РЯДОМ" (кнопка "🔍 Все заведения рядом" тоже ведёт сюда) =====================
# Мы уже добавили её выше в quick_category, поэтому отдельный обработчик "🔍 Найти заведения рядом" больше не нужен.
# Оставим только старый для обратной совместимости, если вдруг была кнопка "🔍 Найти заведения рядом" – заменим на вызов quick_category.
@router.message(F.text == "🔍 Найти заведения рядом")
async def old_find_nearby(msg: Message, state: FSMContext):
    await quick_category(Message(text="🔍 Все заведения рядом"), state)

# ===================== ОБРАБОТЧИКИ CALLBACK (список, карточка, меню) =====================
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
        f"📝 {venue['desc']}\n"
        f"📞 {venue['phone'] if venue['phone'] else 'Не указан'}\n\n"
    )
    if venue['menu']:
        text += "🍴 <b>Меню (первые 5 позиций):</b>\n"
        for item, price in venue['menu']:
            text += f"• {item} — {price} Br\n"
    else:
        text += "🍽️ Меню пока не загружено."

    # Ссылки и кнопки
    map_url = f"https://yandex.ru/maps/?ll={venue['lon']},{venue['lat']}&z=16&text={venue['name']}"
    route_url = f"https://yandex.ru/maps/?rtext={user_lat},{user_lon}~{venue['lat']},{venue['lon']}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Посмотреть на карте", url=map_url)],
        [InlineKeyboardButton(text="🗺 Построить маршрут", url=route_url)],
        [InlineKeyboardButton(text="📋 Полное меню", callback_data=f"full_menu:{venue_id}:{user_lat}:{user_lon}")],
    ])
    if venue['phone']:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text="📞 Забронировать (позвонить)", callback_data=f"book:{venue_id}")
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Назад к списку", callback_data=f"back_to_list:{user_lat}:{user_lon}"),
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")
    ])

    await safe_edit(call, text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("book:"))
async def show_phone(call: CallbackQuery):
    venue_id = int(call.data.split(":")[1])
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name, phone FROM venues WHERE id=?", (venue_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[1]:
        text = f"📞 <b>{row[0]}</b>\nТелефон: <code>{row[1]}</code>"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📞 Позвонить", url=f"tel:{row[1]}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data=call.data.replace("book:", "venue:"))]
        ])
    else:
        text = "У этого заведения нет телефона для бронирования."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data=call.data.replace("book:", "venue:"))]
        ])
    await safe_edit(call, text, reply_markup=kb, parse_mode="HTML")

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
            text += f"  • {item} — {price} Br\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к карточке", callback_data=f"venue:{venue_id}:{user_lat}:{user_lon}")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")]
    ])

    await safe_edit(call, text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("back_to_list:"))
async def back_to_list(call: CallbackQuery):
    parts = call.data.split(":")
    user_lat = float(parts[1])
    user_lon = float(parts[2])
    # Вернём выбор категории (можно было бы хранить предфильтр, но для упрощения покажем общий выбор)
    await safe_edit(
        call,
        "🎯 <b>Выберите категорию заведений:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="☕️ Кофейни", callback_data=f"cat:Кофейня:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="🍽️ Рестораны", callback_data=f"cat:Ресторан:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="🍻 Бары", callback_data=f"cat:Бар:{user_lat}:{user_lon}")],
            [InlineKeyboardButton(text="⭐ Показать всё", callback_data=f"cat:all:{user_lat}:{user_lon}")],
        ]),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("cat:"))
async def list_venues(call: CallbackQuery):
    parts = call.data.split(":")
    category = parts[1]
    user_lat = float(parts[2])
    user_lon = float(parts[3])

    venues = get_nearby_venues(user_lat, user_lon, category)
    if not venues:
        await safe_edit(call, "😔 В этой категории пока ничего рядом нет.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for v_id, v_name, v_addr, dist in venues:
        emoji = "🚶" if dist < 0.5 else "🚗"
        if v_addr:
            short_addr = v_addr.split(",")[0].strip()
            if len(short_addr) > 25:
                short_addr = short_addr[:22] + "..."
            label = f"{emoji} {v_name} ({short_addr}) — {dist:.2f} км"
        else:
            label = f"{emoji} {v_name} — {dist:.2f} км"
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=label, callback_data=f"venue:{v_id}:{user_lat}:{user_lon}")
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Выбрать другую категорию", callback_data=f"cat_reselect:{user_lat}:{user_lon}")
    ])

    cat_display = category if category != 'all' else 'все'
    await safe_edit(call, f"<b>Ближайшие заведения ({cat_display}):</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "main_menu")
async def go_main_menu(call: CallbackQuery):
    await safe_edit(call, "Главное меню", reply_markup=None)
    await safe_send(call.message.chat.id, "Выберите действие:", reply_markup=main_menu())

# ===================== ПРОСМОТР МЕНЮ (БЕЗ ГЕОЛОКАЦИИ) =====================
@router.message(F.text == "📋 Меню заведений")
async def menu_list(msg: Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name, address FROM venues ORDER BY name")
    venues = cur.fetchall()
    conn.close()
    if not venues:
        await safe_send(msg.chat.id, "В базе пока нет заведений.")
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
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")
    ])
    await safe_send(msg.chat.id, "Выберите заведение для просмотра меню:", reply_markup=kb)

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
            text += f"  • {item} — {price} Br\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")]
    ])
    await safe_edit(call, text, reply_markup=kb, parse_mode="HTML")

# ===================== АДМИН-ПАНЕЛЬ (все функции остаются) =====================
# ... (оставьте без изменений, только в admin_menu кнопки, в синхронизацию добавить phone)
# ВАЖНО: в process_venues_csv и add_venue_desc нужно добавить обработку поля phone.
# Приведу только изменённые куски для синхронизации и добавления заведения.

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
            phone = row.get('phone', '')

            cur.execute("SELECT id FROM categories WHERE name=?", (category,))
            cat = cur.fetchone()
            if not cat:
                cur.execute("INSERT INTO categories (name) VALUES (?)", (category,))
                cat_id = cur.lastrowid
            else:
                cat_id = cat[0]
            cur.execute(
                "INSERT INTO venues (name, category_id, address, latitude, longitude, description, phone) "
                "VALUES (?,?,?,?,?,?,?)",
                (name, cat_id, address, lat, lon, desc, phone)
            )
            count += 1
        except Exception as e:
            errors.append(f"Строка {row_num}: {e}")
    conn.commit()
    cur.execute("SELECT name, COUNT(*) FROM venues GROUP BY name HAVING COUNT(*) > 1")
    dups = cur.fetchall()
    conn.close()
    reply = f"✅ Добавлено заведений: {count}"
    if dups:
        dup_names = ", ".join([d[0] for d in dups])
        reply += f"\n⚠️ Обнаружены дубликаты названий: {dup_names}."
    if errors:
        reply += f"\n⚠️ Ошибки в {len(errors)} строках:\n" + "\n".join(errors[:5])
    await safe_send(msg.chat.id, reply)

# В функции add_venue_desc нужно добавить поле phone:
@router.message(AddVenue.description)
async def add_venue_desc(msg: Message, state: FSMContext):
    data = await state.get_data()
    desc = msg.text if msg.text != "-" else ""
    await state.update_data(description=desc)
    await safe_send(msg.chat.id, "Введите <b>телефон</b> (можно отправить прочерк '-'):", parse_mode="HTML")
    await state.set_state(AddVenue.phone)

@router.message(AddVenue.phone)
async def add_venue_phone(msg: Message, state: FSMContext):
    phone = msg.text if msg.text != "-" else ""
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
            "INSERT INTO venues (name, category_id, address, latitude, longitude, description, phone) "
            "VALUES (?,?,?,?,?,?,?)",
            (data['name'], cat_id, data['address'],
             float(data['latitude']), float(data['longitude']), data['description'], phone)
        )
        conn.commit()
        conn.close()
        row_csv = f"{data['name']}, {data['category']}, {data['address']}, {data['latitude']}, {data['longitude']}, {data['description']}, {phone}"
        await safe_send(
            msg.chat.id,
            f"✅ Заведение «{data['name']}» добавлено!\n"
            f"📋 Строка для Google Таблицы:\n<code>{row_csv}</code>",
            reply_markup=admin_menu(),
            parse_mode="HTML"
        )
    except Exception as e:
        await safe_send(msg.chat.id, f"❌ Ошибка: {e}")
    await state.clear()

# Не забудьте обновить Google Sheet: добавьте колонку phone в лист venues (после description).
# Остальная часть кода (очистка, добавление меню, статус) остаётся без изменений, только в safe_send/safe_edit.

# ===================== ЗАПУСК =====================
async def main():
    init_db()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())