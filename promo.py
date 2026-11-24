"""Тест продвижения RBMW (с 24.11.2026 18:25 МСК): 4 кузовные позиции с ручной ставкой за просмотр.
Каждый запуск: дописывает дневную статистику (вчера и сегодня) в data/promo_log.json,
сверяет настройки продвижения на Авито и печатает сравнение «до / во время теста»
для тестовой группы и контрольной (похожие кузовные объявления без продвижения)."""
import datetime, json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analytics as a

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, 'data', 'promo_log.json')
START = datetime.date(2026, 11, 25)          # первый полный день теста (включено 24.11 в 18:25)
BASE = (datetime.date(2026, 11, 18), datetime.date(2026, 11, 23))   # 6 полных дней до теста
END = datetime.date(2026, 12, 1)
TEST = {'19090': 'Борт кузова X7 G07 LCI', '27345': 'Дверь задняя правая X6 G06 LCI', '7555': 'Дверь передняя правая X5 G05',
        '18991': 'Бампер задний X7 G07 LCI'}
PLAN = {'19090': (35, 90), '27345': (30, 90), '7555': (30, 90), '18991': (25, 90)}   # ставка ₽ за просмотр, лимит ₽ в день
CONTROL = re.compile(r'^(Дверь|Бампер|Крыло|Капот|Крышка багажника|Борт|Расширитель)', re.I)
M = ('impressions', 'views', 'contacts', 'favorites', 'allSpending')


def day_stats(u, d):
    for k in range(6):                     # статистика Авито: не чаще ~1 запроса в минуту, иначе 429
        try: S = a.stats_v2(u, d.isoformat(), d.isoformat()); break
        except Exception as e:
            if '429' not in str(e) or k == 5: raise
            time.sleep(65)
    return {str(k): {m: v.get(m, 0) for m in M} for k, v in S.items()}


def main():
    u = a.uid(); today = datetime.datetime.now(a.MSK).date()
    log = json.load(open(LOG, encoding='utf-8')) if os.path.exists(LOG) else {'days': {}, 'items': {}}
    items = [x for x in a.items_all() if x.get('status') == 'active']
    amap = a.ad_ids([x['id'] for x in items])
    for x in items:
        ad = str(amap.get(x['id']) or '')
        if ad: log['items'][ad] = {'avito_id': x['id'], 'title': x.get('title'), 'price': x.get('price')}
    need = [BASE[0] + datetime.timedelta(i) for i in range((today - BASE[0]).days + 1)]
    for d in need:
        if d.isoformat() in log['days'] and d < today - datetime.timedelta(1): continue
        st = day_stats(u, d); time.sleep(20)
        log['days'][d.isoformat()] = {str(amap.get(int(k)) or k): v for k, v in st.items()}
        json.dump(log, open(LOG, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    json.dump(log, open(LOG, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    # настройки продвижения на Авито = согласованные
    av = [log['items'][k]['avito_id'] for k in TEST if k in log['items']]
    pr = {str(p['itemID']): p for p in a.req('/cpxpromo/1/getPromotionsByItemIds', {'itemIds': av}).get('items', [])}
    for k, (bid, lim) in PLAN.items():
        p = pr.get(str(log['items'].get(k, {}).get('avito_id')), {}).get('manualPromotion') or {}
        if not p: print(f'ВНИМАНИЕ продвижение не найдено: №{k} {TEST[k]}')
        elif (p.get('bidPenny'), p.get('limitPenny')) != (bid * 100, lim * 100):
            print(f"ВНИМАНИЕ №{k} {TEST[k]}: ставка {p.get('bidPenny', 0) / 100:.0f} ₽, лимит {p.get('limitPenny', 0) / 100:.0f} ₽ (план {bid}/{lim})")

    def agg(ids, d1, d2):
        t = dict.fromkeys(M, 0); n = 0
        d = d1
        while d <= d2:
            day = log['days'].get(d.isoformat(), {}); n += 1
            for i in ids:
                for m in M: t[m] += day.get(i, {}).get(m, 0)
            d += datetime.timedelta(1)
        t['allSpending'] /= 100; return t, n

    ctrl = [k for k, v in log['items'].items() if k not in TEST and CONTROL.match(v.get('title') or '')]
    last = min(today, END)
    print(f'Тест продвижения: {START:%d.%m}–{END:%d.%m}, сегодня {today:%d.%m} (день {max((today - START).days + 1, 0)} из 7)')
    for name, ids in [('Тест (4 позиции)', list(TEST)), (f'Контроль ({len(ctrl)} кузовных без продвижения)', ctrl)]:
        b, nb = agg(ids, *BASE); t, nt = agg(ids, START, last) if today >= START else (dict.fromkeys(M, 0), 0)
        f = lambda s, n: f"показы {s['impressions'] / max(n, 1) / len(ids):.1f}, просмотры {s['views'] / max(n, 1) / len(ids):.2f} в день на объявл.; контакты {s['contacts']}, избранное {s['favorites']}, расход {s['allSpending']:.0f} ₽"
        print(f'{name}\n  до ({nb} дн.):   {f(b, nb)}\n  тест ({nt} дн.): {f(t, nt)}')
    print('По позициям (тест):')
    for k, n in TEST.items():
        b, nb = agg([k], *BASE); t, nt = agg([k], START, last) if today >= START else (dict.fromkeys(M, 0), 0)
        cpc = f"{t['allSpending'] / t['contacts']:.0f} ₽" if t['contacts'] else '—'
        print(f"  №{k} {n}: до {b['views'] / nb:.1f} просм/день, тест {t['views'] / max(nt, 1):.1f} просм/день; "
              f"контакты {b['contacts']}→{t['contacts']}; расход в тесте {t['allSpending']:.0f} ₽; цена контакта {cpc}")


if __name__ == '__main__':
    main()
