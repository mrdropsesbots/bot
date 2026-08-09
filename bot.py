from dotenv import load_dotenv
load_dotenv()

import os
import json
import logging
import traceback
from datetime import datetime
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.utils.executor import start_webhook
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")+"?v=2"
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# URL админ-панели
base_url = WEBAPP_URL.split("?")[0]  # убираем ?v=...
ADMIN_WEBAPP_URL = base_url.replace("index.html", "admin.html") + "?v=2"




HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

# ================== SUPABASE HELPERS ==================
async def sb_get(path: str):
    async with aiohttp.ClientSession() as session:
        url = f"{SUPABASE_URL}/rest/v1/{path}"
        async with session.get(url, headers=HEADERS) as resp:
            if resp.status == 200:
                return await resp.json()
            logging.error(f"sb_get error {resp.status}: {path} | {await resp.text()[:200]}")
            return []

async def sb_post(path: str, data: dict):
    async with aiohttp.ClientSession() as session:
        url = f"{SUPABASE_URL}/rest/v1/{path}"
        async with session.post(url, headers=HEADERS, json=data) as resp:
            text = await resp.text()
            logging.info(f"sb_post {path} status={resp.status} body={text[:300]}")
            if resp.status in (200, 201):
                try:
                    return json.loads(text)
                except:
                    return [{"id": "unknown"}]
            logging.error(f"sb_post error {resp.status}: {path} | {text[:500]}")
            return None

async def sb_patch(path: str, data: dict):
    async with aiohttp.ClientSession() as session:
        url = f"{SUPABASE_URL}/rest/v1/{path}"
        async with session.patch(url, headers=HEADERS, json=data) as resp:
            if resp.status in (200, 204):
                return True
            logging.error(f"sb_patch error {resp.status}: {path}")
            return False

async def sb_count(path: str, query: str = ""):
    async with aiohttp.ClientSession() as session:
        url = f"{SUPABASE_URL}/rest/v1/{path}?{query}&limit=1"
        h = {**HEADERS, "Prefer": "count=exact"}
        async with session.get(url, headers=h) as resp:
            range_header = resp.headers.get("content-range", "0-0/0")
            try:
                return int(range_header.split("/")[-1])
            except (ValueError, IndexError):
                return 0

# ================== INIT BOT ==================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ================== HANDLERS ==================
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛍 Открыть барахолку", web_app=WebAppInfo(url=WEBAPP_URL)))
    kb.add(InlineKeyboardButton("❓ Как продать", callback_data="help"))
    if message.from_user.id == ADMIN_ID:
        kb.add(InlineKeyboardButton("⚙️ Админ-панель", web_app=WebAppInfo(url=ADMIN_WEBAPP_URL)))
    await message.answer(
        "Привет! Здесь можно продать или купить вещи в Беларуси.\n\n"
        "Нажмите кнопку ниже 👇",
        reply_markup=kb
    )

@dp.callback_query_handler(lambda c: c.data == 'help')
async def help_cb(call: types.CallbackQuery):
    await call.answer()
    await call.message.answer(
        "1. Нажмите «Открыть барахолку»\n"
        "2. Нажмите ➕ Продать внизу\n"
        "3. Заполните форму и прикрепите фото\n"
        "4. Покупатели напишут вам в личку\n\n"
        "VIP-размещение — /vip"
    )

@dp.message_handler(commands=['vip'])
async def vip(message: types.Message):
    await message.answer(
        "💎 <b>VIP-объявление</b>\n\n"
        "Ваш товар будет в начале ленты с оранжевой меткой.\n"
        "Стоимость: 5 BYN / 7 дней\n\n"
        "Переведите на карту и пришлите скрин админу.",
        parse_mode="HTML"
    )

