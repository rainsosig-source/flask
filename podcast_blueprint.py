# ==============================================================================
# 팟캐스트 관련 Blueprint
# 방문자 카운터, 에피소드, 캘린더, 키워드, 방명록 API
# ==============================================================================

from flask import Blueprint, render_template, jsonify, request, session, current_app
from collections import Counter
import json
import os as _os
from database import get_db_connection

podcast_bp = Blueprint('podcast', __name__)

SITE_BASE = 'https://sosig.shop'


def _static_path(mp3_path):
    """mp3_path를 static 상대경로로 반환 (신규: 상대경로 / 구: /.../static/ 접두어 제거)."""
    if not mp3_path:
        return mp3_path
    if '/static/' in mp3_path:
        return mp3_path.split('/static/', 1)[1]
    return mp3_path


def _audio_url(mp3_path):
    rel = _static_path(mp3_path)
    if not rel:
        return None
    if rel.startswith('http'):
        return rel
    if rel.startswith('/'):
        return f"{SITE_BASE}{rel}"
    return f"{SITE_BASE}/static/{rel}"


def _build_podcast_series_jsonld(episodes):
    """PodcastSeries + 최근 N개 PodcastEpisode JSON-LD."""
    parts = []
    for e in episodes:
        au = _audio_url(e.get('mp3_path'))
        if not au:
            continue
        parts.append({
            "@type": "PodcastEpisode",
            "name": e.get('title') or '',
            "description": (e.get('summary') or '')[:280],
            "datePublished": e['created_at'].strftime('%Y-%m-%dT%H:%M:%S+09:00') if e.get('created_at') else None,
            "url": f"{SITE_BASE}/podcast#ep-{e['id']}",
            "associatedMedia": {"@type": "MediaObject", "contentUrl": au, "encodingFormat": "audio/mpeg"},
            "duration": f"PT{int(e['duration_sec'])}S" if e.get('duration_sec') else None,
            "publisher": {"@type": "Organization", "name": e.get('press') or 'sosig.shop'},
        })
    series = {
        "@context": "https://schema.org",
        "@type": "PodcastSeries",
        "name": "sosig.shop AI 뉴스 팟캐스트",
        "description": "매일 엄선된 AI·경제·사회 뉴스를 한국어 2인 대화체로 전하는 자동 생성 팟캐스트.",
        "url": f"{SITE_BASE}/podcast",
        "webFeed": f"{SITE_BASE}/podcast/rss",
        "image": f"{SITE_BASE}/static/podcast-cover.jpg",
        "publisher": {"@type": "Organization", "name": "sosig.shop", "url": SITE_BASE},
        "hasPart": [{k: v for k, v in p.items() if v is not None} for p in parts],
    }
    return json.dumps(series, ensure_ascii=False, separators=(',', ':'))


def _recent_episodes(limit=50):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, press, title, mp3_path, duration_sec, summary, created_at "
                "FROM episodes ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


# --- Pages ---

@podcast_bp.route('/podcast')
def podcast():
    eps = _recent_episodes(50)
    return render_template('podcast.html', podcast_series_jsonld=_build_podcast_series_jsonld(eps))

@podcast_bp.route('/briefing')
def briefing():
    return render_template('briefing.html', briefing_jsonld=_build_briefing_jsonld())


def _build_briefing_jsonld():
    """시간대별·일별 합본 JSON-LD (정적 페이지 — 실제 mp3는 클라이언트가 날짜별 fetch)."""
    series = {
        "@context": "https://schema.org",
        "@type": "PodcastSeries",
        "name": "sosig.shop 시간대별 모음",
        "description": "AI 뉴스 팟캐스트를 새벽·아침·낮·저녁·밤 5개 시간대 + 일별로 자동 합본. 기사 사이 1.5초 무음으로 트랙 구분.",
        "url": f"{SITE_BASE}/briefing",
        "webFeed": f"{SITE_BASE}/podcast/rss",
        "image": f"{SITE_BASE}/static/podcast-cover.jpg",
        "publisher": {"@type": "Organization", "name": "sosig.shop", "url": SITE_BASE},
    }
    return json.dumps(series, ensure_ascii=False, separators=(',', ':'))

@podcast_bp.route('/broadcast')
def broadcast():
    return render_template('broadcast.html')


# --- Visit Counter ---

