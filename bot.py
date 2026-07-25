import os
import datetime
import asyncio
import logging
import csv
import io
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8080"))
DATABASE = os.getenv("DATABASE_PATH", "minsk.db")

# ========== БАЗА ДАННЫХ ==========
def get_conn():
    return sqlite3.connect(DATABASE)

def init_db():
    Path(DATABASE).parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    c = conn.cursor()
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
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        search_count INTEGER DEFAULT 0,
        loc_count INTEGER DEFAULT 0,
        source TEXT DEFAULT 'direct'
    )""")
    conn.commit()
    conn.close()

init_db()

# ========== БОТ ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
user_location = {}

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🍕 Найти блюдо")],
        [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
        [KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True,
)

def update_user(user_id, username, first_name, search=0, loc=0, source="direct"):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if c.fetchone():
        c.execute("""
            UPDATE users SET username = ?, first_name = ?, last_seen = ?,
                search_count = search_count + ?,
                loc_count = loc_count + ?
            WHERE user_id = ?
        """, (username, first_name, now, search, loc, user_id))
    else:
        c.execute("""
            INSERT INTO users (user_id, username, first_name, first_seen, last_seen,
                               search_count, loc_count, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, first_name, now, now, search, loc, source))
    conn.commit()
    conn.close()

# ========== TELEGRAM КОМАНДЫ ==========
@dp.message(Command("start"))
async def start_cmd(message: Message, command: CommandObject):
    user = message.from_user
    ref = command.args if command.args else None
    source = f"ref_{ref}" if ref else "direct"
    update_user(user.id, user.username or "", user.first_name or "", 0, 0, source)
    await message.answer(
        "🍽️ *Минск.Цены.Маршрут*\n\n"
        "🔹 Отправь геолокацию\n"
        "🔹 Напиши название блюда (драники, пицца, стейк)\n"
        "🔹 Получи цены и маршрут\n\n"
        "Админка: открой /admin в браузере",
        parse_mode="Markdown",
        reply_markup=main_kb,
    )

@dp.message(Command("add"))
async def add_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = message.text.split(maxsplit=1)[1].split("|")
        if len(parts) != 6:
            raise ValueError
        name, dish, price, address, lat, lon = [p.strip() for p in parts]
        conn = get_conn()
        conn.execute(
            "INSERT INTO menu (restaurant, dish, price, address, lat, lon) VALUES (?,?,?,?,?,?)",
            (name, dish, float(price), address, float(lat), float(lon)),
        )
        conn.commit()
        conn.close()
        await message.answer(f"✅ Добавлено: {dish} в {name}")
    except Exception as e:
        logging.error(f"/add error: {e}")
        await message.answer("❌ Формат:\n`/add Ресторан|Блюдо|Цена|Адрес|lat|lon`", parse_mode="Markdown")

@dp.message(Command("del"))
async def del_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        row_id = int(message.text.split()[1])
        conn = get_conn()
        conn.execute("DELETE FROM menu WHERE id = ?", (row_id,))
        conn.commit()
        conn.close()
        await message.answer(f"🗑️ Удалено id={row_id}")
    except Exception:
        await message.answer("Использование: /del id")

