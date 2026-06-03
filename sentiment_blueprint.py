"""뉴스 감성 대시보드 — 섹터별 감성 추이·볼륨, 종목 랭킹, 최근 기사. (/sentiment)
데이터: news_sentiment(로컬 Gemma 태깅) JOIN episodes. 예측 아님 — 뉴스 분위기 집계."""
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, jsonify, request, abort, redirect
from database import get_db_connection

sentiment_bp = Blueprint('sentiment', __name__)
KST = timezone(timedelta(hours=9))
_SCORE = {'호재': 1, '악재': -1, '중립': 0}


def _rows(days):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT e.title, e.link, e.press, e.created_at, "
                "s.sentiment, s.score, s.sectors, s.tickers, s.event, s.keywords "
                "FROM news_sentiment s JOIN episodes e ON e.id = s.episode_id "
                "WHERE e.created_at >= (NOW() - INTERVAL %s DAY) "
                "ORDER BY e.created_at DESC", (days,))
            return cur.fetchall()
    finally:
        conn.close()


def _r(row, k, d=None):
    return row.get(k, d) if isinstance(row, dict) else d


@sentiment_bp.route('/api/sentiment.json')
def sentiment_api():
    try:
        days = max(1, min(60, int(request.args.get('days', 7))))
    except ValueError:
        days = 7
    rows = _rows(days)

    overall = {'total': len(rows), '호재': 0, '악재': 0, '중립': 0, 'score_sum': 0.0}
    sec_cnt = defaultdict(lambda: {'count': 0, 'pos': 0, 'neg': 0, 'score_sum': 0.0})
    tic_cnt = defaultdict(lambda: {'count': 0, 'pos': 0, 'neg': 0, 'score_sum': 0.0})
    kw_cnt = defaultdict(lambda: {'count': 0, 'pos': 0, 'neg': 0, 'score_sum': 0.0})
    sec_day = defaultdict(lambda: defaultdict(list))  # sector -> day -> [score]
    days_set = set()
    recent = []

    for row in rows:
        sent = _r(row, 'sentiment', '중립') or '중립'
        try:
            score = float(_r(row, 'score', 0) or 0)
        except (TypeError, ValueError):
            score = 0.0
        secs = [x for x in (_r(row, 'sectors', '') or '').split(',') if x]
        tics = [x for x in (_r(row, 'tickers', '') or '').split(',') if x]
        kws = [x.strip() for x in (_r(row, 'keywords', '') or '').split(',') if x.strip()]
        ca = _r(row, 'created_at')
        day = ca.strftime('%Y-%m-%d') if hasattr(ca, 'strftime') else str(ca)[:10]
        days_set.add(day)

        overall[sent] = overall.get(sent, 0) + 1
        overall['score_sum'] += score
        for sec in secs:
            d = sec_cnt[sec]; d['count'] += 1; d['score_sum'] += score
            d['pos'] += sent == '호재'; d['neg'] += sent == '악재'
            sec_day[sec][day].append(score)
        for tic in tics:
            d = tic_cnt[tic]; d['count'] += 1; d['score_sum'] += score
            d['pos'] += sent == '호재'; d['neg'] += sent == '악재'
        for kw in kws:
            d = kw_cnt[kw]; d['count'] += 1; d['score_sum'] += score
            d['pos'] += sent == '호재'; d['neg'] += sent == '악재'

        if len(recent) < 40:
            recent.append({'title': _r(row, 'title', ''), 'link': _r(row, 'link', ''),
                           'press': _r(row, 'press', ''),
                           'date': ca.strftime('%m-%d %H:%M') if hasattr(ca, 'strftime') else str(ca)[:16],
                           'sentiment': sent, 'score': round(score, 2),
                           'sectors': secs, 'event': _r(row, 'event', '')})

    sectors = sorted(
        [{'sector': k, 'count': v['count'], 'pos': v['pos'], 'neg': v['neg'],
          'avg': round(v['score_sum'] / v['count'], 3) if v['count'] else 0} for k, v in sec_cnt.items()],
        key=lambda x: -x['count'])
    tickers = sorted(
        [{'ticker': k, 'count': v['count'], 'pos': v['pos'], 'neg': v['neg'],
          'avg': round(v['score_sum'] / v['count'], 3) if v['count'] else 0} for k, v in tic_cnt.items()
         if v['count'] >= 2],
        key=lambda x: -x['count'])[:15]

    # 키워드 집계 — 워드클라우드(빈도·평균감성)와 호재/악재 키워드 비교
    kw_all = [{'word': k, 'count': v['count'], 'pos': v['pos'], 'neg': v['neg'],
               'avg': round(v['score_sum'] / v['count'], 3) if v['count'] else 0}
              for k, v in kw_cnt.items() if v['count'] >= 2]
    wordcloud = sorted(kw_all, key=lambda x: -x['count'])[:80]
    kw_pos = sorted([k for k in kw_all if k['pos'] > k['neg']],
                    key=lambda x: (-x['pos'], -x['avg']))[:12]
    kw_neg = sorted([k for k in kw_all if k['neg'] > k['pos']],
                    key=lambda x: (-x['neg'], x['avg']))[:12]

    labels = sorted(days_set)
    top_secs = [s['sector'] for s in sectors[:6]]
    trend = {}
    for sec in top_secs:
        trend[sec] = [round(sum(sec_day[sec][d]) / len(sec_day[sec][d]), 3)
                      if len(sec_day[sec].get(d, [])) >= 3 else None
                      for d in labels]

    return jsonify({
        'updated': datetime.now(KST).strftime('%Y-%m-%d %H:%M'),
        'days': days,
        'overall': {'total': overall['total'], '호재': overall['호재'], '악재': overall['악재'],
                    '중립': overall['중립'],
                    'avg': round(overall['score_sum'] / overall['total'], 3) if overall['total'] else 0},
        'sectors': sectors,
        'tickers': tickers,
        'trend': {'labels': labels, 'series': trend},
        'wordcloud': wordcloud,
        'keyword_compare': {'pos': kw_pos, 'neg': kw_neg},
        'recent': recent,
    })


