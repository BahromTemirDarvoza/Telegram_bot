import asyncio
import logging
from aiogram import Bot, Dispatcher
from config import load_config
from db.base import Base
from db.session import engine
from middlewares import DbSessionMiddleware
from handlers import start, admin, worker, messaging, akfa

async def main():
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bot = Bot(cfg.bot_token)
    dp = Dispatcher()
    dp.update.middleware(DbSessionMiddleware())

    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(worker.router)
    dp.include_router(akfa.router)
    dp.include_router(messaging.router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Bot to‘xtatildi.")