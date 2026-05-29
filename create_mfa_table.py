import os
import pymysql

DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_USER = os.environ.get('DB_USER', 'root')
DB_PASS = os.environ.get('DB_PASS', '')
DB_NAME = os.environ.get('DB_NAME', 'podcast')

if not DB_PASS:
    raise SystemExit("DB_PASS 환경변수가 필요합니다 (예: DB_PASS=... python3 create_mfa_table.py)")

def create_mfa_table():
    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, db=DB_NAME, charset='utf8mb4')
    try:
        with conn.cursor() as cursor:
            sql = """
            CREATE TABLE IF NOT EXISTS verification_codes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(255) NOT NULL,
                countries JSON NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX (session_id)
            )
            """
            cursor.execute(sql)
            print("Table 'verification_codes' created successfully.")
        conn.commit()
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    create_mfa_table()