# 추적 종목 레지스트리 (slug → 표시명·야후심볼). 외신 검색어는 수집기 stock_collector.FOREIGN_QUERY.
STOCKS = {
    'samsung': {'name': '삼성전자', 'symbol': '005930.KS'},
    'skhynix': {'name': 'SK하이닉스', 'symbol': '000660.KS'},
}


def _stock_rows(stock, days):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title, title_ko, link, press, source, sentiment, score, keywords, event, "
                "COALESCE(posted_at, collected_at) AS dt "
                "FROM stock_sentiment WHERE stock=%s "
                "AND collected_at >= (NOW() - INTERVAL %s DAY) ORDER BY dt DESC",
                (stock, days))
            return cur.fetchall()
    finally:
        conn.close()


import urllib.request as _ur
import json as _json
import time as _time
_PRICE_CACHE = {}


def _stock_price(symbol, rng="3mo"):
    now = _time.time()
    ck = (symbol, rng)
    c = _PRICE_CACHE.get(ck)
    if c and now - c[0] < 3600:
        return c[1]
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=%s"
               % (symbol, rng))
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(req, timeout=12) as r:
            d = _json.loads(r.read().decode("utf-8"))
        res = d["chart"]["result"][0]
        ts = res.get("timestamp") or []
        closes = res["indicators"]["quote"][0].get("close") or []
        out = [{"date": datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"), "close": round(cl, 1)}
               for t, cl in zip(ts, closes) if cl is not None]
        _PRICE_CACHE[ck] = (now, out)
        return out
    except Exception:
        return c[1] if c else []


def _stock_daily(name, days):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DATE(collected_at) d, ROUND(AVG(score),3) s, "
                "SUM(sentiment='호재') p, SUM(sentiment='악재') n, COUNT(*) c "
                "FROM stock_sentiment WHERE stock=%s AND collected_at >= (NOW() - INTERVAL %s DAY) "
                "GROUP BY DATE(collected_at) ORDER BY d", (name, days))
            return [{"date": str(_r(row, 'd')), "score": float(_r(row, 's') or 0),
                     "pos": int(_r(row, 'p') or 0), "neg": int(_r(row, 'n') or 0),
                     "count": int(_r(row, 'c') or 0)}
                    for row in cur.fetchall()]
    finally:
        conn.close()


