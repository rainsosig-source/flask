"""금시세·환율 — 국제 금(USD/oz) / 국내 금(원/g·돈) / USD-KRW + 비교(프리미엄). (/gold)
데이터: 국제 금·환율 = Yahoo Finance, 국내 금 = 네이버 marketindex. 5분 캐시. 외부 의존성 없음(urllib)."""
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, jsonify, request

gold_bp = Blueprint('gold', __name__)

OZ_G = 31.1034768          # 1 트로이온스 = 31.1034768 g
DON_G = 3.75               # 1돈 = 3.75 g
KST = timezone(timedelta(hours=9))
_UA = {'User-Agent': 'Mozilla/5.0'}
_cache = {}                # {range: (ts, payload)}
_CACHE_TTL = 300

# range 토글 → (Yahoo interval, Yahoo range, 국내 일수 슬라이스)
_RANGE = {
    '1d':  ('5m',  '1d',   1),
    '1w':  ('60m', '5d',   7),
    '1mo': ('1d',  '1mo',  31),
    '1y':  ('1d',  '1y',   366),
}


def _get_json(url, timeout=12):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _num(s):
    try:
        return float(str(s).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _yahoo_series(symbol, interval, rng):
    """[(ms_epoch, close), ...]"""
    url = (f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
           f'?interval={interval}&range={rng}')
    d = _get_json(url)
    r = d['chart']['result'][0]
    ts = r.get('timestamp') or []
    closes = r['indicators']['quote'][0].get('close') or []
    out = [(t * 1000, c) for t, c in zip(ts, closes) if c is not None]
    meta = r.get('meta', {})
    return out, meta


def _naver_domestic(days):
    """국내 금 일별 종가 [(ms_epoch, krw_per_g), ...] (최근 days일)."""
    url = 'https://api.stock.naver.com/marketindex/metals/M04020000/prices?range=year'
    arr = _get_json(url)
    out = []
    for it in arr:
        v = _num(it.get('closePrice'))
        t = it.get('localTradedAt', '')[:10]
        if v is None or not t:
            continue
        ms = int(datetime.strptime(t, '%Y-%m-%d').replace(tzinfo=KST).timestamp() * 1000)
        out.append((ms, v))
    out.sort()
    return out[-days:] if days < len(out) else out


def _naver_current():
    """네이버 metals 현재가: 국제 금(USD/oz)·국내 금(원/g) + 등락."""
    url = 'https://m.stock.naver.com/front-api/marketIndex/metals?category=metals'
    d = _get_json(url)['result']['mainList']
    by = {x.get('name'): x for x in d}
    def pack(x):
        if not x:
            return None
        return {'price': _num(x.get('closePrice')),
                'change': _num(x.get('fluctuations')),
                'ratio': _num(x.get('fluctuationsRatio')),
                'dir': (x.get('fluctuationsType') or {}).get('name', '')}
    return pack(by.get('국제 금')), pack(by.get('국내 금'))


def _build(rng):
    interval, yrange, days = _RANGE.get(rng, _RANGE['1mo'])
    intl_series, intl_meta = _yahoo_series('GC=F', interval, yrange)   # USD/oz
    fx_series, fx_meta = _yahoo_series('KRW=X', interval, yrange)      # USD/KRW
    dom_series = _naver_domestic(days)                                 # 원/g

    # 비교 차트(원/g): 국제 환산가 일별 = USD/oz × 환율 ÷ 31.1035. 날짜별로 가까운 환율 매칭.
    fx_by_day = {}
    for ms, v in fx_series:
        fx_by_day[datetime.fromtimestamp(ms / 1000, KST).strftime('%Y-%m-%d')] = v
    last_fx = fx_meta.get('regularMarketPrice') or (fx_series[-1][1] if fx_series else None)
    intl_krw_g = []
    for ms, usd_oz in intl_series:
        day = datetime.fromtimestamp(ms / 1000, KST).strftime('%Y-%m-%d')
        fx = fx_by_day.get(day, last_fx)
        if fx:
            intl_krw_g.append((ms, usd_oz * fx / OZ_G))

    # 현재가 카드
    intl_cur, dom_cur = _naver_current()
    fx_now = fx_meta.get('regularMarketPrice')
    fx_prev = fx_meta.get('chartPreviousClose')
    fx_chg = (fx_now - fx_prev) if (fx_now and fx_prev) else None
    intl_conv_krw_g = (intl_cur['price'] * fx_now / OZ_G) if (intl_cur and intl_cur.get('price') and fx_now) else None
    premium = None
    if intl_conv_krw_g and dom_cur and dom_cur.get('price'):
        premium = (dom_cur['price'] - intl_conv_krw_g) / intl_conv_krw_g * 100

    dom_g = dom_cur['price'] if dom_cur else None
    return {
        'range': rng,
        'updated': datetime.now(KST).strftime('%Y-%m-%d %H:%M'),
        'current': {
            'intl_usd_oz': intl_cur,
            'fx_usdkrw': {'price': fx_now, 'change': fx_chg,
                          'ratio': (fx_chg / fx_prev * 100) if (fx_chg and fx_prev) else None},
            'domestic': {'krw_g': dom_g, 'krw_don': (dom_g * DON_G) if dom_g else None,
                         'change': dom_cur['change'] if dom_cur else None,
                         'ratio': dom_cur['ratio'] if dom_cur else None,
                         'dir': dom_cur['dir'] if dom_cur else ''},
            'intl_conv_krw_g': intl_conv_krw_g,
            'intl_conv_krw_don': (intl_conv_krw_g * DON_G) if intl_conv_krw_g else None,
            'premium_pct': premium,
        },
        'chart': {
            'intl_usd_oz': [[t, round(v, 2)] for t, v in intl_series],
            'fx_usdkrw':   [[t, round(v, 2)] for t, v in fx_series],
            'intl_krw_g':  [[t, round(v)] for t, v in intl_krw_g],
            'domestic_krw_g': [[t, round(v)] for t, v in dom_series],
        },
    }


@gold_bp.route('/gold', strict_slashes=False)
def gold_page():
    return render_template('gold.html')


@gold_bp.route('/api/gold.json')
def gold_api():
    rng = request.args.get('range', '1mo')
    if rng not in _RANGE:
        rng = '1mo'
    now = time.time()
    if rng in _cache and now - _cache[rng][0] < _CACHE_TTL:
        return jsonify(_cache[rng][1])
    try:
        payload = _build(rng)
    except Exception as e:
        return jsonify({'error': str(e)[:200]}), 502
    _cache[rng] = (now, payload)
    return jsonify(payload)
