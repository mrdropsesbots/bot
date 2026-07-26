import os
import datetime
import asyncio
import logging
import json
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ========== НАСТРОЙКИ ПРЯМО В КОДЕ ==========
BOT_TOKEN = "8663406888:AAF3891CIjS3tASop0B092IlFN7Pato7DMU"  # ← замени на свой токен
ADMIN_ID = 5377564835                            # ← замени на свой Telegram ID

# Render сам подставит URL, ничего менять не нужно
RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_URL = f"https://{RENDER_HOST}/webhook" if RENDER_HOST else None
PORT = 10000  # Render free tier

MENU_FILE = "menu.json"
USERS_FILE = "users.json"

# ========== JSON DB ==========
def load_json(path):
    if not Path(path).exists():
        return [] if path == MENU_FILE else {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
    users = load_json(USERS_FILE)
    uid = str(user_id)
    if uid in users:
        u = users[uid]
        u["username"] = username
        u["first_name"] = first_name
        u["last_seen"] = now
        u["search_count"] = u.get("search_count", 0) + search
        u["loc_count"] = u.get("loc_count", 0) + loc
    else:
        users[uid] = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "first_seen": now,
            "last_seen": now,
            "search_count": search,
            "loc_count": loc,
            "source": source,
        }
    save_json(USERS_FILE, users)

# ========== КОМАНДЫ ==========
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
        "Админ: `/add`, `/del`, `/list`",
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
        menu = load_json(MENU_FILE)
        new_id = max([m.get("id", 0) for m in menu], default=0) + 1
        menu.append({
            "id": new_id,
            "restaurant": name,
            "dish": dish,
            "price": float(price),
            "address": address,
            "lat": float(lat),
            "lon": float(lon),
        })
        save_json(MENU_FILE, menu)
        await message.answer(f"✅ Добавлено id={new_id}: {dish} в {name}")
    except Exception as e:
        logging.error(f"/add error: {e}")
        await message.answer(
            "❌ Формат:\n`/add Ресторан|Блюдо|Цена|Адрес|lat|lon`\n\n"
            "Пример:\n`/add Васильки|Драники|12.50|ул. Ленина, 10|53.9006|27.5590`",
            parse_mode="Markdown",
        )

@dp.message(Command("del"))
async def del_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        row_id = int(message.text.split()[1])
        menu = load_json(MENU_FILE)
        new_menu = [m for m in menu if m.get("id") != row_id]
        if len(new_menu) == len(menu):
            return await message.answer("❌ Запись не найдена")
        save_json(MENU_FILE, new_menu)
        await message.answer(f"🗑️ Удалено id={row_id}")
    except Exception:
        await message.answer("Использование: /del id")

@dp.message(Command("list"))
async def list_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    menu = load_json(MENU_FILE)
    if not menu:
        return await message.answer("📋 Файл пуст.")
    text = "📋 <b>Записи:</b>\n"
    for m in menu[-20:]:
        text += f"{m['id']}. {m['restaurant']} | {m['dish']} | {m['price']} BYN\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("check"))
async def check_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    menu = load_json(MENU_FILE)
    text = f"📋 <b>Всего:</b> {len(menu)}\n\n<b>Последние 5:</b>\n"
    for m in menu[-5:]:
        text += f"<code>{m}</code>\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    users = load_json(USERS_FILE)
    total = len(users)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    dau = sum(1 for u in users.values() if u.get("last_seen", "").startswith(today))
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    wau = sum(1 for u in users.values() if u.get("last_seen", "") >= week_ago)
    searches = sum(u.get("search_count", 0) for u in users.values())
    locs = sum(u.get("loc_count", 0) for u in users.values())
    text = (
        f"📊 <b>Статистика:</b>\n"
        f"👥 Всего: {total}\n"
        f"📅 DAU: {dau} | WAU: {wau}\n"
        f"🔍 Поисков: {searches}\n"
        f"📍 Геолокаций: {locs}"
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
    update_user(uid, message.from_user.username or "", message.from_user.first_name or "", 1, 0)

    menu = load_json(MENU_FILE)
    results = [m for m in menu if query in m.get("dish", "").lower()]
    results.sort(key=lambda x: x.get("price", 9999))
    results = results[:5]

    if not results:
        await message.answer(f"❌ По запросу «{query}» ничего не найдено.")
        return

    answer = f"🍽️ *Результаты по «{query}»:*\n\n"
    for m in results:
        answer += f"🍴 *{m['restaurant']}* — {m['dish']} {m['price']} BYN\n"
        if uid in user_location:
            u_lat, u_lon = user_location[uid]
            url = f"https://yandex.by/maps/?rtext={u_lat},{u_lon}~{m['lat']},{m['lon']}&rtt=auto"
            answer += f"   🗺️ [Маршрут]({url})\n"
        else:
            url = f"https://yandex.by/maps/?mode=search&text={m['lat']},{m['lon']}"
            answer += f"   📍 [На карте]({url})\n"
        answer += "\n"

    if uid not in user_location:
        answer += "💡 *Отправь геолокацию, чтобы построить маршрут от тебя!*"
    else:
        answer += "🚀 Нажми ссылку для Яндекс.Карт."
    await message.answer(answer, parse_mode="Markdown", disable_web_page_preview=True)

# ========== WEBHOOK / POLLING ==========
async def health(request):
    return web.Response(text="OK")

async def on_startup(bot: Bot):
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        logging.info(f"Webhook: {WEBHOOK_URL}")

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
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
        setup_application(app, dp, bot=bot)
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        asyncio.run(run_polling())

if __name__ == "__main__":
    main()
