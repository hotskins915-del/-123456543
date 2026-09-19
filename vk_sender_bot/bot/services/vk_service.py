import asyncio
import json
import logging
import random
import re
import time
from typing import Any, Dict, Optional, Tuple
import aiohttp

from bot.config import config
from bot.database import get_user, save_user_vk_session, save_user_token

logger = logging.getLogger(__name__)

VK_API_VERSION = "5.199"
VK_API_GATEWAYS = [
    "https://api.vk.me/method",
    "https://api.vk.ru/method",
    "https://api.vk.com/method",
]
VK_IM_URL = "https://vk.ru/im"

WEB_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


class VKService:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._web_tokens: Dict[int, str] = {}
        self._web_token_expires_at: Dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "User-Agent": WEB_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            }
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=25.0)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def clear_user_cache(self, telegram_id: int):
        self._web_tokens.pop(telegram_id, None)
        self._web_token_expires_at.pop(telegram_id, None)

    async def get_user_web_token(self, telegram_id: int, force_refresh: bool = False) -> Optional[str]:
        """
        Retrieves or refreshes the user webToken for a specific Telegram user
        using their saved remixsid and remixnsid cookies.
        Lightweight, pure JSON/HTTP, <30MB RAM.
        """
        now = time.time()
        cached_token = self._web_tokens.get(telegram_id)
        cached_exp = self._web_token_expires_at.get(telegram_id, 0)

        if not force_refresh and cached_token and (cached_exp - now) > 60:
            return cached_token

        async with self._lock:
            cached_token = self._web_tokens.get(telegram_id)
            cached_exp = self._web_token_expires_at.get(telegram_id, 0)
            if not force_refresh and cached_token and (cached_exp - now) > 60:
                return cached_token

            user_data = await get_user(telegram_id)
            sid = (user_data.get("remixsid") or "").strip()
            nsid = (user_data.get("remixnsid") or "").strip()

            if not sid:
                return None

            session = await self.get_session()
            cookies = {
                "remixsid": sid,
                "remixlang": "0"
            }
            if nsid:
                cookies["remixnsid"] = nsid

            try:
                async with session.get(VK_IM_URL, cookies=cookies, allow_redirects=True) as resp:
                    if resp.status != 200:
                        logger.error("Failed to load vk.ru/im for user %s: HTTP %s", telegram_id, resp.status)
                        return None

                    final_url = str(resp.url)
                    if "act=logout" in final_url or "login.vk" in final_url:
                        logger.warning("Session cookies invalidated by VK for user %s", telegram_id)
                        return None

                    html_text = await resp.text()

                    match = re.search(r'webToken:\s*({[^}]+})', html_text)
                    if match:
                        token_data = json.loads(match.group(1))
                        token = token_data.get("access_token")
                        exp = float(token_data.get("expired_at", now + 900))
                        if token:
                            self._web_tokens[telegram_id] = token
                            self._web_token_expires_at[telegram_id] = exp
                            logger.info("Successfully acquired VK webToken for user %s", telegram_id)
                            return token

                    match_simple = re.search(r'webToken:\s*{"access_token":"([^"]+)","expired_at":(\d+)', html_text)
                    if match_simple:
                        token = match_simple.group(1)
                        exp = float(match_simple.group(2))
                        self._web_tokens[telegram_id] = token
                        self._web_token_expires_at[telegram_id] = exp
                        return token

                    logger.error("webToken pattern not found in vk.ru/im response for user %s", telegram_id)
            except Exception as e:
                logger.exception("Error extracting webToken for user %s: %s", telegram_id, e)

            return None

    async def call_vk_api(
        self,
        telegram_id: int,
        method: str,
        params: dict,
        user_mode: bool = True,
        explicit_token: Optional[str] = None
    ) -> Tuple[bool, Any]:
        """Calls VK API across available gateways with automatic fallback."""
        session = await self.get_session()

        if explicit_token:
            token = explicit_token
        elif user_mode:
            # 1. First priority: webToken from session (avoids flood control on VPS)
            token = await self.get_user_web_token(telegram_id)
            # 2. Second priority: OAuth user_token from database
            if not token:
                user_data = await get_user(telegram_id)
                token = (user_data.get("user_token") or "").strip()

            if not token:
                return False, "Не авторизован личный аккаунт VK (войдите через QR-код или ссылку)"
        else:
            token = config.vk_token.strip()
            if not token:
                return False, "Токен сообщества VK не настроен"

        req_params = dict(params)
        req_params["access_token"] = token
        req_params["v"] = VK_API_VERSION

        last_err = "Неизвестная ошибка API"
        for gateway in VK_API_GATEWAYS:
            try:
                url = f"{gateway}/{method}"
                async with session.post(url, data=req_params) as resp:
                    if resp.status != 200:
                        continue

                    data = await resp.json()
                    if "response" in data:
                        return True, data["response"]

                    if "error" in data:
                        err = data["error"]
                        code = err.get("error_code", 0)
                        msg = err.get("error_msg", "Unknown error")

                        # If user webToken expired (error 5), refresh and retry once
                        if code == 5 and user_mode and not explicit_token:
                            self._web_tokens.pop(telegram_id, None)
                            new_token = await self.get_user_web_token(telegram_id, force_refresh=True)
                            if new_token:
                                req_params["access_token"] = new_token
                                async with session.post(url, data=req_params) as retry_resp:
                                    retry_data = await retry_resp.json()
                                    if "response" in retry_data:
                                        return True, retry_data["response"]

                        if code == 901:
                            return False, "Пользователь не разрешил сообщения (для групп)"
                        if code == 902:
                            return False, "У пользователя закрыты личные сообщения"
                        if code == 9:
                            return False, "Flood control: лимит отправки сообщений VK. Увеличьте задержку."
                        if code == 14:
                            return False, "VK запросил ввод капчи"
                        if code == 7:
                            return False, "Нет прав на отправку сообщений данному получателю"
                        if code == 5:
                            return False, "Срок действия токена VK истек (требуется повторный вход)"

                        return False, f"VK API Ошибка {code}: {msg}"
            except Exception as e:
                last_err = str(e)
                continue

        return False, f"Сетевая ошибка обращения к VK: {last_err}"

    def extract_screen_name_or_id(self, target_url: str) -> str:
        """Extracts identifier from a VK URL or string."""
        target = target_url.strip().strip("'\"<>")
        if target.startswith("@"):
            target = target[1:].strip()
        target = re.sub(r'^https?://(?:www\.|m\.)?vk\.(?:com|ru|me)/', '', target, flags=re.IGNORECASE)
        target = re.sub(r'^(?:im\?sel=|write|mail\?act=show&peer=)', '', target, flags=re.IGNORECASE)
        target = target.split('?')[0].split('#')[0].strip('/')
        return target

    async def resolve_peer_id(self, telegram_id: int, target_str: str) -> Tuple[Optional[int], Optional[str]]:
        """Resolves identifier to numerical peer_id."""
        if match := re.match(r'^id(\d+)$', target_str, re.IGNORECASE):
            return int(match.group(1)), None

        if match := re.match(r'^(?:club|public)(\d+)$', target_str, re.IGNORECASE):
            return -int(match.group(1)), None

        if match := re.match(r'^c(\d+)$', target_str, re.IGNORECASE):
            return 2000000000 + int(match.group(1)), None

        if re.match(r'^-?\d+$', target_str):
            val = int(target_str)
            return val, None

        # Custom screen_name via utils.resolveScreenName
        user_data = await get_user(telegram_id)
        user_mode = bool(user_data.get("remixsid") or user_data.get("user_token"))

        success, resp = await self.call_vk_api(
            telegram_id,
            "utils.resolveScreenName",
            {"screen_name": target_str},
            user_mode=user_mode if user_mode else False
        )
        if not success or not resp:
            if user_mode and config.vk_token:
                success, resp = await self.call_vk_api(
                    telegram_id,
                    "utils.resolveScreenName",
                    {"screen_name": target_str},
                    user_mode=False
                )

        if success and resp:
            obj_type = resp.get("type")
            obj_id = resp.get("object_id")
            if obj_type == "user":
                return obj_id, None
            elif obj_type in ("group", "page"):
                return -obj_id, None
            else:
                return obj_id, None

        return None, f"Объект '{target_str}' не найден ВКонтакте"

    async def send_message(self, telegram_id: int, target_url: str, message_text: str) -> Tuple[bool, str]:
        """
        Sends message:
        1. User account (via webToken or user_token from DB) -> sends personal DMs from user profile.
        2. Else group API token (VK_TOKEN) -> sends from group.
        3. Else simulation mode.
        """
        target_clean = self.extract_screen_name_or_id(target_url)

        peer_id, err = await self.resolve_peer_id(telegram_id, target_clean)
        if peer_id is None:
            return False, err or f"Не удалось найти адресата {target_clean}"

        user_data = await get_user(telegram_id)
        user_mode = bool(user_data.get("remixsid") or user_data.get("user_token"))

        if user_mode:
            success, resp = await self.call_vk_api(
                telegram_id,
                "messages.send",
                {
                    "peer_id": peer_id,
                    "message": message_text,
                    "random_id": random.randint(1, 2147483647),
                },
                user_mode=True
            )
            if success:
                return True, f"Успешно (ID: {resp})"
            return False, str(resp)

        if config.vk_token:
            success, resp = await self.call_vk_api(
                telegram_id,
                "messages.send",
                {
                    "peer_id": peer_id,
                    "message": message_text,
                    "random_id": random.randint(1, 2147483647),
                },
                user_mode=False
            )
            if success:
                return True, f"Успешно (ID: {resp})"
            return False, str(resp)

        # Simulation mode
        await asyncio.sleep(0.3)
        return True, f"Симуляция: отправлено на {target_clean} (peer {peer_id})"

    async def get_status_info(self, telegram_id: int) -> Dict[str, Any]:
        """Checks connectivity and returns status of connected personal user and group."""
        user_data = await get_user(telegram_id)

        info = {
            "user_connected": False,
            "user_name": user_data.get("vk_name"),
            "user_id": user_data.get("vk_id"),
            "auth_type": None,
            "group_connected": False,
            "group_name": None,
            "group_id": None,
            "delay": user_data.get("delay", 2.0),
        }

        # 1. Check user token (OAuth)
        if user_data.get("user_token"):
            ok, res = await self.call_vk_api(
                telegram_id,
                "users.get",
                {"fields": "first_name,last_name"},
                user_mode=True,
                explicit_token=user_data["user_token"]
            )
            if ok and res and len(res) > 0:
                u = res[0]
                info["user_connected"] = True
                info["user_name"] = f"{u.get('first_name')} {u.get('last_name')}"
                info["user_id"] = u.get("id")
                info["auth_type"] = "OAuth ссылка"

        # 2. Check web session (QR / cookies)
        if not info["user_connected"] and user_data.get("remixsid"):
            ok, res = await self.call_vk_api(
                telegram_id,
                "users.get",
                {"fields": "first_name,last_name"},
                user_mode=True
            )
            if ok and res and len(res) > 0:
                u = res[0]
                info["user_connected"] = True
                info["user_name"] = f"{u.get('first_name')} {u.get('last_name')}"
                info["user_id"] = u.get("id")
                info["auth_type"] = "Доверенная сессия (QR / Веб)"

        # 3. Check group token
        if config.vk_token:
            ok, res = await self.call_vk_api(telegram_id, "groups.getById", {}, user_mode=False)
            if ok and res:
                groups = res.get("groups", res) if isinstance(res, dict) else res
                if isinstance(groups, list) and len(groups) > 0:
                    g = groups[0]
                    info["group_connected"] = True
                    info["group_name"] = g.get("name")
                    info["group_id"] = g.get("id")

        return info

vk_service = VKService()
