#!/usr/bin/env python3
"""Автозагрузка Авито для магазина RBMW (прайс Bazon -> файл автозагрузки на GitHub).

Команды:
  python3 rbmw.py fetch [--csv путь]   скачать прайс Bazon (или взять готовый файл)
  python3 rbmw.py sync                 убрать проданное, обновить цены
  python3 rbmw.py pick N               добавить N новых позиций в учёт
  python3 rbmw.py photos               скачать и обработать фото для новых позиций
  python3 rbmw.py build                собрать файл автозагрузки out/rbmw.xlsx
  python3 rbmw.py status               сводка по учёту
Ежедневный цикл: daily.sh
"""
import sys, os, re, csv, json, html, copy, hashlib, random, datetime, collections, io
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
import cls, text

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)
CFG = json.load(open(P('config.json'), encoding='utf-8'))
STATE_F = P('state.json')
CSV_NEW, CSV_PREV = P('data', 'bazon.csv'), P('data', 'bazon_prev.csv')
csv.field_size_limit(10**9)
TODAY = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).strftime('%Y-%m-%d')

# ---------------- общие функции ----------------
def num(v):
    try: return float(str(v).replace(',', '.').strip() or 0)
    except ValueError: return 0.0

def load_csv(path):
    rows = {}
    for r in csv.DictReader(open(path, encoding='cp1251', errors='replace'), delimiter=';'):
        a = r['Артикул'].strip()
        if a: rows[a] = r
    return rows

def load_state():
    if os.path.exists(STATE_F): return json.load(open(STATE_F, encoding='utf-8'))
    return {'items': {}, 'log': []}

def save_state(st):
    tmp = STATE_F + '.tmp'
    json.dump(st, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_F)

def log(st, msg):
    st['log'].append(f'{datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))):%d.%m %H:%M} {msg}')
    st['log'] = st['log'][-300:]
    print(msg)

TRL = str.maketrans('АВЕКМНОРСТХаеорсухЕ', 'ABEKMHOPCTXaeopcyxE')
def norm(x): return re.sub(r'[^0-9A-Z]', '', str(x).upper().translate(TRL))
def oem_list(r): return [x.strip() for x in r['Номер'].split(',') if x.strip()]
def photos_of(r): return [u.strip() for u in r['Фото'].split(',') if u.strip()]
def s(v): return '' if v is None else str(v).strip()

# ---------------- применяемость ----------------
COMPAT = json.load(open(P('data', 'compat.json'), encoding='utf-8'))
# Только значения «Поколения», которые Авито точно принимает (у коллеги опубликовано по ним 20+ объявлений).
# 19.11: «4 серия Gran Coupe|G26» и подобные редкие строки дали ошибку «Поколение: значение не найдено».
COMPAT_OK = set(json.load(open(P('data', 'compat_ok.json'), encoding='utf-8')))
MAX_COMPAT = 10
LCI_YEAR = {'G05': 2023, 'G06': 2023, 'G07': 2022, 'F95': 2023, 'F96': 2023, 'F97': 2021, 'F98': 2021,
            'G01': 2021, 'G02': 2021, 'G87': 2024, 'G80': 2024, 'G81': 2024, 'G82': 2024, 'G83': 2024,
            'G30': 2020, 'G31': 2020, 'G11': 2019, 'G12': 2019}
MBODY = {'F95', 'F96', 'F97', 'F98', 'G87', 'G80', 'G81', 'G82', 'G83', 'F90', 'F91', 'F92', 'F93', 'G09'}

def donor_body(r):
    b = r['Кузов'].split(',')[0].split()
    return b[0].upper() if b else ''

def is_lci(r):
    b = donor_body(r); y = r['Год'][-4:]
    y = int(y) if y.isdigit() else 0
    return 'LCI' in r['Модель'].upper() or (b in LCI_YEAR and y > LCI_YEAR[b])

def fit_bodies(r):
    """Кузова из блока «… подходит:» в комментарии Bazon (коды в скобках)."""
    com = r['Комментарий'].replace('\\n', '\n')
    out = []
    if 'подходит' in com:
        tail = com.split('подходит', 1)[1]
        for grp in re.findall(r'\(([^)]*)\)', tail):
            for c in re.findall(r'\b([EFGIU]\d\d)\b', grp):
                if c not in out: out.append(c)
    return out

def compat_lines(r, verified=True):
    b = donor_body(r); L = []
    def add(x):
        if x and (x in COMPAT_OK or not verified) and x not in L and len(L) < MAX_COMPAT: L.append(x)
    if b in COMPAT:
        d = COMPAT[b]; add(d.get('lci') if is_lci(r) and d.get('lci') else d.get('pre') or d.get('lci'))
    for c in fit_bodies(r):
        if c in COMPAT and c != b:
            add(COMPAT[c].get('pre')); add(COMPAT[c].get('lci'))
    if not L and verified:          # поле обязательное: если проверенных нет, берём как есть (не больше 3)
        L = compat_lines(r, verified=False)[:3]
    return L

