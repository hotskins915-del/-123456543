import asyncio
import html
import re
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from bot.config import config
from bot.database import (
    get_user,
    save_user_vk_session,
    save_user_token,
    clear_user_vk,
    update_user_delay
)
from bot.services.vk_service import vk_service
from bot.services.qr_auth import run_qr_auth_flow, cancel_qr_auth, is_qr_auth_active, submit_auth_code
from bot.states.broadcast import SettingsStates
from bot.keyboards.inline import (
    get_settings_keyboard,
    get_logout_keyboard,
    get_confirm_logout_keyboard,
    get_manual_cookies_keyboard,
    get_cancel_keyboard,
    get_back_to_menu_keyboard
)

router = Router(name="settings_router")

OAUTH_URL = (
    "https://oauth.vk.com/authorize?"
    "client_id=2685278&scope=messages,offline&"
    "redirect_uri=https://oauth.vk.com/blank.html&"
    "response_type=token&v=5.199"
)

def extract_token_from_raw(raw: str) -> str:
    raw = raw.strip()
    match = re.search(r'access_token=([a-zA-Z0-9_\-\.]+)', raw)
    if match:
        return match.group(1)
    if raw.startswith("vk1.a.") or len(raw) > 30:
        return raw.split('&')[0].split('#')[0].strip()
    return raw

async def render_settings_text(telegram_id: int) -> str:
    status = await vk_service.get_status_info(telegram_id)
    user_data = await get_user(telegram_id)
    
    if status["user_connected"]:
        auth_type_str = f" ({status['auth_type']})" if status.get("auth_type") else ""
        user_info = f"🟢 <b>{html.escape(status['user_name'])}</b> [ID: <code>{status['user_id']}</code>]{auth_type_str}"
    elif user_data.get("remixsid") or user_data.get("user_token"):
        user_info = "🟡 <i>Данные указаны, проверка связи...</i>"
    else:
        user_info = "❌ <i>Не подключен</i>"

    if status["group_connected"]:
        group_info = f"🟢 <b>{html.escape(status['group_name'])}</b> [ID: <code>{status['group_id']}</code>]"
    elif config.has_group_auth:
        group_info = "🟡 <i>Токен указан</i>"
    else:
        group_info = "❌ <i>Не подключен</i>"

    delay_val = user_data.get("delay", 2.0)

    return (
        "⚙️ <b>Настройки интеграции ВКонтакте</b>\n\n"
        f"👤 <b>Личный аккаунт (рассылка в ЛС):</b>\n"
        f"{user_info}\n\n"
        f"👥 <b>Сообщество (рассылка по группам/беседам):</b>\n"
        f"{group_info}\n\n"
        f"⏱ <b>Интервал между сообщениями:</b> <code>{delay_val} сек</code>\n\n"
        "💡 <i>Выберите действие ниже:</i>"
    )

@router.message(Command("logout"))
async def cmd_logout(message: Message, state: FSMContext):
    await state.clear()
    telegram_id = message.from_user.id
    status = await vk_service.get_status_info(telegram_id)
    has_user = status["user_connected"]
    has_group = status["group_connected"]

    if not has_user and not has_group:
        await message.answer(
            "ℹ️ <b>Нет активных подключений к ВКонтакте.</b>\n\n"
            "Вы не авторизованы ни в личном аккаунте, ни в сообществе.",
            reply_markup=get_back_to_menu_keyboard()
        )
        return

    text = (
        "🚪 <b>Панель выхода из аккаунтов ВКонтакте</b>\n\n"
        f"• Личный аккаунт: {'🟢 ' + html.escape(status['user_name'] or 'Подключен') if has_user else '❌ Не подключен'}\n"
        f"• Сообщество: {'🟢 ' + html.escape(status['group_name'] or 'Подключено') if has_group else '❌ Не подключено'}\n\n"
        "Выберите, откуда вы хотите выйти:"
    )
    await message.answer(text, reply_markup=get_logout_keyboard(has_user, has_group))

