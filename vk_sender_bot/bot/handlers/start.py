import html
import os
import resource
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from bot.config import config
from bot.services.vk_service import vk_service
from bot.keyboards.inline import get_main_menu_keyboard, get_back_to_menu_keyboard

router = Router(name="start_router")

START_TEXT = (
    "👋 <b>Добро пожаловать в панель управления рассылкой ВКонтакте!</b>\n\n"
    "Этот бот позволяет выполнять рассылку сообщений по страницам и чатам ВКонтакте напрямую от вашего имени или от сообщества.\n\n"
    "📋 <b>Как запустить рассылку:</b>\n"
    "1️⃣ Нажмите <b>«📤 Начать рассылку»</b>\n"
    "2️⃣ Отправьте <b>.txt файл со ссылками</b> (каждая ссылка с новой строки) или пришлите их текстом\n"
    "3️⃣ Введите текст рассылаемого сообщения\n"
    "4️⃣ Проверьте параметры и подтвердите запуск!\n\n"
    "⚙️ Проверить подключенные аккаунты можно в разделе настроек."
)

HELP_TEXT = (
    "📖 <b>Инструкция по работе с ботом</b>\n\n"
    "<b>1. Формат ссылок в .txt файле:</b>\n"
    "Файл должен содержать по одной ссылке на каждой строке, например:\n"
    "<code>https://vk.com/id12345678</code>\n"
    "<code>https://vk.com/durov</code>\n"
    "<code>https://vk.com/club123456</code>\n"
    "<code>https://vk.com/im?sel=12345678</code>\n\n"
    "<b>2. Режимы отправки:</b>\n"
    "• <b>Личный аккаунт:</b> отправка напрямую в ЛС пользователям от вашего имени (через сессию ВКонтакте, без тяжелого браузера, потребление <30 МБ RAM).\n"
    "• <b>Сообщество (Группа):</b> отправка сообщений по беседам и подписчикам группы через токен сообщества.\n\n"
    "<b>3. Защита от спам-фильтров:</b>\n"
    "В настройках можно настроить задержку между отправками сообщений (по умолчанию 2 секунды).\n\n"
    "<b>4. Быстрые команды:</b>\n"
    "• /start — Главное меню\n"
    "• /broadcast — Начать рассылку\n"
    "• /logout — Выход из аккаунта и сброс сессий\n"
    "• /cancel — Отмена текущего действия"
)

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(START_TEXT, reply_markup=get_main_menu_keyboard())

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=get_back_to_menu_keyboard())

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено.", reply_markup=get_main_menu_keyboard())

@router.callback_query(F.data == "nav:main_menu")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await call.message.edit_text(START_TEXT, reply_markup=get_main_menu_keyboard())

@router.callback_query(F.data == "nav:help")
async def cb_help(call: CallbackQuery):
    await call.answer()
    await call.message.edit_text(HELP_TEXT, reply_markup=get_back_to_menu_keyboard())

@router.callback_query(F.data == "nav:status")
async def cb_status(call: CallbackQuery):
    await call.answer()
    telegram_id = call.from_user.id
    status = await vk_service.get_status_info(telegram_id)
    
    if status["user_connected"]:
        auth_type_str = f" [{status['auth_type']}]" if status.get("auth_type") else ""
        user_line = f"🟢 <b>{html.escape(status['user_name'])}</b> (ID: <code>{status['user_id']}</code>){auth_type_str}"
    else:
        user_line = "❌ <i>Не подключен</i>"

    if status["group_connected"]:
        group_line = f"🟢 <b>{html.escape(status['group_name'])}</b> (ID: <code>{status['group_id']}</code>)"
    elif config.vk_token:
        group_line = "🟡 <i>Токен указан</i>"
    else:
        group_line = "❌ <i>Не подключен</i>"

    # Memory calculation
    usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    usage_mb = usage_kb / 1024.0

    text = (
        "📊 <b>Текущий статус системы</b>\n\n"
        f"• <b>Личный аккаунт:</b> {user_line}\n"
        f"• <b>Сообщество VK:</b> {group_line}\n"
        f"• <b>Интервал отправки:</b> <code>{status['delay']} сек</code>\n"
        f"• <b>Telegram Bot:</b> Онлайн 🟢\n"
        f"• <b>Использование памяти:</b> <code>~{usage_mb:.1f} МБ</code> (Лимит VPS: 1024 МБ)\n"
    )
    await call.message.edit_text(text, reply_markup=get_back_to_menu_keyboard())