# ---------------- заголовок и описание ----------------
def side(r):
    return ' '.join(x for x in [r['Перед/Зад'], r['Лев/Прав']] if x).lower()

def norm_model(m):
    """Модель из Bazon в том виде, как ищут люди: «7 - series», «5-series(M)» → «7 серия» (21.11)."""
    m = re.sub(r'(?i)(\d)\s*-\s*series', r'\1 серия', str(m or '').replace("'", ' '))
    m = re.sub(r'\(M\)', '', m)
    return re.sub(r'\s+', ' ', m).strip()

def make_title(r):
    b = donor_body(r)
    ms = norm_model(r['Модель'].split(',')[0].replace('BMW', ''))
    ser = re.match(r'\d серия\b', ms)
    mdl = ms.split()
    mdl = ser.group(0) if ser else next((w for w in mdl if w.upper() not in ('LCI',) and not re.fullmatch(r'\d', w)), '')
    if mdl.upper() == b: mdl = ''
    w = [r['Наименование'], side(r), 'BMW', mdl, b, 'LCI' if is_lci(r) and b in LCI_YEAR else '', r['Год'][-4:]]
    t = text.clean_title(' '.join(x for x in w if x))
    comp = '\n'.join(compat_lines(r))
    return text.add_bodies(t, comp)

def esc(t): return html.escape(t, quote=False)

POLICY_HTML = text.POLICY
ENGINE_C5 = ('Двигатель', 'Топливная и выхлопная системы')

def engine_code(r):
    e = r['Двигатель'].strip().split()
    return e[0].upper() if e else ''

def clean(s):
    """Без скобок, лишних запятых и «крючков»: так Авито лучше собирает поисковые фразы (правила руководства 19.11)."""
    s = text.unent(s)
    s = re.sub(r'(?i)(\d)\s*-\s*series', r'\1 серия', s)          # «7 - series» → «7 серия»
    s = re.sub(r'[()\[\]{}«»"]', ' ', s)
    s = re.sub(r'\s+([,.;:!?])', r'\1', s)                          # без пробела перед знаком (после снятия скобок)
    s = re.sub(r'([,;:])(?=[^\s\d])', r'\1 ', s)                     # и с пробелом после
    return re.sub(r'\s+', ' ', s).strip()

def synonyms(r, title, bb):
    """3-4 строки синонимов без знаков: словарь руководства + код двигателя для моторных деталей + общие формы."""
    out = text.syn_lines(title, bb)
    name = re.split(r'(?i)\bBMW\b', title, maxsplit=1)[0].strip()
    tail = ' '.join(bb)
    c5 = category(r['Наименование'])[0]
    ec = engine_code(r)
    extra = []
    if ec and c5 in ENGINE_C5: extra.append(f'{name} BMW {ec} {tail}')
    extra += [f'{name} оригинал BMW {tail}', f'{name} BMW {tail} {"новая" if r["Новый/БУ"] == "Новый" else "б/у"}']
    for x in extra:
        x = re.sub(r'\s+', ' ', x).strip()
        if len(out) >= 4: break
        if x.lower() != title.lower() and x not in out: out.append(x)
    return out[:4]

def soft_caps(x):
    """21.11: «С ДЕФЕКТАМИ! НА ЗАПЧАСТИ!» → «С дефектами! На запчасти!» (латиница и коды не трогаются)."""
    KEEP = ('АКПП', 'МКПП', 'КПП', 'ДВС', 'ЭБУ', 'ГУ', 'ОГ', 'ПТФ', 'РФ', 'ТО', 'ГБЦ', 'ЕГР', 'ЭУР', 'ГУР')
    if len(re.findall(r'\b[А-ЯЁ]{3,}\b', x)) < 2: return x       # только строки, набранные капсом
    x = re.sub(r'\b[А-ЯЁ]{2,}\b', lambda m: m.group(0) if m.group(0) in KEEP else m.group(0).lower(), x)
    return re.sub(r'(^|[.!?]\s+)([а-яё])', lambda m: m.group(1) + m.group(2).upper(), x)

