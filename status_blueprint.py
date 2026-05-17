"""sosig.shop 운영 상태 대시보드 (/status).

자체 DB(podcast, cve_monitor)에서 영상·팟캐스트·CVE 메트릭 조회 + GB10이
HMAC 서명으로 push한 시스템 메트릭(system_status 테이블)을 합쳐서 JSON 제공.
"""
import os
import json
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, jsonify, request, current_app
from database import get_db_connection, get_cve_db

status_bp = Blueprint('status', __name__)

KST = timezone(timedelta(hours=9))
SOSIG_API_TOKEN = os.environ.get('SOSIG_API_TOKEN', '')


def _verify_signature(body: bytes, signature: str) -> bool:
    if not SOSIG_API_TOKEN or not signature:
        return False
    expected = hmac.new(SOSIG_API_TOKEN.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _fmt(dt) -> str | None:
    return dt.strftime('%Y-%m-%d %H:%M') if dt else None


@status_bp.route('/status', strict_slashes=False)
def status_page():
    return render_template('status.html')


@status_bp.route('/api/status.json')
def status_json():
    metrics: dict = {}

    # podcast DB: news_videos / episodes / system_status
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS today_count, MAX(created_at) AS last
                FROM news_videos WHERE status='approved' AND DATE(created_at)=CURDATE()
            """)
            r = cur.fetchone()
            metrics['news_videos'] = {
                'today': int(r['today_count'] or 0),
                'last': _fmt(r['last']),
            }

            cur.execute("""
                SELECT COUNT(*) AS today_count, MAX(created_at) AS last
                FROM episodes WHERE DATE(created_at)=CURDATE()
            """)
            r = cur.fetchone()
            metrics['podcast'] = {
                'today': int(r['today_count'] or 0),
                'last': _fmt(r['last']),
            }

            # GB10이 push한 데이터
            cur.execute("SELECT source, data, updated_at FROM system_status")
            for row in cur.fetchall():
                src = row['source']
                data = row['data']
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except (json.JSONDecodeError, TypeError):
                        data = {}
                metrics[src] = {**(data or {}), 'updated_at': _fmt(row['updated_at'])}
    finally:
        conn.close()

    # CVE DB (vuln + KISA)
    try:
        cve_conn = get_cve_db()
        try:
            with cve_conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        SUM(CASE WHEN ai_status='done' THEN 1 ELSE 0 END) AS analyzed,
                        SUM(CASE WHEN ai_status='pending' THEN 1 ELSE 0 END) AS pending,
                        MAX(ai_status_updated_at) AS last
                    FROM cve_entries
                """)
                r = cur.fetchone()
                metrics['vuln'] = {
                    'analyzed': int(r['analyzed'] or 0),
                    'pending': int(r['pending'] or 0),
                    'last': _fmt(r['last']),
                }

                # KISA 권고 통계 + 사이버 위기 단계
                cur.execute("""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN posted_at > CURDATE() - INTERVAL 7 DAY THEN 1 ELSE 0 END) AS recent_7d,
                        SUM(CASE WHEN ai_priority >= 4 AND posted_at > CURDATE() - INTERVAL 30 DAY THEN 1 ELSE 0 END) AS urgent_30d,
                        SUM(CASE WHEN audio_status='done' AND audio_published_at > NOW() - INTERVAL 7 DAY THEN 1 ELSE 0 END) AS audio_7d,
                        SUM(CASE WHEN audio_status='pending' THEN 1 ELSE 0 END) AS audio_pending,
                        SUM(CASE WHEN audio_status='done' THEN 1 ELSE 0 END) AS audio_total
                    FROM kisa_advisories
                """)
                k = cur.fetchone()
                cur.execute("SELECT level, observed_at FROM kisa_cyber_status ORDER BY id DESC LIMIT 1")
                cs = cur.fetchone()
                metrics['kisa'] = {
                    'total': int(k['total'] or 0),
                    'recent_7d': int(k['recent_7d'] or 0),
                    'urgent_30d': int(k['urgent_30d'] or 0),
                    'audio_7d': int(k['audio_7d'] or 0),
                    'audio_pending': int(k['audio_pending'] or 0),
                    'audio_total': int(k['audio_total'] or 0),
                    'cyber_level': cs['level'] if cs else None,
                    'last_check': _fmt(cs['observed_at']) if cs else None,
                }
        finally:
            cve_conn.close()
    except Exception as e:
        current_app.logger.error(f'status vuln/kisa error: {e}')
        metrics['vuln'] = None

    metrics['_now'] = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
    return jsonify(metrics)


@status_bp.route('/api/status/update', methods=['POST'])
def status_update():
    body = request.get_data()
    if not _verify_signature(body, request.headers.get('X-Signature', '')):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return jsonify({'ok': False, 'error': 'invalid json'}), 400

    source = payload.get('source')
    data = payload.get('data')
    if not source or data is None:
        return jsonify({'ok': False, 'error': 'missing fields'}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO system_status (source, data) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE data=VALUES(data)",
                (source, json.dumps(data)),
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({'ok': True})
