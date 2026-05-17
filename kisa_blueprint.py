"""KISA 보안 권고 페이지 (/kisa) — 한국 사이버 보안 동향."""
import json
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, jsonify, request, current_app
from database import get_cve_db

kisa_bp = Blueprint('kisa', __name__)

KST = timezone(timedelta(hours=9))

CATEGORY_LABELS = {
    'sec_notice': '보안공지',
    'vuln_info': '취약점',
    'notice': '공지사항',
}


@kisa_bp.route('/kisa', strict_slashes=False)
def kisa_page():
    return render_template('kisa.html')


@kisa_bp.route('/api/kisa.json')
def kisa_json():
    category = request.args.get('category', 'all')
    limit = min(int(request.args.get('limit', 50)), 200)

    where = "1=1"
    params: list = []
    if category != 'all' and category in CATEGORY_LABELS:
        where = "category=%s"
        params.append(category)

    conn = get_cve_db()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT id, category, bbs_id, source_id, title, link,
                       posted_at, views, ai_summary, ai_priority,
                       cve_ids, affected_software, audio_path, audio_published_at, created_at
                FROM kisa_advisories
                WHERE {where}
                ORDER BY posted_at DESC, id DESC
                LIMIT %s
            """, (*params, limit))
            rows = cur.fetchall()

            for r in rows:
                if r.get('posted_at'):
                    r['posted_at'] = r['posted_at'].strftime('%Y-%m-%d')
                if r.get('created_at'):
                    r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M')
                if r.get('audio_published_at'):
                    r['audio_published_at'] = r['audio_published_at'].strftime('%Y-%m-%d %H:%M')
                ap = r.get('audio_path')
                if ap:
                    if ap.startswith('/root/flask-app/'):
                        ap = '/' + ap[len('/root/flask-app/'):]
                    elif not ap.startswith('/'):
                        ap = '/static/' + ap
                    r['audio_path'] = ap
                r['category_label'] = CATEGORY_LABELS.get(r['category'], r['category'])
                # JSON 필드 파싱
                for jk in ('cve_ids', 'affected_software'):
                    v = r.get(jk)
                    if isinstance(v, str):
                        try:
                            r[jk] = json.loads(v)
                        except (json.JSONDecodeError, TypeError):
                            r[jk] = []
                    elif v is None:
                        r[jk] = []

            # 사이버 위기 단계
            cur.execute("SELECT level, observed_at FROM kisa_cyber_status ORDER BY id DESC LIMIT 1")
            cs = cur.fetchone()
            cyber_status = None
            if cs:
                cyber_status = {
                    'level': cs['level'],
                    'observed_at': cs['observed_at'].strftime('%Y-%m-%d %H:%M') if cs['observed_at'] else None,
                }

            # 카테고리별 카운트
            cur.execute("SELECT category, COUNT(*) AS n FROM kisa_advisories GROUP BY category")
            counts = {row['category']: int(row['n']) for row in cur.fetchall()}
    finally:
        conn.close()

    return jsonify({
        'advisories': rows,
        'cyber_status': cyber_status,
        'counts': counts,
        '_now': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S'),
    })


@kisa_bp.route('/api/kisa/<int:adv_id>')
def kisa_detail(adv_id: int):
    conn = get_cve_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, category, title, content, link, posted_at, views,
                       ai_summary, ai_priority, ai_categories
                FROM kisa_advisories WHERE id=%s
            """, (adv_id,))
            r = cur.fetchone()
            if not r:
                return jsonify({'error': 'not found'}), 404
            if r.get('posted_at'):
                r['posted_at'] = r['posted_at'].strftime('%Y-%m-%d')
            r['category_label'] = CATEGORY_LABELS.get(r['category'], r['category'])
    finally:
        conn.close()
    return jsonify(r)