def make_desc(r, art, title):
    if CFG.get('desc_v2'): return make_desc_v2(r, art, title)
    comp = '\n'.join(compat_lines(r))
    bb = text.bodies(comp, title); syn = synonyms(r, title, bb)
    syn = CFG.get('syn_override', {}).get(art, syn)   # 21.11: свои строки синонимов для отдельных объявлений (фары)
    # первая строка = заголовок дословно (правило руководства); 21.11 вечер: артикул жирным после строк синонимов
    L = ['<p><strong>' + esc(title) + '</strong>' + ''.join('<br>' + esc(x) for x in syn) + '<br><strong>Артикул товара №' + art + '</strong></p>']
    if text.is_door(title): L.append(text.DOORNOTE)
    cond = 'Новая оригинальная запчасть BMW' if r['Новый/БУ'] == 'Новый' else 'Оригинальная запчасть BMW б/у'
    sell = [cond + ' в наличии в Москве']
    if delivery(title): sell.append('Отправка по России через Авито Доставку')
    L.append('<p>' + '<br>'.join(sell) + '</p>')
    L.append(text.KOMPL)
    nums = oem_list(r)
    info = []
    if nums: info.append('Номер детали BMW ' + esc(nums[0]))
    cross = [x for x in nums[1:] if len(x) >= 7 and x != nums[0]][:5]
    if cross: info.append('Кросс-номера ' + esc(' '.join(dict.fromkeys(cross))))
    if info: L.append('<p>' + '<br>'.join(info) + '</p>')
    com = r['Комментарий'].replace('\\n', '\n')
    # блок применяемости Bazon выводим отдельно («Подходит для»); 21.11: и когда комментарий с него начинается
    com = re.split(r'(?i)(?:^|\n\s*\n)\s*' + re.escape(r['Наименование'].strip()) + r'\s+подходит', com)[0]
    lines = [esc(clean(x)) for x in com.split('\n') if x.strip()]
    lines = [soft_caps(re.sub(r'(?i)\s*\bв\s+сборе\b', '', x)) for x in lines if x]
    # 21.11: «Снято с BMW X5 xDrive50e G05 LCI B58B30S» — LCI после кузова, без повтора кода мотора из модели
    eng = r['Двигатель'].strip()
    mw = [w for w in norm_model(r['Модель']).split() if w.upper() != 'LCI']
    if eng: mw = [w for w in mw if not re.fullmatch(r'(?i)[BNS]\d\d[A-Z]?', w)]
    lci = ' LCI' if ('LCI' in r['Модель'].upper() or is_lci(r)) and r['Кузов'] and 'LCI' not in r['Кузов'].upper() else ''
    donor = 'Снято с BMW ' + esc(clean(' '.join(x for x in [' '.join(mw), (r['Кузов'] + lci).strip(), eng] if x)))
    if r['Год']: donor += f' {esc(r["Год"])} года выпуска'
    L.append('<p><strong>Состояние и комплектность</strong><br>' + '<br>'.join(lines + [donor]) + '</p>')
    if comp:
        fl = []
        for x in comp.split('\n'):
            p = x.split('|'); fl.append(clean(f'BMW {p[1]} {p[2]}'))
        L.append('<p><strong>Подходит для</strong><br>' + '<br>'.join(esc(x) for x in fl) + '</p>')
    L.append('<p>' + text.VINP + '</p>')
    L.append(POLICY_HTML)
    return ' '.join(L)


# ---------------- описание v2 (21.11): синонимы живыми предложениями, «Почему у нас», «Как купить» ----------------
HOOK = r'без пробега|тестов\w* (авто|а/м|машин)|состояни\w* нов|как нов|идеальн\w* состояни|отличн\w* состояни'
AGREE = {'m': ('оригинальный', 'нужен'), 'f': ('оригинальная', 'нужна'), 'n': ('оригинальное', 'нужно'), 'p': ('оригинальные', 'нужны')}
POLICY_V2 = ('<p><strong>Проверка и возврат</strong><br>'
             'Вернуть деталь можно в течение 14 дней после получения, если она в том виде, в каком мы её отправили. '
             'Если деталь не соответствует описанию или фото, вернём деньги полностью.<br>'
             'Не принимаем обратно детали со следами установки, вскрытия или повреждениями после продажи, '
             'а также электронные блоки после программирования или кодирования.<br>'
             'Расходы на установку и расходные материалы не возмещаются.</p>')

def _lc(x):
    w = x.split(' ', 1)
    return (w[0] if re.match(r'^[А-ЯЁA-Z]{2,}', w[0]) else w[0][:1].lower() + w[0][1:]) + (' ' + w[1] if len(w) > 1 else '')

def intro_v2(r, title, syn, bb):
    """Живой абзац вместо 4 строк синонимов: те же ключевые слова, без столбика ключей (21.11)."""
    phr = []
    head = re.split(r'(?i)\s+BMW\b', title, 1)[0].lower()
    for x in syn:
        p = re.split(r'(?i)\s+(оригинал\s+)?BMW\b', x, 1)[0].strip()
        if p and p.lower() != head and p.lower() not in [q.lower() for q in phr]: phr.append(p)
    com = r['Комментарий'].replace('\\n', '\n')
    hook = next((clean(l) for l in com.split('\n') if re.search(HOOK, l, re.I)), '')
    ec = engine_code(r) if category(r['Наименование'])[0] in ENGINE_C5 else ''
    if phr:
        s1 = f'{title} — {AGREE[text.gender(phr[0].split()[0])][0]} {_lc(phr[0])}'
    else:
        s1 = f'{title} — оригинальная запчасть BMW'
    if ec: s1 += f' для двигателя {ec}'
    s1 += (', ' + _lc(hook).rstrip('.!')) if hook else (', новая' if r['Новый/БУ'] == 'Новый' else ', б/у')
    out = [s1 + '.']
    if len(phr) > 1: out.append('Другие названия: ' + ', '.join(_lc(x) for x in phr[1:3]) + '.')
    return ' '.join(out)

