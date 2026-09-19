import asyncio
import html
import io
import time
from typing import Dict, List

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import config
from bot.database import get_user
from bot.services.parser import parse_links_from_content
from bot.services.vk_service import vk_service
from bot.states.broadcast import BroadcastStates
from bot.keyboards.inline import (
    get_confirm_broadcast_keyboard,
    get_broadcast_running_keyboard,
    get_cancel_keyboard,
    get_main_menu_keyboard,
    get_back_to_menu_keyboard,
)

router = Router(name="broadcast_router")

# In-memory flag to signal stop requests per chat_id
ACTIVE_BROADCASTS: Dict[int, bool] = {}

def make_progress_bar(current: int, total: int, length: int = 10) -> str:
    if total <= 0:
        return "░" * length
    fraction = min(1.0, current / total)
    filled = int(fraction * length)
    return "█" * filled + "░" * (length - filled)

@router.callback_query(F.data == "nav:new_broadcast")
@router.message(Command("broadcast"))
async def start_new_broadcast(event: Message | CallbackQuery, state: FSMContext):
    telegram_id = event.from_user.id
    status = await vk_service.get_status_info(telegram_id)

    # Check if user has an account connected
    if not status["user_connected"] and not status["group_connected"]:
        text = (
            "⚠️ <b>Внимание: аккаунт ВКонтакте не подключен!</b>\n\n"
            "Для запуска рассылки сообщений необходимо подключить ваш профиль ВКонтакте.\n\n"
            "Нажмите <b>«📱 Войти через QR-код»</b> — это займет 10 секунд и не требует ввода паролей."
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="📱 Войти через QR-код", callback_data="set:qr_auth")
        builder.button(text="⚙️ Все способы входа", callback_data="nav:settings")
        builder.button(text="« Главное меню", callback_data="nav:main_menu")
        builder.adjust(1)

        if isinstance(event, CallbackQuery):
            await event.answer()
            await event.message.edit_text(text, reply_markup=builder.as_markup())
        else:
            await event.answer(text, reply_markup=builder.as_markup())
        return

    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.answer()
        reply_func = event.message.edit_text
    else:
        reply_func = event.answer

    await state.set_state(BroadcastStates.waiting_for_links)
    text = (
        "📤 <b>Шаг 1 из 2: Загрузка ссылок ВКонтакте</b>\n\n"
        "Отправьте <b>.txt файл</b> со списком ссылок (каждая ссылка с новой строки).\n\n"
        "<i>Поддерживаются любые форматы:</i>\n"
        "• <code>https://vk.com/id12345678</code>\n"
        "• <code>https://vk.com/username</code>\n"
        "• <code>https://vk.me/username</code>\n"
        "• <code>https://vk.com/im?sel=12345</code>\n"
        "• <code>id12345678</code> или <code>username</code>\n\n"
        "<i>Вы также можете просто отправить список ссылок обычным текстом в чат.</i>"
    )
    await reply_func(text, reply_markup=get_cancel_keyboard(to_menu=True))

@router.message(BroadcastStates.waiting_for_links, F.document)
async def process_links_document(message: Message, state: FSMContext, bot: Bot):
    doc = message.document
    if not (doc.file_name and doc.file_name.lower().endswith((".txt", ".csv", ".log"))):
        await message.answer(
            "⚠️ Пожалуйста, отправьте текстовый файл с расширением <b>.txt</b> или пришлите ссылки текстом:",
            reply_markup=get_cancel_keyboard(to_menu=True)
        )
        return

    wait_msg = await message.answer("⏳ Читаю файл и обрабатываю ссылки...")

    try:
        file_io = io.BytesIO()
        await bot.download(doc, destination=file_io)
        raw_bytes = file_io.getvalue()
        
        # Detect encoding
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content = raw_bytes.decode("windows-1251")
            except UnicodeDecodeError:
                content = raw_bytes.decode("latin-1", errors="ignore")

        valid_links, total_lines, duplicates = parse_links_from_content(content)
        await wait_msg.delete()

        if not valid_links:
            await message.answer(
                "❌ В файле не найдено ни одной корректной ссылки ВКонтакте.\n"
                "Проверьте содержимое файла и отправьте его снова:",
                reply_markup=get_cancel_keyboard(to_menu=True)
            )
            return

        await state.update_data(links=valid_links)
        await state.set_state(BroadcastStates.waiting_for_message)

        preview_lines = "\n".join(f"• <code>{html.escape(lnk)}</code>" for lnk in valid_links[:3])
        if len(valid_links) > 3:
            preview_lines += f"\n<i>... и еще {len(valid_links) - 3}</i>"

        text = (
            "✅ <b>Ссылки успешно загружены и проверены!</b>\n\n"
            f"• <b>Всего строк:</b> {total_lines}\n"
            f"• <b>Уникальных адресатов:</b> <b>{len(valid_links)}</b>\n"
            f"• <b>Дубликатов отфильтровано:</b> {duplicates}\n\n"
            f"<b>Пример ссылок:</b>\n{preview_lines}\n\n"
            "📝 <b>Шаг 2 из 2: Текст сообщения</b>\n"
            "Отправьте текст сообщения, который будет разослан по этим адресатам:"
        )
        await message.answer(text, reply_markup=get_cancel_keyboard(to_menu=True))

    except Exception as e:
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка при обработке файла: {html.escape(str(e))}")

