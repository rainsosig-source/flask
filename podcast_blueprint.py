# ==============================================================================
# 팟캐스트 관련 Blueprint
# 방문자 카운터, 에피소드, 캘린더, 키워드, 방영록 API
# ==============================================================================

from flask import Blueprint, render_template, jsonify, request, session, current_app
from collections import Counter
from database import get_db_connection

podcast_bp = Blueprint('podcast', __name__)


def _static_path(mp3_path):
    """mp3_path에서 static 상대경로 추출"""
    if '/static/' in mp3_path:
        return mp3_path.split('/static/')[1]
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


# --- Broadcast API ---

@podcast_bp.route('/api/broadcast')
def api_broadcast():
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    keyword_id = request.args.get('keyword_id')
    q = request.args.get('q', '').strip()
    offset = (page - 1) * per_page

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            where = "WHERE 1=1"
            params = []
            if keyword_id:
                where += " AND e.keyword_id = %s"
                params.append(keyword_id)
            if q:
                where += " AND (e.title LIKE %s OR e.press LIKE %s)"
                params.extend([f'%{q}%', f'%{q}%'])

            cursor.execute(f"SELECT COUNT(*) as total FROM episodes e {where}", tuple(params))
            total = cursor.fetchone()['total']

            cursor.execute(f"""
                SELECT e.press, e.title, e.link, e.mp3_path, e.created_at, e.keyword_id,
                       COALESCE(k.topic, k.keyword) as topic
                FROM episodes e
                LEFT JOIN keywords k ON e.keyword_id = k.id
                {where}
                ORDER BY e.created_at DESC
                LIMIT %s OFFSET %s
            """, tuple(params + [per_page, offset]))
            episodes = cursor.fetchall()

            for ep in episodes:
                ep['static_path'] = _static_path(ep['mp3_path'])
                ep['created_at'] = ep['created_at'].strftime('%Y-%m-%d %H:%M')

        return jsonify({"episodes": episodes, "total": total, "page": page, "per_page": per_page})
    except Exception as e:
        current_app.logger.error(f"Error: {e}")
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()

@podcast_bp.route('/api/broadcast/stats')
def api_broadcast_stats():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as total FROM episodes")
            total = cursor.fetchone()['total']
            cursor.execute("SELECT COUNT(*) as today FROM episodes WHERE DATE(created_at) = CURDATE()")
            today = cursor.fetchone()['today']
            cursor.execute("SELECT COUNT(DISTINCT keyword_id) as topics FROM episodes")
            topics = cursor.fetchone()['topics']
            cursor.execute("SELECT COUNT(DISTINCT press) as sources FROM episodes")
            sources = cursor.fetchone()['sources']
        return jsonify({"total": total, "today": today, "topics": topics, "sources": sources})
    except Exception as e:
        current_app.logger.error(f"Error: {e}")
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
            sql = "SELECT press, title, link, mp3_path, created_at FROM episodes WHERE 1=1"
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
