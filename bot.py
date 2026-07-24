import datetime
import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

BOT_TOKEN = "8663406888:AAF3891CIjS3tASop0B092IlFN7Pato7DMU"
ADMIN_ID = 5377564835  # ваш ID
DATABASE = "minsk.db"

# Инициализация БД
conn = sqlite3.connect(DATABASE)
conn.execute("""
CREATE TABLE IF NOT EXISTS menu (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant TEXT, dish TEXT, price REAL,
    address TEXT, lat REAL, lon REAL
)""")
conn.commit()
conn.close()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
user_loc = {}

main_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="🍕 Найти блюдо")],
    [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
], resize_keyboard=True)

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("Я отладочный бот. Пиши что угодно.", reply_markup=main_kb)

@dp.message(Command("add"))
async def add(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        _, rest, dish, price, addr, lat, lon = message.text.split(maxsplit=1)[1].split("|")
        conn = sqlite3.connect(DATABASE)
        conn.execute("INSERT INTO menu VALUES (NULL,?,?,?,?,?,?)",
                     (rest.strip(), dish.strip(), float(price), addr.strip(), float(lat), float(lon)))
        conn.commit(); conn.close()
        await message.answer("✅ Добавлено")
    except:
        await message.answer("Формат: /add Ресторан|Блюдо|Цена|Адрес|lat|lon")

@dp.message()
async def debug_all(message: Message):
    # Игнорируем только кнопки, чтобы не мешали
    if message.text in ["🍕 Найти блюдо", "📍 Отправить геолокацию"]:
        if message.text == "🍕 Найти блюдо":
            await message.answer("Введите название блюда (отладка)")
        return

    user_text = message.text if message.text else "нет текста"
    # Сохраняем геолокацию, если есть
    if message.location:
        user_loc[message.from_user.id] = (message.location.latitude, message.location.longitude)
        await message.answer(f"📍 Гео получена: {message.location.latitude}, {message.location.longitude}")
        return

    # Пытаемся искать
    query = user_text.strip().lower()
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT * FROM menu WHERE LOWER(dish) LIKE ? LIMIT 5", (f"%{query}%",))
    rows = c.fetchall()
    conn.close()

    answer = f"📩 Получил: '{user_text}'\n🔍 Ищу: '%{query}%'\n"
    if not rows:
        answer += "❌ Ничего не найдено в БД."
    else:
        answer += "✅ Найдено:\n"
        for r in rows:
            answer += f"• {r[1]} – {r[2]} – {r[3]} BYN\n"
    await message.answer(answer)

@dp.message(lambda m: m.text == "/stats")
async def stats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("Статистика временно отключена для отладки")

async def main():
    logging.basicConfig(level=logging.INFO)
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот запущен в режиме отладки")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())