#!/usr/bin/env python3
"""Отдельный файл автозагрузки для разделов в новом формате Авито (официальный шаблон «Объявления» + «Справочники»).
Сейчас: «Багажники и фаркопы → Фаркопы и комплектующие» (data/template_farkop_avito.xlsx).
Позиции: в state.json статус active и feed = "farkop". Цены, проданное — как в основном файле (sync).
  python3 -I extra_feed.py   -> out/rbmw_farkop.xlsx (+ проверка)"""
import sys, os, json, openpyxl
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rbmw
st = rbmw.load_state(); cur = rbmw.load_csv(rbmw.CSV_NEW)
wb = openpyxl.load_workbook(rbmw.P('data', 'template_farkop_avito.xlsx')); ws = wb['Объявления']
h = [c.value for c in ws[2]]; spr = wb['Справочники']; sh = [c.value for c in spr[1]]
lists = {}
for dv in ws.data_validations.dataValidation:
    col = openpyxl.utils.column_index_from_string(str(dv.sqref).split(':')[0].rstrip('0123456789')) - 1
    rng = dv.formula1.split('!')[1].replace('$', '')
    lists[h[col]] = {str(c[0].value) for c in spr[rng] if c[0].value is not None}
base = rbmw.CFG['photo_base'].rstrip('/') + '/'
row, n, err = 5, 0, []
for a, it in sorted(st['items'].items()):
    if it.get('feed') != 'farkop' or it['status'] not in ('active', 'new') or a not in cur or not it.get('photos'): continue
    r = cur[a]; title = it['title']
    v = {'Уникальный идентификатор объявления': a, 'Способ размещения': 'Package', 'Адрес': rbmw.CFG['address'],
         'Способ связи': rbmw.CFG.get('contact', 'По телефону и в сообщениях'), 'Категория': 'Запчасти и аксессуары',
         'Название объявления': title, 'Описание объявления': rbmw.make_desc(r, a, title),
         'Ссылки на фото': ' | '.join(base + f'{a}_{k}.jpg' for k in range(it['photos'])), 'Цена': it['price'],
         'Вид товара': 'Багажники и фаркопы', 'Тип товара': 'Фаркопы и комплектующие', 'Вид объявления': 'Товар приобретен на продажу',
         'Состояние': 'Новое' if r['Новый/БУ'] == 'Новый' else 'Б/у', 'Страна происхождения товара': 'Германия',
         'Авто для которых подходит запчасть': '\n'.join(rbmw.compat_lines(r)) or None}
    for k, val in v.items():
        if k not in h: err.append(f'нет колонки {k}'); continue
        if val is not None and k in lists and str(val) not in lists[k]: err.append(f'№{a}: {k}={val} нет в справочнике')
        if val is not None: ws.cell(row, h.index(k) + 1).value = val
    if int(rbmw.num(r['Цена'])) != it['price']: err.append(f'№{a}: цена не равна Bazon')
    req = [h[i] for i, c in enumerate(ws[3]) if c.value == 'Обязательный' and h[i]]
    miss = [k for k in req if ws.cell(row, h.index(k) + 1).value in (None, '')]
    if miss: err.append(f'№{a}: не заполнено {miss}')
    row += 1; n += 1; it['status'] = 'active'
out = rbmw.P('out', 'rbmw_farkop.xlsx'); os.makedirs(rbmw.P('out'), exist_ok=True); wb.save(out); rbmw.save_state(st)
if err: print('ПРОВЕРКА ДОП. ФАЙЛА НЕ ПРОЙДЕНА:', err); sys.exit(1)
print(f'доп. файл (фаркопы): {n} объявлений, проверка пройдена')
