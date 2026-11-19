#!/bin/bash
# Цикл автозагрузки RBMW: прайс Bazon -> проданное/цены -> +N в день -> фото -> файл -> GitHub.
set -e
cd "$(dirname "$0")"
export TZ=Europe/Moscow
python3 -I rbmw.py fetch
python3 -I rbmw.py sync
python3 -I rbmw.py pick --daily
python3 -I rbmw.py photos
python3 -I rbmw.py build
python3 -I rbmw.py cleanup
cp out/rbmw.xlsx rbmw.xlsx
git add -A
if git diff --cached --quiet; then echo "Без изменений"; else
  git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -qm "RBMW: $(date '+%d.%m %H:%M')" \
     -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" \
     -m "Claude-Session: https://claude.ai/code/session_01QkzCjE8icoTS7ghpfM8Aoa"
  git -c push.negotiate=true push -q origin HEAD:main
  echo "GitHub обновлён"
fi
python3 -I rbmw.py status
# контроль Авито через API: ошибки последней автозагрузки и баланс (нужны ключи AVITO_CLIENT_ID/SECRET)
mkdir -p out; python3 -I analytics.py check || echo "API Авито недоступен"
