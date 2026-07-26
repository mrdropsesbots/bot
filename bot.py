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

# --------------------- Хранилище для удаления сообщений ---------------------
last_msg = {}

async def safe_send(chat_id, text, reply_markup=None, parse_mode=None, disable_web_page_preview=True):
    if chat_id in last_msg:
        try:
            await bot.delete_message(chat_id, last_msg[chat_id])
        except:
            pass
    msg = await bot.send_message(
        chat_id, text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
        disable_web_page_preview=disable_web_page_preview
    )
    last_msg[chat_id] = msg.message_id
    return msg

async def safe_edit(call: CallbackQuery, text, reply_markup=None, parse_mode=None):
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

def get_venues_by_category(category=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if category and category != 'all':
        cur.execute("SELECT id FROM categories WHERE name=?", (category,))
        cat_row = cur.fetchone()
        if not cat_row:
            conn.close()
            return []
        cat_id = cat_row[0]
        cur.execute("SELECT id, name, address, latitude, longitude FROM venues WHERE category_id=? ORDER BY name", (cat_id,))
    else:
        cur.execute("SELECT id, name, address, latitude, longitude FROM venues ORDER BY name")
    venues = cur.fetchall()
    conn.close()
    return [(v[0], v[1], v[2] if v[2] else "", v[3], v[4]) for v in venues]

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
            [KeyboardButton(text="🍻 Бары"), KeyboardButton(text="🔍 Все заведения")],
            [KeyboardButton(text="📋 Меню заведений"), KeyboardButton(text="📍 Мои маршруты")],
            [KeyboardButton(text="ℹ️ О боте"), KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите категорию или действие"
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
        "👋 <b>Добро пожаловать!</b>\nЯ помогу найти лучшие рестораны, кофейни и бары.\nВыберите категорию:",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )

@router.message(F.text == "🔙 Главная")
@router.message(Command("menu"))
async def show_menu(msg: Message):
    await safe_send(msg.chat.id, "Главное меню", reply_markup=main_menu())

@router.message(F.text == "ℹ️ О боте")
async def about(msg: Message):
    await safe_send(
        msg.chat.id,
        "🍽️ <b>Гид по заведениям</b>\n\n"
        "☕️🍽️🍻 Категории кофеен, ресторанов и баров.\n"
        "📋 Меню с ценами в белорусских рублях (Br).\n"
        "🗺 Просмотр на карте и построение маршрута.\n"
        "📞 Бронирование столика по телефону.\n\n"
        "📌 Администраторам: /admin",
        parse_mode="HTML"
    )

@router.message(F.text == "⚙️ Настройки")
async def settings(msg: Message):
    await safe_send(msg.chat.id, "🔧 Здесь будут настройки (язык, радиус поиска). Пока в разработке.")

@router.message(F.text == "📍 Мои маршруты")
async def my_routes(msg: Message):
    await safe_send(msg.chat.id, "📌 Здесь будут сохраняться последние построенные маршруты. Функция в разработке.")

# ===================== ПОИСК ПО КАТЕГОРИЯМ (БЕЗ ГЕОЛОКАЦИИ) =====================
@router.message(F.text.in_({"☕️ Кофейни", "🍽️ Рестораны", "🍻 Бары", "🔍 Все заведения"}))
async def show_category_venues(msg: Message):
    cat_map = {
        "☕️ Кофейни": "Кофейня",
        "🍽️ Рестораны": "Ресторан",
        "🍻 Бары": "Бар",
        "🔍 Все заведения": "all"
    }
    category = cat_map[msg.text]
    venues = get_venues_by_category(category)
    if not venues:
        await safe_send(msg.chat.id, "😔 В этой категории пока нет заведений.", reply_markup=main_menu())
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for v_id, v_name, v_addr, v_lat, v_lon in venues:
        if v_addr:
            short_addr = v_addr.split(",")[0].strip()
            if len(short_addr) > 25:
                short_addr = short_addr[:22] + "..."
            label = f"{v_name} ({short_addr})"
        else:
            label = v_name
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"venue:{v_id}:cat:{category}"
            )
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")
    ])

    cat_display = category if category != 'all' else 'все'
    await safe_send(
        msg.chat.id,
        f"<b>Все заведения ({cat_display}):</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )

# ===================== ОБРАБОТЧИКИ INLINE КНОПОК =====================
@router.callback_query(F.data.startswith("venue:"))
async def show_venue(call: CallbackQuery):
    parts = call.data.split(":")
    if len(parts) == 4 and parts[2] == 'cat':
        venue_id = int(parts[1])
        category = parts[3]
        user_lat, user_lon = None, None
    else:
        # совместимость со старым форматом (если остался)
        venue_id = int(parts[1])
        user_lat = float(parts[2])
        user_lon = float(parts[3])
        category = None

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
        text += "🍴 <b>Топ-5 меню:</b>\n"
        for item, price in venue['menu']:
            text += f"• {item} — {price} Br\n"
    else:
        text += "🍽️ Меню пока не загружено."

    # Ссылки на карту и маршрут
    map_url = f"https://yandex.ru/maps/?ll={venue['lon']},{venue['lat']}&z=16&text={venue['name']}"
    if user_lat is not None and user_lon is not None:
        route_url = f"https://yandex.ru/maps/?rtext={user_lat},{user_lon}~{venue['lat']},{venue['lon']}"
    else:
        route_url = f"https://yandex.ru/maps/?rtext=~{venue['lat']},{venue['lon']}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Посмотреть на карте", url=map_url)],
        [InlineKeyboardButton(text="🗺 Построить маршрут", url=route_url)],
        [InlineKeyboardButton(text="📋 Полное меню",
                              callback_data=f"full_menu:{venue_id}:cat:{category}" if category else f"full_menu:{venue_id}:0:0")],
    ])
    if venue['phone']:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text="📞 Забронировать (позвонить)",
                                 callback_data=f"book:{venue_id}:cat:{category}" if category else f"book:{venue_id}:0:0")
        ])
    if category:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text="🔙 Назад к списку", callback_data=f"back_to_category:{category}"),
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")
        ])
    else:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")
        ])

    await safe_edit(call, text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("book:"))
