from client_api import client_bp
from route_blueprint import route_bp
from vuln_blueprint import vuln_bp
import random
import json
import uuid
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
import pymysql
import os
import sys
from collections import Counter
from datetime import datetime

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
app.register_blueprint(route_bp)
app.register_blueprint(vuln_bp)
app.register_blueprint(client_bp, url_prefix="/api/client")

# Database Configuration (from environment variables)
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_USER = os.environ.get('DB_USER', 'root')
DB_PASS = os.environ.get('DB_PASS', '')
DB_NAME = os.environ.get('DB_NAME', 'podcast')


def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        db=DB_NAME,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

# --- Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/visit', methods=['POST'])
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
            return jsonify({"error": str(e)}), 500
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
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/visit', methods=['GET'])
def get_visit_count():
    page = request.args.get('page', '/')
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT count FROM visit_counter WHERE page = %s", (page,))
            result = cursor.fetchone()
            return jsonify({"count": result['count'] if result else 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/broadcast')
def broadcast():
    return render_template('broadcast.html')

@app.route('/api/broadcast')
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
                full_path = ep['mp3_path']
                ep['static_path'] = full_path.split('/static/')[1] if '/static/' in full_path else full_path
                ep['created_at'] = ep['created_at'].strftime('%Y-%m-%d %H:%M')

        return jsonify({"episodes": episodes, "total": total, "page": page, "per_page": per_page})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/broadcast/stats')
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
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/podcast')
def podcast():
    return render_template('podcast.html')

@app.route('/manager')
def manager_gate():
    session.pop('is_admin', None)
    return render_template('globe.html')

@app.route('/manager/dashboard')
def manager_dashboard():
    # Security Check - Challenge must be passed
    if not session.get('challenge_passed'):
        return redirect('https://sosig.shop')
        
    return render_template('manager.html')

# --- Security Endpoint ---
@app.route('/api/gate/unlock', methods=['POST'])
def gate_unlock():
    if not session.get('challenge_passed'):
        return jsonify({"error": "unauthorized"}), 403
    session['is_admin'] = True
    return jsonify({"status": "unlocked"})

# --- API Endpoints ---

@app.route('/api/keywords', methods=['GET'])
def get_keywords():
    conn = get_db_connection()
    keywords = []
    try:
        with conn.cursor() as cursor:
            # COALESCE: topic이 NULL이면 keyword 값을 사용
            sql = "SELECT id, keyword, COALESCE(topic, keyword) as topic, requirements, priority FROM keywords ORDER BY priority DESC"
            cursor.execute(sql)
            keywords = cursor.fetchall()
    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        conn.close()
    return jsonify(keywords)

@app.route('/api/keywords', methods=['POST'])
def add_keyword():
    if not session.get('challenge_passed'):
        return jsonify({"error": "unauthorized"}), 403
    data = request.json
    keyword = data.get('keyword')
    topic = data.get('topic', keyword)  # topic이 없으면 keyword 사용
    priority = int(data.get('priority', 10))
    requirements = data.get('requirements', '')
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            check_sql = "SELECT id FROM keywords WHERE priority = %s"
            cursor.execute(check_sql, (priority,))
            if cursor.fetchone():
                return jsonify({"error": f"Priority {priority} is already in use."}), 400
                
            sql = "INSERT INTO keywords (keyword, topic, requirements, priority) VALUES (%s, %s, %s, %s)"
            cursor.execute(sql, (keyword, topic, requirements, priority))
        conn.commit()
        return jsonify({"message": "Keyword added"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/keywords/<int:id>', methods=['PUT'])
def update_keyword(id):
    if not session.get('challenge_passed'):
        return jsonify({"error": "unauthorized"}), 403
    data = request.json
    keyword = data.get('keyword')
    topic = data.get('topic', keyword)  # topic이 없으면 keyword 사용
    priority = int(data.get('priority'))
    requirements = data.get('requirements')
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            check_sql = "SELECT id FROM keywords WHERE priority = %s AND id != %s"
            cursor.execute(check_sql, (priority, id))
            if cursor.fetchone():
                return jsonify({"error": f"Priority {priority} is already in use."}), 400
                
            sql = "UPDATE keywords SET keyword=%s, topic=%s, requirements=%s, priority=%s WHERE id=%s"
            cursor.execute(sql, (keyword, topic, requirements, priority, id))
        conn.commit()
        return jsonify({"message": "Keyword updated"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/keywords/<int:id>', methods=['DELETE'])
def delete_keyword(id):
    if not session.get('challenge_passed'):
        return jsonify({"error": "unauthorized"}), 403
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sql = "DELETE FROM keywords WHERE id=%s"
            cursor.execute(sql, (id,))
        conn.commit()
        return jsonify({"message": "Keyword deleted"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/calendar-data')
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
            
            dates = []
            for row in result:
                if row['created_at']:
                    dates.append(row['created_at'].strftime('%Y-%m-%d'))
            
            counts = Counter(dates)
            for date, count in counts.items():
                data[date] = count
                
    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        conn.close()
    return jsonify(data)

@app.route('/api/episodes')
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
                full_path = row['mp3_path']
                if '/static/' in full_path:
                    static_rel_path = full_path.split('/static/')[1]
                else:
                    static_rel_path = full_path
                
                row['static_path'] = static_rel_path
                row['created_at'] = row['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                episodes.append(row)
    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        conn.close()
    return jsonify(episodes)


# --- MFA Logic ---
MFA_COUNTRIES = [
    {'code': 'KR', 'name': 'South Korea'}, {'code': 'US', 'name': 'United States'},
    {'code': 'JP', 'name': 'Japan'}, {'code': 'CN', 'name': 'China'},
    {'code': 'GB', 'name': 'United Kingdom'}, {'code': '-99', 'name': 'France'},
    {'code': 'DE', 'name': 'Germany'}, {'code': 'IT', 'name': 'Italy'},
    {'code': 'CA', 'name': 'Canada'}, {'code': 'AU', 'name': 'Australia'},
    {'code': 'BR', 'name': 'Brazil'}, {'code': 'IN', 'name': 'India'},
    {'code': 'RU', 'name': 'Russia'}, {'code': 'ES', 'name': 'Spain'}
]

@app.route('/api/auth/request-code', methods=['POST'])
def request_mfa_code():
    try:
        # Generate 3 random countries
        selection = random.sample(MFA_COUNTRIES, 3)
        codes = [c['code'] for c in selection]
        names = [c['name'] for c in selection]
        
        # Create session ID (simple uuid for now, or use existing session if available)
        # For simplicity, we'll generate a token and send it back, client must send it with verify
        session_token = str(uuid.uuid4())
        
        conn = get_db_connection()
        with conn.cursor() as cursor:
            sql = "INSERT INTO verification_codes (session_id, countries) VALUES (%s, %s)"
            cursor.execute(sql, (session_token, json.dumps(codes)))
        conn.commit()
        conn.close()
        
        # In a real app, we would send 'names' via Email/SMS and NOT return them here.
        # But for this demo/simulation, we return them so the UI can display them.
        return jsonify({'status': 'sent', 'session_token': session_token})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/verify-code', methods=['POST'])
def verify_mfa_code():
    try:
        data = request.json
        session_token = data.get('session_token')
        user_countries = data.get('countries') # List of ISO codes
        
        if not session_token or not user_countries:
            return jsonify({'status': 'fail', 'message': 'Missing data'}), 400
            
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # Get the latest code for this session
            sql = "SELECT countries FROM verification_codes WHERE session_id = %s ORDER BY id DESC LIMIT 1"
            cursor.execute(sql, (session_token,))
            result = cursor.fetchone()
            
        conn.close()
        
        if not result:
            return jsonify({'status': 'fail', 'message': 'Invalid session'}), 400
            
        stored_countries = json.loads(result['countries'])
        
        # Compare
        if user_countries == stored_countries:
            # Success!
            # Set a session cookie or return a success token
            # For now, we just say 'unlocked' and let the client redirect
            session['authenticated'] = True # Flask session
            return jsonify({'status': 'unlocked'})
        else:
            return jsonify({'status': 'fail', 'message': 'Incorrect sequence'})
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500





# --- Country Challenge Endpoints ---
@app.route('/api/challenge/generate', methods=['POST'])
def generate_challenge():
    try:
        CHALLENGE_COUNTRIES = [
            'South Korea', 'United States of America', 'Japan', 'China', 
            'United Kingdom', 'France', 'Germany', 'Italy', 'Canada', 
            'Australia', 'Brazil', 'India', 'Russia', 'Spain', 'Mexico',
            'Indonesia', 'Turkey', 'Saudi Arabia', 'Argentina', 'South Africa'
        ]
        
        selected = random.sample(CHALLENGE_COUNTRIES, 3)
        
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # Check if id=1 exists
                cursor.execute("SELECT id FROM verification_codes WHERE id = 1")
                exists = cursor.fetchone()
                
                if exists:
                    # Update existing row
                    sql = "UPDATE verification_codes SET countries = %s, session_id = 'challenge' WHERE id = 1"
                    cursor.execute(sql, (json.dumps(selected),))
                else:
                    # Insert new row with id=1
                    sql = "INSERT INTO verification_codes (id, session_id, countries) VALUES (1, 'challenge', %s)"
                    cursor.execute(sql, (json.dumps(selected),))
            conn.commit()
            return jsonify({'status': 'success', 'countries': selected}), 200
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/challenge/current', methods=['GET'])
def get_current_challenge():
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                sql = "SELECT countries FROM verification_codes WHERE id = 1"
                cursor.execute(sql)
                result = cursor.fetchone()
                
                if result and result['countries']:
                    countries = json.loads(result['countries'])
                    return jsonify({'status': 'success', 'countries': countries}), 200
                else:
                    return jsonify({'status': 'none'}), 200
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/challenge/verify', methods=['POST'])
def verify_challenge():
    try:
        data = request.json
        clicked = data.get('countries', [])
        
        if len(clicked) != 3:
            return jsonify({'status': 'fail', 'message': 'Must click 3 countries'}), 400
        
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                sql = "SELECT countries FROM verification_codes WHERE id = 1"
                cursor.execute(sql)
                result = cursor.fetchone()
                
                if not result or not result['countries']:
                    return jsonify({'status': 'fail', 'message': 'No challenge'}), 400
                
                challenge_countries = json.loads(result['countries'])
                
                if set(clicked) == set(challenge_countries):
                    session['challenge_passed'] = True
                    return jsonify({'status': 'success'}), 200
                else:
                    return jsonify({'status': 'fail', 'message': 'Wrong countries'}), 200
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/manager/settings')
def manager_settings():
    if not session.get('challenge_passed'):
        return redirect('https://sosig.shop')
    return render_template('settings.html')

@app.route('/about')
def about():
    return render_template('about.html')

if __name__ == '__main__':
    sys.stdout.reconfigure(line_buffering=True)
    app.run(host='0.0.0.0', port=5000)
