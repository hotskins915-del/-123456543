import os
from pathlib import Path
from dotenv import load_dotenv, set_key

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)

class Config:
    def __init__(self):
        self.bot_token: str = os.getenv("BOT_TOKEN", "").strip()
        self.vk_token: str = os.getenv("VK_TOKEN", "").strip()
        self.user_token: str = os.getenv("USER_VK_TOKEN", "").strip()
        self.remixsid: str = os.getenv("REMIXSID", "").strip()
        self.remixnsid: str = os.getenv("REMIXNSID", "").strip()
        self.delay_between_messages: float = float(os.getenv("DELAY_BETWEEN_MESSAGES", "2.0"))
        
        raw_admins = os.getenv("ADMIN_IDS", "").strip()
        self.admin_ids: set[int] = {
            int(x.strip()) for x in raw_admins.split(",") if x.strip().isdigit()
        }

    @property
    def has_user_auth(self) -> bool:
        return bool(self.user_token or self.remixsid)

    @property
    def has_group_auth(self) -> bool:
        return bool(self.vk_token)

    def update_vk_token(self, new_token: str):
        self.vk_token = new_token.strip()
        os.environ["VK_TOKEN"] = self.vk_token
        set_key(str(ENV_FILE), "VK_TOKEN", self.vk_token)

    def update_user_token(self, new_token: str):
        self.user_token = new_token.strip()
        os.environ["USER_VK_TOKEN"] = self.user_token
        set_key(str(ENV_FILE), "USER_VK_TOKEN", self.user_token)

    def update_remixsid(self, new_sid: str):
        self.remixsid = new_sid.strip()
        os.environ["REMIXSID"] = self.remixsid
        set_key(str(ENV_FILE), "REMIXSID", self.remixsid)

    def update_remixnsid(self, new_nsid: str):
        self.remixnsid = new_nsid.strip()
        os.environ["REMIXNSID"] = self.remixnsid
        set_key(str(ENV_FILE), "REMIXNSID", self.remixnsid)

    def clear_user_account(self):
        self.user_token = ""
        self.remixsid = ""
        self.remixnsid = ""
        os.environ["USER_VK_TOKEN"] = ""
        os.environ["REMIXSID"] = ""
        os.environ["REMIXNSID"] = ""
        set_key(str(ENV_FILE), "USER_VK_TOKEN", "")
        set_key(str(ENV_FILE), "REMIXSID", "")
        set_key(str(ENV_FILE), "REMIXNSID", "")

    def clear_group_account(self):
        self.vk_token = ""
        os.environ["VK_TOKEN"] = ""
        set_key(str(ENV_FILE), "VK_TOKEN", "")

    def clear_all_accounts(self):
        self.clear_user_account()
        self.clear_group_account()

    def update_delay(self, new_delay: float):
        self.delay_between_messages = max(0.5, float(new_delay))
        os.environ["DELAY_BETWEEN_MESSAGES"] = str(self.delay_between_messages)
        set_key(str(ENV_FILE), "DELAY_BETWEEN_MESSAGES", str(self.delay_between_messages))

    def is_admin(self, user_id: int) -> bool:
        if not self.admin_ids:
            return True
        return user_id in self.admin_ids

config = Config()
