#!/usr/bin/env python3
"""Аналитика RBMW через API Авито.

  python3 analytics.py check          отчёт автозагрузки (ошибки) + баланс — для ежечасной проверки
  python3 analytics.py report [дней]  статистика объявлений и расходов за период (по умолчанию 7) -> out/analytics.json, out/analytics.xlsx

Ключи: переменные окружения AVITO_CLIENT_ID / AVITO_CLIENT_SECRET или файл ~/.avito_creds (KEY=VALUE).
В хранилище ключи не кладём: оно открытое.
"""
import os, sys, json, datetime, urllib.request, urllib.parse, collections, re

ROOT = os.path.dirname(os.path.abspath(__file__))
API = 'https://api.avito.ru'
MSK = datetime.timezone(datetime.timedelta(hours=3))
LOW_BALANCE = 500          # ₽: предупреждать, если на кошельке меньше


def creds():
    cid, sec = os.environ.get('AVITO_CLIENT_ID'), os.environ.get('AVITO_CLIENT_SECRET')
    f = os.path.expanduser('~/.avito_creds')
    if (not cid or not sec) and os.path.exists(f):
        kv = dict(l.strip().split('=', 1) for l in open(f) if '=' in l)
        cid, sec = kv.get('AVITO_CLIENT_ID'), kv.get('AVITO_CLIENT_SECRET')
    if not cid or not sec: raise SystemExit('Нет ключей API Авито (AVITO_CLIENT_ID / AVITO_CLIENT_SECRET)')
    return cid, sec


_tok = None
def req(path, data=None, method=None, form=False):
    global _tok
    if _tok is None and path != '/token':
        cid, sec = creds()
        _tok = req('/token', {'grant_type': 'client_credentials', 'client_id': cid, 'client_secret': sec}, form=True)['access_token']
    h = {'User-Agent': 'rbmw-analytics'}
    body = None
    if data is not None:
        if form: body = urllib.parse.urlencode(data).encode(); h['Content-Type'] = 'application/x-www-form-urlencoded'
        else: body = json.dumps(data).encode(); h['Content-Type'] = 'application/json'
    if _tok and path != '/token': h['Authorization'] = 'Bearer ' + _tok
    r = urllib.request.Request(API + path, data=body, headers=h, method=method or ('POST' if body else 'GET'))
    with urllib.request.urlopen(r, timeout=60) as f: return json.loads(f.read())


def uid(): return req('/core/v1/accounts/self')['id']


def balance(u):
    b = req(f'/core/v1/accounts/{u}/balance/')
    return b.get('real', 0), b.get('bonus', 0)


def cmd_check(args):
    u = uid(); out = []
    # последняя загрузка (в т.ч. неудачная): если Авито не смог обработать файл — это главное
    last = (req('/autoload/v4/uploads?per_page=1').get('uploads') or [{}])[0]
    if last.get('status') == 'error':
        ev = '; '.join(dict.fromkeys(re.sub('<[^>]+>', '', e.get('description', '')).split('.')[0] for e in last.get('events', []) if e.get('type') == 'error'))
        print(f"ВНИМАНИЕ Авито не обработал файл (загрузка {last.get('started_at')}): {ev}")
    rep = req('/autoload/v4/uploads/last_successful')
    st = rep.get('stats', {}); started = rep.get('started_at', '')
    secs = {s['slug']: s.get('count', 0) for s in st.get('sections', [])}
    # ошибки и отклонения — по последнему ЗАВЕРШЁННОМУ отчёту (last_successful бывает устаревшим)
    try:
        rid = req('/autoload/v2/reports/last_completed_report').get('report_id')
        errs = req(f'/autoload/v2/reports/{rid}/items?per_page=100&sections=error').get('items', [])
    except Exception:
        errs = req('/autoload/v4/uploads/last_successful/items?per_page=100&sections=error').get('items', []) if secs.get('error') else []
    print(f"Автозагрузка {started}: обработано {st.get('count', 0)}, ошибок {secs.get('error', 0)}, статус {rep.get('status')}")
    for it in errs:
        msgs = '; '.join(dict.fromkeys((m.get('title', '') + ' ' + re.sub('<[^>]+>', ' ', m.get('description', ''))[:160]).strip() for m in it.get('messages', [])))
        print(f"ВНИМАНИЕ ошибка Авито: №{it.get('ad_id')}: {msgs}")
    # сверка цен: опубликованная на Авито = цена в файле (по прайсу Bazon)
    try:
        st = json.load(open(os.path.join(ROOT, 'state.json'), encoding='utf-8'))['items']
        act = [x for x in items_all() if x.get('status') == 'active']
        amap = ad_ids([x['id'] for x in act]); diff = []
        for x in act:
            a = amap.get(x['id']); it = st.get(str(a)) if a else None
            if it and it.get('status') == 'active' and it.get('price') and x.get('price') != it['price']:
                diff.append(f"№{a} {x.get('title', '')[:40]}: на Авито {x.get('price')} ₽, по базе {it['price']} ₽")
        if diff: print(f'Цены на Авито ещё не обновились ({len(diff)}): ' + '; '.join(diff))
    except Exception as e: print('сверка цен не выполнена:', e)
    real, bonus = balance(u)
    print(f'Баланс: {real:.0f} ₽ + бонусы {bonus:.0f}')
    if real + bonus < LOW_BALANCE: print(f'ВНИМАНИЕ мало денег на кошельке Авито: {real + bonus:.0f} ₽ — объявления могут перестать публиковаться')
    json.dump({'checked': datetime.datetime.now(MSK).isoformat(timespec='minutes'), 'upload': rep, 'errors': errs,
               'balance': {'real': real, 'bonus': bonus}},
              open(os.path.join(ROOT, 'out', 'check.json'), 'w'), ensure_ascii=False, indent=1)


