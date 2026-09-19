from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Начать рассылку", callback_data="nav:new_broadcast")
    builder.button(text="⚙️ Настройки VK / Аккаунты", callback_data="nav:settings")
    builder.button(text="ℹ️ Инструкция", callback_data="nav:help")
    builder.button(text="📊 Статус системы", callback_data="nav:status")
    builder.adjust(1, 2, 1)
    return builder.as_markup()

def get_cancel_keyboard(to_menu: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    cb = "nav:main_menu" if to_menu else "nav:cancel"
    builder.button(text="❌ Отмена", callback_data=cb)
    return builder.as_markup()

def get_confirm_broadcast_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Запустить рассылку", callback_data="bc:start")
    builder.button(text="✏️ Изменить текст", callback_data="bc:edit_text")
    builder.button(text="📁 Другие ссылки", callback_data="bc:edit_links")
    builder.button(text="❌ Отмена", callback_data="bc:cancel")
    builder.adjust(1, 2, 1)
    return builder.as_markup()

def get_broadcast_running_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏹ Остановить рассылку", callback_data="bc:stop")
    return builder.as_markup()

def get_settings_keyboard(has_user: bool, has_group: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    qr_text = "🔄 Сменить аккаунт через QR-код" if has_user else "📱 Войти через QR-код (Рекомендуется)"
    builder.button(text=qr_text, callback_data="set:qr_auth")
    builder.button(text="🔗 Войти по OAuth ссылке", callback_data="set:oauth_link")
    builder.button(text="🍪 Ввести куки вручную", callback_data="set:manual_cookies")
    builder.button(text="⏱ Задержка между отправками", callback_data="set:delay")
    
    if has_user or has_group:
        builder.button(text="🚪 Меню выхода из аккаунтов", callback_data="set:logout_menu")
        
    builder.button(text="« Главное меню", callback_data="nav:main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_logout_keyboard(has_user: bool, has_group: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_user:
        builder.button(text="👤 Выйти из личного аккаунта", callback_data="logout:ask_user")
    if has_group:
        builder.button(text="👥 Отключить сообщество", callback_data="logout:ask_group")
    if has_user and has_group:
        builder.button(text="💣 Сбросить всё (полный выход)", callback_data="logout:ask_all")
    builder.button(text="« Назад в настройки", callback_data="nav:settings")
    builder.adjust(1)
    return builder.as_markup()

def get_confirm_logout_keyboard(target: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, подтвердить выход", callback_data=f"logout:do_{target}")
    builder.button(text="❌ Отмена", callback_data="set:logout_menu")
    builder.adjust(1, 1)
    return builder.as_markup()

def get_manual_cookies_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Ввести remixsid", callback_data="set:remixsid")
    builder.button(text="🛡 Ввести remixnsid", callback_data="set:remixnsid")
    builder.button(text="« Назад в настройки", callback_data="nav:settings")
    builder.adjust(1)
    return builder.as_markup()

def get_back_to_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="« Главное меню", callback_data="nav:main_menu")
    return builder.as_markup()
