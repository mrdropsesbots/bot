import os
import io
import csv
import sqlite3
import asyncio
import requests

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
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
MINI_APP_URL     = os.environ.get("MINI_APP_URL", "https://your-miniapp.onrender.com")  # замените после деплоя

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

# --------------------- Вспомогательные функции ---------------------
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
            [KeyboardButton(text="📋 Меню заведений"), KeyboardButton(text="ℹ️ О боте")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите категорию"
    )

def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔄 Синхронизировать из Google Таблиц")],
            [KeyboardButton(text="➕ Добавить заведение"), KeyboardButton(text="🍽️ Добавить позицию меню")],
            [KeyboardButton(text="📊 Статус базы"), KeyboardButton(text="🔙 Главное меню")],
            [KeyboardButton(text="🧹 Очистить базу данных")],
            [KeyboardButton(text="🖥 Открыть админку", web_app=WebAppInfo(url=MINI_APP_URL))],
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
    await msg.answer(
        "👋 <b>Добро пожаловать!</b>\nВыберите категорию заведений:",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )

@router.message(F.text == "🔙 Главная")
@router.message(Command("menu"))
async def show_menu(msg: Message):
    await msg.answer("Главное меню", reply_markup=main_menu())

@router.message(F.text == "ℹ️ О боте")
async def about(msg: Message):
    await msg.answer(
        "🍽️ <b>Гид по заведениям</b>\n\n"
        "☕️🍽️🍻 Категории кофеен, ресторанов и баров.\n"
        "📋 Меню с ценами в белорусских рублях (Br).\n"
        "🗺 Просмотр на карте и построение маршрута.\n"
        "📞 Бронирование столика (если есть телефон).\n\n"
        "📌 Администраторам: /admin",
        parse_mode="HTML"
    )

# ===================== ПОИСК ПО КАТЕГОРИЯМ =====================
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
        await msg.answer("😔 В этой категории пока нет заведений.", reply_markup=main_menu())
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for v_id, v_name, v_addr, v_lat, v_lon in venues:
        label = f"{v_name} ({v_addr.split(',')[0].strip()})" if v_addr else v_name
        if len(label) > 40:
            label = label[:37] + "..."
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=label, callback_data=f"venue:{v_id}:cat:{category}")
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")
    ])

    cat_display = category if category != 'all' else 'все'
    await msg.answer(
        f"<b>Заведения ({cat_display}):</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )

# ===================== ОБРАБОТЧИКИ INLINE КНОПОК =====================
@router.callback_query(F.data.startswith("venue:"))
async def show_venue(call: CallbackQuery):
    parts = call.data.split(":")
    venue_id = int(parts[1])
    category = parts[3] if len(parts) >= 4 and parts[2] == 'cat' else None

    venue = get_venue_details(venue_id)
    if not venue:
        await call.answer("Заведение не найдено.")
        return

    text = (
        f"<b>{venue['name']}</b>\n"
        f"📍 {venue['address']}\n"
        f"📝 {venue['desc']}\n"
    )
    if venue['phone']:
        text += f"📞 {venue['phone']}\n"
    text += "\n"
    if venue['menu']:
        text += "🍴 <b>Топ-5 меню:</b>\n"
        for item, price in venue['menu']:
            text += f"• {item} — {price} Br\n"
    else:
        text += "🍽️ Меню пока не загружено."

    map_url = f"https://yandex.ru/maps/?ll={venue['lon']},{venue['lat']}&z=16&text={venue['name']}"
    route_url = f"https://yandex.ru/maps/?rtext=~{venue['lat']},{venue['lon']}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 На карте", url=map_url),
         InlineKeyboardButton(text="🗺 Маршрут", url=route_url)],
        [InlineKeyboardButton(text="📋 Полное меню", callback_data=f"full_menu:{venue_id}:cat:{category}" if category else f"full_menu:{venue_id}:0:0")],
    ])
    if venue['phone']:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text="📞 Позвонить", url=f"tel:{venue['phone']}"),
            InlineKeyboardButton(text="📋 Бронь", callback_data=f"book:{venue_id}:cat:{category}" if category else f"book:{venue_id}:0:0")
        ])
    if category:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_category:{category}"),
            InlineKeyboardButton(text="🏠 Главная", callback_data="main_menu")
        ])
    else:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text="🏠 Главная", callback_data="main_menu")
        ])

    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("book:"))