async def show_booking_phone(call: CallbackQuery):
    parts = call.data.split(":")
    venue_id = int(parts[1])
    category = None
    if len(parts) >= 4 and parts[2] == 'cat':
        category = parts[3]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name, phone FROM venues WHERE id=?", (venue_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[1]:
        text = f"📞 <b>{row[0]}</b>\nТелефон: <code>{row[1]}</code>"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📞 Позвонить", url=f"tel:{row[1]}")],
            [InlineKeyboardButton(text="🔙 Назад к карточке",
                                  callback_data=f"venue:{venue_id}:cat:{category}" if category else f"venue:{venue_id}:0:0")]
        ])
    else:
        text = "У этого заведения нет телефона для бронирования."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к карточке",
                                  callback_data=f"venue:{venue_id}:cat:{category}" if category else f"venue:{venue_id}:0:0")]
        ])
    await safe_edit(call, text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("full_menu:"))
async def show_full_menu(call: CallbackQuery):
    parts = call.data.split(":")
    venue_id = int(parts[1])
    category = None
    if len(parts) >= 4 and parts[2] == 'cat':
        category = parts[3]

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

    back_callback = f"venue:{venue_id}:cat:{category}" if category else f"venue:{venue_id}:0:0"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к карточке", callback_data=back_callback)],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")]
    ])
    await safe_edit(call, text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("back_to_category:"))