@router.message(BroadcastStates.waiting_for_links, F.text)
async def process_links_text(message: Message, state: FSMContext):
    content = message.text.strip()
    valid_links, total_lines, duplicates = parse_links_from_content(content)

    if not valid_links:
        await message.answer(
            "❌ Не найдено ни одной корректной ссылки ВКонтакте.\n"
            "Отправьте .txt файл или список ссылок текстом:",
            reply_markup=get_cancel_keyboard(to_menu=True)
        )
        return

    await state.update_data(links=valid_links)
    await state.set_state(BroadcastStates.waiting_for_message)

    preview_lines = "\n".join(f"• <code>{html.escape(lnk)}</code>" for lnk in valid_links[:3])
    if len(valid_links) > 3:
        preview_lines += f"\n<i>... и еще {len(valid_links) - 3}</i>"

    text = (
        "✅ <b>Ссылки успешно обработаны!</b>\n\n"
        f"• <b>Всего строк:</b> {total_lines}\n"
        f"• <b>Уникальных адресатов:</b> <b>{len(valid_links)}</b>\n"
        f"• <b>Дубликатов отфильтровано:</b> {duplicates}\n\n"
        f"<b>Пример ссылок:</b>\n{preview_lines}\n\n"
        "📝 <b>Шаг 2 из 2: Текст сообщения</b>\n"
        "Отправьте текст сообщения, который будет разослан по этим адресатам:"
    )
    await message.answer(text, reply_markup=get_cancel_keyboard(to_menu=True))

@router.message(BroadcastStates.waiting_for_message, F.text)
async def process_broadcast_message(message: Message, state: FSMContext):
    msg_text = message.text
    if not msg_text or not msg_text.strip():
        await message.answer("⚠️ Сообщение не может быть пустым. Введите текст рассылки:")
        return

    await state.update_data(broadcast_message=msg_text)
    await state.set_state(BroadcastStates.confirm_broadcast)

    data = await state.get_data()
    links = data.get("links", [])
    telegram_id = message.from_user.id

    user_data = await get_user(telegram_id)
    user_delay = float(user_data.get("delay", 2.0))
    est_seconds = int(len(links) * user_delay)
    est_str = f"{est_seconds} сек" if est_seconds < 60 else f"{est_seconds // 60} мин {est_seconds % 60} сек"

    status = await vk_service.get_status_info(telegram_id)
    if status["user_connected"]:
        vk_mode = f"🟢 Личный аккаунт ({html.escape(status['user_name'])})"
    elif status["group_connected"]:
        vk_mode = f"🟢 Сообщество ({html.escape(status['group_name'])})"
    else:
        vk_mode = "🟡 Режим симуляции (Без авторизации)"

    escaped_msg = html.escape(msg_text)
    if len(escaped_msg) > 500:
        escaped_msg = escaped_msg[:500] + "..."

    confirm_text = (
        "📋 <b>Подтверждение параметров рассылки</b>\n\n"
        f"• <b>Получателей:</b> <code>{len(links)}</code>\n"
        f"• <b>Интервал отправки:</b> <code>{user_delay} сек</code>\n"
        f"• <b>Ориентировочное время:</b> <code>~{est_str}</code>\n"
        f"• <b>Отправитель:</b> {vk_mode}\n\n"
        "💬 <b>Текст сообщения:</b>\n"
        f"<blockquote>{escaped_msg}</blockquote>\n\n"
        "<i>Нажмите «Запустить рассылку» для начала процесса:</i>"
    )
    await message.answer(confirm_text, reply_markup=get_confirm_broadcast_keyboard())