async def show_booking_phone(call: CallbackQuery):
    parts = call.data.split(":")
    venue_id = int(parts[1])
    category = parts[3] if len(parts) >= 4 and parts[2] == 'cat' else None
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name, phone FROM venues WHERE id=?", (venue_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[1]:
        await call.answer(f"Телефон для брони: {row[1]}", show_alert=True)
    else:
        await call.answer("Телефон не указан.", show_alert=True)

@router.callback_query(F.data.startswith("full_menu:"))
async def show_full_menu(call: CallbackQuery):
    parts = call.data.split(":")
    venue_id = int(parts[1])
    category = parts[3] if len(parts) >= 4 and parts[2] == 'cat' else None

    menu = get_full_menu(venue_id)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name, address FROM venues WHERE id=?", (venue_id,))
    row = cur.fetchone()
    conn.close()
    full_name = f"{row[0]}, {row[1]}" if row and row[1] else (row[0] if row else "Заведение")

    if not menu:
        await call.answer("Меню отсутствует.")
        return

    text = f"<b>📋 Меню — {full_name}</b>\n"
    for cat, items in menu.items():
        text += f"\n<i>{cat}</i>\n"
        for item, price in items:
            text += f"  • {item} — {price} Br\n"

    back_cb = f"venue:{venue_id}:cat:{category}" if category else f"venue:{venue_id}:0:0"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data=back_cb)],
        [InlineKeyboardButton(text="🏠 Главная", callback_data="main_menu")]
    ])

    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("back_to_category:"))
async def back_to_category(call: CallbackQuery):
    category = call.data.split(":")[1]
    venues = get_venues_by_category(category)
    if not venues:
        await call.message.edit_text("😔 В этой категории пока нет заведений.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for v_id, v_name, v_addr, v_lat, v_lon in venues:
        label = f"{v_name} ({v_addr.split(',')[0].strip()})" if v_addr else v_name
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=label, callback_data=f"venue:{v_id}:cat:{category}")
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🏠 Главная", callback_data="main_menu")
    ])
    cat_display = category if category != 'all' else 'все'
    await call.message.edit_text(
        f"<b>Заведения ({cat_display}):</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data == "main_menu")
async def go_main_menu(call: CallbackQuery):
    await call.message.edit_text("Выберите категорию в меню снизу.")
    await call.message.answer("Главное меню", reply_markup=main_menu())
    await call.answer()

# ===================== ПРОСМОТР МЕНЮ (БЕЗ КАТЕГОРИЙ) =====================
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
        label = f"{name} ({address.split(',')[0].strip()})" if address else name
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=label, callback_data=f"full_menu_only:{vid}")
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🏠 Главная", callback_data="main_menu")
    ])
    await msg.answer("Выберите заведение для просмотра меню:", reply_markup=kb)

@router.callback_query(F.data.startswith("full_menu_only:"))
async def full_menu_only(call: CallbackQuery):
    venue_id = int(call.data.split(":")[1])
    menu = get_full_menu(venue_id)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name, address FROM venues WHERE id=?", (venue_id,))
    row = cur.fetchone()
    conn.close()
    full_name = f"{row[0]}, {row[1]}" if row and row[1] else (row[0] if row else "Заведение")

    if not menu:
        await call.answer("Меню отсутствует.")
        return

    text = f"<b>📋 Меню — {full_name}</b>\n"
    for cat, items in menu.items():
        text += f"\n<i>{cat}</i>\n"
        for item, price in items:
            text += f"  • {item} — {price} Br\n"

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главная", callback_data="main_menu")]
    ]), parse_mode="HTML")
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
    await msg.answer("Главное меню", reply_markup=main_menu())