async def back_to_category(call: CallbackQuery):
    category = call.data.split(":")[1]
    venues = get_venues_by_category(category)
    if not venues:
        await safe_edit(call, "😔 В этой категории пока нет заведений.", reply_markup=None)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for v_id, v_name, v_addr, v_lat, v_lon in venues:
        if v_addr:
            short_addr = v_addr.split(",")[0].strip()
            if len(short_addr) > 25:
                short_addr = short_addr[:22] + "..."
            label = f"{v_name} ({short_addr})"
        else:
            label = v_name
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=label, callback_data=f"venue:{v_id}:cat:{category}")
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")
    ])
    cat_display = category if category != 'all' else 'все'
    await safe_edit(
        call,
        f"<b>Все заведения ({cat_display}):</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "main_menu")
async def go_main_menu(call: CallbackQuery):
    await safe_edit(call, "Выберите действие:", reply_markup=None)
    await safe_send(call.message.chat.id, "Главное меню", reply_markup=main_menu())

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

# ===================== АДМИН-ПАНЕЛЬ =====================
@router.message(Command("admin"))
async def admin_cmd(msg: Message):
    if not is_admin(msg):
        await safe_send(msg.chat.id, "⛔ Доступ запрещён.")
        return
    await safe_send(msg.chat.id, "🔧 <b>Админ-панель</b>", reply_markup=admin_menu(), parse_mode="HTML")

@router.message(F.text == "🔙 Главное меню", is_admin)
async def admin_back(msg: Message):
    await safe_send(msg.chat.id, "Возврат в главное меню.", reply_markup=main_menu())

# Синхронизация
@router.message(F.text == "🔄 Синхронизировать из Google Таблиц", is_admin)
async def sync_button(msg: Message):
    if not SHEET_URL_VENUES or not SHEET_URL_MENU:
        await safe_send(msg.chat.id, "❌ Не заданы ссылки на Google Таблицы.")
        return
    await safe_send(msg.chat.id, "🔄 Запускаю синхронизацию...")
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
        await safe_send(msg.chat.id, "⚠️ Ошибки:\n" + "\n".join(errors))
    else:
        await safe_send(msg.chat.id, "✅ Данные успешно обновлены!")

# Добавление заведения
@router.message(StateFilter(None), F.text == "➕ Добавить заведение", is_admin)
async def add_venue_start(msg: Message, state: FSMContext):
    await safe_send(msg.chat.id, "Введите <b>название</b> заведения:", parse_mode="HTML")
    await state.set_state(AddVenue.name)

@router.message(AddVenue.name)
async def add_venue_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await safe_send(msg.chat.id, "Введите <b>категорию</b> (например, Кофейня, Ресторан, Бар):", parse_mode="HTML")
    await state.set_state(AddVenue.category)

@router.message(AddVenue.category)
async def add_venue_category(msg: Message, state: FSMContext):
    await state.update_data(category=msg.text)
    await safe_send(msg.chat.id, "Введите <b>адрес</b> (улица, дом):", parse_mode="HTML")
    await state.set_state(AddVenue.address)

@router.message(AddVenue.address)
async def add_venue_address(msg: Message, state: FSMContext):
    await state.update_data(address=msg.text)
    await safe_send(msg.chat.id, "Введите <b>широту</b> (например, 55.7652):", parse_mode="HTML")
    await state.set_state(AddVenue.latitude)

@router.message(AddVenue.latitude)
async def add_venue_lat(msg: Message, state: FSMContext):
    try:
        float(msg.text)
    except ValueError:
        await safe_send(msg.chat.id, "❌ Введите число")
        return
    await state.update_data(latitude=msg.text)
    await safe_send(msg.chat.id, "Введите <b>долготу</b> (например, 37.6050):", parse_mode="HTML")
    await state.set_state(AddVenue.longitude)

@router.message(AddVenue.longitude)
async def add_venue_lon(msg: Message, state: FSMContext):
    try:
        float(msg.text)
    except ValueError:
        await safe_send(msg.chat.id, "❌ Введите число")
        return
    await state.update_data(longitude=msg.text)
    await safe_send(msg.chat.id, "Введите <b>описание</b> (можно отправить прочерк '-'):", parse_mode="HTML")
    await state.set_state(AddVenue.description)

@router.message(AddVenue.description)
async def add_venue_desc(msg: Message, state: FSMContext):
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

# Добавление позиции меню
@router.message(StateFilter(None), F.text == "🍽️ Добавить позицию меню", is_admin)
async def add_item_start(msg: Message, state: FSMContext):
    await safe_send(msg.chat.id, "Введите <b>название заведения</b>, к которому относится блюдо:", parse_mode="HTML")
    await state.set_state(AddItem.venue_name)

@router.message(AddItem.venue_name)
async def add_item_venue(msg: Message, state: FSMContext):
    await state.update_data(venue_name=msg.text)
    await safe_send(msg.chat.id, "Введите <b>название блюда/напитка</b>:", parse_mode="HTML")
    await state.set_state(AddItem.item_name)

@router.message(AddItem.item_name)
async def add_item_name(msg: Message, state: FSMContext):
    await state.update_data(item_name=msg.text)
    await safe_send(msg.chat.id, "Введите <b>цену</b> (только число, например 250):", parse_mode="HTML")
    await state.set_state(AddItem.price)

@router.message(AddItem.price)
async def add_item_price(msg: Message, state: FSMContext):
    try:
        float(msg.text)
    except ValueError:
        await safe_send(msg.chat.id, "❌ Введите число")
        return
    await state.update_data(price=msg.text)
    await safe_send(msg.chat.id, "Введите <b>категорию блюда</b> (Напитки, Десерты и т.п.) или отправьте прочерк:", parse_mode="HTML")
    await state.set_state(AddItem.category)

@router.message(AddItem.category)
async def add_item_cat(msg: Message, state: FSMContext):
    data = await state.get_data()
    category = msg.text if msg.text != "-" else "Без категории"
    venue_name = data['venue_name'].strip()
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM venues WHERE name=?", (venue_name,))
        cnt = cur.fetchone()[0]
        if cnt == 0:
            await safe_send(msg.chat.id, f"❌ Заведение «{venue_name}» не найдено.")
            await state.clear()
            conn.close()
            return
        if cnt > 1:
            cur.execute("SELECT name, address FROM venues WHERE name=?", (venue_name,))
            rows = cur.fetchall()
            addr_list = "\n".join([f"• {r[0]} — {r[1]}" for r in rows])
            await safe_send(
                msg.chat.id,
                f"❌ Найдено несколько заведений с названием «{venue_name}»:\n{addr_list}\n\n"
                "Пожалуйста, используйте уникальное название (например, с улицей)."
            )
            conn.close()
            await state.clear()
            return
        cur.execute("SELECT id FROM venues WHERE name=?", (venue_name,))
        venue_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO menu_items (venue_id, name, price, category) VALUES (?,?,?,?)",
            (venue_id, data['item_name'], float(data['price']), category)
        )
        conn.commit()
        conn.close()
        row_csv = f"{venue_name}, {data['item_name']}, {data['price']}, {category}"
        await safe_send(
            msg.chat.id,
            f"🍽️ «{data['item_name']}» добавлено в меню «{venue_name}».\n"
            f"📋 Строка для Google Таблицы:\n<code>{row_csv}</code>",
            reply_markup=admin_menu(),
            parse_mode="HTML"
        )
    except Exception as e:
        await safe_send(msg.chat.id, f"❌ Ошибка: {e}")
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
        await safe_send(msg.chat.id, f"📊 Заведений: {venues_cnt}\n🍽️ Позиций меню: {items_cnt}")
    except Exception as e:
        await safe_send(msg.chat.id, f"❌ Ошибка доступа к базе: {e}")