# ================== WEB APP DATA ==================
@dp.message_handler(content_types=types.ContentType.WEB_APP_DATA)
async def web_data(message: types.Message):
    user = message.from_user
    raw_data = message.web_app_data.data
    logging.info(f"=== WEBAPP DATA from {user.id}: {raw_data[:500]} ===")

    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error: {e}")
        return await message.answer("❌ Некорректные данные от WebApp.")

    action = data.get("action")
    logging.info(f"Action: {action}")

    # ===== ADMIN ACTIONS =====
    if action in ("admin_approve", "admin_reject", "admin_ban"):
        if user.id != ADMIN_ID:
            return await message.answer("⛔️ Нет доступа.")

        if action == "admin_approve":
            item_id = data.get("item_id")
            await sb_patch(f"items?id=eq.{item_id}", {"status": "approved", "is_active": True})
            items = await sb_get(f"items?id=eq.{item_id}&select=*,profiles(telegram_id)")
            if items:
                await bot.send_message(items[0]["profiles"]["telegram_id"],
                    f"✅ Ваше объявление одобрено!\n\n📦 {items[0]['title']}\n💰 {items[0]['price']} BYN")
            await message.answer(f"✅ Объявление {item_id} одобрено.")

        elif action == "admin_reject":
            item_id = data.get("item_id")
            await sb_patch(f"items?id=eq.{item_id}", {"status": "rejected", "is_active": False})
            items = await sb_get(f"items?id=eq.{item_id}&select=*,profiles(telegram_id)")
            if items:
                await bot.send_message(items[0]["profiles"]["telegram_id"],
                    "❌ Ваше объявление отклонено.\n\nВозможные причины: запрещённый товар, некорректное описание, отсутствие фото.")
            await message.answer(f"❌ Объявление {item_id} отклонено.")

        elif action == "admin_ban":
            tg_id = data.get("tg_id")
            await message.answer(f"🚫 Пользователь <code>{tg_id}</code> в бан-листе.", parse_mode="HTML")
        return

    # ===== USER: CREATE ITEM =====
    if action == "create_item":
        title = data.get("title", "").strip()
        desc = data.get("description", "").strip()
        price = data.get("price", 0)
        photos = data.get("photos", [])

        logging.info(f"create_item: title={title}, price={price}, photos_count={len(photos)}")

        if len(title) < 3:
            return await message.answer("❌ Слишком короткое название. Минимум 3 символа.")
        if price > 100000:
            return await message.answer("❌ Цена слишком высокая. Максимум 100 000 BYN.")

        banned = ["кокаин", "героин", "оружие", "паспорт", "права", "диплом", "наркотик", "пистолет", "травмат", "куплю почку"]
        if any(word in (title + " " + desc).lower() for word in banned):
            return await message.answer("❌ Объявление содержит запрещённый товар.")
        if "http" in title or "t.me/" in title or "@" in title:
            return await message.answer("❌ Нельзя размещать ссылки в названии.")

        # Получить или создать профиль
        profs = await sb_get(f"profiles?telegram_id=eq.{user.id}")
        logging.info(f"Profiles found: {len(profs)}")

        if not profs:
            logging.info(f"Creating profile for {user.id}")
            await sb_post("profiles", {
                "telegram_id": user.id,
                "username": user.username,
                "full_name": user.full_name or "Без имени",
                "city": data.get("city", "Минск")
            })
            profs = await sb_get(f"profiles?telegram_id=eq.{user.id}")

        if not profs:
            logging.error("Profile creation failed")
            return await message.answer("❌ Не удалось создать профиль. Попробуйте позже.")

        profile_id = profs[0]["id"]
        logging.info(f"Profile ID: {profile_id}")

        result = await sb_post("items", {
            "profile_id": profile_id,
            "category_id": data.get("category_id"),
            "title": title,
            "description": desc,
            "price": price,
            "condition": data.get("condition", "used"),
            "city": data.get("city", "Минск"),
            "photos": photos,
            "is_active": False,
            "status": "pending"
        })

        logging.info(f"sb_post items result: {result}")

        if not result:
            return await message.answer("❌ Ошибка сохранения объявления. Попробуйте позже.")

        await message.answer(
            "⏳ Объявление отправлено на модерацию.\n"
            "Обычно проверка занимает 10–30 минут."
        )

        item_id = None
        if isinstance(result, list) and len(result) > 0:
            item_id = result[0].get("id")

        if not item_id:
            last = await sb_get(f"items?profile_id=eq.{profile_id}&order=created_at.desc&limit=1")
            item_id = last[0].get("id") if last else None
            logging.info(f"Fallback item_id: {item_id}")

        if ADMIN_ID and item_id:
            await notify_admin(item_id, data, user)

    elif action == "interest":
        item_id = data.get("item_id")
        if not item_id:
            return await message.answer("❌ Некорректный запрос.")

        items = await sb_get(f"items?id=eq.{item_id}&select=*,profiles(username,telegram_id)")
        if not items:
            return await message.answer("❌ Товар не найден")

        item = items[0]
        seller_tg = item["profiles"]["telegram_id"]

        await sb_post("interests", {
            "item_id": item_id,
            "buyer_tg_id": user.id,
            "buyer_username": user.username,
            "message": data.get("message", "")
        })

        await bot.send_message(
            seller_tg,
            f"📩 <b>Новый покупатель!</b>\n\n"
            f"<b>Товар:</b> {item['title']}\n"
            f"<b>Цена:</b> {item['price']} BYN\n"
            f"<b>Покупатель:</b> @{user.username or user.id}\n\n"
            f"Напишите ему первым!",
            parse_mode="HTML"
        )
        await message.answer("✅ Продавец уведомлён! Он свяжется с вами.")