@router.callback_query(F.data == "bc:edit_text")
async def cb_edit_text(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(BroadcastStates.waiting_for_message)
    await call.message.edit_text(
        "📝 Введите новый текст сообщения для рассылки:",
        reply_markup=get_cancel_keyboard(to_menu=True)
    )

@router.callback_query(F.data == "bc:edit_links")
async def cb_edit_links(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(BroadcastStates.waiting_for_links)
    await call.message.edit_text(
        "📁 Отправьте новый <b>.txt файл</b> со ссылками или пришлите их текстом:",
        reply_markup=get_cancel_keyboard(to_menu=True)
    )

@router.callback_query(F.data == "bc:cancel")
async def cb_cancel_broadcast(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Рассылка отменена")
    await call.message.edit_text("❌ Рассылка отменена.", reply_markup=get_main_menu_keyboard())

@router.callback_query(F.data == "bc:stop")
async def cb_stop_broadcast(call: CallbackQuery):
    chat_id = call.message.chat.id
    ACTIVE_BROADCASTS[chat_id] = False
    await call.answer("⏹ Остановка рассылки...", show_alert=False)

@router.callback_query(F.data == "bc:start")
async def cb_start_broadcast(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    links: List[str] = data.get("links", [])
    msg_text: str = data.get("broadcast_message", "")

    if not links or not msg_text:
        await call.message.edit_text(
            "⚠️ Данные рассылки устарели или пусты. Начните заново.",
            reply_markup=get_main_menu_keyboard()
        )
        await state.clear()
        return

    chat_id = call.message.chat.id
    telegram_id = call.from_user.id
    user_data = await get_user(telegram_id)
    user_delay = float(user_data.get("delay", 2.0))

    ACTIVE_BROADCASTS[chat_id] = True
    await state.set_state(BroadcastStates.broadcasting)

    total = len(links)
    success_count = 0
    fail_count = 0
    errors_log = []
    was_stopped = False
    processed_count = 0

    progress_msg = await call.message.edit_text(
        f"🚀 <b>Рассылка запущена!</b>\n\n"
        f"Прогресс: [░░░░░░░░░░] 0/{total} (0%)\n"
        f"✅ Успешно: 0 | ❌ Ошибок: 0\n"
        f"<i>Инициализация...</i>",
        reply_markup=get_broadcast_running_keyboard()
    )

    start_time = time.time()
    last_update_time = start_time

    try:
        for idx, link in enumerate(links, start=1):
            if not ACTIVE_BROADCASTS.get(chat_id, True):
                was_stopped = True
                errors_log.append("Рассылка принудительно остановлена пользователем.")
                break

            processed_count = idx
            # Send via VK Service with telegram_id
            success, result_detail = await vk_service.send_message(telegram_id, link, msg_text)

            if success:
                success_count += 1
            else:
                fail_count += 1
                errors_log.append(f"{link}: {result_detail}")

            # Update progress message every 2.5 seconds or on the last item
            current_time = time.time()
            if current_time - last_update_time >= 2.5 or idx == total:
                pct = int((idx / total) * 100)
                bar = make_progress_bar(idx, total)
                status_line = f"Последняя: <code>{html.escape(link)}</code> -> {html.escape(result_detail)}"
                
                try:
                    await progress_msg.edit_text(
                        f"📤 <b>Рассылка выполняется...</b>\n\n"
                        f"Прогресс: [{bar}] {idx}/{total} ({pct}%)\n"
                        f"✅ Успешно: <b>{success_count}</b> | ❌ Ошибок: <b>{fail_count}</b>\n\n"
                        f"{status_line}",
                        reply_markup=get_broadcast_running_keyboard()
                    )
                    last_update_time = current_time
                except Exception:
                    pass

            # Delay to avoid rate limiting
            if idx < total:
                await asyncio.sleep(user_delay)

    finally:
        ACTIVE_BROADCASTS.pop(chat_id, None)
        await state.clear()

    elapsed = time.time() - start_time
    finish_title = "⏹ <b>Рассылка остановлена</b>" if was_stopped else "🏁 <b>Рассылка успешно завершена!</b>"

    summary_text = (
        f"{finish_title}\n\n"
        f"• <b>Всего обработано:</b> {processed_count if was_stopped else total}\n"
        f"• <b>✅ Успешно:</b> {success_count}\n"
        f"• <b>❌ Ошибок:</b> {fail_count}\n"
        f"• <b>⏱ Затраченное время:</b> {elapsed:.1f} сек\n\n"
    )

    if errors_log:
        if len(errors_log) <= 5:
            summary_text += "<b>Лог ошибок:</b>\n" + "\n".join(f"• {html.escape(e)}" for e in errors_log)
        else:
            summary_text += (
                f"<b>Лог ошибок (первые 5):</b>\n" +
                "\n".join(f"• {html.escape(e)}" for e in errors_log[:5]) +
                f"\n<i>... и еще {len(errors_log) - 5} ошибок.</i>"
            )

    try:
        await progress_msg.edit_text(summary_text, reply_markup=get_back_to_menu_keyboard())
    except Exception:
        await call.message.answer(summary_text, reply_markup=get_back_to_menu_keyboard())
