import os
import json
import logging
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp
from supabase import create_client, Client

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")  # service_role
WEBAPP_URL = os.getenv("WEBAPP_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ================== USER COMMANDS ==================

@dp.message(Command("start"))
async def start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍 Открыть барахолку", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="❓ Как продать", callback_data="help")]
    ])
    await message.answer(
        "Привет! Здесь можно продать или купить вещи в Беларуси.\n\n"
        "Нажмите кнопку ниже 👇",
        reply_markup=kb
    )

@dp.callback_query(F.data == "help")
async def help_cb(call: types.CallbackQuery):
    await call.message.answer(
        "1. Нажмите «Открыть барахолку»\n"
        "2. Нажмите ➕ Продать внизу\n"
        "3. Заполните форму и прикрепите фото\n"
        "4. Покупатели напишут вам в личку\n\n"
        "VIP-размещение — /vip"
    )

@dp.message(Command("vip"))
async def vip(message: types.Message):
    await message.answer(
        "💎 <b>VIP-объявление</b>\n\n"
        "Ваш товар будет в начале ленты с оранжевой меткой.\n"
        "Стоимость: 5 BYN / 7 дней\n\n"
        "Переведите на карту и пришлите скрин админу.",
        parse_mode="HTML"
    )

# ================== WEB APP DATA ==================

@dp.message(F.web_app_data)
async def web_data(message: types.Message):
    data = json.loads(message.web_app_data.data)
    action = data.get("action")
    user = message.from_user

    if action == "create_item":
        title = data.get("title", "").strip()
        desc = data.get("description", "").strip()
        price = data.get("price", 0)

        # Auto-moderation
        if len(title) < 3:
            return await message.answer("❌ Слишком короткое название. Минимум 3 символа.")
        if price > 100000:
            return await message.answer("❌ Цена слишком высокая. Максимум 100 000 BYN.")
        
        banned = ["кокаин", "героин", "оружие", "паспорт", "права", "диплом", "наркотик", "пистолет", "травмат", "макет", "куплю почку"]
        if any(word in (title + " " + desc).lower() for word in banned):
            return await message.answer("❌ Объявление содержит запрещённый товар.")
        if "http" in title or "t.me/" in title or "@" in title:
            return await message.answer("❌ Нельзя размещать ссылки и контакты в названии.")

        # Profile
        prof = supabase.table("profiles").select("*").eq("telegram_id", user.id).execute()
        if not prof.data:
            supabase.table("profiles").insert({
                "telegram_id": user.id,
                "username": user.username,
                "full_name": user.full_name or "Без имени",
                "city": data.get("city", "Минск")
            }).execute()
            prof = supabase.table("profiles").select("*").eq("telegram_id", user.id).execute()
        profile_id = prof.data[0]["id"]

        # Insert item
        result = supabase.table("items").insert({
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
        }).execute()

        await message.answer(
            "⏳ Объявление отправлено на модерацию.\n"
            "Обычно проверка занимает 10–30 минут.\n"
            "Мы напишем, когда всё будет готово."
        )

        if ADMIN_ID and result.data:
            await notify_admin(result.data[0]["id"], data, user)

    elif action == "interest":
        item_id = data["item_id"]
        item = supabase.table("items").select("*,profiles(telegram_id,username)").eq("id", item_id).single().execute()
        if not item.data:
            return await message.answer("❌ Товар не найден")

        seller_tg = item.data["profiles"]["telegram_id"]

        supabase.table("interests").insert({
            "item_id": item_id,
            "buyer_tg_id": user.id,
            "buyer_username": user.username,
            "message": data.get("message", "")
        }).execute()

        await bot.send_message(
            seller_tg,
            f"📩 <b>Новый покупатель!</b>\n\n"
            f"<b>Товар:</b> {item.data['title']}\n"
            f"<b>Цена:</b> {item.data['price']} BYN\n"
            f"<b>Покупатель:</b> @{user.username or user.id}\n\n"
            f"Напишите ему первым!",
            parse_mode="HTML"
        )
        await message.answer("✅ Продавец уведомлён! Он свяжется с вами.")

async def notify_admin(item_id, data, user):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{item_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{item_id}")
        ],
        [InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban:{user.id}")]
    ])

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

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔️ Только для админа.")

    total_users = supabase.table("profiles").select("*", count="exact", head=True).execute()
    total_items = supabase.table("items").select("*", count="exact", head=True).execute()
    pending = supabase.table("items").select("*", count="exact", head=True).eq("status", "pending").execute()
    approved = supabase.table("items").select("*", count="exact", head=True).eq("status", "approved").execute()
    rejected = supabase.table("items").select("*", count="exact", head=True).eq("status", "rejected").execute()

    today = datetime.now().strftime("%Y-%m-%d")
    today_items = supabase.table("items").select("*", count="exact", head=True).gte("created_at", today).execute()
    today_users = supabase.table("profiles").select("*", count="exact", head=True).gte("created_at", today).execute()

    text = (
        f"📊 <b>Админ-панель</b>\n\n"
        f"👤 Пользователи: {total_users.count} (сегодня: +{today_users.count})\n"
        f"📦 Объявления: {total_items.count}\n"
        f"   🔥 На модерации: {pending.count}\n"
        f"   ✅ Одобрено: {approved.count}\n"
        f"   ❌ Отклонено: {rejected.count}\n"
        f"   📅 Сегодня: +{today_items.count}\n\n"
        f"/moderate — модерация\n"
        f"/users — последние юзеры"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 На модерации", callback_data="admin:moderate")],
        [InlineKeyboardButton(text="♻️ Обновить", callback_data="admin:refresh")]
    ])

    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "admin:refresh")
