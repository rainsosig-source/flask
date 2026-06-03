# ==============================================================================
# 뉴스 영상 Blueprint — POST /api/news_videos, GET /news/videos, Telegram webhook
# ==============================================================================

import hashlib
import hmac
import json
import os
import re
import time
import urllib.request as _ur

from flask import Blueprint, current_app, jsonify, render_template, request, g
from database import get_db_connection

video_bp = Blueprint('video', __name__)

_SECRET = None


def _get_secret() -> str:
    global _SECRET
    if _SECRET is None:
        _SECRET = os.environ.get('SOSIG_VIDEO_API_SECRET', '')
    return _SECRET


def _verify_signature(body: bytes, header_sig: str) -> bool:
    secret = _get_secret()
    if not secret or not header_sig:
        return False
    expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_sig)


# ── API: DGX → 등록 ──────────────────────────────────────────────────────────

@video_bp.route('/api/news_videos', methods=['POST'])
def register_video():
    body = request.get_data()
    if not _verify_signature(body, request.headers.get('X-Signature', '')):
        current_app.logger.warning('news_videos: invalid signature')
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    try:
        data = json.loads(body.decode('utf-8'))
    except Exception:
        return jsonify({'ok': False, 'error': 'bad json'}), 400

    slug = data.get('slug', '').strip()
    ko_title = data.get('ko_title', '').strip()
    if not slug or not ko_title:
        return jsonify({'ok': False, 'error': 'slug and ko_title required'}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO news_videos
                   (slug, ko_title, en_title, reason, article_title, article_source, ko_path, en_path, kind)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                   ko_title=VALUES(ko_title), en_title=VALUES(en_title),
                   reason=VALUES(reason), article_title=VALUES(article_title),
                   article_source=VALUES(article_source),
                   ko_path=VALUES(ko_path), en_path=VALUES(en_path),
                   kind=VALUES(kind), updated_at=NOW()
                """,
                (
                    slug,
                    ko_title,
                    data.get('en_title', ''),
                    data.get('reason', ''),
                    data.get('article_title', ''),
                    data.get('article_source', ''),
                    data.get('ko_path', ''),
                    data.get('en_path', ''),
                    data.get('kind', 'news'),
                ),
            )
            conn.commit()
    except Exception as e:
        current_app.logger.error(f'news_videos register error: {e}')
        return jsonify({'ok': False, 'error': 'db error'}), 500
    finally:
        conn.close()

    current_app.logger.info(f'news_videos registered: {slug}')
    return jsonify({'ok': True, 'slug': slug}), 200


# ── API: Telegram bot → YouTube URL 추가 + 승인 ──────────────────────────────

@video_bp.route('/api/news_videos/approve', methods=['POST'])
def approve_video():
    body = request.get_data()
    if not _verify_signature(body, request.headers.get('X-Signature', '')):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    data = json.loads(body.decode('utf-8'))
    slug = data.get('slug', '').strip()
    youtube_url = data.get('youtube_url', '').strip()
    if not slug or not youtube_url:
        return jsonify({'ok': False, 'error': 'slug and youtube_url required'}), 400

    import re as _re
    def _vid(u):
        m = _re.search(r'(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})', u or '')
        return m.group(1) if m else (u or '').strip()
    new_vid = _vid(youtube_url)

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # 중복 가드: 같은 YouTube 영상이 다른 항목에 이미 매칭돼 있으면 거부 (?si= 등 무시, 영상 ID로 비교)
            cursor.execute(
                "SELECT slug, youtube_url FROM news_videos "
                "WHERE slug<>%s AND youtube_url IS NOT NULL AND youtube_url<>''",
                (slug,),
            )
            for _row in cursor.fetchall():
                _os = _row['slug'] if isinstance(_row, dict) else _row[0]
                _ou = _row['youtube_url'] if isinstance(_row, dict) else _row[1]
                if _vid(_ou) == new_vid:
                    return jsonify({'ok': False, 'error': f'youtube_url already used by: {_os}'}), 409
            cursor.execute(
                "UPDATE news_videos SET youtube_url=%s, status='approved', updated_at=NOW() WHERE slug=%s",
                (youtube_url, slug),
            )
            conn.commit()
            affected = cursor.rowcount
    except Exception as e:
        current_app.logger.error(f'news_videos approve error: {e}')
        return jsonify({'ok': False, 'error': 'db error'}), 500
    finally:
        conn.close()

    if affected == 0:
        return jsonify({'ok': False, 'error': 'slug not found'}), 404
    return jsonify({'ok': True}), 200


# ── API: Telegram bot → 거부 ─────────────────────────────────────────────────

@video_bp.route('/api/news_videos/reject', methods=['POST'])
def reject_video():
    body = request.get_data()
    if not _verify_signature(body, request.headers.get('X-Signature', '')):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    data = json.loads(body.decode('utf-8'))
    slug = data.get('slug', '').strip()
    if not slug:
        return jsonify({'ok': False, 'error': 'slug required'}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE news_videos SET status='rejected', updated_at=NOW() WHERE slug=%s",
                (slug,),
            )
            conn.commit()
            affected = cursor.rowcount
    except Exception as e:
        current_app.logger.error(f'news_videos reject error: {e}')
        return jsonify({'ok': False, 'error': 'db error'}), 500
    finally:
        conn.close()

    if affected == 0:
        return jsonify({'ok': False, 'error': 'slug not found'}), 404
    return jsonify({'ok': True}), 200


# ── API: DGX cleanup → NAS 삭제 대기 목록 ────────────────────────────────────

@video_bp.route('/api/news_videos/pending_cleanup', methods=['POST'])
def pending_cleanup():
    body = request.get_data()
    if not _verify_signature(body, request.headers.get('X-Signature', '')):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT slug, ko_path, en_path FROM news_videos "
                "WHERE status IN ('rejected','deleted') AND nas_cleaned=0"
            )
            rows = cursor.fetchall()
    except Exception as e:
        current_app.logger.error(f'pending_cleanup error: {e}')
        return jsonify({'ok': False, 'error': 'db error'}), 500
    finally:
        conn.close()

    return jsonify({'ok': True, 'items': rows}), 200


# ── API: DGX cleanup → 삭제 완료 확인 ────────────────────────────────────────

@video_bp.route('/api/news_videos/confirm_deleted', methods=['POST'])
def confirm_deleted():
    body = request.get_data()
    if not _verify_signature(body, request.headers.get('X-Signature', '')):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    data = json.loads(body.decode('utf-8'))
    slug = data.get('slug', '').strip()
    if not slug:
        return jsonify({'ok': False, 'error': 'slug required'}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE news_videos SET nas_cleaned=1, updated_at=NOW() WHERE slug=%s",
                (slug,),
            )
            conn.commit()
    except Exception as e:
        current_app.logger.error(f'confirm_deleted error: {e}')
        return jsonify({'ok': False, 'error': 'db error'}), 500
    finally:
        conn.close()

    return jsonify({'ok': True}), 200


# ── API: JSON 피드 ────────────────────────────────────────────────────────────

@video_bp.route('/api/news_videos.json')
def api_json():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT slug, ko_title, en_title, article_source, youtube_url, created_at "
                "FROM news_videos WHERE status='approved' AND kind='news' ORDER BY created_at DESC LIMIT 50"
            )
            rows = cursor.fetchall()
    except Exception as e:
        current_app.logger.error(f'news_videos json error: {e}')
        return jsonify({'error': 'db error'}), 500
    finally:
        conn.close()

    for r in rows:
        if r.get('created_at'):
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M')
    return jsonify(rows), 200


# ── Page: /news/videos ────────────────────────────────────────────────────────

@video_bp.route('/news/videos')
def news_videos_page():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT slug, ko_title, en_title, article_source, youtube_url, reason, created_at "
                "FROM news_videos WHERE status='approved' AND kind='news' ORDER BY created_at DESC"
            )
            videos = cursor.fetchall()
    except Exception as e:
        current_app.logger.error(f'news_videos page error: {e}')
        videos = []
    finally:
        conn.close()

    for v in videos:
        if v.get('created_at'):
            v['created_at_iso'] = v['created_at'].strftime('%Y-%m-%d')
            v['created_at'] = v['created_at'].strftime('%Y년 %m월 %d일')
        yt_url = v.get("youtube_url") or ""
        vid_id = ''
        m = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', yt_url)
        if m:
            vid_id = m.group(1)
        v['yt_id'] = vid_id

    _items = []
    _pos = 0
    for _v in videos:
        if not _v.get("yt_id"):
            continue
        _pos += 1
        _items.append({"@type": "ListItem", "position": _pos, "item": {
            "@type": "VideoObject", "name": _v.get("ko_title") or "",
            "description": _v.get("reason") or _v.get("ko_title") or "",
            "thumbnailUrl": "https://i.ytimg.com/vi/%s/hqdefault.jpg" % _v["yt_id"],
            "uploadDate": _v.get("created_at_iso") or "",
            "embedUrl": "https://www.youtube.com/embed/%s" % _v["yt_id"],
            "contentUrl": _v.get("youtube_url") or ""}})
    if _items:
        g.seo_ld = [{"@context": "https://schema.org", "@type": "ItemList",
                     "name": "뉴스 영상", "numberOfItems": len(_items),
                     "itemListElement": _items}]
    return render_template('news_videos.html', videos=videos)


# ── API: pending 목록 (DGX tg_video_cmd용) ───────────────────────────────────

@video_bp.route('/api/news_videos/pending_list', methods=['POST'])
def pending_list():
    body = request.get_data()
    if not _verify_signature(body, request.headers.get('X-Signature', '')):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'SELECT slug, ko_title, created_at FROM news_videos '
                'WHERE status=\'pending\' ORDER BY created_at DESC'
            )
            rows = cursor.fetchall()
    except Exception as e:
        current_app.logger.error(f'pending_list error: {e}')
        return jsonify({'ok': False, 'error': 'db error'}), 500
    finally:
        conn.close()

    for r in rows:
        if r.get('created_at'):
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M')
    return jsonify({'ok': True, 'items': rows}), 200


# ── API: 게시 승인된 영상 큐 (GB10 publish_worker용) ─────────────────────────
# status='publish_approved' = 텔레그램 버튼으로 사람이 승인한 것. GB10이 mp4를
# YouTube(sosig.gem)에 업로드한 뒤 /approve 로 youtube_url 링크 → status='approved'.

@video_bp.route('/api/news_videos/publish_queue', methods=['POST'])
def publish_queue():
    body = request.get_data()
    if not _verify_signature(body, request.headers.get('X-Signature', '')):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, slug, ko_title, en_title, ko_path, en_path, article_source, kind "
                "FROM news_videos WHERE status='publish_approved' ORDER BY created_at ASC"
            )
            rows = cursor.fetchall()
    except Exception as e:
        current_app.logger.error(f'publish_queue error: {e}')
        return jsonify({'ok': False, 'error': 'db error'}), 500
    finally:
        conn.close()

    return jsonify({'ok': True, 'items': rows}), 200
