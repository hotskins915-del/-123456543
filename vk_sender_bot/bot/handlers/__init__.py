from aiogram import Dispatcher
from bot.handlers.start import router as start_router
from bot.handlers.settings import router as settings_router
from bot.handlers.broadcast import router as broadcast_router

def setup_routers(dp: Dispatcher):
    dp.include_router(start_router)
    dp.include_router(settings_router)
    dp.include_router(broadcast_router)
