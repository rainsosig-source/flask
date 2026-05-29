import pymysql
import json

# Same config as app.py
import os
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_USER = os.environ.get('DB_USER', 'root')
DB_PASS = os.environ.get('DB_PASS', '')
DB_NAME = os.environ.get('DB_NAME', 'podcast')
if not DB_PASS:
    raise SystemExit('DB_PASS env var required')

def check_db():
    print(f"Connecting to {DB_HOST} as {DB_USER}...")
    try:
        conn = pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            db=DB_NAME,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        print("Connected.")
        
        with conn.cursor() as cursor:
            # 1. Check episodes count
            cursor.execute("SELECT COUNT(*) as count FROM episodes")
            print(f"Total episodes: {cursor.fetchone()}")
            
            # 2. Check calendar query
            sql = "SELECT DATE_FORMAT(created_at, '%Y-%m-%d') as date, COUNT(*) as count FROM episodes WHERE 1=1 GROUP BY date"
            cursor.execute(sql)
            results = cursor.fetchall()
            print(f"Calendar Query Results: {results}")
            
            # 3. Check keywords
            cursor.execute("SELECT * FROM keywords")
            print(f"Keywords: {cursor.fetchall()}")

        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_db()
