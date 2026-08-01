import asyncio
import logging
import sys
import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

from config import BOT_TOKEN, ADMIN_ID
from database import db
from handlers import (
    start, help_command, check_vin_start, process_vin,
    confirm_vin, my_checks, parts_estimate, pro_info,
    buy_pro, main_menu, button_handler
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Состояния
WAITING_FOR_VIN = 1

async def main():
    # Создаём приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handler для VIN
    vin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(check_vin_start, pattern="^check_vin$")],
        states={
            WAITING_FOR_VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_vin)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: u.message.reply_text("Отменено"))]
    )
    
    # Обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(vin_conv)
    application.add_handler(CallbackQueryHandler(confirm_vin, pattern="^confirm_vin:"))
    application.add_handler(CallbackQueryHandler(my_checks, pattern="^my_checks$"))
    application.add_handler(CallbackQueryHandler(parts_estimate, pattern="^parts_estimate$"))
    application.add_handler(CallbackQueryHandler(pro_info, pattern="^pro_info$"))
    application.add_handler(CallbackQueryHandler(buy_pro, pattern="^buy_pro$"))
    application.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    
    logger.info("🚀 Бот запущен!")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    asyncio.run(main())
