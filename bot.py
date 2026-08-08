import os
import json
import logging
import asyncio
from datetime import datetime
import aiohttp
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
WEBAPP_URL = os.getenv("WEBAPP_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

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

# ================== POST INIT ==================
async def post_init(app: Application):
    await app.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="Барахолка", web_app=WebAppInfo(url=WEBAPP_URL))
    )

# ================== USER HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🛍 Открыть барахолку", web_app=WebAppInfo(url=WEBAPP_URL))
    ], [
        InlineKeyboardButton("❓ Как продать", callback_data="help")
    ]])
    await update.message.reply_text(
        "Привет! Здесь можно продать или купить вещи в Беларуси.\n\nНажмите кнопку ниже 👇",
        reply_markup=kb
    )

async def help_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "1. Нажмите «Открыть барахолку»\n"
        "2. Нажмите ➕ Продать внизу\n"
        "3. Заполните форму и прикрепите фото\n"
        "4. Покупатели напишут вам в личку\n\n"
        "VIP-размещение — /vip"
    )

async def vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💎 <b>VIP-объявление</b>\n\n"
        "Ваш товар будет в начале ленты с оранжевой меткой.\n"
        "Стоимость: 5 BYN / 7 дней\n\n"
        "Переведите на карту и пришлите скрин админу.",
        parse_mode="HTML"
    )

# ================== WEB APP DATA ==================
async def web_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.web_app_data:
        return
    
    data = json.loads(update.message.web_app_data.data)
    action = data.get("action")
    user = update.effective_user

    if action == "create_item":
        title = data.get("title", "").strip()
        desc = data.get("description", "").strip()
        price = data.get("price", 0)

        if len(title) < 3:
            return await update.message.reply_text("❌ Слишком короткое название. Минимум 3 символа.")
        if price > 100000:
            return await update.message.reply_text("❌ Цена слишком высокая. Максимум 100 000 BYN.")
        
        banned = ["кокаин", "героин", "оружие", "паспорт", "права", "диплом", "наркотик", "пистолет", "травмат", "куплю почку"]
        if any(word in (title + " " + desc).lower() for word in banned):
            return await update.message.reply_text("❌ Объявление содержит запрещённый товар.")
        if "http" in title or "t.me/" in title or "@" in title:
            return await update.message.reply_text("❌ Нельзя размещать ссылки в названии.")

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

        await update.message.reply_text(
            "⏳ Объявление отправлено на модерацию.\n"
            "Обычно проверка занимает 10–30 минут."
        )

        if ADMIN_ID and result:
            await notify_admin(context, result[0]["id"], data, user)

    elif action == "interest":
        item_id = data["item_id"]
        items = await sb_get(f"items?id=eq.{item_id}&select=*,profiles(username,telegram_id)")
        if not items:
            return await update.message.reply_text("❌ Товар не найден")
        
        item = items[0]
        seller_tg = item["profiles"]["telegram_id"]

        await sb_post("interests", {
            "item_id": item_id,
            "buyer_tg_id": user.id,
            "buyer_username": user.username,
            "message": data.get("message", "")
        })

        await context.bot.send_message(
            seller_tg,
            f"📩 <b>Новый покупатель!</b>\n\n"
            f"<b>Товар:</b> {item['title']}\n"
            f"<b>Цена:</b> {item['price']} BYN\n"
            f"<b>Покупатель:</b> @{user.username or user.id}\n\n"
            f"Напишите ему первым!",
            parse_mode="HTML"
        )
        await update.message.reply_text("✅ Продавец уведомлён! Он свяжется с вами.")

async def notify_admin(context: ContextTypes.DEFAULT_TYPE, item_id: str, data: dict, user):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{item_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{item_id}")
    ], [
        InlineKeyboardButton("🚫 Забанить", callback_data=f"ban:{user.id}")
    ]])

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
            await context.bot.send_photo(ADMIN_ID, photos[0], caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await context.bot.send_message(ADMIN_ID, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Admin notify error: {e}")

# ================== ADMIN COMMANDS ==================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔️ Только для админа.")

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

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔥 На модерации", callback_data="admin:moderate"),
        InlineKeyboardButton("♻️ Обновить", callback_data="admin:refresh")
    ]])

    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

