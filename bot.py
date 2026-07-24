import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

BOT_TOKEN = "8663406888:AAF3891CIjS3tASop0B092IlFN7Pato7DMU"
ADMIN_ID = 5377564835  # ваш Telegram ID
DATABASE = "minsk.db"

# База данных
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS menu (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        restaurant TEXT,
        dish TEXT,
        price REAL,
        address TEXT,
        lat REAL,
        lon REAL
    )""")
    conn.commit()
    conn.close()

init_db()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
user_loc = {}

main_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🍕 Найти блюдо")],
              [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)]],
    resize_keyboard=True
)

# /start
@dp.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer("🍽️ Отправь название блюда или геолокацию.", reply_markup=main_kb)

# Геолокация
@dp.message(lambda m: m.location is not None)
async def loc(message: Message):
    user_loc[message.from_user.id] = (message.location.latitude, message.location.longitude)
    await message.answer("✅ Геолокация сохранена. Напиши название блюда.")

# /add — ТОЛЬКО для админа
@dp.message(Command("add"))
async def add_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, rest, dish, price, addr, lat, lon = message.text.split(maxsplit=1)[1].split("|")
        conn = sqlite3.connect(DATABASE)
        conn.execute("INSERT INTO menu (restaurant, dish, price, address, lat, lon) VALUES (?,?,?,?,?,?)",
                     (rest.strip(), dish.strip(), float(price), addr.strip(), float(lat), float(lon)))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Добавлено: {dish.strip()}")
    except:
        await message.answer("Формат: /add Ресторан|Блюдо|Цена|Адрес|lat|lon")

# ПОИСК — ловит ВСЁ, кроме команд и кнопок
@dp.message()
async def search(message: Message):
    # Игнорируем кнопки
    if message.text in ["🍕 Найти блюдо", "📍 Отправить геолокацию"]:
        return
    # Игнорируем команды (начинаются с /)
    if message.text and message.text.startswith('/'):
        return

    query = message.text.strip().lower() if message.text else ""
    if not query:
        return

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT * FROM menu WHERE LOWER(dish) LIKE ? ORDER BY price LIMIT 5", (f"%{query}%",))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await message.answer(f"❌ По запросу «{query}» ничего не найдено.")
        return

    answer = f"🍽️ *Результаты по запросу «{query}»:*\n\n"
    for i, r in enumerate(rows, 1):
        answer += f"{i}. *{r[1]}* — {r[3]} BYN\n"
        if message.from_user.id in user_loc:
            u_lat, u_lon = user_loc[message.from_user.id]
            maps_url = f"https://yandex.by/maps/?rtext={u_lat},{u_lon}~{r[5]},{r[6]}&rtt=auto"
            answer += f"   🗺️ [Маршрут от меня]({maps_url})\n"
        else:
            maps_url = f"https://yandex.by/maps/?mode=search&text={r[5]},{r[6]}"
            answer += f"   📍 [Посмотреть на карте]({maps_url})\n"
        answer += "\n"
    await message.answer(answer, parse_mode="Markdown", disable_web_page_preview=True)

async def main():
    logging.basicConfig(level=logging.INFO)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())