@podcast_bp.route('/api/visit', methods=['POST'])
def visit_count():
    page = request.json.get('page', '/')
    visit_key = f"visited_{page}"
    if session.get(visit_key):
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT count FROM visit_counter WHERE page = %s", (page,))
                result = cursor.fetchone()
                return jsonify({"count": result['count'] if result else 0})
        except Exception as e:
            current_app.logger.error(f"Error: {e}")
            return jsonify({"error": "Internal server error"}), 500
        finally:
            conn.close()
    session[visit_key] = True
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO visit_counter (page, count) VALUES (%s, 1) ON DUPLICATE KEY UPDATE count = count + 1", (page,))
            conn.commit()
            cursor.execute("SELECT count FROM visit_counter WHERE page = %s", (page,))
            result = cursor.fetchone()
            return jsonify({"count": result['count']})
    except Exception as e:
        current_app.logger.error(f"Error: {e}")
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()

@podcast_bp.route('/api/visit', methods=['GET'])
def get_visit_count():
    page = request.args.get('page', '/')
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT count FROM visit_counter WHERE page = %s", (page,))
            result = cursor.fetchone()
            return jsonify({"count": result['count'] if result else 0})
    except Exception as e:
        current_app.logger.error(f"Error: {e}")
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


# --- Guestbook API ---