def cmd_upload(args):
    # попросить Авито забрать файл по ссылке сразу (после выкладки изменений: цены, продажи, новые позиции)
    try: req('/autoload/v1/upload', {}, method='POST')
    except json.JSONDecodeError: pass          # Авито отвечает пустым телом — это успех
    except Exception as e: print('ВНИМАНИЕ не удалось запустить загрузку Авито сразу:', e); return
    print('Авито: загрузка файла запущена сразу после выкладки')


def stats_v2(u, d1, d2):
    rows, off = [], 0
    while True:
        r = req(f'/stats/v2/accounts/{u}/items', {'dateFrom': d1, 'dateTo': d2, 'grouping': 'item', 'limit': 200, 'offset': off,
                 'metrics': ['impressions', 'views', 'contacts', 'favorites', 'allSpending', 'spending']})['result']
        g = r.get('groupings', []); rows += g; off += len(g)
        if not g or off >= r.get('dataTotalCount', 0): break
    return {x['id']: {m['slug']: m['value'] for m in x['metrics']} for x in rows}


def items_all():
    out, page = [], 1
    while True:
        r = req(f'/core/v1/items?per_page=100&page={page}&status=active,old,removed,blocked,rejected').get('resources', [])
        out += r
        if len(r) < 100: break
        page += 1
    return out


def ad_ids(avito_ids):
    m = {}
    for i in range(0, len(avito_ids), 50):
        q = ','.join(str(x) for x in avito_ids[i:i + 50])
        for x in req(f'/autoload/v2/items/ad_ids?query={q}').get('items', []): m[x['avito_id']] = x['ad_id']
    return m


def group(a):
    """Правила из отчёта коллеги (02.11), цены не меняем — только рекомендации."""
    sp, ct, vw, fv, pr = a['spend'], a['contacts'], a['views'], a['fav'], a['price']
    if ct == 0 and sp >= 500:
        if fv >= 3: return 'burn', f'Добавляют в избранное, но не пишут: снизить на 10% (до {int(pr * 0.9 // 100 * 100)} ₽)'
        return 'burn', f'Расход {sp:.0f} ₽ без контактов: проверить цену/заголовок/фото, снизить на 10% (до {int(pr * 0.9 // 100 * 100)} ₽)'
    if ct >= 1 and sp / ct >= 2000: return 'costly', f'Контакт стоит {sp / ct:.0f} ₽: сверить цену с рынком'
    if pr >= 30000 and ct >= 1 and vw and vw < 120 and ct / vw >= 0.015: return 'promo', 'Хорошо конвертирует при малом охвате — кандидат в продвижение'
    if pr >= 30000 and fv >= 5 and vw < 150 and ct <= 1: return 'promo', 'Часто в избранном при малом охвате — кандидат в продвижение'
    if ct >= 2 and sp / max(ct, 1) < 1000: return 'ok', 'Работает — не трогать'
    return 'other', ''


GROUPS = {'burn': 'Сжигают бюджет', 'costly': 'Дорогой контакт', 'promo': 'Кандидаты в продвижение', 'ok': 'Работают', 'other': 'Мало данных'}


