import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import config
from bot.database import init_db
from bot.handlers import setup_routers
from bot.services.vk_service import vk_service

logger = logging.getLogger(__name__)

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    if not config.bot_token:
        logger.error("BOT_TOKEN не задан в .env файле!")
        sys.exit(1)

    logger.info("Инициализация базы данных...")
    await init_db()

    logger.info("Инициализация бота...")
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=MemoryStorage())

    setup_routers(dp)

    # Clean old webhook/updates
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот успешно запущен и ожидает обновлений...")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Остановка бота и закрытие сессий...")
        await vk_service.close()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