def make_desc_v2(r, art, title):
    comp = '\n'.join(compat_lines(r))
    bb = text.bodies(comp, title); syn = synonyms(r, title, bb)
    nums = oem_list(r); deliv = bool(delivery(title))
    L = ['<p><strong>' + esc(title) + '</strong><br><strong>Артикул товара №' + art + '</strong></p>',
         '<p>' + esc(intro_v2(r, title, syn, bb)) + '</p>']
    if text.is_door(title): L.append(text.DOORNOTE)
    why = []
    why.append('Оригинал BMW' + (', номер детали ' + esc(nums[0]) if nums else ''))
    why.append('До оплаты проверим совместимость по VIN вашего автомобиля')
    why.append('По запросу пришлём дополнительные фото и видео детали')
    why.append('В наличии на складе в Москве, можно осмотреть перед покупкой')
    if deliv: why.append('Отправка по России через Авито Доставку')
    L.append('<p><strong>Почему стоит купить у нас</strong><br>' + '<br>'.join('• ' + x for x in why) + '</p>')
    info = []
    if nums: info.append('Номер детали BMW ' + esc(nums[0]))
    cross = [x for x in nums[1:] if len(x) >= 7 and x != nums[0]][:5]
    if cross: info.append('Кросс-номера ' + esc(' '.join(dict.fromkeys(cross))))
    if info: L.append('<p><strong>Номер детали и кросс-номера</strong><br>' + '<br>'.join(info) + '</p>')
    com = r['Комментарий'].replace('\\n', '\n')
    com = re.split(r'(?i)(?:^|\n\s*\n)\s*' + re.escape(r['Наименование'].strip()) + r'\s+подходит', com)[0]
    lines = [esc(clean(x)) for x in com.split('\n') if x.strip()]
    lines = [soft_caps(re.sub(r'(?i)\s*\bв\s+сборе\b', '', x)) for x in lines if x]
    eng = r['Двигатель'].strip()
    mw = [w for w in norm_model(r['Модель']).split() if w.upper() != 'LCI']
    if eng: mw = [w for w in mw if not re.fullmatch(r'(?i)[BNS]\d\d[A-Z]?', w)]
    lci = ' LCI' if ('LCI' in r['Модель'].upper() or is_lci(r)) and r['Кузов'] and 'LCI' not in r['Кузов'].upper() else ''
    donor = 'Снято с BMW ' + esc(clean(' '.join(x for x in [' '.join(mw), (r['Кузов'] + lci).strip(), eng] if x)))
    if r['Год']: donor += f' {esc(r["Год"])} года выпуска'
    L.append('<p><strong>Состояние и комплектность</strong><br>' + '<br>'.join(lines + [donor, 'Комплектацию и что входит в стоимость уточняйте у менеджера']) + '</p>')
    if comp:
        fl = [clean(f'BMW {x.split("|")[1]} {x.split("|")[2]}') for x in comp.split('\n')]
        L.append('<p><strong>Подходит для</strong><br>' + '<br>'.join(esc(x) for x in fl) + '</p>')
    L.append('<p><strong>Как купить</strong><br>Напишите в чат и пришлите VIN: менеджер проверит совместимость и подтвердит наличие.</p>')
    L.append(POLICY_V2 if CFG.get('returns_v2') else POLICY_HTML)
    return ' '.join(L)

# ---------------- категория ----------------
AUDIO = r'акустик|harman|kardon|bowers|wilkins|b&w|динамик|сабвуфер|аудиосистем|автозвук|комплект (музыки|звука)|усилитель (звука|аудио)'
TRANS = [(r'селектор|кулис|переключ', 'Переключение передач'), (r'редуктор|дифференц', 'Мосты и редукторы'),
         (r'кардан', 'Карданная передача'), (r'приводн\w* вал|вал приводн|полуос|шрус', 'Приводные валы, полуоси и ШРУСы'),
         (r'раздаточн', 'Раздаточная коробка')]
def category(name):
    n = name.lower()
    if re.search(AUDIO, n) and not re.search(r'сетк|проводк|жгут', n): return 'AUDIO', None, None
    if re.search(r'фаркоп|прицепное устройство|тягово-сцепн', n) and not re.search(r'блок|проводк|жгут|кнопк', n): return 'FARKOP', None, None
    if re.match(r'руль\b|рулевое колесо', n): return 'Рулевое управление', None, None   # 19.11: Авито сам перенёс руль из «Салона»
    c5, tag, val = cls.classify(name)
    if c5 not in ('Трансмиссия и привод',) and not re.search(r'кронштейн|опора|подушк|пыльник', n):
        for rx, tv in TRANS:
            if re.search(rx, n): return 'Трансмиссия и привод', 'TransmissionSparePartType', tv
    return c5, tag, val