# Синхронизация (оставлена для обратной совместимости, но с Mini App не обязательна)
@router.message(F.text == "🔄 Синхронизировать из Google Таблиц", is_admin)
async def sync_button(msg: Message):
    if not SHEET_URL_VENUES or not SHEET_URL_MENU:
        await msg.answer("❌ Не заданы ссылки на Google Таблицы.")
        return
    await msg.answer("🔄 Синхронизация...")
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
        await msg.answer("✅ Данные обновлены!")

# Добавление заведения (ручное)
@router.message(StateFilter(None), F.text == "➕ Добавить заведение", is_admin)
async def add_venue_start(msg: Message, state: FSMContext):
    await msg.answer("Введите <b>название</b>:", parse_mode="HTML")
    await state.set_state(AddVenue.name)

@router.message(AddVenue.name)
async def add_venue_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await msg.answer("Введите <b>категорию</b> (Кофейня, Ресторан, Бар):", parse_mode="HTML")
    await state.set_state(AddVenue.category)

@router.message(AddVenue.category)
async def add_venue_category(msg: Message, state: FSMContext):
    await state.update_data(category=msg.text)
    await msg.answer("Введите <b>адрес</b>:", parse_mode="HTML")
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
    await msg.answer("Введите <b>долготу</b>:", parse_mode="HTML")
    await state.set_state(AddVenue.longitude)

@router.message(AddVenue.longitude)
async def add_venue_lon(msg: Message, state: FSMContext):
    try:
        float(msg.text)
    except ValueError:
        await msg.answer("❌ Введите число")
        return
    await state.update_data(longitude=msg.text)
    await msg.answer("Введите <b>описание</b> (или '-'):", parse_mode="HTML")
    await state.set_state(AddVenue.description)

@router.message(AddVenue.description)
async def add_venue_desc(msg: Message, state: FSMContext):
    desc = msg.text if msg.text != "-" else ""
    await state.update_data(description=desc)
    await msg.answer("Введите <b>телефон</b> (или '-'):", parse_mode="HTML")
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
            "INSERT INTO venues (name, category_id, address, latitude, longitude, description, phone) VALUES (?,?,?,?,?,?,?)",
            (data['name'], cat_id, data['address'], float(data['latitude']), float(data['longitude']), data['description'], phone)
        )
        conn.commit()
        conn.close()
        await msg.answer(f"✅ Заведение «{data['name']}» добавлено!", reply_markup=admin_menu())
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
    await state.clear()

# Добавление позиции меню
@router.message(StateFilter(None), F.text == "🍽️ Добавить позицию меню", is_admin)
async def add_item_start(msg: Message, state: FSMContext):
    await msg.answer("Введите <b>название заведения</b>:", parse_mode="HTML")
    await state.set_state(AddItem.venue_name)

@router.message(AddItem.venue_name)
async def add_item_venue(msg: Message, state: FSMContext):
    await state.update_data(venue_name=msg.text)
    await msg.answer("Введите <b>название блюда</b>:", parse_mode="HTML")
    await state.set_state(AddItem.item_name)

@router.message(AddItem.item_name)
async def add_item_name(msg: Message, state: FSMContext):
    await state.update_data(item_name=msg.text)
    await msg.answer("Введите <b>цену</b> (только число):", parse_mode="HTML")
    await state.set_state(AddItem.price)

