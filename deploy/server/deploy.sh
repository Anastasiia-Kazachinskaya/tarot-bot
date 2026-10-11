#!/usr/bin/env bash
# Деплой таро-бота на сервере.
#
# Копия этого файла лежит в /home/tarot/deploy.sh (владелец root),
# и SSH-ключ из GitHub Actions может запускать только её: в
# authorized_keys у ключа прописано command="/home/tarot/deploy.sh".
set -euo pipefail

cd /home/tarot/tarot-bot

before=$(git rev-parse HEAD)
git fetch --quiet origin main
git merge --ff-only --quiet origin/main
after=$(git rev-parse HEAD)

if [ "$before" = "$after" ]; then
    echo "Код не изменился: $after"
fi

if git diff --name-only "$before" "$after" | grep -qx "requirements.txt"; then
    echo "requirements.txt изменился — обновляю зависимости"
    .venv/bin/pip install --quiet -r requirements.txt
fi

sudo /usr/bin/systemctl restart tarot-bot
sleep 5

if systemctl is-active --quiet tarot-bot; then
    echo "Задеплоено: $after"
else
    echo "После перезапуска бот не работает — смотри: sudo journalctl -u tarot-bot -n 50"
    exit 1
fi