# ---------------- доставка (из delivery.py) ----------------
NODELIV = r'двер|бампер|капот|крыл[оья]|крышка багажника|подрамник|двигатель в сборе|двс|акпп в сборе|кпп в сборе|кузов|торпед|сидень|кресл|крыш[аи]|порог|балк|лонжерон|рама|лобов|стекло|кардан|рейлинг|радиатор|интеркулер|конденсатор|диск|колес|колёс|шин|выхлопн|глушител|катализатор|багажник|спойлер|панел'
DELIV = [(r'фара', 8, 80, 40, 50), (r'фонар', 3, 60, 25, 40), (r'монитор|дисплей|экран|головное устройство|mgu|проекц|nbt|evo', 3, 45, 15, 35),
         (r'акустик|динамик|усилител|сабвуфер|harman|bowers', 6, 60, 30, 40), (r'зеркал', 5, 50, 30, 40), (r'руль', 5, 50, 20, 50),
         (r'селектор|кулис', 2, 35, 20, 25), (r'амортизатор|стойк', 7, 90, 25, 25), (r'кулак|ступиц', 10, 50, 30, 35), (r'рычаг|тяг', 6, 70, 20, 30),
         (r'насос|компрессор', 8, 45, 30, 35), (r'турбин', 12, 50, 40, 40), (r'коллектор', 10, 70, 30, 40),
         (r'приводн\w* вал|вал приводн|полуос|шрус|привод (перед|задн|лев|прав)', 9, 110, 15, 15),
         (r'блок управления|эбу|модуль|блок|dme|dde|bdc|ecu', 1.5, 35, 12, 25),
         (r'датчик|камер|антенн|реле|переключател|кнопк|выключател|замок|привод|моторчик|сервопривод', 1, 25, 15, 20),
         (r'форсунк|катушк|свеч', 1, 25, 10, 20), (r'обшивк|накладк|молдинг|решетк|решётк', 4, 100, 15, 45), (r'жгут|проводк', 2, 40, 15, 30)]
def delivery(title):
    t = title.lower()
    if re.search(NODELIV, t): return None
    for rx, kg, L, H, W in DELIV:
        if re.search(rx, t):
            way = 'ПВЗ | Курьер | Постамат' if kg <= 3 and max(L, H, W) <= 45 else 'ПВЗ | Курьер'
            return {'Способ доставки': way, 'Вес (Для Доставки)': kg, 'Длина (Для Доставки)': L, 'Высота (Для Доставки)': H, 'Ширина (Для Доставки)': W}
    return None

# ---------------- фото ----------------
def photo_params(art, k):
    rnd = random.Random(int(hashlib.md5(f'{art}-{k}'.encode()).hexdigest(), 16))
    ang = rnd.uniform(0.8, 1.3) * rnd.choice((-1, 1))
    return dict(angle=ang, crop=[rnd.uniform(0.03, 0.04) for _ in range(4)],
                bright=rnd.uniform(1.05, 1.07), contrast=rnd.uniform(1.05, 1.07), color=rnd.uniform(0.99, 1.03))

def process_image(raw, art, k, max_side=1280):
    from PIL import Image, ImageEnhance, ImageOps
    import math
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')
    p = photo_params(art, k)
    w, h = im.size
    im = im.rotate(p['angle'], resample=Image.BICUBIC, expand=False, fillcolor=(255, 255, 255))
    t = math.sin(math.radians(abs(p['angle'])))
    need_x, need_y = (h * t) / w + 0.004, (w * t) / h + 0.004     # убрать углы после поворота
    l, tp, rt, bt = p['crop']
    l, rt = max(l, need_x), max(rt, need_x); tp, bt = max(tp, need_y), max(bt, need_y)
    im = im.crop((int(w * l), int(h * tp), int(w * (1 - rt)), int(h * (1 - bt))))
    im = ImageEnhance.Brightness(im).enhance(p['bright'])
    im = ImageEnhance.Contrast(im).enhance(p['contrast'])
    im = ImageEnhance.Color(im).enhance(p['color'])
    if max(im.size) > max_side:
        sc = max_side / max(im.size); im = im.resize((round(im.width * sc), round(im.height * sc)), Image.LANCZOS)
    out = io.BytesIO(); im.save(out, 'JPEG', quality=88, optimize=True, progressive=True)
    return out.getvalue()

def http_get(url, timeout=60):
    import urllib.request
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as f: return f.read()

# ---------------- команды ----------------
def cmd_fetch(args):
    src = args[args.index('--csv') + 1] if '--csv' in args else None
    tmp = CSV_NEW + '.tmp'
    if src: open(tmp, 'wb').write(open(src, 'rb').read())
    else:
        last = None
        for _ in range(3):
            try: open(tmp, 'wb').write(http_get(CFG['csv_url'], 300)); break
            except Exception as e: last = e; import time; time.sleep(20)
        else: raise SystemExit(f'Прайс Bazon не скачался: {last}')
    n = len(load_csv(tmp))
    if n < 3000: raise SystemExit(f'Подозрительно мало строк в прайсе: {n}, прайс не принят')
    if os.path.exists(CSV_NEW): os.replace(CSV_NEW, CSV_PREV)
    os.replace(tmp, CSV_NEW)
    print(f'прайс Bazon: {n} позиций')

