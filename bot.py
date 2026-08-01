import logging
import sys
import asyncio

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

from config import BOT_TOKEN
from handlers import (
    start, help_command, check_vin_start, process_vin,
    confirm_vin, my_checks, parts_estimate, pro_info,
    buy_pro, main_menu
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

WAITING_FOR_VIN = 1

async def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    vin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(check_vin_start, pattern="^check_vin$")],
        states={
            WAITING_FOR_VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_vin)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: u.message.reply_text("Отменено"))],
        per_message=False
    )
    
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
    
    # Ручной запуск polling (совместимо с Python 3.11+)
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    # Держим бота запущенным
    stop_event = asyncio.Event()
    await stop_event.wait()

if __name__ == "__main__":
    asyncio.run(main())
