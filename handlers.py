async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = await db.get_or_create_user(update.effective_user.id, update.effective_user.username)
    except Exception as e:
        logger.error(f"DB error in start: {e}")
        user = None
    
    welcome_text = (
        "🚗 <b>АвтоЭксперт AI</b>\n\n"
        "Проверь авто по VIN за секунды:\n"
        "• 📋 Технические характеристики\n"
        "• ⚠️ Скрытые проблемы\n"
        "• 💰 Оценка рыночной стоимости\n"
        "• 🔧 Цены на запчасти\n\n"
    )
    
    if user and user.get("subscription_type") == "pro":
        welcome_text += "💎 У вас PRO подписка!\n\n"
    else:
        remaining = 3
        if user:
            remaining = max(0, FREE_CHECKS_LIMIT - user.get("checks_used", 0))
        welcome_text += f"🆓 Бесплатных проверок осталось: {remaining}\n\n"
    
    welcome_text += "Выберите действие:"
    
    await update.message.reply_text(welcome_text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
