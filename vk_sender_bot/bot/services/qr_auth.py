import asyncio
import html
import io
import logging
import os
import re
from typing import Dict, Optional
from aiogram import Bot
from aiogram.types import Message, BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder
from PIL import Image
from playwright.async_api import async_playwright
from pyzbar.pyzbar import decode

from bot.database import save_user_vk_session
from bot.services.vk_service import vk_service

logger = logging.getLogger(__name__)

ACTIVE_QR_SESSIONS: Dict[int, dict] = {}
QR_SEMAPHORE = asyncio.Semaphore(2)

def is_qr_auth_active(telegram_id: int) -> bool:
    return telegram_id in ACTIVE_QR_SESSIONS

async def submit_auth_code(telegram_id: int, code: str) -> bool:
    session = ACTIVE_QR_SESSIONS.get(telegram_id)
    if session and "code_queue" in session:
        await session["code_queue"].put(code.strip())
        return True
    return False

def get_qr_auth_keyboard(qr_url: Optional[str] = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if qr_url:
        builder.button(text="🌐 Открыть вход в браузере", url=qr_url)
    builder.button(text="❌ Отменить вход", callback_data="qr:cancel")
    builder.adjust(1)
    return builder.as_markup()

async def run_qr_auth_flow(telegram_id: int, bot: Bot, chat_id: int, status_message_id: int):
    async with QR_SEMAPHORE:
        p = None
        browser = None
        photo_msg = None
        code_queue = asyncio.Queue()

        session_entry = {
            "task": asyncio.current_task(),
            "code_queue": code_queue,
            "bot": bot,
            "chat_id": chat_id,
        }
        ACTIVE_QR_SESSIONS[telegram_id] = session_entry

        try:
            p = await async_playwright().start()
            chrome_bin = "/opt/google/chrome/chrome"
            exec_path = chrome_bin if os.path.exists(chrome_bin) else None
            browser = await p.chromium.launch(
                executable_path=exec_path,
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check"
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text=(
                        "⏳ <b>Запускаю сессию и генерирую QR-код...</b>\n\n"
                        "⚠️ Запуск защищённой сессии может занять <b>от 1 до 3 минут</b> (в зависимости от нагрузки сервера). Пожалуйста, ожидайте..."
                    )
                )
            except Exception:
                pass

            await page.goto("https://vk.ru", wait_until="domcontentloaded", timeout=20000)

            # Wait for QR frame
            frame_el = page.frame_locator('iframe[src*="qr_auth"]')
            await frame_el.locator("body").wait_for(timeout=15000)
            await page.wait_for_timeout(2000)

            qr_png = await frame_el.locator("body").screenshot()

            # Decode the QR URL for 1-click mobile browser login
            qr_url = None
            try:
                decoded_list = decode(Image.open(io.BytesIO(qr_png)))
                if decoded_list:
                    qr_url = decoded_list[0].data.decode("utf-8")
                    logger.info("Decoded QR url for user %s: %s", telegram_id, qr_url)
            except Exception as e:
                logger.warning("Could not decode QR url: %s", e)

            # Delete the previous text message and send photo
            try:
                await bot.delete_message(chat_id=chat_id, message_id=status_message_id)
            except Exception:
                pass

            caption = (
                "📱 <b>Отсканируйте QR-код для входа ВКонтакте</b>\n\n"
                "1. Откройте приложение <b>ВКонтакте</b> на телефоне ➔ сканер QR (вверху экрана «Сервисы»).\n"
                "2. Наведите камеру на этот код.\n\n"
                "🔢 <b>ЕСЛИ НА ТЕЛЕФОНЕ ВЫСВЕТИЛСЯ 6-ЗНАЧНЫЙ КОД:</b>\n"
                "👉 <b>Просто отправьте эти 6 цифр ответным сообщением прямо сюда в чат!</b>\n"
                "<i>(например: <code>123456</code>) — бот моментально введёт их в сессию.</i>\n\n"
                "🌐 <i>Либо нажмите кнопку <b>«Открыть вход в браузере»</b> ниже.</i>\n\n"
                "⏳ <i>Сессия по QR-коду может загружаться 1–3 минуты, пожалуйста ожидайте... (код активен 3 минуты)</i>"
            )

            photo_msg = await bot.send_photo(
                chat_id=chat_id,
                photo=BufferedInputFile(qr_png, filename="vk_qr.png"),
                caption=caption,
                reply_markup=get_qr_auth_keyboard(qr_url)
            )
            session_entry["photo_msg"] = photo_msg

            # Poll for authorization or 6-digit code entry
            found_sid = None
            found_nsid = None
            last_screen_hash = None

            for step in range(150):  # 150 * 1.2s = 180 seconds (3 full minutes)
                await asyncio.sleep(1.2)

                # Check if user submitted a code via Telegram message
                while not code_queue.empty():
                    raw_code = code_queue.get_nowait()
                    digits = re.sub(r'\D', '', raw_code)
                    if digits:
                        logger.info("Injecting auth code for user %s: %s", telegram_id, digits)
                        typed = False

                        # Search inputs across all frames and page
                        for target in [page] + page.frames:
                            try:
                                inputs = await target.locator('input:not([type="hidden"])').all()
                                if inputs:
                                    logger.info("Found %d input(s) in %s", len(inputs), target.url)
                                    if len(inputs) == 1:
                                        await inputs[0].fill(digits)
                                        await inputs[0].press("Enter")
                                        typed = True
                                        break
                                    elif len(inputs) == len(digits):
                                        for idx, digit in enumerate(digits):
                                            await inputs[idx].fill(digit)
                                        typed = True
                                        break
                            except Exception as e:
                                logger.debug("Input fill error: %s", e)

                        # Strategy 2: Focus and use keyboard
                        if not typed:
                            try:
                                if frame_el:
                                    await frame_el.locator("body").click()
                                await page.keyboard.type(digits, delay=80)
                                await page.keyboard.press("Enter")
                                typed = True
                            except Exception as e:
                                logger.warning("Keyboard type error: %s", e)

                        # Click any submit/continue buttons
                        for btn_text in ["Подтвердить", "Продолжить", "Войти", "Отправить", "Готово"]:
                            for target in [page] + page.frames:
                                try:
                                    btn = target.locator(f'button:has-text("{btn_text}")')
                                    if await btn.count() > 0 and await btn.is_visible():
                                        await btn.click()
                                        break
                                except Exception:
                                    pass

                        await asyncio.sleep(1.0)

                # Check cookies for successful login
                cookies = await context.cookies()
                for c in cookies:
                    if c["name"] == "remixsid":
                        found_sid = c["value"]
                    elif c["name"] == "remixnsid":
                        found_nsid = c["value"]

                current_url = page.url
                if found_sid or "feed" in current_url or "im" in current_url:
                    logger.info("QR Auth success detected for user %s", telegram_id)
                    break

                # If the screen changes (e.g. 2FA confirmation prompt appears), update the photo
                if step % 3 == 0 and photo_msg:
                    try:
                        body_text = await frame_el.locator("body").inner_text()
                        if body_text and len(body_text.strip()) > 5 and body_text != last_screen_hash:
                            last_screen_hash = body_text
                            updated_png = await frame_el.locator("body").screenshot()
                            await photo_msg.edit_media(
                                media=InputMediaPhoto(
                                    media=BufferedInputFile(updated_png, filename="vk_confirm.png"),
                                    caption=(
                                        "🔢 <b>Подтверждение входа ВКонтакте</b>\n\n"
                                        f"{html.escape(body_text.strip()[:250])}\n\n"
                                        "👉 <b>Отправьте высветившийся код ответным сообщением сюда в чат!</b>"
                                    )
                                ),
                                reply_markup=get_qr_auth_keyboard(qr_url)
                            )
                    except Exception:
                        pass

            if not found_sid:
                if photo_msg:
                    try:
                        await photo_msg.edit_caption(
                            caption="⏱ <b>Время ожидания сессии истекло (3 минуты).</b>\nВы можете попробовать снова в настройках."
                        )
                    except Exception:
                        pass
                return

            # Let VK finalize session cookies
            await page.wait_for_timeout(1000)
            cookies = await context.cookies()
            for c in cookies:
                if c["name"] == "remixsid":
                    found_sid = c["value"]
                elif c["name"] == "remixnsid":
                    found_nsid = c["value"]

            # Immediately close browser to release 100% of RAM
            if browser:
                try:
                    await browser.close()
                    browser = None
                except Exception:
                    pass
            if p:
                try:
                    await p.stop()
                    p = None
                except Exception:
                    pass
            logger.info("Playwright browser closed early and RAM released for user %s", telegram_id)

            # Save initial session cookies to database
            await save_user_vk_session(
                telegram_id=telegram_id,
                vk_id=0,
                vk_name="Пользователь VK",
                remixsid=found_sid,
                remixnsid=found_nsid or ""
            )
            vk_service.clear_user_cache(telegram_id)

            # Asynchronously fetch user profile via non-blocking aiohttp in vk_service
            status = await vk_service.get_status_info(telegram_id)
            vk_name = status.get("user_name") or "Пользователь VK"
            vk_id = status.get("user_id") or 0

            # Update database with profile name and ID
            await save_user_vk_session(
                telegram_id=telegram_id,
                vk_id=vk_id,
                vk_name=vk_name,
                remixsid=found_sid,
                remixnsid=found_nsid or ""
            )

            success_caption = (
                f"🎉 <b>Вход успешно выполнен!</b>\n\n"
                f"👤 Аккаунт: <b>{vk_name}</b>\n"
                f"🆔 VK ID: <code>{vk_id}</code>\n\n"
                "✅ <b>Доверенная веб-сессия сохранена.</b>\n"
                "Теперь вы можете загружать базу ссылок и запускать рассылку без ограничений!"
            )

            if photo_msg:
                try:
                    await photo_msg.edit_caption(caption=success_caption)
                except Exception:
                    await bot.send_message(chat_id=chat_id, text=success_caption)
            else:
                await bot.send_message(chat_id=chat_id, text=success_caption)

        except asyncio.CancelledError:
            logger.info("QR auth cancelled for user %s", telegram_id)
        except Exception as e:
            logger.exception("Error in QR auth flow: %s", e)
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Ошибка при входе: {str(e)[:100]}"
                )
            except Exception:
                pass
        finally:
            ACTIVE_QR_SESSIONS.pop(telegram_id, None)
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            if p:
                try:
                    await p.stop()
                except Exception:
                    pass

def cancel_qr_auth(telegram_id: int):
    session = ACTIVE_QR_SESSIONS.get(telegram_id)
    if session:
        task = session.get("task")
        if task and not task.done():
            task.cancel()