# Очистка базы данных
@router.message(F.text == "🧹 Очистить базу данных", is_admin)
async def ask_clear(msg: Message, state: FSMContext):
    await safe_send(
        msg.chat.id,
        "⚠️ Вы собираетесь удалить <b>ВСЕ</b> заведения и меню.\n\n"
        "Отправьте <b>ДА</b> (заглавными) для подтверждения.",
        parse_mode="HTML"
    )
    await state.set_state(ClearConfirm.confirm)

@router.message(ClearConfirm.confirm)
async def do_clear(msg: Message, state: FSMContext):
    if msg.text == "ДА":
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM menu_items")
        cur.execute("DELETE FROM venues")
        cur.execute("DELETE FROM categories")
        conn.commit()
        conn.close()
        await safe_send(msg.chat.id, "✅ База данных полностью очищена.", reply_markup=admin_menu())
    else:
        await safe_send(msg.chat.id, "❌ Очистка отменена.", reply_markup=admin_menu())
    await state.clear()

# ===================== ОБРАБОТЧИКИ CSV =====================
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

async def process_menu_csv(msg: Message, reader):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    count = 0
    skipped = 0
    duplicates = 0
    errors = []
    for row_num, row in enumerate(reader, start=2):
        try:
            venue_name = row['venue_name'].strip()
            item_name = row['item_name']
            price = float(row['price'])
            cat = row.get('category', '')

            cur.execute("SELECT COUNT(*) FROM venues WHERE name=?", (venue_name,))
            cnt = cur.fetchone()[0]
            if cnt == 0:
                skipped += 1
                continue
            if cnt > 1:
                duplicates += 1
                continue

            cur.execute("SELECT id FROM venues WHERE name=?", (venue_name,))
            venue_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO menu_items (venue_id, name, price, category) VALUES (?,?,?,?)",
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
    if duplicates:
        msg_text += f"\n⚠️ Пропущено из-за дубликатов названий: {duplicates}"
    if errors:
        msg_text += f"\n🚫 Ошибки:\n" + "\n".join(errors[:5])
    await safe_send(msg.chat.id, msg_text)

# ===================== ЗАПУСК =====================
async def main():
    init_db()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