@podcast_bp.route('/api/guestbook')
def api_guestbook():
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    offset = (page - 1) * per_page

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as total FROM guestbook WHERE is_visible = 1")
            total = cursor.fetchone()['total']

            cursor.execute("""
                SELECT id, nickname, message, created_at
                FROM guestbook WHERE is_visible = 1
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, (per_page, offset))
            messages = cursor.fetchall()
            for m in messages:
                m['created_at'] = m['created_at'].strftime('%Y-%m-%d %H:%M')

        return jsonify({"messages": messages, "total": total, "page": page, "per_page": per_page})
    except Exception as e:
        current_app.logger.error(f"Guestbook error: {e}")
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@podcast_bp.route('/api/guestbook', methods=['POST'])
def post_guestbook():
    data = request.json or {}
    nickname = data.get('nickname', '').strip()
    message = data.get('message', '').strip()

    if not nickname or not message:
        return jsonify({"error": "닉네임과 메시지를 입력해주세요."}), 400
    if len(nickname) > 50:
        return jsonify({"error": "닉네임은 50자 이내로 입력해주세요."}), 400
    if len(message) > 500:
        return jsonify({"error": "메시지는 500자 이내로 입력해주세요."}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO guestbook (nickname, message) VALUES (%s, %s)",
                (nickname, message)
            )
        conn.commit()
        return jsonify({"message": "등록되었습니다!"}), 201
    except Exception as e:
        current_app.logger.error(f"Guestbook write error: {e}")
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@podcast_bp.route('/api/guestbook/<int:msg_id>', methods=['DELETE'])
def delete_guestbook(msg_id):
    if not session.get('challenge_passed'):
        return jsonify({"error": "unauthorized"}), 403
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE guestbook SET is_visible = 0 WHERE id = %s", (msg_id,))
        conn.commit()
        return jsonify({"message": "삭제되었습니다."}), 200
    except Exception as e:
        current_app.logger.error(f"Guestbook delete error: {e}")
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


# --- Keywords API ---

@podcast_bp.route('/api/keywords', methods=['GET'])
def get_keywords():
    conn = get_db_connection()
    keywords = []
    try:
        with conn.cursor() as cursor:
            sql = "SELECT id, keyword, COALESCE(topic, keyword) as topic, requirements, priority FROM keywords ORDER BY priority DESC"
            cursor.execute(sql)
            keywords = cursor.fetchall()
    except Exception as e:
        current_app.logger.error(f"DB Error: {e}")
    finally:
        conn.close()
    return jsonify(keywords)

@podcast_bp.route('/api/keywords', methods=['POST'])
def add_keyword():
    if not session.get('challenge_passed'):
        return jsonify({"error": "unauthorized"}), 403
    data = request.json
    keyword = data.get('keyword')
    topic = data.get('topic', keyword)
    priority = int(data.get('priority', 10))
    requirements = data.get('requirements', '')

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM keywords WHERE priority = %s", (priority,))
            if cursor.fetchone():
                return jsonify({"error": f"Priority {priority} is already in use."}), 400
            cursor.execute(
                "INSERT INTO keywords (keyword, topic, requirements, priority) VALUES (%s, %s, %s, %s)",
                (keyword, topic, requirements, priority)
            )
        conn.commit()
        return jsonify({"message": "Keyword added"}), 201
    except Exception as e:
        current_app.logger.error(f"Error: {e}")
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()

@podcast_bp.route('/api/keywords/<int:id>', methods=['PUT'])
def update_keyword(id):
    if not session.get('challenge_passed'):
        return jsonify({"error": "unauthorized"}), 403
    data = request.json
    keyword = data.get('keyword')
    topic = data.get('topic', keyword)
    priority = int(data.get('priority'))
    requirements = data.get('requirements')

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM keywords WHERE priority = %s AND id != %s", (priority, id))
            if cursor.fetchone():
                return jsonify({"error": f"Priority {priority} is already in use."}), 400
            cursor.execute(
                "UPDATE keywords SET keyword=%s, topic=%s, requirements=%s, priority=%s WHERE id=%s",
                (keyword, topic, requirements, priority, id)
            )
        conn.commit()
        return jsonify({"message": "Keyword updated"}), 200
    except Exception as e:
        current_app.logger.error(f"Error: {e}")
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()

@podcast_bp.route('/api/keywords/<int:id>', methods=['DELETE'])
def delete_keyword(id):
    if not session.get('challenge_passed'):
        return jsonify({"error": "unauthorized"}), 403
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM keywords WHERE id=%s", (id,))
        conn.commit()
        return jsonify({"message": "Keyword deleted"}), 200
    except Exception as e:
        current_app.logger.error(f"Error: {e}")
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


# --- Calendar & Episodes API ---

@podcast_bp.route('/api/calendar-data')
def calendar_data():
    keyword_id = request.args.get('keyword_id')

    conn = get_db_connection()
    data = {}
    try:
        with conn.cursor() as cursor:
            sql = "SELECT created_at FROM episodes WHERE 1=1"
            params = []
            if keyword_id:
                sql += " AND keyword_id = %s"
                params.append(keyword_id)
            cursor.execute(sql, tuple(params))
            result = cursor.fetchall()

            dates = [row['created_at'].strftime('%Y-%m-%d') for row in result if row['created_at']]
            counts = Counter(dates)
            for date, count in counts.items():
                data[date] = count
    except Exception as e:
        current_app.logger.error(f"DB Error: {e}")
    finally:
        conn.close()
    return jsonify(data)

@podcast_bp.route('/api/episodes')
def get_episodes():
    date_str = request.args.get('date')
    keyword_id = request.args.get('keyword_id')

    conn = get_db_connection()
    episodes = []
    try:
        with conn.cursor() as cursor:
            sql = ("SELECT press, title, link, mp3_path, duration_sec, summary, created_at "
                   "FROM episodes WHERE 1=1")
            params = []
            if date_str:
                sql += " AND DATE(created_at) = %s"
                params.append(date_str)
            if keyword_id:
                sql += " AND keyword_id = %s"
                params.append(keyword_id)
            sql += " ORDER BY created_at DESC"
            if not date_str and not keyword_id:
                sql += " LIMIT 10"

            cursor.execute(sql, tuple(params))
            result = cursor.fetchall()

            for row in result:
                row['static_path'] = _static_path(row['mp3_path'])
                row['created_at'] = row['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                episodes.append(row)
    except Exception as e:
        current_app.logger.error(f"DB Error: {e}")
    finally:
        conn.close()
    return jsonify(episodes)


# --- 시간대별/일별 모음 팟캐스트 ---
import os
import sys as _sys_compile
import subprocess as _subprocess_compile
_sys_compile.path.insert(0, '/opt/flask-app/scripts')
from compilation_periods import PERIODS as _PERIODS, period_label as _period_label, period_range as _period_range, PERIOD_KEYS as _PERIOD_KEYS

_COMPILE_STATIC_ROOT = '/opt/flask-app/static/podcast'


def _compilation_meta(rel_audio_path):
    """rel_audio_path 예시: 'hourly/2026/05/16/dawn.mp3'."""
    abs_path = os.path.join(_COMPILE_STATIC_ROOT, rel_audio_path)
    if not os.path.exists(abs_path):
        return {"exists": False}
    try:
        size = os.path.getsize(abs_path)
    except Exception:
        size = None
    try:
        mtime = int(os.path.getmtime(abs_path))
    except Exception:
        mtime = 0
    duration = None
    try:
        r = _subprocess_compile.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", abs_path],
            capture_output=True, text=True, timeout=10,
        )
        duration = int(float(r.stdout.strip()))
    except Exception:
        pass
    return {
        "exists": True,
        "audio_url": f"/static/podcast/{rel_audio_path}?v={mtime}",
        "size": size,
        "duration_sec": duration,
        "mtime": mtime,
    }


def _episodes_in_window(date_str, hour_start=None, hour_end=None):
    conn = get_db_connection()
    eps = []
    try:
        with conn.cursor() as cur:
            sql = ("SELECT id, press, title, mp3_path, created_at FROM episodes "
                   "WHERE DATE(created_at)=%s ")
            params = [date_str]
            if hour_start is not None and hour_end is not None:
                sql += "AND HOUR(created_at) BETWEEN %s AND %s "
                params += [hour_start, hour_end]
            sql += "ORDER BY created_at ASC"
            cur.execute(sql, tuple(params))
            for row in cur.fetchall():
                eps.append({
                    "id": row["id"], "press": row["press"], "title": row["title"],
                    "static_path": _static_path(row["mp3_path"]),
                    "created_at": row["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
                })
    finally:
        conn.close()
    return eps


@podcast_bp.route('/api/compilation/period/<date_str>/<period>')
def api_compilation_period(date_str, period):
    if period not in _PERIOD_KEYS:
        return jsonify({"error": "invalid period"}), 400
    rel = f"hourly/{date_str.replace('-', '/')}/{period}.mp3"
    meta = _compilation_meta(rel)
    start_h, end_h = _period_range(period)
    eps = _episodes_in_window(date_str, start_h, end_h)
    return jsonify({
        **meta,
        "kind": "period",
        "period": period,
        "period_label": _period_label(period),
        "date": date_str,
        "episodes": eps,
        "episode_count": len(eps),
    })


@podcast_bp.route('/api/compilation/daily/<date_str>')
def api_compilation_daily(date_str):
    rel = f"daily/{date_str[:7].replace('-', '/')}/{date_str[8:10]}.mp3"
    meta = _compilation_meta(rel)
    eps = _episodes_in_window(date_str)
    return jsonify({
        **meta,
        "kind": "daily",
        "date": date_str,
        "episodes": eps,
        "episode_count": len(eps),
    })


@podcast_bp.route('/api/compilation/index/<date_str>')
def api_compilation_index(date_str):
    """해당 날짜의 모든 시간대 + 일별 모음 메타 한 번에 반환."""
    counts_by_hour = {}
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT HOUR(created_at) AS h, COUNT(*) AS n FROM episodes "
                "WHERE DATE(created_at)=%s GROUP BY HOUR(created_at)",
                (date_str,),
            )
            for row in cur.fetchall():
                counts_by_hour[int(row["h"])] = int(row["n"])
    finally:
        conn.close()
    total_day = sum(counts_by_hour.values())

    out = {"date": date_str, "periods": [], "daily": None}
    for key, label, start_h, end_h in _PERIODS:
        rel = f"hourly/{date_str.replace('-', '/')}/{key}.mp3"
        meta = _compilation_meta(rel)
        meta["key"] = key
        meta["label"] = label
        meta["episode_count"] = sum(counts_by_hour.get(h, 0) for h in range(start_h, end_h + 1))
        out["periods"].append(meta)
    rel_d = f"daily/{date_str[:7].replace('-', '/')}/{date_str[8:10]}.mp3"
    daily_meta = _compilation_meta(rel_d)
    daily_meta["episode_count"] = total_day
    out["daily"] = daily_meta
    return jsonify(out)


# --- RSS/Podcast Feed (RSS 2.0 + iTunes namespace) ---
# Apple Podcasts / Spotify / 일반 RSS 리더 호환
from email.utils import formatdate
from xml.sax.saxutils import escape as _xml_escape
from flask import Response, url_for

_PODCAST_STATIC_ROOT = '/opt/flask-app/static'
_FEED_TITLE = 'sosig.shop — AI 뉴스 팟캐스트'
_FEED_DESC  = '매일 엄선된 뉴스 기사를 2인 대화 형식으로 풀어드리는 AI 생성 한국어 팟캐스트'
_FEED_AUTHOR = 'sosig.shop'
_FEED_LANG = 'ko-kr'
_FEED_CATEGORY = 'News'
_FEED_SUBCATEGORY = 'Tech News'
_FEED_IMAGE = 'https://sosig.shop/static/podcast-cover.jpg'
_FEED_LINK = 'https://sosig.shop/podcast'
_FEED_SELF = 'https://sosig.shop/podcast/rss'

def _rfc822(dt):
    # created_at은 TIMESTAMP → naive datetime (KST). UTC로 가정하지 않고 +0900 표시
    import time
    return formatdate(time.mktime(dt.timetuple()), usegmt=False)

def _hms(sec):
    if not sec:
        return '00:00'
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'

def _enclosure_url(mp3_path):
    rel = _static_path(mp3_path)
    if not rel:
        return None
    return f'https://sosig.shop/static/{rel}'

def _enclosure_length(mp3_path):
    rel = _static_path(mp3_path)
    if not rel:
        return 0
    path = os.path.join(_PODCAST_STATIC_ROOT, rel)
    try:
        return os.path.getsize(path)
    except OSError:
        return 0

@podcast_bp.route('/podcast/rss')
@podcast_bp.route('/podcast/feed.xml')
def podcast_rss():
    conn = get_db_connection()
    items = []
    latest = None
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, press, title, link, mp3_path, duration_sec, summary, created_at "
                "FROM episodes WHERE mp3_path IS NOT NULL AND mp3_path <> '' "
                "ORDER BY created_at DESC LIMIT 100"
            )
            for row in cursor.fetchall():
                enc_url = _enclosure_url(row['mp3_path'])
                if not enc_url:
                    continue
                if latest is None:
                    latest = row['created_at']
                items.append(row)
    except Exception as e:
        current_app.logger.error(f"RSS DB Error: {e}")
    finally:
        conn.close()

    last_build = _rfc822(latest) if latest else formatdate(usegmt=False)

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<rss version="2.0" '
                 'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
                 'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
                 'xmlns:atom="http://www.w3.org/2005/Atom">')
    parts.append('<channel>')
    parts.append(f'<title>{_xml_escape(_FEED_TITLE)}</title>')
    parts.append(f'<link>{_FEED_LINK}</link>')
    parts.append(f'<atom:link href="{_FEED_SELF}" rel="self" type="application/rss+xml"/>')
    parts.append(f'<description>{_xml_escape(_FEED_DESC)}</description>')
    parts.append(f'<language>{_FEED_LANG}</language>')
    parts.append(f'<lastBuildDate>{last_build}</lastBuildDate>')
    parts.append(f'<itunes:author>{_xml_escape(_FEED_AUTHOR)}</itunes:author>')
    parts.append(f'<itunes:summary>{_xml_escape(_FEED_DESC)}</itunes:summary>')
    parts.append('<itunes:explicit>false</itunes:explicit>')
    parts.append(f'<itunes:image href="{_FEED_IMAGE}"/>')
    parts.append('<itunes:owner>')
    parts.append(f'<itunes:name>{_xml_escape(_FEED_AUTHOR)}</itunes:name>')
    parts.append(f'<itunes:email>sddari@gmail.com</itunes:email>')
    parts.append('</itunes:owner>')
    parts.append(f'<itunes:category text="{_FEED_CATEGORY}">'
                 f'<itunes:category text="{_FEED_SUBCATEGORY}"/>'
                 '</itunes:category>')

    for row in items:
        enc_url = _enclosure_url(row['mp3_path'])
        enc_len = _enclosure_length(row['mp3_path'])
        title = row.get('title') or ''
        press = row.get('press') or ''
        summary = row.get('summary') or ''
        orig_link = row.get('link') or _FEED_LINK
        guid = f"sosig.shop-episode-{row['id']}"
        pub = _rfc822(row['created_at'])
        dur = _hms(row.get('duration_sec'))
        desc_full = f"[{press}] {summary}\n\n원문: {orig_link}" if press else summary

        parts.append('<item>')
        parts.append(f'<title>{_xml_escape(title)}</title>')
        parts.append(f'<description>{_xml_escape(desc_full)}</description>')
        parts.append(f'<content:encoded><![CDATA[<p>[{press}] {summary}</p><p>원문: <a href="{orig_link.replace(chr(38), "&amp;")}">{orig_link}</a></p>]]></content:encoded>')
        parts.append(f'<pubDate>{pub}</pubDate>')
        parts.append(f'<guid isPermaLink="false">{guid}</guid>')
        parts.append(f'<link>{_xml_escape(orig_link)}</link>')
        parts.append(f'<enclosure url="{_xml_escape(enc_url, {chr(34): "&quot;"})}" length="{enc_len}" type="audio/mpeg"/>')
        parts.append(f'<itunes:duration>{dur}</itunes:duration>')
        parts.append(f'<itunes:author>{_xml_escape(press or _FEED_AUTHOR)}</itunes:author>')
        parts.append('<itunes:explicit>false</itunes:explicit>')
        parts.append('</item>')

    parts.append('</channel></rss>')
    xml = '\n'.join(parts)
    return Response(xml, mimetype='application/rss+xml; charset=utf-8')
