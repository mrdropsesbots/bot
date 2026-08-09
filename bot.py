import os
import json
import time
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from supabase import create_client, Client

# ---------- КОНФИГУРАЦИЯ (Render Environment) ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://pfutmevbueqbfnhkwrrl.supabase.co")
# Сервисный ключ (для записи/обновления, НЕ анонимный)
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")   # добавьте в Render
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://mrdropsesbots.github.io/bot/docs/index.html")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")  # для webhook

# Проверка ключей
if not BOT_TOKEN or not SUPABASE_SERVICE_KEY:
    raise ValueError("BOT_TOKEN и SUPABASE_SERVICE_KEY обязательны")

# Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Aiogram bot + dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

logging.basicConfig(level=logging.INFO)

# ---------- КЛАВИАТУРЫ ----------
def main_menu_keyboard(user_id: int):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    version = int(time.time())
    webapp_url = f"{WEBAPP_URL}?v={version}"
    keyboard.add(
        types.KeyboardButton("🛍 Открыть барахолку", web_app=WebAppInfo(url=webapp_url))
    )
    keyboard.add(types.KeyboardButton("❓ Как продать"))
    if user_id == ADMIN_ID:
        admin_url = f"{WEBAPP_URL.replace('index.html', 'admin.html')}?v={version}"
        keyboard.add(types.KeyboardButton("⚙️ Админ-панель", web_app=WebAppInfo(url=admin_url)))
    return keyboard

def admin_inline_keyboard():
    # Используется в /moderate
    return InlineKeyboardMarkup(row_width=2)

# ---------- ОБРАБОТЧИКИ ----------
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user = message.from_user
    # Создаём/обновляем профиль
    try:
        supabase.table('profiles').upsert({
            'telegram_id': user.id,
            'username': user.username,
            'full_name': user.full_name
        }, on_conflict='telegram_id').execute()
    except Exception as e:
        logging.error(f"Ошибка upsert профиля: {e}")

    await message.answer(
        "🛍 Добро пожаловать в Барахолку!\n"
        "Здесь можно купить или продать что угодно в Беларуси.",
        reply_markup=main_menu_keyboard(user.id)
    )

@dp.message_handler(lambda m: m.text == "❓ Как продать")
async def how_to_sell(message: types.Message):
    await message.answer(
        "📌 Чтобы разместить объявление:\n"
        "1. Нажмите «➕ Продать» в приложении.\n"
        "2. Заполните все поля и загрузите фото.\n"
        "3. После модерации объявление появится в ленте.\n\n"
        "Если есть вопросы — обратитесь к администратору."
    )

@dp.message_handler(commands=['admin'])
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    version = int(time.time())
    admin_url = f"{WEBAPP_URL.replace('index.html', 'admin.html')}?v={version}"
    await message.answer(
        "Админ-панель",
        reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(
            types.KeyboardButton("⚙️ Открыть админку", web_app=WebAppInfo(url=admin_url))
        )
    )

