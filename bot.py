import asyncio
import logging
import csv
import io
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile,
)

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8663406888:AAF3891CIjS3tASop0B092IlFN7Pato7DMU"       # ← заменить
ADMIN_ID = 5377564835                        # ← ваш Telegram user ID
DATABASE = "minsk.db"

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    # Таблица меню
    c.execute("""
    CREATE TABLE IF NOT EXISTS menu (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        restaurant TEXT NOT NULL,
        dish TEXT NOT NULL,
        price REAL NOT NULL,
        address TEXT,
        lat REAL,
        lon REAL
    )""")
    # Таблица пользователей (для статистики)
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        search_count INTEGER DEFAULT 0,
        loc_count INTEGER DEFAULT 0,
        book_count INTEGER DEFAULT 0,
        source TEXT DEFAULT 'direct'
    )""")
    conn.commit()
    conn.close()

init_db()

# ========== БОТ ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
user_location = {}  # {user_id: (lat, lon)}

# ========== КЛАВИАТУРА ==========
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🍕 Найти блюдо по названию")],
        [KeyboardButton(text="📍 Поделиться геолокацией", request_location=True)],
        [KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True,
)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def update_user(user_id: int, username: str, first_name: str,
                search: int = 0, loc: int = 0, book: int = 0, source: str = None):
    """Добавляет или обновляет данные пользователя в таблице users."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if c.fetchone():
        c.execute("""
            UPDATE users
            SET username = ?, first_name = ?, last_seen = ?,
                search_count = search_count + ?,
                loc_count = loc_count + ?,
                book_count = book_count + ?
            WHERE user_id = ?
        """, (username, first_name, now, search, loc, book, user_id))
    else:
        c.execute("""
            INSERT INTO users (user_id, username, first_name, first_seen, last_seen,
                               search_count, loc_count, book_count, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, first_name, now, now, search, loc, book, source or "direct"))
    conn.commit()
    conn.close()

# ========== КОМАНДА /start ==========
@dp.message(Command("start"))
async def start_cmd(message: Message, command: CommandObject):
    user = message.from_user
    ref = command.args if command.args else None
    source = f"ref_{ref}" if ref else "direct"
    update_user(user.id, user.username, user.first_name, source=source)

    await message.answer(
        "🍽️ *Минск.Цены.Маршрут* – найди любимое блюдо по лучшей цене "
        "и проложи маршрут!\n\n"
        "🔹 Отправь геолокацию (кнопка в меню)\n"
        "🔹 Напиши название блюда (например, «драники», «пицца», «стейк»)\n"
        "🔹 Получи список ресторанов с ценами и маршрутом",
        parse_mode="Markdown",
        reply_markup=main_kb,
    )

# ========== ГЕОЛОКАЦИЯ ==========
@dp.message(lambda msg: msg.location is not None)
async def handle_location(message: Message):
    user_id = message.from_user.id
    lat = message.location.latitude
    lon = message.location.longitude
    user_location[user_id] = (lat, lon)
    update_user(user_id, message.from_user.username, message.from_user.first_name, loc=1)
    await message.answer("✅ Геолокация сохранена! Теперь отправь мне название блюда.")

# ========== ПОИСК БЛЮДА ==========
@dp.message(lambda msg: msg.text and not msg.text.startswith('/') and msg.text not in [
    "🍕 Найти блюдо по названию", "📍 Поделиться геолокацией", "ℹ️ Помощь"
])
async def search_food(message: Message):
    query = message.text.strip().lower()
    user_id = message.from_user.id
    update_user(user_id, message.from_user.username, message.from_user.first_name, search=1)

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT * FROM menu WHERE LOWER(dish) LIKE ? ORDER BY price ASC LIMIT 5", (f"%{query}%",))
    results = c.fetchall()
    conn.close()

    if not results:
        await message.answer("❌ Ничего не найдено. Попробуйте другое блюдо.")
        return

    answer = f"🍽️ *Результаты по запросу «{query}»:*\n\n"
    for i, r in enumerate(results, 1):
        # r = (id, restaurant, dish, price, address, lat, lon)
        restaurant = r[1]
        price = r[3]
        answer += f"{i}. *{restaurant}* — {price} BYN\n"
        if user_id in user_location:
            u_lat, u_lon = user_location[user_id]
            maps_url = (
                f"https://yandex.by/maps/?rtext={u_lat},{u_lon}~{r[5]},{r[6]}&rtt=auto"
            )
            answer += f"   🗺️ [Маршрут от меня]({maps_url})\n"
        else:
            maps_url = f"https://yandex.by/maps/?mode=search&text={r[5]},{r[6]}"
            answer += f"   📍 [Посмотреть на карте]({maps_url})\n"
        answer += "\n"

    if user_id not in user_location:
        answer += "💡 *Отправьте геолокацию, чтобы маршрут строился прямо от вас!*"
    else:
        answer += "🚀 Нажмите на ссылку, чтобы открыть маршрут в Яндекс.Картах."

    await message.answer(answer, parse_mode="Markdown", disable_web_page_preview=True)

# ========== КНОПКИ МЕНЮ ==========
@dp.message(lambda msg: msg.text == "🍕 Найти блюдо по названию")
async def prompt_search(message: Message):
    await message.answer("Введите название блюда (например, драники, пицца, стейк)")

@dp.message(lambda msg: msg.text == "ℹ️ Помощь")
async def help_cmd(message: Message):
    help_text = (
        "📌 <b>Как пользоваться ботом:</b>\n"
        "1. Отправьте свою геолокацию (кнопка в меню).\n"
        "2. Напишите название блюда.\n"
        "3. Бот покажет цены в разных ресторанах и даст ссылку на маршрут.\n\n"
        "⚠️ <b>Дисклеймер:</b> цены предоставлены ресторанами. "
        "Сервис не несёт ответственности за актуальность. "
        "Уточняйте стоимость в заведении."
    )
    await message.answer(help_text, parse_mode="HTML")

# ========== АДМИН-КОМАНДЫ ==========
@dp.message(Command("add"))
async def add_restaurant(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        # /add Васильки|Драники|12.5|ул. Ленина, 10|53.9006|27.5590
        parts = message.text.split(maxsplit=1)[1].split("|")
        if len(parts) != 6:
            raise ValueError
        name, dish, price, address, lat, lon = [p.strip() for p in parts]
        conn = sqlite3.connect(DATABASE)
        conn.execute(
            "INSERT INTO menu (restaurant, dish, price, address, lat, lon) VALUES (?,?,?,?,?,?)",
            (name, dish, float(price), address, float(lat), float(lon)),
        )
        conn.commit()
        conn.close()
        await message.answer(f"✅ Добавлено: {dish} в {name}")
    except Exception:
        await message.answer(
            "❌ Формат:\n`/add Ресторан|Блюдо|Цена|Адрес|lat|lon`\n\n"
            "Пример:\n`/add Васильки|Драники|12.50|ул. Ленина, 10|53.9006|27.5590`",
            parse_mode="Markdown",
        )

@dp.message(Command("del"))
async def delete_restaurant(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        row_id = int(message.text.split()[1])
        conn = sqlite3.connect(DATABASE)
        conn.execute("DELETE FROM menu WHERE id = ?", (row_id,))
        conn.commit()
        conn.close()
        await message.answer(f"🗑️ Запись с id={row_id} удалена")
    except Exception:
        await message.answer("Использование: /del id_записи")

@dp.message(Command("list"))
async def list_rows(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect(DATABASE)
    cur = conn.execute("SELECT id, restaurant, dish, price FROM menu ORDER BY restaurant LIMIT 10")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return await message.answer("Таблица пуста.")
    text = "📋 <b>Первые 10 записей:</b>\n"
    for r in rows:
        text += f"{r[0]}. {r[1]} | {r[2]} | {r[3]} BYN\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("export"))
async def export_csv(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect(DATABASE)
    cur = conn.execute("SELECT restaurant, dish, price, address, lat, lon FROM menu")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return await message.answer("База пуста.")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Ресторан", "Блюдо", "Цена", "Адрес", "lat", "lon"])
    for r in rows:
        writer.writerow(r)
    output.seek(0)
    await message.answer_document(
        BufferedInputFile(output.getvalue().encode("utf-8"), filename="menu_export.csv")
    )

@dp.message(lambda msg: msg.document and msg.from_user.id == ADMIN_ID)
async def import_csv(message: Message):
    if not message.document.file_name.endswith('.csv'):
        return await message.answer("❌ Принимаю только CSV-файл")
    file = await bot.get_file(message.document.file_id)
    file_path = file.file_path
    await bot.download_file(file_path, "temp_import.csv")
    imported = 0
    with open("temp_import.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            conn = sqlite3.connect(DATABASE)
            conn.execute(
                "INSERT INTO menu (restaurant, dish, price, address, lat, lon) VALUES (?,?,?,?,?,?)",
                (row["Ресторан"], row["Блюдо"], float(row["Цена"]),
                 row["Адрес"], float(row["lat"]), float(row["lon"])),
            )
            conn.commit()
            conn.close()
            imported += 1
    await message.answer(f"✅ Импортировано {imported} блюд из {message.document.file_name}")

# ========== СТАТИСТИКА ==========
import datetime  # уже есть в начале, но убедимся

@dp.message(Command("stats"))
async def show_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    # всего пользователей
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (today,))
    dau = c.fetchone()[0]
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (week_ago,))
    wau = c.fetchone()[0]
    month_ago = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (month_ago,))
    mau = c.fetchone()[0]
    c.execute("SELECT SUM(search_count) FROM users")
    searches = c.fetchone()[0] or 0
    c.execute("SELECT SUM(loc_count) FROM users")
    locs = c.fetchone()[0] or 0
    conn.close()

    text = (
        f"📊 <b>Статистика бота:</b>\n"
        f"👥 Всего пользователей: {total}\n"
        f"📅 DAU (сегодня): {dau}\n"
        f"📆 WAU (7 дней): {wau}\n"
        f"📆 MAU (30 дней): {mau}\n"
        f"🔍 Всего поисков: {searches}\n"
        f"📍 Геолокаций отправлено: {locs}"
    )
    await message.answer(text, parse_mode="HTML")

# ========== ЗАПУСК ==========
async def main():
    logging.basicConfig(level=logging.INFO)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
