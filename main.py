import asyncio
from aiogram import Bot, Dispatcher, BaseMiddleware
from config import BOT_TOKEN
from handlers import router
from db import update_last_active

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.include_router(router)

class ActivityMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if hasattr(event, 'from_user') and event.from_user:
            await update_last_active(event.from_user.id)
        return await handler(event, data)

dp.update.middleware(ActivityMiddleware())

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())