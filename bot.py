from dotenv import load_dotenv
load_dotenv()
import os
import json
import logging
from datetime import datetime
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.utils.executor import start_webhook
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
WEBAPP_URL = os.getenv("WEBAPP_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

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
            return await resp.json()

async def sb_post(path: str, data: dict):
    async with aiohttp.ClientSession() as session:
        url = f"{SUPABASE_URL}/rest/v1/{path}"
        async with session.post(url, headers=HEADERS, json=data) as resp:
            return await resp.json()

async def sb_patch(path: str, data: dict):
    async with aiohttp.ClientSession() as session:
        url = f"{SUPABASE_URL}/rest/v1/{path}"
        async with session.patch(url, headers=HEADERS, json=data) as resp:
            return await resp.json()

async def sb_count(path: str, query: str = ""):
    async with aiohttp.ClientSession() as session:
        url = f"{SUPABASE_URL}/rest/v1/{path}?{query}&limit=1"
        h = {**HEADERS, "Prefer": "count=exact"}
        async with session.get(url, headers=h) as resp:
            range_header = resp.headers.get("content-range", "0-0/0")
            return int(range_header.split("/")[-1])

# ================== INIT BOT ==================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ================== HANDLERS ==================
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🛍 Открыть барахолку", web_app=WebAppInfo(url=WEBAPP_URL)))
    kb.add(InlineKeyboardButton("❓ Как продать", callback_data="help"))
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
# Найти профиль
profile_url = f"{SUPABASE_URL}/rest/v1/profiles?telegram_id=eq.{user.id}"
async with session.get(profile_url, headers=headers) as resp:
    profiles = await resp.json()

# Если нет — создать
if not profiles:
    new_profile = {
        "telegram_id": user.id,
        "username": user.username,
        "full_name": user.full_name or user.username or "User"
    }
    async with session.post(
        f"{SUPABASE_URL}/rest/v1/profiles",
        headers=headers,
        json=new_profile
    ) as resp:
        profiles = await resp.json()

if not profiles:
    await message.answer("❌ Не удалось создать профиль")
    return

profile_id = profiles[0]["id"]

@dp.message_handler(content_types=types.ContentType.WEB_APP_DATA)
async def web_data(message: types.Message):
    data = json.loads(message.web_app_data.data)
    action = data.get("action")
    user = message.from_user

    if action == "create_item":
        title = data.get("title", "").strip()
        desc = data.get("description", "").strip()
        price = data.get("price", 0)

        if len(title) < 3:
            return await message.answer("❌ Слишком короткое название. Минимум 3 символа.")
        if price > 100000:
            return await message.answer("❌ Цена слишком высокая. Максимум 100 000 BYN.")
        
        banned = ["кокаин", "героин", "оружие", "паспорт", "права", "диплом", "наркотик", "пистолет", "травмат", "куплю почку"]
        if any(word in (title + " " + desc).lower() for word in banned):
            return await message.answer("❌ Объявление содержит запрещённый товар.")
        if "http" in title or "t.me/" in title or "@" in title:
            return await message.answer("❌ Нельзя размещать ссылки в названии.")

        profs = await sb_get(f"profiles?telegram_id=eq.{user.id}")
        if not profs:
            await sb_post("profiles", {
                "telegram_id": user.id,
                "username": user.username,
                "full_name": user.full_name or "Без имени",
                "city": data.get("city", "Минск")
            })
            profs = await sb_get(f"profiles?telegram_id=eq.{user.id}")
        profile_id = profs[0]["id"]

        result = await sb_post("items", {
            "profile_id": profile_id,
            "category_id": data["category_id"],
            "title": title,
            "description": desc,
            "price": price,
            "condition": data.get("condition", "used"),
            "city": data.get("city", "Минск"),
            "photos": data.get("photos", []),
            "is_active": False,
            "status": "pending"
        })

        await message.answer(
            "⏳ Объявление отправлено на модерацию.\n"
            "Обычно проверка занимает 10–30 минут."
        )

        if ADMIN_ID and result:
            await notify_admin(result[0]["id"], data, user)

    elif action == "interest":
        item_id = data["item_id"]
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
        f"📦 <b>{data['title']}</b>\n"
        f"💰 {data['price']} BYN · 🏙 {data.get('city','Минск')}\n"
        f"📂 Категория ID: {data['category_id']}\n"
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
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔️ Только для админа.")

    total_users = await sb_count("profiles")
    total_items = await sb_count("items")
    pending = await sb_count("items", "status=eq.pending")
    approved = await sb_count("items", "status=eq.approved")
    rejected = await sb_count("items", "status=eq.rejected")

    today = datetime.now().strftime("%Y-%m-%d")
    today_items = await sb_count("items", f"created_at=gte.{today}")
    today_users = await sb_count("profiles", f"created_at=gte.{today}")

    text = (
        f"📊 <b>Админ-панель</b>\n\n"
        f"👤 Пользователи: {total_users} (сегодня: +{today_users})\n"
        f"📦 Объявления: {total_items}\n"
        f"   🔥 На модерации: {pending}\n"
        f"   ✅ Одобрено: {approved}\n"
        f"   ❌ Отклонено: {rejected}\n"
        f"   📅 Сегодня: +{today_items}\n\n"
        f"/moderate — модерация\n"
        f"/users — последние юзеры"
    )

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🔥 На модерации", callback_data="admin:moderate"),
        InlineKeyboardButton("♻️ Обновить", callback_data="admin:refresh")
    )
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

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
    if call.message.photo:
        await call.message.edit_caption(caption=new_text, reply_markup=None)
    else:
        await call.message.edit_text(text=new_text, reply_markup=None)
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
    if call.message.photo:
        await call.message.edit_caption(caption=new_text, reply_markup=None)
    else:
        await call.message.edit_text(text=new_text, reply_markup=None)
    await call.answer("Отклонено")

@dp.callback_query_handler(lambda c: c.data.startswith('ban:'))
async def ban_cb(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("Нет доступа", show_alert=True)

    tg_id = call.data.split(":")[1]
    await call.message.answer(f"🚫 Пользователь <code>{tg_id}</code> в бан-листе.", parse_mode="HTML")
    await call.answer("Забанен")

@dp.callback_query_handler(lambda c: c.data == 'admin:refresh')
async def admin_refresh_cb(call: types.CallbackQuery):
    await call.answer("Обновлено")
    await admin_panel(call.message)

@dp.callback_query_handler(lambda c: c.data == 'admin:moderate')
async def admin_moderate_cb(call: types.CallbackQuery):
    await call.answer()
    await moderate_cmd(call.message)

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