def cmd_sync(args):
    st = load_state(); cur = load_csv(CSV_NEW)
    prev = set(load_csv(CSV_PREV)) if os.path.exists(CSV_PREV) else set()
    sold, price = [], []
    for a, it in st['items'].items():
        if it['status'] != 'active': continue
        if a in cur:
            p = int(num(cur[a]['Цена']))
            if p and p != it['price']:
                note = ''
                if p < it['price'] * 0.5:
                    # резкое снижение: массовая переоценка (у тех же номеров/наименований в базе та же цена) или одиночная — возможна ошибка
                    o = norm(oem_list(cur[a])[0]) if oem_list(cur[a]) else ''
                    same = [k for k, x in cur.items() if k != a and ((o and oem_list(x) and norm(oem_list(x)[0]) == o) or
                            (x['Наименование'] == cur[a]['Наименование'] and x['Кузов'] == cur[a]['Кузов']))]
                    eq = sum(1 for k in same if int(num(cur[k]['Цена'])) == p)
                    note = (f' [снижение {round(100 - 100 * p / it["price"])}%: массовая переоценка в базе — так же стоят ещё {eq} шт.]' if eq
                            else f' [ВНИМАНИЕ снижение {round(100 - 100 * p / it["price"])}%: одиночное, похожих по такой цене в базе нет — проверьте Bazon]')
                price.append(f"№{a} {it['title']}: {it['price']} → {p}{note}"); it['price'] = p
            continue
        # нет в прайсе: продано, если было в прошлом прайсе или дороже порога выгрузки (дешёвые Bazon иногда не выгружает)
        if a in prev or it['price'] > CFG.get('bazon_hidden_below', 6400):
            it['status'] = 'sold'; it['sold'] = TODAY; sold.append(f"№{a} {it['title']} ({it['price']} ₽)")
    if sold: log(st, 'Продано/снято: ' + '; '.join(sold))
    if price: log(st, 'Цены: ' + '; '.join(price))
    print(f'продано {len(sold)}, цен изменено {len(price)}')
    save_state(st)

def eligible(cur, st):
    excl = set(str(x) for x in json.load(open(P('data', 'exclude_colleague.json'))))
    excl |= set(str(x) for x in CFG.get('exclude_articles', []))
    used_oem = {norm(i['oem']) for i in st['items'].values() if i.get('oem')}
    out = []
    for a, r in cur.items():
        if a in st['items'] or a in excl: continue
        if r['Статус'] != 'В наличии' or num(r['Цена']) < CFG['min_price'] or not photos_of(r): continue
        if CFG.get('only_used', True) and r['Новый/БУ'] not in ('БУ', 'Контракт'): continue
        c5, _, _ = category(r['Наименование'])
        if c5 in (None, 'Аккумуляторы', 'Автомобиль на запчасти'): continue
        # разделы, которых нет в принятом шаблоне Авито (19.11 фаркоп отклонён: нужен «Багажники и фаркопы»)
        if re.search(r'фаркоп|прицепн|рейлинг|багажник на крыш', r['Наименование'].lower()): continue   # нет принятого Авито листа в нашей книге
        if CFG.get('only_bodies') and donor_body(r) not in CFG['only_bodies']: continue
        if c5 not in ('AUDIO', 'FARKOP') and not any(x in COMPAT_OK for x in compat_lines(r)): continue   # без проверенной применяемости Авито не опубликует
        o = oem_list(r)
        if o and norm(o[0]) in used_oem: continue
        out.append(r)
    out.sort(key=lambda r: -num(r['Цена']))
    return out