async def notify_admin(item_id, data, user):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{item_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{item_id}")
    )
    kb.add(InlineKeyboardButton("🚫 Забанить", callback_data=f"ban:{user.id}"))

    text = (
        f"🆕 <b>Новое на модерацию</b>\n\n"
        f"📦 <b>{data.get('title', 'Без названия')}</b>\n"
        f"💰 {data.get('price', 0)} BYN · 🏙 {data.get('city','Минск')}\n"
        f"📂 Категория ID: {data.get('category_id', '—')}\n"
        f"📝 {data.get('description','Нет описания')[:200]}\n\n"
        f"👤 @{user.username or 'нет'} · ID: <code>{user.id}</code>"
    )

    photos = data.get("photos", [])
    try:
        if photos:
            await bot.send_photo(ADMIN_ID, photos[0], caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await bot.send_message(ADMIN_ID, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Admin notify error: {e}")

# ================== ADMIN COMMANDS ==================
@dp.message_handler(commands=['admin'])
async def admin_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔️ Только для админа.")
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⚙️ Открыть админ-панель", web_app=WebAppInfo(url=ADMIN_WEBAPP_URL)))
    await message.answer("Админ-панель:", reply_markup=kb)

@dp.message_handler(commands=['moderate'])
async def moderate_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔️ Только для админа.")

    pending = await sb_get("items?status=eq.pending&select=*,profiles(username,telegram_id)&order=created_at.asc&limit=5")

    if not pending:
        return await message.answer("✅ Нет объявлений на модерации.")

    await message.answer(f"🔥 <b>На модерации: {len(pending)}</b>", parse_mode="HTML")

    for item in pending:
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{item['id']}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{item['id']}")
        )

        text = (
            f"📦 <b>{item['title']}</b>\n"
            f"💰 {item['price']} BYN · {item['city']}\n"
            f"👤 @{item['profiles']['username'] or item['profiles']['telegram_id']}\n"
            f"📝 {item.get('description', 'Нет описания')[:300]}"
        )

        if item.get('photos') and len(item['photos']) > 0:
            await message.answer_photo(item['photos'][0], caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.message_handler(commands=['users'])
async def users_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔️ Только для админа.")

    users = await sb_get("profiles?order=created_at.desc&limit=10")
    if not users:
        return await message.answer("Нет пользователей.")

    text = "👤 <b>Последние 10 пользователей:</b>\n\n"
    for u in users:
        text += f"• @{u.get('username') or '—'} — {u.get('full_name', 'Без имени')} — {u['city']} — <code>{u['telegram_id']}</code>\n"

    await message.answer(text, parse_mode="HTML")

# ================== CALLBACKS ==================
@dp.callback_query_handler(lambda c: c.data.startswith('approve:'))
async def approve_cb(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("Нет доступа", show_alert=True)

    item_id = call.data.split(":")[1]
    await sb_patch(f"items?id=eq.{item_id}", {"status": "approved", "is_active": True})

    items = await sb_get(f"items?id=eq.{item_id}&select=*,profiles(telegram_id)")
    if items:
        await bot.send_message(
            items[0]["profiles"]["telegram_id"],
            f"✅ Ваше объявление одобрено!\n\n📦 {items[0]['title']}\n💰 {items[0]['price']} BYN"
        )

    old = call.message.caption or call.message.text or ""
    new_text = old + "\n\n✅ ОДОБРЕНО"
    try:
        if call.message.photo:
            await call.message.edit_caption(caption=new_text, reply_markup=None)
        else:
            await call.message.edit_text(text=new_text, reply_markup=None)
    except Exception:
        pass
    await call.answer("Одобрено")

@dp.callback_query_handler(lambda c: c.data.startswith('reject:'))
async def reject_cb(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("Нет доступа", show_alert=True)

    item_id = call.data.split(":")[1]
    await sb_patch(f"items?id=eq.{item_id}", {"status": "rejected", "is_active": False})

    items = await sb_get(f"items?id=eq.{item_id}&select=*,profiles(telegram_id)")
    if items:
        await bot.send_message(
            items[0]["profiles"]["telegram_id"],
            "❌ Ваше объявление отклонено.\n\nВозможные причины: запрещённый товар, некорректное описание, отсутствие фото."
        )

    old = call.message.caption or call.message.text or ""
    new_text = old + "\n\n❌ ОТКЛОНЕНО"
    try:
        if call.message.photo:
            await call.message.edit_caption(caption=new_text, reply_markup=None)
        else:
            await call.message.edit_text(text=new_text, reply_markup=None)
    except Exception:
        pass
    await call.answer("Отклонено")

@dp.callback_query_handler(lambda c: c.data.startswith('ban:'))
async def ban_cb(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("Нет доступа", show_alert=True)

    tg_id = call.data.split(":")[1]
    await call.message.answer(f"🚫 Пользователь <code>{tg_id}</code> в бан-листе.", parse_mode="HTML")
    await call.answer("Забанен")

# ================== STARTUP / SHUTDOWN ==================
async def on_startup(dp):
    if WEBHOOK_HOST:
        await bot.set_webhook(WEBHOOK_URL)
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Барахолка", web_app=WebAppInfo(url=WEBAPP_URL))
        )
        logging.info(f"Webhook set: {WEBHOOK_URL}")
    else:
        logging.warning("RENDER_EXTERNAL_URL not set")

async def on_shutdown(dp):
    await bot.delete_webhook()

# ================== MAIN ==================
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 10000))
    )
