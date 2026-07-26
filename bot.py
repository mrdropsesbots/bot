import os
import datetime
import asyncio
import logging
import csv
import io
import html
import secrets
import aiosqlite
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
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_ОТ_BOTFATHER")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "change-me-in-production")

# Если диск подключен — используем /data, иначе локальный файл (для тестов)
DATABASE = "/data/minsk.db" if Path("/data").exists() else "minsk.db"

RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_URL = f"https://{RENDER_HOST}/webhook" if RENDER_HOST else None
PORT = int(os.getenv("PORT", "10000"))

# ========== БАЗА ДАННЫХ ==========
async def get_conn():
    return await aiosqlite.connect(DATABASE)

async def init_db():
    # Создаём папку, если нужно (для локальных тестов)
    Path(DATABASE).parent.mkdir(parents=True, exist_ok=True)
    conn = await get_conn()
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS menu (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        restaurant TEXT NOT NULL,
        dish TEXT NOT NULL,
        price REAL NOT NULL,
        address TEXT,
        lat REAL,
        lon REAL
    )""")
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        search_count INTEGER DEFAULT 0,
        loc_count INTEGER DEFAULT 0,
        source TEXT DEFAULT 'direct',
        lat REAL,
        lon REAL
    )""")
    # Индекс для быстрого поиска по блюдам
    await conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_dish_lower ON menu(LOWER(dish))
    """)
    await conn.commit()
    await conn.close()

# ========== БОТ ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🍕 Найти блюдо")],
        [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
        [KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True,
)

async def update_user(user_id, username, first_name, search=0, loc=0, source="direct", lat=None, lon=None):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = await get_conn()
    c = await conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    row = await c.fetchone()
    if row:
        await conn.execute("""
            UPDATE users SET username = ?, first_name = ?, last_seen = ?,
                search_count = search_count + ?,
                loc_count = loc_count + ?,
                lat = COALESCE(?, lat),
                lon = COALESCE(?, lon)
            WHERE user_id = ?
        """, (username, first_name, now, search, loc, lat, lon, user_id))
    else:
        await conn.execute("""
            INSERT INTO users (user_id, username, first_name, first_seen, last_seen,
                               search_count, loc_count, source, lat, lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, first_name, now, now, search, loc, source, lat, lon))
    await conn.commit()
    await conn.close()

# ========== TELEGRAM КОМАНДЫ ==========
@dp.message(Command("start"))
async def start_cmd(message: Message, command: CommandObject):
    user = message.from_user
    ref = command.args if command.args else None
    source = f"ref_{ref}" if ref else "direct"
    await update_user(user.id, user.username or "", user.first_name or "", 0, 0, source)
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
        conn = await get_conn()
        await conn.execute(
            "INSERT INTO menu (restaurant, dish, price, address, lat, lon) VALUES (?,?,?,?,?,?)",
            (name, dish, float(price), address, float(lat), float(lon)),
        )
        await conn.commit()
        await conn.close()
        await message.answer(f"✅ Добавлено: {dish} в {name}")
    except Exception as e:
        logging.error(f"/add error: {e}")
        await message.answer(
            "❌ Формат:\n`/add Ресторан|Блюдо|Цена|Адрес|lat|lon`",
            parse_mode="Markdown",
        )

@dp.message(Command("del"))
async def del_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        row_id = int(message.text.split()[1])
        conn = await get_conn()
        await conn.execute("DELETE FROM menu WHERE id = ?", (row_id,))
        await conn.commit()
        await conn.close()
        await message.answer(f"🗑️ Удалено id={row_id}")
    except Exception:
        await message.answer("Использование: /del id")

@dp.message(Command("list"))
async def list_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = await get_conn()
    rows = await conn.execute_fetchall(
        "SELECT id, restaurant, dish, price FROM menu ORDER BY id DESC LIMIT 20"
    )
    await conn.close()
    if not rows:
        return await message.answer("База пуста.")
    text = "📋 <b>Последние 20:</b>\n"
    for r in rows:
        text += f"{r[0]}. {r[1]} | {r[2]} | {r[3]} BYN\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("check"))
async def check_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = await get_conn()
    rows = await conn.execute_fetchall(
        "SELECT * FROM menu ORDER BY id DESC LIMIT 5"
    )
    await conn.close()
    text = "📋 <b>Последние 5:</b>\n"
    for r in rows:
        text += f"<code>{r}</code>\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("export"))
