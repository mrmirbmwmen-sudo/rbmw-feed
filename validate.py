#!/usr/bin/env python3
"""Проверка файла автозагрузки ПЕРЕД выкладкой на GitHub. Если что-то не так — выход с ошибкой, файл не выкладывается.

  python3 validate.py out/rbmw.xlsx            проверить
  python3 validate.py out/rbmw.xlsx --baseline записать текущую структуру шаблона как проверенную Авито

Что проверяется:
 1. Структура каждого листа (заголовки, колонки, порядок) совпадает с той, которую Авито принимал (data/accepted_layout.json).
    Любая смена шаблона = остановка, пока структура не подтверждена успешной загрузкой.
 2. Число объявлений = число активных позиций в учёте; нет «хвостов» (лишних строк до миллиона).
 3. Обязательные поля заполнены, значения из выпадающих списков — из справочников Авито.
 4. Цена в файле = цена в прайсе Bazon; цена > 0.
 5. У каждого объявления есть фото, и файлы фото лежат в хранилище.
 6. Уникальные ID, заголовки до 50 символов.
"""
import sys, os, json, csv, openpyxl

ROOT = os.path.dirname(os.path.abspath(__file__))
LAYOUT = os.path.join(ROOT, 'data', 'accepted_layout.json')
csv.field_size_limit(10**9)


def layout(wb):
    return {ws.title: [c.value for c in ws[2]] for ws in wb.worksheets if not ws.title.startswith('Спр') and ws.title != 'Инструкция'}


def main():
    path = sys.argv[1]
    wb = openpyxl.load_workbook(path)
    if '--baseline' in sys.argv:
        json.dump(layout(wb), open(LAYOUT, 'w'), ensure_ascii=False, indent=0); print('структура записана как проверенная'); return
    err = []
    base = json.load(open(LAYOUT, encoding='utf-8'))
    cur = layout(wb)
    for t, h in cur.items():
        if t not in base: err.append(f'новый лист «{t}» — Авито его ещё не принимал')
        elif h != base[t]: err.append(f'изменилась структура листа «{t}»')
    for t in base:
        if t not in cur: err.append(f'пропал лист «{t}»')
    state = json.load(open(os.path.join(ROOT, 'state.json'), encoding='utf-8'))['items']
    bazon = {r['Артикул'].strip(): r for r in csv.DictReader(open(os.path.join(ROOT, 'data', 'bazon.csv'), encoding='cp1251', errors='replace'), delimiter=';')}
    ids = []
    for ws in wb.worksheets:
        if ws.title.startswith('Спр') or ws.title == 'Инструкция': continue
        h = [c.value for c in ws[2]]
        if 'Уникальный идентификатор объявления' not in h: continue
        idc = h.index('Уникальный идентификатор объявления')
        req = [h[i] for i, c in enumerate(ws[3]) if c.value == 'Обязательный' and h[i]]
        L = {}
        for dv in ws.data_validations.dataValidation:
            try:
                col = openpyxl.utils.column_index_from_string(str(dv.sqref).split(':')[0].rstrip('0123456789')) - 1
                sh, rng = dv.formula1.split('!')
                L[h[col]] = {str(c[0].value) for c in wb[sh.strip("'")][rng.replace('$', '')]}
            except Exception: pass
        if ws.max_row > 5000: err.append(f'лист «{ws.title}»: {ws.max_row} строк — лишние строки в файле')
        for r in ws.iter_rows(min_row=5, values_only=True):
            if not any(v not in (None, '') for v in r): continue
            d = dict(zip(h, r)); a = str(d.get('Уникальный идентификатор объявления') or '')
            if not a: err.append(f'«{ws.title}»: строка без ID'); continue
            ids.append(a)
            miss = [k for k in req if d.get(k) in (None, '')]
            if miss: err.append(f'№{a}: не заполнено {miss}')
            for k, v in d.items():
                if v not in (None, '') and k in L and k != 'Способ доставки' and str(v) not in L[k]: err.append(f'№{a}: «{k}» = {v} нет в справочнике Авито')
            pr = d.get('Цена')
            if not pr or pr <= 0: err.append(f'№{a}: нет цены')
            if a in bazon:
                bp = int(float(bazon[a]['Цена'].replace(',', '.') or 0))
                if pr != bp: err.append(f'№{a}: цена в файле {pr}, в Bazon {bp}')
            else: err.append(f'№{a}: нет в прайсе Bazon (продано?) — а в файле есть')
            if len(str(d.get('Название объявления') or '')) > 50: err.append(f'№{a}: заголовок длиннее 50')
            urls = [u.strip() for u in str(d.get('Ссылки на фото') or '').split('|') if u.strip()]
            if not urls: err.append(f'№{a}: нет фото')
            for u in urls:
                f = os.path.join(ROOT, 'photos', u.rsplit('/', 1)[-1])
                if '/photos/' in u and not os.path.exists(f): err.append(f'№{a}: нет файла фото {u.rsplit("/", 1)[-1]}'); break
    if len(ids) != len(set(ids)): err.append('повторяющиеся ID объявлений')
    active = {a for a, it in state.items() if it['status'] in ('active', 'new') and it.get('photos') and a in bazon}
    if set(ids) != active:
        lost, extra = active - set(ids), set(ids) - active
        if lost: err.append(f'в файле нет активных позиций: {sorted(lost)[:10]}')
        if extra: err.append(f'в файле лишние позиции: {sorted(extra)[:10]}')
    if err:
        print('ПРОВЕРКА НЕ ПРОЙДЕНА — файл НЕ выкладывается:'); [print(' -', e) for e in err[:40]]
        sys.exit(1)
    print(f'проверка пройдена: {len(ids)} объявлений, структура как в принятом Авито шаблоне')


if __name__ == '__main__':
    main()
