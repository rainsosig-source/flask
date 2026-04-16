# ==============================================================================
# 팟캐스트 관련 Blueprint
# 방문자 카운터, 에피소드, 캘린더, 키워드, 방명록 API
# ==============================================================================

from flask import Blueprint, render_template, jsonify, request, session, current_app
from collections import Counter
from database import get_db_connection

podcast_bp = Blueprint('podcast', __name__)


def _static_path(mp3_path):
    """mp3_path를 static 상대경로로 반환 (신규: 상대경로 / 구: /.../static/ 접두어 제거)."""
    if not mp3_path:
        return mp3_path
    if '/static/' in mp3_path:
        return mp3_path.split('/static/', 1)[1]
    return mp3_path


# --- Pages ---

@podcast_bp.route('/podcast')
def podcast():
    return render_template('podcast.html')

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
