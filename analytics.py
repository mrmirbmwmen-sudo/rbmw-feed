#!/usr/bin/env python3
"""Аналитика RBMW через API Авито.

  python3 analytics.py check          отчёт автозагрузки (ошибки) + баланс — для ежечасной проверки
  python3 analytics.py report [дней]  статистика объявлений и расходов за период (по умолчанию 7) -> out/analytics.json, out/analytics.xlsx

Ключи: переменные окружения AVITO_CLIENT_ID / AVITO_CLIENT_SECRET или файл ~/.avito_creds (KEY=VALUE).
В хранилище ключи не кладём: оно открытое.
"""
import os, sys, json, datetime, urllib.request, urllib.parse, urllib.error, collections, re

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
    try: b = req(f'/core/v1/accounts/{u}/balance/')
    except Exception: return 0, 0
    return b.get('real', 0), b.get('bonus', 0)


def advance():
    # 24.11: реклама и размещение списываются с АВАНСА (CPA), а не с кошелька — смотреть сюда
    return req('/cpa/v3/balanceInfo', {}, method='POST').get('balance', 0) / 100


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
        errs = req(f'/autoload/v2/reports/{rid}/items?per_page=100&sections=error').get('items') or []
    except Exception:
        errs = (req('/autoload/v4/uploads/last_successful/items?per_page=100&sections=error').get('items') or []) if secs.get('error') else []
    if secs.get('without_id'): print(f"ВНИМАНИЕ Авито нашёл в файле строки без ID: {secs['without_id']} — проверьте структуру листов")
    if secs.get('removed_from_file'): print(f"Снято с публикации (нет в файле): {secs['removed_from_file']}")
    print(f"Автозагрузка {started}: обработано {st.get('count', 0)}, ошибок {secs.get('error', 0)}, статус {rep.get('status')}")
    for it in errs:
        msgs = '; '.join(dict.fromkeys((m.get('title', '') + ' ' + re.sub('<[^>]+>', ' ', m.get('description', ''))[:160]).strip() for m in it.get('messages', [])))
        print(f"ВНИМАНИЕ ошибка Авито: №{it.get('ad_id')}: {msgs}")
    # сверка цен: опубликованная на Авито = цена в файле (по прайсу Bazon)
    try:
        st = json.load(open(os.path.join(ROOT, 'state.json'), encoding='utf-8'))['items']
        act = [x for x in items_all() if x.get('status') == 'active']
        amap = ad_ids([x['id'] for x in act]); diff = []; case = []; tdiff = []
        for x in act:
            a = amap.get(x['id']); it = st.get(str(a)) if a else None
            if it and it.get('status') == 'active' and it.get('price') and x.get('price') != it['price']:
                diff.append(f"№{a} {x.get('title', '')[:40]}: на Авито {x.get('price')} ₽, по базе {it['price']} ₽")
            # 24.11: заголовок на Авито = заголовок в файле (Авито сам переводит в строчные непонятные ему сокращения)
            at = x.get('title') or ''
            if it and it.get('status') == 'active' and it.get('title') and not at.startswith(it['title']):
                (case if at.lower().startswith(it['title'].lower()) else tdiff).append(f"№{a}: в файле «{it['title']}», на Авито «{at}»")
        if diff: print(f'Цены на Авито ещё не обновились ({len(diff)}): ' + '; '.join(diff))
        if case: print(f'ВНИМАНИЕ Авито изменил регистр в заголовках ({len(case)}) — заменить сокращения словами: ' + '; '.join(case))
        if tdiff: print(f'Заголовки на Авито ещё не обновились ({len(tdiff)}): ' + '; '.join(tdiff[:10]))
    except Exception as e: print('сверка цен не выполнена:', e)
    # чаты: покупатель написал и ждёт ответа дольше 30 минут (скорость ответа влияет на показы)
    try:
        now = datetime.datetime.now(datetime.timezone.utc).timestamp(); wait = []
        for c in req(f'/messenger/v2/accounts/{u}/chats?limit=50').get('chats', []):
            lm = c.get('last_message') or {}
            if ((c.get('context') or {}).get('value') or {}).get('user_id') not in (u, None): continue   # 24.11: чаты, где мы покупатели, не считаем
            if not lm or lm.get('author_id') in (u, 0) or lm.get('direction') == 'out': continue
            age = (now - lm.get('created', now)) / 60
            if 30 <= age <= 24 * 60:
                it = ((c.get('context') or {}).get('value') or {}).get('title', '')
                wait.append(f"{it[:45]} — ждёт {int(age // 60)} ч {int(age % 60)} мин")
        if wait: print(f'ВНИМАНИЕ неотвеченные чаты ({len(wait)}): ' + '; '.join(wait[:10]))
    except Exception as e: print('чаты не проверены:', e)
    # 24.11: размещение и продвижение списываются из АВАНСА; он виден через API /cpa/v3/balanceInfo
    real, bonus = balance(u); adv = advance()
    print(f'Аванс Авито: {adv:.0f} ₽ (кошелёк {real:.0f} ₽ + бонусы {bonus:.0f})')
    if adv < LOW_BALANCE: print(f'ВНИМАНИЕ мало денег на авансе Авито: {adv:.0f} ₽ — объявления могут перестать показываться')
    json.dump({'checked': datetime.datetime.now(MSK).isoformat(timespec='minutes'), 'upload': rep, 'errors': errs,
               'balance': {'advance': adv, 'real': real, 'bonus': bonus}},
              open(os.path.join(ROOT, 'out', 'check.json'), 'w'), ensure_ascii=False, indent=1)