@router.message(AddItem.price)
async def add_item_price(msg: Message, state: FSMContext):
    try:
        float(msg.text)
    except ValueError:
        await msg.answer("❌ Введите число")
        return
    await state.update_data(price=msg.text)
    await msg.answer("Введите <b>категорию блюда</b> (или '-'):", parse_mode="HTML")
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
            await msg.answer(f"❌ Заведение «{venue_name}» не найдено.")
            await state.clear()
            conn.close()
            return
        if cnt > 1:
            cur.execute("SELECT name, address FROM venues WHERE name=?", (venue_name,))
            rows = cur.fetchall()
            addr_list = "\n".join([f"• {r[0]} — {r[1]}" for r in rows])
            await msg.answer(f"❌ Найдено несколько заведений с названием «{venue_name}»:\n{addr_list}\nУточните название.")
            conn.close()
            await state.clear()
            return
        cur.execute("SELECT id FROM venues WHERE name=?", (venue_name,))
        venue_id = cur.fetchone()[0]
        cur.execute("INSERT INTO menu_items (venue_id, name, price, category) VALUES (?,?,?,?)",
                    (venue_id, data['item_name'], float(data['price']), category))
        conn.commit()
        conn.close()
        await msg.answer(f"🍽️ «{data['item_name']}» добавлено!", reply_markup=admin_menu())
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
    await state.clear()

# Статус базы
@router.message(F.text == "📊 Статус базы", is_admin)
async def status_cmd(msg: Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM venues")
    venues_cnt = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM menu_items")
    items_cnt = cur.fetchone()[0]
    conn.close()
    await msg.answer(f"📊 Заведений: {venues_cnt}\n🍽️ Позиций меню: {items_cnt}")

# Очистка базы
@router.message(F.text == "🧹 Очистить базу данных", is_admin)
async def ask_clear(msg: Message, state: FSMContext):
    await msg.answer("⚠️ Удалить ВСЕ заведения и меню?\nОтправьте <b>ДА</b> для подтверждения.", parse_mode="HTML")
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
        await msg.answer("✅ База очищена.", reply_markup=admin_menu())
    else:
        await msg.answer("❌ Отмена.", reply_markup=admin_menu())
    await state.clear()

# ===================== ОБРАБОТЧИКИ CSV =====================
async def process_venues_csv(msg: Message, reader):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    count = 0
    errors = []
    for row_num, row in enumerate(reader, start=2):
        try:
            name = row.get('name')
            category = row.get('category')
            address = row.get('address', '')
            lat = float(row.get('latitude', 0))
            lon = float(row.get('longitude', 0))
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
                "INSERT INTO venues (name, category_id, address, latitude, longitude, description, phone) VALUES (?,?,?,?,?,?,?)",
                (name, cat_id, address, lat, lon, desc, phone)
            )
            count += 1
        except Exception as e:
            errors.append(f"Строка {row_num}: {e}")
    conn.commit()
    conn.close()
    reply = f"✅ Заведений добавлено: {count}"
    if errors:
        reply += "\n" + "\n".join(errors[:5])
    await msg.answer(reply)

async def process_menu_csv(msg: Message, reader):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    count = 0
    skipped = 0
    errors = []
    for row_num, row in enumerate(reader, start=2):
        try:
            venue_name = row.get('venue_name', '').strip()
            item_name = row.get('item_name')
            price = float(row.get('price', 0))
            cat = row.get('category', '')

            cur.execute("SELECT COUNT(*) FROM venues WHERE name=?", (venue_name,))
            cnt = cur.fetchone()[0]
            if cnt == 0:
                skipped += 1
                continue
            if cnt > 1:
                skipped += 1
                continue
            cur.execute("SELECT id FROM venues WHERE name=?", (venue_name,))
            venue_id = cur.fetchone()[0]
            cur.execute("INSERT INTO menu_items (venue_id, name, price, category) VALUES (?,?,?,?)",
                        (venue_id, item_name, price, cat))
            count += 1
        except Exception as e:
            errors.append(f"Строка {row_num}: {e}")
    conn.commit()
    conn.close()
    msg_text = f"🍽️ Позиций меню добавлено: {count}"
    if skipped:
        msg_text += f"\n⚠️ Пропущено: {skipped}"
    if errors:
        msg_text += "\n" + "\n".join(errors[:5])
    await msg.answer(msg_text)

# ===================== ЗАПУСК =====================
async def main():
    init_db()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())