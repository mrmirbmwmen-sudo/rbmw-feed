"""Показатели, из которых складывается «Уровень сервиса» Авито (сам уровень через API не отдаётся):
скорость ответа в чатах, неотвеченные покупатели, рейтинг и отзывы (новые и без ответа продавца).
Запуск: python3 service.py [дней=7]. Пишет out/service.json и печатает сводку; строки «ВНИМАНИЕ» — для оповещения."""
import datetime, json, os, statistics, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analytics as a

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = (9, 21)          # рабочие часы МСК: ответы вне их считаем отдельно


def msk(ts): return datetime.datetime.fromtimestamp(ts, a.MSK)


def chats(u, since):
    out, off = [], 0
    while True:
        r = a.req(f'/messenger/v2/accounts/{u}/chats?limit=100&offset={off}').get('chats', [])
        out += [c for c in r if c.get('updated', 0) >= since]
        if len(r) < 100 or (r and r[-1].get('updated', 0) < since): break
        off += 100
    return out


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    u = a.uid(); now = time.time(); since = now - days * 86400
    waits, waits_work, unanswered, late = [], [], [], []
    mine_items = {x['id'] for x in a.items_all()}          # только чаты по НАШИМ объявлениям (не те, где мы покупатели)
    for c in chats(u, since):
        v = ((c.get('context') or {}).get('value') or {})
        if v.get('id') not in mine_items and v.get('user_id') != u: continue
        try: ms = a.req(f"/messenger/v3/accounts/{u}/chats/{c['id']}/messages/?limit=100").get('messages', [])
        except Exception: continue
        ms = sorted([m for m in ms if m.get('type') in ('text', 'image', 'voice', 'file', 'item', 'link', 'location', 'video')], key=lambda m: m['created'])
        title = (((c.get('context') or {}).get('value') or {}).get('title') or '')[:45]
        pending = None
        for m in ms:
            mine = m.get('author_id') == u or m.get('direction') == 'out'
            if not mine and m.get('author_id', 0) != 0:
                if pending is None: pending = m['created']
            elif mine and pending is not None:
                w = (m['created'] - pending) / 60
                if pending >= since:
                    waits.append(w)
                    if WORK[0] <= msk(pending).hour < WORK[1]: waits_work.append(w)
                    if w > 60 and WORK[0] <= msk(pending).hour < WORK[1]: late.append(f'{title} — {int(w // 60)} ч {int(w % 60)} мин ({msk(pending):%d.%m %H:%M})')
                pending = None
        if pending is not None and now - pending > 30 * 60:
            unanswered.append(f'{title} — ждёт {int((now - pending) // 3600)} ч {int((now - pending) % 3600 // 60)} мин')
        time.sleep(0.3)
    info = a.req('/ratings/v1/info').get('rating', {})
    rv = a.req('/ratings/v1/reviews?offset=0&limit=50').get('reviews', [])
    new_rv = [r for r in rv if r.get('createdAt', r.get('created', 0)) and r.get('createdAt', r.get('created', 0)) >= since]
    no_answer = [r for r in rv if r.get('canAnswer') and not r.get('answer')]
    p = lambda L, q: round(sorted(L)[min(len(L) - 1, int(len(L) * q))]) if L else None
    res = dict(days=days, replies=len(waits), median_min=round(statistics.median(waits)) if waits else None,
               median_work_min=round(statistics.median(waits_work)) if waits_work else None, p90_work_min=p(waits_work, .9),
               within15_work=round(100 * sum(w <= 15 for w in waits_work) / len(waits_work)) if waits_work else None,
               unanswered=unanswered, late=late, rating=info.get('score'), reviews=info.get('reviewsCount'),
               new_reviews=len(new_rv), reviews_without_answer=len(no_answer))
    os.makedirs(os.path.join(ROOT, 'out'), exist_ok=True)
    json.dump(res, open(os.path.join(ROOT, 'out', 'service.json'), 'w'), ensure_ascii=False, indent=1)
    print(f"Сервис за {days} дн.: ответов {res['replies']}, медиана ответа в рабочее время {res['median_work_min']} мин "
          f"(90% — до {res['p90_work_min']} мин), за 15 мин отвечено {res['within15_work']}%; рейтинг {res['rating']} ({res['reviews']} отзывов), "
          f"новых отзывов {res['new_reviews']}, без ответа продавца {res['reviews_without_answer']}")
    if res['within15_work'] is not None and res['within15_work'] < 80:
        print(f"ВНИМАНИЕ скорость ответа: в рабочее время за 15 минут отвечено только {res['within15_work']}% (цель ≥ 80%) — это снижает уровень сервиса и показы")
    if late: print(f'ВНИМАНИЕ долгие ответы (> 1 ч в рабочее время, {len(late)}): ' + '; '.join(late[:8]))
    if unanswered: print(f'ВНИМАНИЕ покупатели ждут ответа ({len(unanswered)}): ' + '; '.join(unanswered[:8]))
    if no_answer: print(f'Отзывы без ответа продавца: {len(no_answer)} — ответ на отзыв повышает доверие (можно отвечать в кабинете)')


if __name__ == '__main__':
    main()
