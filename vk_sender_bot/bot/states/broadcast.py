from aiogram.fsm.state import State, StatesGroup

class BroadcastStates(StatesGroup):
    waiting_for_links = State()
    waiting_for_message = State()
    confirm_broadcast = State()
    broadcasting = State()

class SettingsStates(StatesGroup):
    waiting_for_vk_token = State()
    waiting_for_user_token = State()
    waiting_for_remixsid = State()
    waiting_for_remixnsid = State()
    waiting_for_delay = State()
    waiting_for_qr_code = State()
