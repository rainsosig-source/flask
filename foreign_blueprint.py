"""외신이 본 한국 — BBC/Nikkei 미커버 기사 팟캐스트 (/foreign)."""
from datetime import datetime, timezone, timedelta
import json
from flask import Blueprint, render_template, jsonify, request
from database import get_db_connection

foreign_bp = Blueprint('foreign', __name__)

KST = timezone(timedelta(hours=9))
SITE_BASE = 'https://sosig.shop'

SOURCE_LABELS = {
    'bbc':    'BBC News',
    'nikkei': 'Nikkei Asia',
}


def _audio_url(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith('/root/flask-app/'):
        return '/' + path[len('/root/flask-app/'):]
    if not path.startswith('/'):
        return '/static/' + path
    return path


def _abs_audio(path):
    rel = _audio_url(path)
    if rel and rel.startswith('/'):
        return f"{SITE_BASE}{rel}"
    return rel


def _build_foreign_jsonld():
    """발행된 외신 기사 NewsArticle + PodcastEpisode (associatedMedia로 한국어 팟캐스트 연결)."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, source, original_url, original_title, original_summary,
                       posted_at, audio_path, audio_published_at
                FROM foreign_news WHERE ai_status='done'
                ORDER BY audio_published_at DESC LIMIT 50
            """)
            rows = list(cur.fetchall())
    finally:
        conn.close()

    items = []
    for r in rows:
        published = r.get('audio_published_at') or r.get('posted_at')
        au = _abs_audio(r.get('audio_path'))
        item = {
            "@type": "NewsArticle",
            "headline": r.get('original_title') or '',
            "url": r.get('original_url'),
            "datePublished": published.strftime('%Y-%m-%dT%H:%M:%S+09:00') if published else None,
            "publisher": {"@type": "Organization", "name": SOURCE_LABELS.get(r['source'], r['source'])},
            "inLanguage": "ko",
            "isAccessibleForFree": True,
        }
        if r.get('original_summary'):
            item["description"] = r['original_summary'][:280]
        if au:
            item["audio"] = {"@type": "AudioObject", "contentUrl": au, "encodingFormat": "audio/mpeg"}
        items.append({k: v for k, v in item.items() if v is not None})

    collection = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "외신이 본 한국",
        "description": "BBC·Nikkei Asia 등 해외 매체가 보도한 한국 관련 기사 중 국내 언론이 거의 다루지 않은 것을 큐레이션하여 한국어 팟캐스트로 발행.",
        "url": f"{SITE_BASE}/foreign",
        "inLanguage": "ko",
        "publisher": {"@type": "Organization", "name": "sosig.shop", "url": SITE_BASE},
        "hasPart": items,
    }
    return json.dumps(collection, ensure_ascii=False, separators=(',', ':'))


@foreign_bp.route('/foreign', strict_slashes=False)
def foreign_page():
    return render_template('foreign.html', foreign_jsonld=_build_foreign_jsonld())


@foreign_bp.route('/api/foreign.json')
def foreign_json():
    limit = min(int(request.args.get('limit', 50)), 200)
    source = request.args.get('source', 'all')
    date = request.args.get('date')   # "YYYY-MM-DD" 발행일 필터 (선택)

    where = "ai_status='done'"
    params: list = []
    if source != 'all' and source in SOURCE_LABELS:
        where += " AND source=%s"
        params.append(source)
    if date:
        where += " AND DATE(audio_published_at)=%s"
        params.append(date)
    params.append(limit)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT id, source, original_url, original_title, original_summary,
                       posted_at, audio_path, audio_published_at, naver_match_count,
                       published_episode_id, created_at
                FROM foreign_news
                WHERE {where}
                ORDER BY audio_published_at DESC
                LIMIT %s
            """, params)
            rows = list(cur.fetchall())

            for r in rows:
                for ts_field in ('posted_at', 'audio_published_at', 'created_at'):
                    v = r.get(ts_field)
                    if v:
                        r[ts_field] = v.strftime('%Y-%m-%d %H:%M')
                r['audio_url'] = _audio_url(r.get('audio_path'))
                r['source_label'] = SOURCE_LABELS.get(r['source'], r['source'])

            # 소스별 카운트
            cur.execute("""
                SELECT source, COUNT(*) AS n
                FROM foreign_news WHERE ai_status='done'
                GROUP BY source
            """)
            counts = {row['source']: int(row['n']) for row in cur.fetchall()}

            # 최근 7일 발행 수 (메인 카드용)
            cur.execute("""
                SELECT COUNT(*) AS n FROM foreign_news
                WHERE ai_status='done'
                  AND audio_published_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            """)
            recent7 = int((cur.fetchone() or {}).get('n', 0))

            # 발행된 날짜 목록 (날짜 칩용) — source 필터 반영
            date_where = "ai_status='done' AND audio_published_at IS NOT NULL"
            date_params = []
            if source != 'all' and source in SOURCE_LABELS:
                date_where += " AND source=%s"
                date_params.append(source)
            cur.execute(f"""
                SELECT DATE(audio_published_at) AS d, COUNT(*) AS n
                FROM foreign_news WHERE {date_where}
                GROUP BY DATE(audio_published_at)
                ORDER BY d DESC LIMIT 60
            """, date_params)
            dates = [{'date': row['d'].strftime('%Y-%m-%d'), 'n': int(row['n'])}
                     for row in cur.fetchall() if row['d']]
    finally:
        conn.close()

    return jsonify({
        'articles': rows,
        'counts': counts,
        'recent7': recent7,
        'dates': dates,
        '_now': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S'),
    })


@foreign_bp.route('/api/foreign/<int:article_id>')
def foreign_detail(article_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, source, original_url, original_title, original_summary,
                       original_content, ai_translated_dialogue, ai_translated_article,
                       ai_korean_keywords, naver_match_count,
                       posted_at, audio_path, audio_published_at
                FROM foreign_news WHERE id=%s AND ai_status='done'
            """, (article_id,))
            r = cur.fetchone()
            if not r:
                return jsonify({'error': 'not found'}), 404
            for ts_field in ('posted_at', 'audio_published_at'):
                v = r.get(ts_field)
                if v:
                    r[ts_field] = v.strftime('%Y-%m-%d %H:%M')
            r['audio_url'] = _audio_url(r.get('audio_path'))
            r['source_label'] = SOURCE_LABELS.get(r['source'], r['source'])
    finally:
        conn.close()
    return jsonify(r)


@foreign_bp.route('/api/foreign/stats')
def foreign_stats():
    """메인 카드용 최근 7일 발행 수."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS n FROM foreign_news
                WHERE ai_status='done'
                  AND audio_published_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            """)
            n = int((cur.fetchone() or {}).get('n', 0))
    finally:
        conn.close()
    return jsonify({'recent7': n})