async def admin_refresh(call: types.CallbackQuery):
    await call.answer("Обновлено")
    await admin_panel(call.message)

@dp.callback_query(F.data == "admin:moderate")
async def admin_moderate_btn(call: types.CallbackQuery):
    await call.answer()
    await moderate_cmd(call.message)

@dp.message(Command("moderate"))
async def moderate_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔️ Только для админа.")

    pending = supabase.table("items").select("*,profiles(username,telegram_id)").eq("status", "pending").order("created_at", desc=False).limit(5).execute()

    if not pending.data:
        return await message.answer("✅ Нет объявлений на модерации.")

    await message.answer(f"🔥 <b>На модерации: {len(pending.data)}</b>", parse_mode="HTML")

    for item in pending.data:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{item['id']}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{item['id']}")
            ]
        ])

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

@dp.message(Command("users"))
async def users_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔️ Только для админа.")

    users = supabase.table("profiles").select("*").order("created_at", desc=True).limit(10).execute()
    if not users.data:
        return await message.answer("Нет пользователей.")

    text = "👤 <b>Последние 10 пользователей:</b>\n\n"
    for u in users.data:
        text += f"• @{u.get('username') or '—'} — {u.get('full_name', 'Без имени')} — {u['city']} — <code>{u['telegram_id']}</code>\n"

    await message.answer(text, parse_mode="HTML")

# ================== MODERATION CALLBACKS ==================

@dp.callback_query(F.data.startswith("approve:"))
async def approve_item(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("Нет доступа", show_alert=True)

    item_id = call.data.split(":")[1]
    supabase.table("items").update({"status": "approved", "is_active": True}).eq("id", item_id).execute()

    item = supabase.table("items").select("*,profiles(telegram_id)").eq("id", item_id).single().execute()
    if item.data:
        await bot.send_message(
            item.data["profiles"]["telegram_id"],
            f"✅ Ваше объявление одобрено и опубликовано!\n\n"
            f"📦 {item.data['title']}\n"
            f"💰 {item.data['price']} BYN"
        )

    if call.message.photo:
        await call.message.edit_caption(caption=(call.message.caption or "") + "\n\n✅ ОДОБРЕНО", reply_markup=None)
    else:
        await call.message.edit_text((call.message.text or "") + "\n\n✅ ОДОБРЕНО", reply_markup=None)
    await call.answer("Одобрено")

@dp.callback_query(F.data.startswith("reject:"))
async def reject_item(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("Нет доступа", show_alert=True)

    item_id = call.data.split(":")[1]
    supabase.table("items").update({"status": "rejected", "is_active": False}).eq("id", item_id).execute()

    item = supabase.table("items").select("*,profiles(telegram_id)").eq("id", item_id).single().execute()
    if item.data:
        await bot.send_message(
            item.data["profiles"]["telegram_id"],
            "❌ Ваше объявление отклонено.\n\n"
            "Возможные причины:\n"
            "• Запрещённый товар\n"
            "• Некорректное описание\n"
            "• Отсутствие фото\n\n"
            "Попробуйте разместить снова."
        )

    if call.message.photo:
        await call.message.edit_caption(caption=(call.message.caption or "") + "\n\n❌ ОТКЛОНЕНО", reply_markup=None)
    else:
        await call.message.edit_text((call.message.text or "") + "\n\n❌ ОТКЛОНЕНО", reply_markup=None)
    await call.answer("Отклонено")

@dp.callback_query(F.data.startswith("ban:"))
async def ban_user(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("Нет доступа", show_alert=True)

    tg_id = call.data.split(":")[1]
    await call.message.answer(
        f"🚫 Пользователь <code>{tg_id}</code> в бан-листе.\n"
        f"Для полной блокировки добавьте поле banned в таблицу profiles.",
        parse_mode="HTML"
    )
    await call.answer("Забанен")

# ================== WEBHOOK SERVER ==================

async def webhook_handler(request):
    update = types.Update(**(await request.json()))
    await dp.feed_webhook_update(bot, update)
    return web.Response()

async def on_startup(app):
    url = os.getenv("RENDER_EXTERNAL_URL", "") + "/webhook"
    if url.startswith("http"):
        await bot.set_webhook(url)
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Барахолка", web_app=WebAppInfo(url=WEBAPP_URL)))

async def on_shutdown(app):
    await bot.session.close()

def main():
    app = web.Application()
    app.router.add_post("/webhook", webhook_handler)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
