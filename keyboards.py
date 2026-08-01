from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Проверить VIN", callback_data="check_vin")],
        [InlineKeyboardButton(text="📊 Мои проверки", callback_data="my_checks")],
        [InlineKeyboardButton(text="🔧 Запчасти и цены", callback_data="parts_estimate")],
        [InlineKeyboardButton(text="💎 PRO подписка", callback_data="pro_info")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
    ])

def back_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="main_menu")],
    ])

def pro_upgrade_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Купить PRO $9.99/мес", callback_data="buy_pro")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
    ])

def parts_detail_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Полный отчёт (PRO)", callback_data="buy_pro")],
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="main_menu")],
    ])

def confirm_vin_keyboard(vin: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, проверить", callback_data=f"confirm_vin:{vin}")],
        [InlineKeyboardButton(text="❌ Ввести заново", callback_data="check_vin")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data="main_menu")],
    ])