@dp.message(Command("list"))
async def list_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = get_conn()
    rows = conn.execute("SELECT id, restaurant, dish, price FROM menu ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    if not rows:
        return await message.answer("База пуста.")
    text = "📋 <b>Последние 20 записей:</b>\n"
    for r in rows:
        text += f"{r[0]}. {r[1]} | {r[2]} | {r[3]} BYN\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("check"))
async def check_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = get_conn()
    c = conn.cursor()
    c.execute("PRAGMA table_info(menu)")
    cols = [col[1] for col in c.fetchall()]
    rows = conn.execute("SELECT * FROM menu ORDER BY id DESC LIMIT 5").fetchall()
    conn.close()
    text = f"📋 Колонки: {', '.join(cols)}\n\n<b>Последние 5:</b>\n"
    for r in rows:
        text += f"<code>{r}</code>\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("export"))
async def export_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = get_conn()
    rows = conn.execute("SELECT restaurant, dish, price, address, lat, lon FROM menu").fetchall()
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

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (today,))
    dau = c.fetchone()[0]
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (week_ago,))
    wau = c.fetchone()[0]
    c.execute("SELECT SUM(search_count), SUM(loc_count) FROM users")
    searches, locs = c.fetchone()
    conn.close()
    text = (
        f"📊 <b>Статистика:</b>\n"
        f"👥 Всего: {total}\n"
        f"📅 DAU: {dau} | WAU: {wau}\n"
        f"🔍 Поисков: {searches or 0}\n"
        f"📍 Геолокаций: {locs or 0}"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(lambda msg: msg.location is not None)
async def handle_location(message: Message):
    uid = message.from_user.id
    user_location[uid] = (message.location.latitude, message.location.longitude)
    update_user(uid, message.from_user.username or "", message.from_user.first_name or "", 0, 1)
    await message.answer("✅ Геолокация сохранена! Теперь напиши название блюда.")

@dp.message(lambda msg: msg.text == "🍕 Найти блюдо")
async def prompt_search(message: Message):
    await message.answer("Введи название блюда (драники, пицца, стейк)")

@dp.message(lambda msg: msg.text == "ℹ️ Помощь")
async def help_cmd(message: Message):
    await message.answer(
        "📌 <b>Как пользоваться:</b>\n"
        "1. Отправь геолокацию.\n"
        "2. Напиши название блюда.\n"
        3. Получи цены и маршрут.",
        parse_mode="HTML",
    )

@dp.message()
async def search_food(message: Message):
    if not message.text or message.text.startswith('/'):
        return
    if message.text in ["🍕 Найти блюдо", "📍 Отправить геолокацию", "ℹ️ Помощь"]:
        return

    query = message.text.strip().lower()
    if not query:
        return

    uid = message.from_user.id
    update_user(uid, message.from_user.username or "", message.from_user.first_name or "", 1, 0)

    conn = get_conn()
    rows = conn.execute(
        "SELECT id, restaurant, dish, price, address, lat, lon "
        "FROM menu WHERE LOWER(dish) LIKE ? ORDER BY price ASC LIMIT 5",
        (f"%{query}%",)
    ).fetchall()
    conn.close()

    if not rows:
        await message.answer(f"❌ По запросу «{query}» ничего не найдено.")
        return

    answer = f"🍽️ *Результаты по «{query}»:*\n\n"
    for r in rows:
        rid, restaurant, dish, price, address, lat, lon = r
        answer += f"🍴 *{restaurant}* — {dish} {price} BYN\n"
        if uid in user_location:
            u_lat, u_lon = user_location[uid]
            url = f"https://yandex.by/maps/?rtext={u_lat},{u_lon}~{lat},{lon}&rtt=auto"
            answer += f"   🗺️ [Маршрут]({url})\n"
        else:
            url = f"https://yandex.by/maps/?mode=search&text={lat},{lon}"
            answer += f"   📍 [На карте]({url})\n"
        answer += "\n"

    if uid not in user_location:
        answer += "💡 *Отправь геолокацию, чтобы построить маршрут от тебя!*"
    else:
        answer += "🚀 Нажми ссылку для Яндекс.Карт."
    await message.answer(answer, parse_mode="Markdown", disable_web_page_preview=True)

# ========== ВЕБ-АДМИНКА ==========
ADMIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Админка — Минск.Цены</title>
<style>
*{box-sizing:border-box;font-family:system-ui,-apple-system,sans-serif}
body{max-width:900px;margin:40px auto;padding:0 20px;background:#f5f5f5;color:#333}
h1{color:#2c3e50}
.card{background:#fff;padding:20px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.08);margin-bottom:20px}
input,button{font-size:16px;padding:10px 14px;border-radius:8px;border:1px solid #ddd}
input{width:100%;margin-bottom:10px}
input:focus{outline:none;border-color:#3498db}
button{background:#3498db;color:#fff;border:none;cursor:pointer}
button:hover{background:#2980b9}
.btn-del{background:#e74c3c;padding:6px 12px;font-size:14px}
.btn-del:hover{background:#c0392b}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
th,td{padding:12px;text-align:left;border-bottom:1px solid #eee}
th{background:#2c3e50;color:#fff}
tr:hover{background:#f8f9fa}
.small{color:#888;font-size:12px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media(max-width:600px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<h1>🍽️ Админка — Минск.Цены.Маршрут</h1>

<div class="card">
<h3>➕ Добавить блюдо</h3>
<form method="post" action="/admin/add">
<div class="grid">
<input name="restaurant" placeholder="Ресторан" required>
<input name="dish" placeholder="Блюдо" required>
<input name="price" placeholder="Цена (BYN)" type="number" step="0.01" required>
<input name="address" placeholder="Адрес">
<input name="lat" placeholder="Широта (lat)" type="number" step="any" required>
<input name="lon" placeholder="Долгота (lon)" type="number" step="any" required>
</div>
<button type="submit">Добавить</button>
</form>
</div>

<div class="card">
<h3>📋 Все записи</h3>
<table>
<tr><th>ID</th><th>Ресторан</th><th>Блюдо</th><th>Цена</th><th>Адрес</th><th>Lat</th><th>Lon</th><th></th></tr>
{rows}
</table>
<p class="small">Всего записей: {count}</p>
</div>
</body>
</html>"""

async def admin_page(request):
    conn = get_conn()
    rows = conn.execute("SELECT id, restaurant, dish, price, address, lat, lon FROM menu ORDER BY id DESC").fetchall()
    conn.close()
    
    rows_html = ""
    for r in rows:
        rows_html += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td><td>{r[5]}</td><td>{r[6]}</td>"
        rows_html += f'<td><form method="post" action="/admin/del" style="display:inline"><input type="hidden" name="id" value="{r[0]}"><button class="btn-del" type="submit">Удалить</button></form></td></tr>'
    
    if not rows:
        rows_html = '<tr><td colspan="8" style="text-align:center;color:#888">Нет записей</td></tr>'
    
    html = ADMIN_HTML.format(rows=rows_html, count=len(rows))
    return web.Response(text=html, content_type="text/html")

async def admin_add(request):
    data = await request.post()
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO menu (restaurant, dish, price, address, lat, lon) VALUES (?,?,?,?,?,?)",
            (data["restaurant"], data["dish"], float(data["price"]), data.get("address", ""), float(data["lat"]), float(data["lon"])),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Admin add error: {e}")
    raise web.HTTPFound("/admin")

async def admin_del(request):
    data = await request.post()
    try:
        conn = get_conn()
        conn.execute("DELETE FROM menu WHERE id = ?", (int(data["id"]),))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Admin del error: {e}")
    raise web.HTTPFound("/admin")

async def health(request):
    return web.Response(text="OK")

# ========== ЗАПУСК ==========
async def on_startup(bot: Bot):
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    if WEBHOOK_URL:
        await bot.delete_webhook()

dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)

async def run_polling():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

def main():
    logging.basicConfig(level=logging.INFO)
    if WEBHOOK_URL:
        app = web.Application()
        app.router.add_get("/health", health)
        app.router.add_get("/admin", admin_page)
        app.router.add_post("/admin/add", admin_add)
        app.router.add_post("/admin/del", admin_del)
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
        setup_application(app, dp, bot=bot)
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        asyncio.run(run_polling())

if __name__ == "__main__":
    main()