@router.callback_query(F.data == "nav:settings")
async def cb_settings(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    telegram_id = call.from_user.id
    status = await vk_service.get_status_info(telegram_id)
    has_user = status["user_connected"]
    has_group = config.has_group_auth
    text = await render_settings_text(telegram_id)
    await call.message.edit_text(text, reply_markup=get_settings_keyboard(has_user, has_group))

# ----------------- QR CODE AUTH FLOW -----------------

@router.callback_query(F.data == "set:qr_auth")
async def cb_qr_auth(call: CallbackQuery, bot: Bot, state: FSMContext):
    await call.answer("Генерирую QR-код...")
    telegram_id = call.from_user.id
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    await state.set_state(SettingsStates.waiting_for_qr_code)

    try:
        await call.message.edit_text(
            "⏳ <b>Запускаю сессию и генерирую QR-код...</b>\n\n"
            "⚠️ Запуск защищённой сессии может занять <b>от 1 до 3 минут</b> (в зависимости от нагрузки сервера). Пожалуйста, ожидайте...",
            reply_markup=get_cancel_keyboard(to_menu=True)
        )
    except Exception:
        pass

    # Cancel any existing QR task for this user
    cancel_qr_auth(telegram_id)

    asyncio.create_task(
        run_qr_auth_flow(
            telegram_id=telegram_id,
            bot=bot,
            chat_id=chat_id,
            status_message_id=msg_id
        )
    )

@router.message(SettingsStates.waiting_for_qr_code, F.text)
async def process_qr_auth_code_state(message: Message, state: FSMContext):
    telegram_id = message.from_user.id
    raw_text = message.text.strip()
    digits = re.sub(r'\D', '', raw_text)

    if not is_qr_auth_active(telegram_id):
        await state.clear()
        await message.answer(
            "⚠️ Сессия авторизации истекла или не активна. Начните вход заново в настройках:",
            reply_markup=get_back_to_menu_keyboard()
        )
        return

    if not (3 <= len(digits) <= 10):
        await message.answer(
            "⚠️ Пожалуйста, введите цифры кода подтверждения (например: <code>123456</code>):",
            reply_markup=get_cancel_keyboard(to_menu=True)
        )
        return

    wait_msg = await message.answer(f"⏳ Ввожу код <code>{digits}</code> в сессию VK...")
    ok = await submit_auth_code(telegram_id, digits)
    if ok:
        await wait_msg.edit_text(f"✅ Код <code>{digits}</code> передан в браузер. Ожидаю завершения входа...")
    else:
        await wait_msg.edit_text("❌ Не удалось отправить код в сессию. Попробуйте еще раз.")

@router.message(F.text.regexp(r'^\s*\d[\d\s\-]{2,9}\d\s*$'))
async def process_raw_auth_code_fallback(message: Message):
    telegram_id = message.from_user.id
    if is_qr_auth_active(telegram_id):
        digits = re.sub(r'\D', '', message.text)
        wait_msg = await message.answer(f"⏳ Ввожу код <code>{digits}</code> в активную сессию VK...")
        ok = await submit_auth_code(telegram_id, digits)
        if ok:
            await wait_msg.edit_text(f"✅ Код <code>{digits}</code> передан в браузер. Ожидаю завершения входа...")

@router.callback_query(F.data == "qr:cancel")
async def cb_qr_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    telegram_id = call.from_user.id
    cancel_qr_auth(telegram_id)
    await call.answer("Вход по QR-коду отменен")
    try:
        await call.message.delete()
    except Exception:
        pass
    text = await render_settings_text(telegram_id)
    status = await vk_service.get_status_info(telegram_id)
    await call.bot.send_message(
        chat_id=call.message.chat.id,
        text=text,
        reply_markup=get_settings_keyboard(status["user_connected"], config.has_group_auth)
    )

# ----------------- LOGOUT FLOW -----------------

@router.callback_query(F.data == "set:logout_menu")
async def cb_logout_menu(call: CallbackQuery):
    await call.answer()
    telegram_id = call.from_user.id
    status = await vk_service.get_status_info(telegram_id)
    has_user = status["user_connected"]
    has_group = status["group_connected"]

    text = (
        "🚪 <b>Управление сессиями и выход из аккаунта</b>\n\n"
        "Здесь вы можете безопасно завершить сессию в боте.\n\n"
        f"👤 <b>Личный аккаунт:</b> {'🟢 ' + html.escape(status['user_name'] or 'Подключен') if has_user else '❌ Отключен'}\n"
        f"👥 <b>Сообщество:</b> {'🟢 ' + html.escape(status['group_name'] or 'Подключено') if has_group else '❌ Отключено'}\n\n"
        "Выберите действие:"
    )
    await call.message.edit_text(text, reply_markup=get_logout_keyboard(has_user, has_group))

@router.callback_query(F.data == "logout:ask_user")
async def cb_ask_logout_user(call: CallbackQuery):
    await call.answer()
    telegram_id = call.from_user.id
    status = await vk_service.get_status_info(telegram_id)
    name = status["user_name"] or "личного профиля"
    text = (
        f"❓ <b>Подтверждение выхода</b>\n\n"
        f"Вы уверены, что хотите выйти из личного аккаунта <b>{html.escape(name)}</b>?\n\n"
        "⚠️ <i>Все сохранённые ключи сессии и токен будут немедленно стёрты из базы бота.</i>"
    )
    await call.message.edit_text(text, reply_markup=get_confirm_logout_keyboard("user"))

@router.callback_query(F.data == "logout:ask_group")
async def cb_ask_logout_group(call: CallbackQuery):
    await call.answer()
    telegram_id = call.from_user.id
    status = await vk_service.get_status_info(telegram_id)
    name = status["group_name"] or "сообщества"
    text = (
        f"❓ <b>Подтверждение отключения группы</b>\n\n"
        f"Вы уверены, что хотите отключить токен сообщества <b>{html.escape(name)}</b>?"
    )
    await call.message.edit_text(text, reply_markup=get_confirm_logout_keyboard("group"))

@router.callback_query(F.data == "logout:ask_all")
async def cb_ask_logout_all(call: CallbackQuery):
    await call.answer()
    text = (
        "❓ <b>Полный сброс всех подключений</b>\n\n"
        "Вы действительно хотите выйти со <b>всех</b> аккаунтов (личного и сообщества)?\n\n"
        "Бот перейдёт в исходное состояние симуляции."
    )
    await call.message.edit_text(text, reply_markup=get_confirm_logout_keyboard("all"))

@router.callback_query(F.data == "logout:do_user")
async def cb_do_logout_user(call: CallbackQuery, state: FSMContext):
    await state.clear()
    telegram_id = call.from_user.id
    await clear_user_vk(telegram_id)
    vk_service.clear_user_cache(telegram_id)
    await call.answer("Вы успешно вышли из личного аккаунта", show_alert=True)
    text = "✅ <b>Личный аккаунт успешно отключен.</b>\n\n" + await render_settings_text(telegram_id)
    await call.message.edit_text(text, reply_markup=get_settings_keyboard(False, config.has_group_auth))

@router.callback_query(F.data == "logout:do_group")
async def cb_do_logout_group(call: CallbackQuery, state: FSMContext):
    await state.clear()
    telegram_id = call.from_user.id
    config.clear_group_account()
    await call.answer("Сообщество отключено", show_alert=True)
    text = "✅ <b>Токен сообщества удален.</b>\n\n" + await render_settings_text(telegram_id)
    status = await vk_service.get_status_info(telegram_id)
    await call.message.edit_text(text, reply_markup=get_settings_keyboard(status["user_connected"], False))

@router.callback_query(F.data == "logout:do_all")
async def cb_do_logout_all(call: CallbackQuery, state: FSMContext):
    await state.clear()
    telegram_id = call.from_user.id
    await clear_user_vk(telegram_id)
    config.clear_group_account()
    vk_service.clear_user_cache(telegram_id)
    await call.answer("Все подключения сброшены", show_alert=True)
    text = "✅ <b>Все аккаунты отключены.</b> Бот работает в режиме симуляции.\n\n" + await render_settings_text(telegram_id)
    await call.message.edit_text(text, reply_markup=get_settings_keyboard(False, False))

# ----------------- OAUTH LOGIN FLOW -----------------

@router.callback_query(F.data == "set:oauth_link")
async def cb_oauth_link(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(SettingsStates.waiting_for_user_token)
    
    text = (
        "🔗 <b>Вход по официальной OAuth-ссылке VK</b>\n\n"
        "1. Перейдите по ссылке авторизации:\n"
        f"👉 <a href='{OAUTH_URL}'><b>НАЖМИТЕ СЮДА ДЛЯ ВХОДА В ВК</b></a>\n\n"
        "2. В открывшемся окне нажмите кнопку <b>«Разрешить»</b>.\n\n"
        "3. Браузер перенаправит вас на белую страницу. <b>Скопируйте адрес этой страницы из строки браузера</b> (или сам токен) и пришлите сюда ответным сообщением.\n\n"
        "⚠️ <b>Внимание:</b> ВКонтакте накладывает спам-фильтр (<b>Flood Control</b>) на массовые сообщения через токены сторонних приложений (Kate Mobile). Для стабильной рассылки без ограничений рекомендуется использовать кнопку <b>«📱 Войти через QR-код»</b>."
    )
    await call.message.edit_text(text, reply_markup=get_cancel_keyboard(to_menu=True), disable_web_page_preview=True)

@router.message(SettingsStates.waiting_for_user_token)
async def process_user_token(message: Message, state: FSMContext):
    telegram_id = message.from_user.id
    raw_input = message.text.strip() if message.text else ""
    token = extract_token_from_raw(raw_input)
    
    if len(token) < 20:
        await message.answer(
            "⚠️ Не удалось распознать токен. Скопируйте полный URL из адресной строки браузера и отправьте еще раз:",
            reply_markup=get_cancel_keyboard(to_menu=True)
        )
        return

    wait_msg = await message.answer("⏳ Проверяю токен аккаунта ВКонтакте...")
    
    ok, res = await vk_service.call_vk_api(
        telegram_id,
        "users.get",
        {"fields": "first_name,last_name"},
        user_mode=True,
        explicit_token=token
    )
    await wait_msg.delete()
    
    if not ok or not res or len(res) == 0:
        await message.answer(
            f"❌ Не удалось авторизоваться по этому токену: {html.escape(str(res))}\n\n"
            "Попробуйте перейти по ссылке еще раз или нажмите /cancel.",
            reply_markup=get_cancel_keyboard(to_menu=True)
        )
        return

    user = res[0]
    user_name = f"{user.get('first_name')} {user.get('last_name')}"
    vk_id = user.get("id")
    await save_user_token(telegram_id, vk_id, user_name, token)
    vk_service.clear_user_cache(telegram_id)
    await state.clear()
    
    success_text = (
        f"✅ <b>Вход успешно выполнен!</b>\n\n"
        f"👤 Аккаунт: <b>{html.escape(user_name)}</b>\n"
        f"🆔 ID: <code>{vk_id}</code>\n"
        f"🔑 Авторизация: <b>OAuth User Token</b>\n\n"
        "Теперь вы можете загружать ссылки и запускать рассылку!"
    )
    await message.answer(success_text, reply_markup=get_settings_keyboard(True, config.has_group_auth))

# ----------------- MANUAL COOKIES FLOW -----------------

@router.callback_query(F.data == "set:manual_cookies")
async def cb_manual_cookies(call: CallbackQuery):
    await call.answer()
    text = (
        "🍪 <b>Ручная настройка кук сессии ВКонтакте</b>\n\n"
        "Для работы веб-сессии требуется указать две куки из браузера:\n"
        "• <code>remixsid</code> — ключ сессии\n"
        "• <code>remixnsid</code> — токен авторизации (начинается на vk1.a...)\n\n"
        "<i>Выберите, какую куку хотите обновить:</i>"
    )
    await call.message.edit_text(text, reply_markup=get_manual_cookies_keyboard())

@router.callback_query(F.data == "set:remixsid")
async def cb_set_remixsid(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(SettingsStates.waiting_for_remixsid)
    text = (
        "👤 <b>Введите значение куки remixsid:</b>\n\n"
        "Отправьте значение ответным сообщением:"
    )
    await call.message.edit_text(text, reply_markup=get_cancel_keyboard(to_menu=True))

@router.message(SettingsStates.waiting_for_remixsid)
async def process_remixsid(message: Message, state: FSMContext):
    telegram_id = message.from_user.id
    sid = message.text.strip() if message.text else ""
    if len(sid) < 10:
        await message.answer("⚠️ Значение слишком короткое. Введите еще раз или нажмите /cancel:")
        return

    user_data = await get_user(telegram_id)
    await save_user_vk_session(
        telegram_id=telegram_id,
        vk_id=user_data.get("vk_id") or 0,
        vk_name=user_data.get("vk_name") or "Пользователь VK",
        remixsid=sid,
        remixnsid=user_data.get("remixnsid") or ""
    )
    vk_service.clear_user_cache(telegram_id)
    await state.clear()
    text = "✅ <b>remixsid сохранен!</b>\n\n" + await render_settings_text(telegram_id)
    status = await vk_service.get_status_info(telegram_id)
    await message.answer(text, reply_markup=get_settings_keyboard(status["user_connected"], config.has_group_auth))

@router.callback_query(F.data == "set:remixnsid")
async def cb_set_remixnsid(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(SettingsStates.waiting_for_remixnsid)
    text = (
        "🛡 <b>Введите значение куки remixnsid:</b>\n\n"
        "Отправьте значение ответным сообщением:"
    )
    await call.message.edit_text(text, reply_markup=get_cancel_keyboard(to_menu=True))

@router.message(SettingsStates.waiting_for_remixnsid)
async def process_remixnsid(message: Message, state: FSMContext):
    telegram_id = message.from_user.id
    nsid = message.text.strip() if message.text else ""
    if len(nsid) < 15:
        await message.answer("⚠️ Значение слишком короткое. Введите еще раз или нажмите /cancel:")
        return

    user_data = await get_user(telegram_id)
    await save_user_vk_session(
        telegram_id=telegram_id,
        vk_id=user_data.get("vk_id") or 0,
        vk_name=user_data.get("vk_name") or "Пользователь VK",
        remixsid=user_data.get("remixsid") or "",
        remixnsid=nsid
    )
    vk_service.clear_user_cache(telegram_id)
    await state.clear()
    text = "✅ <b>remixnsid сохранен!</b>\n\n" + await render_settings_text(telegram_id)
    status = await vk_service.get_status_info(telegram_id)
    await message.answer(text, reply_markup=get_settings_keyboard(status["user_connected"], config.has_group_auth))

# ----------------- DELAY SETTINGS -----------------

@router.callback_query(F.data == "set:delay")
async def cb_set_delay(call: CallbackQuery, state: FSMContext):
    await call.answer()
    telegram_id = call.from_user.id
    user_data = await get_user(telegram_id)
    current_delay = user_data.get("delay", 2.0)
    await state.set_state(SettingsStates.waiting_for_delay)
    text = (
        "⏱ <b>Введите задержку между сообщениями (в секундах):</b>\n\n"
        "Рекомендуемое значение: от <code>1.5</code> до <code>3.0</code> секунд для исключения блокировок.\n"
        f"Текущее значение: <code>{current_delay}</code> сек."
    )
    await call.message.edit_text(text, reply_markup=get_cancel_keyboard(to_menu=True))

@router.message(SettingsStates.waiting_for_delay)
async def process_delay(message: Message, state: FSMContext):
    telegram_id = message.from_user.id
    text = message.text.strip().replace(",", ".") if message.text else ""
    try:
        val = float(text)
        if val < 0.2:
            await message.answer("⚠️ Минимальная задержка — 0.2 сек. Введите значение еще раз:")
            return
        await update_user_delay(telegram_id, val)
        await state.clear()
        text = f"✅ Задержка успешно обновлена: <code>{val} сек</code>\n\n" + await render_settings_text(telegram_id)
        status = await vk_service.get_status_info(telegram_id)
        await message.answer(text, reply_markup=get_settings_keyboard(status["user_connected"], config.has_group_auth))
    except ValueError:
        await message.answer("⚠️ Пожалуйста, введите число (например: <code>1.5</code> или <code>2</code>):")