@dp.message_handler(commands=['moderate'])
async def moderate(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        res = supabase.table('items').select('*').eq('status', 'pending').execute()
        items = res.data
        if not items:
            await message.answer("Нет объявлений на модерации.")
            return
        for item in items:
            txt = f"📌 {item['title']} — {item['price']} BYN\n{item.get('description','')}\nГород: {item['city']}"
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{item['id']}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{item['id']}")
            )
            await message.answer(txt, reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Ошибка загрузки pending: {e}")
        await message.answer("Ошибка загрузки.")

@dp.callback_query_handler(lambda c: c.data.startswith('approve_') or c.data.startswith('reject_'))
async def process_moderation(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа")
        return
    action, item_id = callback.data.split('_', 1)
    if action == 'approve':
        # Получаем item для уведомления
        item = supabase.table('items').select('*, profiles!inner(telegram_id, full_name)').eq('id', item_id).single().execute().data
        supabase.table('items').update({'status': 'approved', 'is_active': True}).eq('id', item_id).execute()
        if item and item.get('profiles'):
            seller_id = item['profiles']['telegram_id']
            try:
                await bot.send_message(seller_id, f"✅ Ваше объявление «{item['title']}» одобрено!")
            except Exception as e:
                logging.warning(f"Не удалось уведомить продавца {seller_id}: {e}")
        await callback.message.edit_text(callback.message.text + "\n\n✅ Одобрено")
    elif action == 'reject':
        item = supabase.table('items').select('*, profiles!inner(telegram_id, full_name)').eq('id', item_id).single().execute().data
        supabase.table('items').update({'status': 'rejected', 'is_active': False}).eq('id', item_id).execute()
        if item and item.get('profiles'):
            seller_id = item['profiles']['telegram_id']
            try:
                await bot.send_message(seller_id, f"❌ Ваше объявление «{item['title']}» отклонено модератором.")
            except Exception as e:
                logging.warning(f"Не удалось уведомить продавца {seller_id}: {e}")
        await callback.message.edit_text(callback.message.text + "\n\n❌ Отклонено")
    await callback.answer()

@dp.message_handler(content_types=types.ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: types.Message):
    user = message.from_user
    try:
        data = json.loads(message.web_app_data.data)
    except:
        await message.answer("Ошибка данных")
        return

    action = data.get('action')

    if action == 'create_item':
        # Создаём/обновляем профиль
        try:
            profile = supabase.table('profiles').upsert({
                'telegram_id': user.id,
                'username': user.username,
                'full_name': user.full_name,
                'city': data.get('city', 'Минск')   # сохраним город из объявления как город профиля?
            }, on_conflict='telegram_id').execute()
            profile_id = profile.data[0]['id'] if profile.data else None
        except Exception as e:
            logging.error(f"Ошибка профиля: {e}")
            await message.answer("Ошибка сохранения профиля")
            return

        if not profile_id:
            await message.answer("Ошибка идентификации профиля")
            return

        # Валидация
        title = data.get('title', '').strip()
        if not title or len(title) > 100:
            await message.answer("Название обязательно (до 100 символов)")
            return
        try:
            price = int(data.get('price', 0))
            if price <= 0:
                raise ValueError
        except:
            await message.answer("Некорректная цена")
            return
        condition = data.get('condition')
        if condition not in ('новое', 'б/у', 'как новое'):
            await message.answer("Выберите состояние")
            return
        city = data.get('city')
        valid_cities = ['Минск','Гомель','Могилёв','Витебск','Гродно','Брест']
        if city not in valid_cities:
            await message.answer("Некорректный город")
            return

        insert_data = {
            'profile_id': profile_id,
            'category_id': data.get('category_id'),
            'title': title,
            'description': data.get('description', ''),
            'price': price,
            'condition': condition,
            'city': city,
            'photos': data.get('photos', []),
            'status': 'pending',
            'is_active': False,
            'is_vip': False
        }
        try:
            item_res = supabase.table('items').insert(insert_data).execute()
            item = item_res.data[0] if item_res.data else None
        except Exception as e:
            logging.error(f"Ошибка вставки item: {e}")
            await message.answer("Ошибка сохранения объявления")
            return

        await message.answer("📦 Объявление отправлено на модерацию. Ожидайте.")

        # Уведомление админу
        if ADMIN_ID:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"🆕 Новое объявление от {user.full_name} (@{user.username}):\n"
                    f"«{title}» за {price} BYN\n"
                    f"Город: {city}\n"
                    f"Для проверки нажмите /moderate"
                )
            except Exception as e:
                logging.error(f"Не удалось уведомить админа: {e}")

    elif action == 'interest':
        item_id = data.get('item_id')
        seller_tg_id = data.get('seller_telegram_id')
        if not item_id or not seller_tg_id:
            await message.answer("Ошибка данных запроса")
            return
        # Запись интереса
        try:
            supabase.table('interests').insert({
                'item_id': item_id,
                'buyer_tg_id': user.id,
                'buyer_username': user.username
            }).execute()
        except Exception as e:
            logging.warning(f"Ошибка записи интереса: {e}")

        # Уведомление продавцу
        try:
            await bot.send_message(
                int(seller_tg_id),
                f"💬 Интерес к вашему объявлению!\n"
                f"Покупатель: {user.full_name} (@{user.username})\n"
                f"ID: {user.id}\n"
                f"Свяжитесь с ним напрямую."
            )
        except Exception as e:
            await message.answer("Не удалось уведомить продавца. Возможно, он не запускал бота.")

        await message.answer("✅ Продавец получил ваше сообщение.")

    elif action in ('admin_approve', 'admin_reject', 'admin_ban'):
        if user.id != ADMIN_ID:
            await message.answer("У вас нет прав администратора")
            return
        item_id = data.get('item_id')
        if not item_id:
            await message.answer("Отсутствует item_id")
            return

        if action == 'admin_approve':
            try:
                supabase.table('items').update({'status': 'approved', 'is_active': True}).eq('id', item_id).execute()
                # Уведомление продавцу
                item = supabase.table('items').select('*, profiles!inner(telegram_id, full_name)').eq('id', item_id).single().execute().data
                if item and item.get('profiles'):
                    seller_id = item['profiles']['telegram_id']
                    await bot.send_message(seller_id, f"✅ Ваше объявление «{item['title']}» одобрено!")
                await message.answer("Объявление одобрено")
            except Exception as e:
                logging.error(f"Ошибка approve: {e}")
        elif action == 'admin_reject':
            try:
                supabase.table('items').update({'status': 'rejected', 'is_active': False}).eq('id', item_id).execute()
                item = supabase.table('items').select('*, profiles!inner(telegram_id, full_name)').eq('id', item_id).single().execute().data
                if item and item.get('profiles'):
                    seller_id = item['profiles']['telegram_id']
                    await bot.send_message(seller_id, f"❌ Ваше объявление «{item['title']}» отклонено.")
                await message.answer("Объявление отклонено")
            except Exception as e:
                logging.error(f"Ошибка reject: {e}")
        elif action == 'admin_ban':
            # Заглушка: просто сообщаем
            await message.answer("🚫 Функция бана пока не реализована")
    else:
        await message.answer("Неизвестное действие")

# ---------- НАСТРОЙКА WEBHOOK ----------
async def on_startup(dp):
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
        await bot.set_webhook(webhook_url)
        logging.info(f"Webhook установлен на {webhook_url}")
    else:
        logging.warning("RENDER_EXTERNAL_URL не задан, webhook не установлен")

async def on_shutdown(dp):
    await bot.delete_webhook()
    logging.info("Webhook удалён")

if __name__ == '__main__':
    executor.start_webhook(
        dispatcher=dp,
        webhook_path='/webhook',
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 8000))
    )