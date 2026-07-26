from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
import db
import keyboards
from config import ADMIN_IDS

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    user = message.from_user
    await db.upsert_user({
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "last_active": "now()"
    })
    await message.answer(
        "Привет! Я помогу найти заведения и посмотреть меню.\nВыберите категорию:",
        reply_markup=keyboards.main_menu()
    )

@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("Выберите категорию:", reply_markup=keyboards.main_menu())
    await callback.answer()

@router.callback_query(F.data.startswith("type_"))
async def show_places(callback: CallbackQuery):
    parts = callback.data.split("_")
    place_type = parts[1]
    page = int(parts[2]) if len(parts) == 3 else 0
    places = await db.get_places_by_type(place_type)
    if not places:
        await callback.message.edit_text("В этом разделе пока нет заведений.", reply_markup=keyboards.main_menu())
        await callback.answer()
        return
    await callback.message.edit_text(
        f"Заведения ({place_type}):",
        reply_markup=keyboards.places_list(places, place_type, page)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("place_"))
async def show_place(callback: CallbackQuery):
    place_id = int(callback.data.split("_")[1])
    place = await db.get_place_by_id(place_id)
    if not place:
        await callback.answer("Заведение не найдено")
        return
    text = f"**{place['name']}**\n📍 {place.get('address','')}\n\n{place.get('description','')}"
    has_booking = bool(place.get('booking_phone'))
    await callback.message.edit_text(text, reply_markup=keyboards.place_detail(place_id, has_booking))
    await callback.answer()

@router.callback_query(F.data.startswith("menu_"))
async def show_categories(callback: CallbackQuery):
    place_id = int(callback.data.split("_")[1])
    categories = await db.get_categories(place_id)
    if not categories:
        await callback.message.edit_text("Меню пока не добавлено.", reply_markup=keyboards.place_detail(place_id, False))
        await callback.answer()
        return
    await callback.message.edit_text("Выберите раздел меню:", reply_markup=keyboards.categories_list(place_id, categories))
    await callback.answer()

@router.callback_query(F.data.startswith("cat_"))
async def show_items(callback: CallbackQuery):
    parts = callback.data.split("_")
    cat_id = int(parts[1])
    place_id = int(parts[2])
    items = await db.get_menu_items(cat_id)
    if not items:
        await callback.answer("В этом разделе пока пусто")
        return
    text = ""
    for item in items:
        price = f"{item['price']:.2f} руб." if item.get('price') else "Цена не указана"
        text += f"**{item['name']}**\n{item.get('description','')}\n*{price}*\n\n"
    await callback.message.edit_text(text, reply_markup=keyboards.items_list(cat_id, place_id))
    await callback.answer()

@router.callback_query(F.data.startswith("route_"))
async def build_route(callback: CallbackQuery):
    place_id = int(callback.data.split("_")[1])
    place = await db.get_place_by_id(place_id)
    if not place:
        await callback.answer("Заведение не найдено")
        return
    await callback.message.answer(
        f"🚗 Маршрут до **{place['name']}**",
        reply_markup=keyboards.route_keyboard(place['name'], place['latitude'], place['longitude'])
    )
    await callback.answer()

@router.callback_query(F.data.startswith("book_"))
async def book_table(callback: CallbackQuery):
    place_id = int(callback.data.split("_")[1])
    place = await db.get_place_by_id(place_id)
    if not place or not place.get('booking_phone'):
        await callback.answer("Бронь недоступна")
        return
    await callback.message.answer(
        f"☎️ Телефон для бронирования:\n**{place['booking_phone']}**",
        reply_markup=keyboards.booking_keyboard(place['booking_phone'])
    )
    await callback.answer()

# Админ-команда статистики
@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Нет доступа")
        return
    total = await db.get_users_count()
    new_7d = await db.get_new_users_since(7)
    new_today = await db.get_new_users_since(1)
    await message.answer(
        f"📊 **Статистика**\n"
        f"👥 Всего пользователей: {total}\n"
        f"🆕 Новых за 7 дней: {new_7d}\n"
        f"📅 Новых сегодня: {new_today}"
    )