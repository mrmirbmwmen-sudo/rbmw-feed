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
# проверка перед выкладкой: структура как в принятом Авито шаблоне, поля, справочники, цены = Bazon, фото
if ! python3 -I validate.py out/rbmw.xlsx; then
  echo "ВНИМАНИЕ проверка файла не пройдена — выкладка остановлена, на Авито остаётся прежний файл"
  git checkout -q -- state.json 2>/dev/null || true
  exit 1
fi
cp out/rbmw.xlsx rbmw.xlsx
git add -A
if git diff --cached --quiet; then echo "Без изменений"; else
  git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -qm "RBMW: $(date '+%d.%m %H:%M')" \
     -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" \
     -m "Claude-Session: https://claude.ai/code/session_01QkzCjE8icoTS7ghpfM8Aoa"
  git -c push.negotiate=true push -q origin HEAD:main
  echo "GitHub обновлён"
  sleep 20; python3 -I analytics.py upload || true    # Авито забирает файл сразу, а не в начале следующего часа
fi
python3 -I rbmw.py status
# контроль Авито через API: ошибки последней автозагрузки и баланс (нужны ключи AVITO_CLIENT_ID/SECRET)
mkdir -p out; python3 -I analytics.py check || echo "API Авито недоступен"
# раз в день (утренний запуск): сверка официальных шаблонов Авито с нашими
[ "$(date +%H)" = "09" ] && { python3 -I analytics.py specs || echo "сверка шаблонов не выполнена"; }
