from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Проверить VIN", callback_data="check_vin")],
        [InlineKeyboardButton("📊 Мои проверки", callback_data="my_checks")],
        [InlineKeyboardButton("🔧 Запчасти и цены", callback_data="parts_estimate")],
        [InlineKeyboardButton("💎 PRO подписка", callback_data="pro_info")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
    ])

def back_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ В главное меню", callback_data="main_menu")],
    ])

def pro_upgrade_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Купить PRO $9.99/мес", callback_data="buy_pro")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")],
    ])

def parts_detail_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Полный отчёт (PRO)", callback_data="buy_pro")],
        [InlineKeyboardButton("◀️ В главное меню", callback_data="main_menu")],
    ])

def confirm_vin_keyboard(vin: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, проверить", callback_data=f"confirm_vin:{vin}")],
        [InlineKeyboardButton("❌ Ввести заново", callback_data="check_vin")],
        [InlineKeyboardButton("◀️ Отмена", callback_data="main_menu")],
    ])