async def moderate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔️ Только для админа.")

    pending = await sb_get("items?status=eq.pending&select=*,profiles(username,telegram_id)&order=created_at.asc&limit=5")

    if not pending:
        return await update.message.reply_text("✅ Нет объявлений на модерации.")

    await update.message.reply_text(f"🔥 <b>На модерации: {len(pending)}</b>", parse_mode="HTML")

    for item in pending:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{item['id']}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{item['id']}")
        ]])

        text = (
            f"📦 <b>{item['title']}</b>\n"
            f"💰 {item['price']} BYN · {item['city']}\n"
            f"👤 @{item['profiles']['username'] or item['profiles']['telegram_id']}\n"
            f"📝 {item.get('description', 'Нет описания')[:300]}"
        )

        if item.get('photos') and len(item['photos']) > 0:
            await update.message.reply_photo(item['photos'][0], caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔️ Только для админа.")

    users = await sb_get("profiles?order=created_at.desc&limit=10")
    if not users:
        return await update.message.reply_text("Нет пользователей.")

    text = "👤 <b>Последние 10 пользователей:</b>\n\n"
    for u in users:
        text += f"• @{u.get('username') or '—'} — {u.get('full_name', 'Без имени')} — {u['city']} — <code>{u['telegram_id']}</code>\n"

    await update.message.reply_text(text, parse_mode="HTML")

# ================== CALLBACKS ==================
async def approve_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return await query.answer("Нет доступа", show_alert=True)

    item_id = query.data.split(":")[1]
    await sb_patch(f"items?id=eq.{item_id}", {"status": "approved", "is_active": True})

    items = await sb_get(f"items?id=eq.{item_id}&select=*,profiles(telegram_id)")
    if items:
        await context.bot.send_message(
            items[0]["profiles"]["telegram_id"],
            f"✅ Ваше объявление одобрено!\n\n📦 {items[0]['title']}\n💰 {items[0]['price']} BYN"
        )

    old = query.message.caption or query.message.text or ""
    new_text = old + "\n\n✅ ОДОБРЕНО"
    if query.message.photo:
        await query.edit_message_caption(caption=new_text, reply_markup=None)
    else:
        await query.edit_message_text(text=new_text, reply_markup=None)
    await query.answer("Одобрено")

async def reject_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return await query.answer("Нет доступа", show_alert=True)

    item_id = query.data.split(":")[1]
    await sb_patch(f"items?id=eq.{item_id}", {"status": "rejected", "is_active": False})

    items = await sb_get(f"items?id=eq.{item_id}&select=*,profiles(telegram_id)")
    if items:
        await context.bot.send_message(
            items[0]["profiles"]["telegram_id"],
            "❌ Ваше объявление отклонено.\n\nВозможные причины: запрещённый товар, некорректное описание, отсутствие фото."
        )

    old = query.message.caption or query.message.text or ""
    new_text = old + "\n\n❌ ОТКЛОНЕНО"
    if query.message.photo:
        await query.edit_message_caption(caption=new_text, reply_markup=None)
    else:
        await query.edit_message_text(text=new_text, reply_markup=None)
    await query.answer("Отклонено")

async def ban_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return await query.answer("Нет доступа", show_alert=True)

    tg_id = query.data.split(":")[1]
    await query.message.reply_text(
        f"🚫 Пользователь <code>{tg_id}</code> в бан-листе.",
        parse_mode="HTML"
    )
    await query.answer("Забанен")

async def admin_refresh_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Обновлено")
    await admin_panel(update, context)

async def admin_moderate_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await moderate_cmd(update, context)

# ================== MAIN ==================
async def run_webhook():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("vip", vip))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("moderate", moderate_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CallbackQueryHandler(help_cb, pattern="^help$"))
    app.add_handler(CallbackQueryHandler(admin_refresh_cb, pattern="^admin:refresh$"))
    app.add_handler(CallbackQueryHandler(admin_moderate_cb, pattern="^admin:moderate$"))
    app.add_handler(CallbackQueryHandler(approve_cb, pattern="^approve:"))
    app.add_handler(CallbackQueryHandler(reject_cb, pattern="^reject:"))
    app.add_handler(CallbackQueryHandler(ban_cb, pattern="^ban:"))
    app.add_handler(MessageHandler(filters.ALL, web_data))

    render_url = os.getenv("RENDER_EXTERNAL_URL", "")
    if render_url.startswith("http"):
        await app.initialize()
        await app.start()
        await app.updater.start_webhook(
            listen="0.0.0.0",
            port=int(os.getenv("PORT", 10000)),
            webhook_url=render_url + "/webhook",
            drop_pending_updates=True
        )
        while True:
            await asyncio.sleep(3600)
    else:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        await app.updater.idle()

def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_webhook())

if __name__ == "__main__":
    main()
