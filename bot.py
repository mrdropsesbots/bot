import os
import io
import csv
import sqlite3
import asyncio
import tempfile
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --------------------- Настройки ---------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = list(map(int, os.environ.get("ADMIN_IDS", "").split(","))) if os.environ.get("ADMIN_IDS") else []
DB_PATH = os.environ.get("DB_PATH", "/data/places.db")

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
class ImportState(StatesGroup):
    waiting_for_files = State()

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
            [KeyboardButton(text="📥 Экспорт CSV"), KeyboardButton(text="📤 Импорт CSV")],
            [KeyboardButton(text="📊 Статус базы"), KeyboardButton(text="🧹 Очистить базу")],
            [KeyboardButton(text="🔙 Главное меню")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Админ-панель"
    )

# --------------------- Бот и диспетчер ---------------------
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

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
        [InlineKeyboardButton(text="📋 Полное меню",
                              callback_data=f"full_menu:{venue_id}:cat:{category}" if category else f"full_menu:{venue_id}:0:0")],
    ])
    if venue['phone']:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text="📞 Позвонить", url=f"tel:{venue['phone']}"),
            InlineKeyboardButton(text="📋 Бронь",
                                 callback_data=f"book:{venue_id}:cat:{category}" if category else f"book:{venue_id}:0:0")
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

# ===================== ЭКСПОРТ CSV =====================
@router.message(F.text == "📥 Экспорт CSV", is_admin)
async def export_csv(msg: Message):
    await msg.answer("📤 Готовлю CSV-файлы...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT v.name, c.name, v.address, v.latitude, v.longitude, v.description, v.phone FROM venues v JOIN categories c ON v.category_id=c.id")
    venues = cur.fetchall()

    cur.execute("SELECT v.name, m.name, m.price, m.category FROM menu_items m JOIN venues v ON m.venue_id=v.id")
    menu = cur.fetchall()
    conn.close()

    tmp_venues = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', encoding='utf-8')
    writer = csv.writer(tmp_venues)
    writer.writerow(["name", "category", "address", "latitude", "longitude", "description", "phone"])
    writer.writerows(venues)
    tmp_venues.close()

    tmp_menu = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', encoding='utf-8')
    writer = csv.writer(tmp_menu)
    writer.writerow(["venue_name", "item_name", "price", "category"])
    writer.writerows(menu)
    tmp_menu.close()

    await msg.answer_document(FSInputFile(tmp_venues.name, filename="venues.csv"), caption="📌 Заведения")
    await msg.answer_document(FSInputFile(tmp_menu.name, filename="menu.csv"), caption="🍽️ Меню")

    os.unlink(tmp_venues.name)
    os.unlink(tmp_menu.name)

# ===================== ИМПОРТ CSV =====================
@router.message(F.text == "📤 Импорт CSV", is_admin)
async def import_csv(msg: Message, state: FSMContext):
    await msg.answer(
        "📎 Пришлите два файла: <b>venues.csv</b> и <b>menu.csv</b>.\n"
        "Сначала отправьте venues.csv, затем menu.csv.\n\n"
        "<b>Внимание!</b> Текущая база будет полностью заменена.",
        parse_mode="HTML"
    )
    await state.set_state(ImportState.waiting_for_files)

@router.message(ImportState.waiting_for_files, F.document)
async def process_import(msg: Message, state: FSMContext):
    if msg.document.file_name == "venues.csv":
        await state.update_data(venues_file_id=msg.document.file_id)
        await msg.answer("✅ Venues получен. Теперь пришлите <b>menu.csv</b>.", parse_mode="HTML")
    elif msg.document.file_name == "menu.csv":
        data = await state.get_data()
        if "venues_file_id" not in data:
            await msg.answer("❌ Сначала пришлите venues.csv!")
            return
        venues_file_id = data["venues_file_id"]
        menu_file_id = msg.document.file_id
        await do_import(msg, state, venues_file_id, menu_file_id)
        await state.clear()
    else:
        await msg.answer("❌ Неизвестный файл. Пришлите <b>venues.csv</b> или <b>menu.csv</b>.", parse_mode="HTML")

async def do_import(msg: Message, state: FSMContext, venues_file_id, menu_file_id):
    await msg.answer("⏳ Импорт...")
    try:
        v_file = await bot.get_file(venues_file_id)
        m_file = await bot.get_file(menu_file_id)

        v_bytes = await bot.download_file(v_file.file_path)
        m_bytes = await bot.download_file(m_file.file_path)

        venues_csv = v_bytes.read().decode('utf-8')
        menu_csv = m_bytes.read().decode('utf-8')

        v_reader = csv.DictReader(io.StringIO(venues_csv))
        m_reader = csv.DictReader(io.StringIO(menu_csv))

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM menu_items")
        cur.execute("DELETE FROM venues")
        cur.execute("DELETE FROM categories")

        for row in v_reader:
            cat_name = row.get('category', 'Без категории').strip()
            cur.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat_name,))
            cur.execute("SELECT id FROM categories WHERE name=?", (cat_name,))
            cat_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO venues (name, category_id, address, latitude, longitude, description, phone) "
                "VALUES (?,?,?,?,?,?,?)",
                (row['name'], cat_id, row.get('address',''), float(row.get('latitude',0)), float(row.get('longitude',0)),
                 row.get('description',''), row.get('phone',''))
            )

        for row in m_reader:
            venue_name = row.get('venue_name', '').strip()
            cur.execute("SELECT id FROM venues WHERE name=?", (venue_name,))
            v = cur.fetchone()
            if v:
                cur.execute(
                    "INSERT INTO menu_items (venue_id, name, price, category) VALUES (?,?,?,?)",
                    (v[0], row['item_name'], float(row['price']), row.get('category', ''))
                )

        conn.commit()
        conn.close()
        await msg.answer("✅ Импорт успешно завершён! База обновлена.", reply_markup=admin_menu())
    except Exception as e:
        await msg.answer(f"❌ Ошибка импорта: {e}")

# ===================== СТАТУС И ОЧИСТКА =====================
@router.message(F.text == "📊 Статус базы", is_admin)
async def status_cmd(msg: Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM venues")
    venues_cnt = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM menu_items")
    items_