async def export_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = await get_conn()
    rows = await conn.execute_fetchall(
        "SELECT restaurant, dish, price, address, lat, lon FROM menu"
    )
    await conn.close()
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
    conn = await get_conn()
    c = await conn.execute("SELECT COUNT(*) FROM users")
    total = (await c.fetchone())[0]
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    c = await conn.execute("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (today,))
    dau = (await c.fetchone())[0]
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    c = await conn.execute("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (week_ago,))
    wau = (await c.fetchone())[0]
    c = await conn.execute("SELECT SUM(search_count), SUM(loc_count) FROM users")
    searches, locs = await c.fetchone()
    await conn.close()
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
    lat = message.location.latitude
    lon = message.location.longitude
    await update_user(uid, message.from_user.username or "", message.from_user.first_name or "", 0, 1, lat=lat, lon=lon)
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
        "3. Получи цены и маршрут.",
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
    await update_user(uid, message.from_user.username or "", message.from_user.first_name or "", 1, 0)

    conn = await get_conn()
    rows = await conn.execute_fetchall(
        "SELECT id, restaurant, dish, price, address, lat, lon FROM menu "
        "WHERE LOWER(dish) LIKE ? ORDER BY price ASC LIMIT 5",
        (f"%{query}%",)
    )
    await conn.close()

    if not rows:
        await message.answer(f"❌ По запросу «{query}» ничего не найдено.")
        return

    # Получаем сохранённую геолокацию пользователя из БД
    conn = await get_conn()
    c = await conn.execute("SELECT lat, lon FROM users WHERE user_id = ?", (uid,))
    user_loc = await c.fetchone()
    await conn.close()

    answer = f"🍽️ *Результаты по «{query}»:*\n\n"
    for r in rows:
        rid, restaurant, dish, price, address, lat, lon = r
        answer += f"🍴 *{restaurant}* — {dish} {price} BYN\n"
        if user_loc and user_loc[0] is not None:
            u_lat, u_lon = user_loc
            url = f"https://yandex.by/maps/?rtext={u_lat},{u_lon}~{lat},{lon}&rtt=auto"
            answer += f"   🗺️ [Маршрут]({url})\n"
        else:
            url = f"https://yandex.by/maps/?mode=search&text={lat},{lon}"
            answer += f"   📍 [На карте]({url})\n"
        answer += "\n"

    if not user_loc or user_loc[0] is None:
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
<title>Админка</title>
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
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.pagination{margin-top:15px;text-align:center}
.pagination a{color:#3498db;text-decoration:none;margin:0 10px}
@media(max-width:600px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<h1>🍽️ Админка</h1>
<div class="card">
<h3>➕ Добавить блюдо</h3>
<form method="post" action="/admin/add">
<input type="hidden" name="key" value="{key}">
<div class="grid">
<input name="restaurant" placeholder="Ресторан" required>
<input name="dish" placeholder="Блюдо" required>
<input name="price" placeholder="Цена (BYN)" type="number" step="0.01" required>
<input name="address" placeholder="Адрес">
<input name="lat" placeholder="Широта" type="number" step="any" required>
<input name="lon" placeholder="Долгота" type="number" step="any" required>
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
<div class="pagination">{pagination}</div>
<p style="color:#888;font-size:12px">Всего: {count} | Страница {page}</p>
</div>
</body>
</html>"""

def check_admin_key(request):
    """Проверяет секретный ключ админки."""
    key = request.query.get("key", "")
    if not secrets.compare_digest(key, ADMIN_SECRET_KEY):
        raise web.HTTPForbidden(text="Access denied")
    return key

async def admin_page(request):
    key = check_admin_key(request)
    
    page = int(request.query.get("page", 1))
    per_page = 50
    offset = (page - 1) * per_page
    
    conn = await get_conn()
    rows = await conn.execute_fetchall(
        "SELECT id, restaurant, dish, price, address, lat, lon FROM menu ORDER BY id DESC LIMIT ? OFFSET ?",
        (per_page, offset)
    )
    c = await conn.execute("SELECT COUNT(*) FROM menu")
    total = (await c.fetchone())[0]
    await conn.close()
    
    rows_html = ""
    for r in rows:
        rows_html += f"<tr><td>{r[0]}</td><td>{html.escape(str(r[1]))}</td><td>{html.escape(str(r[2]))}</td>"
        rows_html += f"<td>{r[3]}</td><td>{html.escape(str(r[4] or ''))}</td><td>{r[5]}</td><td>{r[6]}</td>"
        rows_html += f'<td><form method="post" action="/admin/del" style="display:inline"><input type="hidden" name="key" value="{key}"><input type="hidden" name="id" value="{r[0]}"><button class="btn-del" type="submit">Удалить</button></form></td></tr>'
    if not rows:
        rows_html = '<tr><td colspan="8" style="text-align:center;color:#888">Нет записей</td></tr>'
    
    # Пагинация
    total_pages = (total + per_page - 1) // per_page
    pagination = ""
    if page > 1:
        pagination += f'<a href="/admin?key={key}&page={page-1}">← Назад</a>'
    if page < total_pages:
        pagination += f'<a href="/admin?key={key}&page={page+1}">Вперёд →</a>'
    
    html_page = ADMIN_HTML.format(
        rows=rows_html, 
        count=total, 
        page=page,
        key=key,
        pagination=pagination
    )
    return web.Response(text=html_page, content_type="text/html")

async def admin_add(request):
    check_admin_key(request)
    data = await request.post()
    try:
        conn = await get_conn()
        await conn.execute(
            "INSERT INTO menu (restaurant, dish, price, address, lat, lon) VALUES (?,?,?,?,?,?)",
            (data["restaurant"], data["dish"], float(data["price"]), data.get("address", ""), float(data["lat"]), float(data["lon"])),
        )
        await conn.commit()
        await conn.close()
    except Exception as e:
        logging.error(f"Admin add error: {e}")
    key = request.query.get("key", "")
    raise web.HTTPFound(f"/admin?key={key}")

async def admin_del(request):
    check_admin_key(request)
    data = await request.post()
    try:
        conn = await get_conn()
        await conn.execute("DELETE FROM menu WHERE id = ?", (int(data["id"]),))
        await conn.commit()
        await conn.close()
    except Exception as e:
        logging.error(f"Admin del error: {e}")
    key = request.query.get("key", "")
    raise web.HTTPFound(f"/admin?key={key}")

async def health(request):
    return web.Response(text="OK")

# ========== ЗАПУСК ==========
async def on_startup(bot: Bot):
    await init_db()
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    if WEBHOOK_URL:
        await bot.delete_webhook()

dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)

async def run_polling():
    await init_db()
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
