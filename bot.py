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
BOT_TOKEN = "8663406888:AAF3891CIjS3tASop0B092IlFN7Pato7DMU"      # ← замени
ADMIN_ID = 5377564835                            # ← замени на свой Telegram ID
SUPABASE_URL = "https://whabfemisufruvkabfnj.supabase.co/rest/v1/"   # ← замени (без слэша в конце)
SUPABASE_KEY = "sb_secret_4HuEgi9_LUMELEhwY5bnDA_Fz1Vb-Mw"           # ← замени (см. инструкцию ниже)

RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_URL = f"https://{RENDER_HOST}/webhook" if RENDER_HOST else None
PORT = 10000

# ========== SUPABASE REST CLIENT ==========
_http_session = None

class SupabaseDB:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.base = f"{SUPABASE_URL}/rest/v1"

    def _headers(self, prefer_minimal=False):
        h = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        }
        if prefer_minimal:
            h["Prefer"] = "return=minimal"
        return h

    async def _get(self, path, params=None):
        async with self.session.get(f"{self.base}{path}", headers=self._headers(), params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            logging.error(f"Supabase GET {resp.status}: {await resp.text()}")
            return []

    async def _post(self, path, data):
        async with self.session.post(f"{self.base}{path}", headers=self._headers(prefer_minimal=True), json=data) as resp:
            ok = resp.status in (200, 201, 204)
            if not ok:
                logging.error(f"Supabase POST {resp.status}: {await resp.text()}")
            return ok

    async def _patch(self, path, data, params=None):
        async with self.session.patch(f"{self.base}{path}", headers=self._headers(), json=data, params=params) as resp:
            ok = resp.status in (200, 204)
            if not ok:
                logging.error(f"Supabase PATCH {resp.status}: {await resp.text()}")
            return ok

    async def _delete(self, path, params):
        async with self.session.delete(f"{self.base}{path}", headers=self._headers(), params=params) as resp:
            return resp.status in (200, 204)

    # --- MENU ---
    async def search_menu(self, query):
        return await self._get("/menu", {
            "dish": f"ilike.*{query}*",
            "order": "price.asc",
            "limit": 5,
        })

    async def add_menu(self, restaurant, dish, price, address, lat, lon):
        return await self._post("/menu", {
            "restaurant": restaurant, "dish": dish, "price": price,
            "address": address, "lat": lat, "lon": lon,
        })

    async def delete_menu(self, row_id):
        return await self._delete("/menu", {"id": f"eq.{row_id}"})

    async def list_menu(self, limit=20):
        return await self._get("/menu", {"order": "id.desc", "limit": limit})

    # --- USERS ---
    async def get_user(self, user_id):
        rows = await self._get("/users", {"user_id": f"eq.{user_id}"})
        return rows[0] if rows else None

    async def update_user(self, user_id, username, first_name, search=0, loc=0, source="direct"):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        existing = await self.get_user(user_id)
        if existing:
            await self._patch("/users", {
                "username": username, "first_name": first_name, "last_seen": now,
                "search_count": (existing.get("search_count") or 0) + search,
                "loc_count": (existing.get("loc_count") or 0) + loc,
            }, {"user_id": f"eq.{user_id}"})
        else:
            await self._post("/users", {
                "user_id": user_id, "username": username, "first_name": first_name,
                "first_seen": now, "last_seen": now,
                "search_count": search, "loc_count": loc, "source": source,
            })

    async def stats(self):
        rows = await self._get("/users", {"select": "*"})
        total = len(rows)
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        dau = sum(1 for r in rows if str(r.get("last_seen", "")).startswith(today))
        week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        wau = sum(1 for r in rows if str(r.get("last_seen", "")) >= week_ago)
        searches = sum((r.get("search_count") or 0) for r in rows)
        locs = sum((r.get("loc_count") or 0) for r in rows)
        return total, dau, wau, searches, locs

def get_db():
    return SupabaseDB(_http_session)

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

# ========== КОМАНДЫ ==========
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
            raise ValueError
        name, dish, price, address, lat, lon = [p.strip() for p in parts]
        ok = await get_db().add_menu(name, dish, float(price), address, float(lat), float(lon))
        await message.answer(f"✅ Добавлено: {dish} в {name}" if ok else "❌ Ошибка")
    except Exception as e:
        logging.error(f"/add error: {e}")
        await message.answer("❌ Формат:\n`/add Ресторан|Блюдо|Цена|Адрес|lat|lon`", parse_mode="Markdown")

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
    rows = await get_db().list_menu(20)
    if not rows:
        return await message.answer("База пуста.")
    text = "📋 <b>Последние 20:</b>\n"
    for r in rows:
        text += f"{r['id']}. {r['restaurant']} | {r['dish']} | {r['price']} BYN\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("check"))
async def check_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    rows = await get_db().list_menu(5)
    text = f"📋 <b>Последние 5:</b>\n"
    for r in rows:
        text += f"<code>{r}</code>\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    total, dau, wau, searches, locs = await get_db().stats()
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

    rows = await get_db().search_menu(query)
    if not rows:
        await message.answer(f"❌ По запросу «{query}» ничего не найдено.")
        return

    answer = f"🍽️ *Результаты по «{query}»:*\n\n"
    for r in rows:
        answer += f"🍴 *{r['restaurant']}* — {r['dish']} {r['price']} BYN\n"
        if uid in user_location:
            u_lat, u_lon = user_location[uid]
            url = f"https://yandex.by/maps/?rtext={u_lat},{u_lon}~{r['lat']},{r['lon']}&rtt=auto"
            answer += f"   🗺️ [Маршрут]({url})\n"
        else:
            url = f"https://yandex.by/maps/?mode=search&text={r['lat']},{r['lon']}"
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