@sentiment_bp.route('/api/sentiment/stock/<slug>.json')
def stock_api(slug):
    meta = STOCKS.get(slug)
    if not meta:
        abort(404)
    name, symbol = meta['name'], meta['symbol']
    days = request.args.get('days', 120, type=int)
    limit = request.args.get('limit', 40, type=int)
    rows = _stock_rows(name, days)
    counts = {'호재': 0, '악재': 0, '중립': 0}
    src = {'domestic': {'호재': 0, '악재': 0, '중립': 0},
           'foreign': {'호재': 0, '악재': 0, '중립': 0}}
    daily = defaultdict(lambda: {'호재': 0, '악재': 0})
    pos, neg = [], []
    kw_pos, kw_neg = {}, {}
    for row in rows:
        s = _r(row, 'sentiment', '중립') or '중립'
        counts[s] = counts.get(s, 0) + 1
        so = _r(row, 'source', 'domestic') or 'domestic'
        src.setdefault(so, {'호재': 0, '악재': 0, '중립': 0})
        src[so][s] = src[so].get(s, 0) + 1
        dt = _r(row, 'dt')
        dstr = dt.strftime('%Y-%m-%d') if dt else ''
        item = {
            'title': _r(row, 'title', ''),
            'title_ko': _r(row, 'title_ko', '') or '',
            'link': _r(row, 'link', ''),
            'press': _r(row, 'press', ''),
            'source': so,
            'date': dstr,
            'score': round(float(_r(row, 'score', 0) or 0), 2),
            'keywords': [k for k in (_r(row, 'keywords', '') or '').split(',') if k][:6],
            'event': _r(row, 'event', '') or '',
        }
        for _k in item['keywords']:
            if s == '호재': kw_pos[_k] = kw_pos.get(_k, 0) + 1
            elif s == '악재': kw_neg[_k] = kw_neg.get(_k, 0) + 1
        if s == '호재':
            daily[dstr]['호재'] += 1
            if len(pos) < limit:
                pos.append(item)
        elif s == '악재':
            daily[dstr]['악재'] += 1
            if len(neg) < limit:
                neg.append(item)
    total = sum(counts.values())
    pn = counts['호재'] + counts['악재']
    top_pos = [{'word': w, 'count': c} for w, c in sorted(kw_pos.items(), key=lambda x: -x[1])[:12]]
    top_neg = [{'word': w, 'count': c} for w, c in sorted(kw_neg.items(), key=lambda x: -x[1])[:12]]
    sd = _stock_daily(name, days)
    _prev = None
    for _e in sd:
        _e['event'] = bool(_prev is not None and abs(_e['score'] - _prev) >= 0.3)
        _prev = _e['score']
    from datetime import date as _date
    _pts = [(_date.fromisoformat(_e['date']), _e['score']) for _e in sd if _e['date']]
    for _e in sd:
        if not _e['date']:
            _e['ma'] = None; continue
        _d0 = _date.fromisoformat(_e['date'])
        _win = [sc for dd, sc in _pts if 0 <= (_d0 - dd).days <= 6]
        _e['ma'] = round(sum(_win) / len(_win), 3) if _win else None
    trend = [{'date': d[5:], '호재': v['호재'], '악재': v['악재']}
             for d, v in sorted(daily.items()) if d][-30:]
    return jsonify({
        'stock': name, 'slug': slug, 'days': days, 'total': total,
        'counts': counts, 'by_source': src,
        'pos_ratio': round(counts['호재'] / pn, 3) if pn else 0,
        'trend': trend, 'positive': pos, 'negative': neg,
        'price': _stock_price(symbol), 'sentiment_daily': sd,
        'kw_pos': top_pos, 'kw_neg': top_neg,
    })


@sentiment_bp.route('/api/sentiment/samsung.json')  # 하위호환
def samsung_api():
    return stock_api('samsung')


@sentiment_bp.route('/sentiment/stock/<slug>', strict_slashes=False)
def stock_page(slug):
    meta = STOCKS.get(slug)
    if not meta:
        abort(404)
    stocks = [{'slug': k, 'name': v['name']} for k, v in STOCKS.items()]
    return render_template('stock_sentiment.html', slug=slug, stock_name=meta['name'], stocks=stocks)


@sentiment_bp.route('/sentiment/samsung', strict_slashes=False)  # 하위호환 → 통일 경로로 301
def samsung_page():
    return redirect('/sentiment/stock/samsung', code=301)


@sentiment_bp.route('/sentiment', strict_slashes=False)
def sentiment_page():
    stocks = [{'slug': k, 'name': v['name']} for k, v in STOCKS.items()]
    return render_template('sentiment.html', stocks=stocks)