def cmd_report(args):
    days = int(args[0]) if args else 7
    u = uid()
    today = datetime.datetime.now(MSK).date()
    d1, d2 = (today - datetime.timedelta(days=days - 1)).isoformat(), today.isoformat()
    S = stats_v2(u, d1, d2)
    items = items_all()
    amap = ad_ids([x['id'] for x in items])
    state = json.load(open(os.path.join(ROOT, 'state.json'), encoding='utf-8'))['items']
    rows = []
    for x in items:
        s = S.get(x['id'], {})
        a = dict(avito_id=x['id'], ad_id=amap.get(x['id']), title=x.get('title'), status=x.get('status'), price=x.get('price') or 0,
                 url=x.get('url'), impressions=s.get('impressions', 0), views=s.get('views', 0), contacts=s.get('contacts', 0),
                 fav=s.get('favorites', 0), spend=round(s.get('allSpending', 0) / 100, 2))   # расходы в API — в копейках
        it = state.get(str(a['ad_id'])) if a['ad_id'] else None
        a['in_feed'] = bool(it); a['added'] = it.get('added') if it else None
        a['days'] = (today - datetime.date.fromisoformat(it['added'])).days if it else None
        a['cpc'] = round(a['spend'] / a['contacts']) if a['contacts'] else None
        a['grp'], a['rec'] = group(a)
        rows.append(a)
    act = [a for a in rows if a['status'] == 'active']
    real, bonus = balance(u)
    tot = lambda k, L=act: sum(a[k] for a in L)
    summary = dict(period=f'{d1} — {d2}', days=days, active=len(act), all=len(rows), impressions=tot('impressions'), views=tot('views'),
                   contacts=tot('contacts'), fav=tot('fav'), spend=round(tot('spend'), 2),
                   spend_all=round(sum(a['spend'] for a in rows), 2), cpc=round(tot('spend') / max(tot('contacts'), 1)),
                   ctr=round(100 * tot('views') / max(tot('impressions'), 1), 1), cr=round(100 * tot('contacts') / max(tot('views'), 1), 2),
                   wasted=round(sum(a['spend'] for a in act if a['contacts'] == 0), 2), balance=real, bonus=bonus,
                   groups={g: sum(1 for a in act if a['grp'] == g) for g in GROUPS})
    os.makedirs(os.path.join(ROOT, 'out'), exist_ok=True)
    json.dump({'summary': summary, 'items': rows}, open(os.path.join(ROOT, 'out', 'analytics.json'), 'w'), ensure_ascii=False, indent=1)
    write_xlsx(summary, rows)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def write_xlsx(sm, rows):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook(); s = wb.active; s.title = 'Сводка'
    s.append([f"RBMW: статистика Авито за {sm['period']}"]); s['A1'].font = Font(bold=True, size=14); s.append([])
    for k, v in [('Активных объявлений', sm['active']), ('Показы', sm['impressions']), ('Просмотры', sm['views']), ('Контакты', sm['contacts']),
                 ('Избранное', sm['fav']), ('Расход, ₽', sm['spend']), ('Цена контакта, ₽', sm['cpc']), ('Просмотры/показы, %', sm['ctr']),
                 ('Контакты/просмотры, %', sm['cr']), ('Расход на объявления без контактов, ₽', sm['wasted']),
                 ('Баланс кошелька, ₽', sm['balance']), ('Бонусы', sm['bonus'])]:
        s.append([k, v])
    s.column_dimensions['A'].width = 42; s.column_dimensions['B'].width = 14
    H = ['Артикул', 'Объявление', 'Статус', 'Цена', 'Дней', 'Показы', 'Просмотры', 'Контакты', 'Избранное', 'Расход, ₽', 'Цена контакта', 'Рекомендация', 'Ссылка']
    for g, name in GROUPS.items():
        L = sorted([a for a in rows if a['grp'] == g and a['status'] == 'active'], key=lambda a: -a['spend'])
        w = wb.create_sheet(name[:30]); w.append(H)
        for c in w[1]: c.font = Font(bold=True, color='FFFFFF'); c.fill = PatternFill('solid', fgColor='305496')
        for a in L: w.append([a['ad_id'], a['title'], a['status'], a['price'], a['days'], a['impressions'], a['views'], a['contacts'], a['fav'], a['spend'], a['cpc'], a['rec'], a['url']])
        for col, wd in zip('ABCDEFGHIJKLM', [10, 48, 8, 10, 6, 9, 10, 9, 9, 10, 12, 60, 40]): w.column_dimensions[col].width = wd
        w.freeze_panes = 'A2'
    wb.save(os.path.join(ROOT, 'out', 'analytics.xlsx'))


if __name__ == '__main__':
    {'check': cmd_check, 'report': cmd_report, 'upload': cmd_upload}[sys.argv[1]](sys.argv[2:])
