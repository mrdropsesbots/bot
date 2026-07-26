import os
import datetime
import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ========== НАСТРОЙКИ ПРЯМО В КОДЕ ==========
BOT_TOKEN = "8663406888:AAF3891CIjS3tASop0B092IlFN7Pato7DMU"          # ← замени
ADMIN_ID = 5377564835                                # ← замени на свой Telegram ID
FIREBASE_URL = "https://menu-3328b-default-rtdb.europe-west1.firebasedatabase.app"  # ← замени (без слэша в конце)

RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_URL = f"https://{RENDER_HOST}/webhook" if RENDER_HOST else None
PORT = 10000

# ========== FIREBASE REST CLIENT ==========
_http_session = None

class FirebaseDB:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def get(self, path):
        async with self.session.get(f"{FIREBASE_URL}/{path}.json") as resp:
            if resp.status == 200:
                data = await resp.json()
                return data if data is not None else {}
            logging.error(f"Firebase GET {path}: {resp.status}")
            return {}

    async def put(self, path, data):
        async with self.session.put(f"{FIREBASE_URL}/{path}.json", json=data) as resp:
            return resp.status == 200

    async def patch(self, path, data):
        async with self.session.patch(f"{FIREBASE_URL}/{path}.json", json=data) as resp:
            return resp.status == 200

    async def delete(self, path):
        async with self.session.delete(f"{FIREBASE_URL}/{path}.json") as resp:
            return resp.status == 200

    # --- MENU ---
    async def get_menu(self):
        raw = await self.get("menu")
        if not raw:
            return []
        return [{"id": int(k), **v} for k, v in raw.items() if k.isdigit()]

    async def add_menu(self, restaurant, dish, price, address, lat, lon):
        menu = await self.get_menu()
        new_id = max([m["id"] for m in menu], default=0) + 1
        await self.put(f"menu/{new_id}", {
            "restaurant": restaurant,
            "dish": dish,
            "price": price,
            "address": address,
            "lat": lat,
            "lon": lon,
        })
        return new_id

    async def delete_menu(self, row_id):
        return await self.delete(f"menu/{row_id}")

    # --- USERS ---
    async def update_user(self, user_id, username, first_name, search=0, loc=0, source="direct"):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        uid = str(user_id)
        user = await self.get(f"users/{uid}")
        if user:
            await self.patch(f"users/{uid}", {
                "username": username,
                "first_name": first_name,
                "last_seen": now,
                "search_count": user.get("search_count", 0) + search,
                "loc_count": user.get("loc_count", 0) + loc,
            })
        else:
            await self.put(f"users/{uid}", {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "first_seen": now,
                "last_seen": now,
                "search_count": search,
                "loc_count": loc,
                "source": source,
            })

    async def get_stats(self):
        users = await self.get("users")
        if not users:
            return 0, 0, 0, 0, 0
        total = len(users)
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        dau = sum(1 for u in users.values() if str(u.get("last_seen", "")).startswith(today))
        week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        wau = sum(1 for u in users.values() if str(u.get("last_seen", "")) >= week_ago)
        searches = sum(u.get("search_count", 0) for u in users.values())
        locs = sum(u.get("loc_count", 0) for u in users.values())
        return total, dau, wau, searches, locs

def get_db():
    return FirebaseDB(_http_session)

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

@dp.message(Command("start"))
async def start_cmd(message: Message, command: CommandObject):
    user = message.from_user
    ref = command.args if command.args else None
    source = f"ref_{ref}" if ref else "direct"
    await get_db().update_user(user.id, user.username or "", user.first_name or "", 0, 0, source)
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
            raise ValueError(f"Ожидалось 6 частей, получено {len(parts)}")
        name, dish, price, address, lat, lon = [p.strip() for p in parts]
        new_id = await get_db().add_menu(name, dish, float(price), address, float(lat), float(lon))
        await message.answer(f"✅ Добавлено id={new_id}: {dish} в {name}")
    except Exception as e:
        logging.error(f"/add error: {e}")
        await message.answer(
            f"❌ Ошибка: {e}\n\nФормат:\n`/add Ресторан|Блюдо|Цена|Адрес|lat|lon`",
            parse_mode="Markdown",
        )

@dp.message(Command("del"))
async def del_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        row_id = int(message.text.split()[1])
        ok = await get_db().delete_menu(row_id)
        await message.answer(f"🗑️ Удалено id={row_id}" if ok else "❌ Не найдено")
    except Exception:
        await message.answer("Использование: /del id")

@dp.message(Command("list"))
async def list_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    menu = await get_db().get_menu()
    if not menu:
        return await message.answer("База пуста.")
    text = "📋 <b>Последние 20:</b>\n"
    for m in menu[-20:]:
        text += f"{m['id']}. {m['restaurant']} | {m['dish']} | {m['price']} BYN\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("check"))
async def check_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    menu = await get_db().get_menu()
    text = f"📋 <b>Всего:</b> {len(menu)}\n\n<b>Последние 5:</b>\n"
    for m in menu[-5:]:
        text += f"<code>{m}</code>\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    total, dau, wau, searches, locs = await get_db().get_stats()
    await message.answer(
        f"📊 <b>Статистика:</b>\n"
        f"👥 Всего: {total}\n"
        f"📅 DAU: {dau} | WAU: {wau}\n"
        f"🔍 Поисков: {searches}\n"
        f"📍 Геолокаций: {locs}",
        parse_mode="HTML",
    )

@dp.message(lambda msg: msg.location is not None)
async def handle_location(message: Message):
    uid = message.from_user.id
    user_location[uid] = (message.location.latitude, message.location.longitude)
    await get_db().update_user(uid, message.from_user.username or "", message.from_user.first_name or "", 0, 1)
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
    await get_db().update_user(uid, message.from_user.username or "", message.from_user.first_name or "", 1, 0)

    menu = await get_db().get_menu()
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

# ========== WEBHOOK ==========
async def health(request):
    return web.Response(text="OK")

async def on_startup(bot: Bot):
    global _http_session
    _http_session = aiohttp.ClientSession()
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    if _http_session:
        await _http_session.close()
    if WEBHOOK_URL:
        await bot.delete_webhook()

dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)

async def run_polling():
    global _http_session
    _http_session = aiohttp.ClientSession()
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
