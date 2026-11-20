#!/usr/bin/env python3
"""Самопроверка и самовосстановление RBMW (запускается в каждом цикле daily.sh ДО сборки файла).

1. Последний завершённый отчёт Авито чистый (нет ошибки файла, нет строк без ID) → текущий шаблон и структура
   сохраняются как «последние рабочие» (data/last_good/).
2. Отчёт плохой (Авито не обработал файл или нашёл строки без ID), а шаблон менялся после последнего рабочего →
   АВТООТКАТ шаблона и принятой структуры на последние рабочие + уведомление.
3. Объявление с ошибкой Авито в двух завершённых отчётах подряд → снимается из файла и в этом же цикле заменяется
   следующей позицией из пула (rbmw.py topup) + уведомление.
4. Сверка с Авито: позиции из файла, которых нет среди активных на Авито дольше 3 часов → уведомление.
5. Утренняя самопроверка (запуск 9:xx): сводка — объявлений в файле / активных на Авито, пул, баланс и на сколько
   дней его хватит, ошибки, неотвеченные чаты.
Состояние — data/health.json (в хранилище, без ключей).
"""
import os, sys, json, shutil, hashlib, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analytics as A, rbmw

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)
HEALTH = P('data', 'health.json')
GOOD = P('data', 'last_good')
NOW = datetime.datetime.now(A.MSK)
SAFE_FILES = ['data/template.xlsx', 'data/accepted_layout.json']


def md5(p): return hashlib.md5(open(p, 'rb').read()).hexdigest() if os.path.exists(p) else None


def load():
    if os.path.exists(HEALTH): return json.load(open(HEALTH, encoding='utf-8'))
    return {'last_good': None, 'errors': {}, 'missing': {}, 'reports_seen': []}


def main():
    h = load(); msgs = []
    rid = A.req('/autoload/v2/reports/last_completed_report').get('report_id')
    rep = A.req(f'/autoload/v2/reports/{rid}')
    secs = {}
    for s in (rep.get('section_stats') or {}).get('sections', []): secs[s['slug']] = s.get('count', 0)
    bad = rep.get('status') == 'error' or secs.get('without_id', 0) > 0
    new_report = rid not in h['reports_seen']
    if new_report: h['reports_seen'] = (h['reports_seen'] + [rid])[-20:]

    # 1-2. рабочая версия / автооткат
    cur = {f: md5(P(f)) for f in SAFE_FILES}
    if not bad and new_report:
        os.makedirs(GOOD, exist_ok=True)
        for f in SAFE_FILES: shutil.copy(P(f), os.path.join(GOOD, os.path.basename(f)))
        h['last_good'] = {'report': rid, 'at': NOW.isoformat(timespec='minutes'), 'md5': cur}
    elif bad and h.get('last_good'):
        changed = [f for f in SAFE_FILES if cur[f] != h['last_good']['md5'].get(f)]
        if changed:
            for f in SAFE_FILES: shutil.copy(os.path.join(GOOD, os.path.basename(f)), P(f))
            why = 'файл не обработан' if rep.get('status') == 'error' else f"строк без ID: {secs.get('without_id')}"
            msgs.append(f"ВНИМАНИЕ АВТООТКАТ: Авито ({why}) после изменения шаблона — восстановлена рабочая версия от {h['last_good']['at']}")
        else:
            msgs.append(f"ВНИМАНИЕ отчёт Авито плохой ({rep.get('status')}, без ID {secs.get('without_id', 0)}), шаблон не менялся — нужна проверка")

    # 3. ошибки объявлений два отчёта подряд → снять и заменить
    if new_report:
        items = A.req(f'/autoload/v2/reports/{rid}/items?per_page=100&sections=error').get('items') or []
        now_err = {str(x.get('ad_id')): '; '.join(dict.fromkeys(m.get('title', '') for m in x.get('messages', []) if m.get('type') == 'error')) for x in items if x.get('ad_id')}
        st = rbmw.load_state(); removed = []
        for a, why in now_err.items():
            e = h['errors'].get(a)
            if e and e.get('last_report') != rid:
                e['count'] = e.get('count', 1) + 1; e['last_report'] = rid
            elif not e:
                h['errors'][a] = {'count': 1, 'first': NOW.isoformat(timespec='minutes'), 'last_report': rid, 'why': why}
            if h['errors'][a]['count'] >= 2 and a in st['items'] and st['items'][a]['status'] == 'active':
                st['items'][a].update(status='removed', replaced=False, reason='Авито: ' + why[:200]); removed.append(f'№{a} ({why[:80]})')
        for a in list(h['errors']):
            if a not in now_err: h['errors'].pop(a)
        if removed:
            rbmw.save_state(st)
            msgs.append('ВНИМАНИЕ сняты из-за повторной ошибки Авито и будут заменены в этом цикле: ' + '; '.join(removed))

    # 4. сверка: позиции файла, не активные на Авито > 3 ч
    st = rbmw.load_state()
    feeds = [f['feed_url'] for f in A.req('/autoload/v2/profile').get('feeds_data', [])]
    extra_on = any(u.endswith('rbmw_farkop.xlsx') for u in feeds)
    ours = {a for a, i in st['items'].items() if i['status'] == 'active' and i.get('photos') and (not i.get('feed') or extra_on)}
    if not extra_on and any(i.get('feed') and i['status'] == 'active' for i in st['items'].values()) and NOW.hour == 9:
        msgs.append('Напоминание: фаркоп ждёт подключения второго файла (rbmw_farkop.xlsx) в настройках автозагрузки Авито')
    act = [x for x in A.items_all() if x.get('status') == 'active']
    amap = A.ad_ids([x['id'] for x in act]); on_avito = set(amap.values())
    miss = ours - on_avito
    for a in list(h['missing']):
        if a not in miss: h['missing'].pop(a)
    for a in miss: h['missing'].setdefault(a, NOW.isoformat(timespec='minutes'))
    old = [a for a, t in h['missing'].items() if (NOW - datetime.datetime.fromisoformat(t)).total_seconds() > 3 * 3600]
    if old: msgs.append(f'ВНИМАНИЕ в файле, но не активны на Авито дольше 3 ч ({len(old)}): ' + ', '.join(sorted(old)[:15]))

    # 5. утренняя самопроверка
    if NOW.hour == 9:
        real, bonus = A.balance(A.uid())
        S = A.stats_v2(A.uid(), (NOW.date() - datetime.timedelta(days=2)).isoformat(), NOW.date().isoformat())
        spend3 = sum(v.get('allSpending', 0) for v in S.values()) / 100
        per_day = spend3 / 3 if spend3 else 0
        days = (real + bonus) / per_day if per_day else None
        pool = len(rbmw.eligible(rbmw.load_csv(rbmw.CSV_NEW), st))
        msgs.append(f"Утренняя самопроверка: в файле {len(ours)}, активных на Авито {len(on_avito & ours)}; "
                    f"последний отчёт Авито — {rep.get('status')}; пул от {rbmw.CFG['min_price']} ₽ — {pool} поз.; "
                    f"баланс {real + bonus:.0f} ₽, расход ~{per_day:.0f} ₽/день" + (f", хватит на ~{days:.1f} дн." if days else ''))
        if days is not None and days < 5: msgs.append(f'ВНИМАНИЕ баланса Авито хватит примерно на {days:.1f} дн. — пополните кошелёк')

    json.dump(h, open(HEALTH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for m in msgs: print(m)
    if not msgs: print('самопроверка: всё в порядке')


if __name__ == '__main__':
    main()
