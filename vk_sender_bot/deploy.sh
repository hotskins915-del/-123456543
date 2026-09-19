#!/usr/bin/env bash
set -e

# ==============================================================
# Универсальный скрипт установки и обновления VK Sender Bot
# Подходит для чистых VPS (Ubuntu 20.04 / 22.04 / 24.04, Debian)
# ==============================================================

SUDO=""
if [ "$EUID" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    fi
fi

echo "🚀 [1/6] Обновление пакетов сервера..."
$SUDO apt update -y || true
$SUDO apt install -y python3 python3-pip python3-venv libzbar0 curl git wget || true

echo "🌐 [2/6] Проверка и установка Google Chrome..."
if [ ! -f "/opt/google/chrome/chrome" ]; then
    echo "Установка Google Chrome..."
    wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb || true
    if [ -f "/tmp/chrome.deb" ]; then
        $SUDO apt install -y /tmp/chrome.deb || $SUDO apt --fix-broken install -y || true
        rm -f /tmp/chrome.deb
    fi
else
    echo "Google Chrome уже установлен."
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "🐍 [3/6] Настройка окружения Python..."
PYTHON_EXEC="/usr/bin/python3"

# Попытка создать venv, если доступен модуль venv
if python3 -m venv venv 2>/dev/null; then
    echo "Виртуальное окружение venv успешно создано."
    source venv/bin/activate
    PYTHON_EXEC="$PROJECT_DIR/venv/bin/python3"
    pip install --upgrade pip
    pip install -r requirements.txt
    playwright install-deps chromium || true
else
    echo "venv недоступен без прав root, устанавливаем зависимости в систему..."
    pip install -r requirements.txt --break-system-packages || pip install --user -r requirements.txt
fi

echo "⚙️ [4/6] Проверка файла конфигурации .env..."
if [ ! -f ".env" ]; then
    echo "Создание шаблона .env файла..."
    cat << 'EOF' > .env
BOT_TOKEN=
VK_TOKEN=
DELAY_BETWEEN_MESSAGES=2.0
EOF
    echo "⚠️ ВНИМАНИЕ: Заполните BOT_TOKEN в файле .env перед запуском бота!"
fi

echo "🔄 [5/6] Настройка и запуск службы systemd..."
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"

cat << EOF > "$SYSTEMD_USER_DIR/vk_sender_bot.service"
[Unit]
Description=VK Sender Telegram Bot (aiogram 3)
After=network.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_EXEC -u $PROJECT_DIR/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable vk_sender_bot.service
systemctl --user restart vk_sender_bot.service

echo ""
echo "=========================================================="
echo "✅ Бот успешно настроен и запущен через systemd!"
echo "• Проверить статус:  systemctl --user status vk_sender_bot"
echo "• Посмотреть логи:   journalctl --user -u vk_sender_bot -f"
echo "• Перезапустить:     systemctl --user restart vk_sender_bot"
echo "=========================================================="