def cmd_upload(args):
    # попросить Авито забрать файл по ссылке сразу (после выкладки изменений: цены, продажи, новые позиции)
    try: req('/autoload/v1/upload', {}, method='POST')
    except json.JSONDecodeError: pass          # Авито отвечает пустым телом — это успех
    except urllib.error.HTTPError as e:
        if e.code == 429: print('Авито: ручной запуск загрузки уже был недавно — файл заберёт по расписанию (в начале часа)'); return
        print('ВНИМАНИЕ не удалось запустить загрузку Авито сразу:', e); return
    except Exception as e: print('ВНИМАНИЕ не удалось запустить загрузку Авито сразу:', e); return
    print('Авито: загрузка файла запущена сразу после выкладки')


SPEC_SLUGS = {'Для автомобилей - Автосвет': 'avtosvet', 'Для автомобилей - Двигатель': 'dvigatel', 'Для автомобилей - Кузов': 'kuzov',
    'Для автомобилей - Подвеска': 'podveska', 'Для автомобилей - Рулевое управление': 'rulevoe_upravlenie', 'Для автомобилей - Салон': 'salon',
    'Для автомобилей - Стёкла': 'stekla', 'Для автомобилей - Топливная и выхлопная системы': 'toplivnaia_i_vyxlopnaia_sistemy',
    'Для автомобилей - Тормозная система': 'tormoznaia_sistema_5539', 'Для автомобилей - Трансмиссия и привод': 'transmissiia_i_privod',
    'Для автомобилей - Электрооборудование': 'elektrooborudovanie', 'Для автомобилей - Система охлаждения': 'sistema_oxlazdeniia',
    'Аудио- и видеотехника - Другое': 'audio__i_videotehnika_drugoe', 'Багажники и фаркопы - Фаркопы и комплектующие': 'farkopy_i_komplektuyushie'}


def spec_snapshot():
    snap = {}
    for name, slug in SPEC_SLUGS.items():
        F = req(f'/autoload/v1/user-docs/node/{slug}/fields')['fields']; d = {}
        for f in F:
            c = (f.get('content') or [{}])[0]
            vals = sorted(str(v.get('value') if isinstance(v, dict) else v) for v in (c.get('values') or []))
            d[f['label']] = {'req': bool(c.get('required')), 'dep': bool(c.get('required_by_dependency')), 'values': vals}
        snap[name] = d
    return snap


def cmd_specs(args):
    """Сверка официальных шаблонов Авито (API) с сохранённым снимком и с нашим шаблоном."""
    import openpyxl
    path = os.path.join(ROOT, 'data', 'avito_spec.json')
    snap = spec_snapshot()
    if '--baseline' in args or not os.path.exists(path):
        json.dump(snap, open(path, 'w'), ensure_ascii=False, indent=0); print('снимок шаблонов Авито сохранён'); return
    old = json.load(open(path, encoding='utf-8')); msgs = []
    heads = {}
    # 24.11: фаркопы живут в отдельном файле (rbmw_farkop.xlsx) по своему шаблону — сверяем оба
    for tpl in ('template.xlsx', 'template_farkop_avito.xlsx'):
        p = os.path.join(ROOT, 'data', tpl)
        if not os.path.exists(p): continue
        wb = openpyxl.load_workbook(p, read_only=(tpl == 'template.xlsx'))   # у шаблона фаркопов неверные размеры листа — читаем целиком
        for ws in wb.worksheets:
            rows = ws.iter_rows(min_row=1, max_row=2, values_only=True); a1 = next(rows, [None])[0]; h = next(rows, ())
            if a1 and h: heads[str(a1)] = heads.get(str(a1), set()) | set(x for x in h if x)
    for name, d in snap.items():
        o = old.get(name, {}); h = next((v for k, v in heads.items() if k.endswith(name)), set())
        for lab, f in d.items():
            if f['req'] and lab not in h: msgs.append(f'«{name}»: обязательное поле «{lab}» отсутствует в нашем шаблоне')
            if lab not in o: msgs.append(f'«{name}»: Авито добавил поле «{lab}»' + (' (ОБЯЗАТЕЛЬНОЕ)' if f['req'] else ''))
            elif f['req'] and not o[lab]['req']: msgs.append(f'«{name}»: поле «{lab}» стало обязательным')
            elif o[lab]['values'] and f['values'] != o[lab]['values']:
                gone = sorted(set(o[lab]['values']) - set(f['values']))
                if gone: msgs.append(f'«{name}»: в поле «{lab}» Авито убрал значения {gone[:5]}')
        for lab in o:
            if lab not in d: msgs.append(f'«{name}»: Авито убрал поле «{lab}»')
    if msgs:
        print('ВНИМАНИЕ изменились шаблоны Авито:'); [print(' -', m) for m in msgs[:30]]
    else: print('Шаблоны Авито без изменений')


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
    real, bonus = balance(u); real = advance()
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
                 ('Аванс Авито, ₽', sm['balance']), ('Бонусы', sm['bonus'])]:
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
    {'check': cmd_check, 'report': cmd_report, 'upload': cmd_upload, 'specs': cmd_specs}[sys.argv[1]](sys.argv[2:])