def cmd_pick(args):
    st = load_state()
    if args and args[0] == '--daily':          # раз в день: первый запуск — стартовая партия, потом per_day
        if any(i['added'] == TODAY for i in st['items'].values()): print('сегодня уже добавляли'); return
        n = CFG['per_day'] if st['items'] else CFG['start_batch']
    else: n = int(args[0]) if args else CFG['per_day']
    cur = load_csv(CSV_NEW); pool = eligible(cur, st)
    names, bodies, oems, got = collections.Counter(), collections.Counter(), set(), []
    for r in pool:
        if len(got) >= n: break
        o = norm(oem_list(r)[0]) if oem_list(r) else ''
        if o and o in oems: continue
        if names[r['Наименование']] >= 1 or bodies[donor_body(r)] >= max(3, n // 3): continue
        got.append(r); names[r['Наименование']] += 1; bodies[donor_body(r)] += 1; oems.add(o)
    for r in got:
        a = r['Артикул'].strip()
        st['items'][a] = {'status': 'new', 'added': TODAY, 'price': int(num(r['Цена'])), 'title': make_title(r),
                          'oem': oem_list(r)[0] if oem_list(r) else '', 'photos': 0}
    log(st, f'Добавлено в очередь {len(got)} (в пуле осталось {len(pool) - len(got)})')
    save_state(st)

def cmd_topup(args):
    """Замена позиций, снятых не из-за продажи (ошибка раздела, поколения и т.п.): добавляем столько же новых сразу."""
    st = load_state()
    need = [a for a, i in st['items'].items() if i['status'] == 'removed' and not i.get('replaced')]
    if not need: return
    cur = load_csv(CSV_NEW); pool = eligible(cur, st); oems = set(); got = []
    for r in pool:
        if len(got) >= len(need): break
        o = norm(oem_list(r)[0]) if oem_list(r) else ''
        if o and o in oems: continue
        got.append(r); oems.add(o)
    for r in got:
        a = r['Артикул'].strip()
        st['items'][a] = {'status': 'new', 'added': TODAY, 'price': int(num(r['Цена'])), 'title': make_title(r),
                          'oem': oem_list(r)[0] if oem_list(r) else '', 'photos': 0, 'replaces': need[len(st['items']) % len(need)] if need else None}
    for a in need[:len(got)]: st['items'][a]['replaced'] = True
    log(st, f'Замена снятых позиций: добавлено {len(got)} вместо {", ".join(need[:len(got)])}')
    save_state(st)


def cmd_photos(args):
    st = load_state(); cur = load_csv(CSV_NEW); ok = fail = 0
    for a, it in st['items'].items():
        if it['status'] not in ('new', 'active') or it.get('photos') or a not in cur: continue
        urls = photos_of(cur[a])[:10]; done = 0
        for k, u in enumerate(urls):
            dst = P('photos', f'{a}_{k}.jpg')
            if not os.path.exists(dst):
                try: open(dst, 'wb').write(process_image(http_get(u), a, k))
                except Exception as e: print('фото не скачалось', a, u, e); continue
            done += 1
        if done: it['photos'] = done; ok += 1
        else: fail += 1
    save_state(st); print(f'обработаны фото у {ok} позиций, без фото {fail}')

def sheet_for(wb, c5):
    for ws in wb.worksheets:
        a1 = str(ws['A1'].value or '')
        if c5 == 'AUDIO' and a1.endswith('Аудио- и видеотехника - Другое'): return ws
        if c5 == 'FARKOP' and a1.endswith('Багажники и фаркопы - Фаркопы и комплектующие'): return ws
        if a1.startswith('Транспорт - Запчасти и аксессуары - Запчасти - Для автомобилей') and a1.endswith(' - ' + str(c5)): return ws
    return None

def ensure_col(ws, name, fmt):
    h = [c.value for c in ws[2]]
    if name in h: return h.index(name) + 1
    c = len(h) + 1
    while ws.cell(2, c).value is not None: c += 1
    ws.cell(2, c).value = name; ws.cell(3, c).value = 'Необязательный'; ws.cell(4, c).value = fmt
    return c

def lists_of(wb, ws, h):
    import openpyxl
    L = {}
    for dv in ws.data_validations.dataValidation:
        try:
            col = openpyxl.utils.column_index_from_string(str(dv.sqref).split(':')[0].rstrip('0123456789')) - 1
            sh, rng = dv.formula1.split('!')
            if col < len(h): L[h[col]] = {str(c[0].value) for c in wb[sh.strip("'")][rng.replace('$', '')]}
        except Exception: pass
    return L

def cmd_build(args):
    import openpyxl
    st = load_state(); cur = load_csv(CSV_NEW)
    allow_raw = '--raw-photos' in args
    wb = openpyxl.load_workbook(P('data', 'template.xlsx'))
    base = CFG.get('photo_base', '').rstrip('/') + '/'
    stat = collections.Counter(); titles = collections.Counter(); problems = []
    rows = []
    for a, it in sorted(st['items'].items(), key=lambda x: (x[1]['added'], x[0])):
        if it['status'] not in ('new', 'active') or a not in cur: continue
        if it.get('feed'): continue          # позиции отдельных файлов (extra_feed.py)
        r = cur[a]
        if it.get('photos'): urls = [base + f'{a}_{k}.jpg' for k in range(it['photos'])]
        elif allow_raw: urls = photos_of(r)[:10]
        else: stat['ждут обработки фото'] += 1; continue
        rows.append((a, it, r, urls))
    for a, it, r, urls in rows:
        title = it['title']; titles[title.lower()] += 1
    for a, it, r, urls in rows:
        title = it['title']
        if titles[title.lower()] > 1:       # одинаковые заголовки: кузовным — цвет, остальным — 7 цифр номера
            if text.BODY.match(title) and r['Цвет']: ex = 'цвет ' + r['Цвет'].split('/')[0].split('_')[0].split()[0]
            else: ex = norm(it['oem'])[-7:]
            if ex and ex.lower() not in title.lower(): title = text.fit50(title, ex)
        c5, tag, val = category(r['Наименование'])
        ws = sheet_for(wb, c5)
        if ws is None: problems.append(f'№{a}: нет листа для «{c5}»'); continue
        h = [c.value for c in ws[2]]
        v = {'Уникальный идентификатор объявления': a, 'Способ размещения': 'Package', 'Номер телефона': CFG.get('phone') or None,
             'Адрес': CFG['address'], 'Способ связи': CFG.get('contact', 'По телефону и в сообщениях'), 'Категория': 'Запчасти и аксессуары',
             'Описание объявления': make_desc(r, a, title), 'Ссылки на фото': ' | '.join(urls), 'Название объявления': title,
             'Цена': it['price'], 'Вид объявления': 'Товар приобретен на продажу', 'Состояние': 'Б/у' if r['Новый/БУ'] != 'Новый' else 'Новое',
             'Доступность': 'В наличии', 'Производитель': 'BMW', 'Страна происхождения товара': 'Германия',
             'Номер детали OEM': re.sub(r'[^0-9A-Za-z-]', '', it['oem'].translate(TRL))}
        if c5 == 'AUDIO':
            v.update({'Вид товара': 'Аудио- и видеотехника', 'Тип техники': 'Другое'})
        elif c5 == 'FARKOP':     # раздел по спецификации API Авито (farkopy_i_komplektuyushie), 19.11
            v.update({'Вид товара': 'Багажники и фаркопы', 'Тип товара': 'Фаркопы и комплектующие',
                      'Авто для которых подходит запчасть': '\n'.join(compat_lines(r)) or None})
        else:
            v.update({'Вид товара': 'Запчасти', 'Тип товара': 'Для автомобилей', 'Вид запчасти': c5,
                      'Авто для которых подходит запчасть': '\n'.join(compat_lines(r)) or None})
            tcol = {'TipDetaliKuzova': 'Тип детали кузова', 'EngineSparePartType': 'Тип детали двигателя',
                    'TransmissionSparePartType': 'Тип детали трансмиссии'}.get(tag)
            if tcol: v[tcol] = val
        if CFG.get('delivery', True):
            d = delivery(title)
            if d:
                for k2, val2 in d.items():
                    ensure_col(ws, k2, 'Дробное число' if 'Вес' in k2 else 'Целое число'); v[k2] = val2
                stat['с доставкой'] += 1
        h = [c.value for c in ws[2]]
        lists = lists_of(wb, ws, h)
        bad = [f'{k}={vv}' for k, vv in v.items() if vv is not None and k in lists and str(vv) not in lists[k] and k not in ('Способ доставки',)]
        if bad: problems.append(f'№{a}: значение не из справочника Авито: {bad}')
        if c5 not in ('AUDIO', 'FARKOP') and not v.get('Авто для которых подходит запчасть'): stat['без применяемости'] += 1
        idc = h.index('Уникальный идентификатор объявления') + 1   # в новых шаблонах первой идёт колонка «Ошибка»
        row = 5
        while ws.cell(row, idc).value not in (None, ''): row += 1
        for j, name in enumerate(h, start=1):
            if name in v and v[name] is not None: ws.cell(row, j).value = v[name]
        if it['status'] == 'new': it['status'] = 'active'; it['first_feed'] = TODAY
        stat['в файле'] += 1; stat['лист: ' + ws.title] += 1
        if len(title) > 50: problems.append(f'№{a}: заголовок длиннее 50')
    os.makedirs(P('out'), exist_ok=True)
    out = P('out', CFG.get('feed_file', 'rbmw.xlsx'))
    wb.save(out + '.tmp'); os.replace(out + '.tmp', out)
    save_state(st)
    for k, v in sorted(stat.items()): print(f'{k}: {v}')
    for p in problems: print('ВНИМАНИЕ', p)
    print('файл:', out)

def cmd_cleanup(args):
    # фото проданных позиций удаляем через 7 дней, чтобы хранилище не разрасталось
    st = load_state(); n = 0
    lim = (datetime.date.fromisoformat(TODAY) - datetime.timedelta(days=7)).isoformat()
    for a, it in st['items'].items():
        if it['status'] == 'sold' and it.get('sold', TODAY) <= lim and it.get('photos'):
            for k in range(it['photos']):
                f = P('photos', f'{a}_{k}.jpg')
                if os.path.exists(f): os.remove(f); n += 1
            it['photos'] = 0
    save_state(st); print('удалено старых фото', n)

def cmd_status(args):
    st = load_state(); c = collections.Counter(i['status'] for i in st['items'].values())
    act = [i for i in st['items'].values() if i['status'] in ('active', 'new')]
    print(dict(c), 'на сумму', sum(i['price'] for i in act), '₽')
    for l in st['log'][-15:]: print(l)

if __name__ == '__main__':
    cmd, args = sys.argv[1], sys.argv[2:]
    {'fetch': cmd_fetch, 'sync': cmd_sync, 'pick': cmd_pick, 'photos': cmd_photos, 'build': cmd_build, 'status': cmd_status, 'cleanup': cmd_cleanup, 'topup': cmd_topup}[cmd](args)
