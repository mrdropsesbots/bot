from aiogram.utils.keyboard import InlineKeyboardBuilder

def main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🍽 Рестораны", callback_data="type_ресторан")
    builder.button(text="☕ Кофейни", callback_data="type_кофейня")
    builder.button(text="🍸 Бары", callback_data="type_бар")
    builder.adjust(1)
    return builder.as_markup()

def places_list(places, place_type, page=0, per_page=5):
    builder = InlineKeyboardBuilder()
    start = page * per_page
    end = start + per_page
    for place in places[start:end]:
        builder.button(text=place['name'], callback_data=f"place_{place['id']}")
    nav = []
    if page > 0:
        nav.append(("⬅️ Назад", f"type_{place_type}_{page-1}"))
    if end < len(places):
        nav.append(("Вперёд ➡️", f"type_{place_type}_{page+1}"))
    for text, cb in nav:
        builder.button(text=text, callback_data=cb)
    builder.button(text="🔙 В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def place_detail(place_id, has_booking=False):
    builder = InlineKeyboardBuilder()
    builder.button(text="📖 Показать меню", callback_data=f"menu_{place_id}")
    builder.button(text="🗺 Построить маршрут", callback_data=f"route_{place_id}")
    if has_booking:
        builder.button(text="📞 Забронировать", callback_data=f"book_{place_id}")
    builder.button(text="🔙 В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def categories_list(place_id, categories):
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(text=cat['name'], callback_data=f"cat_{cat['id']}_{place_id}")
    builder.button(text="🔙 К заведению", callback_data=f"place_{place_id}")
    builder.adjust(1)
    return builder.as_markup()

def items_list(category_id, place_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К разделам", callback_data=f"menu_{place_id}")
    builder.button(text="🔙 К заведению", callback_data=f"place_{place_id}")
    builder.adjust(1)
    return builder.as_markup()

def route_keyboard(place_name, lat, lon):
    url = f"https://yandex.by/maps/?mode=routes&rtext=~{lat},{lon}"
    builder = InlineKeyboardBuilder()
    builder.button(text="🚗 Открыть Яндекс.Карты", url=url)
    builder.adjust(1)
    return builder.as_markup()

def booking_keyboard(phone):
    builder = InlineKeyboardBuilder()
    builder.button(text="📞 Позвонить", url=f"tel:{phone}")
    builder.adjust(1)
    return builder.as_markup()